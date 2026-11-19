"""Opt-in shared /app and /tmp OverlayFS for native cross-directory rename."""
from pathlib import Path
import os
import sys
import tempfile


def patch_source(source: str) -> str:
    marker = '# AGENTTX_COMMON_APP_TMP_OVERLAY'
    if marker in source:
        return source
    start = '# actually mount the overlays\n'
    skip = '    case "$pure_mountpoint" in\n        (/|/dev|/proc) continue;;\n    esac\n'
    if source.count(start) != 1 or source.count(skip) != 1:
        raise ValueError('try mount layout changed; inspect source')
    source = source.replace(start, '''# AGENTTX_COMMON_APP_TMP_OVERLAY
# Keep infrastructure probes on the established mount layout. Real sessions
# may opt into one root overlay so /app -> /tmp is a native atomic rename.
common_app_tmp=0
case "$SANDBOX_DIR" in
    /tmp/agenttx-try-probe-*) ;;
    *)
        if [ "${TRY_COMMON_APP_TMP_OVERLAY:-0}" = 1 ]; then
            mkdir -p "$SANDBOX_DIR/workdir/.shared-root" "$SANDBOX_DIR/upperdir/tmp"
            # Persistent worker scripts live in coordinator-owned /tmp dirs.
            # The root lower tree cannot traverse Docker's nested /tmp mount.
            for command_dir in /tmp/agenttx-cmd-*; do
                [ -d "$command_dir" ] || continue
                cp -a "$command_dir" "$SANDBOX_DIR/upperdir/tmp/" || exit 1
            done
            mount -t overlay overlay -o "lowerdir=/,upperdir=$SANDBOX_DIR/upperdir,workdir=$SANDBOX_DIR/workdir/.shared-root,index=off" "$SANDBOX_DIR/temproot" || exit 1
            # Device links and mount targets belong to this namespace, never
            # to the tracked writable root or a rollback snapshot.
            mount -t tmpfs -o nosuid,mode=755,size=64m tmpfs "$SANDBOX_DIR/temproot/dev" || exit 1
            # Causal validation forks keep their coordinator scripts in the
            # outer /tmp tmpfs, which the shared root lower tree cannot see.
            case "$SANDBOX_DIR" in
                /tmp/agenttx-fork-*)
                    mkdir -p "$SANDBOX_DIR/temproot$SANDBOX_DIR"
                    mount --bind "$SANDBOX_DIR" "$SANDBOX_DIR/temproot$SANDBOX_DIR" || exit 1
                    ;;
            esac
            common_app_tmp=1
        fi
        ;;
esac

''' + start, 1)
    return source.replace(skip, skip + '''    if [ "$common_app_tmp" = 1 ]; then
        case "$pure_mountpoint" in /app|/tmp) continue;; esac
        # Session storage contains this namespace's root mount. Overlaying
        # its parent recursively would descend into our own root forever.
        if [ "$pure_mountpoint" = /results ]; then
            mkdir -p "$SANDBOX_DIR/temproot/results"
            mount --bind /results "$SANDBOX_DIR/temproot/results" || exit 1
            continue
        fi
    fi
''', 1)


if __name__ == '__main__':
    source, destination = map(Path, sys.argv[1:])
    output = patch_source(source.read_text())
    fd, temporary = tempfile.mkstemp(prefix=destination.name + '.shared-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(output)
        os.chmod(temporary, source.stat().st_mode & 0o777)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
