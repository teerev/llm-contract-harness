"""Canonical defaults hardening tests (M-19).

1. Value-pinning tests — every safety invariant and determinism-sensitive
   value is asserted to its exact expected value.  An accidental change
   causes exactly one test failure here.

2. No-shadow tests — verify that consuming modules do not re-define
   constants that should be imported from the defaults module.

3. Completeness tests — assert the public constant count for each defaults
   module so that new constants must be added to the inventory.

4. Generated-doc freshness — assert docs/CONFIG_DEFAULTS.md matches the
   generator output (R6 from the risk register).
"""

from __future__ import annotations

import inspect
import os
import re
import sys

import pytest

import factory.defaults as fd
import planner.defaults as pd


# =====================================================================
# 1. VALUE-PINNING TESTS
# =====================================================================


class TestPlannerValuePins:
    """Pin every safety and determinism-sensitive planner default."""

    # --- Determinism ---

    def test_default_model(self):
        assert pd.DEFAULT_MODEL == "gpt-5.2-codex"

    def test_default_reasoning_effort(self):
        assert pd.DEFAULT_REASONING_EFFORT == "medium"

    def test_compile_hash_hex_length(self):
        assert pd.COMPILE_HASH_HEX_LENGTH == 16

    # --- Safety ---

    def test_skip_dirs(self):
        expected = frozenset({
            ".git", "__pycache__", ".pytest_cache", "node_modules",
            ".mypy_cache", ".tox", ".venv", "venv", ".eggs",
        })
        assert pd.SKIP_DIRS == expected
        assert len(pd.SKIP_DIRS) == 9

    def test_max_json_payload_bytes(self):
        assert pd.MAX_JSON_PAYLOAD_BYTES == 10 * 1024 * 1024

    def test_shell_operator_tokens(self):
        expected = frozenset({"|", "||", "&&", ";", ">", ">>", "<", "<<"})
        assert pd.SHELL_OPERATOR_TOKENS == expected
        assert len(pd.SHELL_OPERATOR_TOKENS) == 8


class TestFactoryValuePins:
    """Pin every safety and determinism-sensitive factory default."""

    # --- Determinism ---

    def test_run_id_hex_length(self):
        assert fd.RUN_ID_HEX_LENGTH == 16

    # --- Safety ---

    def test_max_file_write_bytes(self):
        assert fd.MAX_FILE_WRITE_BYTES == 200 * 1024

    def test_max_total_write_bytes(self):
        assert fd.MAX_TOTAL_WRITE_BYTES == 500 * 1024

    def test_max_json_payload_bytes(self):
        assert fd.MAX_JSON_PAYLOAD_BYTES == 10 * 1024 * 1024

    def test_max_context_bytes(self):
        assert fd.MAX_CONTEXT_BYTES == 200 * 1024

    def test_max_context_files(self):
        assert fd.MAX_CONTEXT_FILES == 10

    def test_git_timeout_seconds(self):
        assert fd.GIT_TIMEOUT_SECONDS == 30

    def test_allowed_stages(self):
        expected = frozenset({
            "preflight", "llm_output_invalid", "write_scope_violation",
            "stale_context", "write_failed", "verify_failed",
            "acceptance_failed", "exception",
        })
        assert fd.ALLOWED_STAGES == expected
        assert len(fd.ALLOWED_STAGES) == 8


# =====================================================================
# 2. NO-SHADOW TESTS
# =====================================================================

# Pattern: an assignment like ``MAX_FOO = 123`` that is NOT an import.
# We match lines like ``NAME = value`` but exclude:
#   - ``from ... import NAME`` (handled by looking for ``=``)
#   - lines starting with ``#`` (comments)
#   - lines inside string literals (imperfect but good enough)
_ASSIGN_RE = re.compile(r"^(\w+)\s*[:=]\s*(?!.*\bimport\b)")


def _get_public_names(module) -> set[str]:
    """Return the set of public constant names defined in a module.

    Excludes underscore-prefixed names, the ``annotations`` re-export
    from ``from __future__ import annotations``, and submodules.
    """
    return {
        name for name in dir(module)
        if not name.startswith("_")
        and name != "annotations"
        and not inspect.ismodule(getattr(module, name))
    }


def _find_shadowed_assignments(source: str, defaults_names: set[str]) -> list[str]:
    """Return constant names that appear as top-level assignments in *source*.

    Only catches simple ``NAME = value`` or ``NAME: type = value`` patterns
    at column 0.  Skips import lines and comments.
    """
    shadows = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("from ") or stripped.startswith("import "):
            continue
        m = _ASSIGN_RE.match(stripped)
        if m:
            name = m.group(1)
            if name in defaults_names:
                shadows.append(name)
    return shadows


class TestPlannerNoShadow:
    """No consuming module re-defines a constant that lives in planner.defaults."""

    _planner_names = _get_public_names(pd)

    @pytest.mark.parametrize("mod_path", [
        "planner.openai_client",
        "planner.compiler",
        "planner.prompt_template",
        "planner.validation",
    ])
    def test_no_shadow(self, mod_path):
        import importlib
        mod = importlib.import_module(mod_path)
        source = inspect.getsource(mod)
        shadows = _find_shadowed_assignments(source, self._planner_names)
        assert shadows == [], (
            f"{mod_path} shadows planner.defaults names: {shadows}. "
            f"These should be imports, not local assignments."
        )


class TestFactoryNoShadow:
    """No consuming module re-defines a constant that lives in factory.defaults."""

    _factory_names = _get_public_names(fd)

    @pytest.mark.parametrize("mod_path", [
        "factory.__main__",
        "factory.llm",
        "factory.schemas",
        "factory.nodes_se",
        "factory.nodes_po",
        "factory.util",
        "factory.workspace",
    ])
    def test_no_shadow(self, mod_path):
        import importlib
        mod = importlib.import_module(mod_path)
        source = inspect.getsource(mod)
        shadows = _find_shadowed_assignments(source, self._factory_names)
        assert shadows == [], (
            f"{mod_path} shadows factory.defaults names: {shadows}. "
            f"These should be imports, not local assignments."
        )


# =====================================================================
# 3. COMPLETENESS TESTS
# =====================================================================


class TestCompleteness:
    """Assert the public constant count for each defaults module.

    If you add a new constant to a defaults module, update the expected
    count here.  This forces the inventory table to be reviewed.
    """

    def test_planner_defaults_count(self):
        names = _get_public_names(pd)
        assert len(names) == 26, (
            f"Expected 26 public constants in planner.defaults, got {len(names)}: "
            f"{sorted(names)}"
        )

    def test_factory_defaults_count(self):
        names = _get_public_names(fd)
        assert len(names) == 30, (
            f"Expected 30 public constants in factory.defaults, got {len(names)}: "
            f"{sorted(names)}"
        )


# =====================================================================
# 4. GENERATED-DOC FRESHNESS (R6)
# =====================================================================


class TestDocFreshness:
    """Assert docs/CONFIG_DEFAULTS.md matches the generator output."""

    def test_config_defaults_not_stale(self):
        # Import the generator
        tools_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "tools"
        )
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import dump_defaults

        doc_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "docs", "CONFIG_DEFAULTS.md",
        )
        if not os.path.isfile(doc_path):
            pytest.skip("docs/CONFIG_DEFAULTS.md not found (run: python tools/dump_defaults.py)")

        expected = dump_defaults.generate()
        with open(doc_path, "r", encoding="utf-8") as fh:
            actual = fh.read()

        assert actual == expected, (
            "docs/CONFIG_DEFAULTS.md is stale. "
            "Regenerate with: python tools/dump_defaults.py"
        )
