#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON_BIN=/home/bfq/miniconda3/envs/agenttx/bin/python3.11

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY='*' no_proxy='*' AGENTTX_NO_PROXY=1

exec bwrap \
  --bind / / \
  --dev-bind /dev /dev \
  --proc /proc \
  --tmpfs /root \
  --tmpfs /sys \
  --setenv HOME /root \
  --setenv AGENTTX_MOTIVATION_BWRAP 1 \
  --setenv SWE_AGENT_LOG_STREAM_LEVEL ERROR \
  --setenv CONDA_PREFIX /home/bfq/miniconda3/envs/agenttx \
  --setenv PYTHONPATH "$ROOT_DIR/src:$ROOT_DIR" \
  "$PYTHON_BIN" -u "$ROOT_DIR/experiments/scripts/capture_clean_native_traces.py" \
  --model gpt-5.6-luna \
  --reasoning-effort max \
  "$@"
