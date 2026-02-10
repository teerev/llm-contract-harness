"""Tests for the cross-work-order chain validator (validate_plan_v2).

Covers rules R1–R7 from ACTION_PLAN.md milestone M3.
"""

from __future__ import annotations

import pytest

from planner.validation import (
    E101_PRECOND,
    E102_CONTRADICTION,
    E103_POST_OUTSIDE,
    E104_NO_POSTCOND,
    E105_VERIFY_IN_ACC,
    E106_VERIFY_CONTRACT,
    W101_ACCEPTANCE_DEP,
    ValidationError,
    compute_verify_exempt,
    extract_file_dependencies,
    validate_plan_v2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMPTY_REPO: set[str] = set()

VERIFY_CONTRACT = {
    "command": "python -m pytest -q",
    "requires": [
        {"kind": "file_exists", "path": "scripts/verify.sh"},
        {"kind": "file_exists", "path": "tests/test_placeholder.py"},
    ],
}


def _wo(wo_id: str = "WO-01", **overrides) -> dict:
    """Build a minimal valid work order dict with conditions."""
    base: dict = {
        "id": wo_id,
        "title": f"Test {wo_id}",
        "intent": "test intent",
        "preconditions": [],
        "postconditions": [
            {"kind": "file_exists", "path": p}
            for p in overrides.get("allowed_files", ["src/a.py"])
        ],
        "allowed_files": ["src/a.py"],
        "forbidden": [],
        "acceptance_commands": ['python -c "assert True"'],
        "context_files": ["src/a.py"],
        "notes": None,
    }
    base.update(overrides)
    return base


def _codes(errors: list[ValidationError]) -> set[str]:
    return {e.code for e in errors}


def _codes_for_wo(errors: list[ValidationError], wo_id: str) -> set[str]:
    return {e.code for e in errors if e.wo_id == wo_id}


def _errors_with_code(errors: list[ValidationError], code: str) -> list[ValidationError]:
    return [e for e in errors if e.code == code]


# ---------------------------------------------------------------------------
# R1: Precondition satisfiability (E101)
# ---------------------------------------------------------------------------


class TestR1PreconditionSatisfiability:
    def test_missing_dependency_rejected(self):
        """WO-02 requires a file that no prior WO creates and repo lacks."""
        wo1 = _wo("WO-01",
                   allowed_files=["scripts/verify.sh"],
                   postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}])
        wo2 = _wo("WO-02",
                   allowed_files=["src/app.py"],
                   preconditions=[{"kind": "file_exists", "path": "src/models.py"}],
                   postconditions=[{"kind": "file_exists", "path": "src/app.py"}])
        errors = validate_plan_v2([wo1, wo2], None, EMPTY_REPO)
        assert E101_PRECOND in _codes_for_wo(errors, "WO-02")
        assert "src/models.py" in str(errors[0])

    def test_satisfied_by_prior_postcondition(self):
        """Precondition met by prior WO's postcondition passes."""
        wo1 = _wo("WO-01",
                   allowed_files=["src/models.py"],
                   postconditions=[{"kind": "file_exists", "path": "src/models.py"}])
        wo2 = _wo("WO-02",
                   allowed_files=["src/app.py"],
                   preconditions=[{"kind": "file_exists", "path": "src/models.py"}],
                   postconditions=[{"kind": "file_exists", "path": "src/app.py"}])
        errors = validate_plan_v2([wo1, wo2], None, EMPTY_REPO)
        assert E101_PRECOND not in _codes(errors)

    def test_satisfied_by_initial_repo(self):
        """Precondition met by a file already in the repo passes."""
        wo = _wo("WO-01",
                 preconditions=[{"kind": "file_exists", "path": "README.md"}],
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], None, {"README.md"})
        assert E101_PRECOND not in _codes(errors)

    def test_file_absent_satisfied(self):
        """file_absent precondition on a path not in state passes."""
        wo = _wo("WO-01",
                 preconditions=[{"kind": "file_absent", "path": "src/new.py"}],
                 allowed_files=["src/new.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/new.py"}])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E101_PRECOND not in _codes(errors)

    def test_file_absent_violated(self):
        """file_absent precondition fails when file already exists."""
        wo = _wo("WO-01",
                 preconditions=[{"kind": "file_absent", "path": "README.md"}],
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], None, {"README.md"})
        assert E101_PRECOND in _codes(errors)

    def test_no_preconditions_passes(self):
        """WO with empty preconditions has no R1 errors."""
        wo = _wo("WO-01", preconditions=[])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E101_PRECOND not in _codes(errors)


