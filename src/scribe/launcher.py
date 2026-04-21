"""Desktop launcher for Scribe.

Designed for the packaged executable: finds a free port, starts the Flask
server in a background thread, opens the user's default browser to the
dashboard, and keeps a console window open so the user can close it to
shut down.

Usage:
    python -m scribe.launcher           # development
    scribe-desktop                      # console script
    Scribe.exe                          # PyInstaller bundle
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


DEFAULT_PORT = 5050  # offset from 5000 so it doesn't clash with a dev server
HOST = "127.0.0.1"


def _find_free_port(preferred: int = DEFAULT_PORT) -> int:
    """Return the preferred port if free, otherwise ask the OS for one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    """Poll the port until it accepts connections or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            try:
                s.connect((HOST, port))
                return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                time.sleep(0.15)
    return False


def _cli_preflight() -> str | None:
    """Return None if the Claude CLI is present, else a helpful message."""
    try:
        from scribe.sdk import _find_claude_cli
    except Exception as e:  # noqa: BLE001 - import failures during bundling
        return f"Could not import Scribe's SDK wrapper: {e}"

    path = _find_claude_cli()
    if path:
        return None
    return (
        "Scribe is installed, but the Claude Code CLI is not.\n\n"
        "Every Scribe pipeline runs via the local `claude` CLI, which you\n"
        "log into with your own Anthropic account or Max subscription. Install\n"
        "it first, then relaunch Scribe.\n\n"
        "  Windows: install the Claude desktop app from\n"
        "           https://claude.ai/download (bundles the CLI), or\n"
        "           run:  npm install -g @anthropic-ai/claude-code\n\n"
        "  macOS:   brew install claude\n\n"
        "  Linux:   npm install -g @anthropic-ai/claude-code\n\n"
        "Full instructions: https://docs.claude.com/en/docs/claude-code/overview\n"
    )


def _banner(url: str) -> None:
    print()
    print("  " + "=" * 60)
    print("  SCRIBE  --  Academic writing powered by Claude")
    print("  " + "=" * 60)
    print()
    print(f"  Running at: {url}")
    print("  Your browser should open automatically.")
    print()
    print("  Keep this window open while you work.")
    print("  Close it (or press Ctrl+C) to stop Scribe.")
    print("  " + "-" * 60)
    print()


def _open_browser_when_ready(url: str, port: int) -> None:
    if _wait_for_server(port):
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 -- best-effort; the URL is printed
            pass


def _run_flask(host: str, port: int) -> None:
    """Start the Flask server (blocking call)."""
    # Deferred import so a missing dep shows a readable message rather than
    # a console-flash crash when the user double-clicks the .exe
    from scribe.web.app import run_web

    run_web(host=host, port=port, debug=False)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Preflight: warn if the Claude CLI isn't installed yet. We still start
    # the server, because the user can use the Restyle and export features
    # that don't touch Claude.
    warning = _cli_preflight()

    port = _find_free_port()
    url = f"http://{HOST}:{port}"

    _banner(url)
    if warning:
        print("  [WARNING]")
        for line in warning.rstrip().splitlines():
            print(f"    {line}")
        print()

    # Open the browser on a delay so we don't race the server startup
    browser_thread = threading.Thread(
        target=_open_browser_when_ready, args=(url, port), daemon=True,
    )
    browser_thread.start()

    try:
        _run_flask(HOST, port)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"\n  ERROR: {e}", file=sys.stderr)
        print(
            "  (Try closing any other Scribe instance, or free up port "
            f"{port}.)",
            file=sys.stderr,
        )
        _wait_for_keypress()
        return 1
    except Exception as e:  # noqa: BLE001 -- last-resort error surface
        print(f"\n  UNEXPECTED ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        _wait_for_keypress()
        return 1

    print("\n  Scribe stopped. You can close this window.\n")
    return 0


def _wait_for_keypress() -> None:
    """Keep the console open so a packaged .exe doesn't vanish on error."""
    if os.environ.get("SCRIBE_NO_PAUSE"):
        return
    try:
        input("\n  Press Enter to close... ")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    raise SystemExit(main())
