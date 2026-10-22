{#
  Local patch: plans the Learn tab's course. Shaped after the "teach" skill
  (github.com/mattpocock/skills, skills/productivity/teach): lessons are short, each teaches one
  tightly scoped thing, ends in one tangible win, and is ordered so each builds on the last.

  The plan only picks titles, objectives and which concepts and segments each lesson owns. The
  lesson text is written per lesson by lesson.md, which sees only that lesson's segments.
#}
You are planning a short guided course that teaches the material of {{ title or "this source" }}.

{% if mission %}
## Why the learner is studying this

{{ mission }}

Pick and order lessons so they serve this goal first.
{% else %}
The learner has not said why they are studying this. Plan for someone who wants to understand
the material well enough to use it.
{% endif %}

## How to plan

- Plan {{ min_lessons }} to {{ max_lessons }} lessons. A lesson is about five minutes of work: one
  idea or skill, small enough to hold in working memory. Usually one or two concepts per lesson.
- Order them so every lesson only needs what earlier lessons taught. Foundations first.
- `title`: short and concrete, the thing being learned, not a chapter label ("Why appending is
  O(1) on average", not "Part 2").
- `objective`: finishes the sentence "By the end of this lesson you can …". It names one thing
  the learner will be able to do (explain, calculate, choose, write, trace), not a topic.
- `concept_ids`: the ids of the concepts below that this lesson teaches. Every concept with
  importance 4 or 5 must belong to some lesson. Use only ids listed below.
- `segment_ids`: the ids of the source segments the lesson draws on, taken from its concepts
  and the segments below. Use only ids listed below; never leave it empty.
- Teach only what the source covers. Do not plan lessons on topics the source does not teach.

## Concepts

{% for concept in concepts %}
- id {{ concept.id }} — **{{ concept.name }}** (importance {{ concept.importance }}){% if concept.segment_ids %} — segments {{ concept.segment_ids | join(", ") }}{% endif %}

  {{ concept.definition }}
{% endfor %}

## Source segments (opening of each)

{% for segment in segments %}
[{{ segment.id }}]{% if segment.timestamp %} ({{ segment.timestamp }}){% endif %} {{ segment.text }}…
{% endfor %}
