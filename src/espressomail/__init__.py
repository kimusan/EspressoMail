"""EspressoMail: A full-featured terminal email client built on Espresso TUI.

Supports multi-account IMAP/SMTP, Unified Inbox, offline SQLite storage with
fuzzy search, message threading, Sieve filter management, GPG/PGP security,
terminal image rendering (chafa/Sixel/Kitty/Half-block), and customizable themes.
"""

import re
from pathlib import Path


def _get_version() -> str:
    """Read version number from pyproject.toml or package metadata."""
    # 1. Check pyproject.toml in source repository
    try:
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        if pyproject.is_file():
            content = pyproject.read_text(encoding="utf-8")
            match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                return match.group(1).strip()
    except Exception:
        pass

    # 2. Check installed package metadata
    try:
        from importlib.metadata import version
        v = version("espressomail")
        if v:
            return v.strip()
    except Exception:
        pass

    return "0.2.0"


__version__ = _get_version()
__author__ = "Kim Schulz"
__email__ = "kim@schulz.dk"

__all__ = ["__version__", "__author__", "__email__"]
