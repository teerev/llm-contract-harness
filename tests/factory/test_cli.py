"""Tests for CLI entrypoint and preflight rejections."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.factory.conftest import init_git_repo, write_work_order


def _run_factory(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run ``python -m factory`` with the given arguments."""
    run_env = os.environ.copy()
    # Ensure OPENAI_API_KEY is not set (prevent accidental network calls)
    run_env.pop("OPENAI_API_KEY", None)
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "factory"] + list(args),
        capture_output=True,
        timeout=30,
        env=run_env,
    )


# ---------------------------------------------------------------------------
# Help and basic CLI wiring
# ---------------------------------------------------------------------------


class TestCLIHelp:
    def test_no_args_shows_help(self):
        result = _run_factory()
        assert result.returncode == 1
        assert b"usage:" in result.stdout.lower() or b"usage:" in result.stderr.lower() \
            or b"Factory harness" in result.stdout

    def test_help_flag(self):
        result = _run_factory("--help")
        assert result.returncode == 0
        assert b"factory" in result.stdout.lower()

    def test_run_help(self):
        result = _run_factory("run", "--help")
        assert result.returncode == 0
        assert b"--repo" in result.stdout
        assert b"--work-order" in result.stdout


# ---------------------------------------------------------------------------
# --max-attempts validation
# ---------------------------------------------------------------------------


class TestMaxAttempts:
    def test_zero_rejected(self, tmp_path):
        repo = init_git_repo(str(tmp_path / "repo"))
        wo = write_work_order(str(tmp_path / "wo.json"))
        out = str(tmp_path / "out")

        result = _run_factory(
            "run",
            "--repo", repo,
            "--work-order", wo,
            "--out", out,
            "--llm-model", "test",
            "--max-attempts", "0",
        )
        assert result.returncode == 1
        assert b"--max-attempts must be at least 1" in result.stderr


# ---------------------------------------------------------------------------
# Preflight rejections
# ---------------------------------------------------------------------------


class TestPreflightRejections:
    def test_not_a_git_repo(self, tmp_path):
        nope = str(tmp_path / "nope")
        os.makedirs(nope)
        wo = write_work_order(str(tmp_path / "wo.json"))
        out = str(tmp_path / "out")

        result = _run_factory(
            "run",
            "--repo", nope,
            "--work-order", wo,
            "--out", out,
            "--llm-model", "test",
        )
        assert result.returncode == 1
        assert b"not a git repository" in result.stderr

    def test_dirty_repo_untracked(self, tmp_path):
        repo = init_git_repo(str(tmp_path / "repo"))
        # Create untracked file
        with open(os.path.join(repo, "untracked.txt"), "w") as f:
            f.write("dirty")
        wo = write_work_order(str(tmp_path / "wo.json"))
        out = str(tmp_path / "out")

        result = _run_factory(
            "run",
            "--repo", repo,
            "--work-order", wo,
            "--out", out,
            "--llm-model", "test",
        )
        assert result.returncode == 1
        assert b"uncommitted changes" in result.stderr

        # Repo should not be modified by the preflight check
        # (the untracked file should still be there, untouched)
        assert os.path.exists(os.path.join(repo, "untracked.txt"))

    def test_dirty_repo_staged(self, tmp_path):
        repo = init_git_repo(str(tmp_path / "repo"))
        with open(os.path.join(repo, "hello.txt"), "w") as f:
            f.write("changed")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        wo = write_work_order(str(tmp_path / "wo.json"))
        out = str(tmp_path / "out")

        result = _run_factory(
            "run",
            "--repo", repo,
            "--work-order", wo,
            "--out", out,
            "--llm-model", "test",
        )
        assert result.returncode == 1
        assert b"uncommitted changes" in result.stderr

    def test_outdir_inside_repo(self, tmp_path):
        repo = init_git_repo(str(tmp_path / "repo"))
        wo = write_work_order(str(tmp_path / "wo.json"))
        out = os.path.join(repo, "output")

        result = _run_factory(
            "run",
            "--repo", repo,
            "--work-order", wo,
            "--out", out,
            "--llm-model", "test",
        )
        assert result.returncode == 1
        assert b"must not be inside" in result.stderr

    def test_invalid_work_order(self, tmp_path):
        repo = init_git_repo(str(tmp_path / "repo"))
        wo = str(tmp_path / "bad_wo.json")
        with open(wo, "w") as f:
            f.write("not json")
        out = str(tmp_path / "out")

        result = _run_factory(
            "run",
            "--repo", repo,
            "--work-order", wo,
            "--out", out,
            "--llm-model", "test",
        )
        assert result.returncode == 1
        assert b"Failed to load work order" in result.stderr

    def test_missing_work_order(self, tmp_path):
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")

        result = _run_factory(
            "run",
            "--repo", repo,
            "--work-order", str(tmp_path / "nope.json"),
            "--out", out,
            "--llm-model", "test",
        )
        assert result.returncode == 1
        assert b"Failed to load work order" in result.stderr

    def test_preflight_does_not_modify_repo(self, tmp_path):
        """After a preflight failure (not a git repo), the directory is untouched."""
        nope = str(tmp_path / "nope")
        os.makedirs(nope)
        # Put a file in there
        sentinel = os.path.join(nope, "sentinel.txt")
        with open(sentinel, "w") as f:
            f.write("original")

        wo = write_work_order(str(tmp_path / "wo.json"))
        out = str(tmp_path / "out")

        _run_factory(
            "run",
            "--repo", nope,
            "--work-order", wo,
            "--out", out,
            "--llm-model", "test",
        )

        # Directory unchanged
        with open(sentinel) as f:
            assert f.read() == "original"
        assert set(os.listdir(nope)) == {"sentinel.txt"}
