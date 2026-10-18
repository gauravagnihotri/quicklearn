# Local setup

This fork runs quicklearn on Proxmox LXC 134 (`/opt/quicklearn`, http://192.168.86.81:8080) with all
inference on a workstation GPU (RTX 4500 Ada, 24 GB) at 192.168.86.20:

| Port | Service | Serve script (copies in [`local/`](local/); run from `~/Documents/fix_gpu_drivers`) |
|---|---|---|
| 8000 | vLLM v0.29.0, Gemma 4 26B-A4B QAT AWQ INT4, thinking on | `serve_gemma4.sh` |
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

## Updating

```bash
git fetch upstream && git rebase upstream/main   # here, then push
# on LXC 134:
cd /opt/quicklearn && git pull && docker compose up -d --build
```
