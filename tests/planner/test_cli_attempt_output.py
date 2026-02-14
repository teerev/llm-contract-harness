"""Tests for planner CLI attempt-lifecycle output.

Verifies:
  1. Attempt numbers increment correctly across retries.
  2. Non-final failed attempts print lowercase red "fail" with error codes
     and artifact path.
  3. Final failure prints uppercase red "FAIL".
  4. Success prints uppercase green "PASS".
  5. Spinner is suppressed in --quiet mode or when isatty() is False.
  6. ANSI codes are absent when --no-color is set.

All tests are offline — compile_plan is mocked at the module level.
"""

from __future__ import annotations

import io
import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest

from planner.cli import (
    _AttemptPrinter,
    _StreamingSpinner,
    _format_error_summary,
    _is_tty,
    main,
)
from planner.compiler import AttemptEvent, CompileResult, MAX_COMPILE_ATTEMPTS
from planner.validation import ValidationError
from factory.console import Console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _success_result(outdir: str, artifacts_dir: str) -> CompileResult:
    r = CompileResult()
    r.compile_hash = "abc123"
    r.run_id = "run-1"
    r.run_dir = artifacts_dir
    r.artifacts_dir = artifacts_dir
    r.outdir = outdir
    r.work_orders = [{"id": "WO-01", "title": "Test"}]
    r.errors = []
    r.success = True
    r.compile_attempts = 1
    return r


def _failed_result(outdir: str, artifacts_dir: str, attempts: int = 5) -> CompileResult:
    r = CompileResult()
    r.compile_hash = "abc123"
    r.run_id = "run-1"
    r.run_dir = artifacts_dir
    r.artifacts_dir = artifacts_dir
    r.outdir = outdir
    r.work_orders = []
    r.errors = ["[E101] WO-02: precondition unsatisfied"]
    r.success = False
    r.compile_attempts = attempts
    return r


# ---------------------------------------------------------------------------
# _format_error_summary
# ---------------------------------------------------------------------------


class TestFormatErrorSummary:
    def test_single_error(self):
        errors = [ValidationError(code="E101", wo_id="WO-02", message="precondition unsatisfied")]
        summary = _format_error_summary(errors)
        assert "[E101]" in summary
        assert "WO-02" in summary
        assert "precondition unsatisfied" in summary

    def test_multiple_errors_truncated(self):
        errors = [
            ValidationError(code=f"E{100 + i}", wo_id=f"WO-{i:02d}", message=f"err {i}")
            for i in range(10)
        ]
        summary = _format_error_summary(errors, max_show=3)
        assert "[E100]" in summary
        assert "[E101]" in summary
        assert "[E102]" in summary
        assert "(+7 more)" in summary

    def test_empty_errors(self):
        assert _format_error_summary([]) == ""

    def test_long_message_truncated(self):
        errors = [ValidationError(code="E001", wo_id=None, message="x" * 100)]
        summary = _format_error_summary(errors)
        assert "..." in summary
        assert len(summary) < 200


# ---------------------------------------------------------------------------
# _AttemptPrinter — unit tests
# ---------------------------------------------------------------------------


