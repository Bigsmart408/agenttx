"""Keep nested Ext4/XFS/Btrfs data mounts visible through try's overlays."""
import os
from pathlib import Path
import sys
import tempfile


def patch_source(source: str) -> str:
    marker = '# AGENTTX_NESTED_DATA_MOUNTS'
    if marker in source:
        return source
    inventory = '    sort -u -o "$DIRS_AND_MOUNTS" "$DIRS_AND_MOUNTS"\n'
    mount = '    overlay_mountpoint="$3"\n'
    if source.count(inventory) != 1 or source.count(mount) != 1:
        raise ValueError('try mount layout changed; inspect source')
    source = source.replace(inventory, inventory + '''
    # AGENTTX_NESTED_DATA_MOUNTS
    # Overlay lowerdir traversal does not cross into child mounts. Record
    # data mount boundaries before constructing any sandbox views.
    awk '$5 != "/" {
        for (i = 7; i <= NF; i++) {
            if ($i == "-") {
                if ($(i+1) ~ /^(ext4|xfs|btrfs)$/) print $5
                break
            }
        }
    }' /proc/self/mountinfo > "$SANDBOX_DIR/.agenttx-data-mounts"
''', 1)
    return source.replace(mount, mount + '''    # Let recursive_overlay split ancestors at real data mount boundaries.
    # Each child gets its own overlay; writes still enter the session upper.
    if [ -f "$sandbox_dir/.agenttx-data-mounts" ]; then
        while IFS= read -r agenttx_data_mount; do
            case "$agenttx_data_mount" in
                "$overlay_mountpoint"/*) return 1;;
            esac
        done < "$sandbox_dir/.agenttx-data-mounts"
    fi
''', 1)


if __name__ == '__main__':
    source, destination = map(Path, sys.argv[1:])
    output = patch_source(source.read_text())
    fd, temporary = tempfile.mkstemp(prefix=destination.name+'.mounts-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(output)
        os.chmod(temporary, source.stat().st_mode & 0o777)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
