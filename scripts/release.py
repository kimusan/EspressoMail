#!/usr/bin/env python3
"""EspressoMail automated release management script.

Performs complete preflight checks, runs test suite, builds package artifacts,
bumps the semantic version in code and packaging files, updates CHANGELOG.md,
creates a git release commit, tags the release, and pushes to GitHub.

Usage:
    python3 scripts/release.py [patch|minor|major|<version>] [options]

Examples:
    python3 scripts/release.py patch --dry-run
    python3 scripts/release.py minor
    python3 scripts/release.py 0.2.0 --message "Add unified all-unread folder and CardDAV sync"
    python3 scripts/release.py patch --no-push
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"
INIT_FILE = REPO_ROOT / "src" / "espressomail" / "__init__.py"
CHANGELOG_FILE = REPO_ROOT / "CHANGELOG.md"
PKGBUILD_FILE = REPO_ROOT / "packaging" / "arch" / "PKGBUILD"

SEMVER_REGEX = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def log(msg: str, prefix: str = "==>") -> None:
    print(f"\033[1;34m{prefix}\033[0m {msg}")


def log_success(msg: str) -> None:
    print(f"\033[1;32m==> SUCCESS:\033[0m {msg}")


def log_warn(msg: str) -> None:
    print(f"\033[1;33m==> WARNING:\033[0m {msg}")


def log_error(msg: str) -> None:
    print(f"\033[1;31m==> ERROR:\033[0m {msg}", file=sys.stderr)


def run_cmd(
    cmd: list[str],
    cwd: Path = REPO_ROOT,
    capture_output: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a system command."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            capture_output=capture_output,
            check=check,
            env=merged_env,
        )
    except subprocess.CalledProcessError as e:
        if capture_output:
            log_error(f"Command failed: {' '.join(cmd)}\n{e.stderr or e.stdout}")
        else:
            log_error(f"Command failed: {' '.join(cmd)}")
        raise


def get_current_version() -> str:
    """Read the current version string from pyproject.toml (single source of truth)."""
    if PYPROJECT_FILE.is_file():
        content = PYPROJECT_FILE.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if match:
            return match.group(1).lstrip("v")
    if INIT_FILE.is_file():
        content = INIT_FILE.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1).lstrip("v")
    raise ValueError(f"Could not find version in {PYPROJECT_FILE}")


def calculate_next_version(current: str, bump_or_version: str) -> str:
    """Calculate the next semver version string."""
    m = SEMVER_REGEX.match(current)
    if not m:
        raise ValueError(f"Current version '{current}' is not valid semantic versioning (X.Y.Z)")
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))

    choice = bump_or_version.lower().strip().lstrip("v")
    if choice == "patch":
        return f"{major}.{minor}.{patch + 1}"
    elif choice == "minor":
        return f"{major}.{minor + 1}.0"
    elif choice == "major":
        return f"{major + 1}.0.0"
    else:
        # User provided an explicit version string
        target_match = SEMVER_REGEX.match(choice)
        if not target_match:
            raise ValueError(
                f"Invalid target version or bump type '{bump_or_version}'. "
                f"Must be 'major', 'minor', 'patch', or 'X.Y.Z'"
            )
        target_tuple = (
            int(target_match.group(1)),
            int(target_match.group(2)),
            int(target_match.group(3)),
        )
        current_tuple = (major, minor, patch)
        if target_tuple <= current_tuple:
            log_warn(
                f"Target version ({choice}) is not greater than current version ({current})."
            )
        return choice


def check_git_status(allow_dirty: bool = False, allow_branch: bool = False) -> str:
    """Validate git repository state."""
    # 1. Check if git is available
    res = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, check=False)
    if res.returncode != 0:
        raise RuntimeError("Not inside a git repository.")

    # 2. Get current branch
    branch_res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True)
    current_branch = branch_res.stdout.strip()
    if current_branch != "main" and not allow_branch:
        log_warn(f"You are on branch '{current_branch}', not 'main'.")

    # 3. Check for uncommitted changes
    status_res = run_cmd(["git", "status", "--porcelain"], capture_output=True)
    dirty_files = [line for line in status_res.stdout.splitlines() if line.strip()]
    if dirty_files and not allow_dirty:
        log_error("Working directory has uncommitted changes:")
        for line in dirty_files[:10]:
            print(f"    {line}", file=sys.stderr)
        if len(dirty_files) > 10:
            print(f"    ... and {len(dirty_files) - 10} more", file=sys.stderr)
        raise RuntimeError("Cannot release with dirty working tree. Commit or stash changes first.")

    # 4. Check remote origin
    remote_res = run_cmd(["git", "remote"], capture_output=True)
    remotes = remote_res.stdout.split()
    if "origin" not in remotes:
        log_warn("No 'origin' remote found. Automatic push will not be possible.")

    return current_branch


def check_tag_exists(tag: str) -> bool:
    """Check if git tag already exists locally or on remote."""
    res = run_cmd(["git", "tag", "-l", tag], capture_output=True)
    return tag in res.stdout.split()


def get_python_exe() -> str:
    """Find a Python executable with PIL/Pillow and build dependencies installed."""
    candidates = ["/usr/bin/python3", sys.executable, "python3"]
    for cand in candidates:
        try:
            res = subprocess.run([cand, "-c", "import PIL"], capture_output=True)
            if res.returncode == 0:
                return cand
        except Exception:
            pass
    return sys.executable


def run_tests() -> None:
    """Run full test suite."""
    log("Running test suite...")
    # Find scratch espresso if not in environment
    extra_paths = [str(REPO_ROOT / "src")]
    scratch_espresso = Path("/home/kim/.gemini/antigravity/brain/72e763bc-c9c9-4823-82f6-6d58cd3e84ee/scratch/espresso/src")
    if scratch_espresso.is_dir():
        extra_paths.append(str(scratch_espresso))

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    if existing_pythonpath:
        extra_paths.append(existing_pythonpath)

    pythonpath_val = ":".join(extra_paths)
    env = {"PYTHONPATH": pythonpath_val}

    py_exe = get_python_exe()
    test_cmd = [py_exe, "-m", "unittest", "discover", "-s", "tests", "-v"]
    run_cmd(test_cmd, env=env)
    log_success("All tests passed.")


def run_build_check() -> None:
    """Test building wheel and sdist to ensure package configuration is valid."""
    log("Verifying package build (sdist & wheel)...")
    py_exe = get_python_exe()
    build_cmd = [py_exe, "-m", "build", "--no-isolation"]
    run_cmd(build_cmd)
    log_success("Package build verification succeeded.")


def update_version_files(new_version: str, dry_run: bool = False) -> list[Path]:
    """Update version strings in source and packaging files."""
    changed_files: list[Path] = []

    # 1. Update pyproject.toml (single source of truth)
    if PYPROJECT_FILE.is_file():
        pyproject_content = PYPROJECT_FILE.read_text(encoding="utf-8")
        new_pyproject_content = re.sub(
            r'^version\s*=\s*["\'][^"\']+["\']',
            f'version = "{new_version}"',
            pyproject_content,
            flags=re.MULTILINE,
        )
        if new_pyproject_content != pyproject_content:
            changed_files.append(PYPROJECT_FILE)
            if not dry_run:
                PYPROJECT_FILE.write_text(new_pyproject_content, encoding="utf-8")

    # 2. Update packaging/arch/PKGBUILD if exists
    if PKGBUILD_FILE.is_file():
        pkgbuild_content = PKGBUILD_FILE.read_text(encoding="utf-8")
        new_pkgbuild_content = re.sub(
            r'^pkgver=.*$',
            f'pkgver={new_version}',
            pkgbuild_content,
            flags=re.MULTILINE,
        )
        if new_pkgbuild_content != pkgbuild_content:
            changed_files.append(PKGBUILD_FILE)
            if not dry_run:
                PKGBUILD_FILE.write_text(new_pkgbuild_content, encoding="utf-8")

    return changed_files


def update_changelog(
    new_version: str,
    prev_version: str,
    message: str | None = None,
    notes: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Promote [Unreleased] section in CHANGELOG.md to new version section."""
    if not CHANGELOG_FILE.is_file():
        log_warn("CHANGELOG.md not found. Skipping changelog update.")
        return False

    today = datetime.date.today().isoformat()
    content = CHANGELOG_FILE.read_text(encoding="utf-8")

    unreleased_pattern = r"##\s*\[Unreleased\]\s*\n"
    if not re.search(unreleased_pattern, content):
        # Create unreleased section at top if missing
        insert_marker = "# Changelog\n\n"
        if insert_marker in content:
            content = content.replace(
                insert_marker,
                f"{insert_marker}## [Unreleased]\n\n",
                1,
            )

    # Check if there is already an entry for this version
    if f"## [{new_version}]" in content:
        blocks = []
        if message and message.strip() and message.strip() not in content:
            blocks.append(f"> {message.strip()}")
        if notes and notes.strip() and notes.strip() not in content:
            blocks.append(notes.strip())

        if blocks:
            header_pattern = rf"(##\s*\[{re.escape(new_version)}\][^\n]*\n)"
            msg_block = "\n" + "\n\n".join(blocks) + "\n\n"
            new_content = re.sub(header_pattern, rf"\1{msg_block}", content, count=1)
            if not dry_run:
                CHANGELOG_FILE.write_text(new_content, encoding="utf-8")
            return True
        log_warn(f"Section ## [{new_version}] already exists in CHANGELOG.md.")
        return False

    # Insert new version section under ## [Unreleased]
    blocks: list[str] = []
    if message and message.strip():
        blocks.append(f"> {message.strip()}")
    if notes and notes.strip():
        blocks.append(notes.strip())

    msg_block = ("\n\n" + "\n\n".join(blocks) + "\n") if blocks else "\n"
    replacement = (
        f"## [Unreleased]\n\n"
        f"## [{new_version}] - {today}\n"
        f"{msg_block}"
    )
    new_content = re.sub(unreleased_pattern, replacement, content, count=1)

    # Update bottom comparison links if present
    repo_url = "https://github.com/kimusan/EspressoMail"
    unreleased_link_pattern = r"\[Unreleased\]:\s*https://github\.com/[^\s]+/compare/v[^\s]+\.\.\.HEAD"
    new_unreleased_link = f"[Unreleased]: {repo_url}/compare/v{new_version}...HEAD"
    new_version_link = f"[{new_version}]: {repo_url}/compare/v{prev_version}...v{new_version}"

    if re.search(unreleased_link_pattern, new_content):
        new_content = re.sub(
            unreleased_link_pattern,
            f"{new_unreleased_link}\n{new_version_link}",
            new_content,
            count=1,
        )
    else:
        new_content += f"\n{new_unreleased_link}\n{new_version_link}\n"

    if not dry_run:
        CHANGELOG_FILE.write_text(new_content, encoding="utf-8")

    return True


