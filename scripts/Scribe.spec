# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Scribe desktop launcher.

Usage:
    pyinstaller scripts/Scribe.spec --clean --noconfirm

Produces:
    dist/Scribe/Scribe.exe           (onedir build, smaller download on update)
    dist/Scribe.exe                  (onefile build if ONEFILE=1)

The onedir layout is the default; it's faster to update and avoids
Windows SmartScreen flagging a freshly-signed single-file exe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(os.getcwd())
SRC = ROOT / "src" / "scribe"

ONEFILE = os.environ.get("ONEFILE") == "1"

# ---------------------------------------------------------------------------
# Gather Scribe's package data (templates, static files, resource markdown,
# citation-style presets) so the bundled exe has everything the web UI needs.
# ---------------------------------------------------------------------------

datas = []

# Jinja templates + static CSS
datas += collect_data_files("scribe.web", includes=["templates/*.html", "static/*.css"])

# Resources (default style, academic writing rules)
datas += collect_data_files("scribe.resources", includes=["*.md"])

# Citation styles (parent + journal + custom presets)
try:
    datas += collect_data_files("scribe.citations", includes=["**/*.yml", "**/*.yaml", "**/*.md", "**/*.json"])
except Exception:
    pass

# ---------------------------------------------------------------------------
# Hidden imports -- Typer commands are imported lazily via string references
# in our CLI. PyInstaller can't see them, so list the submodules explicitly.
# ---------------------------------------------------------------------------

hiddenimports = collect_submodules("scribe") + [
    "scribe.cli",
    "scribe.web.app",
    "scribe.launcher",
    "scribe.stages.planner",
    "scribe.stages.executor",
    "scribe.stages.stitcher",
    "scribe.stages.reviewer",
    "scribe.stages.auditor",
    "scribe.stages.reviser",
    "scribe.stages.revision_stitcher",
    "scribe.stages.expander_planner",
    "scribe.stages.expander",
    "scribe.revision",
    "scribe.expansion",
    "scribe.export",
    "scribe.parsers.pdf",
    "scribe.parsers.docx",
    "scribe.parsers.xlsx",
    "scribe.parsers.refs",
    "scribe.parsers.outline",
    "scribe.parsers.sections",
]

# Third-party libs whose submodules are imported lazily
hiddenimports += [
    "tiktoken_ext.openai_public",
    "tiktoken_ext",
    "claude_code_sdk._internal.transport.subprocess_cli",
    "claude_code_sdk._internal.message_parser",
    "claude_code_sdk._internal.client",
    "claude_code_sdk.types",
]

# tiktoken ships its encoding files as data
try:
    datas += collect_data_files("tiktoken")
    datas += collect_data_files("tiktoken_ext")
except Exception:
    pass


block_cipher = None


a = Analysis(
    [str(SRC / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Desktop stack we don't need
        "tkinter", "matplotlib", "notebook", "IPython", "jupyter",
        "pytest", "hypothesis",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="Scribe",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,  # keep a console window so users can see status + errors
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Scribe",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=None,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Scribe",
    )
