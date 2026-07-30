"""Prompt loading.

PRD §11: "Prompts live in versioned ``.md`` files under ``prompts/``, never inline in Python.
They will be iterated far more than the code around them."

Templates are Jinja2. Each file starts with a short comment block recording what it is for and
why it is shaped the way it is, so that whoever tunes it next knows which constraints are
load-bearing and which are taste.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

PROMPT_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(PROMPT_DIR),
        # StrictUndefined turns a renamed variable into a loud error at render time rather
        # than a silently empty section of the prompt — which would degrade output quality in
        # a way that is very hard to notice.
        undefined=StrictUndefined,
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def render(name: str, **context: Any) -> str:
    """Render ``prompts/<name>.md``."""
    template = _environment().get_template(f"{name}.md")
    return template.render(**context).strip()


def available() -> list[str]:
    return sorted(p.stem for p in PROMPT_DIR.glob("*.md"))
