"""Give each try namespace private POSIX shared memory, including semaphores."""
from pathlib import Path
import os
import sys
import tempfile


MARKER = "AGENTTX_PRIVATE_SHM"


def patch_source(source: str) -> str:
    if MARKER in source:
        return source
    setup = '    mkdir -p "$sandbox_dir/temproot/dev" "$sandbox_dir/temproot/dev/pts"'
    cleanup = 'unmount_devices() {\n    sandbox_dir="$1"'
    if source.count(setup) != 1 or source.count(cleanup) != 1:
        raise ValueError("try device mount layout changed; inspect source before patching")
    source = source.replace(setup, setup + '''
    # AGENTTX_PRIVATE_SHM: never expose the caller's IPC files to task commands.
    mkdir -p "$sandbox_dir/temproot/dev/shm"
    mount -t tmpfs -o nosuid,nodev,noexec,mode=1777,size=64m tmpfs "$sandbox_dir/temproot/dev/shm" || exit 1''', 1)
    return source.replace(cleanup, cleanup + '''
    umount "$sandbox_dir/temproot/dev/shm" 2>>"$try_mount_log"
    rmdir "$sandbox_dir/temproot/dev/shm" 2>>"$try_remove_log"''', 1)


def main() -> None:
    path = Path(sys.argv[1])
    before = path.read_text()
    after = patch_source(before)
    if after == before:
        return
    # Replace the local file without changing any snapshot that hardlinks it.
    fd, name = tempfile.mkstemp(prefix=path.name + ".shm-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(after)
        os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
