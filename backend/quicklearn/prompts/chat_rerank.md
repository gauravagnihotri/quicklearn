{#
  Reranking step of hybrid retrieval (PRD §7.5): top-k 12 from fusion, reranked to 6 here.

  Runs on the `fast` model. Vector similarity and BM25 both retrieve things that merely look
  related; a cheap relevance judgement removes the near-misses so the answer prompt spends its
  context on excerpts that actually bear on the question.
#}
Rate how well each excerpt helps answer the question. This is a filtering step — be strict.

## Question

{{ query }}

## Excerpts

{% for excerpt in excerpts %}
### id: {{ excerpt.id }}

{{ excerpt.text }}

{% endfor %}

Score each excerpt 0.0–1.0:

- **1.0** — directly answers the question, or contains the specific fact asked for.
- **0.5** — relevant background; useful supporting context but not the answer.
- **0.0** — same general topic but does not bear on this question. Topical overlap is not
  relevance.

Return a score for every id listed.
