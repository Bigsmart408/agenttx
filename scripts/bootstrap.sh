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

# AGENTTX_RECURSIVE_OVERLAY: Ubuntu 5.4 kernels (with the locked-children
# SAUCE patch in clone_private_mount) reject overlay lowerdirs whose subtree
# contains MNT_LOCKED child mounts (e.g. docker/snap/workspace mounts).  try
# therefore could not overlay /usr, /var, /home, etc., leaving the sandbox
# without bash.  This patch makes try fall back to overlaying each mount-free
# subtree and each child mount root individually.
if [ -f "$TRY_SCRIPT" ] && ! grep -Eq 'AGENTTX_RECURSIVE_OVERLAY' "$TRY_SCRIPT"; then
  python3 - "$TRY_SCRIPT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

old_fn_end = (
    '    mount -t overlay overlay -o "$overlay_spec" "$sandbox_dir/temproot/$overlay_mountpoint"\n'
    '}\n'
    '\n'
    'mountable_without_mergerfs() {'
)
new_fn = (
    '    mount -t overlay overlay -o "$overlay_spec" "$sandbox_dir/temproot/$overlay_mountpoint"\n'
    '}\n'
    '\n'
    '## AGENTTX_RECURSIVE_OVERLAY: Ubuntu 5.4 kernels (with the locked-children\n'
    '## SAUCE patch) reject overlay lowerdirs whose subtree contains MNT_LOCKED\n'
    '## child mounts (e.g. docker/snap/workspace mounts).  Overlay each mount-free\n'
    '## subtree and each child mount root individually so the sandbox stays usable.\n'
    'recursive_overlay() {\n'
    '    target="$1"\n'
    '    mkdir -p "$SANDBOX_DIR/upperdir$target" "$SANDBOX_DIR/workdir$target" "$SANDBOX_DIR/temproot$target" 2>>"$try_mount_log"\n'
    '    if make_overlay "$SANDBOX_DIR" "$target" "$target" 2>>"$try_mount_log"; then\n'
    '        return 0\n'
    '    fi\n'
    '    for child in "$target"/* "$target"/.[!.]*; do\n'
    '        [ -e "$child" ] || [ -L "$child" ] || continue\n'
    '        case "$child" in\n'
    '            /proc|/dev) continue;;\n'
    '        esac\n'
    '        if [ -d "$child" ] && ! [ -L "$child" ]; then\n'
    '            recursive_overlay "$child"\n'
    '        elif [ -L "$child" ]; then\n'
    '            mkdir -p "$SANDBOX_DIR/temproot$(dirname "$child")" 2>>"$try_mount_log"\n'
    '            ln -s "$(readlink "$child")" "$SANDBOX_DIR/temproot$child" 2>>"$try_mount_log" || true\n'
    '            echo "$child" >> "$SANDBOX_DIR/.agenttx_cleanup" 2>/dev/null || true\n'
    '        else\n'
    '            mkdir -p "$SANDBOX_DIR/temproot$(dirname "$child")" 2>>"$try_mount_log"\n'
    '            cp -a "$child" "$SANDBOX_DIR/temproot$child" 2>>"$try_mount_log" || true\n'
    '            echo "$child" >> "$SANDBOX_DIR/.agenttx_cleanup" 2>/dev/null || true\n'
    '        fi\n'
    '    done\n'
    '    return 0\n'
    '}\n'
    '\n'
    'mountable_without_mergerfs() {'
)
if old_fn_end not in text:
    raise SystemExit("try recursive-overlay anchor (make_overlay tail) changed")
text = text.replace(old_fn_end, new_fn, 1)

old_warn = (
    '        if [ -z "$UNION_HELPER" ]\n'
    '        then\n'
    '            ## We can ignore this mountpoint, if the user program tries to use it, it will crash, but if not we can run normally\n'
    '            printf "%s: Warning: Failed mounting $mountpoint as an overlay and mergerfs or unionfs not set and could not be found, see \"$try_mount_log\"\n" "$TRY_COMMAND" >&2\n'
    '        else'
)
new_warn = (
    '        if [ -z "$UNION_HELPER" ]\n'
    '        then\n'
    '            ## AGENTTX_RECURSIVE_OVERLAY: try per-subtree overlays first; the\n'
    '            ## warning below is only emitted when the whole subtree is unusable.\n'
    '            if ! recursive_overlay "$pure_mountpoint" 2>>"$try_mount_log"; then\n'
    '                printf "%s: Warning: Failed mounting $mountpoint as an overlay and mergerfs or unionfs not set and could not be found, see \"$try_mount_log\"\n" "$TRY_COMMAND" >&2\n'
    '            fi\n'
    '        else'
)
if old_warn not in text:
    raise SystemExit("try recursive-overlay anchor (warning branch) changed")
text = text.replace(old_warn, new_warn, 1)

old_clean = (
    '    while IFS="" read -r mountpoint\n'
    '    do\n'
    '        pure_mountpoint=${mountpoint##*:}\n'
    '        if [  -L "$pure_mountpoint" ]\n'
    '        then\n'
    '            rm "${SANDBOX_DIR}/temproot/${mountpoint}" 2>>"$try_remove_log"\n'
    '        fi\n'
    '    done <"$DIRS_AND_MOUNTS"\n'
    '\n'
    '    emit_trace'
)
new_clean = (
    '    while IFS="" read -r mountpoint\n'
    '    do\n'
    '        pure_mountpoint=${mountpoint##*:}\n'
    '        if [  -L "$pure_mountpoint" ]\n'
    '        then\n'
    '            rm "${SANDBOX_DIR}/temproot/${mountpoint}" 2>>"$try_remove_log"\n'
    '        fi\n'
    '    done <"$DIRS_AND_MOUNTS"\n'
    '\n'
    '    # AGENTTX_RECURSIVE_OVERLAY: remove fallback-created nested symlinks/files\n'
    '    if [ -f "$SANDBOX_DIR/.agenttx_cleanup" ]\n'
    '    then\n'
    '        while IFS="" read -r path\n'
    '        do\n'
    '            rm -rf "${SANDBOX_DIR}/temproot${path}" 2>>"$try_remove_log" || true\n'
    '        done < "$SANDBOX_DIR/.agenttx_cleanup"\n'
    '        rm -f "$SANDBOX_DIR/.agenttx_cleanup"\n'
    '    fi\n'
    '\n'
    '    emit_trace'
)
if old_clean not in text:
    raise SystemExit("try recursive-overlay anchor (cleanup) changed")
text = text.replace(old_clean, new_clean, 1)

path.write_text(text, encoding="utf-8")
PY
  chmod +x "$TRY_SCRIPT"
fi
echo "AgentTX root: $ROOT"

if ! command -v strace >/dev/null 2>&1; then
  echo "warning: strace is required for default dependency tracing" >&2
fi
