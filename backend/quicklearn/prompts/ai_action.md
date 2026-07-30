{#
  Inline AI actions on an editor text selection (PRD §7.2).

  Output replaces or augments text inside a TipTap document, so it must be plain prose the
  editor can insert — no markdown headings, no code fences, no preamble.
#}
{% if action == "explain" %}
Explain the selected passage from the user's notes. Assume they understood the surrounding
material but got lost here. Be concrete, use an example if it helps, and keep it to a short
paragraph.
{% elif action == "expand" %}
Expand the selected passage with more detail, reasoning and an example. Preserve the original
claims exactly — you are elaborating, not revising. Two to three paragraphs at most.
{% elif action == "simplify" %}
Rewrite the selected passage in plainer language. Keep every technical term that carries
meaning, but remove jargon that does not. Preserve all factual content and any mathematics
exactly. Shorter than the original.
{% elif action == "key_points" %}
Reduce the selected passage to its key points as a short bullet list. Each bullet one line.
No preamble.
{% else %}
Rewrite the selected passage more clearly, preserving all factual content.
{% endif %}

Rules:

- Return only the replacement text. No preamble, no "Here's the...", no markdown headings, no
  code fences.
- Preserve mathematics in `$...$` form exactly as written.
- Match the surrounding register.
- Do not introduce facts absent from the selection and its context.

{% if context %}
## Surrounding context (for reference — do not rewrite this)

{{ context }}
{% endif %}

## Selected passage

{{ selection }}
