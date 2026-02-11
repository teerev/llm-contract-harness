"""Tests for factory.console — the shared console output module."""

from __future__ import annotations

import io

import pytest

from factory.console import Console


# ---------------------------------------------------------------------------
# Console unit tests
# ---------------------------------------------------------------------------


class TestConsoleBasics:
    """Basic Console output formatting."""

    def _make(self, verbosity: str = "normal") -> tuple[Console, io.StringIO, io.StringIO]:
        out = io.StringIO()
        err = io.StringIO()
        con = Console(verbosity=verbosity, color=False, out=out, err=err)
        return con, out, err

    def test_header(self):
        con, out, _ = self._make()
        con.header("planner compile")
        text = out.getvalue()
        assert "planner compile" in text
        assert "\u2500" in text  # ─ rule character

    def test_kv(self):
        con, out, _ = self._make()
        con.kv("Run ID", "abc123")
        text = out.getvalue()
        assert "Run ID:" in text
        assert "abc123" in text

    def test_kv_verbose_only_hidden_in_normal(self):
        con, out, _ = self._make("normal")
        con.kv("Baseline", "abc123", verbose_only=True)
        assert out.getvalue() == ""

    def test_kv_verbose_only_shown_in_verbose(self):
        con, out, _ = self._make("verbose")
        con.kv("Baseline", "abc123", verbose_only=True)
        assert "abc123" in out.getvalue()

    def test_attempt_start(self):
        con, out, _ = self._make()
        con.attempt_start(1, 3, "retry")
        text = out.getvalue()
        assert "Attempt 1/3" in text
        assert "retry" in text

    def test_step(self):
        con, out, _ = self._make()
        con.step("TR", "scope OK", "2 files")
        text = out.getvalue()
        assert "TR" in text
        assert "scope OK" in text

    def test_verdict_pass(self):
        con, out, _ = self._make()
        con.verdict("PASS")
        assert "PASS" in out.getvalue()

    def test_verdict_fail(self):
        con, out, _ = self._make()
        con.verdict("FAIL")
        assert "FAIL" in out.getvalue()

    def test_error_to_stderr(self):
        con, _, err = self._make()
        con.error("something broke")
        text = err.getvalue()
        assert "ERROR" in text
        assert "something broke" in text

    def test_warning_to_stderr(self):
        con, _, err = self._make()
        con.warning("heads up")
        text = err.getvalue()
        assert "WARNING" in text
        assert "heads up" in text

    def test_error_block(self):
        con, out, _ = self._make()
        lines = ["line1", "line2", "line3", "line4", "line5", "line6"]
        con.error_block(lines, max_lines=3)
        text = out.getvalue()
        # Should show last 3 lines in normal mode
        assert "line4" in text
        assert "line5" in text
        assert "line6" in text
        assert "line1" not in text

    def test_error_block_verbose_shows_all(self):
        con, out, _ = self._make("verbose")
        lines = ["line1", "line2", "line3", "line4", "line5", "line6"]
        con.error_block(lines, max_lines=3)
        text = out.getvalue()
        assert "line1" in text
        assert "line6" in text

    def test_bullet(self):
        con, out, _ = self._make()
        con.bullet("WO-01  Bootstrap verify")
        assert "WO-01" in out.getvalue()

    def test_rollback_notice(self):
        con, out, _ = self._make()
        con.rollback_notice("abc123def456")
        text = out.getvalue()
        assert "rollback" in text
        assert "abc123def456" in text


class TestQuietMode:
    """Quiet mode suppresses everything except errors and verdict."""

    def _make(self) -> tuple[Console, io.StringIO, io.StringIO]:
        out = io.StringIO()
        err = io.StringIO()
        con = Console(verbosity="quiet", color=False, out=out, err=err)
        return con, out, err

    def test_header_suppressed(self):
        con, out, _ = self._make()
        con.header("test")
        assert out.getvalue() == ""

    def test_kv_suppressed(self):
        con, out, _ = self._make()
        con.kv("Key", "value")
        assert out.getvalue() == ""

    def test_step_suppressed(self):
        con, out, _ = self._make()
        con.step("SE", "calling LLM")
        assert out.getvalue() == ""

    def test_verdict_shown(self):
        con, out, _ = self._make()
        con.verdict("PASS")
        assert "PASS" in out.getvalue()

    def test_error_shown(self):
        con, _, err = self._make()
        con.error("bad thing")
        assert "bad thing" in err.getvalue()


class TestColorDisabled:
    """With color=False, output contains no ANSI escape codes."""

    def test_no_ansi_in_header(self):
        out = io.StringIO()
        con = Console(color=False, out=out)
        con.header("test header")
        text = out.getvalue()
        assert "\033[" not in text

    def test_no_ansi_in_verdict(self):
        out = io.StringIO()
        con = Console(color=False, out=out)
        con.verdict("PASS")
        text = out.getvalue()
        assert "\033[" not in text

    def test_no_ansi_in_error(self):
        err = io.StringIO()
        con = Console(color=False, err=err)
        con.error("test error")
        text = err.getvalue()
        assert "\033[" not in text


