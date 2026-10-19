<div align="center">

# quicklearn

**Self-hosted, open-source AI study engine.**
Ingest any lecture, PDF, or video — get notes, flashcards, quizzes, and podcasts.
Your data never leaves your machine.

</div>

```bash
git clone https://github.com/artbyjazi/quicklearn && cd quicklearn
```

Then [build and run it](#running-from-source) — one `npm` build, one `pip install`, one command to
start. Open <http://127.0.0.1:8080>, paste in an API key, upload a lecture. That's the whole setup.

> [!NOTE]
> A `Dockerfile` and `docker-compose.yml` are included and written to spec, but **have not been
> built or run** — treat them as unverified. The from-source path below is the tested one.

> [!IMPORTANT]
> **This is a fork** of [artbyjazi/quicklearn](https://github.com/artbyjazi/quicklearn), kept on
> branch [`local-inference`](../../tree/local-inference). It runs every model locally on one
> 24 GB GPU and adds a few features. The section below covers what differs; everything after it
> is upstream's README.

## This fork

### What's different

| | |
|---|---|
| **Local inference only** | LLM, speech-to-text and text-to-speech run on a workstation GPU through OpenAI-compatible servers: [vLLM](https://github.com/vllm-project/vllm) serving Gemma 4 26B-A4B (INT4, thinking on) and [speaches](https://github.com/speaches-ai/speaches) serving distil-whisper large-v3 and Kokoro. Embeddings stay on the built-in local model. No API keys, no cloud calls |
| **Simplified notes** | Every source also gets an ADHD-friendly second note, shown with a **Simplified** switch on the Notes tab. Its prompt carries the rules of the [i-have-adhd](https://github.com/ayghri/i-have-adhd) skill (MIT) mapped onto study notes: lead with the idea to grasp, numbered steps, lists of five or fewer, a "You can now" summary and one self-check per section |
| **Video playback** | Video sources play the original video, not just the extracted audio. Transcript clicks and note anchors seek it |
| **Formatted note text** | The prompt defines inline markup (`**bold**`, `*italic*`, `` `code` ``, `$maths$`, paragraphs, lists) and it renders as real formatting; inline maths renders with KaTeX |
| **Flashcards** | Cloze cards show blanks until flipped; maths and code render on cards |
| **Safer autosave** | The server refuses note saves from a tab running an older build, during processing, from an older note version, or that would empty several blocks at once |
| **Reasoning-model fixes** | Output-token headroom for thinking, longer timeouts, model-default sampling, and schema field ordering so vLLM structured output never drops block text |

The full patch list, with files and reasons, is in [`LOCAL_SETUP.md`](LOCAL_SETUP.md).

### Running this setup

1. **GPU machine:** start the model servers with the scripts in [`local/`](local/). They need
   Docker with the NVIDIA runtime. Set `HF_TOKEN` first if your Hugging Face account needs it.

   ```bash
   ./local/serve_gemma4.sh     # vLLM on :8000, served as "gemma4"
   ./local/serve_speaches.sh   # STT + TTS on :8001
   ```

2. **App host:** clone this branch and point
   [`docker-compose.override.yml`](docker-compose.override.yml) at the GPU machine's address
   (`192.168.86.20` here), then build and start:

   ```bash
   git clone -b local-inference https://github.com/gauravagnihotri/quicklearn && cd quicklearn
   docker compose up -d --build   # http://<host>:8080
   ```

Both model servers restart with Docker. Reload open QuickLearn tabs after every deploy: the
server refuses note saves from a page built before the update.

### Keeping up with upstream

```bash
git fetch upstream && git rebase upstream/main && git push --force-with-lease
# then on the app host: git pull && docker compose up -d --build
```

---

## What it does

| | |
|---|---|
| **Ingest** | Audio, video, PDF, DOCX, PPTX, images, YouTube URLs, articles, pasted text, or record a lecture straight in the browser |
| **Notes** | Structured, editable notes with math, diagrams, code and tables — every block linked back to the exact moment in the source that produced it |
| **Flashcards** | Generated from extracted concepts, scheduled with FSRS-6, exportable to Anki |
| **Quizzes** | MCQ, cloze, short answer and free response, with a mastery model that drills your weak concepts until they stick |
| **Chat** | Ask questions across one source, a folder, or your whole library — with citations that click through to the source |
| **Podcasts** | Two-host audio dialogue from your notes, with an RSS feed you can subscribe to in any podcast app |

## Why

Three things about the commercial options are worth fixing.

**Cost.** Notes and quizzes are the core value, and they sit behind a subscription. Here you pay
metered API rates instead — around **$0.02 for a 60-minute lecture** on Groq, or **$0.00** if you
point it at a local Ollama instance.

**Privacy.** Recording a lecture and uploading it to a third party is legally grey, and flatly
prohibited for a lot of medical, legal and enterprise material. Nothing here leaves localhost except
the inference call itself — and with Ollama, not even that.

**Lock-in.** Your corpus is a SQLite file in a directory you chose. Export to Markdown, DOCX, or
Anki `.apkg` whenever you want.

## Cost

Measured per 60-minute lecture, full pipeline — transcript, notes, concepts, cards and a quiz.

| Setup | STT | LLM | Total |
|---|---|---|---|
| Groq (`whisper-large-v3-turbo` + `llama-3.3-70b`) | ~$0.01 | ~$0.01 | **~$0.02** |
| OpenAI (`whisper-1` + `gpt-4o-mini`) | ~$0.36 | ~$0.03 | **~$0.39** |
| Ollama, fully local | $0.00 | $0.00 | **$0.00** |

quicklearn shows you an estimate before every job and tracks a running total on the dashboard. The
bundled pricing table is in [`pricing.py`](backend/quicklearn/providers/pricing.py) — correct as of
the version you're reading, but providers change prices, so treat it as an estimate.

## Privacy — what actually leaves your machine

Precisely one thing: the inference request to whichever provider you configured. That means the
audio or text being processed, plus the prompt. Nothing else — no telemetry, no analytics, no crash
reporting, no phone-home version check.

- Your API keys are encrypted at rest with a key derived from `/data/.secret`, generated on first
  boot and never transmitted.
- The server binds to `127.0.0.1` by default. There is no auth because there is nothing to
  authenticate against — if you expose it beyond localhost, put it behind your own proxy and auth.
- Configure Ollama as your provider and **nothing leaves the machine at all.**

## Honest comparison

Where quicklearn loses is as important as where it wins.

| | quicklearn | Turbo AI | Coconote | NotebookLM |
|---|---|---|---|---|
| Price | Free + API costs | Subscription | Subscription | Free |
| Self-hosted | ✅ | ❌ | ❌ | ❌ |
| Fully offline option | ✅ Ollama | ❌ | ❌ | ❌ |
| Own your data | ✅ SQLite on disk | ❌ | ❌ | ❌ |
| Anki export | ✅ | ❌ | ❌ | ❌ |
| Bring your own model | ✅ | ❌ | ❌ | ❌ |
| **Setup effort** | ❌ Docker + an API key | ✅ Sign up | ✅ Sign up | ✅ Sign up |
| **Mobile apps** | ❌ Responsive web only | ✅ Native | ✅ Native | ✅ Native |
| **Polish** | ❌ v1 of a solo project | ✅ Funded team | ✅ Funded team | ✅ Google |
| **Live transcription** | ❌ Record → process on stop | ✅ | ✅ | n/a |
| **Cross-device sync** | ❌ Single machine | ✅ | ✅ | ✅ |
| **Someone to call** | ❌ GitHub issues | ✅ Support | ✅ Support | ✅ |

If you want something that just works on your phone with zero setup, buy one of the others. If you
want to own your study corpus and not pay rent on it, this is for you.

## Provider setup

You need **one LLM provider** and, if you're processing audio, **one STT provider**. Embeddings
default to a local ONNX model that needs no key and downloads itself (~130 MB) on first use.

All of it is configured in **Settings → Providers** in the UI. There are no config files to edit and
no environment variables to set.

<details>
<summary><b>Groq</b> — recommended, has a free tier, fastest and cheapest</summary>

1. Sign up at <https://console.groq.com>
2. **API Keys** → **Create API Key**, copy it
3. In quicklearn: **Settings → Providers → Groq**, paste, **Test connection**
4. Groq covers both LLM and STT, so this single key gets you the whole pipeline

</details>

<details>
<summary><b>OpenAI</b></summary>

1. <https://platform.openai.com/api-keys> → **Create new secret key**
2. Paste into **Settings → Providers → OpenAI**, **Test connection**
3. Covers LLM, STT (`whisper-1`), TTS and embeddings — everything from one key

</details>

<details>
<summary><b>Anthropic</b></summary>

1. <https://console.anthropic.com/settings/keys> → **Create Key**
2. Paste into **Settings → Providers → Anthropic**, **Test connection**
3. LLM only — pair it with Groq or Deepgram for transcription

</details>

<details>
<summary><b>Ollama</b> — fully local, $0, nothing leaves your machine</summary>

1. Install from <https://ollama.com>
2. Pull a model: `ollama pull llama3.1:8b`
3. In quicklearn: **Settings → Providers → Ollama**. The default base URL
   `http://localhost:11434` is usually right. **Test connection** lists your pulled models.
4. Ollama has no speech-to-text, so audio still needs a cloud STT provider. Text, PDF and YouTube
   sources with captions work with zero network calls.

</details>

<details>
<summary><b>Others</b> — Google, OpenRouter, Deepgram, ElevenLabs, LM Studio</summary>

Same pattern: get a key from the provider, paste it into **Settings → Providers**, hit **Test
connection**. LM Studio is OpenAI-compatible and needs no key — just point it at
`http://localhost:1234/v1`.

</details>

### Per-task model routing

Different work needs different models, and paying frontier prices for relevance classification is
pointless. quicklearn routes three task classes independently in **Settings → Model routing**:

- **`fast`** — classification, tagging, relevance filtering, reranking. Use the cheapest thing you have.
- **`standard`** — notes, flashcards, quizzes. Mid-tier.
- **`deep`** — podcast scripting and complex synthesis. Your best model.

### No key yet?

There's a built-in **`mock`** provider that returns deterministic fake output. It exists so the test
suite can run offline, but it also means you can click through every screen and understand the
product before you sign up for anything. Select it in Settings. It is not useful for actual studying.

## Running from source

**Requirements:** Python 3.11+, Node 20+, and [ffmpeg](https://ffmpeg.org/download.html) on `PATH`
(`ffmpeg -version` should print something). No database to install, no services to start.

**1. Get the code**

```bash
git clone https://github.com/artbyjazi/quicklearn && cd quicklearn
```

**2. Build the frontend.** This compiles the SPA and copies it into the Python package, which is
where the server looks for it.

```bash
cd frontend && npm ci && npm run build && cd ..
```

**3. Install the backend into a virtual environment.** The venv is not optional — modern Linux and
macOS refuse a bare `pip install` with `externally-managed-environment`.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

<details>
<summary>Windows (PowerShell)</summary>

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

</details>

The `[dev]` extra adds pytest, mypy and ruff. Drop it for a plain `pip install -e .` if you never
intend to run the test suite.

**4. Run it**

```bash
python -m quicklearn
```

Open <http://127.0.0.1:8080>. Data lands in `~/quicklearn` by default; pass `--data-dir` to put it
somewhere else.

> [!NOTE]
> **First ingest will pause for a while.** The default embedding model is a local ONNX file that
> downloads itself on first use — roughly 130 MB, once. It caches inside your data directory, so a
> second run is instant. There is no progress bar for it.

### Reproducible installs

`backend/uv.lock` pins every transitive dependency. If you have [uv](https://docs.astral.sh/uv/),
`uv sync --extra dev` from `backend/` gives you the exact resolved set instead of whatever pip
picks today. `frontend/package-lock.json` does the same job for `npm ci`.

### Development

Run the backend and Vite as two processes; Vite serves the UI on 5173 with hot reload and proxies
`/api` — including the WebSockets — through to port 8080.

```bash
cd backend && python -m quicklearn --reload
```

```bash
cd frontend && npm run dev
```

Open <http://127.0.0.1:5173>. Because the proxy makes everything same-origin, you do **not** need
`--dev-cors`; that flag is only for pointing a UI on some other origin at the API.

### Tests

```bash
cd backend && pytest -q && ruff check . && ruff format --check .
```

The whole suite runs offline against the `mock` provider — no keys, no network. Audio tests
self-skip if ffmpeg is missing.

`mypy quicklearn` is configured and runs in CI, but is **not** green yet — strict mode reports
around 40 pre-existing errors, mostly missing annotations on SQLAlchemy row unpacking and a
`Coroutine` vs `Awaitable` mismatch in the job dispatch table. Contributions welcome; it is
non-blocking in CI until it reaches zero.

### Configuration

Everything a user configures — providers, keys, model routing — lives in **Settings** in the UI and
is stored in the database, not in files. The process itself takes a handful of environment
variables, all optional, each with a matching CLI flag:

| Variable | Flag | Default | |
|---|---|---|---|
| `QUICKLEARN_DATA_DIR` | `--data-dir` | `/data` if it exists, else `~/quicklearn` | Database, media, cache and key |
| `QUICKLEARN_HOST` | `--host` | `127.0.0.1` | Set to `0.0.0.0` only behind your own proxy and auth |
| `QUICKLEARN_PORT` | `--port` | `8080` | |
| `QUICKLEARN_WORKERS` | `--workers` | `2` | Background job concurrency |
| `QUICKLEARN_MAX_PARALLEL_LLM` | — | `4` | Concurrent inference calls |
| `QUICKLEARN_LOG_LEVEL` | `--log-level` | `info` | |
| `QUICKLEARN_RELOAD` | `--reload` | off | Development only |
| `QUICKLEARN_DEV_CORS` | `--dev-cors` | off | Allows `localhost:5173`; unnecessary with the Vite proxy |

Provider keys can also be supplied by environment variable, which overrides whatever is stored in
the database — useful for containers and CI: `QUICKLEARN_OPENAI_API_KEY`, `QUICKLEARN_GROQ_API_KEY`,
`QUICKLEARN_ANTHROPIC_API_KEY`, `QUICKLEARN_GOOGLE_API_KEY`, `QUICKLEARN_OPENROUTER_API_KEY`,
`QUICKLEARN_DEEPGRAM_API_KEY`, `QUICKLEARN_ELEVENLABS_API_KEY`, plus
`QUICKLEARN_OLLAMA_BASE_URL` and `QUICKLEARN_LMSTUDIO_BASE_URL`.

### Docker

Unverified — see the note at the top — but written to match the layout the app expects:

```bash
docker compose up --build
```

Or without compose:

```bash
docker build -t quicklearn . && docker run -p 8080:8080 -v quicklearn-data:/data quicklearn
```

## Architecture

One Python process. SQLite. No Redis, no Postgres, no sidecars.

```
FastAPI (Uvicorn) ─┬─ JSON API           /api/v1/*
                   ├─ WebSockets         /api/v1/ws/jobs, /api/v1/chat/ws/{id}
                   ├─ Static SPA         React + Vite, prebuilt
                   └─ asyncio worker pool ── polls the SQLite `jobs` table
```

Everything lives under one directory, so backing up is a directory copy and uninstalling is deleting
it:

```
/data
  quicklearn.db      SQLite — metadata, content, embeddings
  media/<source_id>/ original upload, normalized audio.wav, podcast.mp3
  cache/             ONNX embedding model weights
  .secret            encryption key for stored API keys (chmod 600)
```

Ingest is six discrete, individually retryable job types: **acquire → normalize → extract → segment
→ filter → generate**. Note generation is map-reduce (outline, then parallel section expansion, then
assembly) rather than single-shot, because single-pass generation degrades badly past a few thousand
tokens of output no matter how large the context window is.

Every generated artifact — note block, flashcard, quiz item, chat citation — stores the IDs of the
source segments it came from. That's what makes one-click click-to-source work, and it's the main
defence against hallucination: if a claim can't point at its evidence, that's visible.

## Legal

**yt-dlp / YouTube.** quicklearn can ingest YouTube URLs using
[yt-dlp](https://github.com/yt-dlp/yt-dlp). **This may conflict with YouTube's Terms of Service.**
The feature is user-directed: you supply a URL, it fetches that URL. No content is scraped by
default, nothing is bundled, and no discovery or bulk download is provided. Whether your use is
permitted is your call to make in your jurisdiction and under your agreement with YouTube.

**Recording lectures.** Consent laws vary by state and country, and your institution likely has its
own policy independent of the law. quicklearn gives you a record button; it does not give you
permission to use it.

## License

MIT — see [LICENSE](LICENSE).
