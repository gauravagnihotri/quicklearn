# Local setup

This fork runs quicklearn on Proxmox LXC 134 (`/opt/quicklearn`, http://192.168.86.81:8080) with all
inference on a workstation GPU (RTX 4500 Ada, 24 GB) at 192.168.86.20:

| Port | Service | Serve script (in `~/Documents/fix_gpu_drivers`) |
|---|---|---|
| 8000 | vLLM v0.29.0, Gemma 4 26B-A4B QAT AWQ INT4, thinking on | `serve_gemma4.sh` |
| 8001 | speaches: faster-whisper large-v3-turbo (STT) + Kokoro 82M (TTS) | `serve_speaches.sh` |

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
| Note blocks require `type` and `text` in the JSON schema | `pipeline/schemas.py` | vLLM emits properties in schema order; optional `text` got skipped |
| Empty headings fall back to the section title; leading `#`s stripped | `pipeline/generate/notes.py` | Models omit heading text (headings were dropped) or keep Markdown `##` |
| Outer `$`/`$$` stripped from maths blocks | `pipeline/generate/notes.py` | KaTeX rejects delimiters inside a block that is already maths |
| Cards with `{{c1::…}}` become cloze; `{{c1:` is repaired | `pipeline/schemas.py` | Models omit `type` on cloze cards |
| Cloze deletions render as blanks until the card is flipped | `frontend/src/components/ClozeText.tsx`, `routes/Review.tsx`, `routes/Source.tsx` | Upstream only handles cloze in the Anki export; the app showed `{{c1::answer}}` raw |

## Updating

```bash
git fetch upstream && git rebase upstream/main   # here, then push
# on LXC 134:
cd /opt/quicklearn && git pull && docker compose up -d --build
```