# ---------------------------------------------------------------------------
# Integration: planner CLI output
# ---------------------------------------------------------------------------


class TestPlannerConsoleOutput:
    """Planner CLI produces structured console output."""

    def test_planner_success_output(self, tmp_path, capsys):
        """Successful compile shows header, hash, work order count, and verdict."""
        import json
        from unittest.mock import patch, MagicMock

        from planner.cli import main

        spec = str(tmp_path / "spec.txt")
        outdir = str(tmp_path / "out")
        with open(spec, "w") as f:
            f.write("Build a calculator.")

        # Mock the compile_plan to return a success result
        mock_result = MagicMock()
        mock_result.compile_hash = "abcd1234abcd1234"
        mock_result.artifacts_dir = str(tmp_path / "artifacts")
        mock_result.errors = []
        mock_result.warnings = []
        mock_result.work_orders = [
            {"id": "WO-01", "title": "Bootstrap verify"},
            {"id": "WO-02", "title": "Package skeleton"},
        ]
        mock_result.outdir = outdir
        mock_result.compile_attempts = 1

        with patch("planner.compiler.compile_plan", return_value=mock_result):
            code = main(["compile", "--spec", spec, "--outdir", outdir, "--no-color"])

        assert code == 0
        captured = capsys.readouterr()
        assert "planner compile" in captured.out
        assert "abcd1234abcd1234" in captured.out
        assert "PASS" in captured.out

    def test_planner_failure_output(self, tmp_path, capsys):
        """Failed compile shows error count and FAIL verdict."""
        from unittest.mock import patch, MagicMock

        from planner.cli import main

        spec = str(tmp_path / "spec.txt")
        outdir = str(tmp_path / "out")
        with open(spec, "w") as f:
            f.write("Build something.")

        mock_result = MagicMock()
        mock_result.compile_hash = "fail1234fail1234"
        mock_result.artifacts_dir = str(tmp_path / "artifacts")
        mock_result.errors = ["[E001] WO-02: id format violation"]
        mock_result.warnings = []
        mock_result.work_orders = []
        mock_result.outdir = outdir
        mock_result.compile_attempts = 3

        with patch("planner.compiler.compile_plan", return_value=mock_result):
            code = main(["compile", "--spec", spec, "--outdir", outdir, "--no-color"])

        assert code == 2
        captured = capsys.readouterr()
        assert "FAIL" in captured.out
        assert "validation failed" in captured.out


# ---------------------------------------------------------------------------
# Integration: factory CLI output
# ---------------------------------------------------------------------------


class TestFactoryConsoleOutput:
    """Factory CLI produces structured console output."""

    def test_factory_pass_output(self, tmp_path, capsys):
        """Successful run shows header, WO info, attempt summary, and PASS verdict."""
        import os
        from unittest.mock import patch

        from factory.run import run_cli
        from factory.console import Console

        from tests.factory.conftest import (
            add_verify_script,
            init_git_repo,
            make_valid_proposal_json,
            write_work_order,
        )

        repo = init_git_repo(str(tmp_path / "repo"))
        add_verify_script(repo)
        out = str(tmp_path / "out")
        wo_path = write_work_order(str(tmp_path / "wo.json"))

        valid_json = make_valid_proposal_json(repo)

        import argparse
        args = argparse.Namespace(
            repo=repo, work_order=wo_path, out=out,
            max_attempts=2, llm_model="test", llm_temperature=0,
            timeout_seconds=30, allow_verify_exempt=False,
        )
        con = Console(verbosity="normal", color=False)

        with patch("factory.llm.complete", return_value=valid_json):
            run_cli(args, console=con)

        captured = capsys.readouterr()
        assert "factory run" in captured.out
        assert "PASS" in captured.out
        assert "run_summary.json" in captured.out

    def test_factory_fail_output(self, tmp_path, capsys):
        """Failed run shows failure stage and FAIL verdict."""
        import os
        from unittest.mock import patch

        from factory.run import run_cli
        from factory.console import Console

        from tests.factory.conftest import (
            add_verify_script,
            init_git_repo,
            make_valid_proposal_json,
            write_work_order,
        )

        repo = init_git_repo(str(tmp_path / "repo"))
        add_verify_script(repo)
        out = str(tmp_path / "out")
        wo_path = write_work_order(
            str(tmp_path / "wo.json"),
            acceptance_commands=["python -c 'raise SystemExit(1)'"],
        )

        valid_json = make_valid_proposal_json(repo)

        import argparse
        args = argparse.Namespace(
            repo=repo, work_order=wo_path, out=out,
            max_attempts=1, llm_model="test", llm_temperature=0,
            timeout_seconds=30, allow_verify_exempt=False,
        )
        con = Console(verbosity="normal", color=False)

        import pytest as _pt
        with _pt.raises(SystemExit) as exc_info:
            with patch("factory.llm.complete", return_value=valid_json):
                run_cli(args, console=con)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "factory run" in captured.out
        assert "FAIL" in captured.out
