"""Logging configuration and debug logging support for EspressoMail."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path


DEFAULT_LOG_DIR = Path.home() / ".local" / "share" / "espressomail"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "espressomail.log"

_LOG_CONFIGURED = False


def setup_logging(debug: bool = False, log_file: str | Path | None = None) -> Path:
    """Configure application logging to write into a persistent rotating log file.

    Parameters
    ----------
    debug : bool
        If True, set logging level to DEBUG. Otherwise, use INFO.
    log_file : str | Path | None
        Path to the target log file. Defaults to ~/.local/share/espressomail/espressomail.log.

    Returns
    -------
    Path
        The resolved absolute path of the active log file.
    """
    global _LOG_CONFIGURED
    target_path = Path(log_file).expanduser().resolve() if log_file else DEFAULT_LOG_FILE
    target_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    level = logging.DEBUG if debug else logging.INFO

    if not _LOG_CONFIGURED:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s (%(threadName)s): %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        try:
            handler = RotatingFileHandler(
                filename=str(target_path),
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(formatter)
            handler.setLevel(level)
            root_logger.addHandler(handler)
        except Exception:
            handler = logging.FileHandler(str(target_path), encoding="utf-8")
            handler.setFormatter(formatter)
            handler.setLevel(level)
            root_logger.addHandler(handler)

        _LOG_CONFIGURED = True

    root_logger.setLevel(level)
    for h in root_logger.handlers:
        h.setLevel(level)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return target_path