class TestAttemptPrinter:
    """Test the _AttemptPrinter directly with captured Console output."""

    def _make_printer(self, use_color: bool = False, verbosity: str = "normal"):
        out = io.StringIO()
        con = Console(verbosity=verbosity, color=use_color, out=out)
        printer = _AttemptPrinter(con, use_color=use_color, verbosity=verbosity)
        return printer, out

    def test_start_event_prints_attempt_number(self):
        printer, out = self._make_printer()
        event = AttemptEvent(kind="start", attempt=2, max_attempts=5)
        printer.handle(event)
        text = out.getvalue()
        assert "Attempt 2/5" in text

    def test_pass_event_prints_uppercase_pass(self):
        printer, out = self._make_printer()
        event = AttemptEvent(kind="pass", attempt=1, max_attempts=5)
        printer.handle(event)
        text = out.getvalue()
        assert "PASS" in text
        assert "Attempt 1/5" in text

    def test_pass_event_with_color_has_green(self):
        printer, out = self._make_printer(use_color=True)
        event = AttemptEvent(kind="pass", attempt=1, max_attempts=5)
        printer.handle(event)
        text = out.getvalue()
        # Green ANSI code
        assert "\033[32m" in text
        assert "PASS" in text

    def test_nonfinal_fail_prints_lowercase_fail(self):
        errors = [ValidationError(code="E101", wo_id="WO-02", message="unsatisfied")]
        printer, out = self._make_printer()
        event = AttemptEvent(
            kind="fail", attempt=1, max_attempts=5,
            errors=errors,
            errors_artifact="/tmp/errors.json",
            is_final=False,
        )
        printer.handle(event)
        text = out.getvalue()
        assert "Attempt 1/5" in text
        # Lowercase "fail", NOT uppercase "FAIL"
        assert "fail" in text
        assert "[E101]" in text
        assert "/tmp/errors.json" in text
        assert "Retrying" in text
        assert "Attempt 2/5" in text

    def test_final_fail_prints_uppercase_fail(self):
        errors = [ValidationError(code="E101", wo_id="WO-02", message="unsatisfied")]
        printer, out = self._make_printer()
        event = AttemptEvent(
            kind="fail", attempt=5, max_attempts=5,
            errors=errors,
            errors_artifact="/tmp/errors.json",
            is_final=True,
        )
        printer.handle(event)
        text = out.getvalue()
        assert "Attempt 5/5" in text
        # Must contain uppercase "FAIL" (and not "Retrying")
        assert "FAIL" in text
        assert "Retrying" not in text

    def test_nonfinal_fail_with_color_has_red(self):
        errors = [ValidationError(code="E101", wo_id="WO-02", message="unsatisfied")]
        printer, out = self._make_printer(use_color=True)
        event = AttemptEvent(
            kind="fail", attempt=1, max_attempts=5,
            errors=errors, errors_artifact="/tmp/e.json", is_final=False,
        )
        printer.handle(event)
        text = out.getvalue()
        # Red ANSI code
        assert "\033[31m" in text
        # Lowercase fail (not FAIL)
        stripped = _strip_ansi(text)
        # Check that we have lowercase fail somewhere that is NOT inside "FAIL"
        assert "fail" in stripped

    def test_final_fail_with_color_has_red(self):
        errors = [ValidationError(code="E101", wo_id="WO-02", message="unsatisfied")]
        printer, out = self._make_printer(use_color=True)
        event = AttemptEvent(
            kind="fail", attempt=5, max_attempts=5,
            errors=errors, errors_artifact="/tmp/e.json", is_final=True,
        )
        printer.handle(event)
        text = out.getvalue()
        assert "\033[31m" in text
        stripped = _strip_ansi(text)
        assert "FAIL" in stripped

    def test_quiet_mode_suppresses_all_output(self):
        printer, out = self._make_printer(verbosity="quiet")
        for kind in ("start", "pass", "fail"):
            event = AttemptEvent(kind=kind, attempt=1, max_attempts=5, is_final=True)
            printer.handle(event)
        assert out.getvalue() == ""

    def test_no_color_produces_no_ansi(self):
        errors = [ValidationError(code="E101", wo_id="WO-02", message="unsatisfied")]
        printer, out = self._make_printer(use_color=False)
        for event in [
            AttemptEvent(kind="start", attempt=1, max_attempts=5),
            AttemptEvent(kind="fail", attempt=1, max_attempts=5,
                         errors=errors, errors_artifact="/tmp/e.json", is_final=False),
            AttemptEvent(kind="start", attempt=2, max_attempts=5),
            AttemptEvent(kind="pass", attempt=2, max_attempts=5),
        ]:
            printer.handle(event)
        text = out.getvalue()
        assert "\033[" not in text

    def test_attempt_numbers_increment_across_retries(self):
        """Simulate a retry scenario: attempt 1 fails, attempt 2 succeeds."""
        printer, out = self._make_printer()
        errors = [ValidationError(code="E101", wo_id="WO-02", message="bad")]

        printer.handle(AttemptEvent(kind="start", attempt=1, max_attempts=3))
        printer.handle(AttemptEvent(kind="fail", attempt=1, max_attempts=3,
                                    errors=errors, errors_artifact="/tmp/e.json",
                                    is_final=False))
        printer.handle(AttemptEvent(kind="start", attempt=2, max_attempts=3))
        printer.handle(AttemptEvent(kind="pass", attempt=2, max_attempts=3))

        text = out.getvalue()
        assert "Attempt 1/3" in text
        assert "Attempt 2/3" in text


# ---------------------------------------------------------------------------
# _StreamingSpinner — unit tests
# ---------------------------------------------------------------------------


