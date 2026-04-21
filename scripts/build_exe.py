"""Build a Windows .exe for Scribe's desktop launcher.

Run from the project root:

    python scripts/build_exe.py               # onedir build (default)
    python scripts/build_exe.py --onefile     # single-file exe (slower start)

Outputs:
    dist/Scribe/Scribe.exe            -- onedir
    dist/Scribe.exe                   -- onefile

Prereqs:
    pip install -e .
    pip install pyinstaller
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "scripts" / "Scribe.spec"


def run(cmd: list[str], env: dict | None = None) -> None:
    print(">", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT), env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Build a single-file exe (slower to launch but easier to share).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=True,
        help="Clean build artifacts before building (default on).",
    )
    parser.add_argument(
        "--no-clean",
        dest="clean",
        action="store_false",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    if args.onefile:
        env["ONEFILE"] = "1"

    # Clean previous build output to avoid stale hidden imports.
    if args.clean:
        for d in ("build", "dist"):
            p = ROOT / d
            if p.exists():
                print(f"cleaning {p}")
                shutil.rmtree(p, ignore_errors=True)

    if not SPEC.exists():
        print(f"Spec not found: {SPEC}", file=sys.stderr)
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(SPEC),
        "--noconfirm",
    ]
    run(cmd, env=env)

    # Report output path
    if args.onefile:
        out = ROOT / "dist" / "Scribe.exe"
    else:
        out = ROOT / "dist" / "Scribe" / "Scribe.exe"

    if out.exists():
        size_mb = out.stat().st_size / (1024 * 1024)
        print()
        print("=" * 60)
        print(f"Built: {out}")
        print(f"Size:  {size_mb:.1f} MB")
        if not args.onefile:
            folder = out.parent
            folder_size = sum(
                p.stat().st_size for p in folder.rglob("*") if p.is_file()
            ) / (1024 * 1024)
            print(f"Folder size (for zipping): {folder_size:.1f} MB")
            print()
            print("To distribute the onedir build:")
            print(f"  1. Zip the entire folder: {folder}")
            print("  2. Users unzip it anywhere and run Scribe.exe")
        print()
        print("Users still need the Claude Code CLI installed separately.")
        print("Scribe's launcher detects it and prints install instructions")
        print("if it's missing.")
        print("=" * 60)
        return 0
    else:
        print(f"ERROR: expected output not found: {out}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