# ---------------------------------------------------------------------------
# R2: Contradictory preconditions (E102)
# ---------------------------------------------------------------------------


class TestR2ContradictoryPreconditions:
    def test_contradictory_rejected(self):
        wo = _wo("WO-01",
                 preconditions=[
                     {"kind": "file_exists", "path": "src/a.py"},
                     {"kind": "file_absent", "path": "src/a.py"},
                 ])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E102_CONTRADICTION in _codes(errors)
        assert "src/a.py" in str(_errors_with_code(errors, E102_CONTRADICTION)[0])

    def test_non_contradictory_passes(self):
        wo = _wo("WO-01",
                 preconditions=[
                     {"kind": "file_exists", "path": "src/a.py"},
                     {"kind": "file_absent", "path": "src/b.py"},
                 ])
        errors = validate_plan_v2([wo], None, {"src/a.py"})
        assert E102_CONTRADICTION not in _codes(errors)


# ---------------------------------------------------------------------------
# R3: Postcondition achievability (E103)
# ---------------------------------------------------------------------------


class TestR3PostconditionAchievability:
    def test_postcondition_outside_allowed_rejected(self):
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[
                     {"kind": "file_exists", "path": "src/a.py"},
                     {"kind": "file_exists", "path": "src/b.py"},
                 ])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        e103s = _errors_with_code(errors, E103_POST_OUTSIDE)
        assert len(e103s) == 1
        assert "src/b.py" in e103s[0].message

    def test_postcondition_inside_allowed_passes(self):
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E103_POST_OUTSIDE not in _codes(errors)

    def test_empty_postconditions_skips_check(self):
        """R3 is not applied when postconditions are empty (backward compat)."""
        wo = _wo("WO-01", postconditions=[])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E103_POST_OUTSIDE not in _codes(errors)


# ---------------------------------------------------------------------------
# R4: Allowed-files coverage (E104)
# ---------------------------------------------------------------------------


