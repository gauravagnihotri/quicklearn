"""Enumerations shared between the ORM, the API schemas and the pipeline.

Stored as TEXT (``native_enum=False``) so migrations stay trivial on SQLite and the raw
database file remains readable with any sqlite3 client — a stated goal of the project.
"""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    YOUTUBE = "youtube"
    WEB = "web"
    TEXT = "text"
    IMAGE = "image"


class SourceOrigin(StrEnum):
    UPLOAD = "upload"
    RECORDING = "recording"
    URL = "url"
    PASTE = "paste"


class SourceStatus(StrEnum):
    #: Browser recording still in progress, or abandoned mid-flight. Not in the original
    #: spec, but the "Unfinished recording — process anyway?" recovery flow needs a state
    #: that is distinguishable from a queued upload.
    RECORDING = "recording"
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(StrEnum):
    INGEST = "ingest"
    NORMALIZE = "normalize"
    TRANSCRIBE = "transcribe"
    EXTRACT = "extract"
    SEGMENT = "segment"
    FILTER = "filter"
    GENERATE_NOTES = "generate_notes"
    #: Local patch: the ADHD-friendly "Simplified" note, written after ingest finishes.
    GENERATE_SIMPLIFIED_NOTES = "generate_simplified_notes"
    GENERATE_CONCEPTS = "generate_concepts"
    GENERATE_CARDS = "generate_cards"
    GENERATE_QUIZ = "generate_quiz"
    GENERATE_PODCAST = "generate_podcast"
    INDEX = "index"


class BlockType(StrEnum):
    HEADING = "heading"
    PARA = "para"
    LIST = "list"
    TABLE = "table"
    LATEX = "latex"
    MERMAID = "mermaid"
    CODE = "code"
    CALLOUT = "callout"


class CardType(StrEnum):
    BASIC = "basic"
    CLOZE = "cloze"
    REVERSE = "reverse"


class CardState(StrEnum):
    """Card lifecycle state.

    FSRS-6 dropped the ``new`` state that FSRS-4 had — a fresh card is ``learning`` with
    ``step=0`` and no stability. ``NEW`` is retained here purely as a presentation concern
    (due-count badges, "new vs review" split in the review UI) and is derived from
    ``last_review IS NULL``; it is never handed to the scheduler.
    """

    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


class QuizItemType(StrEnum):
    MCQ = "mcq"
    CLOZE = "cloze"
    SHORT = "short"
    FREE = "free"


class QuizMode(StrEnum):
    PRACTICE = "practice"
    TEST = "test"
    CUMULATIVE = "cumulative"


class QuizSessionStatus(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"
    ABANDONED = "abandoned"


class PodcastStatus(StrEnum):
    PENDING = "pending"
    SCRIPTING = "scripting"
    SYNTHESIZING = "synthesizing"
    ASSEMBLING = "assembling"
    READY = "ready"
    FAILED = "failed"


class ChatScope(StrEnum):
    SOURCE = "source"
    FOLDER = "folder"
    GLOBAL = "global"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class TaskClass(StrEnum):
    """Per-task model routing classes. See PRD §4.3."""

    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class NoteStyle(StrEnum):
    CONCISE = "concise"
    DETAILED = "detailed"
    CORNELL = "cornell"
    EXAM_PREP = "exam_prep"


class ProviderKind(StrEnum):
    LLM = "llm"
    STT = "stt"
    TTS = "tts"
    EMBEDDING = "embedding"
