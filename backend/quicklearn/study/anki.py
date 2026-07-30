"""Anki `.apkg` export and import (PRD §7.3).

The PRD singles this out: "This single feature is the most likely driver of adoption, because
it lets Anki's existing user base adopt quicklearn as a card *generator* without abandoning
their established workflow."

Scheduling state is preserved, which genanki does not do on its own — it emits every card as
new. So the package is generated, then its embedded SQLite collection is opened and the
``cards`` rows are rewritten with real intervals, ease, repetition counts, and the FSRS memory
state Anki reads from ``card.data``. Without that, importing a deck you have been reviewing for
a month resets it to day one, which would make the feature worse than useless for exactly the
users it is aimed at.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from ..db.enums import CardState, CardType
from ..db.models import Card, Source
from ..db.session import session_scope
from ..db.types import utcnow
from ..errors import ValidationError

log = logging.getLogger(__name__)

#: Stable ids. Reusing them means re-importing an updated deck updates the existing notes in
#: Anki rather than creating a second copy of every card.
BASIC_MODEL_ID = 1607392319
CLOZE_MODEL_ID = 1607392320
DEFAULT_DECK_ID = 2059400110

_CSS = """
.card {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  font-size: 19px;
  text-align: left;
  color: #1a1a1a;
  background: #ffffff;
  padding: 18px 22px;
  line-height: 1.55;
}
.nightMode .card, .night_mode .card { color: #e8e8e8; background: #1e1e1e; }
hr#answer { border: none; border-top: 1px solid #d0d0d0; margin: 16px 0; }
.nightMode hr#answer { border-top-color: #3a3a3a; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
.cloze { font-weight: 600; color: #2563eb; }
.nightMode .cloze { color: #7aa7ff; }
.src { margin-top: 14px; font-size: 12px; opacity: 0.55; }
"""


def _basic_model():  # type: ignore[no-untyped-def]
    import genanki

    return genanki.Model(
        BASIC_MODEL_ID,
        "quicklearn Basic",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Source"}],
        templates=[
            {
                "name": "Recall",
                "qfmt": "{{Front}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Back}}'
                '{{#Source}}<div class="src">{{Source}}</div>{{/Source}}',
            }
        ],
        css=_CSS,
    )


def _reverse_model():  # type: ignore[no-untyped-def]
    import genanki

    return genanki.Model(
        BASIC_MODEL_ID + 1,
        "quicklearn Basic (and reversed)",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Source"}],
        templates=[
            {
                "name": "Recall",
                "qfmt": "{{Front}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Back}}'
                '{{#Source}}<div class="src">{{Source}}</div>{{/Source}}',
            },
            {
                "name": "Recognise",
                "qfmt": "{{Back}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Front}}'
                '{{#Source}}<div class="src">{{Source}}</div>{{/Source}}',
            },
        ],
        css=_CSS,
    )


def _cloze_model():  # type: ignore[no-untyped-def]
    import genanki

    return genanki.Model(
        CLOZE_MODEL_ID,
        "quicklearn Cloze",
        fields=[{"name": "Text"}, {"name": "Extra"}, {"name": "Source"}],
        templates=[
            {
                "name": "Cloze",
                "qfmt": "{{cloze:Text}}",
                "afmt": "{{cloze:Text}}<br>{{Extra}}"
                '{{#Source}}<div class="src">{{Source}}</div>{{/Source}}',
            }
        ],
        css=_CSS,
        model_type=1,  # genanki.Model.CLOZE
    )


#: Anki card types, matching its `cards.type` column.
_ANKI_TYPE = {
    CardState.NEW: 0,
    CardState.LEARNING: 1,
    CardState.REVIEW: 2,
    CardState.RELEARNING: 3,
}


@dataclass(slots=True)
class ExportResult:
    path: Path
    card_count: int
    deck_name: str


def export_apkg(
    destination: Path,
    *,
    source_ids: list[int] | None = None,
    folder_id: int | None = None,
    deck_name: str = "quicklearn",
    preserve_scheduling: bool = True,
) -> ExportResult:
    import genanki

    with session_scope() as session:
        stmt = select(Card, Source.title).join(Source, Source.id == Card.source_id)
        if source_ids:
            stmt = stmt.where(Card.source_id.in_(source_ids))
        if folder_id is not None:
            stmt = stmt.where(Source.folder_id == folder_id)
        rows = session.execute(stmt.order_by(Card.id)).all()

        cards = [
            {
                "id": card.id,
                "type": card.type,
                "front": card.front,
                "back": card.back,
                "source_title": title,
                "due": card.due,
                "stability": card.stability,
                "difficulty": card.difficulty,
                "scheduled_days": card.scheduled_days,
                "reps": card.reps,
                "lapses": card.lapses,
                "state": card.state,
                "suspended": card.suspended,
            }
            for card, title in rows
        ]

    if not cards:
        raise ValidationError("There are no flashcards to export yet.")

    deck = genanki.Deck(DEFAULT_DECK_ID, deck_name)
    basic, reverse, cloze = _basic_model(), _reverse_model(), _cloze_model()

    # Maps our card id onto the note guid, so scheduling can be reattached after genanki
    # has assigned Anki's own row ids.
    guid_to_card: dict[str, dict[str, object]] = {}

    for card in cards:
        source_label = f"quicklearn · {card['source_title']}"
        guid = genanki.guid_for("quicklearn", str(card["id"]))

        if card["type"] is CardType.CLOZE:
            note = genanki.Note(
                model=cloze,
                fields=[str(card["front"]), str(card["back"]), source_label],
                guid=guid,
            )
        elif card["type"] is CardType.REVERSE:
            note = genanki.Note(
                model=reverse,
                fields=[str(card["front"]), str(card["back"]), source_label],
                guid=guid,
            )
        else:
            note = genanki.Note(
                model=basic,
                fields=[str(card["front"]), str(card["back"]), source_label],
                guid=guid,
            )

        deck.add_note(note)
        guid_to_card[guid] = card

    destination.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(str(destination))

    if preserve_scheduling:
        try:
            _apply_scheduling(destination, guid_to_card)
        except Exception as exc:
            # A deck that imports without history still beats no deck at all.
            log.warning("could not preserve scheduling state in the export: %s", exc)

    return ExportResult(path=destination, card_count=len(cards), deck_name=deck_name)


def _apply_scheduling(apkg_path: Path, guid_to_card: dict[str, dict[str, object]]) -> None:
    """Rewrite the packaged collection's ``cards`` rows with real scheduling state."""
    with tempfile.TemporaryDirectory(prefix="quicklearn-apkg-") as tmp:
        workdir = Path(tmp)
        with zipfile.ZipFile(apkg_path) as archive:
            archive.extractall(workdir)

        collection = next(
            (
                workdir / name
                for name in ("collection.anki2", "collection.anki21")
                if (workdir / name).exists()
            ),
            None,
        )
        if collection is None:
            log.debug("no collection database inside the package; skipping scheduling")
            return

        conn = sqlite3.connect(str(collection))
        try:
            crt_row = conn.execute("SELECT crt FROM col").fetchone()
            if not crt_row:
                return
            collection_created = int(crt_row[0])
            today = int((time.time() - collection_created) // 86400)
            now = utcnow()

            note_rows = conn.execute("SELECT id, guid FROM notes").fetchall()
            for note_id, guid in note_rows:
                card = guid_to_card.get(guid)
                if card is None:
                    continue

                state = card["state"]
                if state is CardState.NEW:
                    continue  # already correct as a new card

                interval = max(1, int(card["scheduled_days"] or 1))
                days_until_due = max(0, (card["due"] - now).days) if card["due"] else 0  # type: ignore[operator]
                anki_type = _ANKI_TYPE.get(state, 2)  # type: ignore[arg-type]

                # Anki stores ease in permille. FSRS difficulty is 1..10 where higher is
                # harder, so it is inverted into the 1300..3700 band Anki uses.
                difficulty = float(card["difficulty"] or 5.0)
                factor = int(max(1300, min(3700, 2500 + (5.0 - difficulty) * 200)))

                data = (
                    json.dumps({"s": card["stability"], "d": card["difficulty"], "dr": 0.9})
                    if card["stability"]
                    else ""
                )

                conn.execute(
                    """
                    UPDATE cards
                       SET type = ?, queue = ?, due = ?, ivl = ?, factor = ?,
                           reps = ?, lapses = ?, data = ?
                     WHERE nid = ?
                    """,
                    (
                        anki_type,
                        -1 if card["suspended"] else anki_type,
                        today + days_until_due,
                        interval,
                        factor,
                        int(card["reps"] or 0),
                        int(card["lapses"] or 0),
                        data,
                        note_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        # Rebuild the archive with the modified collection.
        rebuilt = workdir / "_rebuilt.apkg"
        with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in sorted(workdir.iterdir()):
                if entry.name == "_rebuilt.apkg" or entry.is_dir():
                    continue
                archive.write(entry, entry.name)
        shutil.move(str(rebuilt), str(apkg_path))


# --------------------------------------------------------------------------- import


@dataclass(slots=True)
class ImportResult:
    imported: int
    skipped: int
    source_id: int


_CLOZE_MARKER = "{{c"


def import_apkg(path: Path, *, folder_id: int | None = None) -> ImportResult:
    """Import notes from an Anki package as a new source.

    Only fields are imported, not scheduling: an imported deck's history belongs to the user's
    Anki collection, and claiming to have inherited it would misrepresent when cards are due.
    """
    from ..db.enums import SourceKind, SourceOrigin, SourceStatus
    from ..pipeline.acquire import create_source

    with tempfile.TemporaryDirectory(prefix="quicklearn-import-") as tmp:
        workdir = Path(tmp)
        try:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(workdir)
        except zipfile.BadZipFile as exc:
            raise ValidationError("That file is not a valid Anki package.") from exc

        collection = next(
            (
                workdir / name
                for name in ("collection.anki21", "collection.anki2")
                if (workdir / name).exists()
            ),
            None,
        )
        if collection is None:
            raise ValidationError(
                "No Anki collection found inside that package. Newer Anki exports using the "
                "compressed .anki21b format are not supported — re-export with "
                "'Support older Anki versions' enabled."
            )

        conn = sqlite3.connect(str(collection))
        try:
            rows = conn.execute("SELECT flds FROM notes").fetchall()
        finally:
            conn.close()

    parsed: list[tuple[CardType, str, str]] = []
    skipped = 0
    for (fields_blob,) in rows:
        # Anki separates fields with the 0x1f unit separator.
        fields = [f.strip() for f in str(fields_blob).split("\x1f")]
        fields = [_strip_html(f) for f in fields]
        if not fields or not fields[0]:
            skipped += 1
            continue

        if _CLOZE_MARKER in fields[0]:
            parsed.append((CardType.CLOZE, fields[0], fields[1] if len(fields) > 1 else ""))
        elif len(fields) >= 2 and fields[1]:
            parsed.append((CardType.BASIC, fields[0], fields[1]))
        else:
            skipped += 1

    if not parsed:
        raise ValidationError("No usable notes were found in that package.")

    source_id = create_source(
        title=f"Imported: {path.stem}",
        kind=SourceKind.TEXT,
        origin=SourceOrigin.UPLOAD,
        folder_id=folder_id,
        original_filename=path.name,
        status=SourceStatus.READY,
        metadata={"imported_from": "anki", "note_count": len(parsed)},
    )
    _persist_imported(source_id, parsed)
    return ImportResult(imported=len(parsed), skipped=skipped, source_id=source_id)


def import_csv(
    data: bytes, *, filename: str = "cards.csv", folder_id: int | None = None
) -> ImportResult:
    """Import a two-column CSV of front/back pairs."""
    from ..db.enums import SourceKind, SourceOrigin, SourceStatus
    from ..pipeline.acquire import create_source

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    reader = csv.reader(io.StringIO(text), dialect)
    parsed: list[tuple[CardType, str, str]] = []
    skipped = 0

    for index, row in enumerate(reader):
        cells = [cell.strip() for cell in row if cell.strip()]
        if len(cells) < 2:
            skipped += 1
            continue
        front, back = cells[0], cells[1]
        # Skip an obvious header row.
        if index == 0 and front.lower() in {"front", "question", "term"}:
            continue
        card_type = CardType.CLOZE if _CLOZE_MARKER in front else CardType.BASIC
        parsed.append((card_type, front, back))

    if not parsed:
        raise ValidationError(
            "No card rows found. The CSV needs at least two columns: front and back."
        )

    source_id = create_source(
        title=f"Imported: {Path(filename).stem}",
        kind=SourceKind.TEXT,
        origin=SourceOrigin.UPLOAD,
        folder_id=folder_id,
        original_filename=filename,
        status=SourceStatus.READY,
        metadata={"imported_from": "csv", "note_count": len(parsed)},
    )
    _persist_imported(source_id, parsed)
    return ImportResult(imported=len(parsed), skipped=skipped, source_id=source_id)


def _persist_imported(source_id: int, parsed: list[tuple[CardType, str, str]]) -> None:
    from ..db.search import upsert_many
    from ..db.session import raw_sqlite

    now = utcnow()
    with session_scope() as session:
        rows = [
            Card(
                source_id=source_id,
                type=card_type,
                front=front,
                back=back,
                due=now,
                state=CardState.NEW,
                step=0,
            )
            for card_type, front, back in parsed
        ]
        session.add_all(rows)
        session.flush()
        ids = [row.id for row in rows]

    with raw_sqlite() as conn:
        upsert_many(
            conn,
            "card",
            [
                (card_id, front, back, source_id)
                for card_id, (_, front, back) in zip(ids, parsed, strict=True)
            ],
        )


_TAG_RE = None


def _strip_html(value: str) -> str:
    """Reduce Anki's HTML fields to text.

    Anki stores fields as HTML. Left alone, imported cards show literal ``<div>`` markup in the
    review UI, which looks broken.
    """
    global _TAG_RE
    if _TAG_RE is None:
        import re

        _TAG_RE = re.compile(r"<[^>]+>")

    import html as html_module

    text = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("</div>", "\n").replace("</p>", "\n")
    text = _TAG_RE.sub("", text)
    return html_module.unescape(text).strip()
