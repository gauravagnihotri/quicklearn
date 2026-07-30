{#
  Image and scanned-page extraction (PRD §6.3).

  Used for image sources and as the OCR fallback when a PDF's text layer is too sparse to be
  real — i.e. a scan. The output becomes segment text, so it must be content, not commentary
  about the image.
#}
Transcribe and describe everything in this image.

- Transcribe all visible text verbatim, preserving its reading order and structure.
- Reproduce mathematics in LaTeX using `$...$` for inline and `$$...$$` for display.
- Render tables as markdown tables.
- For diagrams, charts and figures: describe what is shown, label the axes and series, and
  state the relationship or trend being conveyed. Someone who cannot see the image should be
  able to use your description in its place.
- If handwriting is illegible, write `[illegible]` rather than guessing.
- Output the content itself. Do not write "This image shows..." or comment on image quality.

{% if hint %}
Context: {{ hint }}
{% endif %}
