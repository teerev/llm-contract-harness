"""Tests for planner/validation.py — structured validation errors and all rules."""

from __future__ import annotations

import pytest

from planner.validation import (
    E000_STRUCTURAL,
    E001_ID,
    E002_VERIFY,
    E003_SHELL_OP,
    E004_GLOB,
    E005_SCHEMA,
    E006_SYNTAX,
    ValidationError,
    _check_python_c_syntax,
    normalize_work_order,
    parse_and_validate,
    validate_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wo(wo_id: str = "WO-01", **overrides) -> dict:
    """Build a minimal valid work order dict.

    Includes ``bash scripts/verify.sh`` in acceptance so E002 doesn't fire
    by default (except for WO-01 bootstrap which is exempt).
    """
    is_bootstrap = wo_id == "WO-01" and "scripts/verify.sh" in overrides.get(
        "allowed_files", []
    )
    default_acceptance = (
        ['python -c "assert True"']
        if is_bootstrap
        else ["bash scripts/verify.sh", 'python -c "assert True"']
    )
    base = {
        "id": wo_id,
        "title": f"Test {wo_id}",
        "intent": "test intent",
        "allowed_files": ["src/a.py"],
        "forbidden": [],
        "acceptance_commands": default_acceptance,
        "context_files": ["src/a.py"],
        "notes": None,
    }
    base.update(overrides)
    return base


def _codes(errors: list[ValidationError]) -> set[str]:
    """Extract the set of error codes from a list of ValidationError."""
    return {e.code for e in errors}


def _codes_for_wo(errors: list[ValidationError], wo_id: str) -> set[str]:
    """Extract error codes for a specific work order."""
    return {e.code for e in errors if e.wo_id == wo_id}


# ---------------------------------------------------------------------------
# ValidationError basics
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_str_with_wo_id(self):
        e = ValidationError(code="E001", wo_id="WO-03", message="bad id")
        assert str(e) == "[E001] WO-03: bad id"

    def test_str_without_wo_id(self):
        e = ValidationError(code="E000", wo_id=None, message="empty list")
        assert str(e) == "[E000] empty list"

    def test_to_dict(self):
        e = ValidationError(
            code="E003", wo_id="WO-02", message="shell op", field="acceptance_commands"
        )
        d = e.to_dict()
        assert d == {
            "code": "E003",
            "wo_id": "WO-02",
            "message": "shell op",
            "field": "acceptance_commands",
        }

    def test_frozen(self):
        e = ValidationError(code="E001", wo_id=None, message="x")
        with pytest.raises(AttributeError):
            e.code = "E002"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# E000: Structural errors
# ---------------------------------------------------------------------------


class TestE000Structural:
    def test_empty_work_orders_list(self):
        errors = validate_plan([])
        assert len(errors) == 1
        assert errors[0].code == E000_STRUCTURAL
        assert "empty" in errors[0].message

    def test_top_level_not_dict(self):
        _, errors = parse_and_validate("not a dict")  # type: ignore[arg-type]
        assert len(errors) == 1
        assert errors[0].code == E000_STRUCTURAL
        assert "object" in errors[0].message

    def test_missing_work_orders_key(self):
        _, errors = parse_and_validate({"system_overview": []})
        assert len(errors) == 1
        assert errors[0].code == E000_STRUCTURAL
        assert "work_orders" in errors[0].message


# ---------------------------------------------------------------------------
# E001: ID format and contiguity
# ---------------------------------------------------------------------------


class TestE001Id:
    def test_valid_single_id(self):
        errors = validate_plan([_wo("WO-01")])
        assert E001_ID not in _codes(errors)

    def test_valid_contiguous_ids(self):
        errors = validate_plan([
            _wo("WO-01"),
            _wo("WO-02"),
            _wo("WO-03"),
        ])
        assert E001_ID not in _codes(errors)

    def test_bad_format(self):
        errors = validate_plan([_wo("wo-1")])
        assert E001_ID in _codes(errors)

    def test_non_contiguous(self):
        errors = validate_plan([
            _wo("WO-01"),
            _wo("WO-03"),  # gap: skips WO-02
        ])
        assert E001_ID in _codes_for_wo(errors, "WO-03")

    def test_wrong_start(self):
        errors = validate_plan([_wo("WO-02")])
        assert E001_ID in _codes(errors)


# ---------------------------------------------------------------------------
# E002: Verify command presence
# ---------------------------------------------------------------------------


class TestE002Verify:
    def test_verify_present_passes(self):
        wo = _wo("WO-02", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "assert True"',
        ])
        errors = validate_plan([_wo("WO-01"), wo])
        assert E002_VERIFY not in _codes_for_wo(errors, "WO-02")

    def test_verify_missing_fails(self):
        wo = _wo("WO-02", acceptance_commands=['python -c "assert True"'])
        errors = validate_plan([_wo("WO-01"), wo])
        assert E002_VERIFY in _codes_for_wo(errors, "WO-02")

    def test_bootstrap_exempt(self):
        """WO-01 creating verify.sh is exempt from the verify-in-acceptance rule."""
        wo = _wo(
            "WO-01",
            allowed_files=["scripts/verify.sh"],
            context_files=["scripts/verify.sh"],
            acceptance_commands=['python -c "assert True"'],
        )
        errors = validate_plan([wo])
        assert E002_VERIFY not in _codes(errors)

    def test_bootstrap_exempt_only_wo01(self):
        """WO-02 creating verify.sh is NOT exempt."""
        wo = _wo(
            "WO-02",
            allowed_files=["scripts/verify.sh"],
            context_files=["scripts/verify.sh"],
            acceptance_commands=['python -c "assert True"'],
        )
        errors = validate_plan([_wo("WO-01"), wo])
        assert E002_VERIFY in _codes_for_wo(errors, "WO-02")


