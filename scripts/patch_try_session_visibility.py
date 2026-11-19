"""Expose execution scripts, but hide coordinator storage from task commands."""


def patch_source(source: str) -> str:
    marker = '# AGENTTX_PRIVATE_SESSION_VIEW'
    if marker in source:
        return source
    anchor = '## Check if chroot_executable exists, #29\n'
    if source.count(anchor) != 1:
        raise ValueError('try root transition layout changed')
    setup = '''# AGENTTX_PRIVATE_SESSION_VIEW
# Overlay backing files and tracer output are coordinator state. Exposing
# their aliases makes legitimate task writes appear as unrelated writes.
session_view="$SANDBOX_DIR/temproot$SANDBOX_DIR"
mkdir -p "$session_view"
mount -t tmpfs -o nosuid,nodev,mode=755,size=1m tmpfs "$session_view" || exit 1
for runner_script in "$chroot_executable" "$script_to_execute"; do
    runner_target="$SANDBOX_DIR/temproot$runner_script"
    touch "$runner_target" || exit 1
    mount --bind "$runner_script" "$runner_target" || exit 1
    mount -o remount,bind,ro "$runner_target" || exit 1
done

'''
    source = source.replace(anchor, setup + anchor, 1)
    anchor = '# unmount the devices\n'
    if source.count(anchor) != 1:
        raise ValueError('try root cleanup layout changed')
    cleanup = '''for runner_script in "$chroot_executable" "$script_to_execute"; do
    umount "$SANDBOX_DIR/temproot$runner_script" 2>>"$try_mount_log" || true
done
umount "$session_view" 2>>"$try_mount_log" || true

'''
    return source.replace(anchor, cleanup + anchor, 1)
