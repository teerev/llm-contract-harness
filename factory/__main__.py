"""``python -m factory`` entry point — argparse wiring, delegates to run.run_cli()."""

from __future__ import annotations

import argparse
import sys

from factory.defaults import (
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Factory harness: deterministic SE → TR → PO loop.",
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

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        if args.max_attempts < 1:
            print(
                "ERROR: --max-attempts must be at least 1.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Defer import so ``--help`` stays fast
        from factory.run import run_cli

        run_cli(args)


if __name__ == "__main__":
    main()