class TestR4AllowedFilesCoverage:
    def test_allowed_without_postcondition_rejected(self):
        wo = _wo("WO-01",
                 allowed_files=["src/a.py", "src/b.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        e104s = _errors_with_code(errors, E104_NO_POSTCOND)
        assert len(e104s) == 1
        assert "src/b.py" in e104s[0].message

    def test_all_allowed_have_postconditions_passes(self):
        wo = _wo("WO-01",
                 allowed_files=["src/a.py", "src/b.py"],
                 postconditions=[
                     {"kind": "file_exists", "path": "src/a.py"},
                     {"kind": "file_exists", "path": "src/b.py"},
                 ])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E104_NO_POSTCOND not in _codes(errors)

    def test_empty_postconditions_skips_check(self):
        """R4 is not applied when postconditions are empty (backward compat)."""
        wo = _wo("WO-01", allowed_files=["src/a.py", "src/b.py"], postconditions=[])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E104_NO_POSTCOND not in _codes(errors)


# ---------------------------------------------------------------------------
# R5: Acceptance command dependencies (W101)
# ---------------------------------------------------------------------------


class TestR5AcceptanceDeps:
    def test_unverifiable_acceptance_warns(self):
        """Import of a module whose file is never created → W101."""
        wo1 = _wo("WO-01",
                   allowed_files=["mypackage/__init__.py"],
                   postconditions=[{"kind": "file_exists", "path": "mypackage/__init__.py"}])
        wo2 = _wo("WO-02",
                   allowed_files=["src/main.py"],
                   preconditions=[{"kind": "file_exists", "path": "mypackage/__init__.py"}],
                   postconditions=[{"kind": "file_exists", "path": "src/main.py"}],
                   acceptance_commands=[
                       'python -c "from mypackage.solver import Solver"',
                   ])
        errors = validate_plan_v2([wo1, wo2], None, EMPTY_REPO)
        w101s = _errors_with_code(errors, W101_ACCEPTANCE_DEP)
        assert len(w101s) >= 1
        assert "mypackage.solver" in w101s[0].message

    def test_satisfied_import_no_warning(self):
        """Import of a module whose file IS created → no warning."""
        wo1 = _wo("WO-01",
                   allowed_files=["mypackage/__init__.py", "mypackage/solver.py"],
                   postconditions=[
                       {"kind": "file_exists", "path": "mypackage/__init__.py"},
                       {"kind": "file_exists", "path": "mypackage/solver.py"},
                   ])
        wo2 = _wo("WO-02",
                   allowed_files=["src/main.py"],
                   preconditions=[{"kind": "file_exists", "path": "mypackage/solver.py"}],
                   postconditions=[{"kind": "file_exists", "path": "src/main.py"}],
                   acceptance_commands=[
                       'python -c "from mypackage.solver import Solver"',
                   ])
        errors = validate_plan_v2([wo1, wo2], None, EMPTY_REPO)
        assert W101_ACCEPTANCE_DEP not in _codes(errors)

    def test_stdlib_import_ignored(self):
        """Imports of stdlib modules do not trigger W101."""
        wo = _wo("WO-01",
                 acceptance_commands=['python -c "import os; assert os.path.isfile(\'x\')"'])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert W101_ACCEPTANCE_DEP not in _codes(errors)

    def test_non_python_c_ignored(self):
        """bash commands don't trigger import-based W101."""
        wo = _wo("WO-01",
                 acceptance_commands=["bash scripts/run.sh"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        # scripts/run.sh isn't in cumulative_after, but bash dep checking is
        # separate from import analysis. The flat extract_file_dependencies
        # would flag it, but _extract_import_groups (used by R5) does not.
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert W101_ACCEPTANCE_DEP not in _codes(errors)

    def test_import_satisfied_by_init_py(self):
        """Import of 'mypackage' is satisfied by mypackage/__init__.py."""
        wo = _wo("WO-01",
                 allowed_files=["mypackage/__init__.py"],
                 postconditions=[{"kind": "file_exists", "path": "mypackage/__init__.py"}],
                 acceptance_commands=['python -c "import mypackage"'])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert W101_ACCEPTANCE_DEP not in _codes(errors)


# ---------------------------------------------------------------------------
# R6: Verify contract reachability (E106)
# ---------------------------------------------------------------------------


class TestR6VerifyContract:
    def test_verify_contract_never_satisfied_rejected(self):
        """Plan where verify_contract is never fully satisfied → E106."""
        wo = _wo("WO-01",
                 allowed_files=["scripts/verify.sh"],
                 postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}])
        errors = validate_plan_v2([wo], VERIFY_CONTRACT, EMPTY_REPO)
        e106s = _errors_with_code(errors, E106_VERIFY_CONTRACT)
        assert len(e106s) >= 1
        assert "test_placeholder.py" in e106s[0].message

    def test_verify_contract_satisfied_passes(self):
        """Verify contract fully satisfied by cumulative state → no E106."""
        wo1 = _wo("WO-01",
                   allowed_files=["scripts/verify.sh"],
                   postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}])
        wo2 = _wo("WO-02",
                   allowed_files=["tests/test_placeholder.py"],
                   postconditions=[{"kind": "file_exists", "path": "tests/test_placeholder.py"}])
        errors = validate_plan_v2([wo1, wo2], VERIFY_CONTRACT, EMPTY_REPO)
        assert E106_VERIFY_CONTRACT not in _codes(errors)

    def test_no_verify_contract_skips_check(self):
        """No verify_contract → R6 is not applied."""
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E106_VERIFY_CONTRACT not in _codes(errors)

    def test_verify_contract_satisfied_by_initial_repo(self):
        """Verify contract requirements already in the repo → passes."""
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        repo = {"scripts/verify.sh", "tests/test_placeholder.py"}
        errors = validate_plan_v2([wo], VERIFY_CONTRACT, repo)
        assert E106_VERIFY_CONTRACT not in _codes(errors)


# ---------------------------------------------------------------------------
# R7: Verify command not in acceptance (E105)
# ---------------------------------------------------------------------------


