#!/usr/bin/env bash
set -euo pipefail
cd /home/bfq/agenttx-executions/swe-local-monitored-20261018-full-r1/project
source scripts/agenttx-local-env.sh
export PATH=/home/bfq/.venvs/agenttx/bin:/home/bfq/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONPATH="$PWD/src:$PWD"
export CODEX_CONFIG_FILE=/home/bfq/.config/agenttx/experiment-20261017/config.toml
export CODEX_AUTH_FILE=/home/bfq/.config/agenttx/experiment-20261017/auth.json
export AGENTTX_SWE_PROFILE=/home/bfq/.config/agenttx/experiment-20261017/sweagent.toml
export AGENTTX_SWE_AGENT_ROOT=/home/bfq/agenttx-deps/SWE-agent-3ea751c087f32b16e039a2233dd6eefecef325d5
export SWE_AGENT_TRAJECTORY_DIR=/mnt/data/agenttx-runtime/logs/sweagent-trajectories
export AGENTTX_HTTPBIN_IMAGE=sha256:462caaba497ce310ec1584e6e6021e66710570bcd815b2b7df80713b8d14683b
export AGENTTX_SWE_MONITORED_RUN=sweagent-swe-local-monitored-20261018-full-r1
export AGENTTX_SKIP_ORPHAN_REAP=1
export AGENTTX_RANDOMIZE_CRASH=1
export AGENTTX_FAULT_SEED=20261018
export AGENTTX_PRECRASH_REFERENCE_PROGRESS=0
export AGENTTX_SWE_VERIFY=docker
export AGENTTX_NO_PROXY=1
export NO_PROXY='*'
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
test "$(hostname)" = r760-ssd
nohup /home/bfq/.venvs/agenttx/bin/python -u experiments/scripts/run_swe_monitored_local.py --full-suite \
  > /mnt/data/agenttx-runtime/logs/sweagent-swe-local-monitored-20261018-full-r1.log 2>&1 < /dev/null &
printf '%s\n' "$!" > /mnt/data/agenttx-runtime/validation/local-swe-full-20261018.pid
