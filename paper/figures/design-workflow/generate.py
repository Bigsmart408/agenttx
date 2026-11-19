#!/usr/bin/env python3
"""Regenerate both editable Lancet PPT diagrams and their paper exports."""
import sys
sys.dont_write_bytecode = True
from generate_diagrams import main

if __name__ == '__main__':
    main()
