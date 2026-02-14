"""Tests for the llmch unified CLI — help, wiring, run-all ordering, stop-on-failure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from unittest.mock import MagicMock, patch

import pytest

from llmch.__main__ import _discover_work_orders, _wo_sort_key, main


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
    def test_top_level_help_lists_subcommands(self):
        r = _llmch("--help")
        assert r.returncode == 0
        assert "plan" in r.stdout
        assert "run" in r.stdout
        assert "run-all" in r.stdout

    def test_top_level_help_does_not_list_pipeline(self):
        r = _llmch("--help")
        assert "pipeline" not in r.stdout

    def test_plan_help(self):
        r = _llmch("plan", "--help")
        assert r.returncode == 0
        assert "--spec" in r.stdout

    def test_run_help(self):
        r = _llmch("run", "--help")
        assert r.returncode == 0
        assert "--repo" in r.stdout
        assert "--work-order" in r.stdout

    def test_run_all_help(self):
        r = _llmch("run-all", "--help")
        assert r.returncode == 0
        assert "--repo" in r.stdout
        assert "--workdir" in r.stdout

    def test_pipeline_is_invalid(self):
        r = _llmch("pipeline", "--help")
        assert r.returncode != 0

    def test_no_args_shows_help(self):
        r = _llmch()
        assert r.returncode == 0
        assert "plan" in r.stdout


# ---------------------------------------------------------------------------
# Argument wiring
# ---------------------------------------------------------------------------


class TestPlanWiring:
    def test_missing_spec_fails(self):
        r = _llmch("plan")
        assert r.returncode != 0
        assert "--spec" in r.stderr

    def test_plan_delegates_to_planner(self):
        r = _llmch("plan", "--spec", "/nonexistent/spec.txt")
        assert r.returncode != 0
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
        r = _llmch("run", "--repo", "/tmp", "--work-order", "/nonexistent/wo.json")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "work order" in combined.lower() or "not found" in combined.lower() or "no such file" in combined.lower()


class TestRunAllWiring:
    def test_missing_repo_fails(self):
        r = _llmch("run-all", "--workdir", "/tmp")
        assert r.returncode != 0
        assert "--repo" in r.stderr

    def test_missing_workdir_fails(self):
        r = _llmch("run-all", "--repo", "/tmp")
        assert r.returncode != 0
        assert "--workdir" in r.stderr

    def test_nonexistent_workdir_fails(self):
        r = _llmch("run-all", "--repo", "/tmp", "--workdir", "/nonexistent/dir")
        assert r.returncode != 0
        assert "does not exist" in (r.stdout + r.stderr).lower()

    def test_empty_workdir_fails(self, tmp_path):
        r = _llmch("run-all", "--repo", "/tmp", "--workdir", str(tmp_path))
        assert r.returncode != 0
        assert "no wo-" in (r.stdout + r.stderr).lower()


# ---------------------------------------------------------------------------
# run-all: WO discovery and ordering
# ---------------------------------------------------------------------------


class TestWorkOrderOrdering:
    def test_numeric_sort(self, tmp_path):
        """WO-01, WO-02, WO-10 must sort as 1, 2, 10 — not lexical."""
        for name in ["WO-10.json", "WO-01.json", "WO-02.json"]:
            (tmp_path / name).write_text("{}")
        files = _discover_work_orders(str(tmp_path))
        names = [os.path.basename(f) for f in files]
        assert names == ["WO-01.json", "WO-02.json", "WO-10.json"]

    def test_sort_key_numeric(self):
        assert _wo_sort_key("WO-01.json") < _wo_sort_key("WO-02.json")
        assert _wo_sort_key("WO-02.json") < _wo_sort_key("WO-10.json")

    def test_sort_key_fallback(self):
        """Non-WO files get a high sort value."""
        assert _wo_sort_key("other.json")[0] == 999999

    def test_only_wo_pattern_matched(self, tmp_path):
        """Non-WO-*.json files are not picked up."""
        (tmp_path / "WO-01.json").write_text("{}")
        (tmp_path / "manifest.json").write_text("{}")
        (tmp_path / "notes.txt").write_text("")
        files = _discover_work_orders(str(tmp_path))
        assert len(files) == 1
        assert "WO-01" in files[0]


# ---------------------------------------------------------------------------
# run-all: stop-on-failure
# ---------------------------------------------------------------------------


class TestRunAllStopOnFailure:
    def test_stops_on_first_failure(self, tmp_path):
        """If WO-02 fails, WO-03 must not be invoked."""
        for name in ["WO-01.json", "WO-02.json", "WO-03.json"]:
            (tmp_path / name).write_text(json.dumps({"id": name.replace(".json", ""), "title": f"Test {name}"}))

        invoked: list[str] = []

        def mock_exec(cmd: list[str]) -> int:
            # Find the --work-order value
            for i, arg in enumerate(cmd):
                if arg == "--work-order" and i + 1 < len(cmd):
                    invoked.append(os.path.basename(cmd[i + 1]))
                    break
            # WO-02 fails
            if invoked[-1] == "WO-02.json":
                return 1
            return 0

        import llmch.__main__ as mod
        original_exec = mod._exec
        mod._exec = mock_exec
        try:
            import argparse
            args = argparse.Namespace(
                repo="/tmp/repo",
                workdir=str(tmp_path),
                branch=None,
                create_branch=False,
                reuse_branch=False,
                max_attempts=None,
                llm_model=None,
                allow_verify_exempt=False,
                artifacts_dir=None,
                verbose=False,
                quiet=False,
                no_color=False,
            )
            rc = mod._run_run_all(args, [])
        finally:
            mod._exec = original_exec

        assert rc == 1
        assert invoked == ["WO-01.json", "WO-02.json"]
        assert "WO-03.json" not in invoked


# ---------------------------------------------------------------------------
# run-all: passthrough forwarding
# ---------------------------------------------------------------------------


class TestPassthrough:
    def test_extra_args_forwarded(self, tmp_path):
        """Extra args after -- must reach the factory invocation."""
        (tmp_path / "WO-01.json").write_text(json.dumps({"id": "WO-01", "title": "Test"}))

        captured_cmd: list[list[str]] = []

        def mock_exec(cmd: list[str]) -> int:
            captured_cmd.append(cmd)
            return 0

        import llmch.__main__ as mod
        original_exec = mod._exec
        mod._exec = mock_exec
        try:
            import argparse
            args = argparse.Namespace(
                repo="/tmp/repo",
                workdir=str(tmp_path),
                branch=None,
                create_branch=False,
                reuse_branch=False,
                max_attempts=None,
                llm_model=None,
                allow_verify_exempt=False,
                artifacts_dir=None,
                verbose=False,
                quiet=False,
                no_color=False,
            )
            mod._run_run_all(args, ["--llm-temperature", "0.3", "--no-push"])
        finally:
            mod._exec = original_exec

        assert len(captured_cmd) == 1
        cmd = captured_cmd[0]
        assert "--llm-temperature" in cmd
        assert "0.3" in cmd
        assert "--no-push" in cmd


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


class TestPackaging:
    def test_console_script_declared_in_pyproject(self):
        toml_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "pyproject.toml"
        )
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        scripts = data.get("project", {}).get("scripts", {})
        assert "llmch" in scripts
        assert "llmch.__main__:main" in scripts["llmch"]

    def test_main_is_callable(self):
        from llmch.__main__ import main
        assert callable(main)
