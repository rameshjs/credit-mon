#!/usr/bin/env python3
"""Wrapper so systemd can run the package straight out of this checkout."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from credit_mon.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["refresh"]))
