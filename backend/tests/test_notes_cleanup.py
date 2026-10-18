"""Local patch: segment-id markers the model copies into note text are removed."""

from __future__ import annotations

from quicklearn.pipeline.generate.notes import strip_segment_markers


def test_markers_naming_shown_segments_are_removed() -> None:
    allowed = {10, 11, 12}
    assert strip_segment_markers("[11] Use an index. [10, 12]", allowed) == "Use an index."
    assert strip_segment_markers("Reading is O(1) [11].", allowed) == "Reading is O(1)."


def test_brackets_that_are_content_stay() -> None:
    allowed = {10, 11, 12}
    # Array literals from the lecture are not segment ids.
    assert strip_segment_markers("Shift [5, 6, 7] right", allowed) == "Shift [5, 6, 7] right"
    # A bracket mixing an id with other numbers is content too.
    assert strip_segment_markers("values [10, 3]", allowed) == "values [10, 3]"
    # Maths is never touched, even when its numbers match.
    assert strip_segment_markers("index $a[11]$ here", allowed) == "index $a[11]$ here"
    assert strip_segment_markers("call `arr[10]`", allowed) == "call `arr[10]`"