# ---------------------------------------------------------------------------
# E003: Shell operators
# ---------------------------------------------------------------------------


class TestE003ShellOp:
    def test_clean_command_passes(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "x = 1; print(x)"',  # semicolon inside quotes is OK
        ])
        errors = validate_plan([wo])
        assert E003_SHELL_OP not in _codes(errors)

    def test_pipe_rejected(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            "echo hello | grep hello",
        ])
        errors = validate_plan([wo])
        assert E003_SHELL_OP in _codes(errors)

    def test_double_ampersand_rejected(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            "true && echo ok",
        ])
        errors = validate_plan([wo])
        assert E003_SHELL_OP in _codes(errors)

    def test_redirect_rejected(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            "echo hello > out.txt",
        ])
        errors = validate_plan([wo])
        assert E003_SHELL_OP in _codes(errors)


# ---------------------------------------------------------------------------
# E004: Glob characters
# ---------------------------------------------------------------------------


class TestE004Glob:
    def test_clean_paths_pass(self):
        wo = _wo("WO-01", allowed_files=["src/a.py"], context_files=["src/a.py"])
        errors = validate_plan([wo])
        assert E004_GLOB not in _codes(errors)

    def test_glob_star_in_allowed_files(self):
        wo = _wo("WO-01", allowed_files=["src/*.py"], context_files=["src/a.py"])
        errors = validate_plan([wo])
        e004s = [e for e in errors if e.code == E004_GLOB]
        assert len(e004s) >= 1
        assert "allowed_files" in e004s[0].message

    def test_glob_question_in_context_files(self):
        wo = _wo("WO-01", context_files=["src/?.py"])
        errors = validate_plan([wo])
        e004s = [e for e in errors if e.code == E004_GLOB]
        assert len(e004s) >= 1
        assert "context_files" in e004s[0].message

    def test_glob_bracket_rejected(self):
        wo = _wo("WO-01", allowed_files=["src/[ab].py"])
        errors = validate_plan([wo])
        assert E004_GLOB in _codes(errors)


# ---------------------------------------------------------------------------
# E005: Schema validation
# ---------------------------------------------------------------------------


