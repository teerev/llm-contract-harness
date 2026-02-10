"""Tests for planner/prompt_template.py — template loading, placeholder substitution."""

from __future__ import annotations

import os

import pytest

from planner.prompt_template import (
    OPTIONAL_PLACEHOLDERS,
    REQUIRED_PLACEHOLDER,
    load_template,
    render_prompt,
    resolve_template_path,
)


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


class TestRenderPrompt:
    def test_substitutes_product_spec(self):
        tmpl = "Before {{PRODUCT_SPEC}} After"
        result = render_prompt(tmpl, "MY SPEC")
        assert result == "Before MY SPEC After"

    def test_raises_when_placeholder_missing(self):
        with pytest.raises(ValueError, match="PRODUCT_SPEC"):
            render_prompt("no placeholder here", "spec")

    def test_optional_placeholders_replaced_with_empty(self):
        tmpl = "{{PRODUCT_SPEC}} {{DOCTRINE}} {{REPO_HINTS}}"
        result = render_prompt(tmpl, "SPEC")
        assert result == "SPEC  "

    def test_optional_placeholders_absent_is_fine(self):
        tmpl = "Just {{PRODUCT_SPEC}}"
        result = render_prompt(tmpl, "SPEC")
        assert result == "Just SPEC"

    def test_multiple_occurrences_all_replaced(self):
        tmpl = "{{PRODUCT_SPEC}} and {{PRODUCT_SPEC}}"
        result = render_prompt(tmpl, "X")
        assert result == "X and X"

    def test_preserves_surrounding_text(self):
        tmpl = "# Title\n\n{{PRODUCT_SPEC}}\n\n# End"
        result = render_prompt(tmpl, "content")
        assert "# Title" in result
        assert "content" in result
        assert "# End" in result


# ---------------------------------------------------------------------------
# load_template
# ---------------------------------------------------------------------------


class TestLoadTemplate:
    def test_reads_file(self, tmp_path):
        p = tmp_path / "t.md"
        p.write_text("hello {{PRODUCT_SPEC}}")
        result = load_template(str(p))
        assert result == "hello {{PRODUCT_SPEC}}"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_template(str(tmp_path / "nonexistent.md"))


# ---------------------------------------------------------------------------
# resolve_template_path
# ---------------------------------------------------------------------------


class TestResolveTemplatePath:
    def test_explicit_path_returned(self, tmp_path):
        p = tmp_path / "my_template.md"
        p.write_text("content")
        result = resolve_template_path(str(p))
        assert result == str(p)

    def test_explicit_path_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            resolve_template_path(str(tmp_path / "missing.md"))

    def test_default_resolved_from_package(self):
        """When no explicit path, resolves to planner/PLANNER_PROMPT.md."""
        result = resolve_template_path(None)
        assert result.endswith("PLANNER_PROMPT.md")
        assert os.path.isfile(result)

    def test_default_contains_placeholder(self):
        """The default template must contain {{PRODUCT_SPEC}}."""
        path = resolve_template_path(None)
        content = load_template(path)
        assert REQUIRED_PLACEHOLDER in content
