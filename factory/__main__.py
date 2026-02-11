"""``python -m factory`` entry point — argparse wiring, delegates to run.run_cli()."""

from __future__ import annotations

import argparse
import sys

from factory.console import Console
from factory.defaults import (
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Factory harness: structural enforcement SE → TR → PO loop.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- ``run`` sub-command ---
    run_parser = subparsers.add_parser("run", help="Execute a work order")
    run_parser.add_argument(
        "--repo", required=True, help="Path to the product git repo"
    )
    run_parser.add_argument(
        "--work-order", required=True, help="Path to the work-order JSON file"
    )
    run_parser.add_argument(
        "--out", required=True, help="Output directory for artifacts"
    )
    run_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"Maximum number of attempts (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    run_parser.add_argument(
        "--llm-model", required=True, help="LLM model name (e.g. gpt-4o)"
    )
    run_parser.add_argument(
        "--llm-temperature",
        type=float,
        default=DEFAULT_LLM_TEMPERATURE,
        help=f"LLM temperature (default: {DEFAULT_LLM_TEMPERATURE})",
    )
    run_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-command timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    run_parser.add_argument(
        "--allow-verify-exempt",
        action="store_true",
        default=False,
        help=(
            "Honor verify_exempt=true in work orders (skip global verification). "
            "Without this flag, verify_exempt is overridden to false with a warning."
        ),
    )
    run_parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Show timestamps, durations, baseline commit, and full error excerpts",
    )
    run_parser.add_argument(
        "--quiet", action="store_true", default=False,
        help="Suppress all output except verdict and errors",
    )
    run_parser.add_argument(
        "--no-color", action="store_true", default=False,
        help="Disable colored output",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        color = False if getattr(args, "no_color", False) else None
        verbosity = "quiet" if getattr(args, "quiet", False) else (
            "verbose" if getattr(args, "verbose", False) else "normal"
        )
        con = Console(verbosity=verbosity, color=color)

        if args.max_attempts < 1:
            con.error("--max-attempts must be at least 1.")
            sys.exit(1)

        # Defer import so ``--help`` stays fast
        from factory.run import run_cli

        run_cli(args, console=con)


if __name__ == "__main__":
    main()
