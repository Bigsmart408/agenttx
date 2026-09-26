#!/usr/bin/env bash
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RESULT_DIR=${1:?usage: run_auto_fault_localization_swe10.sh RESULT_DIR [LOG_PATH]}
LOG_PATH=${2:-}
if (( $# >= 2 )); then
  shift 2
else
  shift
fi
PYTHON=${AGENTTX_PYTHON:-/home/bfq/miniconda3/envs/agenttx/bin/python3.11}
SEED=${AGENTTX_FAULT_SEED:-0}

if [[ -n "$LOG_PATH" ]]; then
  mkdir -p "$(dirname "$LOG_PATH")"
  exec >>"$LOG_PATH" 2>&1
fi

tasks=(
  astropy__astropy-12907
  astropy__astropy-14182
  django__django-10914
  django__django-11001
  matplotlib__matplotlib-18869
  matplotlib__matplotlib-22711
  pytest-dev__pytest-5103
  pytest-dev__pytest-8906
  scikit-learn__scikit-learn-10297
  scikit-learn__scikit-learn-13142
)
if (( $# > 0 )); then
  tasks=("$@")
fi

mkdir -p "$RESULT_DIR"
status=0
for task in "${tasks[@]}"; do
  echo "START $task $(date -Is)"
  if PYTHONPATH="$ROOT/src:$ROOT" "$PYTHON" \
    "$ROOT/experiments/scripts/smoke_auto_fault_localization.py" \
    --task "$task" \
    --python "$PYTHON" \
    --seed "$SEED" \
    --output "$RESULT_DIR/$task.json"; then
    task_status=0
  else
    task_status=$?
    status=1
  fi
  echo "END $task rc=$task_status $(date -Is)"
done

exit "$status"
