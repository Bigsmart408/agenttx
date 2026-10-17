# Source this file before local AgentTX environment preparation or experiments.
export AGENTTX_LOCAL_ONLY=1
export AGENTTX_ENVIRONMENT_COHORT=environment-preparation-local-20261017-r3
export AGENTTX_DATA_ROOT=/mnt/data/agenttx-runtime
export AGENTTX_CACHE_DIR="$AGENTTX_DATA_ROOT/cache"
export AGENTTX_RESULTS_DIR="$AGENTTX_DATA_ROOT/results"
export AGENTTX_LOGS_DIR="$AGENTTX_DATA_ROOT/logs"
export TMPDIR="$AGENTTX_DATA_ROOT/tmp"
export AGENTTX_TRACE_SPOOL_ROOT="$AGENTTX_DATA_ROOT/tmp"
export PIP_CACHE_DIR="$AGENTTX_DATA_ROOT/pip-cache"
export UV_CACHE_DIR="$AGENTTX_DATA_ROOT/uv-cache"
export HF_HOME="$AGENTTX_DATA_ROOT/hf-cache"
export TIKTOKEN_CACHE_DIR="$AGENTTX_DATA_ROOT/tiktoken-cache"
export DOCKER_HOST=unix:///run/agenttx-docker/docker.sock
export AGENTTX_DOCKER_BUILD_NETWORK=host
export PYTHONDONTWRITEBYTECODE=1
