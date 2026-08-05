#!/usr/bin/env bash
# Prefer built try; fall back to upstream script name variants.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRY_DIR="$ROOT/third_party/try"
if [ -x "$TRY_DIR/try" ]; then
  exec "$TRY_DIR/try" "$@"
fi
# some checkouts keep the script at repo root after configure/make
if [ -x "$TRY_DIR/scripts/try" ]; then
  exec "$TRY_DIR/scripts/try" "$@"
fi
echo "try binary not found under $TRY_DIR" >&2
exit 127
