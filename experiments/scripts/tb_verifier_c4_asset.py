"""Reuse the pinned C4 shard only after verifying its full content hash."""
CONTRACT = 'c4-pinned-readonly-asset-model-and-final/v3'
SHA256 = '807a548efbb10153c9eff0df5733a97a1b51ab1743242530de1b02a8ea17ace7'
ASSET = '/results/cache/verifier-assets/c4-heldout-00009.json.gz'
PINNED_ASSET = '/opt/agenttx-verifier-assets/c4-heldout-00009.json.gz'


def patch_source(source):
    lines = source.splitlines(keepends=True)
    candidates = [i for i,line in enumerate(lines)
                  if line.startswith('curl ') and '--continue-at -' in line and ASSET in line]
    if len(candidates) != 1:
        raise ValueError('C4 pinned asset download layout changed')
    check = f'echo "{SHA256}  {ASSET}" | sha256sum -c -'
    if check not in source:
        raise ValueError('C4 pinned asset checksum missing or changed')
    index = candidates[0]
    # The mutable task cache may be partial or contain different bytes. Never
    # resume it: restore the exact pinned prerequisite from a read-only mount.
    lines[index] = (f'if ! ( {check} ) >/dev/null 2>&1; then\n'
                    + f'  echo "{SHA256}  {PINNED_ASSET}" | sha256sum -c -\n'
                    + f'  cp --reflink=auto -- "{PINNED_ASSET}" "{ASSET}"\nfi\n')
    return ''.join(lines)
