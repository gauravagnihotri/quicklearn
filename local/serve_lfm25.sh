#!/bin/bash
# Serve Liquid LFM2.5 24B-A2B (AWQ INT4 W4A16) with vLLM v0.29.0 on the RTX 4500 Ada.
# MoE with 2B active params, hybrid short-conv + GQA, always reasons before answering.
# Shares port 8000 and the container name with serve_gemma4.sh, so only one runs.
MODEL=cyankiwi/LFM2.5-24B-A2B-AWQ-INT4
NAME=${NAME:-vllm-coder}
MAXLEN=${MAXLEN:-65536}
GPU_UTIL=${GPU_UTIL:-0.83}  # leaves ~4 GB for speaches (STT/TTS for QuickLearn)
KV_DTYPE=${KV_DTYPE:-auto}  # only the GQA layers keep a KV cache, so bf16 fits
docker rm -f $NAME sglang-coder >/dev/null 2>&1
docker run -d --name $NAME --gpus all --restart unless-stopped \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --env "HF_TOKEN=$HF_TOKEN" -p 8000:8000 --ipc=host \
  vllm/vllm-openai:v0.29.0 \
  --model $MODEL --served-model-name lfm25 \
  --max-model-len $MAXLEN --gpu-memory-utilization $GPU_UTIL --kv-cache-dtype $KV_DTYPE \
  --max-num-seqs 8 --max-num-batched-tokens 4096 --async-scheduling \
  --enable-auto-tool-choice --tool-call-parser lfm2 --reasoning-parser qwen3
