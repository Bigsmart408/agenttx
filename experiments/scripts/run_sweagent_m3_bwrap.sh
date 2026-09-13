#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON_BIN=/home/bfq/miniconda3/envs/agenttx/bin/python3.11

exec bwrap \
  --bind / / \
  --dev-bind /dev /dev \
  --proc /proc \
  --tmpfs /root \
  --tmpfs /sys \
  --tmpfs /run \
  --setenv HOME /root \
  --setenv AGENTTX_MOTIVATION_BWRAP 1 \
  --setenv SWE_AGENT_LOG_STREAM_LEVEL ERROR \
  --setenv CONDA_PREFIX /home/bfq/miniconda3/envs/agenttx \
  --setenv PYTHONPATH "$ROOT_DIR/src:$ROOT_DIR" \
  "$PYTHON_BIN" -u "$ROOT_DIR/experiments/scripts/bench_intra_turn_replay.py" \
  --harness sweagent "$@"