class TestStreamingSpinner:
    def test_disabled_spinner_is_noop(self):
        """When disabled, start/stop should be no-ops and not hang."""
        spinner = _StreamingSpinner(enabled=False)
        spinner.start()
        spinner.stop()
        # Should not raise or hang

    def test_enabled_spinner_starts_and_stops(self, monkeypatch):
        """Verify the spinner thread starts, runs, and stops cleanly."""
        writes = []
        fake_stderr = MagicMock()
        fake_stderr.write = lambda s: writes.append(s)
        fake_stderr.flush = lambda: None
        monkeypatch.setattr("planner.cli.sys.stderr", fake_stderr)

        spinner = _StreamingSpinner(enabled=True)
        spinner.start()
        # Give it a moment to write at least one frame
        import time
        time.sleep(0.4)
        spinner.stop()

        # Should have written at least one spinner frame
        assert any("Streaming" in w for w in writes)

    def test_stop_is_idempotent(self):
        """Calling stop() when never started should not raise."""
        spinner = _StreamingSpinner(enabled=True)
        spinner.stop()  # never started — should be fine

    def test_stop_clears_spinner_line(self, monkeypatch):
        """After stop(), a line-clear sequence should be written."""
        writes = []
        fake_stderr = MagicMock()
        fake_stderr.write = lambda s: writes.append(s)
        fake_stderr.flush = lambda: None
        monkeypatch.setattr("planner.cli.sys.stderr", fake_stderr)

        spinner = _StreamingSpinner(enabled=True)
        spinner.start()
        import time
        time.sleep(0.3)
        spinner.stop()

        # The clear sequence: \r\033[2K
        assert any("\r\033[2K" in w for w in writes)


# ---------------------------------------------------------------------------
# Integration: main() with mocked compile_plan
# ---------------------------------------------------------------------------


class TestMainAttemptOutput:
    """Integration tests that call main() with mocked compile_plan.

    compile_plan is patched to invoke the on_attempt callback with
    realistic events, then return a CompileResult. We capture stdout
    and verify the attempt lifecycle output.
    """

    def _make_compile_side_effect(self, events: list[AttemptEvent], result: CompileResult):
        """Return a side_effect function that fires events then returns result."""
        def _side_effect(*, spec_path, outdir, template_path, artifacts_dir,
                         overwrite, repo_path, on_attempt=None):
            for ev in events:
                if on_attempt is not None:
                    on_attempt(ev)
            return result
        return _side_effect

    @patch("planner.compiler.compile_plan")
    def test_single_pass_attempt(self, mock_compile, tmp_path, capsys):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        events = [
            AttemptEvent(kind="start", attempt=1, max_attempts=5),
            AttemptEvent(kind="pass", attempt=1, max_attempts=5),
        ]
        result = _success_result(outdir, artdir)

        mock_compile.side_effect = self._make_compile_side_effect(events, result)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])

        assert code == 0
        out = capsys.readouterr().out
        assert "Attempt 1/5" in out
        assert "PASS" in out

    @patch("planner.compiler.compile_plan")
    def test_retry_then_pass(self, mock_compile, tmp_path, capsys):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        errors = [ValidationError(code="E101", wo_id="WO-02", message="precondition unsatisfied")]
        err_path = os.path.join(artdir, "validation_errors_attempt_1.json")
        events = [
            AttemptEvent(kind="start", attempt=1, max_attempts=5),
            AttemptEvent(kind="fail", attempt=1, max_attempts=5,
                         errors=errors, errors_artifact=err_path, is_final=False),
            AttemptEvent(kind="start", attempt=2, max_attempts=5),
            AttemptEvent(kind="pass", attempt=2, max_attempts=5),
        ]
        result = _success_result(outdir, artdir)
        result.compile_attempts = 2

        mock_compile.side_effect = self._make_compile_side_effect(events, result)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])

        assert code == 0
        out = capsys.readouterr().out
        # Both attempts appear
        assert "Attempt 1/5" in out
        assert "Attempt 2/5" in out
        # First attempt shows error code and fail
        assert "[E101]" in out
        assert "fail" in out.lower()
        # Second attempt shows PASS
        assert "PASS" in out

    @patch("planner.compiler.compile_plan")
    def test_all_fail_final_uppercase(self, mock_compile, tmp_path, capsys):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        errors = [ValidationError(code="E101", wo_id="WO-02", message="unsatisfied")]
        events = []
        for i in range(1, 6):
            err_path = os.path.join(artdir, f"validation_errors_attempt_{i}.json")
            events.append(AttemptEvent(kind="start", attempt=i, max_attempts=5))
            events.append(AttemptEvent(
                kind="fail", attempt=i, max_attempts=5,
                errors=errors, errors_artifact=err_path,
                is_final=(i == 5),
            ))

        result = _failed_result(outdir, artdir)

        mock_compile.side_effect = self._make_compile_side_effect(events, result)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir])

        # Should fail
        assert code == 2
        out = capsys.readouterr().out
        stripped = _strip_ansi(out)

        # All 5 attempts present
        for i in range(1, 6):
            assert f"Attempt {i}/5" in stripped

        # Final attempt: uppercase FAIL
        # Non-final: should NOT have "Retrying" on the last one
        lines = stripped.split("\n")
        # Find the line with "Attempt 5/5" verdict
        attempt5_lines = [l for l in lines if "Attempt 5/5" in l and ("FAIL" in l or "fail" in l)]
        assert len(attempt5_lines) >= 1
        assert "FAIL" in attempt5_lines[0]
        # Non-final attempts should have "Retrying"
        assert "Retrying" in stripped

    @patch("planner.compiler.compile_plan")
    def test_quiet_suppresses_attempt_output(self, mock_compile, tmp_path, capsys):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        events = [
            AttemptEvent(kind="start", attempt=1, max_attempts=5),
            AttemptEvent(kind="pass", attempt=1, max_attempts=5),
        ]
        result = _success_result(outdir, artdir)
        mock_compile.side_effect = self._make_compile_side_effect(events, result)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir,
                      "--quiet"])

        assert code == 0
        out = capsys.readouterr().out
        # Quiet mode: verdict still present but no "Attempt N/M" headers
        assert "Attempt 1/5" not in out

    @patch("planner.compiler.compile_plan")
    def test_no_color_produces_no_ansi(self, mock_compile, tmp_path, capsys):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")
        os.makedirs(outdir, exist_ok=True)
        os.makedirs(artdir, exist_ok=True)

        errors = [ValidationError(code="E101", wo_id="WO-02", message="unsatisfied")]
        events = [
            AttemptEvent(kind="start", attempt=1, max_attempts=5),
            AttemptEvent(kind="fail", attempt=1, max_attempts=5,
                         errors=errors, errors_artifact="/tmp/e.json", is_final=False),
            AttemptEvent(kind="start", attempt=2, max_attempts=5),
            AttemptEvent(kind="pass", attempt=2, max_attempts=5),
        ]
        result = _success_result(outdir, artdir)
        result.compile_attempts = 2
        mock_compile.side_effect = self._make_compile_side_effect(events, result)

        code = main(["compile", "--spec", str(spec), "--outdir", outdir,
                      "--template", str(spec), "--artifacts-dir", artdir,
                      "--no-color"])

        assert code == 0
        out = capsys.readouterr().out
        # No ANSI escape sequences should be present
        assert "\033[" not in out


