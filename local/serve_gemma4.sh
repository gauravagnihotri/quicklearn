#!/bin/bash
# Serve Google Gemma 4 26B-A4B (QAT, AWQ INT4 W4A16) with vLLM v0.29.0 on the RTX 4500 Ada.
# MoE with 4B active params; shares port 8000 and the container name with serve_nemotron.sh, so only one runs.
MODEL=cyankiwi/gemma-4-26B-A4B-it-qat-AWQ-INT4
NAME=${NAME:-vllm-coder}
MAXLEN=${MAXLEN:-65536}
GPU_UTIL=${GPU_UTIL:-0.83}  # leaves ~4 GB for speaches (STT/TTS for QuickLearn)
KV_DTYPE=${KV_DTYPE:-fp8}  # bf16 KV needs 3 GiB for 64k but only ~1.9 GiB is free at 0.83
THINK=${THINK:-1}  # Gemma 4 thinks only when asked; 1 turns it on by default for every client
THINK_ARGS=()
[ "$THINK" = 1 ] && THINK_ARGS=(--default-chat-template-kwargs '{"enable_thinking": true}')
docker rm -f $NAME sglang-coder >/dev/null 2>&1
docker run -d --name $NAME --gpus all --restart unless-stopped \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --env "HF_TOKEN=$HF_TOKEN" -p 8000:8000 --ipc=host \
  vllm/vllm-openai:v0.29.0 \
  --model $MODEL --served-model-name gemma4 \
  --max-model-len $MAXLEN --gpu-memory-utilization $GPU_UTIL --kv-cache-dtype $KV_DTYPE \
  --max-num-seqs 8 --max-num-batched-tokens 4096 \
  --limit-mm-per-prompt '{"image": 0}' --async-scheduling \
  --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 "${THINK_ARGS[@]}"
