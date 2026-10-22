"""Local patch: the schema sent to a structured-output model must require what the app needs.

Under grammar enforcement (vLLM, llama.cpp) an optional field can be skipped for good, which is
how Bonsai 2 returned a quiz with every explanation empty.
"""

from __future__ import annotations

import pytest

from quicklearn.pipeline import schemas


@pytest.mark.parametrize(
    ("model", "needed"),
    [
        (schemas.QuizItemOut, {"type", "question", "options", "explanation", "answer"}),
        (schemas.GradeResult, {"feedback", "score", "is_correct"}),
        (schemas.ConceptOut, {"name", "definition"}),
        (schemas.OutlineSection, {"title", "thesis", "segment_ids"}),
        (schemas.RerankScore, {"id", "score"}),
        (schemas.PodcastLine, {"speaker", "text"}),
        (schemas.TitleSuggestion, {"title"}),
    ],
)
def test_essential_fields_are_required(model: type, needed: set[str]) -> None:
    assert needed <= set(model.model_json_schema()["required"])


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (schemas.Outline, "sections"),
        (schemas.SectionBlocks, "blocks"),
        (schemas.ConceptList, "concepts"),
        (schemas.CardList, "cards"),
        (schemas.QuizItemList, "items"),
        (schemas.RelevanceScores, "scores"),
        (schemas.RerankScores, "scores"),
        (schemas.FollowupQuestions, "questions"),
        (schemas.PodcastScript, "lines"),
    ],
)
def test_list_wrappers_cannot_come_back_empty_objects(model: type, field: str) -> None:
    # ``{}`` would otherwise be valid and a job would "succeed" with nothing in it
    assert model.model_json_schema()["required"] == [field]


def test_quiz_answer_is_the_last_required_field() -> None:
    # A required field after the point the model thinks it's done leaves only whitespace legal
    schema = schemas.QuizItemOut.model_json_schema()
    required = [name for name in schema["properties"] if name in schema["required"]]
    assert required[-1] == "answer"


def test_missing_fields_still_validate_without_enforcement() -> None:
    item = schemas.QuizItemOut.model_validate({"question": "Why?"})
    assert item.explanation == "" and item.answer == ""
