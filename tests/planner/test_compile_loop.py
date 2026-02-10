"""Tests for planner/compiler.py — compile loop, retry, verify_exempt injection."""

from __future__ import annotations

import copy
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from planner.compiler import (
    MAX_COMPILE_ATTEMPTS,
    CompileResult,
    _build_repo_file_listing,
    _build_revision_prompt,
    compile_plan,
)
from planner.validation import ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A minimal valid plan manifest that passes both structural and chain checks.
_VALID_MANIFEST = {
    "system_overview": ["test project"],
    "verify_contract": {
        "command": "python -m pytest -q",
        "requires": [
            {"kind": "file_exists", "path": "scripts/verify.sh"},
            {"kind": "file_exists", "path": "tests/test_placeholder.py"},
        ],
    },
    "work_orders": [
        {
            "id": "WO-01",
            "title": "Bootstrap verify",
            "intent": "Create verify script.",
            "preconditions": [{"kind": "file_absent", "path": "scripts/verify.sh"}],
            "postconditions": [{"kind": "file_exists", "path": "scripts/verify.sh"}],
            "allowed_files": ["scripts/verify.sh"],
            "forbidden": [],
            "acceptance_commands": [
                'python -c "import os; assert os.path.isfile(\'scripts/verify.sh\')"',
            ],
            "context_files": ["scripts/verify.sh"],
            "notes": None,
        },
        {
            "id": "WO-02",
            "title": "Project skeleton",
            "intent": "Create package and test.",
            "preconditions": [{"kind": "file_exists", "path": "scripts/verify.sh"}],
            "postconditions": [
                {"kind": "file_exists", "path": "mypackage/__init__.py"},
                {"kind": "file_exists", "path": "tests/test_placeholder.py"},
            ],
            "allowed_files": ["mypackage/__init__.py", "tests/test_placeholder.py"],
            "forbidden": [],
            "acceptance_commands": ['python -c "import mypackage"'],
            "context_files": [
                "scripts/verify.sh",
                "mypackage/__init__.py",
                "tests/test_placeholder.py",
            ],
            "notes": None,
        },
    ],
}

# A manifest with a chain error: WO-02 has unsatisfied precondition.
_INVALID_MANIFEST = {
    "system_overview": ["test"],
    "work_orders": [
        {
            "id": "WO-01",
            "title": "First",
            "intent": "First.",
            "preconditions": [],
            "postconditions": [{"kind": "file_exists", "path": "src/a.py"}],
            "allowed_files": ["src/a.py"],
            "forbidden": [],
            "acceptance_commands": ['python -c "assert True"'],
            "context_files": ["src/a.py"],
            "notes": None,
        },
        {
            "id": "WO-02",
            "title": "Second",
            "intent": "Second.",
            "preconditions": [
                {"kind": "file_exists", "path": "src/missing.py"},  # never created
            ],
            "postconditions": [{"kind": "file_exists", "path": "src/b.py"}],
            "allowed_files": ["src/b.py"],
            "forbidden": [],
            "acceptance_commands": ['python -c "assert True"'],
            "context_files": ["src/b.py"],
            "notes": None,
        },
    ],
}


@pytest.fixture()
def spec_file(tmp_path):
    """Write a minimal spec file and return its path."""
    p = str(tmp_path / "spec.txt")
    with open(p, "w") as f:
        f.write("Build a hello world app.\n")
    return p


@pytest.fixture()
def template_file(tmp_path):
    """Write a minimal template with the required placeholder."""
    p = str(tmp_path / "template.md")
    with open(p, "w") as f:
        f.write("Generate work orders for:\n{{PRODUCT_SPEC}}\n")
    return p


@pytest.fixture()
def outdir(tmp_path):
    d = str(tmp_path / "out")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture()
def artifacts_dir(tmp_path):
    d = str(tmp_path / "artifacts")
    os.makedirs(d, exist_ok=True)
    return d


def _mock_client_returning(*responses: str):
    """Return a mock OpenAIResponsesClient whose generate_text returns *responses* in order."""
    mock_client = MagicMock()
    mock_client.generate_text = MagicMock(side_effect=list(responses))
    return mock_client


# ---------------------------------------------------------------------------
# _build_revision_prompt
# ---------------------------------------------------------------------------


