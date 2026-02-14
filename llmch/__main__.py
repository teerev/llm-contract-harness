"""``python -m llmch`` — unified CLI entry point.

Thin subprocess-delegation wrapper around:
  - python -m planner compile  (llmch plan)
  - python -m factory run      (llmch run / llmch run-all)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# Subprocess runner (shared)
# ---------------------------------------------------------------------------

def _exec(cmd: list[str]) -> int:
    """Run *cmd* with inherited stdio (streaming, color-preserving).

    Returns the child's exit code.
    """
    try:
        proc = subprocess.run(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        return proc.returncode
    except KeyboardInterrupt:
        return 130


# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------

def _build_plan_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "plan",
        help="Compile a product spec into validated work orders",
        description="Compile a natural-language product spec into a sequence of validated work-order JSON files.",
    )
    p.add_argument("--spec", required=True, help="Path to the product spec text file")
    p.add_argument("--outdir", default=None, help="Export directory for WO-*.json files (optional)")
    p.add_argument("--repo", default=None, help="Target repo for precondition validation (optional)")
    p.add_argument("--artifacts-dir", default=None, help="Artifacts root directory (default: ./artifacts)")
    p.add_argument("--verbose", action="store_true", default=False)
    p.add_argument("--quiet", action="store_true", default=False)
    p.add_argument("--no-color", action="store_true", default=False)


def _run_plan(args: argparse.Namespace, extra: list[str]) -> int:
    cmd = [sys.executable, "-m", "planner", "compile", "--spec", args.spec]
    if args.outdir:
        cmd += ["--outdir", args.outdir]
    if args.repo:
        cmd += ["--repo", args.repo]
    if args.artifacts_dir:
        cmd += ["--artifacts-dir", args.artifacts_dir]
    if args.verbose:
        cmd += ["--verbose"]
    if args.quiet:
        cmd += ["--quiet"]
    if args.no_color:
        cmd += ["--no-color"]
    cmd += extra
    return _exec(cmd)


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------

def _build_run_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "run",
        help="Execute a single work order against a git repo",
        description="Execute a single work order inside the structural enforcement harness (SE → TR → PO).",
    )
    p.add_argument("--repo", required=True, help="Path to the product git repo")
    p.add_argument("--work-order", required=True, help="Path to the work-order JSON file")
    p.add_argument("--branch", default=None, help="Working branch name")
    p.add_argument("--create-branch", action="store_true", default=False, help="Create the branch (fails if exists)")
    p.add_argument("--reuse-branch", action="store_true", default=False, help="Require branch to exist (resume mode)")
    p.add_argument("--max-attempts", type=int, default=None, help="Max SE→TR→PO attempts (default: 5)")
    p.add_argument("--llm-model", default=None, help="LLM model name (default: gpt-5.2)")
    p.add_argument("--allow-verify-exempt", action="store_true", default=False, help="Honor verify_exempt=true")
    p.add_argument("--python", default=None, help="Python interpreter for target-repo venv")
    p.add_argument("--artifacts-dir", default=None, help="Artifacts root directory (default: ./artifacts)")
    p.add_argument("--verbose", action="store_true", default=False)
    p.add_argument("--quiet", action="store_true", default=False)
    p.add_argument("--no-color", action="store_true", default=False)


def _build_factory_cmd(args: argparse.Namespace, wo_path: str, extra: list[str]) -> list[str]:
    """Build the factory run command for a single work order.

    Shared between ``run`` (single WO) and ``run-all`` (batch).
    """
    cmd = [sys.executable, "-m", "factory", "run",
           "--repo", args.repo, "--work-order", wo_path]
    if getattr(args, "branch", None):
        cmd += ["--branch", args.branch]
    if getattr(args, "create_branch", False):
        cmd += ["--create-branch"]
    if getattr(args, "reuse_branch", False):
        cmd += ["--reuse-branch"]
    if getattr(args, "max_attempts", None) is not None:
        cmd += ["--max-attempts", str(args.max_attempts)]
    if getattr(args, "llm_model", None):
        cmd += ["--llm-model", args.llm_model]
    if getattr(args, "allow_verify_exempt", False):
        cmd += ["--allow-verify-exempt"]
    if getattr(args, "python", None):
        cmd += ["--python", args.python]
    if getattr(args, "artifacts_dir", None):
        cmd += ["--artifacts-dir", args.artifacts_dir]
    if getattr(args, "verbose", False):
        cmd += ["--verbose"]
    if getattr(args, "quiet", False):
        cmd += ["--quiet"]
    if getattr(args, "no_color", False):
        cmd += ["--no-color"]
    cmd += extra
    return cmd


def _run_run(args: argparse.Namespace, extra: list[str]) -> int:
    cmd = _build_factory_cmd(args, args.work_order, extra)
    return _exec(cmd)


# ---------------------------------------------------------------------------
# Subcommand: run-all
# ---------------------------------------------------------------------------

_WO_NUM_RE = re.compile(r"WO-(\d+)", re.IGNORECASE)


def _wo_sort_key(path: str) -> tuple[int, str]:
    """Sort key: numeric WO index first, then lexical fallback."""
    name = os.path.basename(path)
    m = _WO_NUM_RE.search(name)
    if m:
        return (int(m.group(1)), name)
    return (999999, name)


def _discover_work_orders(workdir: str) -> list[str]:
    """Find and sort WO-*.json files in *workdir*."""
    pattern = os.path.join(workdir, "WO-*.json")
    files = sorted(glob.glob(pattern), key=_wo_sort_key)
    return files


def _build_run_all_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "run-all",
        help="Run all work orders in a directory sequentially",
        description=(
            "Discover WO-*.json files in --workdir, sort by WO number, "
            "and execute each via 'factory run' in order. "
            "Stops on the first failure. Extra flags after '--' are "
            "forwarded to each factory run invocation."
        ),
    )
    p.add_argument("--repo", required=True, help="Path to the product git repo")
    p.add_argument("--workdir", required=True, help="Directory containing WO-*.json files")
    p.add_argument("--branch", default=None, help="Working branch name")
    p.add_argument("--create-branch", action="store_true", default=False, help="Create the branch (fails if exists)")
    p.add_argument("--reuse-branch", action="store_true", default=False, help="Require branch to exist (resume mode)")
    p.add_argument("--max-attempts", type=int, default=None, help="Max SE→TR→PO attempts per WO (default: 5)")
    p.add_argument("--llm-model", default=None, help="LLM model name (default: gpt-5.2)")
    p.add_argument("--allow-verify-exempt", action="store_true", default=False, help="Honor verify_exempt=true")
    p.add_argument("--python", default=None, help="Python interpreter for target-repo venv")
    p.add_argument("--artifacts-dir", default=None, help="Artifacts root directory (default: ./artifacts)")
    p.add_argument("--verbose", action="store_true", default=False)
    p.add_argument("--quiet", action="store_true", default=False)
    p.add_argument("--no-color", action="store_true", default=False)


def _run_run_all(args: argparse.Namespace, extra: list[str]) -> int:
    workdir = os.path.realpath(args.workdir)
    if not os.path.isdir(workdir):
        print(f"ERROR: --workdir does not exist: {workdir}", file=sys.stderr)
        return 1

    wo_files = _discover_work_orders(workdir)
    if not wo_files:
        print(f"ERROR: No WO-*.json files found in {workdir}", file=sys.stderr)
        return 1

    total = len(wo_files)
    print(f"Found {total} work order(s) in {workdir}\n")

    for i, wo_path in enumerate(wo_files, 1):
        name = os.path.splitext(os.path.basename(wo_path))[0]

        # Try to extract title from JSON for nicer output
        title = ""
        try:
            with open(wo_path, "r", encoding="utf-8") as fh:
                title = json.load(fh).get("title", "")
        except Exception:
            pass

        label = f"{name}: {title}" if title else name
        print(f"[{i}/{total}] {label}")

        # First WO: honor --create-branch. Subsequent: force --reuse-branch.
        run_args = argparse.Namespace(**vars(args))
        run_args.work_order = wo_path
        if i == 1:
            pass  # keep create_branch / reuse_branch as user set them
        else:
            run_args.create_branch = False
            run_args.reuse_branch = True if args.branch else False

        cmd = _build_factory_cmd(run_args, wo_path, extra)
        rc = _exec(cmd)

        if rc != 0:
            print(f"\n{name} FAILED (exit {rc}). Stopping.", file=sys.stderr)
            return rc

    print(f"\nAll {total} work order(s) passed.")
    return 0


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------

DESCRIPTION = """\
llmch — structurally enforced work orders for LLM code generation

Commands:
  plan       Compile a product spec into validated work orders
  run        Execute a single work order against a git repo
  run-all    Run all work orders in a directory sequentially

Use 'llmch <command> --help' for details on each command.
Extra flags after '--' are forwarded to the underlying tool.
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="llmch",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    _build_plan_parser(subparsers)
    _build_run_parser(subparsers)
    _build_run_all_parser(subparsers)

    # parse_known_args to allow -- passthrough for advanced flags
    args, extra = parser.parse_known_args()

    if args.command is None:
        parser.print_help()
        return 0

    dispatch = {
        "plan": _run_plan,
        "run": _run_run,
        "run-all": _run_run_all,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args, extra)


if __name__ == "__main__":
    sys.exit(main())
