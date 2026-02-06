"""Integration tests for the full graph — routing, artifacts, rollback."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from factory.graph import (
    _finalize_node,
    _route_after_finalize,
    _route_after_se,
    _route_after_tr,
    build_graph,
)
from factory.util import (
    ARTIFACT_ACCEPTANCE_RESULT,
    ARTIFACT_FAILURE_BRIEF,
    ARTIFACT_PROPOSED_WRITES,
    ARTIFACT_RUN_SUMMARY,
    ARTIFACT_SE_PROMPT,
    ARTIFACT_VERIFY_RESULT,
    ARTIFACT_WORK_ORDER,
    ARTIFACT_WRITE_RESULT,
    compute_run_id,
    load_json,
    make_attempt_dir,
    save_json,
)
from factory.workspace import get_baseline_commit, is_clean

from tests.conftest import (
    file_sha256,
    init_git_repo,
    make_valid_proposal_json,
    minimal_work_order,
    write_work_order,
)


# ---------------------------------------------------------------------------
# Routing unit tests (pure, no git)
# ---------------------------------------------------------------------------


class TestRouting:
    def test_se_to_tr_on_success(self):
        assert _route_after_se({"failure_brief": None}) == "tr"

    def test_se_to_finalize_on_failure(self):
        assert _route_after_se({"failure_brief": {"stage": "exception"}}) == "finalize"

    def test_tr_to_po_on_success(self):
        assert _route_after_tr({"failure_brief": None}) == "po"

    def test_tr_to_finalize_on_failure(self):
        assert _route_after_tr({"failure_brief": {"stage": "stale_context"}}) == "finalize"

    def test_finalize_end_on_pass(self):
        result = _route_after_finalize({"verdict": "PASS", "attempt_index": 2, "max_attempts": 2})
        assert result == "__end__"

    def test_finalize_end_on_exhausted(self):
        result = _route_after_finalize({"verdict": "FAIL", "attempt_index": 3, "max_attempts": 2})
        assert result == "__end__"

    def test_finalize_retry_when_attempts_remain(self):
        result = _route_after_finalize({"verdict": "FAIL", "attempt_index": 2, "max_attempts": 2})
        assert result == "se"


# ---------------------------------------------------------------------------
# Full graph integration — PASS path
# ---------------------------------------------------------------------------


class TestFullPassPath:
    def test_pass_path(self, tmp_path):
        """Full PASS: SE → TR → PO → finalize → END."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        os.makedirs(out)

        # Create verify.sh that passes
        scripts_dir = os.path.join(repo, "scripts")
        os.makedirs(scripts_dir)
        with open(os.path.join(scripts_dir, "verify.sh"), "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add verify"], cwd=repo, capture_output=True)

        baseline = get_baseline_commit(repo)
        wo = minimal_work_order()
        run_id = compute_run_id(wo, baseline)

        valid_json = make_valid_proposal_json(repo)

        initial_state = {
            "work_order": wo,
            "repo_root": repo,
            "baseline_commit": baseline,
            "max_attempts": 2,
            "timeout_seconds": 30,
            "llm_model": "test",
            "llm_temperature": 0,
            "out_dir": out,
            "run_id": run_id,
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

        graph = build_graph()

        with patch("factory.llm.complete", return_value=valid_json):
            final = graph.invoke(initial_state)

        assert final["verdict"] == "PASS"
        assert len(final["attempts"]) == 1
        assert final["repo_tree_hash_after"] is not None

        # Artifacts exist
        attempt_dir = make_attempt_dir(out, run_id, 1)
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_SE_PROMPT))
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_PROPOSED_WRITES))
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_WRITE_RESULT))
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_VERIFY_RESULT))
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_ACCEPTANCE_RESULT))

        # File was written to repo
        with open(os.path.join(repo, "hello.txt")) as f:
            assert f.read() == "hello world\n"


# ---------------------------------------------------------------------------
# Full graph — FAIL: acceptance failure + rollback
# ---------------------------------------------------------------------------


