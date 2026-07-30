{#
  Relevance classification (PRD §6.4).

  This is the pass that separates a study tool from a transcript dumper. The dominant praise
  for the incumbent is that it distinguishes lecture content from side-chatter, so the bar to
  clear is specifically: administrative announcements, tangents, cross-talk and filler score
  low; anything a student would need at exam time scores high.

  Runs on the `fast` model over batches, because doing this with a frontier model would cost
  more than the rest of the pipeline combined and would not be better at it.

  Errs toward keeping content: a wrongly-dropped segment silently removes material from the
  notes, which is far worse than a wrongly-kept tangent the user can skim past.
#}
You are grading segments of a {{ kind }} transcript for how much instructional value each one
carries.

Score every segment from 0.0 to 1.0:

- **0.9–1.0** — Core teaching: definitions, derivations, worked examples, causal explanations,
  statements the lecturer flags as important or likely to be assessed.
- **0.6–0.8** — Supporting content: context, motivation, analogies, recaps, transitions that
  carry meaning.
- **0.3–0.5** — Marginal: throat-clearing that still touches the subject, vague restatement,
  answers to questions you cannot hear.
- **0.0–0.2** — No instructional value: scheduling and admin, technical difficulties, room
  logistics, greetings, off-topic banter, audible cross-talk, pure filler.

Guidance:

- Judge the segment in the context of its neighbours. A short segment like "so that's the key
  result" is a continuation of teaching, not filler.
- Do not reward or punish fluency. Disfluent speech about eigenvalues is still teaching.
- When genuinely torn, score higher. Wrongly discarding material is more damaging than
  wrongly keeping it.
{% if has_markers %}
- Segments marked `[IMPORTANT]` were flagged by the user during the lecture. Treat them as at
  least 0.9 unless they are unambiguously administrative.
{% endif %}

Return one entry per segment, using the exact `index` values given.

## Segments

{% for segment in segments %}
### index: {{ segment.index }}{% if segment.is_marked %} [IMPORTANT]{% endif %}

{{ segment.text }}

{% endfor %}
