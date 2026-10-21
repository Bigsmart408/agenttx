#!/usr/bin/env bash
set -euo pipefail
cd /home/bfq/agenttx-executions/swe-local-monitored-20261018-full-r5/project
source scripts/agenttx-local-env.sh
export PATH=/home/bfq/.venvs/agenttx/bin:/home/bfq/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONPATH="$PWD/src:$PWD"
export CODEX_CONFIG_FILE=/home/bfq/.config/agenttx/experiment-20261017/config.toml
export CODEX_AUTH_FILE=/home/bfq/.config/agenttx/experiment-20261017/auth.json
export AGENTTX_SWE_PROFILE=/home/bfq/.config/agenttx/experiment-20261017/sweagent.toml
export AGENTTX_SWE_AGENT_ROOT=/home/bfq/agenttx-deps/SWE-agent-3ea751c087f32b16e039a2233dd6eefecef325d5
export SWE_AGENT_TRAJECTORY_DIR=/mnt/data/agenttx-runtime/logs/sweagent-trajectories
export AGENTTX_HTTPBIN_IMAGE=sha256:5197bbb61a81b10d093842cacded095afa6a9ecf904109e1173abb16283b473f
export AGENTTX_SWE_MONITORED_RUN=sweagent-swe-local-monitored-20261018-full-r5
export AGENTTX_SKIP_ORPHAN_REAP=1
export AGENTTX_RANDOMIZE_CRASH=1
export AGENTTX_RECOVERY_CONTEXT=full
export AGENTTX_SWE_INCLUDE_IDEAL=1
export AGENTTX_SWE_CANARY_PROOF=/mnt/data/agenttx-runtime/validation/swe-local-full-context-canary-20261018.json
export AGENTTX_FAULT_SEED=20261018
export AGENTTX_PRECRASH_REFERENCE_PROGRESS=0
export AGENTTX_SWE_VERIFY=docker
export AGENTTX_NO_PROXY=1
export NO_PROXY='*'
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
test "$(hostname)" = r760-ssd
while ! test -f /mnt/data/agenttx-runtime/results/sweagent-swe-local-monitored-20261018-full-r4/execution.json; do sleep 5; done
sleep 2
nohup /home/bfq/.venvs/agenttx/bin/python -u experiments/scripts/run_swe_monitored_local.py --full-suite --continue-from sweagent-swe-local-monitored-20261018-full-r4 --resume-all-valid --repair-large-write \
  > /mnt/data/agenttx-runtime/logs/sweagent-swe-local-monitored-20261018-full-r5.log 2>&1 < /dev/null &
printf '%s\n' "$!" > /mnt/data/agenttx-runtime/validation/local-swe-full-continuation-20261018.pid
