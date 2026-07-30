"""Chat with cited answers over the user's own material (PRD §7.5)."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import prompts
from ..db.enums import ChatScope, MessageRole
from ..db.models import Chat, Message, Segment
from ..db.session import session_scope
from ..errors import NotFoundError, QuicklearnError
from ..pipeline.schemas import FollowupQuestions
from ..providers import gateway
from ..providers.base import Message as ProviderMessage
from ..providers.base import text_message
from ..rag import retrieve as rag
from .common import chat_dict, message_dict, run_db, segment_dict

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

CITATION_RE = re.compile(r"\[(\d+)\]")
#: Phrases that count as the model admitting it has nothing. Checked when retrieval was empty.
DISCLAIMER_MARKERS = (
    "don't cover",
    "do not cover",
    "not covered",
    "no information",
    "nothing in your",
    "couldn't find",
    "could not find",
    "isn't in your",
    "is not in your",
    "not found in",
    "don't contain",
    "do not contain",
    "no relevant",
    "not mentioned",
    "unable to find",
)
NO_CONTEXT_FALLBACK = (
    "I couldn't find anything in your notes that answers this. Try rephrasing, widening the "
    "scope to your whole library, or adding the source material first."
)


class CreateChatRequest(BaseModel):
    scope_type: Literal["source", "folder", "global"] = "global"
    scope_id: int | None = None
    title: str = ""


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


@router.post("")
def create_chat(payload: CreateChatRequest) -> dict[str, Any]:
    with session_scope() as session:
        chat = Chat(
            scope_type=ChatScope(payload.scope_type),
            scope_id=payload.scope_id,
            title=payload.title.strip() or "New chat",
        )
        session.add(chat)
        session.flush()
        return chat_dict(chat)


@router.get("")
def list_chats(limit: int = 50) -> dict[str, Any]:
    with session_scope() as session:
        rows = list(
            session.execute(select(Chat).order_by(Chat.created_at.desc()).limit(limit)).scalars()
        )
        counts = dict(
            session.execute(
                select(Message.chat_id, func.count(Message.id)).group_by(Message.chat_id)
            ).all()
        )
        return {
            "chats": [chat_dict(chat, message_count=int(counts.get(chat.id, 0))) for chat in rows]
        }


@router.get("/{chat_id}/messages")
def get_messages(chat_id: int) -> dict[str, Any]:
    with session_scope() as session:
        chat = session.get(Chat, chat_id)
        if chat is None:
            raise NotFoundError(f"Chat {chat_id} not found.")
        rows = list(
            session.execute(
                select(Message).where(Message.chat_id == chat_id).order_by(Message.id)
            ).scalars()
        )
        return {"chat": chat_dict(chat), "messages": [message_dict(m) for m in rows]}


@router.delete("/{chat_id}")
def delete_chat(chat_id: int) -> dict[str, Any]:
    with session_scope() as session:
        chat = session.get(Chat, chat_id)
        if chat is None:
            raise NotFoundError(f"Chat {chat_id} not found.")
        session.delete(chat)
    return {"deleted": True}


@router.get("/citations")
def resolve_citations(ids: str) -> dict[str, Any]:
    """Resolve citation markers to segments, for click-through from an answer."""
    try:
        wanted = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError as exc:
        raise NotFoundError("Invalid citation ids.") from exc

    with session_scope() as session:
        rows = list(session.execute(select(Segment).where(Segment.id.in_(wanted))).scalars())
    return {"segments": [segment_dict(segment) for segment in rows]}


# --------------------------------------------------------------------------- answering


def _load_chat(chat_id: int) -> tuple[str, int | None, list[ProviderMessage]]:
    """Chat scope plus recent turns, for conversational context."""
    with session_scope() as session:
        chat = session.get(Chat, chat_id)
        if chat is None:
            raise NotFoundError(f"Chat {chat_id} not found.")
        history = list(
            session.execute(
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.id.desc())
                .limit(8)
            ).scalars()
        )
        turns: list[ProviderMessage] = [
            text_message("assistant" if m.role is MessageRole.ASSISTANT else "user", m.content)
            for m in reversed(history)
        ]
        return str(chat.scope_type), chat.scope_id, turns


def _store(
    chat_id: int, role: MessageRole, content: str, citations: list[int], followups: list[str]
) -> dict[str, Any]:
    with session_scope() as session:
        message = Message(
            chat_id=chat_id,
            role=role,
            content=content,
            citations=citations,
            followups=followups,
        )
        session.add(message)
        session.flush()
        chat = session.get(Chat, chat_id)
        # Name the chat after its first question, so the sidebar is scannable.
        if chat is not None and chat.title == "New chat" and role is MessageRole.USER:
            chat.title = content.strip()[:80]
        return message_dict(message)


def validate_answer(answer: str, excerpts: list[rag.Excerpt]) -> tuple[str, list[int]]:
    """Enforce the citation contract from PRD §7.5.

    Two checks. When retrieval found nothing, the answer *must* say so — if the model answered
    anyway, it did so from parametric knowledge, which is precisely the failure this feature
    exists to avoid, and the answer is replaced. When retrieval did find something, citation
    markers are resolved against the excerpts actually supplied so a fabricated ``[99]`` cannot
    render as a real link.
    """
    valid_ids = {excerpt.id for excerpt in excerpts}

    if not excerpts:
        lowered = answer.lower()
        if not any(marker in lowered for marker in DISCLAIMER_MARKERS):
            log.warning("model answered without retrieved context and no disclaimer; replacing")
            return NO_CONTEXT_FALLBACK, []
        return answer, []

    cited = {int(match) for match in CITATION_RE.findall(answer)}
    hallucinated = cited - valid_ids
    if hallucinated:
        log.info("dropping %d citation marker(s) not in the retrieved set", len(hallucinated))
        answer = CITATION_RE.sub(
            lambda m: "" if int(m.group(1)) in hallucinated else m.group(0), answer
        )
    return answer, sorted(cited & valid_ids)


async def _followups(query: str, answer: str) -> list[str]:
    try:
        result = await gateway.complete_structured(
            "chat followups",
            [
                text_message(
                    "user", prompts.render("chat_followups", query=query, answer=answer, count=3)
                )
            ],
            FollowupQuestions,
            task_class="fast",
            temperature=0.6,
            max_tokens=400,
        )
        return [q.strip() for q in result.questions if q.strip()][:3]
    except Exception as exc:
        log.debug("follow-up generation failed: %s", exc)
        return []


async def _build_prompt(
    chat_id: int, question: str
) -> tuple[list[ProviderMessage], list[rag.Excerpt]]:
    scope_type, scope_id, history = await run_db(_load_chat, chat_id)
    excerpts = await rag.retrieve(question, scope_type=scope_type, scope_id=scope_id)

    system = prompts.render(
        "chat_system",
        excerpts=[
            {
                "id": excerpt.id,
                "text": excerpt.text,
                "source_title": excerpt.source_title,
                "timestamp": excerpt.timestamp,
            }
            for excerpt in excerpts
        ],
        scope_description=rag.scope_description(scope_type, scope_id),
    )
    messages: list[ProviderMessage] = [text_message("system", system)]
    messages.extend(history)
    messages.append(text_message("user", question))
    return messages, excerpts


@router.post("/{chat_id}/message")
async def send_message(chat_id: int, payload: SendMessageRequest) -> dict[str, Any]:
    """Non-streaming answer. The WebSocket route is preferred, this is the fallback."""
    question = payload.content.strip()
    await run_db(_store, chat_id, MessageRole.USER, question, [], [])

    messages, excerpts = await _build_prompt(chat_id, question)
    raw = await gateway.complete_text("chat", messages, temperature=0.3, max_tokens=2000)
    answer, citations = validate_answer(raw, excerpts)
    followups = await _followups(question, answer) if excerpts else []

    stored = await run_db(_store, chat_id, MessageRole.ASSISTANT, answer, citations, followups)
    stored["excerpts"] = [excerpt.to_dict() for excerpt in excerpts]
    return stored


@router.websocket("/ws/{chat_id}")
async def chat_socket(websocket: WebSocket, chat_id: int) -> None:
    """Streaming chat.

    Tokens stream as they arrive, but the *stored* answer is the validated one. Streaming raw
    output and validating afterwards means the user may briefly see a citation that is then
    corrected — acceptable, and far better than withholding the answer until it is complete.
    """
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            question = str(payload.get("content") or "").strip()
            if not question:
                await websocket.send_json({"type": "error", "message": "Empty message."})
                continue

            try:
                await run_db(_store, chat_id, MessageRole.USER, question, [], [])
                messages, excerpts = await _build_prompt(chat_id, question)

                await websocket.send_json(
                    {
                        "type": "excerpts",
                        "excerpts": [excerpt.to_dict() for excerpt in excerpts],
                    }
                )

                collected: list[str] = []
                async for delta in gateway.stream_text(
                    "chat", messages, temperature=0.3, max_tokens=2000
                ):
                    collected.append(delta)
                    await websocket.send_json({"type": "token", "text": delta})

                answer, citations = validate_answer("".join(collected), excerpts)
                followups = await _followups(question, answer) if excerpts else []
                stored = await run_db(
                    _store, chat_id, MessageRole.ASSISTANT, answer, citations, followups
                )
                await websocket.send_json(
                    {
                        "type": "done",
                        "message": stored,
                        "citations": citations,
                        "followups": followups,
                        # Set when validation replaced the answer, so the UI can re-render.
                        "corrected": answer != "".join(collected),
                    }
                )
            except QuicklearnError as exc:
                await websocket.send_json({"type": "error", "message": exc.message})
            except Exception as exc:
                log.exception("chat stream failed")
                await websocket.send_json(
                    {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                )
    except WebSocketDisconnect:
        return
    except Exception:  # pragma: no cover - transport-level failure
        log.debug("chat socket closed unexpectedly", exc_info=True)
