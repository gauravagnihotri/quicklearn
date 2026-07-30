"""Flashcard review, scheduling and Anki interop (PRD §7.3)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db.models import Card
from ..db.session import session_scope
from ..errors import DependencyMissingError, NotFoundError, ValidationError
from ..study import anki, review
from .common import card_dict, run_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/cards", tags=["cards"])


class ReviewRequest(BaseModel):
    rating: int = Field(ge=1, le=4)
    duration_ms: int | None = None


class ExportRequest(BaseModel):
    source_ids: list[int] = Field(default_factory=list)
    folder_id: int | None = None
    deck_name: str = "quicklearn"
    preserve_scheduling: bool = True


class CardEdit(BaseModel):
    front: str | None = None
    back: str | None = None
    suspended: bool | None = None


@router.get("/due")
def get_due(
    folder_id: int | None = None,
    source_id: int | None = None,
    limit: int = Query(default=50, le=200),
    include_new: bool = True,
) -> dict[str, Any]:
    cards = review.due_cards(
        folder_id=folder_id,
        source_ids=[source_id] if source_id else None,
        limit=limit,
        include_new=include_new,
    )
    return {"cards": cards, "counts": review.due_counts(folder_id=folder_id)}


@router.get("/counts")
def get_counts(folder_id: int | None = None) -> dict[str, Any]:
    return review.due_counts(folder_id=folder_id)


@router.get("/forecast")
def get_forecast(
    days: int = Query(default=30, ge=1, le=365), folder_id: int | None = None
) -> dict[str, Any]:
    return {"forecast": review.forecast(days, folder_id=folder_id)}


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    return review.review_stats()


@router.get("")
def list_cards(
    source_id: int | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    with session_scope() as session:
        stmt = select(Card)
        if source_id is not None:
            stmt = stmt.where(Card.source_id == source_id)
        rows = list(session.execute(stmt.order_by(Card.id).limit(limit).offset(offset)).scalars())
    return {"cards": [card_dict(card) for card in rows]}


@router.post("/{card_id}/review")
def submit_review(card_id: int, payload: ReviewRequest) -> dict[str, Any]:
    return review.apply_rating(card_id, payload.rating, payload.duration_ms)


@router.post("/{card_id}/undo")
def undo(card_id: int) -> dict[str, Any]:
    if not review.undo_review(card_id):
        raise NotFoundError("No review to undo for that card.")
    return {"undone": True}


@router.patch("/{card_id}")
def edit_card(card_id: int, payload: CardEdit) -> dict[str, Any]:
    with session_scope() as session:
        card = session.get(Card, card_id)
        if card is None:
            raise NotFoundError(f"Card {card_id} not found.")
        if payload.front is not None:
            card.front = payload.front.strip() or card.front
        if payload.back is not None:
            card.back = payload.back.strip() or card.back
        if payload.suspended is not None:
            card.suspended = payload.suspended
        session.flush()
        return card_dict(card)


@router.delete("/{card_id}")
def delete_card(card_id: int) -> dict[str, Any]:
    with session_scope() as session:
        card = session.get(Card, card_id)
        if card is None:
            raise NotFoundError(f"Card {card_id} not found.")
        session.delete(card)
    return {"deleted": True}


@router.post("/export/anki")
async def export_anki(payload: ExportRequest) -> FileResponse:
    """Export to `.apkg`, preserving scheduling state where possible."""
    temp_dir = Path(tempfile.mkdtemp(prefix="quicklearn-export-"))
    destination = temp_dir / f"{payload.deck_name or 'quicklearn'}.apkg"

    result = await run_db(
        anki.export_apkg,
        destination,
        source_ids=payload.source_ids or None,
        folder_id=payload.folder_id,
        deck_name=payload.deck_name or "quicklearn",
        preserve_scheduling=payload.preserve_scheduling,
    )
    return FileResponse(
        result.path,
        media_type="application/octet-stream",
        filename=result.path.name,
        headers={"X-Card-Count": str(result.card_count)},
    )


@router.post("/import")
async def import_cards(
    file: Annotated[UploadFile, File()],
    folder_id: Annotated[int | None, Form()] = None,
) -> dict[str, Any]:
    """Import an Anki `.apkg` or a two-column CSV."""
    filename = file.filename or "import"
    data = await file.read()
    if not data:
        raise ValidationError("The uploaded file was empty.")

    suffix = Path(filename).suffix.lower()
    if suffix == ".apkg":
        with tempfile.NamedTemporaryFile(suffix=".apkg", delete=False) as handle:
            handle.write(data)
            temp_path = Path(handle.name)
        try:
            result = await run_db(anki.import_apkg, temp_path, folder_id=folder_id)
        finally:
            temp_path.unlink(missing_ok=True)
    elif suffix in {".csv", ".tsv", ".txt"}:
        result = await run_db(anki.import_csv, data, filename=filename, folder_id=folder_id)
    else:
        raise ValidationError(
            f"Cannot import '{suffix}'. Supported formats are .apkg (Anki) and .csv."
        )

    return {
        "imported": result.imported,
        "skipped": result.skipped,
        "source_id": result.source_id,
    }


@router.post("/optimize-fsrs")
async def optimize_fsrs() -> dict[str, Any]:
    """Fit FSRS parameters to the user's own review history (PRD §7.3)."""
    from ..handlers.study import optimize_fsrs_parameters

    try:
        parameters = await optimize_fsrs_parameters()
    except DependencyMissingError:
        raise
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return {"parameters": parameters, "count": len(parameters)}
