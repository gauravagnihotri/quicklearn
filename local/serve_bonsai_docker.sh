#!/bin/bash
# Production LLM: PrismML Ternary Bonsai 2 27B on port 8000, replacing Gemma 4 (vllm-coder) since 2026-10-22.
# Runs PrismML's llama.cpp build (~/llama.cpp-prism, CUDA 12.8) inside the vLLM image with the host's CUDA 12.9
# libraries mounted (the image ships CUDA 13), so it restarts with Docker on boot like vllm-coder did. Answers to "gemma4" too, so
# QuickLearn, Vane, Omnara and anydoc keep working unchanged. Back to Gemma:
#   docker stop llm-bonsai && docker start vllm-coder
# Blind QuickLearn reviews and probes behind the switch: memory qwen38-bonsai-trial, ql_trial/.
NAME=${NAME:-llm-bonsai}
IMAGE=${IMAGE:-vllm/vllm-openai:v0.29.0}
LLAMA=${LLAMA:-$HOME/llama.cpp-prism/bin}
MODELS=${MODELS:-/media/garyfirestorm/SSD_Drv/models/Ternary-Bonsai-2-27B-gguf}
PORT=${PORT:-8000}
CTX=${CTX:-262144}   # shared by all slots (--kv-unified), so one request can use all of it
SLOTS=${SLOTS:-4}    # QuickLearn expands several note sections at once
CUDA_LIBS=${CUDA_LIBS:-/usr/local/cuda-12.9/lib64}
KV=${KV:-q8_0}       # ~34 KiB/token: 262k is ~8.7 GB, leaving room for speaches (~2.2 GB)

docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" --gpus all --restart unless-stopped -p "$PORT:8000" \
  -v "$LLAMA:/llama:ro" -v "$MODELS:/models:ro" -v "$CUDA_LIBS:/cuda12:ro" -e LD_LIBRARY_PATH=/llama:/cuda12 \
  --entrypoint /llama/llama-server "$IMAGE" \
  -m /models/Ternary-Bonsai-2-27B-PTQ1_0.gguf --mmproj /models/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf \
  -a bonsai,gemma4 -ngl 99 -c "$CTX" -np "$SLOTS" --kv-unified -fa on -ctk "$KV" -ctv "$KV" \
  --host 0.0.0.0 --port 8000 --jinja --metrics \
  --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.05 \
  $EXTRA
echo "$NAME on :$PORT (docker logs -f $NAME)"
