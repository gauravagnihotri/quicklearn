{#
  Shared style-preset fragment, included by the outline and section prompts so a single
  definition of "Cornell" governs both passes. Presets come from PRD §7.2.
#}
{% if style == "concise" %}
**Style: Concise.** Bullet-dominant. Short declarative lines. Strip hedging and repetition.
Prefer a list over a paragraph whenever the content is enumerable. Keep prose to at most two
sentences per point.
{% elif style == "cornell" %}
**Style: Cornell.** Structure each section as a cue/notes pair, then a summary. Emit a
`callout` block with variant `cue` holding 2–5 short recall questions or key terms for the
section, followed by the detailed notes, and close the section with a `callout` of variant
`summary` containing a two-sentence synthesis. This layout is the point of the preset — do not
collapse it into ordinary prose.
{% elif style == "exam_prep" %}
**Style: Exam Prep.** Foreground what is likely to be tested. Lead with definitions, formal
statements, conditions and edge cases. Use `callout` blocks with variant `key` for anything the
lecturer signalled as assessable. Include worked steps for any procedure. Omit anecdote and
motivation unless it is a memory aid.
{% else %}
**Style: Detailed.** Explanatory prose with worked examples. Preserve the reasoning, not just
the conclusion — a reader who missed the lecture should be able to follow the argument. Use
lists for genuine enumerations and prose for everything else.
{% endif %}
