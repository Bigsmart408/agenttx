#!/usr/bin/env bash
set -euo pipefail

# The task image stays unchanged; these tools live only in this disposable run.
cp /host-tools/strace /usr/bin/strace
cp /host-tools/git /usr/bin/git
if [ "${AGENTTX_AGENT:-sweagent}" = codex ]; then
  cp /host-tools/codex /usr/local/bin/codex
  cp /host-tools/codex-code-mode-host /usr/local/bin/codex-code-mode-host
fi
cp /host-libs/libunwind-ptrace.so.0 /usr/lib/x86_64-linux-gnu/libunwind-ptrace.so.0
cp /host-libs/libunwind-x86_64.so.8 /usr/lib/x86_64-linux-gnu/libunwind-x86_64.so.8
cp /host-libs/libunwind.so.8 /usr/lib/x86_64-linux-gnu/libunwind.so.8

set +e
if [ "${AGENTTX_AGENT:-sweagent}" = codex ]; then
  capture_script=capture_clean_codex_in_image.py
else
  capture_script=capture_clean_native_in_image.py
fi
if [ "${AGENTTX_VERIFY_TB_NATIVE:-0}" = 1 ]; then
  timeout "${AGENTTX_AGENT_TIMEOUT_S}" \
    /home/bfq/miniconda3/envs/agenttx/bin/python3.11 \
    "/home/bfq/agenttx/experiments/scripts/$capture_script" "$@"
else
  /home/bfq/miniconda3/envs/agenttx/bin/python3.11 \
    "/home/bfq/agenttx/experiments/scripts/$capture_script" "$@"
fi
status=$?
set -e
if [ "$status" -eq 0 ] && [ "${AGENTTX_VERIFY_TB_NATIVE:-0}" = 1 ]; then
  pending="/results/_pending/${AGENTTX_RECORD_STEM}"
  mkdir -p /tests
  if [ -d "${AGENTTX_TB_TESTS_SOURCE}" ]; then
    cp -a "${AGENTTX_TB_TESTS_SOURCE}/." /tests/
  fi
  set +e
  timeout 3600 bash /tmp/agenttx-run-tests.sh >"$pending/verifier.stdout.log" 2>"$pending/verifier.stderr.log"
  printf '%s\n' "$?" >"$pending/verifier.rc"
  set -e
fi
chown -R "${AGENTTX_HOST_UID}:${AGENTTX_HOST_GID}" "$AGENTTX_WORKSPACE" \
  "/results/_pending/${AGENTTX_RECORD_STEM:-${AGENTTX_SUITE}__${AGENTTX_TASK_ID}}"
exit "$status"
