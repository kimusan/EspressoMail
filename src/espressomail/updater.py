"""GitHub release update checker for EspressoMail."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Sequence

from espressomail import __version__

logger = logging.getLogger(__name__)

GITHUB_REPO: str = "kimusan/EspressoMail"
GITHUB_RELEASES_API: str = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a semantic version string into a comparable tuple of integers."""
    cleaned = version_str.strip().lstrip("vV")
    # Extract leading numbers separated by dots
    match = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
    if not match:
        return (0,)
    parts = match.group(1).split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return (0,)


def is_newer_version(latest_str: str, current_str: str) -> bool:
    """Return True if latest_str is strictly newer than current_str."""
    v_latest = parse_version(latest_str)
    v_current = parse_version(current_str)
    # Pad to equal length for comparison (e.g. (0, 2) vs (0, 2, 0))
    max_len = max(len(v_latest), len(v_current))
    padded_latest = v_latest + (0,) * (max_len - len(v_latest))
    padded_current = v_current + (0,) * (max_len - len(v_current))
    return padded_latest > padded_current


@dataclass(frozen=True)
class ReleaseInfo:
    """Information about a GitHub release."""

    tag_name: str
    version: str
    name: str
    html_url: str
    published_at: str = ""
    body: str = ""
    is_newer: bool = False


def check_github_release(
    repo: str = GITHUB_REPO,
    current_version: str = __version__,
    timeout: float = 4.0,
) -> ReleaseInfo | None:
    """Query GitHub API for the latest release and determine if an update is available.

    Returns a ReleaseInfo instance or None if offline/error.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"EspressoMail/{current_version}",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_data = resp.read().decode("utf-8")
            data = json.loads(raw_data)
            tag_name = str(data.get("tag_name", "")).strip()
            version = tag_name.lstrip("vV")
            name = str(data.get("name") or tag_name or "New Release")
            html_url = str(data.get("html_url") or f"https://github.com/{repo}/releases")
            published_at = str(data.get("published_at") or "")
            body = str(data.get("body") or "")

            newer = is_newer_version(version, current_version)
            logger.info(
                "GitHub release check: latest=%s (tag=%s), current=%s, is_newer=%s",
                version,
                tag_name,
                current_version,
                newer,
            )
            return ReleaseInfo(
                tag_name=tag_name,
                version=version,
                name=name,
                html_url=html_url,
                published_at=published_at,
                body=body,
                is_newer=newer,
            )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.debug("Could not check GitHub releases: %s", exc)
        return None
    except Exception as exc:
        logger.debug("Unexpected error checking GitHub releases: %s", exc)
        return None
