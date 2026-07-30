# syntax=docker/dockerfile:1
#
# Two stages: Node compiles the SPA, Python runs it. The compiled assets are copied into the
# Python package as `quicklearn/static/`, which is where `Config.static_dir` looks first.

# --- stage 1: build the frontend -------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build

# Copy manifests first so the dependency layer is cached across source changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# The repo build script also copies dist/ into ../backend; here there is no backend to copy
# into, so run Vite directly and let the next stage place the output.
RUN npx tsc -b && npx vite build

# --- stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim AS runtime

# ffmpeg and ffprobe are hard requirements for the audio/video pipeline
# (see backend/quicklearn/pipeline/media.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/quicklearn ./quicklearn
COPY --from=frontend /build/dist ./quicklearn/static

RUN pip install --no-cache-dir .

# The default bind address is 127.0.0.1, which is correct on a laptop but unreachable from
# outside a container — it must be 0.0.0.0 here.
ENV QUICKLEARN_HOST=0.0.0.0 \
    QUICKLEARN_PORT=8080 \
    QUICKLEARN_DATA_DIR=/data \
    PYTHONUNBUFFERED=1

# Everything the app owns lives under one directory: SQLite DB, media, model cache, key.
VOLUME ["/data"]
EXPOSE 8080

# Non-root: the mounted volume must be writable by this uid.
RUN useradd --create-home --uid 10001 quicklearn \
    && mkdir -p /data \
    && chown -R quicklearn:quicklearn /data
USER quicklearn

ENTRYPOINT ["quicklearn"]
