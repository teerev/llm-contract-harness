"""``python -m llmch`` — unified CLI entry point.

Thin subprocess-delegation wrapper around:
  - python -m planner compile  (llmch plan)
  - python -m factory run      (llmch run)
  - python run_pipeline.py     (llmch pipeline)
"""

from __future__ import annotations

import argparse
import os
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
    p.add_argument("--artifacts-dir", default=None, help="Artifacts root directory (default: ./artifacts)")
    p.add_argument("--verbose", action="store_true", default=False)
    p.add_argument("--quiet", action="store_true", default=False)
    p.add_argument("--no-color", action="store_true", default=False)


def _run_run(args: argparse.Namespace, extra: list[str]) -> int:
    cmd = [sys.executable, "-m", "factory", "run",
           "--repo", args.repo, "--work-order", args.work_order]
    if args.branch:
        cmd += ["--branch", args.branch]
    if args.create_branch:
        cmd += ["--create-branch"]
    if args.reuse_branch:
        cmd += ["--reuse-branch"]
    if args.max_attempts is not None:
        cmd += ["--max-attempts", str(args.max_attempts)]
    if args.llm_model:
        cmd += ["--llm-model", args.llm_model]
    if args.allow_verify_exempt:
        cmd += ["--allow-verify-exempt"]
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
# Subcommand: pipeline
# ---------------------------------------------------------------------------

def _build_pipeline_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "pipeline",
        help="Plan + execute all work orders end-to-end",
        description="Full pipeline: compile a spec into work orders, then execute them sequentially against a repo.",
    )
    p.add_argument("--seed", required=True, help="Path to the product spec / seed text file")
    p.add_argument("--repo", required=True, help="Path to the target product git repo")
    p.add_argument("--branch", required=True, help="Git branch name for factory commits")
    p.add_argument("--create-branch", action="store_true", required=True, help="Create the branch (fails if exists)")
    p.add_argument("--model", default=None, help="LLM model name (default: gpt-5.2)")
    p.add_argument("--max-attempts", type=int, default=None, help="Max factory attempts per WO (default: 5)")
    p.add_argument("--artifacts-dir", default=None, help="Artifacts root directory (default: ./artifacts)")


def _run_pipeline(args: argparse.Namespace, extra: list[str]) -> int:
    # run_pipeline.py lives at repo root
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_pipeline.py")
    cmd = [sys.executable, script,
           "--seed", args.seed, "--repo", args.repo,
           "--branch", args.branch, "--create-branch"]
    if args.model:
        cmd += ["--model", args.model]
    if args.max_attempts is not None:
        cmd += ["--max-attempts", str(args.max_attempts)]
    if args.artifacts_dir:
        cmd += ["--artifacts-dir", args.artifacts_dir]
    cmd += extra
    return _exec(cmd)


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------

DESCRIPTION = """\
llmch — structurally enforced work orders for LLM code generation

Commands:
  plan       Compile a product spec into validated work orders
  run        Execute a single work order against a git repo
  pipeline   Plan + execute all work orders end-to-end

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
    _build_pipeline_parser(subparsers)

    # parse_known_args to allow -- passthrough for advanced flags
    args, extra = parser.parse_known_args()

    if args.command is None:
        parser.print_help()
        return 0

    dispatch = {
        "plan": _run_plan,
        "run": _run_run,
        "pipeline": _run_pipeline,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args, extra)


if __name__ == "__main__":
    sys.exit(main())
