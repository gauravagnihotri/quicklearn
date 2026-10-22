{#
  Local patch: the Learn tab's reference sheet. The "teach" skill's reference documents are the
  compressed essence of the lessons, built for quick lookup and printing, and revisited far more
  often than the lessons themselves. The glossary is added separately from the concepts, so this
  covers everything else.
#}
Write a one-page reference sheet for a course on {{ title or "this source" }}, compressed from
the lessons below. The learner will come back to it for quick lookup and may print it.

## What goes on it

Write it as `groups`, each a short `label` (2–4 words, such as "Operations and costs" or
"Resizing, step by step") and a `body` holding that group's content. Aim for 3–6 groups.

- The rules, formulae, procedures, complexities, syntax or numbers worth looking up, whichever
  this material actually has. Procedures as numbered steps; comparisons as a table; code as
  fenced code blocks with a language.
- A last group labelled "Common mistakes" if the lessons warned about any.

## Rules

- Dense and scannable: fragments and lists, no paragraphs of prose, no introduction or
  conclusion. Every `body` holds real content, never just a title.
- No glossary of terms; that is added separately.
- Only what the lessons say. Preserve formulae, numbers and notation exactly.
- `body` is Markdown: `**bold**`, `*italic*`, `` `code` ``, `$inline maths$`, `$$display
  maths$$` on its own line, lists, tables and fenced code. No HTML, no headings, no [n]
  citation markers.

## The lessons

{% for lesson in lessons %}
### {{ loop.index }}. {{ lesson.title }}

{{ lesson.body }}

{{ lesson.example }}

{% endfor %}
