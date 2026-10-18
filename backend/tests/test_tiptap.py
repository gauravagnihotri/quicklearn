"""Local patch: inline markup in generated note text becomes real TipTap marks and blocks."""

from __future__ import annotations

from typing import Any

from quicklearn.pipeline import tiptap
from quicklearn.pipeline.schemas import NoteBlockOut


def _text(text: str, *marks: str) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = [{"type": mark} for mark in marks]
    return node


def _para(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(content)}


def test_inline_markdown_becomes_marks() -> None:
    assert tiptap.inline_nodes("a **bold** b *it* c `x = 1`") == [
        _text("a "),
        _text("bold", "bold"),
        _text(" b "),
        _text("it", "italic"),
        _text(" c "),
        _text("x = 1", "code"),
    ]


def test_maths_is_left_alone_inside_and_outside_marks() -> None:
    # `*` inside maths is multiplication, not emphasis; maths stays one text node so the
    # editor's KaTeX decoration still sees the whole `$...$`.
    assert tiptap.inline_nodes("$a*b*c$ and **cost $O(n)$**") == [
        _text("$a*b*c$"),
        _text(" and "),
        _text("cost ", "bold"),
        _text("$O(n)$", "bold"),
    ]


def test_html_from_the_model_is_converted_not_shown() -> None:
    assert tiptap.inline_nodes("<b>Big O</b> is the <em>worst</em> case &amp; more") == [
        _text("Big O", "bold"),
        _text(" is the "),
        _text("worst", "italic"),
        _text(" case & more"),
    ]


def test_comparison_in_maths_is_not_mistaken_for_a_tag() -> None:
    assert tiptap.inline_nodes("$x<y$ but z>w") == [_text("$x<y$"), _text(" but z>w")]


def test_single_newline_is_a_hard_break() -> None:
    assert tiptap.inline_nodes("one\ntwo") == [_text("one"), {"type": "hardBreak"}, _text("two")]


def test_callout_with_paragraphs_and_a_numbered_list() -> None:
    block = NoteBlockOut(
        type="callout",
        variant="example",
        text="<b>Example</b>\n\nSteps:\n1. Add $5$\n2. Add *6*",
        segment_ids=[3],
    )
    node = tiptap.block_to_nodes(block)[0]
    assert node["type"] == "callout"
    assert node["attrs"] == {"segmentIds": [3], "variant": "example"}
    assert node["content"] == [
        _para(_text("Example", "bold")),
        _para(_text("Steps:")),
        {
            "type": "orderedList",
            "attrs": {"start": 1},
            "content": [
                {"type": "listItem", "content": [_para(_text("Add "), _text("$5$"))]},
                {"type": "listItem", "content": [_para(_text("Add "), _text("6", "italic"))]},
            ],
        },
    ]


def test_para_with_blank_lines_becomes_several_anchored_paragraphs() -> None:
    nodes = tiptap.block_to_nodes(NoteBlockOut(type="para", text="One.\n\nTwo.", segment_ids=[1]))
    assert nodes == [
        {"type": "paragraph", "attrs": {"segmentIds": [1]}, "content": [_text("One.")]},
        {"type": "paragraph", "attrs": {"segmentIds": [1]}, "content": [_text("Two.")]},
    ]


def test_list_items_and_table_cells_get_marks() -> None:
    items = tiptap.block_to_nodes(NoteBlockOut(type="list", items=["**a** b"], segment_ids=[1]))
    assert items[0]["content"][0]["content"] == [_para(_text("a", "bold"), _text(" b"))]

    table = tiptap.block_to_nodes(
        NoteBlockOut(type="table", headers=["Op"], rows=[["`read`"]], segment_ids=[1])
    )[0]
    cell = table["content"][1]["content"][0]
    assert cell["content"] == [_para(_text("read", "code"))]


def test_code_and_maths_blocks_are_untouched() -> None:
    code = tiptap.block_to_nodes(NoteBlockOut(type="code", text="a = **b**", segment_ids=[1]))
    assert code[0]["content"] == [_text("a = **b**")]
    latex = tiptap.block_to_nodes(NoteBlockOut(type="latex", text="a*b*c", segment_ids=[1]))
    assert latex[0]["attrs"]["latex"] == "a*b*c"


def test_text_reads_back_without_markup() -> None:
    doc = tiptap.blocks_to_doc(
        [NoteBlockOut(type="callout", variant="key", text="**Key:** one\n\n- two", segment_ids=[1])]
    )
    [block] = tiptap.doc_to_blocks(doc)
    assert block.type == "callout"
    assert "**" not in block.text
    assert "Key:" in block.text and "two" in block.text
