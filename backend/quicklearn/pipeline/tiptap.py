"""Conversion between generated blocks, TipTap documents, and export formats.

TipTap is ProseMirror underneath, so a document is a nested JSON tree of typed nodes. Three
custom node types are used beyond the defaults — ``mathBlock``, ``mermaidBlock`` and
``callout`` — matched by extensions on the frontend.

Every top-level node carries ``attrs.segmentIds``. That is what draws the gutter anchor next to
a block and lets clicking it seek the audio player to the moment that produced it. Losing those
attributes during an edit round-trip would quietly break the product's core trust mechanism, so
:func:`doc_to_blocks` reads them back out rather than recomputing.
"""

from __future__ import annotations

import html
import itertools
import json
import re
from typing import Any

from .schemas import NoteBlockOut

TipTapNode = dict[str, Any]


def _text_nodes(text: str) -> list[TipTapNode]:
    """ProseMirror rejects empty text nodes, so an empty string means no children."""
    return [{"type": "text", "text": text}] if text else []


# --------------------------------------------------------------------------- inline markup
#
# Local patch: the note prompt allows a small inline markup in block text — **bold**, *italic*,
# `code`, $maths$ and blank-line paragraphs — and these helpers turn it into real marks and
# blocks. Upstream put every string in verbatim, so whatever the model improvised (`<b>`,
# `**`, numbered steps inside a callout) showed as raw characters. Stray HTML is converted the
# same way, so older outputs and a model that ignores the rule still render.

_MATH = re.compile(r"\$[^$\n]+\$")
_INLINE = re.compile(
    r"(?P<math>\$[^$\n]+\$)"
    r"|`(?P<code>[^`\n]+)`"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|(?<![\w*])\*(?P<italic>[^*\s](?:[^*\n]*[^*\s])?)\*(?![\w*])"
)
_HTML_MARKS = [
    (re.compile(r"<(b|strong)>(.*?)</\1>", re.I | re.S), r"**\2**"),
    (re.compile(r"<(i|em)>(.*?)</\1>", re.I | re.S), r"*\2*"),
    (re.compile(r"<code>(.*?)</code>", re.I | re.S), r"`\1`"),
    (re.compile(r"<br\s*/?>", re.I), "\n"),
    (re.compile(r"</p>\s*<p[^>]*>", re.I), "\n\n"),
    (re.compile(r"<li[^>]*>", re.I), "\n- "),
]
# Only known tag names are stripped: a bare `<` pattern would also eat text like "x<y and z>w".
_HTML_LEFTOVER = re.compile(
    r"</?(?:p|div|span|ul|ol|li|u|mark|sup|sub|small|strong|b|i|em|code|h[1-6])\b[^>]*>", re.I
)
_BULLET_LINE = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED_LINE = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def _line_kind(line: str) -> str:
    if _NUMBERED_LINE.match(line):
        return "numbered"
    return "bullet" if _BULLET_LINE.match(line) else "prose"


def unhtml(text: str) -> str:
    """HTML from the model to the inline markup, leaving maths untouched."""
    if "<" not in text and "&" not in text:
        return text
    parts = _MATH.split(text)
    maths = _MATH.findall(text)
    out: list[str] = []
    for index, part in enumerate(parts):
        for pattern, replacement in _HTML_MARKS:
            part = pattern.sub(replacement, part)
        out.append(html.unescape(_HTML_LEFTOVER.sub("", part)))
        if index < len(maths):
            out.append(maths[index])
    return "".join(out)


def _plain(text: str, marks: tuple[str, ...]) -> list[TipTapNode]:
    nodes: list[TipTapNode] = []
    for index, line in enumerate(text.split("\n")):
        if index:
            nodes.append({"type": "hardBreak"})
        if line:
            node: TipTapNode = {"type": "text", "text": line}
            if marks:
                node["marks"] = [{"type": mark} for mark in marks]
            nodes.append(node)
    return nodes


def _inline(text: str, marks: tuple[str, ...] = ()) -> list[TipTapNode]:
    nodes: list[TipTapNode] = []
    position = 0
    for match in _INLINE.finditer(text):
        nodes += _plain(text[position : match.start()], marks)
        if match["math"]:
            # One unmarked-by-us text node, so the editor's KaTeX decoration sees all of it.
            nodes += _plain(match["math"], marks)
        elif match["code"] is not None:
            nodes.append({"type": "text", "text": match["code"], "marks": [{"type": "code"}]})
        elif match["bold"] is not None:
            nodes += _inline(match["bold"], (*marks, "bold"))
        else:
            nodes += _inline(match["italic"], (*marks, "italic"))
        position = match.end()
    return nodes + _plain(text[position:], marks)


def inline_nodes(text: str) -> list[TipTapNode]:
    """Text with inline markup as TipTap text nodes; no trailing or leading line breaks."""
    nodes = _inline(unhtml(text).strip())
    while nodes and nodes[0]["type"] == "hardBreak":
        nodes.pop(0)
    while nodes and nodes[-1]["type"] == "hardBreak":
        nodes.pop()
    return nodes


