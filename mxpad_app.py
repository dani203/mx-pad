#!/usr/bin/env python3
"""Launch the MX Pad GNOME app."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mxpad.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
