{#
  Flashcard generation (PRD §7.3).

  Generated from concepts rather than transcript. Cards are scheduled with FSRS, which assumes
  each card tests one recallable fact — a card bundling three facts produces a rating that means
  nothing, because "Hard" cannot say which third the user missed.
#}
Write flashcards for the following concepts from {{ title or "this material" }}.

Card types:

- `basic` — a direct question on the front, the answer on the back.
- `cloze` — a sentence with the key term replaced by `{% raw %}{{c1::hidden text}}{% endraw %}`.
  Use for definitions and formulae where the surrounding sentence is the useful cue.
- `reverse` — front gives the definition, back gives the term. Use when recognising the term
  from its description is genuinely worth practising.

Rules:

- At most {{ max_per_concept }} card(s) per concept. Prefer one excellent card to three weak ones.
- One fact per card. If a concept has three parts worth testing, write three cards.
- The front must be answerable without seeing the back, and without the phrase "according to
  the lecture". Questions must stand alone.
- No yes/no questions, and no questions whose answer is guessable from their phrasing.
- Keep the back tight — a sentence or two, or a formula. Not a paragraph.
- Preserve notation exactly. Use `$...$` for inline mathematics.
- Set `concept_id` to the id of the concept the card tests, and carry over its `segment_ids`.

## Concepts

{% for concept in concepts %}
### id: {{ concept.id }} — {{ concept.name }} (importance {{ concept.importance }})

{{ concept.definition }}
{% if concept.segment_ids %}
segment_ids: {{ concept.segment_ids | join(", ") }}
{% endif %}

{% endfor %}
