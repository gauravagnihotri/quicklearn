"""End-to-end API tests.

Walks the product the way a user does — upload, notes, edit, cards, quiz, chat, export — over
real HTTP through ``ASGITransport``, with the ``mock`` provider standing in for inference. This
is the test that would catch a broken wiring between two layers that both pass their own unit
tests.
"""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from quicklearn.config import Config
from quicklearn.jobs import queue as jq
from quicklearn.jobs.worker import run_job_inline
from quicklearn.main import create_app

from .conftest import HAVE_FFMPEG


@pytest_asyncio.fixture
async def client(config: Config, store: Any) -> AsyncIterator[httpx.AsyncClient]:
    """API client with the mock provider configured.

    The worker pool is deliberately not started — tests drain jobs explicitly with
    :func:`drain`, so assertions run against a finished pipeline instead of polling.
    """
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
        yield client


async def drain(limit: int = 40) -> None:
    """Run every queued job to completion, including ones queued by other jobs."""
    for _ in range(limit):
        jobs = jq.list_jobs(limit=50, active_only=True)
        queued = [job for job in jobs if job["status"] == "queued"]
        if not queued:
            return
        for job in queued:
            await run_job_inline(job["id"])


@pytest.fixture
def audio_file(config: Config) -> Path:
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    path = config.data_dir / "lecture.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=120",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return path


# --------------------------------------------------------------------------- basics


async def test_health_reports_capabilities(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "sqlite_vec" in body
    assert "vector_index" in body


async def test_dashboard_works_when_empty(client: httpx.AsyncClient) -> None:
    """A fresh install must render, not 500 on missing data."""
    response = await client.get("/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["sources"] == 0
    assert body["due"]["due"] == 0
    assert body["stats"]["streak_days"] == 0


async def test_unknown_source_returns_actionable_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/sources/9999")
    assert response.status_code == 404
    assert "9999" in response.json()["message"]


# --------------------------------------------------------------------------- text ingest


async def test_text_source_runs_the_full_pipeline(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/sources/text",
        json={
            "title": "Linear Algebra",
            "content": (
                "Eigenvalues describe how a linear transformation scales its eigenvectors.\n\n"
                "The characteristic polynomial is found by solving det(A - lambda I) = 0.\n\n"
                "A symmetric matrix always has real eigenvalues and orthogonal eigenvectors, "
                "a result known as the spectral theorem.\n\n"
                "Principal component analysis projects data onto the top eigenvectors of the "
                "covariance matrix, which is positive semi-definite by construction."
            ),
        },
    )
    assert response.status_code == 200
    source_id = response.json()["source_id"]

    await drain()

    detail = (await client.get(f"/sources/{source_id}")).json()
    assert detail["source"]["status"] == "ready", detail["source"]["error"]
    assert detail["counts"]["segments"] > 0
    assert detail["counts"]["concepts"] > 0
    assert detail["counts"]["cards"] > 0
    assert detail["note"] is not None
    assert detail["note"]["doc"]["type"] == "doc"


async def test_generated_blocks_anchor_to_real_segments(client: httpx.AsyncClient) -> None:
    """The anti-hallucination guarantee: every anchor must resolve to a segment that exists."""
    response = await client.post(
        "/sources/text",
        json={"title": "Anchors", "content": "Eigenvalues scale eigenvectors. " * 40},
    )
    source_id = response.json()["source_id"]
    await drain()

    segment_ids = {
        s["id"] for s in (await client.get(f"/sources/{source_id}/segments")).json()["segments"]
    }
    note_id = (await client.get(f"/sources/{source_id}")).json()["note"]["id"]
    blocks = (await client.get(f"/notes/{note_id}")).json()["blocks"]

    assert blocks, "note should have blocks"
    anchored = [b for b in blocks if b["segment_ids"]]
    assert anchored, "at least some blocks must cite a source segment"
    for block in anchored:
        for sid in block["segment_ids"]:
            assert sid in segment_ids, f"block cites segment {sid}, which does not exist"


# --------------------------------------------------------------------------- editor


async def test_note_autosave_marks_dirty_and_reindexes(client: httpx.AsyncClient) -> None:
    source_id = (
        await client.post(
            "/sources/text",
            json={"title": "Edit me", "content": "Original text about photosynthesis. " * 20},
        )
    ).json()["source_id"]
    await drain()
    note_id = (await client.get(f"/sources/{source_id}")).json()["note"]["id"]

    edited = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"segmentIds": []},
                "content": [{"type": "text", "text": "Mitochondrial respiration is my own note."}],
            }
        ],
    }
    response = await client.patch(f"/notes/{note_id}", json={"doc": edited})
    assert response.status_code == 200
    assert response.json()["is_dirty"] is True

    # The user's own writing must be findable, or search silently ignores half the library.
    hits = (await client.get("/search", params={"q": "Mitochondrial"})).json()["results"]
    assert any("Mitochondrial" in hit["snippet"] for hit in hits), hits


