# Local setup

This fork runs quicklearn on Proxmox LXC 134 (`/opt/quicklearn`, http://192.168.86.81:8080) with all
inference on a workstation GPU (RTX 4500 Ada, 24 GB) at 192.168.86.20:

| Port | Service | Serve script (in `~/Documents/fix_gpu_drivers`) |
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
| Outer `$`/`$$` stripped from maths blocks | `pipeline/generate/notes.py` | KaTeX rejects delimiters inside a block that is already maths |
| Cards with `{{c1::…}}` become cloze; `{{c1:` is repaired | `pipeline/schemas.py` | Models omit `type` on cloze cards |
| Card text renders cloze blanks (until flipped), inline `$maths$` and `` `code` `` | `frontend/src/components/CardText.tsx`, `routes/Review.tsx`, `routes/Source.tsx` | Upstream printed card text verbatim: `{{c1::answer}}` gave the answer away and formulas showed raw LaTeX |

## Updating

```bash
git fetch upstream && git rebase upstream/main   # here, then push
# on LXC 134:
cd /opt/quicklearn && git pull && docker compose up -d --build
```
