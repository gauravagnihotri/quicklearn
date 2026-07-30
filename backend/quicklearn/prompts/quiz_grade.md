{#
  Grading for short-answer and free-response items (PRD §7.4).

  The rubric is deliberately asymmetric: lenient on phrasing, strict on substance. A student who
  writes the right idea in their own words has demonstrated understanding; one who parrots the
  lecturer's exact sentence with the key term wrong has not. Grading the reverse way — which is
  what naive string comparison does — teaches students to memorise wording.
#}
Grade this {{ item_type }} answer.

## Question

{{ question }}

## Reference answer

{{ reference }}

## The student wrote

{{ response }}

## Rubric

- Judge substance, not wording. Different phrasing, synonyms, a different but valid order, or a
  correct answer reached another way all earn full marks.
- Do not require the reference answer's exact terminology unless the term itself is what is
  being tested.
- Spelling, grammar and notation style are irrelevant unless they change the meaning.
- A partially correct answer earns partial credit. Say specifically which part is missing.
- A confidently stated wrong answer is wrong. Do not award marks for fluency.
- An empty, evasive, or off-topic answer scores 0.0.

{% if item_type == "free" %}
Score 0.0–1.0 against how much of the reference answer's required content is present and correct.
Treat 0.7 as the threshold for "understands this".
{% else %}
Score 1.0 if substantively correct, 0.5 if the core idea is right but incomplete or imprecise,
0.0 otherwise.
{% endif %}

`is_correct` is true when the score is at least {{ pass_threshold }}.

Write `feedback` directly to the student in one or two sentences. If they were wrong, say what
the right answer is and where they went astray. Be plain, not encouraging-for-its-own-sake.
