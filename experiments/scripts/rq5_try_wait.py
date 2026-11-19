#!/usr/bin/env python3
"""Keep a native try view open while the benchmark accesses its filesystem."""
import sys

print('RQ5_TRY_READY', flush=True)
if sys.stdin.readline() != 'exit\n':
    raise SystemExit('Missing normal shutdown request')