class TestR7VerifyInAcceptance:
    def test_verify_in_acceptance_rejected(self):
        wo = _wo("WO-01",
                 acceptance_commands=[
                     "bash scripts/verify.sh",
                     'python -c "assert True"',
                 ])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E105_VERIFY_IN_ACC in _codes(errors)

    def test_verify_with_leading_trailing_whitespace_still_caught(self):
        """E105 strips the command before comparing — leading/trailing ws is caught."""
        wo = _wo("WO-01",
                 acceptance_commands=["  bash scripts/verify.sh  "])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E105_VERIFY_IN_ACC in _codes(errors)

    def test_verify_with_extra_internal_whitespace_caught(self):
        """M-08: Double space now caught via shlex.split normalization."""
        wo = _wo("WO-01",
                 acceptance_commands=["bash  scripts/verify.sh"])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E105_VERIFY_IN_ACC in _codes(errors)

    def test_verify_with_dot_slash_prefix_caught(self):
        """M-08: './scripts/verify.sh' now caught via posixpath.normpath."""
        wo = _wo("WO-01",
                 acceptance_commands=["bash ./scripts/verify.sh"])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E105_VERIFY_IN_ACC in _codes(errors)

    def test_no_verify_in_acceptance_passes(self):
        wo = _wo("WO-01",
                 acceptance_commands=['python -c "assert True"'])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E105_VERIFY_IN_ACC not in _codes(errors)

    def test_similar_but_different_command_passes(self):
        """Commands that look like but aren't exactly verify pass."""
        wo = _wo("WO-01",
                 acceptance_commands=["bash scripts/run.sh"])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        assert E105_VERIFY_IN_ACC not in _codes(errors)

    def test_verify_caught_across_multiple_wos(self):
        """E105 is checked per-WO; flagged on whichever WO has it."""
        wo1 = _wo("WO-01",
                   acceptance_commands=['python -c "assert True"'])
        wo2 = _wo("WO-02",
                   acceptance_commands=["bash scripts/verify.sh"])
        errors = validate_plan_v2([wo1, wo2], None, EMPTY_REPO)
        assert E105_VERIFY_IN_ACC in _codes_for_wo(errors, "WO-02")
        assert E105_VERIFY_IN_ACC not in _codes_for_wo(errors, "WO-01")


# ---------------------------------------------------------------------------
# compute_verify_exempt
# ---------------------------------------------------------------------------


class TestComputeVerifyExempt:
    def test_wo1_exempt_wo2_not(self):
        """WO-01 (only verify.sh) is exempt; WO-02 (adds tests) is not."""
        wo1 = _wo("WO-01",
                   allowed_files=["scripts/verify.sh"],
                   postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}])
        wo2 = _wo("WO-02",
                   allowed_files=["tests/test_placeholder.py"],
                   postconditions=[{"kind": "file_exists", "path": "tests/test_placeholder.py"}])
        result = compute_verify_exempt([wo1, wo2], VERIFY_CONTRACT, EMPTY_REPO)
        assert result[0]["verify_exempt"] is True
        assert result[1]["verify_exempt"] is False

    def test_single_wo_satisfies_all(self):
        """One WO satisfying entire verify_contract → not exempt."""
        wo = _wo("WO-01",
                 allowed_files=["scripts/verify.sh", "tests/test_placeholder.py"],
                 postconditions=[
                     {"kind": "file_exists", "path": "scripts/verify.sh"},
                     {"kind": "file_exists", "path": "tests/test_placeholder.py"},
                 ])
        result = compute_verify_exempt([wo], VERIFY_CONTRACT, EMPTY_REPO)
        assert result[0]["verify_exempt"] is False

    def test_repo_already_satisfies(self):
        """Repo already has all verify_contract files → no WO is exempt."""
        repo = {"scripts/verify.sh", "tests/test_placeholder.py"}
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        result = compute_verify_exempt([wo], VERIFY_CONTRACT, repo)
        assert result[0]["verify_exempt"] is False

    def test_empty_verify_contract_all_false(self):
        """Empty requires → nothing is exempt."""
        wo = _wo("WO-01")
        result = compute_verify_exempt([wo], {"requires": []}, EMPTY_REPO)
        assert result[0]["verify_exempt"] is False

    def test_three_wo_gradual_satisfaction(self):
        """WO-01 and WO-02 exempt; WO-03 (final requirement) not exempt."""
        three_req_contract = {
            "command": "pytest",
            "requires": [
                {"kind": "file_exists", "path": "scripts/verify.sh"},
                {"kind": "file_exists", "path": "src/core.py"},
                {"kind": "file_exists", "path": "tests/test_core.py"},
            ],
        }
        wo1 = _wo("WO-01",
                   allowed_files=["scripts/verify.sh"],
                   postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}])
        wo2 = _wo("WO-02",
                   allowed_files=["src/core.py"],
                   postconditions=[{"kind": "file_exists", "path": "src/core.py"}])
        wo3 = _wo("WO-03",
                   allowed_files=["tests/test_core.py"],
                   postconditions=[{"kind": "file_exists", "path": "tests/test_core.py"}])

        result = compute_verify_exempt([wo1, wo2, wo3], three_req_contract, EMPTY_REPO)
        assert result[0]["verify_exempt"] is True   # 1/3 satisfied
        assert result[1]["verify_exempt"] is True   # 2/3 satisfied
        assert result[2]["verify_exempt"] is False  # 3/3 satisfied

    def test_no_requires_key_all_false(self):
        """verify_contract without 'requires' key → nothing is exempt."""
        wo = _wo("WO-01")
        result = compute_verify_exempt([wo], {"command": "pytest"}, EMPTY_REPO)
        assert result[0]["verify_exempt"] is False

    def test_does_not_mutate_input(self):
        """Input list is not mutated; result is a new list."""
        wo = _wo("WO-01")
        original_keys = set(wo.keys())
        result = compute_verify_exempt([wo], VERIFY_CONTRACT, EMPTY_REPO)
        assert "verify_exempt" not in wo  # original dict unchanged
        assert "verify_exempt" in result[0]
        assert set(wo.keys()) == original_keys


