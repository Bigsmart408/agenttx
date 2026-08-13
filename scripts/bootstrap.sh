#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p third_party
if [ ! -d third_party/try/.git ]; then
  git clone --depth 1 https://github.com/binpash/try.git third_party/try || true
fi

# Ubuntu 20.04 / kernel 5.4 Docker-overlay hosts reject try's default
# unprivileged `userxattr` mount option.  Root can mount the same OverlayFS
# without that option and does not need a user namespace.  Keep this small
# compatibility patch in the project bootstrap because third_party/try is
# intentionally gitignored and is recreated on a fresh checkout.
TRY_SCRIPT="third_party/try/try"
if [ -f "$TRY_SCRIPT" ] && ! grep -Eq 'AGENTTX_ROOT_OVERLAY_COMPAT|overlay_options=' "$TRY_SCRIPT"; then
  python3 - "$TRY_SCRIPT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old_mount = (
    '    mount -t overlay overlay -o userxattr -o '
    '"lowerdir=$lowerdirs,upperdir=$sandbox_dir/upperdir/$overlay_mountpoint,'
    'workdir=$sandbox_dir/workdir/$overlay_mountpoint,index=off" '
    '"$sandbox_dir/temproot/$overlay_mountpoint"'
)
new_mount = '''    # AGENTTX_ROOT_OVERLAY_COMPAT
    if [ "${TRY_OVERLAY_OPTIONS+x}" = x ]; then
        overlay_options="$TRY_OVERLAY_OPTIONS"
    elif [ "$(id -u)" -eq 0 ]; then
        overlay_options=""
    else
        overlay_options="userxattr"
    fi
    overlay_spec="lowerdir=$lowerdirs,upperdir=$sandbox_dir/upperdir/$overlay_mountpoint,workdir=$sandbox_dir/workdir/$overlay_mountpoint,index=off"
    if [ -n "$overlay_options" ]; then
        overlay_spec="$overlay_options,$overlay_spec"
    fi
    mount -t overlay overlay -o "$overlay_spec" "$sandbox_dir/temproot/$overlay_mountpoint"'''
if old_mount not in text:
    raise SystemExit("try source layout changed; inspect third_party/try/try before proceeding")
text = text.replace(old_mount, new_mount, 1)
old_unshare = '    unshare --mount --map-root-user --user --pid --fork $EXTRA_NS "$mount_and_execute"'
new_unshare = '''    if [ "$(id -u)" -eq 0 ] && [ "${TRY_FORCE_USER_NAMESPACE:-0}" != "1" ]; then
        unshare --mount --pid --fork $EXTRA_NS "$mount_and_execute"
    else
        unshare --mount --map-root-user --user --pid --fork $EXTRA_NS "$mount_and_execute"
    fi'''
if old_unshare not in text:
    raise SystemExit("try unshare layout changed; inspect third_party/try/try before proceeding")
text = text.replace(old_unshare, new_unshare, 1)
path.write_text(text, encoding="utf-8")
PY
  chmod +x "$TRY_SCRIPT"
fi
echo "AgentTX root: $ROOT"

if ! command -v strace >/dev/null 2>&1; then
  echo "warning: strace is required for default dependency tracing" >&2
fi
