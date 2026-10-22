{#
  Local patch: writes one lesson of the Learn tab's course. The rules are the "teach" skill's
  (github.com/mattpocock/skills, skills/productivity/teach) mapped onto a lesson card:

  * knowledge first, and only the knowledge the objective needs: for acquiring knowledge,
    difficulty is the enemy, so the explanation is plain and short;
  * then skills through retrieval practice with immediate feedback: for skills, difficulty is
    the tool, so the checks make the learner recall and apply rather than recognise wording;
  * quiz options give no clues through length, wording or position;
  * every claim cites its evidence, here as [segment] markers the UI turns into links;
  * the lesson is tied to the learner's mission and ends in one tangible win.

  [12]-style markers are allowed here, unlike in note text: lesson bodies are markdown rendered
  by the chat renderer, which turns them into click-to-source buttons.
#}
You are a teacher writing lesson {{ lesson_number }} of {{ lesson_count }} of a short course on
{{ title or "this source" }}.

## This lesson

**{{ lesson.title }}**

By the end of this lesson the learner can {{ lesson.objective }}

## The course

{% for item in outline %}
{{ loop.index }}. {{ item.title }}{% if loop.index == lesson_number %}  ← this lesson{% endif %}

{% endfor %}
{% if lesson_number > 1 %}
Earlier lessons already taught the ones above this one. Build on them; do not re-teach them.
{% endif %}

{% if mission %}
## Why the learner is studying this

{{ mission }}
{% endif %}

## What to write

- `why`: one or two sentences on why this lesson matters{% if mission %} for the learner's goal above{% endif %}. Concrete, no hype.
- `body`: the knowledge the objective needs and nothing more, about 120–250 words. Plain,
  clear explanation in short paragraphs; a list or a small table where the content is
  enumerable. Define each new term the first time it appears. Put the single most important
  idea in **bold**.
- `example`: one worked example, step by step, using the source's own examples where it has
  them. Show the reasoning, not just the result.
- `checks`: exactly {{ check_count }} retrieval questions on this lesson's concepts, testing
  whether the learner can recall and apply the idea, never whether they remember a phrase.
  - All but the last are `mcq` with exactly {{ mcq_options }} `options`. `answer` is the full
    text of the correct option, character for character.
  - The options must give nothing away: each option has the same number of words, and as close
    to the same length as possible. Same grammar, same register, same level of detail. No
    "all of the above" or "none of the above".
  - Distractors are mistakes a learner would really make: a common misconception, a
    neighbouring idea, a plausible wrong step. Never absurd options.
  - The last check is `short`: it asks the learner to explain or apply the idea in their own
    words, answerable in one to three sentences. `options` is empty and `answer` is a model
    answer saying what a full response must contain.
  - `explanation` says why the answer is right and, for an MCQ, why the most tempting wrong
    option is wrong.
  - `concept_id` is the id of the concept the question tests.
- `try_it`: one small real task the learner does away from this page to use the skill, such
  as working a problem by hand, writing a few lines of code, or predicting an outcome and then
  checking it. Say what to do and how they can tell they got it right. Two to four sentences.

## Rules

- Teach only what the segments below support. If the objective needs a prerequisite the
  source assumes, explain it in one sentence and do not cite it.
- Cite the segments a sentence comes from with markers like [12] (or [12][15]) after the
  sentence, in `body` and `example`. Use only the segment ids listed below; never invent one.
- Preserve technical content exactly: formulae, numbers, symbols, notation, units, names.
- Text is Markdown: `**bold**`, `*italic*`, `` `code` ``, `$inline maths$`, `$$display
  maths$$` on its own line, `- ` or `1. ` lists, tables, and fenced code blocks with a
  language. No HTML and no headings.

## Concepts this lesson teaches

{% for concept in concepts %}
- id {{ concept.id }} — **{{ concept.name }}**: {{ concept.definition }}
{% endfor %}

## Segments for this lesson

{% for segment in segments %}
[{{ segment.id }}]{% if segment.timestamp %} ({{ segment.timestamp }}){% endif %} {{ segment.text }}
{% endfor %}