def _paragraph(text: str, attrs: dict[str, Any] | None = None) -> TipTapNode:
    node: TipTapNode = {"type": "paragraph", "content": inline_nodes(text)}
    if attrs is not None:
        node = {"type": "paragraph", "attrs": attrs, "content": node["content"]}
    return node


def _list_node(items: list[str], ordered: bool, attrs: dict[str, Any] | None) -> TipTapNode:
    node: TipTapNode = {"type": "orderedList" if ordered else "bulletList"}
    if ordered or attrs is not None:
        node["attrs"] = {**(attrs or {}), **({"start": 1} if ordered else {})}
    node["content"] = [{"type": "listItem", "content": [_paragraph(item)]} for item in items]
    return node


def rich_blocks(text: str, attrs: dict[str, Any] | None = None) -> list[TipTapNode]:
    """Text to paragraphs and lists: blank lines split paragraphs, `- ` / `1. ` lines are lists.

    ``attrs`` goes on every node produced, so each keeps its gutter anchor.
    """
    nodes: list[TipTapNode] = []
    for chunk in re.split(r"\n\s*\n", unhtml(text).strip()):
        lines = [line for line in chunk.split("\n") if line.strip()]
        # Consecutive lines of one kind — prose, bullet or numbered — become one node.
        for kind, run in itertools.groupby(lines, key=_line_kind):
            group = list(run)
            if kind == "prose":
                nodes.append(_paragraph("\n".join(group), attrs))
            else:
                pattern = _NUMBERED_LINE if kind == "numbered" else _BULLET_LINE
                items = [pattern.match(line).group(1) for line in group]  # type: ignore[union-attr]
                nodes.append(_list_node(items, kind == "numbered", attrs))
    return [node for node in nodes if node.get("content")]


def _attrs(block: NoteBlockOut, **extra: Any) -> dict[str, Any]:
    return {"segmentIds": list(block.segment_ids), **extra}


def block_to_nodes(block: NoteBlockOut) -> list[TipTapNode]:
    kind = block.type

    if kind == "heading":
        return [
            {
                "type": "heading",
                "attrs": _attrs(block, level=block.level or 2),
                "content": inline_nodes(block.text),
            }
        ]

    if kind == "para":
        return rich_blocks(block.text, _attrs(block))

    if kind == "list":
        items = [item.strip() for item in block.items if item.strip()] or [
            line for line in block.text.split("\n") if line.strip()
        ]
        return [_list_node(items, False, _attrs(block))] if items else []

    if kind == "latex":
        return [{"type": "mathBlock", "attrs": _attrs(block, latex=block.text)}]

    if kind == "mermaid":
        return [{"type": "mermaidBlock", "attrs": _attrs(block, code=block.text)}]

    if kind == "code":
        return [
            {
                "type": "codeBlock",
                "attrs": _attrs(block, language=block.language or "text"),
                "content": _text_nodes(block.text),
            }
        ]

    if kind == "callout":
        return [
            {
                "type": "callout",
                "attrs": _attrs(block, variant=block.variant or "key"),
                "content": rich_blocks(block.text) or [{"type": "paragraph", "content": []}],
            }
        ]

    if kind == "table":
        if not block.headers and not block.rows:
            return []
        header_row = {
            "type": "tableRow",
            "content": [
                {
                    "type": "tableHeader",
                    "attrs": {"colspan": 1, "rowspan": 1},
                    "content": [_paragraph(str(h))],
                }
                for h in block.headers
            ],
        }
        width = len(block.headers) or max((len(r) for r in block.rows), default=0)
        body = [
            {
                "type": "tableRow",
                "content": [
                    {
                        "type": "tableCell",
                        "attrs": {"colspan": 1, "rowspan": 1},
                        # Pad short rows: ProseMirror's table schema requires every row to
                        # have the same cell count or the document is invalid.
                        "content": [_paragraph(str(row[i]) if i < len(row) else "")],
                    }
                    for i in range(width)
                ],
            }
            for row in block.rows
        ]
        content = ([header_row] if block.headers else []) + body
        return [{"type": "table", "attrs": _attrs(block), "content": content}] if content else []

    return rich_blocks(block.text, _attrs(block))


def block_to_node(block: NoteBlockOut) -> TipTapNode | None:
    nodes = block_to_nodes(block)
    return nodes[0] if nodes else None


def blocks_to_doc(blocks: list[NoteBlockOut]) -> TipTapNode:
    content = [node for block in blocks for node in block_to_nodes(block)]
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "content": content}


def empty_doc() -> TipTapNode:
    return {"type": "doc", "content": [{"type": "paragraph", "content": []}]}


# --------------------------------------------------------------------------- reading back


def _collect_text(node: TipTapNode) -> str:
    if node.get("type") == "text":
        return str(node.get("text") or "")
    return "".join(_collect_text(child) for child in node.get("content") or [])


