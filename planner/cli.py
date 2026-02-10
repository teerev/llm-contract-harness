"""Argparse CLI wiring for ``python -m planner``."""

from __future__ import annotations

import argparse
import sys


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
        "--outdir", required=True, help="Output directory for WO-*.json files"
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

    return parser


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


def _run_compile(args: argparse.Namespace) -> int:
    """Execute the compile command."""
    from planner.compiler import compile_plan

    try:
        result = compile_plan(
            spec_path=args.spec,
            outdir=args.outdir,
            template_path=args.template,
            artifacts_dir=args.artifacts_dir,
            overwrite=args.overwrite,
            repo_path=args.repo,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        # API errors
        msg = str(exc)
        print(f"ERROR: {msg}", file=sys.stderr)
        if "API" in msg or "OPENAI" in msg:
            return 3
        return 1
    except Exception as exc:
        # Catch transport/network errors (httpx, urllib, etc.)
        print(f"ERROR: API request failed: {exc}", file=sys.stderr)
        return 3

    # --- Console output ---
    print(f"Compile hash: {result.compile_hash}")
    print(f"Artifacts:    {result.artifacts_dir}")

    if result.errors:
        # Validation or parse error
        error_path = _find_validation_errors(result)
        print(f"Work orders:  0 (validation failed)")
        print(f"Errors:       {len(result.errors)}")
        if error_path:
            print(f"See:          {error_path}")

        is_parse = any("JSON parse" in e for e in result.errors)
        return 4 if is_parse else 2

    print(f"Work orders:  {len(result.work_orders)}")
    print(f"Output dir:   {result.outdir}")

    if args.print_summary:
        print()
        for wo in result.work_orders:
            print(f"  {wo['id']}  {wo.get('title', '')}")

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
