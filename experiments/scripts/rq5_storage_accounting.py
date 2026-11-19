"""Count allocated data once across reflink versions and hard-link aliases."""
import fcntl
import os
from pathlib import Path
import stat
import struct


def physical_extents(path):
    """Return allocated FIEMAP ranges, resolving delayed allocation first."""
    header, extent = struct.Struct('=QQIIII'), struct.Struct('=QQQQQIIII')
    capacity, start = 128, 0
    result = []
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        while True:
            buffer = bytearray(header.size+capacity*extent.size)
            header.pack_into(buffer, 0, start, (1 << 64)-1-start, 1, 0, capacity, 0)
            fcntl.ioctl(fd, 0xC020660B, buffer)
            count = header.unpack_from(buffer)[3]
            if not count:
                break
            for index in range(count):
                fields = extent.unpack_from(buffer, header.size+index*extent.size)
                logical, physical, length, flags = fields[0], fields[1], fields[2], fields[5]
                if flags & (0x2 | 0x4):
                    raise RuntimeError(f'unresolved physical allocation: {path}')
                if not flags & 0x200:  # Inline data resides in inode metadata.
                    result.append((physical, physical+length))
            next_start = logical+length
            if flags & 1:
                break
            if next_start <= start:
                raise RuntimeError(f'FIEMAP made no progress: {path}')
            start = next_start
    finally:
        os.close(fd)
    return result


def union_bytes(ranges):
    total, end = 0, -1
    for begin, stop in sorted(ranges):
        total += max(0, stop-max(begin, end))
        end = max(end, stop)
    return total


def backing_usage(root):
    """Data/directory allocation; exclude mounted views and duplicate extents.

    FIEMAP_SYNC resolves buffered allocation. This observer is used only in
    the resource experiment, separately from uninstrumented latency runs.
    """
    root = Path(root)
    device = root.stat().st_dev
    seen, stack, extents = set(), [root], []
    other_blocks = 0
    result = dict(regular_file_bytes=0, regular_files=0, directories=0,
                  skipped_mounts=0, inode_block_bytes=0)
    while stack:
        path = stack.pop()
        info = path.lstat()
        if info.st_dev != device:
            result['skipped_mounts'] += 1
            continue
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        if stat.S_ISREG(info.st_mode):
            ranges = physical_extents(path)
            extents.extend(ranges)
            # Attribute blocks not mapped by the data-fork FIEMAP remain
            # separately allocated to this inode.
            info = path.lstat()
            other_blocks += max(0, info.st_blocks*512-sum(b-a for a, b in ranges))
            result['regular_files'] += 1
            result['regular_file_bytes'] += info.st_size
        else:
            other_blocks += info.st_blocks*512
            if stat.S_ISDIR(info.st_mode):
                result['directories'] += 1
                with os.scandir(path) as entries:
                    stack.extend(Path(entry.path) for entry in entries)
        result['inode_block_bytes'] += info.st_blocks*512
    referenced = sum(b-a for a, b in extents)
    unique = union_bytes(extents)
    result.update(allocated_bytes=unique+other_blocks, data_unique_bytes=unique,
                  data_reference_bytes=referenced, duplicate_extent_bytes=referenced-unique,
                  unique_inodes=len(seen), storage_method='FIEMAP physical union + non-data blocks')
    return result
