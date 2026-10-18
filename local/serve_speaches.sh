#!/bin/bash
# Serve speaches (OpenAI-compatible STT + TTS) on the RTX 4500 Ada for QuickLearn on LXC 134.
# STT: distil-whisper large-v3; TTS: Kokoro 82M. Runs alongside serve_gemma4.sh (vLLM at 0.83 GPU util).
# Models load on first request and unload after 300 s idle, freeing ~1.5-2 GB of VRAM.
NAME=${NAME:-speaches}
STT_MODEL=Systran/faster-distil-whisper-large-v3  # English-only; turbo looped on the last sentence and large-v3 does not fit beside Gemma
TTS_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX
docker rm -f $NAME >/dev/null 2>&1
docker run -d --name $NAME --gpus all --restart unless-stopped \
  -v speaches-hf-cache:/home/ubuntu/.cache/huggingface/hub \
  -p 8001:8000 \
  ghcr.io/speaches-ai/speaches:latest-cuda
# Models must be downloaded once into the cache volume before first use
until curl -sf http://localhost:8001/health >/dev/null; do sleep 2; done
for m in $STT_MODEL $TTS_MODEL; do curl -s -X POST "http://localhost:8001/v1/models/$m"; echo; done