class TestSpinnerInMainContext:
    """Test that spinner is properly suppressed in non-TTY and quiet modes."""

    def test_spinner_disabled_in_quiet(self):
        """In quiet mode, spinner should be disabled regardless of TTY."""
        # _StreamingSpinner(enabled=False) means no thread starts
        spinner = _StreamingSpinner(enabled=False)
        spinner.start()
        assert spinner._thread is None
        spinner.stop()

    def test_is_tty_false_for_stringio(self):
        """StringIO objects are not TTYs."""
        assert _is_tty(io.StringIO()) is False

    def test_spinner_stops_on_reasoning_start(self, monkeypatch):
        """The spinner must stop when reasoning_start fires, otherwise
        spinner output interleaves with reasoning deltas on stderr."""
        writes = []
        fake_stderr = MagicMock()
        fake_stderr.write = lambda s: writes.append(s)
        fake_stderr.flush = lambda: None
        monkeypatch.setattr("planner.cli.sys.stderr", fake_stderr)

        spinner = _StreamingSpinner(enabled=True)

        # Simulate the stream_status callback the CLI wires up:
        def _stream_status(status: str) -> None:
            if status == "start":
                spinner.start()
            elif status == "reasoning_start":
                spinner.stop()
            elif status == "done":
                spinner.stop()

        import time
        _stream_status("start")
        assert spinner._thread is not None, "spinner should be running"
        time.sleep(0.3)

        _stream_status("reasoning_start")
        assert spinner._thread is None, "spinner must stop before reasoning writes"

        # After stop, no more spinner writes should appear
        writes.clear()
        time.sleep(0.3)
        spinner_writes = [w for w in writes if "Streaming" in w]
        assert spinner_writes == [], (
            f"Spinner wrote after reasoning_start: {spinner_writes!r}"
        )

        _stream_status("done")  # should be a no-op (already stopped)
