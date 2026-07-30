"""SQLAlchemy 2.0 typed ORM models.

Implements the entity set in PRD §5. Two structural notes:

* ``Segment.id`` is a plain integer primary key because it doubles as the ``rowid`` of the
  sqlite-vec virtual table holding that segment's embedding. Keeping them aligned means a
  vector hit resolves to a segment with no join table.
* ``segment_ids`` appears on every generated artifact — note blocks, concepts, cards, quiz
  items, chat messages. That back-pointer is what makes one-click click-to-source possible
  and is the primary structural defence against hallucination: an artifact that cannot name
  its evidence is visibly suspect.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Column as SAColumn
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import (
    BlockType,
    CardState,
    CardType,
    ChatScope,
    JobStatus,
    JobType,
    MessageRole,
    PodcastStatus,
    QuizItemType,
    QuizMode,
    QuizSessionStatus,
    SourceKind,
    SourceOrigin,
    SourceStatus,
)
from .types import JSONDict, UTCDateTime, utcnow


def _enum(python_enum: type, name: str) -> Enum:
    """String-valued enum column. Stores ``"ready"``, not ``"READY"``."""
    return Enum(
        python_enum,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    pass


source_tags = Table(
    "source_tags",
    Base.metadata,
    SAColumn("source_id", ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
    SAColumn("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), index=True
    )
    color: Mapped[str | None] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    children: Mapped[list[Folder]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped[Folder | None] = relationship(back_populates="children", remote_side=[id])
    sources: Mapped[list[Source]] = relationship(back_populates="folder")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    color: Mapped[str | None] = mapped_column(String(32))


class Source(Base):
    """A single piece of ingested material."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind, "source_kind"))
    origin: Mapped[SourceOrigin] = mapped_column(_enum(SourceOrigin, "source_origin"))
    status: Mapped[SourceStatus] = mapped_column(
        _enum(SourceStatus, "source_status"), default=SourceStatus.PENDING, index=True
    )

    original_filename: Mapped[str | None] = mapped_column(String(512))
    media_path: Mapped[str | None] = mapped_column(String(1024))
    audio_path: Mapped[str | None] = mapped_column(String(1024))
    duration_sec: Mapped[float | None] = mapped_column(Float)
    page_count: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    #: Free-form provenance: url, author, course, lecture_date, recording markers.
    #: Mapped away from ``metadata`` because that name is reserved on declarative classes.
    meta: Mapped[JSONDict] = mapped_column("metadata", JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    folder: Mapped[Folder | None] = relationship(back_populates="sources")
    tags: Mapped[list[Tag]] = relationship(secondary=source_tags, lazy="selectin")
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    segments: Mapped[list[Segment]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    notes: Mapped[list[Note]] = relationship(back_populates="source", cascade="all, delete-orphan")
    concepts: Mapped[list[Concept]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    cards: Mapped[list[Card]] = relationship(back_populates="source", cascade="all, delete-orphan")
    quiz_items: Mapped[list[QuizItem]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), unique=True, index=True
    )
    language: Mapped[str | None] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    #: Provider response, kept verbatim. Source data is never destroyed.
    raw: Mapped[JSONDict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    source: Mapped[Source] = relationship(back_populates="transcript")


class Segment(Base):
    """Atomic unit of source content — the anchor everything else points at."""

    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("source_id", "index", name="uq_segment_source_index"),
        Index("ix_segment_source_start", "source_id", "start_ms"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    transcript_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE")
    )
    index: Mapped[int] = mapped_column(Integer)

    # Time-based sources
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    # Document-based sources
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSON)

    speaker: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)

    #: 0.0–1.0 instructional-content score from the relevance pass (PRD §6.4). Segments below
    #: the user's threshold stay in the transcript but are withheld from generation context.
    relevance: Mapped[float] = mapped_column(Float, default=1.0)
    #: Set when the user hit the marker button during recording, so the relevance pass can
    #: treat the surrounding window as high-importance.
    is_marked: Mapped[bool] = mapped_column(Boolean, default=False)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    source: Mapped[Source] = relationship(back_populates="segments")

    @property
    def start_sec(self) -> float | None:
        return None if self.start_ms is None else self.start_ms / 1000.0


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    #: TipTap/ProseMirror document.
    doc: Mapped[JSONDict] = mapped_column(JSON, default=dict)
    outline: Mapped[list[Any]] = mapped_column(JSON, default=list)
    style: Mapped[str] = mapped_column(String(32), default="detailed")
    version: Mapped[int] = mapped_column(Integer, default=1)
    #: True once the user has edited by hand — regeneration then offers a new version rather
    #: than silently overwriting their work (PRD §12.3).
    is_dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    source: Mapped[Source] = relationship(back_populates="notes")
    blocks: Mapped[list[NoteBlock]] = relationship(
        back_populates="note", cascade="all, delete-orphan", order_by="NoteBlock.index"
    )


class NoteBlock(Base):
    """Denormalised note content, for citation resolution and full-text search."""

    __tablename__ = "note_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    type: Mapped[BlockType] = mapped_column(_enum(BlockType, "block_type"))
    text: Mapped[str] = mapped_column(Text, default="")
    segment_ids: Mapped[list[int]] = mapped_column(JSON, default=list)

    note: Mapped[Note] = relationship(back_populates="blocks")


class Concept(Base):
    """Extracted knowledge unit — the spine of the study engine.

    Cards and quiz items generate from concepts rather than raw transcript, which is what
    makes mastery coherent: a concept is the thing that can be known or not known.
    """

    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    definition: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    segment_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    mastery: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    source: Mapped[Source] = relationship(back_populates="concepts")
    cards: Mapped[list[Card]] = relationship(back_populates="concept", cascade="all, delete-orphan")


class Card(Base):
    """A flashcard plus its FSRS-6 scheduling state.

    ``elapsed_days``/``scheduled_days``/``reps``/``lapses`` are maintained by this application
    rather than by the ``fsrs`` library — FSRS-6 dropped them from its own ``Card`` — because
    Anki's ``.apkg`` format needs the interval and repetition counts to round-trip.
    """

    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[CardType] = mapped_column(_enum(CardType, "card_type"), default=CardType.BASIC)
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    segment_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- FSRS state ---
    due: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    #: Position within the configured learning/relearning step ladder (new in FSRS-6).
    step: Mapped[int | None] = mapped_column(Integer, default=0)
    elapsed_days: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_days: Mapped[int] = mapped_column(Integer, default=0)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[CardState] = mapped_column(
        _enum(CardState, "card_state"), default=CardState.NEW, index=True
    )
    last_review: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    concept: Mapped[Concept | None] = relationship(back_populates="cards")
    source: Mapped[Source | None] = relationship(back_populates="cards")
    reviews: Mapped[list[Review]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class Review(Base):
    """Every card answer, retained so FSRS parameters can be optimised from real history."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    #: Card scheduling state prior to this review, so a rating can be undone.
    state_before: Mapped[JSONDict] = mapped_column(JSON, default=dict)

    card: Mapped[Card] = relationship(back_populates="reviews")


class QuizItem(Base):
    __tablename__ = "quiz_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    type: Mapped[QuizItemType] = mapped_column(_enum(QuizItemType, "quiz_item_type"))
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    answer: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    segment_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    #: Items minted on demand by adaptive practice, rather than part of a saved bank.
    ephemeral: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    source: Mapped[Source | None] = relationship(back_populates="quiz_items")
    concept: Mapped[Concept | None] = relationship()


class QuizSession(Base):
    """Not in PRD §5, but the ``/quiz/session/*`` endpoints in §8 require server-side state."""

    __tablename__ = "quiz_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[QuizMode] = mapped_column(_enum(QuizMode, "quiz_mode"))
    status: Mapped[QuizSessionStatus] = mapped_column(
        _enum(QuizSessionStatus, "quiz_session_status"), default=QuizSessionStatus.ACTIVE
    )
    source_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"))
    length: Mapped[int | None] = mapped_column(Integer)
    served: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)

    attempts: Mapped[list[QuizAttempt]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("quiz_sessions.id", ondelete="CASCADE"), index=True
    )
    quiz_item_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_items.id", ondelete="CASCADE"), index=True
    )
    response: Mapped[str] = mapped_column(Text, default="")
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    feedback: Mapped[str] = mapped_column(Text, default="")
    attempted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    session: Mapped[QuizSession | None] = relationship(back_populates="attempts")
    item: Mapped[QuizItem] = relationship()


class Podcast(Base):
    __tablename__ = "podcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    #: Multi-source podcasts (a week of notes) keep the full list here.
    source_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(512), default="Podcast")
    #: ``[{speaker, text, emotion?}]``
    script: Mapped[list[Any]] = mapped_column(JSON, default=list)
    audio_path: Mapped[str | None] = mapped_column(String(1024))
    duration_sec: Mapped[float | None] = mapped_column(Float)
    target_minutes: Mapped[int] = mapped_column(Integer, default=10)
    voice_config: Mapped[JSONDict] = mapped_column(JSON, default=dict)
    status: Mapped[PodcastStatus] = mapped_column(
        _enum(PodcastStatus, "podcast_status"), default=PodcastStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[ChatScope] = mapped_column(_enum(ChatScope, "chat_scope"))
    scope_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512), default="New chat")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    #: Resolved segment ids backing this answer.
    citations: Mapped[list[int]] = mapped_column(JSON, default=list)
    followups: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    chat: Mapped[Chat] = relationship(back_populates="messages")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "id"),
        UniqueConstraint("source_id", "type", "dedupe_key", name="uq_job_idempotency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[JobType] = mapped_column(_enum(JobType, "job_type"))
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.QUEUED, index=True
    )
    payload: Mapped[JSONDict] = mapped_column(JSON, default=dict)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    #: Enforces "re-running a pipeline stage must not duplicate rows" (PRD §11) at the
    #: database level rather than by convention.
    dedupe_key: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)

    children: Mapped[list[Job]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped[Job | None] = relationship(back_populates="children", remote_side=[id])


class Setting(Base):
    """Provider config and API keys. Deliberately the database rather than env vars, so that
    setup happens entirely in the UI and no config file ever needs hand-editing."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class LlmCall(Base):
    """Every LLM call, logged. Non-negotiable per PRD §11 — it powers the cost dashboard and
    is the only way to debug a quality regression after the fact."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128))
    task_class: Mapped[str] = mapped_column(String(32), default="standard")
    purpose: Mapped[str] = mapped_column(String(64), default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    #: Audio seconds for STT, characters for TTS. Those are not token-priced.
    audio_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    characters: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class UploadSession(Base):
    """Server-side state for resumable chunked uploads and in-progress browser recordings."""

    __tablename__ = "upload_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(1024))
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    received_bytes: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    is_recording: Mapped[bool] = mapped_column(Boolean, default=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
