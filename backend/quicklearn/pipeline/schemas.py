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


# --------------------------------------------------------------------------- relevance


class RelevanceScore(BaseModel):
    index: int
    relevance: float = Field(ge=0.0, le=1.0)


class RelevanceScores(BaseModel):
    scores: list[RelevanceScore] = Field(default_factory=list)


# --------------------------------------------------------------------------- notes


class OutlineSection(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    thesis: str = ""
    segment_ids: list[int] = Field(default_factory=list)


class Outline(BaseModel):
    sections: list[OutlineSection] = Field(default_factory=list)


def _require_type_and_text(schema: dict[str, Any]) -> None:
    # Local patch: vLLM's structured output emits properties in schema order, so with everything
    # optional a model that writes ``level`` or ``segment_ids`` first can never add ``text``.
    schema["required"] = ["type", "text"]


class NoteBlockOut(BaseModel):
    model_config = ConfigDict(json_schema_extra=_require_type_and_text)

    type: BlockTypeLiteral = "para"
    text: str = ""
    #: heading only
    level: int | None = Field(default=None, ge=1, le=6)
    #: code only
    language: str | None = None
    #: callout only — key | warning | example | cue | summary
    variant: str | None = None
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
    blocks: list[NoteBlockOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- concepts & study


class ConceptOut(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    definition: str = ""
    importance: int = Field(default=3, ge=1, le=5)
    segment_ids: list[int] = Field(default_factory=list)


class ConceptList(BaseModel):
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
    cards: list[CardOut] = Field(default_factory=list)


class QuizItemOut(BaseModel):
    type: Literal["mcq", "cloze", "short", "free"] = "mcq"
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    explanation: str = ""
    difficulty: int = Field(default=3, ge=1, le=5)
    concept_id: int | None = None
    segment_ids: list[int] = Field(default_factory=list)


class QuizItemList(BaseModel):
    items: list[QuizItemOut] = Field(default_factory=list)


class GradeResult(BaseModel):
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    is_correct: bool = False
    feedback: str = ""


# --------------------------------------------------------------------------- chat & podcast


class RerankScore(BaseModel):
    id: int
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class RerankScores(BaseModel):
    scores: list[RerankScore] = Field(default_factory=list)


class FollowupQuestions(BaseModel):
    questions: list[str] = Field(default_factory=list)


class PodcastLine(BaseModel):
    speaker: Literal["Host A", "Host B"] = "Host A"
    text: str = Field(min_length=1)
    emotion: str | None = None


class PodcastScript(BaseModel):
    lines: list[PodcastLine] = Field(default_factory=list)


class TitleSuggestion(BaseModel):
    title: str = Field(default="", max_length=200)
