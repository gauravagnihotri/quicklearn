"""Pydantic models describing every structured LLM response.

These are the contract between prompts and code. Deliberately flat and free of exotic typing:
they are converted to JSON Schema and handed to providers whose schema support ranges from
strict (OpenAI) to a restricted OpenAPI subset (Gemini) to none at all. A clever type here
becomes a runtime failure on somebody's provider.

Field constraints do double duty — they validate the response *and* document the requirement
for the model, since the schema is included in the prompt when native enforcement is absent.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BlockTypeLiteral = Literal[
    "heading", "para", "list", "table", "latex", "mermaid", "code", "callout"
]


def _require(*names: str) -> Any:
    """Local patch: mark fields required in the schema sent to the model, not in validation.

    Under structured output (vLLM, llama.cpp) a field the schema leaves optional is one the
    grammar lets the model skip for good. Gemma 4 happened to write them; Bonsai 2 left every
    quiz explanation empty. Defaults stay, so providers without enforcement still validate.
    Keep the last required field one the model writes last anyway: a required field after the
    point where the model thinks it's done leaves only whitespace legal (see ``LessonCheckOut``).
    """

    def extra(schema: dict[str, Any]) -> None:
        schema["required"] = [name for name in schema.get("properties", {}) if name in names]

    return extra


# --------------------------------------------------------------------------- relevance


class RelevanceScore(BaseModel):
    index: int
    relevance: float = Field(ge=0.0, le=1.0)


class RelevanceScores(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("scores"))

    scores: list[RelevanceScore] = Field(default_factory=list)


# --------------------------------------------------------------------------- notes


class OutlineSection(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("title", "thesis", "segment_ids"))

    title: str = Field(min_length=1, max_length=300)
    thesis: str = ""
    segment_ids: list[int] = Field(default_factory=list)


class Outline(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("sections"))

    sections: list[OutlineSection] = Field(default_factory=list)


def _require_type_and_text(schema: dict[str, Any]) -> None:
    # Local patch: vLLM's structured output emits properties in schema order, so with everything
    # optional a model that writes ``level`` or ``segment_ids`` first can never add ``text``.
    schema["required"] = ["type", "text"]


class NoteBlockOut(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_type_and_text)

    # Local patch: field order is the order a model writes in under structured output, so the
    # attributes that say what a block is come before ``text``. With ``text`` second, callouts got
    # their variant name ("warning") as their only content.
    type: BlockTypeLiteral = "para"
    #: heading only
    level: int | None = Field(default=None, ge=1, le=6)
    #: callout only — key | warning | example | cue | summary
    variant: str | None = None
    #: code only
    language: str | None = None
    text: str = ""
    #: list only
    items: list[str] = Field(default_factory=list)
    #: table only
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    segment_ids: list[int] = Field(default_factory=list)

    @field_validator("segment_ids", mode="before")
    @classmethod
    def _coerce_segment_ids(cls, value: object) -> list[int]:
        """Accept a bare int or a list of stringified ints.

        Models return ``"segment_ids": 12`` or ``["12", "13"]`` often enough that rejecting
        those would discard otherwise-perfect blocks over a formatting quibble.
        """
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, list):
            result = []
            for item in value:
                try:
                    result.append(int(item))
                except (TypeError, ValueError):
                    continue
            return result
        return []


class SectionBlocks(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("blocks"))

    blocks: list[NoteBlockOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- concepts & study


class ConceptOut(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("name", "definition"))

    name: str = Field(min_length=1, max_length=300)
    definition: str = ""
    importance: int = Field(default=3, ge=1, le=5)
    segment_ids: list[int] = Field(default_factory=list)


class ConceptList(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("concepts"))

    concepts: list[ConceptOut] = Field(default_factory=list)


class CardOut(BaseModel):
    type: Literal["basic", "cloze", "reverse"] = "basic"
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    concept_id: int | None = None
    segment_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _infer_cloze(self) -> CardOut:
        """Local patch: models often write ``{{c1::...}}`` but omit ``type``, which defaults to
        basic and shows the answer on the front."""
        # A single colon (``{{c1:x}}``) is a common slip that stops the blank rendering.
        self.front = re.sub(r"\{\{c(\d+):(?!:)", r"{{c\1::", self.front)
        if self.type == "basic" and "{{c" in self.front:
            self.type = "cloze"
        return self


class CardList(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("cards"))

    cards: list[CardOut] = Field(default_factory=list)


class QuizItemOut(BaseModel):
    # Local patch: ``explanation`` comes before ``answer`` so the last required field is the
    # answer, which the model writes last anyway.
    model_config = ConfigDict(
        json_schema_extra=_require("type", "question", "options", "explanation", "answer")
    )

    type: Literal["mcq", "cloze", "short", "free"] = "mcq"
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    explanation: str = ""
    answer: str = ""
    difficulty: int = Field(default=3, ge=1, le=5)
    concept_id: int | None = None
    segment_ids: list[int] = Field(default_factory=list)


class QuizItemList(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("items"))

    items: list[QuizItemOut] = Field(default_factory=list)


class GradeResult(BaseModel):
    # Local patch: all required (a skipped ``score`` grades every answer 0 and wrong), with the
    # feedback first so the verdict follows the reasoning.
    model_config = ConfigDict(json_schema_extra=_require("feedback", "score", "is_correct"))

    feedback: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    is_correct: bool = False


# --------------------------------------------------------------------------- learn (local patch)
#
# The Learn tab's course. Every field is required and short descriptive fields come before long
# text, for the same vLLM structured-output reason as ``NoteBlockOut``: properties are written
# in schema order and an optional field can be skipped for good.


def _require_all(schema: dict[str, Any]) -> None:
    schema["required"] = list(schema.get("properties", {}))


class LessonPlan(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_all)

    title: str = Field(min_length=1, max_length=200)
    #: Finishes the sentence "By the end of this lesson you can ..."
    objective: str = ""
    concept_ids: list[int] = Field(default_factory=list)
    segment_ids: list[int] = Field(default_factory=list)


class CourseOutline(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_all)

    lessons: list[LessonPlan] = Field(default_factory=list)


def _require_all_but_explanation(schema: dict[str, Any]) -> None:
    # With ``explanation`` required, Gemma 4 finished a short-answer check after ``answer``,
    # found only whitespace allowed by the grammar, and wrote whitespace until max_tokens.
    # Optional and last, it can close the object instead.
    schema["required"] = [name for name in schema.get("properties", {}) if name != "explanation"]


class LessonCheckOut(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_all_but_explanation)

    type: Literal["mcq", "short"] = "mcq"
    concept_id: int | None = None
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    explanation: str = ""


class LessonOut(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_all)

    why: str = ""
    body: str = ""
    example: str = ""
    checks: list[LessonCheckOut] = Field(default_factory=list)
    try_it: str = ""


class ReferenceGroup(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_all)

    label: str = ""
    body: str = ""


class ReferenceSheetOut(BaseModel):
    # One free-form ``sheet`` string came back as just the course title, with the whole sheet
    # left in the thinking; labelled groups get written out.
    model_config = ConfigDict(json_schema_extra=_require_all)

    groups: list[ReferenceGroup] = Field(default_factory=list)


# --------------------------------------------------------------------------- chat & podcast


class RerankScore(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("id", "score"))

    id: int
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class RerankScores(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("scores"))

    scores: list[RerankScore] = Field(default_factory=list)


class FollowupQuestions(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("questions"))

    questions: list[str] = Field(default_factory=list)


class PodcastLine(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("speaker", "text"))

    speaker: Literal["Host A", "Host B"] = "Host A"
    text: str = Field(min_length=1)
    emotion: str | None = None


class PodcastScript(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("lines"))

    lines: list[PodcastLine] = Field(default_factory=list)


class TitleSuggestion(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require("title"))

    title: str = Field(default="", max_length=200)
