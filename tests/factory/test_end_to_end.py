"""End-to-end tests through run_cli — the highest-fidelity integration tests.

These tests address the adversarial review's core finding: the path through
run.py was completely untested.  Each test calls run_cli() directly (not
graph.invoke()) so the full pipeline is exercised:

    __main__.py arg parsing → run_cli() → preflight → build_graph →
    graph.invoke → nodes → finalize → summary writing → exit code
"""

from __future__ import annotations

import argparse
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from factory.run import run_cli
from factory.util import (
    ARTIFACT_FAILURE_BRIEF,
    ARTIFACT_RUN_SUMMARY,
    ARTIFACT_WORK_ORDER,
    ARTIFACT_WRITE_RESULT,
    load_json,
)
from factory.workspace import is_clean

from tests.factory.conftest import (
    add_verify_script,
    file_sha256,
    init_git_repo,
    init_multi_file_git_repo,
    make_multi_file_proposal_json,
    make_valid_proposal_json,
    write_work_order,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(repo: str, wo_path: str, out: str, **overrides) -> argparse.Namespace:
    """Build an argparse.Namespace matching __main__.py's argument structure."""
    defaults = {
        "repo": repo,
        "work_order": wo_path,
        "out": out,
        "max_attempts": 2,
        "llm_model": "test-model",
        "llm_temperature": 0,
        "timeout_seconds": 30,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Action 1: True end-to-end CLI test — PASS path through run_cli
# ---------------------------------------------------------------------------


class TestEndToEndPassViaCLI:
    """Adversarial review §1/§6.1: run.py is untested for normal execution.

    This test goes through: run_cli → preflight → build_graph → graph.invoke
    → SE → TR → PO → finalize → summary write → return (exit 0).
    """

    def test_pass_path_via_run_cli(self, tmp_path, capsys):
        # --- Setup: real git repo with verify.sh committed ---
        repo = init_git_repo(str(tmp_path / "repo"))
        add_verify_script(repo)

        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        args = _make_args(repo, wo_path, out)
        valid_json = make_valid_proposal_json(repo)

        # --- Execute through run_cli (NOT graph.invoke) ---
        with patch("factory.llm.complete", return_value=valid_json):
            run_cli(args)  # PASS returns normally — no SystemExit

        # --- Assert stdout ---
        captured = capsys.readouterr()
        assert "Verdict: PASS" in captured.out
        assert "Run summary:" in captured.out

        # --- Assert run_summary.json ON DISK (not from graph state) ---
        run_dirs = os.listdir(out)
        assert len(run_dirs) == 1
        run_dir = os.path.join(out, run_dirs[0])

        summary = load_json(os.path.join(run_dir, ARTIFACT_RUN_SUMMARY))

        # Stable top-level keys and types
        assert isinstance(summary["run_id"], str)
        assert len(summary["run_id"]) == 16
        assert summary["work_order_id"] == "test-wo-1"
        assert summary["verdict"] == "PASS"
        assert isinstance(summary["total_attempts"], int)
        assert summary["total_attempts"] == 1
        assert isinstance(summary["baseline_commit"], str)
        assert len(summary["baseline_commit"]) == 40
        assert summary["repo_tree_hash_after"] is not None
        assert isinstance(summary["config"], dict)
        assert "llm_model" in summary["config"]
        assert isinstance(summary["attempts"], list)
        assert len(summary["attempts"]) == 1

        # Attempt record shape (from disk, not graph state)
        attempt = summary["attempts"][0]
        assert attempt["attempt_index"] == 1
        assert attempt["write_ok"] is True
        assert isinstance(attempt["touched_files"], list)
        assert len(attempt["touched_files"]) > 0
        assert isinstance(attempt["verify"], list)
        assert isinstance(attempt["acceptance"], list)
        assert attempt["failure_brief"] is None

        # work_order.json artifact also written by run.py
        assert os.path.isfile(os.path.join(run_dir, ARTIFACT_WORK_ORDER))


# ---------------------------------------------------------------------------
# Action 2: Exit code 2 — emergency handler (last-resort safety net)
# ---------------------------------------------------------------------------


class TestEmergencyHandler:
    """Adversarial review §1/§2.7: exit-code-2 handler is completely dark.

    Forces graph.invoke() to raise an unhandled exception and verifies
    the emergency handler: exit code 2, rollback attempted, ERROR summary
    written, stderr contains the verdict.
    """

    def test_exit_code_2_on_graph_crash(self, tmp_path, capsys):
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        args = _make_args(repo, wo_path, out, max_attempts=1)

        # Patch build_graph to return a mock graph that raises on invoke.
        # This is the cleanest way to force the emergency path without
        # depending on LangGraph's internal exception propagation.
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("unexpected crash in graph")

        with patch("factory.run.build_graph", return_value=mock_graph):
            with pytest.raises(SystemExit) as exc_info:
                run_cli(args)

        assert exc_info.value.code == 2

        # --- Assert stderr contains emergency verdict ---
        captured = capsys.readouterr()
        assert "Verdict: ERROR" in captured.err
        assert "unexpected crash in graph" in captured.err

        # --- Assert run_summary.json written with ERROR verdict ---
        run_dirs = os.listdir(out)
        assert len(run_dirs) == 1
        run_dir = os.path.join(out, run_dirs[0])

        summary = load_json(os.path.join(run_dir, ARTIFACT_RUN_SUMMARY))
        assert summary["verdict"] == "ERROR"
        assert summary["total_attempts"] == 0
        assert "unexpected crash in graph" in summary.get("error", "")
        assert "error_traceback" in summary
        assert isinstance(summary["error_traceback"], str)
        assert isinstance(summary["attempts"], list)
        assert len(summary["attempts"]) == 0

        # Repo should be clean (best-effort rollback)
        assert is_clean(repo)


# ---------------------------------------------------------------------------
# Action 3: M-02 — BaseException (KeyboardInterrupt) triggers rollback
# ---------------------------------------------------------------------------


class TestBaseExceptionRollback:
    """M-02: KeyboardInterrupt during graph execution must trigger rollback,
    write an emergency summary, and exit with code 130 (standard SIGINT)."""

    def test_keyboard_interrupt_triggers_rollback(self, tmp_path, capsys):
        """Ctrl-C during graph.invoke → rollback + ERROR summary + exit 130."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        args = _make_args(repo, wo_path, out, max_attempts=1)

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = KeyboardInterrupt()

        with patch("factory.run.build_graph", return_value=mock_graph):
            with pytest.raises(SystemExit) as exc_info:
                run_cli(args)

        # Exit code 130 = 128 + SIGINT(2)
        assert exc_info.value.code == 130

        # Repo must be clean (rollback was performed)
        assert is_clean(repo)

        # Emergency summary must be written with ERROR verdict
        run_dirs = os.listdir(out)
        assert len(run_dirs) == 1
        run_dir = os.path.join(out, run_dirs[0])

        summary = load_json(os.path.join(run_dir, ARTIFACT_RUN_SUMMARY))
        assert summary["verdict"] == "ERROR"

        # stderr must contain the verdict
        captured = capsys.readouterr()
        assert "Verdict: ERROR" in captured.err

    def test_keyboard_interrupt_with_dirty_repo(self, tmp_path, capsys):
        """If KeyboardInterrupt happens after a file was written, the file
        must be restored by rollback."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        # Record original content
        orig = open(os.path.join(repo, "hello.txt")).read()

        args = _make_args(repo, wo_path, out, max_attempts=1)

        def dirty_then_interrupt(state):
            """Simulate: graph writes a file then KeyboardInterrupt fires."""
            with open(os.path.join(repo, "hello.txt"), "w") as f:
                f.write("DIRTY — partial write from TR\n")
            raise KeyboardInterrupt()

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = dirty_then_interrupt

        with patch("factory.run.build_graph", return_value=mock_graph):
            with pytest.raises(SystemExit) as exc_info:
                run_cli(args)

        assert exc_info.value.code == 130

        # Rollback must have restored the file
        assert is_clean(repo)
        with open(os.path.join(repo, "hello.txt")) as f:
            assert f.read() == orig

    def test_system_exit_preserved(self, tmp_path, capsys):
        """SystemExit from graph.invoke must re-raise with original code,
        not be swallowed or remapped to 2."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        args = _make_args(repo, wo_path, out, max_attempts=1)

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = SystemExit(42)

        with patch("factory.run.build_graph", return_value=mock_graph):
            with pytest.raises(SystemExit) as exc_info:
                run_cli(args)

        # Original exit code must be preserved
        assert exc_info.value.code == 42

        # Repo must still be clean (rollback was performed before re-raise)
        assert is_clean(repo)


# ---------------------------------------------------------------------------
# M-09: rollback_failed field in run summary
# ---------------------------------------------------------------------------


class TestRollbackFailedField:
    """M-09: run_summary.json must contain a machine-readable rollback_failed
    field so downstream tooling can distinguish clean vs dirty repos."""

    def test_rollback_failed_false_on_normal_error(self, tmp_path, capsys):
        """When rollback succeeds, rollback_failed must be False."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        args = _make_args(repo, wo_path, out, max_attempts=1)

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("crash")

        with patch("factory.run.build_graph", return_value=mock_graph):
            with pytest.raises(SystemExit):
                run_cli(args)

        run_dirs = os.listdir(out)
        run_dir = os.path.join(out, run_dirs[0])
        summary = load_json(os.path.join(run_dir, ARTIFACT_RUN_SUMMARY))

        assert summary["rollback_failed"] is False
        assert summary.get("remediation") is None

    def test_rollback_failed_true_when_rollback_raises(self, tmp_path, capsys):
        """When rollback fails, rollback_failed must be True with remediation."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        args = _make_args(repo, wo_path, out, max_attempts=1)

        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = RuntimeError("crash")

        # is_clean must return True for preflight, then False for the
        # post-rollback check inside the emergency handler.
        is_clean_results = iter([True, False])

        with patch("factory.run.build_graph", return_value=mock_graph), \
             patch("factory.run.rollback", side_effect=RuntimeError("locked")), \
             patch("factory.run.is_clean", side_effect=lambda _: next(is_clean_results)):
            with pytest.raises(SystemExit):
                run_cli(args)

        run_dirs = os.listdir(out)
        run_dir = os.path.join(out, run_dirs[0])
        summary = load_json(os.path.join(run_dir, ARTIFACT_RUN_SUMMARY))

        assert summary["rollback_failed"] is True
        assert isinstance(summary["remediation"], str)
        assert "reset --hard" in summary["remediation"]

    def test_rollback_failed_false_on_normal_pass(self, tmp_path, capsys):
        """Normal PASS path also has rollback_failed=False explicitly."""
        repo = init_git_repo(str(tmp_path / "repo"))
        add_verify_script(repo)
        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(wo_path)

        args = _make_args(repo, wo_path, out)
        valid_json = make_valid_proposal_json(repo)

        with patch("factory.llm.complete", return_value=valid_json):
            run_cli(args)

        run_dirs = os.listdir(out)
        run_dir = os.path.join(out, run_dirs[0])
        summary = load_json(os.path.join(run_dir, ARTIFACT_RUN_SUMMARY))

        assert summary["verdict"] == "PASS"
        assert summary["rollback_failed"] is False


# ---------------------------------------------------------------------------
# Action 4: Multi-write failure rollback — atomicity guarantee
# ---------------------------------------------------------------------------


class TestMultiWriteRollback:
    """Adversarial review §3.4/§4: multi-file rollback and atomicity untested.

    Two files are written, acceptance fails, both files must be rolled back.
    """

    def test_multi_write_acceptance_failure_full_rollback(self, tmp_path, capsys):
        """Both hello.txt and second.txt written → acceptance fails → both rolled back."""
        repo = init_multi_file_git_repo(str(tmp_path / "repo"))
        add_verify_script(repo)

        # Record original file contents
        orig_hello = open(os.path.join(repo, "hello.txt")).read()
        orig_second = open(os.path.join(repo, "second.txt")).read()

        out = str(tmp_path / "out")
        wo_path = str(tmp_path / "wo.json")
        write_work_order(
            wo_path,
            allowed_files=["hello.txt", "second.txt"],
            context_files=["hello.txt", "second.txt"],
            acceptance_commands=["python -c 'raise SystemExit(1)'"],
        )

        args = _make_args(repo, wo_path, out, max_attempts=1)
        multi_json = make_multi_file_proposal_json(repo)

        with patch("factory.llm.complete", return_value=multi_json):
            with pytest.raises(SystemExit) as exc_info:
                run_cli(args)

        assert exc_info.value.code == 1  # FAIL verdict

        # --- Assert COMPLETE rollback: no partial writes persist ---
        assert is_clean(repo), "Repo must be clean after rollback"

        with open(os.path.join(repo, "hello.txt")) as f:
            assert f.read() == orig_hello, "hello.txt must be restored"
        with open(os.path.join(repo, "second.txt")) as f:
            assert f.read() == orig_second, "second.txt must be restored"

        # git status --porcelain must be empty
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True,
        )
        assert result.stdout.strip() == b"", "git status must be empty after rollback"

        # --- Artifacts survive (out_dir is outside repo) ---
        run_dirs = os.listdir(out)
        assert len(run_dirs) == 1
        run_dir = os.path.join(out, run_dirs[0])

        summary = load_json(os.path.join(run_dir, ARTIFACT_RUN_SUMMARY))
        assert summary["verdict"] == "FAIL"

        attempt = summary["attempts"][0]
        assert attempt["write_ok"] is True  # writes succeeded before acceptance failed
        assert sorted(attempt["touched_files"]) == ["hello.txt", "second.txt"]
        assert attempt["failure_brief"]["stage"] == "acceptance_failed"
