#!/usr/bin/env python3
"""Convenience launcher: run PageWright from the repo root.

    python PageWright.py [photo-or-project]

The real application lives in the src/ layout (src/pagewright/), which
is kept for packaging hygiene (pip install -e ., console scripts, tests
importing the same package the user runs). This shim just puts src/ on
sys.path and hands off to pagewright.main.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "src"))

from pagewright.main import main   # noqa: E402  (path setup must run first)

if __name__ == "__main__":
    sys.exit(main())
