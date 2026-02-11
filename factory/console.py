"""Shared console output for planner and factory.

Provides structured, optionally colored terminal output following the
"Sectioned Report" design (Option B from docs/CONSOLE_UX_PROPOSAL.md).

Verbosity levels:
  quiet   — verdict + errors only
  normal  — section headers, attempt progress, key-value summaries,
            failure excerpts, artifact paths
  verbose — everything in normal + timestamps, durations, token counts,
            baseline commits, full error excerpts, file lists

Color:
  Auto-detected (TTY check on stdout). Override with color=True/False
  or the --no-color CLI flag.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _supports_color(stream: TextIO) -> bool:
    """Return True if *stream* is a TTY that likely supports ANSI color."""
    if os.environ.get("NO_COLOR"):  # https://no-color.org/
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return hasattr(stream, "isatty") and stream.isatty()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

_VERBOSITY_LEVELS = {"quiet": 0, "normal": 1, "verbose": 2}
_RULE_CHAR = "\u2500"  # ─
_RULE_WIDTH = 72


class Console:
    """Structured terminal output with optional color and verbosity control."""

    def __init__(
        self,
        verbosity: str = "normal",
        color: bool | None = None,
        out: TextIO | None = None,
        err: TextIO | None = None,
    ) -> None:
        self._out = out or sys.stdout
        self._err = err or sys.stderr
        self._level = _VERBOSITY_LEVELS.get(verbosity, 1)
        self._color = color if color is not None else _supports_color(self._out)
        self._kv_width = 14  # default key column width

    # --- Internal helpers ------------------------------------------------

    def _c(self, code: str, text: str) -> str:
        """Wrap *text* in ANSI *code* if color is enabled."""
        if not self._color:
            return text
        return f"{code}{text}{_RESET}"

    def _write(self, msg: str, *, stream: TextIO | None = None) -> None:
        s = stream or self._out
        s.write(msg + "\n")
        s.flush()

    # --- Structure -------------------------------------------------------

    def header(self, title: str) -> None:
        """Print a section header: ── title ──────────────"""
        if self._level < 1:
            return
        rule_len = max(0, _RULE_WIDTH - len(title) - 4)
        line = f"{_RULE_CHAR * 2} {title} {_RULE_CHAR * rule_len}"
        self._write("")
        self._write(self._c(_BOLD, line))

    def kv(self, key: str, value: str, *, verbose_only: bool = False) -> None:
        """Print an aligned key: value pair."""
        if verbose_only and self._level < 2:
            return
        if self._level < 1:
            return
        k = self._c(_DIM, f"  {key + ':':<{self._kv_width}}")
        self._write(f"{k} {value}")

    def attempt_start(self, index: int, max_attempts: int, note: str = "") -> None:
        """Print an attempt header."""
        if self._level < 1:
            return
        suffix = f"  ({note})" if note else ""
        self._write("")
        self._write(self._c(_BOLD, f"  Attempt {index}/{max_attempts}{suffix}"))

    def step(self, node: str, status: str, detail: str = "") -> None:
        """Print a step within an attempt (e.g., 'SE  proposal: 2 files')."""
        if self._level < 1:
            return
        d = f"  {detail}" if detail else ""
        self._write(f"    {node:<4}{status}{d}")

    def error_block(self, lines: list[str], max_lines: int = 5) -> None:
        """Print an indented error excerpt (last N lines)."""
        if self._level < 1:
            return
        show = lines[-max_lines:] if self._level < 2 else lines
        for line in show:
            self._write(self._c(_RED, f"        {line}"))

    def verdict(self, result: str, detail: str = "") -> None:
        """Print the final verdict (PASS/FAIL/ERROR) with color."""
        if result == "PASS":
            v = self._c(_GREEN, "PASS")
        elif result == "ERROR":
            v = self._c(_RED, "ERROR")
        else:
            v = self._c(_RED, "FAIL")
        suffix = f"  {detail}" if detail else ""
        self._write("")
        self._write(f"  Verdict: {v}{suffix}")

    def warning(self, msg: str) -> None:
        """Print a warning to stderr."""
        tag = self._c(_YELLOW, "WARNING")
        self._write(f"{tag}: {msg}", stream=self._err)

    def error(self, msg: str) -> None:
        """Print an error to stderr."""
        tag = self._c(_RED, "ERROR")
        self._write(f"{tag}: {msg}", stream=self._err)

    def critical(self, msg: str) -> None:
        """Print a critical error to stderr."""
        tag = self._c(_RED, "CRITICAL")
        self._write(f"{tag}: {msg}", stream=self._err)

    def bullet(self, text: str) -> None:
        """Print an indented bullet point."""
        if self._level < 1:
            return
        self._write(f"    {text}")

    def blank(self) -> None:
        """Print a blank line (suppressed in quiet mode)."""
        if self._level < 1:
            return
        self._write("")

    def info(self, msg: str) -> None:
        """Print an informational line (suppressed in quiet mode)."""
        if self._level < 1:
            return
        self._write(f"  {msg}")

    def rollback_notice(self, target: str) -> None:
        """Print a rollback indicator."""
        if self._level < 1:
            return
        self._write(self._c(_YELLOW, f"    → rollback to {target[:12]}"))
