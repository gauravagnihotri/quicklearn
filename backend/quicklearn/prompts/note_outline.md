{#
  Pass 1 of 3 — outline (PRD §6.5).

  Map-reduce, never single-shot: a 90-minute lecture is 15k+ tokens of transcript, and
  single-pass generation degrades badly past ~3k tokens of *output* regardless of how large the
  context window is. This pass only plans; expansion happens per-section in pass 2.

  Sections must carry the segment id range they cover, because that range is what pass 2 gets
  fed and what every generated block will ultimately anchor to.
#}
You are structuring a set of study notes from a {{ kind }}{% if title %} titled "{{ title }}"{% endif %}.

Produce an outline only. Do not write the notes themselves — a later pass expands each section.

Requirements:

- Between {{ min_sections }} and {{ max_sections }} sections, following the material's own
  progression rather than an imposed template.
- Each section needs a specific `title` (not "Introduction" or "Part 2"), a one-line `thesis`
  stating what the section establishes, and the `segment_ids` it draws on.
- `segment_ids` must be ids that appear below. Sections should tile the material in order and
  should not overlap.
- Group by idea, not by clock time. A single concept explained over ten minutes is one section.
- Ignore administrative content — scheduling, logistics, technical problems.

{% include "_style.md" %}

## Source segments

{% for segment in segments %}
[{{ segment.id }}]{% if segment.timestamp %} ({{ segment.timestamp }}){% endif %} {{ segment.text }}
{% endfor %}
