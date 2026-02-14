"""Argparse CLI wiring for ``python -m planner``."""

from __future__ import annotations

import argparse
import os
import sys
import time
import threading
from typing import TextIO

from factory.console import Console


# ---------------------------------------------------------------------------
# ANSI helpers (local to planner CLI — does not touch factory/console.py)
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"

# Braille spinner frames — compact and CI-safe (no wide Unicode).
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _supports_color(stream: TextIO) -> bool:
    """Return True if *stream* likely supports ANSI color."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return hasattr(stream, "isatty") and stream.isatty()
    except Exception:
        return False


def _is_tty(stream: TextIO) -> bool:
    try:
        return hasattr(stream, "isatty") and stream.isatty()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Streaming spinner — safe for CI and tests
# ---------------------------------------------------------------------------

class _StreamingSpinner:
    """Minimal carriage-return spinner shown while streaming OpenAI output.

    Only active when stderr is a TTY and verbosity is not quiet.
    Uses a daemon thread that writes ``\\r  Streaming… <frame>`` every
    250 ms.  The thread is stopped deterministically by ``stop()``.

    When stderr is not a TTY, or quiet mode is active, calling ``start()``
    and ``stop()`` are no-ops.
    """

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._enabled:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        # Clear the spinner line
        sys.stderr.write("\r\033[2K")
        sys.stderr.flush()

    def _run(self) -> None:
        idx = 0
        while not self._stop_event.is_set():
            frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
            sys.stderr.write(f"\r    Streaming… {frame}")
            sys.stderr.flush()
            idx += 1
            self._stop_event.wait(0.25)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planner",
        description="Compile a product spec into validated work-order JSON files.",
    )
    subparsers = parser.add_subparsers(dest="command")

    compile_parser = subparsers.add_parser(
        "compile", help="Compile a product spec into work orders"
    )
    compile_parser.add_argument(
        "--spec", required=True, help="Path to the product spec text file"
    )
    compile_parser.add_argument(
        "--outdir", default=None,
        help="Optional export directory for WO-*.json files (canonical output is always in artifacts)",
    )
    compile_parser.add_argument(
        "--template",
        default=None,
        help=(
            "Path to prompt template (default: ./examples/CREATE_WORK_ORDERS_PROMPT.md)"
        ),
    )
    compile_parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="Artifacts directory (default: ./examples/artifacts or ./artifacts)",
    )
    compile_parser.add_argument(
        "--repo",
        default=None,
        help=(
            "Path to the target product repo. Used to build a file listing "
            "for precondition validation. If omitted, a fresh (empty) repo "
            "is assumed."
        ),
    )
    compile_parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite existing WO-*.json and manifest in outdir",
    )
    compile_parser.add_argument(
        "--print-summary",
        action="store_true",
        default=False,
        help="Print compile summary to stdout",
    )
    compile_parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Show timestamps, token counts, and full error excerpts",
    )
    compile_parser.add_argument(
        "--quiet", action="store_true", default=False,
        help="Suppress all output except verdict and errors",
    )
    compile_parser.add_argument(
        "--no-color", action="store_true", default=False,
        help="Disable colored output",
    )

    return parser


def _verbosity(args: argparse.Namespace) -> str:
    if getattr(args, "quiet", False):
        return "quiet"
    if getattr(args, "verbose", False):
        return "verbose"
    return "normal"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "compile":
        return _run_compile(args)

    parser.print_help()
    return 1


# ---------------------------------------------------------------------------
# Attempt lifecycle output
# ---------------------------------------------------------------------------

_MAX_INLINE_ERRORS = 3  # show at most this many error codes inline


def _format_error_summary(errors: list, max_show: int = _MAX_INLINE_ERRORS) -> str:
    """Build a compact one-line summary from a list of ValidationError objects."""
    if not errors:
        return ""
    shown = errors[:max_show]
    parts = []
    for e in shown:
        code = e.code if hasattr(e, "code") else str(e)
        msg = e.message if hasattr(e, "message") else ""
        # Truncate long messages
        if len(msg) > 60:
            msg = msg[:57] + "..."
        wo_prefix = f"{e.wo_id}: " if hasattr(e, "wo_id") and e.wo_id else ""
        parts.append(f"[{code}] {wo_prefix}{msg}")
    text = "; ".join(parts)
    remaining = len(errors) - max_show
    if remaining > 0:
        text += f" (+{remaining} more)"
    return text


class _AttemptPrinter:
    """Handles per-attempt lifecycle console output.

    Receives AttemptEvent objects from the compile loop and prints
    structured, colored output to stdout via the Console.
    """

    def __init__(self, con: Console, use_color: bool, verbosity: str) -> None:
        self._con = con
        self._color = use_color
        self._verbosity = verbosity

    def _c(self, code: str, text: str) -> str:
        if not self._color:
            return text
        return f"{code}{text}{_RESET}"

    def handle(self, event: "AttemptEvent") -> None:
        from planner.compiler import AttemptEvent  # deferred to avoid circular

        if self._verbosity == "quiet":
            return

        if event.kind == "start":
            label = f"Attempt {event.attempt}/{event.max_attempts}"
            self._con.blank()
            self._con.info(self._c(_BOLD, label))

        elif event.kind == "fail":
            label = f"Attempt {event.attempt}/{event.max_attempts}"
            if event.is_final:
                verdict = self._c(_RED, "FAIL")
            else:
                verdict = self._c(_RED, "fail")
            self._con.info(f"{label} — {verdict}")
            # Compact error summary
            if event.errors:
                summary = _format_error_summary(event.errors)
                self._con.info(f"  {self._c(_DIM, summary)}")
            if event.errors_artifact:
                self._con.info(f"  {self._c(_DIM, 'errors: ' + event.errors_artifact)}")
            if not event.is_final:
                next_attempt = event.attempt + 1
                self._con.info(
                    self._c(_DIM, f"  Retrying: Attempt {next_attempt}/{event.max_attempts}…")
                )

        elif event.kind == "pass":
            label = f"Attempt {event.attempt}/{event.max_attempts}"
            verdict = self._c(_GREEN, "PASS")
            self._con.info(f"{label} — {verdict}")


# ---------------------------------------------------------------------------
# _run_compile — main compile command
# ---------------------------------------------------------------------------


def _run_compile(args: argparse.Namespace) -> int:
    """Execute the compile command."""
    from planner.compiler import compile_plan
    from planner.openai_client import set_stream_status_callback

    color = False if getattr(args, "no_color", False) else None
    con = Console(verbosity=_verbosity(args), color=color)

    # Resolve effective color: if color is None, Console auto-detects.
    use_color = color if color is not None else _supports_color(sys.stdout)
    verbosity = _verbosity(args)

    con.header("planner compile")
    con.kv("Spec", args.spec)

    # --- Attempt lifecycle printer ---
    printer = _AttemptPrinter(con, use_color, verbosity)

    # --- Streaming spinner (TTY-only, non-quiet) ---
    spinner_enabled = (
        verbosity != "quiet"
        and _is_tty(sys.stderr)
    )
    spinner = _StreamingSpinner(enabled=spinner_enabled)

    def _stream_status(status: str) -> None:
        if status == "start":
            spinner.start()
        elif status == "reasoning_start":
            # Stop spinner before reasoning text starts writing to stderr;
            # the two would interleave and produce garbled output.
            spinner.stop()
        elif status == "done":
            spinner.stop()

    set_stream_status_callback(_stream_status)

    try:
        result = compile_plan(
            spec_path=args.spec,
            outdir=args.outdir,
            template_path=args.template,
            artifacts_dir=args.artifacts_dir,
            overwrite=args.overwrite,
            repo_path=args.repo,
            on_attempt=printer.handle,
        )
    except FileNotFoundError as exc:
        spinner.stop()
        con.error(str(exc))
        return 1
    except FileExistsError as exc:
        spinner.stop()
        con.error(str(exc))
        return 1
    except RuntimeError as exc:
        spinner.stop()
        msg = str(exc)
        con.error(msg)
        if "API" in msg or "OPENAI" in msg:
            return 3
        return 1
    except Exception as exc:
        spinner.stop()
        con.error(f"API request failed: {exc}")
        return 3
    finally:
        # Ensure spinner is always cleaned up and callback is removed
        spinner.stop()
        set_stream_status_callback(None)

    # --- Structured output ---
    con.kv("Compile hash", result.compile_hash)
    con.kv("Run ID", result.run_id)
    con.kv("Artifacts", result.run_dir)
    con.kv("Attempts", str(result.compile_attempts), verbose_only=True)

    if result.errors:
        error_path = _find_validation_errors(result)
        con.kv("Work orders", "0 (validation failed)")
        con.kv("Errors", str(len(result.errors)))
        if error_path:
            con.kv("See", error_path)
        con.verdict("FAIL")

        is_parse = any("JSON parse" in e for e in result.errors)
        return 4 if is_parse else 2

    con.kv("Work orders", str(len(result.work_orders)))
    con.kv("Output dir", result.outdir or os.path.join(result.run_dir, "output"))

    if args.print_summary or _verbosity(args) == "verbose":
        con.blank()
        for wo in result.work_orders:
            con.bullet(f"{wo['id']}  {wo.get('title', '')}")

    con.verdict("PASS")
    return 0


def _find_validation_errors(result) -> str:
    """Return path to validation_errors.json if it exists."""
    import os

    for candidate in [
        os.path.join(result.outdir, "validation_errors.json"),
        os.path.join(result.artifacts_dir, "validation_errors.json"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    return ""