class TestBuildRevisionPrompt:
    def test_contains_error_codes(self):
        errors = [
            ValidationError(code="E101", wo_id="WO-02", message="precondition unsatisfied"),
            ValidationError(code="E105", wo_id="WO-01", message="verify in acceptance"),
        ]
        prompt = _build_revision_prompt("my spec", '{"work_orders": []}', errors)
        assert "[E101]" in prompt
        assert "[E105]" in prompt
        assert "WO-02" in prompt
        assert "my spec" in prompt

    def test_contains_previous_response(self):
        errors = [ValidationError(code="E000", wo_id=None, message="bad json")]
        prompt = _build_revision_prompt("spec", "PREV_RESPONSE_HERE", errors)
        assert "PREV_RESPONSE_HERE" in prompt

    def test_contains_fix_instruction(self):
        errors = [ValidationError(code="E001", wo_id="WO-01", message="bad id")]
        prompt = _build_revision_prompt("spec", "{}", errors)
        # Assert the prompt asks the LLM to correct the errors — check for any
        # reasonable phrasing rather than exact wording.
        lower = prompt.lower()
        assert any(w in lower for w in ("fix", "correct", "repair")), (
            "Revision prompt should instruct the LLM to fix/correct errors"
        )

    def test_sections_present(self):
        """Revision prompt should have structured sections for errors, previous response, and spec."""
        errors = [
            ValidationError(code="E001", wo_id="WO-01", message="bad id"),
            ValidationError(code="E003", wo_id="WO-02", message="shell op"),
        ]
        prompt = _build_revision_prompt("THE_SPEC", "THE_PREV_RESPONSE", errors)
        # All errors are represented
        assert "[E001]" in prompt
        assert "[E003]" in prompt
        # Both the previous response and original spec are included
        assert "THE_PREV_RESPONSE" in prompt
        assert "THE_SPEC" in prompt


# ---------------------------------------------------------------------------
# _build_repo_file_listing
# ---------------------------------------------------------------------------


