"""Deterministic offline LLM provider.

Serves two purposes, and the second is why it earns its place in the shipped product rather
than living in the test tree:

1. **The test suite runs with no network and no API keys.** Every pipeline test exercises real
   code paths against this provider.
2. **A new user can click through the entire product before signing up for anything.** Upload a
   file, watch notes build, review cards, take a quiz, read a cited chat answer. Nothing about
   the experience is stubbed out except the intelligence.

To do that honestly it must return *schema-valid* output for whatever structured request it
receives, built from the actual input text rather than lorem ipsum. So it walks the requested
JSON Schema and fills it with sentences drawn from the prompt. The results are not intelligent —
they are shaped correctly and recognisably derived from your material, which is what makes the
UI legible while the plumbing is verified.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ..base import LLMProvider, LLMResponse, Message, ModelInfo

MODELS = ["mock-fast", "mock-standard", "mock-deep"]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _flatten(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content
    return " ".join(
        part.get("text", "") for part in content if isinstance(part, dict) and "text" in part
    )


#: Openings that mark a sentence as an instruction to the model rather than source material.
_INSTRUCTION_START = (
    "you are",
    "you will",
    "write ",
    "return ",
    "extract ",
    "produce ",
    "score ",
    "rate ",
    "grade ",
    "do not ",
    "don't ",
    "use ",
    "prefer ",
    "avoid ",
    "output ",
    "respond ",
    "give ",
    "judge ",
    "keep ",
    "set ",
    "include ",
    "omit ",
    "match ",
    "preserve ",
    "ignore ",
    "answer only",
    "cite ",
    "no ",
    "each ",
    "every block",
    "at most",
    "between ",
    "target:",
    "requirements",
    "rules",
    "guidance",
    "style:",
)


def _content_only(text: str) -> str:
    """Isolate the source material from the surrounding instructions.

    Every prompt template puts the actual material last, under a markdown heading —
    "## Source segments", "## Concepts", "## Material". Taking everything after the final
    heading gives the mock real content to echo instead of the instructions telling it what to
    do, which is the difference between a demo note that reads like a lecture and one that
    reads like a system prompt.
    """
    marker = text.rfind("\n## ")
    if marker != -1:
        # +1 skips the newline so the split lands on the heading line itself, which is then
        # discarded — otherwise the heading text leaks into the generated content.
        parts = text[marker + 1 :].split("\n", 1)
        if len(parts) > 1 and len(parts[1].strip()) > 40:
            return parts[1]
    return text


#: "[12] (1:30) " — the id and timestamp prefix on every listed segment.
_SEGMENT_PREFIX = re.compile(r"^\[\d+\]\s*(\([^)]*\)\s*)?")
_BULLET_PREFIX = re.compile(r"^[-*+]\s+")
#: Structural lines in the rendered prompts that are not source content.
_METADATA_LINE = re.compile(r"^(#{1,6}\s|segment_ids\s*:|id\s*:|\||>\s|```)")


def _clean_body(text: str) -> str:
    """Strip prompt scaffolding line by line, leaving only prose from the source."""
    lines: list[str] = []
    for raw in _content_only(text).splitlines():
        line = raw.strip()
        if not line or _METADATA_LINE.match(line):
            continue
        # Order matters: remove the segment prefix before the bullet prefix, or "- [3] text"
        # leaves a stray "3]" behind.
        line = _SEGMENT_PREFIX.sub("", line)
        line = _BULLET_PREFIX.sub("", line)
        line = _SEGMENT_PREFIX.sub("", line).strip()
        if not line or "segment_ids" in line:
            continue
        lines.append(line)
    return " ".join(lines)


def _sentences(text: str) -> list[str]:
    candidates = []
    for raw in _SENTENCE_SPLIT.split(_clean_body(text)):
        sentence = raw.strip()
        if len(sentence) < 25 or sentence.lower().startswith(_INSTRUCTION_START):
            continue
        candidates.append(sentence)
    return candidates or ["This section covers the core material from the source."]


#: Words that are common in prompt instructions and would make poor concept names.
_STOP_PHRASES = {
    "Segments",
    "Segment",
    "Concepts",
    "Concept",
    "Material",
    "Source",
    "Sources",
    "Requirements",
    "Rules",
    "Guidance",
    "Between",
    "Ignore",
    "Preserve",
    "Section",
    "Sections",
    "Because",
    "However",
    "Therefore",
    "Something",
    "Everything",
}

#: Long but contentless English words, filtered so concept names land on real terminology.
_COMMON_WORDS = {
    "actually",
    "although",
    "anything",
    "becomes",
    "brought",
    "concrete",
    "concretely",
    "consider",
    "continue",
    "different",
    "entirely",
    "especially",
    "everyone",
    "example",
    "examples",
    "explain",
    "explained",
    "following",
    "generally",
    "however",
    "important",
    "including",
    "interest",
    "material",
    "mentioned",
    "necessary",
    "nothing",
    "obviously",
    "particular",
    "particularly",
    "possible",
    "probably",
    "provides",
    "question",
    "questions",
    "reading",
    "really",
    "remember",
    "similar",
    "something",
    "sometimes",
    "specific",
    "students",
    "suppose",
    "therefore",
    "thinking",
    "together",
    "typically",
    "understand",
    "usually",
    "whatever",
    "whether",
    "without",
    "yourself",
    "problem",
    "deadline",
    "administrative",
    "practical",
    "practically",
    "commonly",
    "worth",
}


def _key_phrases(text: str) -> list[str]:
    """Technical-looking terms from the source, used as stand-in concept names.

    Ranked by frequency then length, so a lecture about eigenvalues yields "Eigenvectors" and
    "Determinant" rather than "Today" and "Before" — which makes a mock-generated flashcard
    deck legible enough to actually evaluate the review UI against.
    """
    words = re.findall(r"\b[a-zA-Z][a-zA-Z-]{6,}\b", _clean_body(text))
    counts: dict[str, int] = {}
    for word in words:
        cleaned = word.strip("-").capitalize()
        if cleaned in _STOP_PHRASES or cleaned.lower() in _COMMON_WORDS:
            continue
        counts[cleaned] = counts.get(cleaned, 0) + 1

    ranked = sorted(counts, key=lambda w: (-counts[w], -len(w), w))
    return ranked or ["Key idea"]


class MockSchemaFiller:
    """Builds a schema-valid instance, drawing text from the supplied context."""

    def __init__(self, context: str, seed: int, defs: dict[str, Any] | None = None) -> None:
        self.sentences = _sentences(context)
        self.phrases = _key_phrases(context)
        self.rng = random.Random(seed)
        # Pydantic renders any nested model as a `$ref` into `$defs`. Without resolving those,
        # every nested object would degrade to a bare string and fail validation.
        self.defs = defs or {}
        self._sentence_cursor = 0
        self._phrase_cursor = 0
        self._enum_cursor = 0
        self._segment_cursor = 0

    def sentence(self) -> str:
        value = self.sentences[self._sentence_cursor % len(self.sentences)]
        self._sentence_cursor += 1
        return value

    def phrase(self) -> str:
        value = self.phrases[self._phrase_cursor % len(self.phrases)]
        self._phrase_cursor += 1
        return value

    def build(self, schema: dict[str, Any], field: str = "", depth: int = 0) -> Any:
        schema = self._resolve(schema, depth)
        if depth > 8:
            return None

        if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
            # Rotate rather than always taking the first option, so a demo note contains a mix
            # of headings, paragraphs and callouts instead of eighteen headings.
            options = schema["enum"]
            choice = options[self._enum_cursor % len(options)]
            self._enum_cursor += 1
            return choice
        if "const" in schema:
            return schema["const"]

        declared = schema.get("type")
        if isinstance(declared, list):
            declared = next((t for t in declared if t != "null"), "string")

        if declared == "object" or "properties" in schema:
            properties: dict[str, Any] = schema.get("properties") or {}
            # Fill every declared property, not just the required ones — optional fields are
            # where interesting rendering lives (callout variants, code languages, speaker
            # emotion), and a demo with them all absent looks broken rather than minimal.
            return {
                key: self.build(sub if isinstance(sub, dict) else {}, key, depth + 1)
                for key, sub in properties.items()
            }

        if declared == "array":
            item_schema = schema.get("items")
            item_schema = item_schema if isinstance(item_schema, dict) else {"type": "string"}
            # Respect minItems as a floor, not as the answer — a schema saying "at least one
            # section" should still demo as several.
            count = max(int(schema.get("minItems") or 0), self._array_length(field))
            count = min(count, int(schema.get("maxItems") or count))
            return [self.build(item_schema, field, depth + 1) for _ in range(max(1, count))]

        if declared == "integer":
            return self._number(schema, field, integral=True)
        if declared == "number":
            return self._number(schema, field, integral=False)
        if declared == "boolean":
            return False
        if declared == "null":
            return None
        return self._string(field)

    def _resolve(self, schema: dict[str, Any], depth: int) -> dict[str, Any]:
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target = self.defs.get(ref.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                merged = {k: v for k, v in schema.items() if k != "$ref"}
                return self._resolve({**target, **merged}, depth + 1)

        for union_key in ("anyOf", "oneOf"):
            options = schema.get(union_key)
            if isinstance(options, list):
                non_null = [o for o in options if isinstance(o, dict) and o.get("type") != "null"]
                if non_null:
                    return self._resolve(non_null[0], depth + 1)
        return schema

    def _array_length(self, field: str) -> int:
        if field in {"segment_ids", "segmentIds"}:
            return 2
        if field in {"options", "choices"}:
            return 4  # MCQ wants exactly four
        if field in {"sections", "blocks", "concepts", "cards", "items", "lines"}:
            return 5  # enough content to make the UI look inhabited
        return 3

    def _number(self, schema: dict[str, Any], field: str, *, integral: bool) -> float | int:
        low = schema.get("minimum", schema.get("exclusiveMinimum", 0))
        high = schema.get("maximum", schema.get("exclusiveMaximum", 5 if integral else 1.0))
        try:
            low_f, high_f = float(low), float(high)
        except (TypeError, ValueError):
            low_f, high_f = 0.0, 1.0
        if high_f < low_f:
            low_f, high_f = high_f, low_f

        if field in {"segment_ids", "segmentIds"}:
            # Walk forward so anchors spread across the source rather than all pointing at
            # segment zero. The pipeline clamps these to real ids regardless.
            self._segment_cursor += 1
            return self._segment_cursor
        if field in {"index", "segment_index", "level"}:
            return int(low_f) if field != "level" else 2
        if field in {"relevance", "score", "mastery", "confidence"}:
            # Comfortably above the 0.3 relevance cutoff, so mock content is not all filtered
            # out and the notes stage actually has material to work with.
            return round(min(max(0.82, low_f), high_f), 2)
        midpoint = low_f + (high_f - low_f) * 0.6
        return round(midpoint) if integral else round(midpoint, 2)

    def _string(self, field: str) -> str:
        lowered = field.lower()
        if lowered in {"title", "name", "heading", "concept", "term"}:
            return self.phrase()
        if lowered in {"speaker"}:
            return self.rng.choice(["Host A", "Host B"])
        if lowered in {"type", "variant", "kind"}:
            return "para"
        if lowered in {"question"}:
            return f"What does the material say about {self.phrase().lower()}?"
        if lowered in {"answer", "back"}:
            return self.sentence()
        if lowered in {"front"}:
            return f"Define: {self.phrase()}"
        if lowered in {"definition", "thesis", "explanation", "summary", "feedback"}:
            return self.sentence()
        if lowered in {"language"}:
            return "en"
        return self.sentence()


class MockLLM(LLMProvider):
    name = "mock"
    label = "Mock (offline demo)"
    requires_api_key = False
    is_local = True

    @property
    def context_window(self) -> int:
        return 128_000

    @property
    def supports_structured_output(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=m, name=m, context_window=128_000) for m in MODELS]

    @staticmethod
    def _context_and_seed(messages: Sequence[Message]) -> tuple[str, int]:
        joined = "\n".join(_flatten(m["content"]) for m in messages)
        seed = int(hashlib.sha256(joined.encode()).hexdigest()[:8], 16)
        return joined, seed

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        context, seed = self._context_and_seed(messages)

        if json_schema is not None:
            defs = json_schema.get("$defs") or json_schema.get("definitions") or {}
            filler = MockSchemaFiller(context, seed, defs if isinstance(defs, dict) else {})
            text = json.dumps(filler.build(json_schema), ensure_ascii=False)
        else:
            text = self._prose(context, seed)

        # Rough but non-zero token accounting, so the cost dashboard and the llm_calls log have
        # realistic shapes to render during a demo.
        return LLMResponse(
            text=text,
            model=model or "mock-standard",
            prompt_tokens=max(1, len(context) // 4),
            completion_tokens=max(1, len(text) // 4),
            finish_reason="stop",
        )

    @staticmethod
    def _prose(context: str, seed: int) -> str:
        filler = MockSchemaFiller(context, seed)
        lines = [
            f"**{filler.phrase()}** — {filler.sentence()}",
            "",
            f"{filler.sentence()} {filler.sentence()}",
            "",
            f"In short: {filler.sentence()}",
            "",
            "_(Generated by the offline mock provider. Configure a real provider in "
            "Settings → Providers for actual analysis.)_",
        ]
        return "\n".join(lines)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        import asyncio

        context, seed = self._context_and_seed(messages)
        text = self._prose(context, seed)
        for token in re.split(r"(\s+)", text):
            if token:
                yield token
                # A visible cadence, so streaming UI can be verified as actually streaming.
                await asyncio.sleep(0.004)
