"""Keep device aliases out of the sandbox's persistent backing directories."""


def patch_source(source: str) -> str:
    marker = '# AGENTTX_PRIVATE_DEV_ROOT'
    if marker in source:
        return source
    start = '    mkdir -p "$sandbox_dir/temproot/dev" "$sandbox_dir/temproot/dev/pts"\n'
    replacement = '''    # AGENTTX_PRIVATE_DEV_ROOT
    # A filesystem walk can see the backing session through /results. Device
    # symlinks there would resolve against the live /dev and block on ptmx.
    # Keep all live device entries on a namespace-local mount instead.
    mkdir -p "$sandbox_dir/temproot/dev"
    mount -t tmpfs -o nosuid,mode=755,size=64m tmpfs "$sandbox_dir/temproot/dev" || exit 1
    mkdir -p "$sandbox_dir/temproot/dev/pts"
'''
    if source.count(start) != 1:
        raise ValueError('try device mount layout changed; inspect source')
    source = source.replace(start, replacement, 1)
    end = '''        rm -f "$sandbox_dir/temproot/dev/$dev" 2>>"$try_remove_log"
    done
}'''
    if source.count(end) != 1:
        raise ValueError('try device cleanup layout changed; inspect source')
    return source.replace(end, end[:-1] + '''    umount "$sandbox_dir/temproot/dev/pts" 2>>"$try_mount_log" || true
    umount "$sandbox_dir/temproot/dev" 2>>"$try_mount_log"
}''', 1)
