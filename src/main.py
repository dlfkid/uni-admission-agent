#!/usr/bin/env python3
"""
UniAdmission Agent — Legacy entry point (thin shim).

Delegates to the Typer CLI in ``src.cmd.cli``.
Kept for backward compatibility with ``uv run src/main.py``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cmd.cli import app  # noqa: E402


def main() -> int:
    """Run the Typer CLI application."""
    try:
        app()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


if __name__ == "__main__":
    sys.exit(main())