class TestBuildRepoFileListing:
    def test_lists_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x")
        (tmp_path / "README.md").write_text("y")
        result = _build_repo_file_listing(str(tmp_path))
        assert "src/main.py" in result
        assert "README.md" in result

    def test_skips_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("x")
        (tmp_path / "src.py").write_text("y")
        result = _build_repo_file_listing(str(tmp_path))
        assert "src.py" in result
        assert ".git/config" not in result

    def test_skips_pycache(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.cpython-312.pyc").write_text("x")
        result = _build_repo_file_listing(str(tmp_path))
        assert len(result) == 0

    def test_empty_dir(self, tmp_path):
        result = _build_repo_file_listing(str(tmp_path))
        assert result == set()


# ---------------------------------------------------------------------------
# compile_plan — single pass (no retry needed)
# ---------------------------------------------------------------------------


class TestCompileSinglePass:
    @patch("planner.compiler.OpenAIResponsesClient")
    def test_valid_plan_succeeds(self, MockClient, spec_file, template_file,
                                  outdir, artifacts_dir):
        mock_client = _mock_client_returning(json.dumps(_VALID_MANIFEST))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        assert result.errors == []
        assert result.compile_attempts == 1
        assert len(result.work_orders) == 2
        # WO files should be written
        assert os.path.isfile(os.path.join(outdir, "WO-01.json"))
        assert os.path.isfile(os.path.join(outdir, "WO-02.json"))
        # Only one LLM call
        assert mock_client.generate_text.call_count == 1

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_verify_exempt_injected(self, MockClient, spec_file, template_file,
                                     outdir, artifacts_dir):
        """WO-01 should get verify_exempt=True, WO-02 verify_exempt=False."""
        mock_client = _mock_client_returning(json.dumps(_VALID_MANIFEST))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        wo1 = result.work_orders[0]
        wo2 = result.work_orders[1]
        assert wo1["verify_exempt"] is True   # only verify.sh, no tests
        assert wo2["verify_exempt"] is False  # verify.sh + tests = satisfied

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_verify_exempt_written_to_json(self, MockClient, spec_file,
                                            template_file, outdir, artifacts_dir):
        """verify_exempt should appear in the written WO-01.json file."""
        mock_client = _mock_client_returning(json.dumps(_VALID_MANIFEST))
        MockClient.return_value = mock_client

        compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        with open(os.path.join(outdir, "WO-01.json")) as f:
            wo1_disk = json.load(f)
        assert wo1_disk["verify_exempt"] is True

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_compile_with_repo(self, MockClient, spec_file, template_file,
                                outdir, artifacts_dir, tmp_path):
        """--repo flag feeds repo_file_listing into validation."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "existing.py").write_text("x")

        mock_client = _mock_client_returning(json.dumps(_VALID_MANIFEST))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
            repo_path=str(repo),
        )

        # Should still succeed — the valid manifest's preconditions are
        # file_absent(scripts/verify.sh) which is true for a fresh repo
        assert result.success is True

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_warnings_only_still_succeeds(self, MockClient, spec_file,
                                           template_file, outdir, artifacts_dir):
        """A plan with W101 warnings but no hard errors should succeed."""
        manifest_with_warning = copy.deepcopy(_VALID_MANIFEST)
        # Replace WO-02 acceptance with an import whose module isn't created
        manifest_with_warning["work_orders"][1]["acceptance_commands"] = [
            'python -c "from mypackage.solver import Solver"',
        ]
        mock_client = _mock_client_returning(json.dumps(manifest_with_warning))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        assert result.compile_attempts == 1
        assert mock_client.generate_text.call_count == 1
        assert len(result.warnings) > 0
        assert any("W101" in w for w in result.warnings)
        # WO files should still be written despite warnings
        assert os.path.isfile(os.path.join(outdir, "WO-01.json"))
        assert os.path.isfile(os.path.join(outdir, "WO-02.json"))

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_manifest_written_on_success(self, MockClient, spec_file,
                                          template_file, outdir, artifacts_dir):
        """Successful compile writes WORK_ORDERS_MANIFEST.json."""
        mock_client = _mock_client_returning(json.dumps(_VALID_MANIFEST))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        manifest_path = os.path.join(outdir, "WORK_ORDERS_MANIFEST.json")
        assert os.path.isfile(manifest_path)
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert "work_orders" in manifest
        assert len(manifest["work_orders"]) == 2


# ---------------------------------------------------------------------------
# compile_plan — retry loop
# ---------------------------------------------------------------------------


class TestCompileRetry:
    @patch("planner.compiler.OpenAIResponsesClient")
    def test_retry_on_json_parse_error(self, MockClient, spec_file,
                                        template_file, outdir, artifacts_dir):
        """First attempt returns garbage, second returns valid JSON → success."""
        mock_client = _mock_client_returning(
            "NOT VALID JSON!!!",
            json.dumps(_VALID_MANIFEST),
        )
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        assert result.compile_attempts == 2
        assert mock_client.generate_text.call_count == 2
        # Per-attempt artifacts exist
        assert os.path.isfile(
            os.path.join(result.artifacts_dir, "validation_errors_attempt_1.json")
        )

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_retry_on_chain_error(self, MockClient, spec_file, template_file,
                                   outdir, artifacts_dir):
        """First attempt has chain error (E101), second is valid → success."""
        mock_client = _mock_client_returning(
            json.dumps(_INVALID_MANIFEST),  # has E101
            json.dumps(_VALID_MANIFEST),    # clean
        )
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        assert result.compile_attempts == 2
        assert mock_client.generate_text.call_count == 2

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_max_retries_exhausted(self, MockClient, spec_file, template_file,
                                    outdir, artifacts_dir):
        """All attempts return invalid → failure with errors."""
        mock_client = _mock_client_returning(
            *[json.dumps(_INVALID_MANIFEST)] * MAX_COMPILE_ATTEMPTS
        )
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is False
        assert result.compile_attempts == MAX_COMPILE_ATTEMPTS
        assert mock_client.generate_text.call_count == MAX_COMPILE_ATTEMPTS
        assert len(result.errors) > 0
        assert any("E101" in e for e in result.errors)

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_all_json_parse_failures_exhausts(self, MockClient, spec_file,
                                               template_file, outdir, artifacts_dir):
        """All attempts return unparseable JSON → failure after MAX attempts."""
        mock_client = _mock_client_returning(
            *["NOT JSON AT ALL"] * MAX_COMPILE_ATTEMPTS
        )
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is False
        assert result.compile_attempts == MAX_COMPILE_ATTEMPTS
        assert mock_client.generate_text.call_count == MAX_COMPILE_ATTEMPTS
        assert any("JSON parse" in e for e in result.errors)

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_revision_prompt_sent_on_retry(self, MockClient, spec_file,
                                            template_file, outdir, artifacts_dir):
        """On retry, the revision prompt should contain error codes."""
        mock_client = _mock_client_returning(
            json.dumps(_INVALID_MANIFEST),
            json.dumps(_VALID_MANIFEST),
        )
        MockClient.return_value = mock_client

        compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        # Second call should be with the revision prompt
        assert mock_client.generate_text.call_count == 2
        second_prompt = mock_client.generate_text.call_args_list[1][0][0]
        assert "[E101]" in second_prompt
        assert "fix" in second_prompt.lower() or "Fix" in second_prompt


# ---------------------------------------------------------------------------
# compile_plan — M-01: verify_exempt never trusted from LLM
# ---------------------------------------------------------------------------

# A valid one-WO manifest with NO verify_contract.
# The LLM injects verify_exempt: true — the compiler must overwrite it to False.
_MANIFEST_NO_CONTRACT_LLM_EXEMPT = {
    "system_overview": ["test"],
    # No verify_contract key at all
    "work_orders": [
        {
            "id": "WO-01",
            "title": "Create module",
            "intent": "Create the main module.",
            "preconditions": [],
            "postconditions": [{"kind": "file_exists", "path": "src/main.py"}],
            "allowed_files": ["src/main.py"],
            "forbidden": [],
            "acceptance_commands": [
                'python -c "import os; assert os.path.isfile(\'src/main.py\')"',
            ],
            "context_files": ["src/main.py"],
            "notes": None,
            "verify_exempt": True,  # LLM-injected — must be overwritten
        },
    ],
}


class TestVerifyExemptSanitisation:
    """M-01: The compiler must always overwrite verify_exempt, never preserve
    the LLM-provided value."""

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_forced_false_when_no_contract(self, MockClient, spec_file,
                                            template_file, outdir, artifacts_dir):
        """When verify_contract is absent, verify_exempt must be False
        regardless of what the LLM provided."""
        mock_client = _mock_client_returning(
            json.dumps(_MANIFEST_NO_CONTRACT_LLM_EXEMPT)
        )
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        assert result.work_orders[0]["verify_exempt"] is False

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_forced_false_when_contract_null(self, MockClient, spec_file,
                                              template_file, outdir, artifacts_dir):
        """When verify_contract is explicitly None, verify_exempt must be False."""
        manifest = copy.deepcopy(_MANIFEST_NO_CONTRACT_LLM_EXEMPT)
        manifest["verify_contract"] = None
        mock_client = _mock_client_returning(json.dumps(manifest))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        assert result.work_orders[0]["verify_exempt"] is False

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_wrong_type_contract_rejected_not_crash(self, MockClient, spec_file,
                                                     template_file, outdir,
                                                     artifacts_dir):
        """When verify_contract is a non-dict (e.g. a list), compilation must
        fail with a structured E000 error — not crash with AttributeError.
        (M-01 + M-03 combined: M-03 guards validate_plan_v2, M-01 guards
        the verify_exempt computation.)"""
        manifest = copy.deepcopy(_MANIFEST_NO_CONTRACT_LLM_EXEMPT)
        manifest["verify_contract"] = ["not", "a", "dict"]
        # Supply enough responses for all retry attempts
        mock_client = _mock_client_returning(
            *[json.dumps(manifest)] * MAX_COMPILE_ATTEMPTS
        )
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        # Compilation fails (non-dict verify_contract is a validation error)
        # but does NOT crash — structured errors are returned.
        assert result.success is False
        assert any("E000" in e for e in result.errors)

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_llm_value_overwritten_when_contract_present(self, MockClient,
                                                          spec_file, template_file,
                                                          outdir, artifacts_dir):
        """Even when verify_contract is valid, the LLM-provided verify_exempt
        value must be overwritten by the computed value."""
        manifest = copy.deepcopy(_VALID_MANIFEST)
        # LLM sets verify_exempt: True on WO-02, but the computed value
        # should be False (all verify_contract requirements are satisfied
        # after WO-02's postconditions).
        manifest["work_orders"][1]["verify_exempt"] = True
        mock_client = _mock_client_returning(json.dumps(manifest))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        assert result.success is True
        # WO-02 computed value should be False (contract satisfied), not the
        # LLM-provided True.
        assert result.work_orders[1]["verify_exempt"] is False

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_verify_exempt_false_written_to_disk(self, MockClient, spec_file,
                                                  template_file, outdir,
                                                  artifacts_dir):
        """The overwritten verify_exempt=False must survive to the WO JSON on disk."""
        mock_client = _mock_client_returning(
            json.dumps(_MANIFEST_NO_CONTRACT_LLM_EXEMPT)
        )
        MockClient.return_value = mock_client

        compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        with open(os.path.join(outdir, "WO-01.json")) as f:
            wo1_disk = json.load(f)
        assert wo1_disk["verify_exempt"] is False


# ---------------------------------------------------------------------------
# compile_plan — summary artifacts
# ---------------------------------------------------------------------------


class TestCompileSummary:
    @patch("planner.compiler.OpenAIResponsesClient")
    def test_summary_includes_attempts(self, MockClient, spec_file,
                                        template_file, outdir, artifacts_dir):
        mock_client = _mock_client_returning(json.dumps(_VALID_MANIFEST))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=spec_file,
            outdir=outdir,
            template_path=template_file,
            artifacts_dir=artifacts_dir,
        )

        summary_path = os.path.join(result.artifacts_dir, "compile_summary.json")
        assert os.path.isfile(summary_path)
        with open(summary_path) as f:
            summary = json.load(f)
        assert summary["compile_attempts"] == 1
        assert summary["success"] is True
        assert isinstance(summary["attempt_records"], list)