class TestAcceptanceFailureAndRollback:
    def test_rollback_on_acceptance_failure(self, tmp_path):
        """Write succeeds, verify passes, acceptance fails → rollback."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        os.makedirs(out)

        # Verify passes
        scripts_dir = os.path.join(repo, "scripts")
        os.makedirs(scripts_dir)
        with open(os.path.join(scripts_dir, "verify.sh"), "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add verify"], cwd=repo, capture_output=True)

        baseline = get_baseline_commit(repo)
        original_content = open(os.path.join(repo, "hello.txt")).read()

        # Acceptance will fail
        wo = minimal_work_order(acceptance_commands=["python -c 'raise SystemExit(1)'"])
        run_id = compute_run_id(wo, baseline)

        valid_json = make_valid_proposal_json(repo)

        initial_state = {
            "work_order": wo,
            "repo_root": repo,
            "baseline_commit": baseline,
            "max_attempts": 1,
            "timeout_seconds": 30,
            "llm_model": "test",
            "llm_temperature": 0,
            "out_dir": out,
            "run_id": run_id,
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

        graph = build_graph()

        with patch("factory.llm.complete", return_value=valid_json):
            final = graph.invoke(initial_state)

        assert final["verdict"] == "FAIL"

        # Repo must be rolled back
        assert is_clean(repo)
        with open(os.path.join(repo, "hello.txt")) as f:
            assert f.read() == original_content

        # Artifacts in out_dir survive rollback (out_dir is outside repo)
        attempt_dir = make_attempt_dir(out, run_id, 1)
        assert os.path.isfile(os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF))
        fb = load_json(os.path.join(attempt_dir, ARTIFACT_FAILURE_BRIEF))
        assert fb["stage"] == "acceptance_failed"


# ---------------------------------------------------------------------------
# Max-attempts stop condition
# ---------------------------------------------------------------------------


class TestMaxAttemptsStop:
    def test_stops_after_max_attempts(self, tmp_path):
        """LLM always returns invalid JSON → stops after max_attempts."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        os.makedirs(out)

        baseline = get_baseline_commit(repo)
        wo = minimal_work_order()
        run_id = compute_run_id(wo, baseline)

        initial_state = {
            "work_order": wo,
            "repo_root": repo,
            "baseline_commit": baseline,
            "max_attempts": 3,
            "timeout_seconds": 30,
            "llm_model": "test",
            "llm_temperature": 0,
            "out_dir": out,
            "run_id": run_id,
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

        graph = build_graph()

        with patch("factory.llm.complete", return_value="INVALID JSON"):
            final = graph.invoke(initial_state)

        assert final["verdict"] == "FAIL"
        assert len(final["attempts"]) == 3

        # Each attempt has its own artifact dir
        for i in range(1, 4):
            ad = make_attempt_dir(out, run_id, i)
            assert os.path.isdir(ad), f"attempt_{i} dir missing"
            assert os.path.isfile(os.path.join(ad, ARTIFACT_SE_PROMPT))

    def test_attempt_index_increments(self, tmp_path):
        """Attempt indices in records should be 1, 2, ..., max_attempts."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        os.makedirs(out)

        baseline = get_baseline_commit(repo)
        wo = minimal_work_order()
        run_id = compute_run_id(wo, baseline)

        initial_state = {
            "work_order": wo,
            "repo_root": repo,
            "baseline_commit": baseline,
            "max_attempts": 2,
            "timeout_seconds": 30,
            "llm_model": "test",
            "llm_temperature": 0,
            "out_dir": out,
            "run_id": run_id,
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

        graph = build_graph()

        with patch("factory.llm.complete", return_value="bad"):
            final = graph.invoke(initial_state)

        indices = [a["attempt_index"] for a in final["attempts"]]
        assert indices == [1, 2]


# ---------------------------------------------------------------------------
# Artifact forensics — stable keys and types
# ---------------------------------------------------------------------------


class TestArtifactForensics:
    def test_run_summary_keys(self, tmp_path):
        """run_summary.json should have the expected stable keys (when written by run.py)."""
        # This test exercises the graph-level output to verify attempt record shapes
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        os.makedirs(out)

        baseline = get_baseline_commit(repo)
        wo = minimal_work_order()
        run_id = compute_run_id(wo, baseline)

        initial_state = {
            "work_order": wo,
            "repo_root": repo,
            "baseline_commit": baseline,
            "max_attempts": 1,
            "timeout_seconds": 30,
            "llm_model": "test",
            "llm_temperature": 0,
            "out_dir": out,
            "run_id": run_id,
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

        graph = build_graph()
        with patch("factory.llm.complete", return_value="bad json"):
            final = graph.invoke(initial_state)

        # Verify attempt record shape
        attempt = final["attempts"][0]
        required_keys = {
            "attempt_index", "baseline_commit", "proposal_path",
            "touched_files", "write_ok", "verify", "acceptance",
            "failure_brief",
        }
        assert required_keys <= set(attempt.keys())
        assert isinstance(attempt["attempt_index"], int)
        assert isinstance(attempt["write_ok"], bool)
        assert isinstance(attempt["verify"], list)
        assert isinstance(attempt["acceptance"], list)

    def test_write_result_keys(self, tmp_path):
        """write_result.json must have keys: write_ok, touched_files, errors."""
        repo = init_git_repo(str(tmp_path / "repo"))
        out = str(tmp_path / "out")
        os.makedirs(out)

        # Trigger a scope violation
        from factory.nodes_tr import tr_node
        from tests.conftest import EMPTY_SHA256

        baseline = get_baseline_commit(repo)
        wo = minimal_work_order()
        proposal = {
            "summary": "bad",
            "writes": [{"path": "nope.txt", "base_sha256": EMPTY_SHA256, "content": "x"}],
        }
        state = {
            "work_order": wo,
            "repo_root": repo,
            "attempt_index": 1,
            "run_id": "test",
            "out_dir": out,
            "proposal": proposal,
        }
        tr_node(state)

        wr = load_json(os.path.join(make_attempt_dir(out, "test", 1), ARTIFACT_WRITE_RESULT))
        assert set(wr.keys()) == {"write_ok", "touched_files", "errors"}
        assert isinstance(wr["write_ok"], bool)
        assert isinstance(wr["touched_files"], list)
        assert isinstance(wr["errors"], list)
