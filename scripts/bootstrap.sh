#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p third_party
if [ ! -d third_party/try/.git ]; then
  git clone --depth 1 https://github.com/binpash/try.git third_party/try || true
fi
echo "AgentTX root: $ROOT"

if ! command -v strace >/dev/null 2>&1; then
  echo "warning: strace is required for default dependency tracing" >&2
fi
