# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for EspressoMail."""
from __future__ import annotations

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Hidden imports needed for runtime TEA, protocols, crypto, image rendering, and storage
hidden_imports = [
    "espresso",
    "espressoTUI",
    "espressomail",
    "PIL",
    "PIL.Image",
    "sqlite3",
    "email",
    "email.mime",
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.application",
    "email.mime.image",
    "email.header",
    "email.utils",
    "urllib.request",
    "xml.etree.ElementTree",
    "shutil",
    "subprocess",
    "tempfile",
    "uuid",
    "webbrowser",
]

# Safely collect submodules
for pkg in ["espresso", "espressoTUI", "espressomail"]:
    try:
        hidden_imports.extend(collect_submodules(pkg))
    except Exception:
        pass

# Deduplicate
hidden_imports = sorted(list(set(hidden_imports)))

# Collect data files
datas = []
for pkg in ["espresso", "espressoTUI", "espressomail"]:
    try:
        datas.extend(collect_data_files(pkg))
    except Exception:
        pass

# Root of repo is two directories up from this spec file
spec_dir = os.path.dirname(os.path.abspath(SPEC))
repo_root = os.path.abspath(os.path.join(spec_dir, "..", ".."))
src_dir = os.path.join(repo_root, "src")

a = Analysis(
    [os.path.join(spec_dir, "entrypoint.py")],
    pathex=[repo_root, src_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "numpy",
        "pandas",
        "unittest",
        "test",
        "tests",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe_name = "espressomail.exe" if sys.platform.startswith("win") else "espressomail"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="espressomail",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
