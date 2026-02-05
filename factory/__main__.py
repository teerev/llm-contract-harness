"""``python -m factory`` entry point — argparse wiring, delegates to run.run_cli()."""

from __future__ import annotations

import argparse
import sys


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
        default=2,
        help="Maximum number of attempts (default: 2)",
    )
    run_parser.add_argument(
        "--llm-model", required=True, help="LLM model name (e.g. gpt-4o)"
    )
    run_parser.add_argument(
        "--llm-temperature",
        type=float,
        default=0,
        help="LLM temperature (default: 0)",
    )
    run_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Per-command timeout in seconds (default: 600)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        # Defer import so ``--help`` stays fast
        from factory.run import run_cli

        run_cli(args)


if __name__ == "__main__":
    main()
