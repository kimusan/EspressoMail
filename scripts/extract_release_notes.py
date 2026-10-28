#!/usr/bin/env python3
"""Extract release notes from git tag annotation and CHANGELOG.md."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def extract_notes(tag: str, manual_notes: str = "", repo_root: Path | None = None) -> str:
    if manual_notes.strip():
        return manual_notes.strip()

    if repo_root is None:
        repo_root = Path.cwd()

    version = tag.lstrip("v") if tag else ""

    # Extract git tag annotation message if present
    tag_msg = ""
    if tag:
        try:
            res = subprocess.run(
                ["git", "tag", "-l", "--format=%(contents)", tag],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
            )
            tag_msg = res.stdout.strip()
        except Exception:
            pass

    # Extract matching section from CHANGELOG.md
    changelog_path = repo_root / "CHANGELOG.md"
    changelog_notes = ""
    if changelog_path.is_file() and version:
        content = changelog_path.read_text(encoding="utf-8")
        pattern = rf"##\s*\[{re.escape(version)}\][^\n]*\n(.*?)(?=\n##\s*\[|\Z)"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            changelog_notes = match.group(1).strip()

    body_parts: list[str] = []
    # If tag_msg has custom content (not just 'Release vX.Y.Z'), include it at top
    default_tag_msg = f"Release {tag}"
    is_commit_title = any(
        tag_msg.strip().startswith(prefix)
        for prefix in ("feat:", "feat(", "fix:", "fix(", "chore:", "chore(", "docs:", "docs(", "refactor:", "refactor(")
    )
    if tag_msg and tag_msg != default_tag_msg and tag_msg not in changelog_notes and not is_commit_title:
        first_line = tag_msg.split("\n")[0].strip()
        if first_line not in changelog_notes:
            body_parts.append(f"### {tag_msg}\n")

    if changelog_notes:
        body_parts.append(changelog_notes)
    elif tag_msg:
        body_parts.append(tag_msg)
    else:
        body_parts.append(
            f"### EspressoMail {tag}\n\nAutomated release with multi-platform binary executables, native Linux packages, and PyPI distribution."
        )

    return "\n\n".join(body_parts).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract release notes for GitHub Release")
    parser.add_argument("--tag", default="", help="Git tag (e.g. v0.1.0)")
    parser.add_argument("--output", default="RELEASE_NOTES.md", help="Output file path")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    args = parser.parse_args()

    tag = args.tag or os.environ.get("GITHUB_REF_NAME", "")
    manual_notes = os.environ.get("RELEASE_NOTES_INPUT", "")

    notes = extract_notes(tag=tag, manual_notes=manual_notes, repo_root=Path(args.repo_root).resolve())

    out_path = Path(args.output).resolve()
    out_path.write_text(notes + "\n", encoding="utf-8")

    print(f"Extracted release notes written to {out_path} ({len(notes)} chars):")
    print("=" * 60)
    print(notes)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
