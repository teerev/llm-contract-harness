#!/usr/bin/env python3
"""Full pipeline orchestrator: planner compile → factory run (sequential).

Takes a seed spec and a target repo, compiles work orders via the planner,
then executes them sequentially via the factory. Commits after each
successful WO. Pushes after the first commit; if the push fails, disables
push for all subsequent commits and continues.

Usage:
    python run_pipeline.py \
        --seed spec.txt \
        --repo /path/to/product \
        --branch factory/my-feature \
        --create-branch
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, capturing output, with no shell."""
    return subprocess.run(cmd, capture_output=True, timeout=30, shell=False, **kwargs)


def _run_streaming(
    cmd: list[str],
    timeout: int = 3600,
) -> tuple[int, str]:
    """Run a command with real-time output passthrough and color preservation.

    stdout and stderr are both forwarded to the terminal in real time
    (merged onto the parent's file descriptors, preserving ANSI color).
    stdout is also tee'd into a buffer so we can parse it afterward.

    Returns (exit_code, captured_stdout).
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=None,  # inherit — stderr goes straight to terminal
        bufsize=1,    # line-buffered
    )
    captured = io.StringIO()
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            sys.stdout.write(line)
            sys.stdout.flush()
            captured.write(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return proc.returncode or 0, captured.getvalue()


def _fatal(msg: str) -> None:
    print(f"\n  FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def _info(msg: str) -> None:
    print(f"  {msg}")


def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}\n")


def _find_wo_files(directory: str) -> list[str]:
    """Find and sort WO-*.json files in a directory."""
    pattern = os.path.join(directory, "WO-*.json")
    files = sorted(glob.glob(pattern))
    return files


# ---------------------------------------------------------------------------
# Planner stage
# ---------------------------------------------------------------------------

def run_planner(
    seed_path: str,
    repo_path: str | None,
    artifacts_dir: str | None,
) -> tuple[str, list[str]]:
    """Run the planner and return (output_dir, list_of_wo_paths).

    Raises SystemExit on failure.
    """
    _banner("PLANNER — Compiling work orders")
    _info(f"Seed: {seed_path}")
    if repo_path:
        _info(f"Repo: {repo_path}")

    cmd = [
        sys.executable, "-m", "planner", "compile",
        "--spec", seed_path,
    ]
    if repo_path:
        cmd += ["--repo", repo_path]
    if artifacts_dir:
        cmd += ["--artifacts-dir", artifacts_dir]

    returncode, stdout_text = _run_streaming(cmd, timeout=3600)

    if returncode != 0:
        _fatal(f"Planner failed with exit code {returncode}")

    # Find the output directory from planner stdout.
    # The planner prints lines like "  Output dir   /path/to/output" via Console.kv().
    # We also strip ANSI escape codes for parsing.
    import re
    _ansi_re = re.compile(r"\x1b\[[0-9;]*m")

    output_dir = None
    for line in stdout_text.splitlines():
        clean = _ansi_re.sub("", line)
        if "Output dir" in clean:
            # Console.kv format: "  Output dir   <path>"
            parts = clean.split(None, 2)
            if len(parts) >= 3:
                candidate = parts[-1].strip()
                if os.path.isdir(candidate):
                    output_dir = candidate
                    break

    # Fallback: scan the artifacts directory for the most recent planner run
    if not output_dir:
        artifacts_root = artifacts_dir or os.path.join(".", "artifacts")
        planner_root = os.path.join(artifacts_root, "planner")
        if os.path.isdir(planner_root):
            runs = sorted(os.listdir(planner_root))
            if runs:
                candidate = os.path.join(planner_root, runs[-1], "output")
                if os.path.isdir(candidate):
                    output_dir = candidate

    if not output_dir:
        _fatal("Could not determine planner output directory")

    wo_files = _find_wo_files(output_dir)
    if not wo_files:
        _fatal(f"No WO-*.json files found in {output_dir}")

    _info(f"Output: {output_dir}")
    _info(f"Work orders: {len(wo_files)}")
    for f in wo_files:
        with open(f) as fh:
            wo = json.load(fh)
        _info(f"  {wo.get('id', '?')}  {wo.get('title', '')}")

    return output_dir, wo_files


# ---------------------------------------------------------------------------
# Factory stage
# ---------------------------------------------------------------------------

def run_factory(
    wo_files: list[str],
    repo_path: str,
    branch: str,
    create_branch: bool,
    artifacts_dir: str | None,
    model: str,
    max_attempts: int,
) -> tuple[int, int]:
    """Run the factory on each WO sequentially. Returns (passed, failed)."""

    passed = 0
    failed = 0
    push_enabled = True  # try push on first success; disable if it fails

    for i, wo_path in enumerate(wo_files):
        wo_name = os.path.splitext(os.path.basename(wo_path))[0]

        with open(wo_path) as fh:
            wo = json.load(fh)
        is_first = (i == 0)
        verify_exempt = wo.get("verify_exempt", False)

        _banner(f"FACTORY — {wo_name}: {wo.get('title', '')}")

        cmd = [
            sys.executable, "-m", "factory", "run",
            "--repo", repo_path,
            "--work-order", wo_path,
            "--llm-model", model,
            "--max-attempts", str(max_attempts),
            "--branch", branch,
        ]

        if artifacts_dir:
            cmd += ["--artifacts-dir", artifacts_dir]

        # First WO: create the branch. Subsequent: reuse it.
        if is_first and create_branch:
            cmd += ["--create-branch"]
        else:
            cmd += ["--reuse-branch"]

        # Honor verify_exempt (planner computed it via M-01)
        if verify_exempt:
            cmd += ["--allow-verify-exempt"]

        # Disable push at factory level — we handle it ourselves
        cmd += ["--no-push"]

        t0 = time.time()
        returncode, _stdout = _run_streaming(cmd, timeout=3600)
        elapsed = time.time() - t0

        if returncode != 0:
            _info(f"{wo_name} FAILED (exit {returncode}, {elapsed:.1f}s)")
            failed += 1
            break

        _info(f"{wo_name} PASSED ({elapsed:.1f}s)")
        passed += 1

        # --- Push after first successful commit ---
        if push_enabled:
            _info(f"Pushing {branch} to remote...")
            push_result = _run(
                ["git", "push", "-u", "origin", branch],
                cwd=repo_path,
            )
            if push_result.returncode != 0:
                stderr = push_result.stderr.decode("utf-8", errors="replace").strip()
                _info(f"Push failed (non-fatal): {stderr}")
                _info("Continuing without push for remaining work orders.")
                push_enabled = False
            else:
                _info("Push succeeded.")

    return passed, failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Full pipeline: planner compile → factory run (sequential).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--seed", required=True,
        help="Path to the product spec / seed text file",
    )
    parser.add_argument(
        "--repo", required=True,
        help="Path to the target product git repo",
    )
    parser.add_argument(
        "--branch", required=True,
        help="Git branch name for factory commits (e.g. factory/my-feature)",
    )
    parser.add_argument(
        "--create-branch", action="store_true", required=True,
        help="Create the branch (fails if it already exists)",
    )
    parser.add_argument(
        "--artifacts-dir", default=None,
        help="Artifacts root directory (default: ./artifacts)",
    )
    parser.add_argument(
        "--model", default="gpt-5.2",
        help="LLM model name for factory SE calls (default: gpt-5.2)",
    )
    parser.add_argument(
        "--max-attempts", type=int, default=5,
        help="Max factory attempts per work order (default: 5)",
    )

    args = parser.parse_args()

    # --- Validate inputs ---
    if not os.path.isfile(args.seed):
        _fatal(f"Seed file not found: {args.seed}")
    repo = os.path.realpath(args.repo)
    if not os.path.isdir(repo):
        _fatal(f"Repo directory not found: {repo}")

    t_start = time.time()

    # --- Stage 1: Planner ---
    output_dir, wo_files = run_planner(
        seed_path=args.seed,
        repo_path=repo,
        artifacts_dir=args.artifacts_dir,
    )

    # --- Stage 2: Factory ---
    passed, failed = run_factory(
        wo_files=wo_files,
        repo_path=repo,
        branch=args.branch,
        create_branch=args.create_branch,
        artifacts_dir=args.artifacts_dir,
        model=args.model,
        max_attempts=args.max_attempts,
    )

    total = len(wo_files)
    elapsed = time.time() - t_start

    _banner(f"RESULTS: {passed} passed, {failed} failed, "
            f"{total - passed - failed} skipped ({total} total, {elapsed:.0f}s)")

    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
