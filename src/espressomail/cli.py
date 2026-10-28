"""Command-line interface entrypoint for EspressoMail."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from espresso import Program

from espressomail import __version__
from espressomail.logging_config import setup_logging
from espressomail.storage.database import LocalStore
from espressomail.themes.engine import THEME_ORDER
from espressomail.ui.app import EspressoMailApp

logger = logging.getLogger("espressomail.cli")


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for `espressomail`."""
    parser = argparse.ArgumentParser(
        prog="espressomail",
        description="EspressoMail: A full-featured terminal email client built on the Espresso TUI framework.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"espressomail {__version__}",
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable verbose debug logging to log file",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to the log file (default: ~/.local/share/espressomail/espressomail.log)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to the local SQLite email database file (default: ~/.local/share/espressomail/espressomail.db)",
    )
    parser.add_argument(
        "--no-splash",
        action="store_true",
        help="Skip the animated startup splash screen",
    )
    parser.add_argument(
        "--theme",
        type=str,
        choices=THEME_ORDER,
        default="",
        help="Initial color theme (espresso, catppuccin, dracula, nord, gruvbox, solarized_light)",
    )
    parser.add_argument(
        "--image-backend",
        type=str,
        choices=["auto", "chafa", "sixel", "kitty", "half_block", "braille", "off"],
        default="",
        help="Terminal image rendering backend for inline image attachments",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the EspressoMail terminal client."""
    parser = build_parser()
    args = parser.parse_args(argv)

    log_path = setup_logging(debug=args.debug, log_file=args.log_file)
    logger.info("Starting EspressoMail %s (debug=%s, log_file=%s)", __version__, args.debug, log_path)

    store = LocalStore(db_path=args.db)
    if args.image_backend:
        store.set_setting("image_backend", args.image_backend)

    app = EspressoMailApp(
        store=store,
        show_splash=not args.no_splash,
        initial_theme=args.theme,
    )
    Program(app, alt_screen=True, mouse=True).run()
    store.close()
    logger.info("EspressoMail exited cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
