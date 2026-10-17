#!/usr/bin/env bash
# Dedicated namespace required by SWE-agent; also bounds try's host /sys mounts.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PILOT_PYTHON=${AGENTTX_PYTHON:-python3}
PILOT_PYTHON=$(command -v "$PILOT_PYTHON")
export PATH="$(dirname "$PILOT_PYTHON"):$PATH"
exec bwrap \
  --bind / / --dev-bind /dev /dev --proc /proc \
  --tmpfs /root --tmpfs /sys --tmpfs /run \
  --setenv HOME /root \
  --setenv AGENTTX_MOTIVATION_BWRAP 1 \
  --setenv SWE_AGENT_LOG_STREAM_LEVEL ERROR \
  --setenv PYTHONDONTWRITEBYTECODE 1 \
  --setenv PYTHONPATH "$ROOT_DIR/src:$ROOT_DIR" \
  "$PILOT_PYTHON" -u "$ROOT_DIR/experiments/scripts/bench_runtime_recovery_pilot.py" "$@"
