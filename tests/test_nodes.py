"""Tests for SE / TR / PO nodes — patched LLM, real tmp git repos."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from factory.nodes_po import _combined_excerpt, _get_verify_commands, po_node
from factory.nodes_se import se_node
from factory.nodes_tr import tr_node
from factory.schemas import CmdResult, FailureBrief
from factory.util import (
    ARTIFACT_FAILURE_BRIEF,
    ARTIFACT_PROPOSED_WRITES,
    ARTIFACT_RAW_LLM_RESPONSE,
    ARTIFACT_SE_PROMPT,
    ARTIFACT_WRITE_RESULT,
    make_attempt_dir,
    sha256_bytes,
    sha256_file,
)
from factory.workspace import get_baseline_commit

from tests.conftest import (
    EMPTY_SHA256,
    file_sha256,
    init_git_repo,
    make_valid_proposal_json,
    minimal_work_order,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_state(git_repo, out_dir, **overrides):
    """Build a minimal state dict for node tests."""
    wo = minimal_work_order()
    state = {
        "work_order": wo,
        "repo_root": git_repo,
        "baseline_commit": get_baseline_commit(git_repo),
        "max_attempts": 2,
        "timeout_seconds": 30,
        "llm_model": "test-model",
        "llm_temperature": 0,
        "out_dir": out_dir,
        "run_id": "testrun",
        "attempt_index": 1,
        "proposal": None,
        "touched_files": [],
        "write_ok": False,
        "failure_brief": None,
        "verify_results": [],
        "acceptance_results": [],
        "attempts": [],
        "verdict": "",
        "repo_tree_hash_after": None,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# _combined_excerpt
# ---------------------------------------------------------------------------


class TestCombinedExcerpt:
    def _cr(self, stdout="", stderr=""):
        return CmdResult(
            command=["test"],
            exit_code=1,
            stdout_trunc=stdout,
            stderr_trunc=stderr,
            stdout_path="/tmp/o",
            stderr_path="/tmp/e",
            duration_seconds=0.1,
        )

    def test_both_streams(self):
        r = _combined_excerpt(self._cr(stdout="out", stderr="err"))
        assert r == "[stderr]\nerr\n[stdout]\nout"

    def test_stderr_only(self):
        r = _combined_excerpt(self._cr(stderr="err"))
        assert r == "[stderr]\nerr"

    def test_stdout_only(self):
        r = _combined_excerpt(self._cr(stdout="out"))
        assert r == "[stdout]\nout"

    def test_neither(self):
        r = _combined_excerpt(self._cr())
        assert r == ""


# ---------------------------------------------------------------------------
# _get_verify_commands
# ---------------------------------------------------------------------------


class TestGetVerifyCommands:
    def test_with_verify_script(self, git_repo):
        os.makedirs(os.path.join(git_repo, "scripts"), exist_ok=True)
        with open(os.path.join(git_repo, "scripts", "verify.sh"), "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        cmds = _get_verify_commands(git_repo)
        assert cmds == [["bash", "scripts/verify.sh"]]

    def test_without_verify_script(self, git_repo):
        cmds = _get_verify_commands(git_repo)
        assert len(cmds) == 3
        assert cmds[0] == ["python", "-m", "compileall", "-q", "."]


# ---------------------------------------------------------------------------
# SE node
# ---------------------------------------------------------------------------


class TestSENode:
    def test_valid_proposal(self, git_repo, out_dir):
        """SE node produces a valid proposal when LLM returns valid JSON."""
        valid_json = make_valid_proposal_json(git_repo)
        state = _base_state(git_repo, out_dir)

        with patch("factory.llm.complete", return_value=valid_json):
            result = se_node(state)

        assert result["proposal"] is not None
        assert result["failure_brief"] is None
        assert result["write_ok"] is False  # SE never sets write_ok=True

        # Artifacts
        attempt_dir = make_attempt_dir(out_dir, "testrun", 1)
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_SE_PROMPT))
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_PROPOSED_WRITES))

    def test_llm_exception(self, git_repo, out_dir):
        """SE node handles LLM exception with stage='exception'."""
        state = _base_state(git_repo, out_dir)

        with patch("factory.llm.complete", side_effect=RuntimeError("API down")):
            result = se_node(state)

        assert result["proposal"] is None
        assert result["failure_brief"]["stage"] == "exception"
        assert "API down" in result["failure_brief"]["primary_error_excerpt"]

        # Write-ahead artifact
        attempt_dir = make_attempt_dir(out_dir, "testrun", 1)
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF))

    def test_llm_invalid_json(self, git_repo, out_dir):
        """SE node handles malformed LLM output with stage='llm_output_invalid'."""
        state = _base_state(git_repo, out_dir)

        with patch("factory.llm.complete", return_value="not json at all!!!"):
            result = se_node(state)

        assert result["proposal"] is None
        assert result["failure_brief"]["stage"] == "llm_output_invalid"

        attempt_dir = make_attempt_dir(out_dir, "testrun", 1)
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_RAW_LLM_RESPONSE))
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF))

    def test_prompt_file_created(self, git_repo, out_dir):
        """se_prompt.txt is always created, even on LLM failure."""
        state = _base_state(git_repo, out_dir)

        with patch("factory.llm.complete", side_effect=RuntimeError("boom")):
            se_node(state)

        attempt_dir = make_attempt_dir(out_dir, "testrun", 1)
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_SE_PROMPT))

    def test_previous_failure_brief_in_prompt(self, git_repo, out_dir):
        """When retrying, the prompt should include the previous failure brief."""
        prev_fb = FailureBrief(
            stage="verify_failed",
            command="pytest",
            exit_code=1,
            primary_error_excerpt="test failed",
            constraints_reminder="fix tests",
        )
        state = _base_state(
            git_repo, out_dir,
            failure_brief=prev_fb.model_dump(),
            attempt_index=2,
        )

        valid_json = make_valid_proposal_json(git_repo)
        with patch("factory.llm.complete", return_value=valid_json) as mock_llm:
            se_node(state)

        prompt = mock_llm.call_args[1]["prompt"]
        assert "Previous Attempt FAILED" in prompt
        assert "verify_failed" in prompt


# ---------------------------------------------------------------------------
# TR node
# ---------------------------------------------------------------------------


class TestTRNode:
    def _make_proposal(self, git_repo, path="hello.txt", content="new content\n"):
        """Build a valid proposal dict with correct base hash."""
        h = file_sha256(os.path.join(git_repo, path))
        return {
            "summary": "change",
            "writes": [{"path": path, "base_sha256": h, "content": content}],
        }

    def test_valid_write(self, git_repo, out_dir):
        """TR applies writes and creates write_result.json with write_ok=True."""
        proposal = self._make_proposal(git_repo)
        state = _base_state(git_repo, out_dir, proposal=proposal)

        result = tr_node(state)

        assert result["write_ok"] is True
        assert result["failure_brief"] is None
        assert "hello.txt" in result["touched_files"]

        # File was actually written
        with open(os.path.join(git_repo, "hello.txt")) as f:
            assert f.read() == "new content\n"

        # Artifact
        attempt_dir = make_attempt_dir(out_dir, "testrun", 1)
        wr = json.loads(open(os.path.join(attempt_dir, ARTIFACT_WRITE_RESULT)).read())
        assert wr["write_ok"] is True

    def test_scope_violation(self, git_repo, out_dir):
        """TR rejects writes to files not in allowed_files."""
        proposal = {
            "summary": "bad",
            "writes": [{"path": "forbidden.txt", "base_sha256": EMPTY_SHA256, "content": "x"}],
        }
        state = _base_state(git_repo, out_dir, proposal=proposal)

        result = tr_node(state)

        assert result["write_ok"] is False
        assert result["failure_brief"]["stage"] == "write_scope_violation"
        assert not os.path.exists(os.path.join(git_repo, "forbidden.txt"))

    def test_stale_context(self, git_repo, out_dir):
        """TR rejects writes with wrong base_sha256."""
        proposal = {
            "summary": "stale",
            "writes": [{"path": "hello.txt", "base_sha256": "wrong_hash", "content": "x"}],
        }
        state = _base_state(git_repo, out_dir, proposal=proposal)

        result = tr_node(state)

        assert result["write_ok"] is False
        assert result["failure_brief"]["stage"] == "stale_context"

    def test_duplicate_paths_rejected(self, git_repo, out_dir):
        """TR rejects proposals with duplicate file paths."""
        h = file_sha256(os.path.join(git_repo, "hello.txt"))
        proposal = {
            "summary": "dup",
            "writes": [
                {"path": "hello.txt", "base_sha256": h, "content": "a"},
                {"path": "hello.txt", "base_sha256": h, "content": "b"},
            ],
        }
        state = _base_state(git_repo, out_dir, proposal=proposal)

        result = tr_node(state)

        assert result["write_ok"] is False
        assert result["failure_brief"]["stage"] == "write_scope_violation"
        assert "Duplicate" in result["failure_brief"]["primary_error_excerpt"]

    def test_write_failure(self, git_repo, out_dir):
        """TR handles atomic write failures with stage='write_failed'."""
        proposal = self._make_proposal(git_repo)
        state = _base_state(git_repo, out_dir, proposal=proposal)

        with patch("factory.nodes_tr._atomic_write", side_effect=OSError("disk full")):
            result = tr_node(state)

        assert result["write_ok"] is False
        assert result["failure_brief"]["stage"] == "write_failed"


# ---------------------------------------------------------------------------
# PO node
# ---------------------------------------------------------------------------


class TestPONode:
    def _po_state(self, git_repo, out_dir, acceptance_cmds=None):
        """State for PO node — verify script exists, acceptance configurable."""
        # Create a verify script that passes
        scripts_dir = os.path.join(git_repo, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        with open(os.path.join(scripts_dir, "verify.sh"), "w") as f:
            f.write("#!/bin/bash\nexit 0\n")

        if acceptance_cmds is None:
            acceptance_cmds = ["python -c 'print(1)'"]

        wo = minimal_work_order(acceptance_commands=acceptance_cmds)
        return _base_state(git_repo, out_dir, work_order=wo)

    def test_all_pass(self, git_repo, out_dir):
        state = self._po_state(git_repo, out_dir)
        result = po_node(state)

        assert result["failure_brief"] is None
        assert len(result["verify_results"]) > 0
        assert len(result["acceptance_results"]) > 0

    def test_verify_failure(self, git_repo, out_dir):
        """PO catches verify command failures."""
        scripts_dir = os.path.join(git_repo, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        with open(os.path.join(scripts_dir, "verify.sh"), "w") as f:
            f.write("#!/bin/bash\necho FAIL >&2\nexit 1\n")

        wo = minimal_work_order()
        state = _base_state(git_repo, out_dir, work_order=wo)
        result = po_node(state)

        assert result["failure_brief"] is not None
        assert result["failure_brief"]["stage"] == "verify_failed"
        assert result["failure_brief"]["exit_code"] == 1

    def test_acceptance_failure(self, git_repo, out_dir):
        state = self._po_state(
            git_repo, out_dir,
            acceptance_cmds=["python -c 'raise SystemExit(1)'"],
        )
        result = po_node(state)

        assert result["failure_brief"] is not None
        assert result["failure_brief"]["stage"] == "acceptance_failed"