def perform_release(
    target_version: str,
    bump_type: str,
    message: str | None = None,
    notes: str | None = None,
    dry_run: bool = False,
    no_push: bool = False,
    skip_tests: bool = False,
    skip_build: bool = False,
    allow_dirty: bool = False,
    allow_branch: bool = False,
) -> None:
    """Orchestrate the complete release process."""
    current_version = get_current_version()
    next_version = calculate_next_version(current_version, target_version)
    tag_name = f"v{next_version}"

    print("=" * 60)
    print("EspressoMail Release Automation")
    print("=" * 60)
    print(f" Current version:  {current_version}")
    print(f" Target version:   {next_version} ({tag_name})")
    print(f" Mode:             {'DRY RUN (no changes will be made)' if dry_run else 'LIVE RELEASE'}")
    print(f" Push to GitHub:   {'No (--no-push)' if no_push else 'Yes'}")
    if message:
        print(f" Summary (-m):     {message.strip()}")
    if notes:
        preview = notes.strip().splitlines()[0] if notes.strip() else ""
        print(f" Notes (--notes):  {preview[:60]}... ({len(notes)} chars)")
    print("=" * 60)

    # Pre-flight check: tag existence
    if check_tag_exists(tag_name):
        raise RuntimeError(f"Git tag '{tag_name}' already exists locally. Cannot release.")

    # Pre-flight check: git repo state
    current_branch = check_git_status(allow_dirty=allow_dirty or dry_run, allow_branch=allow_branch)

    # Verification: tests
    if not skip_tests:
        run_tests()
    else:
        log_warn("Skipping tests (--skip-tests).")

    # Verification: build check
    if not skip_build:
        run_build_check()
    else:
        log_warn("Skipping package build check (--skip-build).")

    # Update files
    log(f"Updating version strings to '{next_version}'...")
    changed_files = update_version_files(next_version, dry_run=dry_run)
    for f in changed_files:
        log(f"  Modified: {f.relative_to(REPO_ROOT)}")

    # Update Changelog
    log("Updating CHANGELOG.md...")
    changelog_updated = update_changelog(
        next_version,
        current_version,
        message=message,
        notes=notes,
        dry_run=dry_run,
    )
    if changelog_updated:
        changed_files.append(CHANGELOG_FILE)
        log("  Modified: CHANGELOG.md")

    rel_title = message.strip() if message and message.strip() else f"Release {tag_name}"
    tag_cmd = ["git", "tag", "-a", tag_name, "-m", rel_title]
    if notes and notes.strip():
        tag_cmd.extend(["-m", notes.strip()])

    if dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN SUMMARY: Planned actions")
        print("=" * 60)
        print(f"1. Stage files:")
        for cf in changed_files:
            print(f"     git add {cf.relative_to(REPO_ROOT)}")
        print(f"2. Commit:")
        print(f"     git commit -m 'chore(release): {tag_name}'")
        print(f"3. Tag:")
        if notes and notes.strip():
            print(f"     git tag -a {tag_name} -m '{rel_title}' -m '<full release notes>'")
        else:
            print(f"     git tag -a {tag_name} -m '{rel_title}'")
        if not no_push:
            print(f"4. Push:")
            print(f"     git push origin {current_branch}")
            print(f"     git push origin {tag_name}")
        print("=" * 60)
        log_success("Dry run finished successfully. Re-run without --dry-run to execute.")
        return

    # Live execution: stage files
    if changed_files:
        stage_paths = [str(cf.relative_to(REPO_ROOT)) for cf in changed_files]
        log(f"Staging changed files: {' '.join(stage_paths)}")
        run_cmd(["git", "add"] + stage_paths)

        # Live execution: commit
        commit_msg = f"chore(release): {tag_name}"
        log(f"Creating release commit: '{commit_msg}'")
        run_cmd(["git", "commit", "-m", commit_msg])
    else:
        log("No modified files to commit; working tree is up to date.")

    # Live execution: tag
    log(f"Creating annotated tag: '{tag_name}'")
    run_cmd(tag_cmd)

    # Live execution: push
    if not no_push:
        log(f"Pushing commit to origin/{current_branch}...")
        run_cmd(["git", "push", "origin", current_branch])
        log(f"Pushing tag {tag_name} to origin...")
        run_cmd(["git", "push", "origin", tag_name])
        log_success(f"Pushed commit and tag '{tag_name}' to GitHub!")
    else:
        log_warn(f"Skipping push (--no-push). Run manually:")
        print(f"    git push origin {current_branch} && git push origin {tag_name}")

    print("\n" + "=" * 60)
    print("RELEASE PUBLICATION TRIGGERED")
    print("=" * 60)
    print(f"Tag:                {tag_name}")
    print(f"GitHub Actions:     https://github.com/kimusan/EspressoMail/actions")
    print(f"GitHub Releases:    https://github.com/kimusan/EspressoMail/releases/tag/{tag_name}")
    print("=" * 60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Automated release manager for EspressoMail.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/release.py patch --dry-run
  python3 scripts/release.py minor -m "Add CardDAV sync"
  python3 scripts/release.py major --notes "### Major Changes\n- New sync engine"
  python3 scripts/release.py 0.2.0 -m "Release v0.2.0" --notes-file release_notes.md
  cat release_notes.md | python3 scripts/release.py patch --notes-file -
        """,
    )
    parser.add_argument(
        "bump",
        nargs="?",
        default="patch",
        help="Version bump type ('patch', 'minor', 'major') or explicit target version (e.g. '0.2.0'). Default: 'patch'",
    )
    parser.add_argument(
        "-m",
        "--message",
        type=str,
        default="",
        help="Release title / short summary for git tag, CHANGELOG.md, and GitHub release post",
    )
    parser.add_argument(
        "-n",
        "--notes",
        type=str,
        default="",
        help="Full release notes in Markdown for the GitHub release post and CHANGELOG.md",
    )
    parser.add_argument(
        "--notes-file",
        type=str,
        default="",
        help="Path to file containing full release notes in Markdown (use '-' for stdin)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the release without making changes or pushing",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit and tag locally, but do not push to remote repository",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip running the test suite",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip running the build verification check",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow running release even if working tree has unstaged/uncommitted files",
    )
    parser.add_argument(
        "--allow-branch",
        action="store_true",
        help="Allow releasing from a branch other than 'main'",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.notes and args.notes_file:
        log_error("Cannot specify both --notes and --notes-file.")
        return 1

    notes_content = args.notes.strip() if args.notes else ""
    if args.notes_file:
        if args.notes_file == "-":
            log("Reading release notes from standard input (stdin)...")
            notes_content = sys.stdin.read().strip()
        else:
            notes_path = Path(args.notes_file).resolve()
            if not notes_path.is_file():
                log_error(f"Release notes file not found: {notes_path}")
                return 1
            notes_content = notes_path.read_text(encoding="utf-8").strip()

    try:
        perform_release(
            target_version=args.bump,
            bump_type=args.bump,
            message=args.message,
            notes=notes_content,
            dry_run=args.dry_run,
            no_push=args.no_push,
            skip_tests=args.skip_tests,
            skip_build=args.skip_build,
            allow_dirty=args.allow_dirty,
            allow_branch=args.allow_branch,
        )
        return 0
    except Exception as e:
        log_error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