# ---------------------------------------------------------------------------
# extract_file_dependencies
# ---------------------------------------------------------------------------


class TestCumulativeStateAdvancement:
    """Verify that file_state is correctly advanced across work orders."""

    def test_file_absent_after_prior_creates_it_fails(self):
        """WO-02 claims file_absent for a path WO-01 created → E101."""
        wo1 = _wo("WO-01",
                   allowed_files=["src/a.py"],
                   postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        wo2 = _wo("WO-02",
                   allowed_files=["src/b.py"],
                   preconditions=[{"kind": "file_absent", "path": "src/a.py"}],
                   postconditions=[{"kind": "file_exists", "path": "src/b.py"}])
        errors = validate_plan_v2([wo1, wo2], None, EMPTY_REPO)
        assert E101_PRECOND in _codes_for_wo(errors, "WO-02")

    def test_three_wo_chain_all_satisfied(self):
        """Three WOs with correct dependency chain → no errors."""
        wo1 = _wo("WO-01",
                   allowed_files=["src/base.py"],
                   postconditions=[{"kind": "file_exists", "path": "src/base.py"}])
        wo2 = _wo("WO-02",
                   allowed_files=["src/derived.py"],
                   preconditions=[{"kind": "file_exists", "path": "src/base.py"}],
                   postconditions=[{"kind": "file_exists", "path": "src/derived.py"}])
        wo3 = _wo("WO-03",
                   allowed_files=["tests/test_all.py"],
                   preconditions=[
                       {"kind": "file_exists", "path": "src/base.py"},
                       {"kind": "file_exists", "path": "src/derived.py"},
                   ],
                   postconditions=[{"kind": "file_exists", "path": "tests/test_all.py"}])
        errors = validate_plan_v2([wo1, wo2, wo3], None, EMPTY_REPO)
        assert E101_PRECOND not in _codes(errors)


class TestExtractFileDeps:
    def test_python_c_import(self):
        deps = extract_file_dependencies('python -c "from mypackage.solver import Solver"')
        assert "mypackage/solver.py" in deps
        assert "mypackage/solver/__init__.py" in deps

    def test_python_c_plain_import(self):
        deps = extract_file_dependencies('python -c "import mypackage"')
        assert "mypackage.py" in deps or "mypackage/__init__.py" in deps

    def test_stdlib_excluded(self):
        deps = extract_file_dependencies('python -c "import os; import sys"')
        assert deps == []

    def test_bash_script(self):
        deps = extract_file_dependencies("bash scripts/verify.sh")
        assert deps == ["scripts/verify.sh"]

    def test_python_script(self):
        deps = extract_file_dependencies("python run_tests.py")
        assert deps == ["run_tests.py"]

    def test_non_matching_command(self):
        deps = extract_file_dependencies("echo hello")
        assert deps == []

    def test_syntax_error_returns_empty(self):
        deps = extract_file_dependencies('python -c "def foo(:"')
        assert deps == []


# ---------------------------------------------------------------------------
# Integration: valid plan end-to-end
# ---------------------------------------------------------------------------


class TestValidPlanEndToEnd:
    def test_valid_two_wo_plan(self):
        """Well-formed bootstrap + skeleton plan → zero errors."""
        wo1 = _wo("WO-01",
                   allowed_files=["scripts/verify.sh"],
                   preconditions=[{"kind": "file_absent", "path": "scripts/verify.sh"}],
                   postconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
                   acceptance_commands=[
                       'python -c "import os; assert os.path.isfile(\'scripts/verify.sh\')"',
                   ],
                   context_files=["scripts/verify.sh"])
        wo2 = _wo("WO-02",
                   allowed_files=["mypackage/__init__.py", "tests/test_placeholder.py"],
                   preconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
                   postconditions=[
                       {"kind": "file_exists", "path": "mypackage/__init__.py"},
                       {"kind": "file_exists", "path": "tests/test_placeholder.py"},
                   ],
                   acceptance_commands=['python -c "import mypackage"'],
                   context_files=[
                       "scripts/verify.sh",
                       "mypackage/__init__.py",
                       "tests/test_placeholder.py",
                   ])
        errors = validate_plan_v2([wo1, wo2], VERIFY_CONTRACT, EMPTY_REPO)
        assert errors == []

    def test_valid_plan_against_nonempty_repo(self):
        """Precondition satisfied by initial repo file → passes."""
        repo = {"scripts/verify.sh", "tests/test_placeholder.py"}
        wo = _wo("WO-01",
                 allowed_files=["src/feature.py"],
                 preconditions=[{"kind": "file_exists", "path": "scripts/verify.sh"}],
                 postconditions=[{"kind": "file_exists", "path": "src/feature.py"}],
                 acceptance_commands=['python -c "assert True"'])
        errors = validate_plan_v2([wo], VERIFY_CONTRACT, repo)
        assert errors == []

    def test_old_format_wo_no_conditions_passes(self):
        """Old-format WO (no conditions) produces no chain errors except R7."""
        wo = {
            "id": "WO-01",
            "title": "T",
            "intent": "I",
            "allowed_files": ["src/a.py"],
            "forbidden": [],
            "acceptance_commands": ['python -c "assert True"'],
            "context_files": ["src/a.py"],
            "notes": None,
        }
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        # No chain errors at all (no conditions → R1-R4 trivially pass,
        # no verify_contract → R6 skipped, no verify in acceptance → R7 ok)
        assert errors == []


# ---------------------------------------------------------------------------
# M-03: Non-dict verify_contract type guard
# ---------------------------------------------------------------------------


class TestVerifyContractTypeGuard:
    """M-03: validate_plan_v2 and compute_verify_exempt must not crash when
    verify_contract is a non-dict type (list, string, int, etc.)."""

    def test_validate_plan_v2_list_verify_contract(self):
        """verify_contract is a list → structured E000 error, no crash."""
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], ["not", "a", "dict"], EMPTY_REPO)
        assert any(e.code == "E000" for e in errors)
        assert any("list" in e.message for e in errors)

    def test_validate_plan_v2_string_verify_contract(self):
        """verify_contract is a string → structured E000 error, no crash."""
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], "bad", EMPTY_REPO)
        assert any(e.code == "E000" for e in errors)
        assert any("str" in e.message for e in errors)

    def test_validate_plan_v2_int_verify_contract(self):
        """verify_contract is an int → structured E000 error, no crash."""
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], 42, EMPTY_REPO)
        assert any(e.code == "E000" for e in errors)

    def test_compute_verify_exempt_non_dict_returns_all_false(self):
        """compute_verify_exempt with non-dict verify_contract → all False, no crash."""
        wos = [
            _wo("WO-01",
                allowed_files=["src/a.py"],
                postconditions=[{"kind": "file_exists", "path": "src/a.py"}]),
        ]
        result = compute_verify_exempt(wos, ["bad"], EMPTY_REPO)
        assert len(result) == 1
        assert result[0]["verify_exempt"] is False

    def test_validate_plan_v2_none_verify_contract_still_works(self):
        """verify_contract=None (the normal case) must not be broken by the guard."""
        wo = _wo("WO-01",
                 allowed_files=["src/a.py"],
                 postconditions=[{"kind": "file_exists", "path": "src/a.py"}])
        errors = validate_plan_v2([wo], None, EMPTY_REPO)
        # No errors — None is the normal "no contract" case
        assert errors == []
