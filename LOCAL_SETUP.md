# Local setup

This fork runs quicklearn on Proxmox LXC 134 (`/opt/quicklearn`, http://192.168.86.81:8080) with all
inference on a workstation GPU (RTX 4500 Ada, 24 GB) at 192.168.86.20:

| Port | Service | Serve script (copies in [`local/`](local/); run from `~/Documents/fix_gpu_drivers`) |
|---|---|---|
| 8000 | vLLM v0.29.0, Gemma 4 26B-A4B QAT AWQ INT4, thinking on. `serve_lfm25.sh` (Liquid LFM2.5 24B-A2B, served as `lfm25`) was tried on 2026-10-18 and wrote weaker notes: paragraph walls, leaked `segment_ids:` lines, factual slips | `serve_gemma4.sh` |
| 8001 | speaches: distil-whisper large-v3 (STT, English) + Kokoro 82M (TTS) | `serve_speaches.sh` |

Embeddings stay on the default local fastembed model inside the container.
`docker-compose.override.yml` wires the providers up through environment variables.

## Patches

Each is marked `Local patch` in the source and, where it changes behaviour, gated by an env var
so upstream defaults are unchanged.

| Change | File | Why |
|---|---|---|
| `QUICKLEARN_REASONING_HEADROOM` adds output tokens to every call | `providers/llm/openai_compat.py` | Thinking spends the caller's `max_tokens` before the JSON answer |
| `QUICKLEARN_LLM_LONG_TIMEOUT` uses the 900 s timeout | `providers/llm/openai_compat.py` | Thinking calls on a shared local GPU exceed 300 s |
| `QUICKLEARN_USE_MODEL_SAMPLING` stops sending `temperature` | `providers/llm/openai_compat.py` | Low temperatures made the model think ~50% longer |
| Note blocks require `type` and `text`; descriptive fields (`level`, `variant`, `language`) come before `text` | `pipeline/schemas.py` | vLLM emits properties in schema order: optional `text` got skipped, and with `text` second callouts got their variant name as content |
| Empty headings fall back to the section title; leading `#`s stripped | `pipeline/generate/notes.py` | Models omit heading text (headings were dropped) or keep Markdown `##` |
| Lists put in `text` as HTML `<li>` or `- ` bullets are split into `items` | `pipeline/generate/notes.py` | One bullet showed the raw `<ul><li>` markup |
| Inline `$maths$` in notes renders with KaTeX (source shows while the cursor is in it) | `frontend/src/components/editor/extensions.tsx`, `index.css` | The note prompt asks for inline maths but upstream only renders maths blocks |
| Callouts use a plain DOM node view instead of React | `frontend/src/components/editor/extensions.tsx` | On some machines the React content hole re-rendered after load; the editor saw that as deleting the text and autosaved empty callouts |
| Note saves are guarded by the server: refused (409) from a tab running an older UI build (each build is stamped with an ID, sent as `X-Quicklearn-Build`), while a job for the source is queued or running, when based on an older note version, or when two or more blocks lose all their text at once. The editor is also read-only while the source processes | `api/notes.py`, `errors.py`, `frontend/vite.config.ts`, `lib/api.ts`, `components/editor/NoteEditor.tsx`, `routes/Source.tsx`, `tests/test_api.py` | A tab opened before the callout fix was deployed kept running the old code, reconnected to the new server, and autosaved empty callouts 2s after "2. Static Arrays" was generated |
| Note text has a defined inline markup: the section prompt allows only `**bold**`, `*italic*`, `` `code` ``, `$maths$`, blank-line paragraphs and `- `/`1. ` lists; the converter turns it into real TipTap marks, paragraphs and lists (callouts and paras included), and converts stray HTML (`<b>`, `<em>`, `<br>`, `<li>`) the same way | `prompts/note_section.md`, `pipeline/tiptap.py`, `pipeline/generate/notes.py`, `tests/test_tiptap.py` | The schema fixed block structure but not the text inside it, so the model improvised `<b>` tags and numbered steps that showed as raw characters |
| "Simplified" notes: every source also gets a second, ADHD-friendly note (style `adhd`), written by its own job (`generate_simplified_notes`) queued after ingest finishes. The prompt fragment is the i-have-adhd skill's facts and rules (MIT, github.com/ayghri/i-have-adhd) mapped onto notes, with no instruction to simplify. A **Simplified** switch on the Notes tab flips between the two; regenerate has a "Simplified notes" target; each note is locked only while its own job runs | `prompts/_adhd.md`, `prompts/_style.md`, `pipeline/generate/notes.py` (`note_for`), `handlers/ingest.py`, `api/sources.py`, `api/notes.py`, `pipeline/generate/podcast.py`, `db/enums.py`, `routes/Source.tsx`, `lib/*.ts` | Requested feature; upstream has one note per source |
| `[12]`-style segment markers the model copies into note text are removed, but only when every number is a segment shown for that section (array literals like `[5, 6, 7]`, maths and code stay), as is an echoed `segment_ids: [3]` label even in backticks; the prompt also forbids them | `pipeline/generate/notes.py`, `prompts/note_section.md`, `tests/test_notes_cleanup.py` | The segment list is shown as `[id] text`, and the model mimicked it in up to 28 places per note |
| Video sources play the original video (falls back to audio if the browser can't decode it) | `frontend/src/components/player/VideoPlayer.tsx`, `routes/Source.tsx` | Upstream stores the upload but only ever showed the extracted audio |
| Outer `$`/`$$` stripped from maths blocks | `pipeline/generate/notes.py` | KaTeX rejects delimiters inside a block that is already maths |
| Cards with `{{c1::…}}` become cloze; `{{c1:` is repaired | `pipeline/schemas.py` | Models omit `type` on cloze cards |
| Card text renders cloze blanks (until flipped), inline `$maths$` and `` `code` `` | `frontend/src/components/CardText.tsx`, `routes/Review.tsx`, `routes/Source.tsx` | Upstream printed card text verbatim: `{{c1::answer}}` gave the answer away and formulas showed raw LaTeX |
| Conversations can be deleted from the chat sidebar | `frontend/src/routes/Chat.tsx` | Upstream had the API endpoint and client call but no button for it |
| Chat retrieval matches any word of the question on the keyword side (`OR`, BM25-ranked); the search box still needs every word | `db/search.py`, `rag/retrieve.py` | Typed questions rarely have every word in one passage, so chat silently ran on vector search alone |
| Desktop sidebar collapses to an icon rail (remembered per browser) | `frontend/src/components/layout/AppShell.tsx`, `lib/store.ts` | Requested |
| Chat answers render markdown: bold, lists, headings, tables, code, `$maths$` (KaTeX); citations stay clickable | `frontend/src/lib/markdown.ts`, `components/Markdown.tsx`, `routes/Chat.tsx`; tests in `frontend/tests` (`npm test`) | Upstream printed answers verbatim, so `**bold**`, `*` bullets and `$O(n)$` showed raw |
| A structured call that hits the output token limit gets one fresh attempt before it fails | `providers/gateway.py`, `tests/test_providers.py` | Under schema enforcement Gemma sometimes falls into a loop until `max_tokens`: whitespace after an unescaped `"`, the same block over and over, or `segment_ids: [24, 24, …]`. About 3% of note-section calls (6 of ~210 in the live DB and trials, 2026-10-22), each lost as a "could not be generated" section after ~5 minutes. It's a bad draw, so a fresh sample almost always finishes. A prompt rule against double quotes didn't lower the rate and made thinking ~35% longer |
| Fields the app depends on are required in the schema sent to the model (`_require`): quiz `type`/`question`/`options`/`explanation`/`answer`, grading `feedback`/`score`/`is_correct`, concept `definition`, outline `thesis`/`segment_ids`, rerank `score`, podcast `speaker`, title, and every list wrapper (`items`, `blocks`, `cards`…). Quiz `explanation` now comes before `answer`, and grading `feedback` before `score`, so the last required field is one the model writes last anyway. Validation still accepts missing fields | `pipeline/schemas.py`, `tests/test_schemas.py` | Under grammar enforcement an optional field can be skipped for good: Bonsai 2 (llama.cpp) returned every quiz explanation empty, a skipped grading `score` would mark every answer 0 and wrong, and `{}` was a valid empty result for every list. Checked 2026-10-22 on Gemma 4, Bonsai 2 and Qwen3.8 |
| LaTeX commands the model leaves unescaped in JSON (`\times`, `\frac`, `\beta`…) are repaired before parsing | `providers/llm/openai_compat.py` | JSON decoded them as tab, form feed and backspace, so formulas in notes lost their commands |
| **Learn** tab: a source becomes a short guided course, after the teach skill (github.com/mattpocock/skills, `skills/productivity/teach`). Built on demand from the tab (never by ingest) by the `generate_course` job: a plan call picks 1–8 lessons from the concepts, then one call per lesson writes an objective, a why tied to the learner's optional goal ("mission"), a short explanation and worked example with clickable `[segment]` cites, 3 checks (MCQ with answers spread over positions, plus one short answer graded by the quiz grader) and a "try it" task, then a reference sheet (cheat sheet plus a glossary from the concepts, printable via `?sheet=true`). Checks are `QuizItem`s with `lesson_id` and `ephemeral` (outside the test bank, untouched by quiz regenerate) and answers move concept mastery. Later lessons open with warm-up questions on earlier lessons' weakest concepts (bank items first, no model call). Lesson body is read-only markdown. Adds the fork's first migration (`courses`, `lessons`, `quiz_items.lesson_id`, as a plain ADD COLUMN because a batch rebuild of `quiz_items` would cascade-delete `quiz_attempts`) | `pipeline/generate/course.py`, `prompts/course_outline.md`, `lesson.md`, `course_reference.md`, `pipeline/schemas.py`, `api/learn.py`, `handlers/ingest.py`, `db/models.py`, `db/migrations/versions/b3c1d2e4f5a6_*`, `frontend/src/components/learn/LearnTab.tsx`, `routes/Source.tsx`, `router.tsx`, `lib/*.ts`, `tests/test_learn.py` | Requested: notes are for reading; this is a learning path with retrieval practice, spacing and a reference to come back to |

## Updating

```bash
git fetch upstream && git rebase upstream/main   # here, then push
# on LXC 134:
cd /opt/quicklearn && git pull && docker compose up -d --build
```
