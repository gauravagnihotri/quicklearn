"""Jobs, settings, usage and the job-progress WebSocket (PRD §8)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import __version__
from ..db.enums import ProviderKind
from ..db.models import LlmCall
from ..db.session import session_scope, vec_available
from ..errors import NotFoundError, ValidationError
from ..jobs import queue as jq
from ..jobs.events import get_bus
from ..providers import gateway, registry
from ..settings import DEFAULTS, get_settings
from .common import run_db

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


class SettingsPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class TestProviderRequest(BaseModel):
    provider: str
    kind: Literal["llm", "stt", "tts", "embedding"] = "llm"
    api_key: str = ""
    base_url: str = ""


# --------------------------------------------------------------------------- jobs


@router.get("/jobs")
def list_jobs(active_only: bool = False, limit: int = 50) -> dict[str, Any]:
    return {"jobs": jq.list_jobs(limit=limit, active_only=active_only)}


@router.get("/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    job = jq.get_job(job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} not found.")
    #: Children let the UI render the ingest progress tree.
    job["children"] = [
        child for child in jq.list_jobs(limit=50) if child.get("parent_id") == job_id
    ]
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict[str, Any]:
    if not jq.cancel(job_id):
        raise ValidationError("That job has already finished.")
    return {"cancelled": True}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int) -> dict[str, Any]:
    if not jq.retry(job_id):
        raise ValidationError("That job is not in a retryable state.")
    return {"retried": True}


@router.websocket("/ws/jobs")
async def jobs_socket(websocket: WebSocket) -> None:
    """Push job progress so the UI shows real stage names, never an indefinite spinner."""
    await websocket.accept()
    bus = get_bus()
    queue = await bus.subscribe("jobs")
    try:
        # Send current state immediately, so a page load doesn't wait for the next event.
        await websocket.send_json(
            {"type": "snapshot", "jobs": await run_db(jq.list_jobs, limit=25, active_only=True)}
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25.0)
            except TimeoutError:
                # Keepalive: proxies and browsers drop idle sockets.
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event.to_json())
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - transport level
        log.debug("jobs socket closed", exc_info=True)
    finally:
        await bus.unsubscribe(queue, "jobs")


# --------------------------------------------------------------------------- settings


@router.get("/settings")
def get_settings_payload() -> dict[str, Any]:
    settings = get_settings()
    return {
        "settings": settings.as_public_dict(),
        "providers": registry.catalogue(),
        "defaults": DEFAULTS,
        "configured": {
            "llm": registry.has_llm(),
            "stt": registry.has_stt(),
            "tts": registry.has_tts(),
        },
    }


@router.patch("/settings")
def patch_settings(payload: SettingsPatch) -> dict[str, Any]:
    if not payload.values:
        raise ValidationError("No settings supplied.")

    settings = get_settings()
    settings.set_many(payload.values)
    # Cached provider instances hold the old credentials; drop them so a newly pasted key
    # takes effect on the very next request rather than after a restart.
    registry.reset()

    return {
        "settings": settings.as_public_dict(),
        "configured": {
            "llm": registry.has_llm(),
            "stt": registry.has_stt(),
            "tts": registry.has_tts(),
        },
    }


@router.post("/settings/test-provider")
async def test_provider(payload: TestProviderRequest) -> dict[str, Any]:
    """Validate a key and list its models, before it is saved.

    PRD §4.3 wants failures loud and specific at config time rather than mid-pipeline, so this
    returns the reason as data instead of raising.
    """
    kind = ProviderKind(payload.kind)
    settings = get_settings()
    # Fall back to a stored key so "Test" works on an already-saved provider without
    # requiring the user to paste the secret again.
    api_key = payload.api_key or settings.api_key(payload.provider)
    base_url = payload.base_url or settings.base_url(payload.provider)

    try:
        provider = registry.build_for_test(kind, payload.provider, api_key, base_url)
    except Exception as exc:
        return {"ok": False, "message": str(exc), "models": []}

    result = await provider.test_connection()
    with contextlib.suppress(Exception):
        await provider.aclose()

    return {
        "ok": result.ok,
        "message": result.message,
        "models": [
            {"id": model.id, "name": model.name, "context_window": model.context_window}
            for model in result.models
        ],
    }


@router.get("/settings/models")
async def available_models(kind: str = "llm", provider: str | None = None) -> dict[str, Any]:
    """Models per configured provider, for the routing pickers."""
    provider_kind = ProviderKind(kind)
    catalogue = {
        ProviderKind.LLM: registry.LLM_PROVIDERS,
        ProviderKind.STT: registry.STT_PROVIDERS,
        ProviderKind.TTS: registry.TTS_PROVIDERS,
        ProviderKind.EMBEDDING: registry.EMBEDDING_PROVIDERS,
    }[provider_kind]

    names = [provider] if provider else list(catalogue)
    settings = get_settings()
    output: dict[str, Any] = {}

    async def probe(name: str) -> None:
        cls = catalogue.get(name)
        if cls is None:
            return
        if getattr(cls, "requires_api_key", True) and not settings.api_key(name):
            output[name] = {"ok": False, "message": "No API key configured.", "models": []}
            return
        try:
            instance = registry.get_provider(provider_kind, name)
            models = await instance.list_models()
            output[name] = {
                "ok": True,
                "models": [
                    {"id": m.id, "name": m.name, "context_window": m.context_window} for m in models
                ],
            }
        except Exception as exc:
            output[name] = {"ok": False, "message": str(exc), "models": []}

    await asyncio.gather(*(probe(name) for name in names))
    return {"kind": kind, "providers": output}


@router.post("/settings/reset-onboarding")
def reset_onboarding() -> dict[str, Any]:
    get_settings().set("onboarding.completed", False)
    return {"ok": True}


# --------------------------------------------------------------------------- usage


@router.get("/usage")
def usage(limit: int = 50) -> dict[str, Any]:
    totals = gateway.total_cost()
    with session_scope() as session:
        recent = list(
            session.execute(
                select(LlmCall).order_by(LlmCall.created_at.desc()).limit(limit)
            ).scalars()
        )
    totals["recent"] = [
        {
            "id": call.id,
            "provider": call.provider,
            "model": call.model,
            "purpose": call.purpose,
            "task_class": call.task_class,
            "prompt_tokens": call.prompt_tokens,
            "completion_tokens": call.completion_tokens,
            "audio_seconds": round(call.audio_seconds, 2),
            "characters": call.characters,
            "cost_usd": round(call.cost_usd, 6),
            "latency_ms": call.latency_ms,
            "ok": call.ok,
            "error": call.error,
            "created_at": call.created_at.isoformat(),
        }
        for call in recent
    ]
    return totals


@router.post("/usage/estimate")
async def estimate(payload: dict[str, Any]) -> dict[str, Any]:
    """Pre-flight cost estimate, shown before a job runs (PRD §4.4)."""
    from ..providers import pricing

    text = str(payload.get("text") or "")
    audio_seconds = float(payload.get("audio_seconds") or 0.0)
    tokens = gateway.estimate_tokens(text) if text else int(payload.get("tokens") or 0)

    settings = get_settings()
    llm_provider, llm_model = settings.routing("standard")
    stt_provider = settings.get_str("stt.provider")
    stt_model = settings.get_str("stt.model")

    # Note generation reads the transcript several times across the passes and writes roughly
    # a third of its length back out, so a naive single-pass estimate would be far too low.
    prompt_tokens = int(tokens * 2.5)
    completion_tokens = int(tokens * 0.6)

    llm = pricing.llm_cost(llm_provider, llm_model, prompt_tokens, completion_tokens)
    stt = pricing.stt_cost(stt_provider, stt_model, audio_seconds)
    total = llm + stt

    return {
        "estimated_usd": round(total.usd, 5),
        "known": total.known,
        "breakdown": {
            "transcription": {"usd": round(stt.usd, 5), "known": stt.known},
            "generation": {"usd": round(llm.usd, 5), "known": llm.known},
        },
        "assumptions": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "audio_seconds": audio_seconds,
        },
        "note": "Estimate only — providers change prices and token counts vary.",
    }


# --------------------------------------------------------------------------- health


@router.get("/health")
def health() -> dict[str, Any]:
    from ..pipeline.media import ffmpeg_available
    from ..rag import index as rag_index

    return {
        "status": "ok",
        "version": __version__,
        "ffmpeg": ffmpeg_available(),
        "sqlite_vec": vec_available(),
        "vector_index": rag_index.index_status(),
        "workers": jq.queue_depth(),
        "providers": {
            "llm": registry.has_llm(),
            "stt": registry.has_stt(),
            "tts": registry.has_tts(),
        },
    }
