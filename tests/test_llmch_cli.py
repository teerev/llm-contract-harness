"""Tests for the llmch unified CLI — argument wiring and help output."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib

import pytest


def _llmch(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m llmch`` with the given arguments."""
    return subprocess.run(
        [sys.executable, "-m", "llmch"] + list(args),
        capture_output=True,
        text=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Help output
# ---------------------------------------------------------------------------


class TestHelp:
    def test_top_level_help(self):
        r = _llmch("--help")
        assert r.returncode == 0
        assert "plan" in r.stdout
        assert "run" in r.stdout
        assert "pipeline" in r.stdout

    def test_plan_help(self):
        r = _llmch("plan", "--help")
        assert r.returncode == 0
        assert "--spec" in r.stdout

    def test_run_help(self):
        r = _llmch("run", "--help")
        assert r.returncode == 0
        assert "--repo" in r.stdout
        assert "--work-order" in r.stdout

    def test_pipeline_help(self):
        r = _llmch("pipeline", "--help")
        assert r.returncode == 0
        assert "--seed" in r.stdout
        assert "--repo" in r.stdout
        assert "--branch" in r.stdout

    def test_no_args_shows_help(self):
        r = _llmch()
        assert r.returncode == 0
        assert "plan" in r.stdout


# ---------------------------------------------------------------------------
# Argument wiring — verify flags reach the underlying command
# ---------------------------------------------------------------------------


class TestPlanWiring:
    def test_missing_spec_fails(self):
        """llmch plan without --spec should fail (argparse error)."""
        r = _llmch("plan")
        assert r.returncode != 0
        assert "--spec" in r.stderr

    def test_plan_delegates_to_planner(self):
        """llmch plan --spec X should invoke planner compile --spec X.

        We can't run a real compile (needs API key), but we can verify
        the planner is invoked by checking for its characteristic error.
        """
        r = _llmch("plan", "--spec", "/nonexistent/spec.txt")
        # Planner should report "file not found" or similar — proving
        # it was actually invoked (not just parsed by llmch).
        assert r.returncode != 0
        # The error comes from the planner, not from llmch argparse
        combined = r.stdout + r.stderr
        assert "spec" in combined.lower() or "not found" in combined.lower() or "no such file" in combined.lower()


class TestRunWiring:
    def test_missing_repo_fails(self):
        r = _llmch("run", "--work-order", "x.json")
        assert r.returncode != 0
        assert "--repo" in r.stderr

    def test_missing_work_order_fails(self):
        r = _llmch("run", "--repo", "/tmp")
        assert r.returncode != 0
        assert "--work-order" in r.stderr

    def test_run_delegates_to_factory(self):
        """llmch run with a bad work order should invoke factory and get its error."""
        r = _llmch("run", "--repo", "/tmp", "--work-order", "/nonexistent/wo.json")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "work order" in combined.lower() or "not found" in combined.lower() or "no such file" in combined.lower()


class TestPipelineWiring:
    def test_missing_seed_fails(self):
        r = _llmch("pipeline", "--repo", "/tmp", "--branch", "x", "--create-branch")
        assert r.returncode != 0
        assert "--seed" in r.stderr

    def test_missing_branch_fails(self):
        r = _llmch("pipeline", "--seed", "x.txt", "--repo", "/tmp", "--create-branch")
        assert r.returncode != 0
        assert "--branch" in r.stderr


# ---------------------------------------------------------------------------
# Packaging — console script entry point
# ---------------------------------------------------------------------------


class TestPackaging:
    def test_console_script_declared_in_pyproject(self):
        """pyproject.toml must declare llmch as a console script."""
        toml_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "pyproject.toml"
        )
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        scripts = data.get("project", {}).get("scripts", {})
        assert "llmch" in scripts, "llmch console script not declared in pyproject.toml"
        assert "llmch.__main__:main" in scripts["llmch"]

    def test_main_is_callable(self):
        """The entry point target must be importable and callable."""
        from llmch.__main__ import main
        assert callable(main)
