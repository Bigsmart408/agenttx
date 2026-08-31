#!/bin/bash
# Relaunch Codex until every campaign task has causal tests_ok=True.
set -u
ROOT=/home/pengpeng/agenttx
LOGDIR="$ROOT/experiments/results/codex_operator_testsok"
PY=/home/pengpeng/miniconda3/envs/agenttx/bin/python
CLASH=/home/pengpeng/.local/bin/agentTX-clash
CODEX=/home/pengpeng/.local/bin/codex
STATUS="$ROOT/experiments/scripts/codex_campaign_status.py"
PROMPT_BASE="$ROOT/experiments/PROMPTS/run_10case_keep_iterating.md"
export PATH="/home/pengpeng/.local/bin:$PATH"

mkdir -p "$LOGDIR"
exec >>"$LOGDIR/supervisor.log" 2>&1

echo "$(date -Is) supervisor start pid=$$"

if ! "$CLASH" status | grep -q running; then
  "$CLASH" start || true
fi

round=0
while true; do
  if [ -f "$LOGDIR/STOP" ]; then
    echo "$(date -Is) STOP file present; exiting"
    exit 0
  fi

  "$PY" "$STATUS" || true
  # shellcheck disable=SC1091
  ALL_SOLVED=0
  NEXT_TASK=""
  PENDING_TASKS=""
  SOLVED_COUNT=0
  if [ -f "$LOGDIR/progress.env" ]; then
    # values are task ids without spaces
    ALL_SOLVED=$(sed -n 's/^ALL_SOLVED=//p' "$LOGDIR/progress.env" | head -1)
    NEXT_TASK=$(sed -n 's/^NEXT_TASK=//p' "$LOGDIR/progress.env" | head -1)
    PENDING_TASKS=$(sed -n 's/^PENDING_TASKS=//p' "$LOGDIR/progress.env" | head -1)
    SOLVED_COUNT=$(sed -n 's/^SOLVED_COUNT=//p' "$LOGDIR/progress.env" | head -1)
  fi

  if [ "${ALL_SOLVED:-0}" = "1" ]; then
    echo "$(date -Is) all 10 tasks have causal tests_ok; exiting"
    exit 0
  fi

  if pgrep -u pengpeng -f 'bench_official_tasks.py' >/dev/null; then
    echo "$(date -Is) bench still running; wait before launching Codex"
    sleep 60
    continue
  fi

  if pgrep -u pengpeng -f 'codex exec' | grep -v vscode | grep -v 'codex-code-mode-host' >/dev/null; then
    echo "$(date -Is) Codex already running; wait"
    sleep 60
    continue
  fi

  round=$((round + 1))
  echo "$(date -Is) launch Codex round=$round solved=${SOLVED_COUNT:-0} next=${NEXT_TASK:-} pending=${PENDING_TASKS:-}"

  {
    cat "$PROMPT_BASE"
    echo
    echo "## 本轮续跑状态（机器生成，以这个为准）"
    echo
    echo "这是监督进程第 ${round} 次拉起。上一轮会话结束了，你必须接着干，不要重开整场规划。"
    echo "已解决题数：${SOLVED_COUNT:-0}/10"
    echo "未解决：${PENDING_TASKS:-}"
    echo "下一题：${NEXT_TASK:-cancel-async-tasks}"
    echo
    cat "$LOGDIR/progress.txt" 2>/dev/null || true
    echo
    echo "立刻从下一题开始跑 causal/temporal。不要结束整场，除非 10 题 causal 都 tests_ok。"
  } >"$LOGDIR/stdin_prompt.md"

  : >"$LOGDIR/last_message.txt"
  : >"$LOGDIR/run.round${round}.log"

  "$CLASH" run -- env CODEX_AUTH_MODE=auto \
    "$CODEX" exec \
    -C "$ROOT" \
    -m gpt-5.6-luna \
    -c 'model_reasoning_effort="high"' \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    -o "$LOGDIR/last_message.txt" \
    - \
    <"$LOGDIR/stdin_prompt.md" \
    >"$LOGDIR/run.log" 2>&1
  rc=$?
  cp -f "$LOGDIR/run.log" "$LOGDIR/run.round${round}.log" 2>/dev/null || true
  echo "$(date -Is) Codex exited rc=$rc round=$round"
  sleep 15
done
