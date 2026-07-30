"""Common shape returned by every extractor.

Extraction produces *raw units* — the finest-grained pieces the source naturally yields, with
whatever positional metadata that source type supports. Merging them into the ~30-second
semantic chunks that become ``Segment`` rows is :mod:`quicklearn.pipeline.segment`'s job, so
each extractor only has to know its own format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RawUnit:
    text: str
    # Time-based sources
    start_ms: int | None = None
    end_ms: int | None = None
    # Document sources
    page_no: int | None = None
    bbox: list[float] | None = None
    speaker: str | None = None
    #: Set for units the user flagged live with the recording marker button.
    is_marked: bool = False


@dataclass(slots=True)
class ExtractResult:
    units: list[RawUnit] = field(default_factory=list)
    language: str | None = None
    duration_sec: float | None = None
    page_count: int | None = None
    provider: str = ""
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(unit.text for unit in self.units if unit.text.strip())