async def test_regenerate_warns_when_the_note_was_edited(client: httpx.AsyncClient) -> None:
    """PRD §12.3 — warn rather than silently overwrite a hand-edited note."""
    source_id = (
        await client.post(
            "/sources/text",
            json={"title": "Careful", "content": "Some content here about cells. " * 20},
        )
    ).json()["source_id"]
    await drain()
    note_id = (await client.get(f"/sources/{source_id}")).json()["note"]["id"]

    before = (
        await client.post(f"/sources/{source_id}/regenerate", json={"target": "notes"})
    ).json()
    assert before["note_was_edited"] is False
    # Local patch: notes refuse saves while a job for the source is queued, so let it finish.
    await drain()

    await client.patch(
        f"/notes/{note_id}",
        json={
            "doc": {
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "mine"}]}],
            }
        },
    )
    after = (await client.post(f"/sources/{source_id}/regenerate", json={"target": "notes"})).json()
    assert after["note_was_edited"] is True


def _callout(text: str) -> dict[str, Any]:
    return {
        "type": "callout",
        "attrs": {"variant": "key"},
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}] if text else []}
        ],
    }


def _doc(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "content": list(nodes)}


async def _ready_note(client: httpx.AsyncClient) -> tuple[int, int, dict[str, Any]]:
    source_id = (
        await client.post(
            "/sources/text",
            json={"title": "Guarded", "content": "Content about ribosomes and proteins. " * 20},
        )
    ).json()["source_id"]
    await drain()
    note = (await client.get(f"/sources/{source_id}")).json()["note"]
    return source_id, note["id"], note


async def test_note_save_refused_while_source_processes(client: httpx.AsyncClient) -> None:
    """Local patch: a regeneration in flight would overwrite the save anyway."""
    source_id, note_id, _ = await _ready_note(client)
    await client.post(f"/sources/{source_id}/regenerate", json={"target": "notes"})

    response = await client.patch(f"/notes/{note_id}", json={"doc": _doc(_callout("mine"))})
    assert response.status_code == 409

    await drain()
    response = await client.patch(f"/notes/{note_id}", json={"doc": _doc(_callout("mine"))})
    assert response.status_code == 200


async def test_note_save_refused_from_older_version(client: httpx.AsyncClient) -> None:
    source_id, note_id, note = await _ready_note(client)
    await client.post(f"/sources/{source_id}/regenerate", json={"target": "notes"})
    await drain()

    stale = await client.patch(
        f"/notes/{note_id}", json={"doc": _doc(_callout("old")), "base_version": note["version"]}
    )
    assert stale.status_code == 409
    current = (await client.get(f"/notes/{note_id}")).json()["version"]
    fresh = await client.patch(
        f"/notes/{note_id}", json={"doc": _doc(_callout("new")), "base_version": current}
    )
    assert fresh.status_code == 200


async def test_note_save_refused_when_it_blanks_several_blocks(client: httpx.AsyncClient) -> None:
    """The empty-callout signature: blocks keep their place but every one loses its text."""
    _, note_id, _ = await _ready_note(client)
    full = _doc(_callout("one"), _callout("two"), _callout("three"))
    assert (await client.patch(f"/notes/{note_id}", json={"doc": full})).status_code == 200

    one_cleared = _doc(_callout(""), _callout("two"), _callout("three"))
    assert (await client.patch(f"/notes/{note_id}", json={"doc": one_cleared})).status_code == 200

    blanked = _doc(_callout(""), _callout(""), _callout(""))
    assert (await client.patch(f"/notes/{note_id}", json={"doc": blanked})).status_code == 409
    saved = (await client.get(f"/notes/{note_id}")).json()["doc"]
    assert saved == one_cleared