class TestE005Schema:
    def test_valid_schema_passes(self):
        wo = _wo("WO-01")
        errors = validate_plan([wo])
        assert E005_SCHEMA not in _codes(errors)

    def test_absolute_path_rejected(self):
        wo = _wo("WO-01", allowed_files=["/etc/passwd"])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_empty_acceptance_rejected(self):
        wo = _wo("WO-01", acceptance_commands=[])
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)

    def test_too_many_context_files(self):
        files = [f"f{i}.py" for i in range(11)]
        wo = _wo("WO-01", allowed_files=files, context_files=files)
        errors = validate_plan([wo])
        assert E005_SCHEMA in _codes(errors)


# ---------------------------------------------------------------------------
# E006: Python syntax in python -c
# ---------------------------------------------------------------------------


class TestE006Syntax:
    def test_valid_python_c_passes(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "x = 1; print(x)"',
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_syntax_error_caught(self):
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "def foo(:"',  # SyntaxError
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX in _codes(errors)

    def test_multiline_python_c(self):
        # Valid multi-statement python -c
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "import os; assert os.path.isfile(\'x.py\')"',
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_non_python_command_not_checked(self):
        """bash commands are not syntax-checked."""
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            "bash scripts/run.sh",
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_python_without_c_flag_not_checked(self):
        """python script.py is not syntax-checked (only python -c)."""
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            "python run_tests.py",
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX not in _codes(errors)

    def test_helper_returns_none_for_valid(self):
        assert _check_python_c_syntax('python -c "1+1"', "WO-01") is None

    def test_helper_returns_error_for_invalid(self):
        err = _check_python_c_syntax('python -c "if True"', "WO-01")
        assert err is not None
        assert err.code == E006_SYNTAX
        assert err.wo_id == "WO-01"

    def test_helper_returns_none_for_non_python(self):
        assert _check_python_c_syntax("bash foo.sh", "WO-01") is None

    def test_incomplete_expression(self):
        """Incomplete expression like 'def' alone is a syntax error."""
        wo = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "def"',
        ])
        errors = validate_plan([wo])
        assert E006_SYNTAX in _codes(errors)


# ---------------------------------------------------------------------------
# parse_and_validate integration
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    def test_valid_manifest(self):
        manifest = {
            "system_overview": ["test"],
            "work_orders": [_wo("WO-01")],
        }
        work_orders, errors = parse_and_validate(manifest)
        # May have E002 (verify missing) depending on WO-01 setup;
        # but should not have structural errors.
        assert E000_STRUCTURAL not in _codes(errors)
        assert len(work_orders) == 1

    def test_returns_normalized_work_orders(self):
        wo = _wo("WO-01", allowed_files=["  src/a.py  "])
        manifest = {"work_orders": [wo]}
        work_orders, _ = parse_and_validate(manifest)
        # Whitespace should be stripped
        assert work_orders[0]["allowed_files"] == ["src/a.py"]

    def test_multiple_errors_accumulated(self):
        """A plan with multiple problems returns all errors, not just the first."""
        wo1 = _wo("WO-01", acceptance_commands=[
            "bash scripts/verify.sh",
            'python -c "def foo(:"',  # E006
        ])
        wo2 = _wo(
            "WO-03",  # gap → E001
            acceptance_commands=['python -c "assert True"'],  # no verify → E002
        )
        _, errors = parse_and_validate({"work_orders": [wo1, wo2]})
        codes = _codes(errors)
        assert E001_ID in codes      # WO-03 should be WO-02
        assert E002_VERIFY in codes  # WO-03 missing verify
        assert E006_SYNTAX in codes  # WO-01 has syntax error


# ---------------------------------------------------------------------------
# normalize_work_order
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_whitespace(self):
        raw = {"id": "  WO-01 ", "title": " T ", "allowed_files": [" a.py "]}
        result = normalize_work_order(raw)
        assert result["id"] == "WO-01"
        assert result["title"] == "T"
        assert result["allowed_files"] == ["a.py"]

    def test_deduplicates(self):
        raw = {"allowed_files": ["a.py", "a.py", "b.py"]}
        result = normalize_work_order(raw)
        assert result["allowed_files"] == ["a.py", "b.py"]
