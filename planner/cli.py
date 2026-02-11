"""Argparse CLI wiring for ``python -m planner``."""

from __future__ import annotations

import argparse
import os
import sys

from factory.console import Console


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


def _run_compile(args: argparse.Namespace) -> int:
    """Execute the compile command."""
    from planner.compiler import compile_plan

    color = False if getattr(args, "no_color", False) else None
    con = Console(verbosity=_verbosity(args), color=color)

    con.header("planner compile")
    con.kv("Spec", args.spec)

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
        con.error(str(exc))
        return 1
    except FileExistsError as exc:
        con.error(str(exc))
        return 1
    except RuntimeError as exc:
        msg = str(exc)
        con.error(msg)
        if "API" in msg or "OPENAI" in msg:
            return 3
        return 1
    except Exception as exc:
        con.error(f"API request failed: {exc}")
        return 3

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
