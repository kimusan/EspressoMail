#!/usr/bin/env python3
"""PyInstaller entrypoint for EspressoMail."""
from __future__ import annotations

import sys
from espressomail.cli import main

if __name__ == "__main__":
    sys.exit(main())
