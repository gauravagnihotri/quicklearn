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

import json
import re
from typing import Any

from .schemas import NoteBlockOut

TipTapNode = dict[str, Any]


def _text_nodes(text: str) -> list[TipTapNode]:
    """ProseMirror rejects empty text nodes, so an empty string means no children."""
    return [{"type": "text", "text": text}] if text else []


def _attrs(block: NoteBlockOut, **extra: Any) -> dict[str, Any]:
    return {"segmentIds": list(block.segment_ids), **extra}


def block_to_node(block: NoteBlockOut) -> TipTapNode | None:
    kind = block.type

    if kind == "heading":
        return {
            "type": "heading",
            "attrs": _attrs(block, level=block.level or 2),
            "content": _text_nodes(block.text),
        }

    if kind == "para":
        return {"type": "paragraph", "attrs": _attrs(block), "content": _text_nodes(block.text)}

    if kind == "list":
        items = block.items or [line for line in block.text.split("\n") if line.strip()]
        if not items:
            return None
        return {
            "type": "bulletList",
            "attrs": _attrs(block),
            "content": [
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _text_nodes(item.strip())}],
                }
                for item in items
                if item.strip()
            ],
        }

    if kind == "latex":
        return {"type": "mathBlock", "attrs": _attrs(block, latex=block.text)}

    if kind == "mermaid":
        return {"type": "mermaidBlock", "attrs": _attrs(block, code=block.text)}

    if kind == "code":
        return {
            "type": "codeBlock",
            "attrs": _attrs(block, language=block.language or "text"),
            "content": _text_nodes(block.text),
        }

    if kind == "callout":
        return {
            "type": "callout",
            "attrs": _attrs(block, variant=block.variant or "key"),
            "content": [{"type": "paragraph", "content": _text_nodes(block.text)}],
        }

    if kind == "table":
        if not block.headers and not block.rows:
            return None
        header_row = {
            "type": "tableRow",
            "content": [
                {
                    "type": "tableHeader",
                    "attrs": {"colspan": 1, "rowspan": 1},
                    "content": [{"type": "paragraph", "content": _text_nodes(str(h))}],
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
                        "content": [
                            {
                                "type": "paragraph",
                                # Pad short rows: ProseMirror's table schema requires every
                                # row to have the same cell count or the document is invalid.
                                "content": _text_nodes(str(row[i]) if i < len(row) else ""),
                            }
                        ],
                    }
                    for i in range(width)
                ],
            }
            for row in block.rows
        ]
        content = ([header_row] if block.headers else []) + body
        return {"type": "table", "attrs": _attrs(block), "content": content} if content else None

    return {"type": "paragraph", "attrs": _attrs(block), "content": _text_nodes(block.text)}


def blocks_to_doc(blocks: list[NoteBlockOut]) -> TipTapNode:
    content = [node for node in (block_to_node(b) for b in blocks) if node is not None]
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