_NODE_TO_BLOCK = {
    "heading": "heading",
    "paragraph": "para",
    "bulletList": "list",
    "orderedList": "list",
    "mathBlock": "latex",
    "mermaidBlock": "mermaid",
    "codeBlock": "code",
    "callout": "callout",
    "table": "table",
}


def doc_to_blocks(doc: TipTapNode) -> list[NoteBlockOut]:
    """Flatten a document back into blocks, for search indexing and citation lookup.

    Called after every autosave, so a user's edits stay searchable and their anchors stay
    resolvable.
    """
    blocks: list[NoteBlockOut] = []
    for node in doc.get("content") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "")
        attrs = node.get("attrs") or {}
        segment_ids = [int(i) for i in (attrs.get("segmentIds") or []) if isinstance(i, int | str)]
        block_type = _NODE_TO_BLOCK.get(node_type, "para")

        if node_type == "mathBlock":
            text = str(attrs.get("latex") or "")
        elif node_type == "mermaidBlock":
            text = str(attrs.get("code") or "")
        else:
            text = _collect_text(node)

        if node_type in {"bulletList", "orderedList"}:
            items = [_collect_text(item) for item in node.get("content") or []]
            blocks.append(
                NoteBlockOut(
                    type="list",
                    text="\n".join(i for i in items if i),
                    items=[i for i in items if i],
                    segment_ids=segment_ids,
                )
            )
            continue

        if node_type == "table":
            headers: list[str] = []
            rows: list[list[str]] = []
            for row in node.get("content") or []:
                cells = [_collect_text(c) for c in row.get("content") or []]
                is_header = any((c.get("type") == "tableHeader") for c in row.get("content") or [])
                if is_header and not headers:
                    headers = cells
                else:
                    rows.append(cells)
            blocks.append(
                NoteBlockOut(
                    type="table",
                    text=" ".join(headers + [c for r in rows for c in r]),
                    headers=headers,
                    rows=rows,
                    segment_ids=segment_ids,
                )
            )
            continue

        if not text.strip() and block_type not in {"latex", "mermaid"}:
            continue

        blocks.append(
            NoteBlockOut(
                type=block_type,  # type: ignore[arg-type]
                text=text,
                level=int(attrs["level"]) if "level" in attrs else None,
                language=str(attrs["language"]) if "language" in attrs else None,
                variant=str(attrs["variant"]) if "variant" in attrs else None,
                segment_ids=segment_ids,
            )
        )
    return blocks


# --------------------------------------------------------------------------- export


def doc_to_markdown(doc: TipTapNode, *, title: str = "") -> str:
    lines: list[str] = []
    if title:
        lines += [f"# {title}", ""]

    for block in doc_to_blocks(doc):
        if block.type == "heading":
            lines += [f"{'#' * min(6, block.level or 2)} {block.text}", ""]
        elif block.type == "list":
            lines += [f"- {item}" for item in (block.items or block.text.split("\n")) if item]
            lines.append("")
        elif block.type == "latex":
            lines += ["$$", block.text, "$$", ""]
        elif block.type == "mermaid":
            lines += ["```mermaid", block.text, "```", ""]
        elif block.type == "code":
            lines += [f"```{block.language or ''}", block.text, "```", ""]
        elif block.type == "callout":
            label = (block.variant or "note").replace("_", " ").title()
            body = "\n".join(f"> {line}" for line in block.text.split("\n"))
            lines += [f"> **{label}**", ">", body, ""]
        elif block.type == "table":
            if block.headers:
                lines.append("| " + " | ".join(block.headers) + " |")
                lines.append("|" + "|".join([" --- "] * len(block.headers)) + "|")
            for row in block.rows:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
        else:
            lines += [block.text, ""]

    return "\n".join(lines).strip() + "\n"


def doc_to_plaintext(doc: TipTapNode, *, title: str = "") -> str:
    parts = [title, ""] if title else []
    for block in doc_to_blocks(doc):
        if block.type == "list":
            parts += [f"- {item}" for item in (block.items or []) if item]
        elif block.type == "table":
            if block.headers:
                parts.append("\t".join(block.headers))
            parts += ["\t".join(row) for row in block.rows]
        else:
            parts.append(block.text)
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def outline_from_blocks(blocks: list[NoteBlockOut]) -> list[dict[str, Any]]:
    """Heading tree for the note sidebar."""
    return [
        {"level": block.level or 2, "text": block.text, "index": index}
        for index, block in enumerate(blocks)
        if block.type == "heading" and block.text.strip()
    ]


_MERMAID_OK = re.compile(
    r"^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram|"
    r"journey|gantt|pie|mindmap|timeline|gitGraph|quadrantChart|C4Context)\b",
    re.IGNORECASE,
)


def looks_like_mermaid(text: str) -> bool:
    """Cheap validity check.

    A malformed Mermaid block renders as a red error box in the middle of otherwise good notes,
    which looks far more broken than the diagram simply being absent. Blocks failing this are
    demoted to code blocks at assembly time.
    """
    return bool(_MERMAID_OK.match(text.strip()))


def dumps(doc: TipTapNode) -> str:
    return json.dumps(doc, ensure_ascii=False)
