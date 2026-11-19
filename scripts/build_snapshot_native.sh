#!/usr/bin/env bash
set -euo pipefail
agenttx_snapshot_root="$(cd "$(dirname "$0")/.." && pwd)"
"${CC:-cc}" -O3 -Wall -Wextra -Werror -fPIC -shared -pthread \
  "$agenttx_snapshot_root/src/agenttx/snapshot_native.c" \
  -o "$agenttx_snapshot_root/src/agenttx/_snapshot_native.so"
