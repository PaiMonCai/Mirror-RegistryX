#!/usr/bin/env python3
"""Host-side wrapper for the panel terminal password reset module."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from panel.password_reset import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
