"""Tests for planner/cli.py — argument parsing, exit codes, console output."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from planner.cli import build_parser, main
from planner.compiler import CompileResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _success_result(outdir: str, artifacts_dir: str) -> CompileResult:
    r = CompileResult()
    r.compile_hash = "abc123"
    r.artifacts_dir = artifacts_dir
    r.outdir = outdir
    r.work_orders = [{"id": "WO-01", "title": "Test"}]
    r.errors = []
    r.success = True
    return r


def _validation_error_result(outdir: str, artifacts_dir: str) -> CompileResult:
    r = CompileResult()
    r.compile_hash = "abc123"
    r.artifacts_dir = artifacts_dir
    r.outdir = outdir
    r.work_orders = []
    r.errors = ["[E001] WO-01: bad id"]
    r.success = False
    return r


def _parse_error_result(outdir: str, artifacts_dir: str) -> CompileResult:
    r = CompileResult()
    r.compile_hash = "abc123"
    r.artifacts_dir = artifacts_dir
    r.outdir = outdir
    r.work_orders = []
    r.errors = ["[E000] JSON parse error: blah"]
    r.success = False
    return r


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_compile_args_parsed(self):
        parser = build_parser()
        args = parser.parse_args([
            "compile", "--spec", "s.txt", "--outdir", "out",
        ])
        assert args.command == "compile"
        assert args.spec == "s.txt"
        assert args.outdir == "out"
        assert args.overwrite is False
        assert args.print_summary is False

    def test_optional_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "compile", "--spec", "s.txt", "--outdir", "out",
            "--template", "t.md", "--artifacts-dir", "art",
            "--overwrite", "--print-summary", "--repo", "/repo",
        ])
        assert args.template == "t.md"
        assert args.artifacts_dir == "art"
        assert args.overwrite is True
        assert args.print_summary is True
        assert args.repo == "/repo"


# ---------------------------------------------------------------------------
# main() exit codes
# ---------------------------------------------------------------------------


class TestMainExitCodes:
    """Test exit codes from main().

    compile_plan is imported lazily inside _run_compile, so we patch
    at the source module: planner.compiler.compile_plan.
    """

    def test_no_command_returns_1(self):
        assert main([]) == 1

    @patch("planner.compiler.compile_plan")
    def test_success_returns_0(self, mock_compile, tmp_path):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        mock_compile.return_value = _success_result(outdir, artdir)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])
        assert code == 0

    @patch("planner.compiler.compile_plan")
    def test_validation_error_returns_2(self, mock_compile, tmp_path):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        mock_compile.return_value = _validation_error_result(outdir, artdir)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])
        assert code == 2

    @patch("planner.compiler.compile_plan")
    def test_parse_error_returns_4(self, mock_compile, tmp_path):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        mock_compile.return_value = _parse_error_result(outdir, artdir)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])
        assert code == 4

    @patch("planner.compiler.compile_plan")
    def test_runtime_api_error_returns_3(self, mock_compile, tmp_path):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")

        mock_compile.side_effect = RuntimeError("OpenAI API returned 500")

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])
        assert code == 3

    @patch("planner.compiler.compile_plan")
    def test_file_not_found_returns_1(self, mock_compile, tmp_path):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")

        mock_compile.side_effect = FileNotFoundError("no such file")

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])
        assert code == 1

    @patch("planner.compiler.compile_plan")
    def test_print_summary_flag(self, mock_compile, tmp_path, capsys):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        mock_compile.return_value = _success_result(outdir, artdir)

        main(["compile", "--spec", str(spec), "--outdir", outdir,
              "--template", str(spec), "--artifacts-dir", artdir,
              "--print-summary"])

        captured = capsys.readouterr().out
        assert "WO-01" in captured
        assert "Test" in captured

    @patch("planner.compiler.compile_plan")
    def test_file_exists_error_returns_1(self, mock_compile, tmp_path):
        """FileExistsError (overwrite refused) returns exit code 1."""
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        mock_compile.side_effect = FileExistsError("already exists")

        code = main(["compile", "--spec", str(spec), "--outdir", "out",
                      "--template", str(spec), "--artifacts-dir", "art"])
        assert code == 1

    @patch("planner.compiler.compile_plan")
    def test_generic_exception_returns_3(self, mock_compile, tmp_path):
        """Unexpected exceptions (transport errors etc.) return exit code 3."""
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        mock_compile.side_effect = ConnectionError("network down")

        code = main(["compile", "--spec", str(spec), "--outdir", "out",
                      "--template", str(spec), "--artifacts-dir", "art"])
        assert code == 3

    @patch("planner.compiler.compile_plan")
    def test_runtime_error_without_api_returns_1(self, mock_compile, tmp_path):
        """RuntimeError without 'API'/'OPENAI' in message returns 1, not 3."""
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        mock_compile.side_effect = RuntimeError("something unrelated broke")

        code = main(["compile", "--spec", str(spec), "--outdir", "out",
                      "--template", str(spec), "--artifacts-dir", "art"])
        assert code == 1


# ---------------------------------------------------------------------------
# Console output content
# ---------------------------------------------------------------------------


class TestConsoleOutput:
    """Verify console output structure (stdout/stderr) for different scenarios."""

    @patch("planner.compiler.compile_plan")
    def test_success_output_shows_hash_and_count(self, mock_compile, tmp_path, capsys):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        mock_compile.return_value = _success_result(outdir, artdir)

        main(["compile", "--spec", str(spec), "--outdir", outdir,
              "--template", str(spec), "--artifacts-dir", artdir])

        out = capsys.readouterr().out
        assert "abc123" in out  # compile hash
        assert "1" in out  # work order count

    @patch("planner.compiler.compile_plan")
    def test_validation_error_shows_see_path(self, mock_compile, tmp_path, capsys):
        """When validation_errors.json exists, 'See:' line should appear."""
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)
        # Create the file that _find_validation_errors looks for
        (tmp_path / "out" / "validation_errors.json").write_text("[]")

        mock_compile.return_value = _validation_error_result(outdir, artdir)

        main(["compile", "--spec", str(spec), "--outdir", outdir,
              "--template", str(spec), "--artifacts-dir", artdir])

        out = capsys.readouterr().out
        assert "See:" in out

    @patch("planner.compiler.compile_plan")
    def test_error_output_on_stderr(self, mock_compile, tmp_path, capsys):
        """Exception errors go to stderr, not stdout."""
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        mock_compile.side_effect = FileNotFoundError("spec.txt not found")

        main(["compile", "--spec", str(spec), "--outdir", "out",
              "--template", str(spec), "--artifacts-dir", "art"])

        captured = capsys.readouterr()
        assert "ERROR" in captured.err
        assert "spec.txt" in captured.err