async def test_note_save_refused_from_another_ui_build(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab left open across a deploy runs the old code; it must not write."""
    from quicklearn.api import notes as notes_api

    _, note_id, _ = await _ready_note(client)
    monkeypatch.setattr(notes_api, "_served_build", lambda: "build-2")
    doc = {"doc": _doc(_callout("x"))}

    assert (await client.patch(f"/notes/{note_id}", json=doc)).status_code == 409
    old = await client.patch(
        f"/notes/{note_id}", json=doc, headers={"X-Quicklearn-Build": "build-1"}
    )
    assert old.status_code == 409
    ok = await client.patch(
        f"/notes/{note_id}", json=doc, headers={"X-Quicklearn-Build": "build-2"}
    )
    assert ok.status_code == 200


async def test_note_exports(client: httpx.AsyncClient) -> None:
    source_id = (
        await client.post(
            "/sources/text",
            json={"title": "Export", "content": "Exportable content about enzymes. " * 20},
        )
    ).json()["source_id"]
    await drain()
    note_id = (await client.get(f"/sources/{source_id}")).json()["note"]["id"]

    for fmt, media_type in [
        ("md", "text/markdown"),
        ("txt", "text/plain"),
        ("html", "text/html"),
        ("json", "application/json"),
        ("docx", "application/vnd.openxmlformats"),
    ]:
        response = await client.get(f"/notes/{note_id}/export", params={"format": fmt})
        assert response.status_code == 200, fmt
        assert media_type in response.headers["content-type"], fmt
        assert len(response.content) > 0, fmt


async def test_ai_action_returns_text(client: httpx.AsyncClient) -> None:
    source_id = (
        await client.post(
            "/sources/text",
            json={"title": "AI", "content": "Content for actions about osmosis. " * 20},
        )
    ).json()["source_id"]
    await drain()
    note_id = (await client.get(f"/sources/{source_id}")).json()["note"]["id"]

    response = await client.post(
        f"/notes/{note_id}/ai-action",
        json={
            "action": "simplify",
            "selection": "A complicated sentence about osmosis and diffusion.",
        },
    )
    assert response.status_code == 200
    assert response.json()["result"].strip()


# --------------------------------------------------------------------------- study


async def test_review_flow_schedules_and_updates_mastery(client: httpx.AsyncClient) -> None:
    await client.post(
        "/sources/text",
        json={
            "title": "Study",
            "content": "Photosynthesis converts light energy into chemical energy. " * 25,
        },
    )
    await drain()

    due = (await client.get("/cards/due")).json()
    assert due["cards"], "cards should be due immediately after generation"

    card = due["cards"][0]
    # Every rating must be previewed, or the user cannot choose honestly.
    assert set(card["intervals"]) == {"1", "2", "3", "4"} or set(card["intervals"]) == {1, 2, 3, 4}

    response = await client.post(
        f"/cards/{card['id']}/review", json={"rating": 3, "duration_ms": 4200}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["reps"] == 1
    assert result["due"] > card["due"], "a Good rating must push the card into the future"

    undo = await client.post(f"/cards/{card['id']}/undo")
    assert undo.status_code == 200


async def test_forecast_and_stats(client: httpx.AsyncClient) -> None:
    await client.post(
        "/sources/text",
        json={"title": "Forecast", "content": "Cellular respiration produces ATP. " * 25},
    )
    await drain()

    forecast = (await client.get("/cards/forecast", params={"days": 14})).json()["forecast"]
    assert len(forecast) == 14
    stats = (await client.get("/cards/stats")).json()
    assert stats["cards"] > 0
    assert stats["optimize_threshold"] == 400


async def test_anki_export_round_trips(client: httpx.AsyncClient) -> None:
    """The adoption feature: export must produce a real, re-importable package."""
    await client.post(
        "/sources/text",
        json={
            "title": "Anki",
            "content": "The Krebs cycle occurs in the mitochondrial matrix. " * 25,
        },
    )
    await drain()

    exported = await client.post("/cards/export/anki", json={"deck_name": "Test Deck"})
    assert exported.status_code == 200
    payload = exported.content
    assert payload[:2] == b"PK", "an .apkg is a zip archive"
    original_count = int(exported.headers["X-Card-Count"])
    assert original_count > 0

    reimported = await client.post(
        "/cards/import",
        files={"file": ("Test Deck.apkg", payload, "application/octet-stream")},
    )
    assert reimported.status_code == 200, reimported.text
    assert reimported.json()["imported"] == original_count


async def test_csv_import(client: httpx.AsyncClient) -> None:
    csv_bytes = (
        b"Front,Back\n"
        b"What is ATP?,The energy currency of the cell\n"
        b"Define osmosis,Water moving down its gradient\n"
    )
    response = await client.post(
        "/cards/import", files={"file": ("deck.csv", csv_bytes, "text/csv")}
    )
    assert response.status_code == 200
    assert response.json()["imported"] == 2


async def test_quiz_practice_session(client: httpx.AsyncClient) -> None:
    source_id = (
        await client.post(
            "/sources/text",
            json={
                "title": "Quiz",
                "content": "Osmosis is the diffusion of water across a semipermeable membrane. "
                * 25,
            },
        )
    ).json()["source_id"]
    await drain()

    created = await client.post(
        "/quiz/session", json={"mode": "practice", "source_ids": [source_id]}
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    question = (await client.get(f"/quiz/session/{session_id}/next")).json()
    assert question["done"] is False
    assert question["question"]
    # The answer must not travel with the question.
    assert "answer" not in question

    answered = await client.post(
        f"/quiz/session/{session_id}/answer",
        json={"item_id": question["id"], "response": question.get("options", ["something"])[0]},
    )
    assert answered.status_code == 200
    body = answered.json()
    assert "score" in body and "feedback" in body

    results = (await client.get(f"/quiz/session/{session_id}/results")).json()
    assert results["answered"] == 1
    assert "weak_concepts" in results


async def test_test_mode_withholds_feedback(client: httpx.AsyncClient) -> None:
    """PRD §7.4: test mode gives no feedback until submission."""
    source_id = (
        await client.post(
            "/sources/text",
            json={
                "title": "Test mode",
                "content": "Glycolysis splits glucose into two pyruvate molecules. " * 25,
            },
        )
    ).json()["source_id"]
    await drain()
    await client.post(f"/sources/{source_id}/regenerate", json={"target": "quiz", "count": 5})
    await drain()

    session_id = (
        await client.post(
            "/quiz/session", json={"mode": "test", "source_ids": [source_id], "length": 3}
        )
    ).json()["session_id"]

    question = (await client.get(f"/quiz/session/{session_id}/next")).json()
    if question.get("done"):
        pytest.skip("no bank items generated by the mock provider")

    answered = (
        await client.post(
            f"/quiz/session/{session_id}/answer",
            json={"item_id": question["id"], "response": "an answer"},
        )
    ).json()
    assert "feedback" not in answered
    assert "correct_answer" not in answered
    assert "score" in answered


# --------------------------------------------------------------------------- chat & search


async def test_chat_answers_with_citations(client: httpx.AsyncClient) -> None:
    source_id = (
        await client.post(
            "/sources/text",
            json={
                "title": "Chat source",
                "content": "The mitochondria is the powerhouse of the cell. " * 20
                + "Chlorophyll absorbs light in the blue and red parts of the spectrum. " * 20,
            },
        )
    ).json()["source_id"]
    await drain()

    chat_id = (
        await client.post("/chat", json={"scope_type": "source", "scope_id": source_id})
    ).json()["id"]

    response = await client.post(
        f"/chat/{chat_id}/message", json={"content": "What does chlorophyll absorb?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"].strip()
    assert body["excerpts"], "retrieval should find something in the indexed source"


async def test_chat_refuses_to_answer_without_retrieval(client: httpx.AsyncClient) -> None:
    """The core trust property: an empty library must produce a disclaimer, not an answer."""
    chat_id = (await client.post("/chat", json={"scope_type": "global"})).json()["id"]
    body = (
        await client.post(
            f"/chat/{chat_id}/message", json={"content": "What is the capital of France?"}
        )
    ).json()

    assert body["citations"] == []
    lowered = body["content"].lower()
    assert any(
        marker in lowered
        for marker in (
            "couldn't find",
            "could not find",
            "don't cover",
            "not in your",
            "nothing in your",
        )
    ), f"expected a disclaimer, got: {body['content'][:300]}"
    assert "paris" not in lowered, "must not answer from parametric knowledge"


async def test_search_keyword_and_semantic(client: httpx.AsyncClient) -> None:
    await client.post(
        "/sources/text",
        json={
            "title": "Photosynthesis notes",
            "content": "Chlorophyll captures photons in the thylakoid membrane. " * 20,
        },
    )
    await drain()

    keyword = (await client.get("/search", params={"q": "chlorophyll"})).json()
    assert keyword["results"], "keyword search should find the indexed text"

    semantic = (await client.get("/search", params={"q": "chlorophyll", "semantic": True})).json()
    assert semantic["semantic"] is True


async def test_search_handles_punctuation_without_erroring(client: httpx.AsyncClient) -> None:
    """FTS5 treats many characters as operators; user input must be escaped, not passed raw."""
    for query in ["C++ (advanced)", 'what is "x"?', "a AND OR NOT", "*", "trailing-"]:
        response = await client.get("/search", params={"q": query})
        assert response.status_code == 200, f"query {query!r} broke search"


# --------------------------------------------------------------------------- library


async def test_folder_tree_and_cycle_guard(client: httpx.AsyncClient) -> None:
    parent = (await client.post("/folders", json={"name": "Semester 1"})).json()
    child = (
        await client.post("/folders", json={"name": "Biology", "parent_id": parent["id"]})
    ).json()

    tree = (await client.get("/folders")).json()
    assert len(tree["folders"]) == 2

    # Making the parent a child of its own descendant would orphan the subtree.
    response = await client.patch(f"/folders/{parent['id']}", json={"parent_id": child["id"]})
    assert response.status_code == 422
    assert "itself" in response.json()["message"]


async def test_deleting_a_folder_keeps_its_sources_by_default(client: httpx.AsyncClient) -> None:
    folder = (await client.post("/folders", json={"name": "Temp"})).json()
    source_id = (
        await client.post(
            "/sources/text",
            json={"title": "Filed", "content": "Some content. " * 30, "folder_id": folder["id"]},
        )
    ).json()["source_id"]

    await client.delete(f"/folders/{folder['id']}")
    source = (await client.get(f"/sources/{source_id}")).json()["source"]
    assert source["folder_id"] is None, "sources should be unfiled, not destroyed"


async def test_tagging_a_source(client: httpx.AsyncClient) -> None:
    source_id = (
        await client.post("/sources/text", json={"title": "Tagged", "content": "Content. " * 30})
    ).json()["source_id"]
    response = await client.patch(f"/sources/{source_id}", json={"tags": ["exam", "week-3"]})
    assert set(response.json()["tags"]) == {"exam", "week-3"}
    assert {t["name"] for t in (await client.get("/tags")).json()["tags"]} == {"exam", "week-3"}


async def test_delete_source_removes_everything(client: httpx.AsyncClient) -> None:
    source_id = (
        await client.post(
            "/sources/text",
            json={"title": "Doomed", "content": "Transient content about ribosomes. " * 25},
        )
    ).json()["source_id"]
    await drain()

    assert (await client.get("/cards/counts")).json()["total"] > 0
    assert (await client.delete(f"/sources/{source_id}")).status_code == 200
    assert (await client.get(f"/sources/{source_id}")).status_code == 404
    assert (await client.get("/cards/counts")).json()["total"] == 0
    assert (await client.get("/search", params={"q": "ribosomes"})).json()["results"] == []


# --------------------------------------------------------------------------- settings


async def test_settings_round_trip_and_secret_masking(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/settings",
        json={
            "values": {
                "providers.openai.api_key": "sk-super-secret",
                "generation.note_style": "cornell",
            }
        },
    )
    assert response.status_code == 200

    body = (await client.get("/settings")).json()
    assert body["settings"]["generation.note_style"] == "cornell"
    # The key must never come back to the browser — only the fact that one is set.
    assert body["settings"]["_secrets_present"]["openai"] is True
    assert "sk-super-secret" not in response.text
    assert "sk-super-secret" not in str(body)


async def test_test_provider_reports_failure_as_data(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/settings/test-provider",
        json={"provider": "mock", "kind": "llm", "api_key": ""},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    unknown = await client.post("/settings/test-provider", json={"provider": "nope", "kind": "llm"})
    assert unknown.status_code == 200
    assert unknown.json()["ok"] is False


async def test_usage_tracks_cost(client: httpx.AsyncClient) -> None:
    await client.post(
        "/sources/text", json={"title": "Cost", "content": "Content for costing. " * 30}
    )
    await drain()

    usage = (await client.get("/usage")).json()
    assert usage["calls"] > 0, "every LLM call must be logged (PRD §11)"
    assert usage["total_usd"] == 0.0, "the mock provider is free"
    assert usage["recent"]


async def test_cost_estimate_before_running(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/usage/estimate", json={"text": "word " * 5000, "audio_seconds": 3600}
    )
    assert response.status_code == 200
    body = response.json()
    assert "estimated_usd" in body
    assert body["assumptions"]["audio_seconds"] == 3600


# --------------------------------------------------------------------------- media


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not on PATH")
async def test_audio_upload_produces_timestamped_segments(
    client: httpx.AsyncClient, audio_file: Path
) -> None:
    with audio_file.open("rb") as handle:
        response = await client.post(
            "/sources/upload", files={"file": ("lecture.wav", handle, "audio/wav")}
        )
    assert response.status_code == 200, response.text
    source_id = response.json()["source_id"]

    await drain()

    detail = (await client.get(f"/sources/{source_id}")).json()
    assert detail["source"]["status"] == "ready", detail["source"]["error"]
    assert detail["source"]["duration_sec"] == pytest.approx(120, abs=2)

    segments = (await client.get(f"/sources/{source_id}/segments")).json()["segments"]
    assert segments
    assert all(s["start_ms"] is not None for s in segments), "audio segments need timestamps"
    assert segments[0]["timestamp"] == "0:00"
    # Timestamps must stay inside the real audio or the player desyncs.
    assert max(s["end_ms"] for s in segments) <= 121_000

    audio = await client.get(f"/sources/{source_id}/audio")
    assert audio.status_code == 200


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not on PATH")
async def test_resumable_chunked_upload(client: httpx.AsyncClient, audio_file: Path) -> None:
    data = audio_file.read_bytes()
    init = (
        await client.post(
            "/sources/upload/init",
            json={"filename": "chunked.wav", "total_bytes": len(data)},
        )
    ).json()
    token = init["upload_token"]

    chunk_size = len(data) // 3 + 1
    offset = 0
    while offset < len(data):
        piece = data[offset : offset + chunk_size]
        response = await client.post(
            "/sources/upload/chunk",
            params={"token": token, "offset": offset},
            content=piece,
        )
        assert response.status_code == 200
        offset += len(piece)

    # Re-sending a chunk must be idempotent, not corrupt the file.
    await client.post(
        "/sources/upload/chunk", params={"token": token, "offset": 0}, content=data[:chunk_size]
    )

    completed = await client.post("/sources/upload/complete", json={"token": token})
    assert completed.status_code == 200
    source_id = completed.json()["source_id"]

    await drain()
    detail = (await client.get(f"/sources/{source_id}")).json()
    assert detail["source"]["status"] == "ready", detail["source"]["error"]
    assert detail["source"]["size_bytes"] == len(data)


async def test_recording_lifecycle(client: httpx.AsyncClient) -> None:
    started = (await client.post("/sources/record/start", json={"title": "Lecture 1"})).json()
    token, source_id = started["upload_token"], started["source_id"]
    assert started["stt_configured"] is True

    assert (await client.get("/sources/recordings/unfinished")).json()["recordings"]

    await client.post("/sources/record/chunk", params={"token": token}, content=b"\x00" * 4096)
    marker = await client.post(f"/sources/{source_id}/marker", json={"position_ms": 12000})
    assert marker.json()["markers"] == [12000]

    # A tab that closes mid-recording leaves the partial file recoverable.
    unfinished = (await client.get("/sources/recordings/unfinished")).json()["recordings"]
    assert any(r["id"] == source_id and r["has_data"] for r in unfinished)

    assert (await client.delete(f"/sources/recordings/unfinished/{source_id}")).status_code == 200


async def test_recording_warns_when_stt_is_unconfigured(client: httpx.AsyncClient) -> None:
    """PRD §7.1 — never let someone record 90 minutes then discover they can't transcribe it."""
    await client.patch("/settings", json={"values": {"stt.provider": ""}})
    started = (await client.post("/sources/record/start", json={})).json()
    assert started["stt_configured"] is False
    assert "transcription" in (started["warning"] or "").lower()


# --------------------------------------------------------------------------- jobs


async def test_job_tree_is_reported(client: httpx.AsyncClient) -> None:
    await client.post("/sources/text", json={"title": "Jobs", "content": "Job content. " * 30})
    await drain()

    jobs = (await client.get("/jobs")).json()["jobs"]
    ingest = next(job for job in jobs if job["type"] == "ingest")
    detail = (await client.get(f"/jobs/{ingest['id']}")).json()

    assert detail["status"] == "succeeded"
    assert detail["children"], "the UI needs a progress tree, not one opaque job"
    assert all(child["parent_id"] == ingest["id"] for child in detail["children"])
