{#
  Quiz item generation (PRD §7.4).

  The distractor instruction is the important part. Random distractors make MCQs trivially
  guessable by elimination and teach nothing; distractors drawn from *adjacent* concepts test
  whether the student can actually tell two neighbouring ideas apart, which is where real
  understanding lives.
#}
Write quiz items testing the concepts below, drawn from {{ title or "this material" }}.

{% if requested_types %}
Item types to produce: {{ requested_types | join(", ") }}.
{% endif %}

- `mcq` — exactly {{ mcq_options }} options. `answer` must be the full text of the correct
  option, matching one entry in `options` character for character.
- `cloze` — a sentence with the tested term removed; `answer` is the removed text alone.
- `short` — answerable in a sentence or a formula. `answer` is a reference answer, graded
  leniently on phrasing.
- `free` — requires explanation or derivation. `answer` is a model answer describing what a
  full response must contain.

Rules:

- Produce {{ count }} item(s), spread across the concepts rather than clustered on one.
- **MCQ distractors must come from adjacent concepts in this same material** — a neighbouring
  definition, a commonly confused term, a plausible-but-wrong step. Never nonsense options,
  never obviously-wrong throwaways, never "all of the above".
- All options must be the same rough length and register. Length is a tell, and students learn
  to exploit it rather than learn the material.
- `explanation` must say *why* the answer is right, and for MCQs why the tempting distractor
  is wrong.
- `difficulty` 1–5: 1 is recall, 3 is application, 5 is synthesis or edge cases.
- Set `concept_id` to the concept tested and carry its `segment_ids`.
- Test understanding, not recall of the lecturer's exact wording.

## Concepts

{% for concept in concepts %}
### id: {{ concept.id }} — {{ concept.name }}{% if concept.mastery is not none %} (current mastery {{ "%.2f"|format(concept.mastery) }}){% endif %}

{{ concept.definition }}
{% if concept.segment_ids %}
segment_ids: {{ concept.segment_ids | join(", ") }}
{% endif %}

{% endfor %}
{% if neighbours %}

## Adjacent concepts — mine these for distractors

{% for concept in neighbours %}
- **{{ concept.name }}** — {{ concept.definition }}
{% endfor %}
{% endif %}
