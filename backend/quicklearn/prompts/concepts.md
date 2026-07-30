{#
  Concept extraction (PRD §6.6).

  Deliberately a separate pass from note generation, and the spine of the study engine: cards
  and quiz items are generated from concepts, not from raw transcript. That is what makes the
  mastery model coherent — a concept is the unit that can be known or not known, so both a card
  and a quiz item can point at the same thing and update the same score.
#}
Extract the distinct knowledge units from this {{ kind }}{% if title %} ("{{ title }}"){% endif %}.

A concept is something a student could be asked about and could either know or not know: a
definition, a theorem, a mechanism, a procedure, a causal relationship, a named phenomenon.

Requirements:

- Extract between {{ min_concepts }} and {{ max_concepts }} concepts, ordered as they appear.
- `name` — the term itself, as the source uses it. Two to five words. Not a sentence.
- `definition` — one or two sentences, self-contained. A student reading only this should
  understand it without the surrounding notes.
- `importance` — 1 to 5. Reserve 5 for things the lecturer explicitly flagged as central or
  assessable; use 1 for passing mentions.
- `segment_ids` — where the concept is taught. Only ids listed below.

Do not extract:

- Administrative details, dates, logistics.
- Restatements of the same idea — merge them into one concept.
- Things mentioned but not explained. A name-drop is not a concept.

## Source segments

{% for segment in segments %}
[{{ segment.id }}] {{ segment.text }}
{% endfor %}
