{#
  Pass 2 of 3 — section expansion (PRD §6.5).

  Runs once per outline section, in parallel with bounded concurrency. Each call sees only its
  own segments plus the global outline for context, which is what keeps output quality high:
  the model writes 400 focused tokens rather than 4000 sprawling ones.

  Every block must carry `segment_ids`. That field is load-bearing — it powers click-to-source
  and is the primary structural defence against hallucination. A block that cannot cite its
  evidence is a block the user cannot trust.
#}
You are writing one section of a set of study notes.

## The section you are writing

**{{ section.title }}** — {{ section.thesis }}

## Where it sits

{% for item in outline %}
{{ loop.index }}. {{ item.title }}{% if item.title == section.title %}  ← you are here{% endif %}
{% endfor %}

## Rules

- Write only this section. Do not repeat other sections or add a conclusion for the whole
  document.
- Open with a `heading` block (level {{ heading_level }}) containing the section title.
- Every block must list the `segment_ids` it came from. Use only ids from the segments below.
  Never invent one, and never leave the list empty.
- Preserve technical content exactly: formulae, numbers, symbols, notation, units, names.
- Use `latex` blocks for any mathematics, with valid KaTeX. Inline maths may appear inside
  `para` text using `$...$`.
- Text fields (`text`, `items`, table cells) are plain text with only this inline markup:
  `**bold**`, `*italic*`, `` `code` `` and `$maths$`. In `para` and `callout` text a blank line
  starts a new paragraph, and lines beginning `- ` or `1. ` form a list. Never write HTML tags
  or Markdown headings inside text.
- Use a `table` block when the material is genuinely tabular; give it `headers` and `rows`.
- Use a `mermaid` block only for a process, hierarchy or relationship the lecturer actually
  described. Valid Mermaid syntax, or omit it.
- Use `code` blocks for code, with the `language` set.
- Use `callout` blocks (`key`, `warning`, `example`, `cue`, `summary`) sparingly, for things
  the source itself emphasised.
- Do not invent content that is not in the segments. If the source is thin here, write less.

{% include "_style.md" %}

## Segments for this section

{% for segment in segments %}
[{{ segment.id }}]{% if segment.timestamp %} ({{ segment.timestamp }}){% endif %} {{ segment.text }}
{% endfor %}
