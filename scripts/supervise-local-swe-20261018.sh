#!/usr/bin/env bash
set -euo pipefail
cd /home/bfq/agenttx
source scripts/agenttx-local-env.sh
export PYTHONDONTWRITEBYTECODE=1
exec /home/bfq/.venvs/agenttx/bin/python -u experiments/scripts/supervise_swe_local.py
