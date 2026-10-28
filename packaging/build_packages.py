#!/usr/bin/env python3
"""Unified package builder for Debian (.deb), RPM (.rpm), and Arch Linux (.pkg.tar.zst)."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Linux packages for EspressoMail")
    parser.add_argument(
        "--version",
        default="",
        help="Package version (defaults to version in src/espressomail/__init__.py)",
    )
    parser.add_argument(
        "--binary",
        default="dist/espressomail",
        help="Path to compiled standalone binary (default: dist/espressomail)",
    )
    parser.add_argument(
        "--out-dir",
        default="dist/packages",
        help="Directory to place built packages (default: dist/packages)",
    )
    parser.add_argument(
        "--formats",
        default="deb,rpm,arch",
        help="Comma-separated package formats to build (deb, rpm, arch)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    version = args.version
    if not version:
        init_file = repo_root / "src" / "espressomail" / "__init__.py"
        for line in init_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                version = line.split("=")[1].strip(" '\"")
                break
    if not version:
        version = "0.1.0"
    version = version.lstrip("v")

    binary_path = (repo_root / args.binary).resolve()
    out_dir = (repo_root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not binary_path.is_file():
        print(f"Error: binary file not found at {binary_path}", file=sys.stderr)
        return 1

    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    packaging_dir = repo_root / "packaging"

    success = True
    for fmt in formats:
        print(f"\n==========================================")
        print(f"Building {fmt.upper()} package for version {version}...")
        print(f"==========================================")
        if fmt == "deb":
            script = packaging_dir / "deb" / "build_deb.sh"
            cmd = ["bash", str(script), version, str(binary_path), str(out_dir)]
        elif fmt == "rpm":
            script = packaging_dir / "rpm" / "build_rpm.sh"
            cmd = ["bash", str(script), version, str(binary_path), str(out_dir)]
        elif fmt in ("arch", "pacman"):
            script = packaging_dir / "arch" / "build_arch.sh"
            cmd = ["bash", str(script), version, str(binary_path), str(out_dir)]
        else:
            print(f"Unknown format: {fmt}", file=sys.stderr)
            success = False
            continue

        res = subprocess.run(cmd, cwd=str(repo_root))
        if res.returncode != 0:
            print(f"Failed to build {fmt} package (exit code {res.returncode})", file=sys.stderr)
            success = False
        else:
            print(f"Successfully built {fmt} package.")

    print("\nPackage generation complete. Contents of output directory:")
    for f in sorted(out_dir.glob("*")):
        print(f"  - {f.name} ({f.stat().st_size:,} bytes)")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
