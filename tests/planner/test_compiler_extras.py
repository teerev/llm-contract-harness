"""Extra tests for planner/compiler.py — parse_json, compile_hash, no-write-on-failure."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from planner.compiler import (
    _compute_compile_hash,
    _parse_json,
    compile_plan,
)
from planner.openai_client import LLMResult


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------


class TestParseJson:
    def test_plain_json(self):
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"key": "value"}\n```'
        result = _parse_json(raw)
        assert result == {"key": "value"}

    def test_fences_no_language_tag(self):
        raw = '```\n{"a": 1}\n```'
        result = _parse_json(raw)
        assert result == {"a": 1}

    def test_leading_trailing_whitespace(self):
        raw = '  \n{"a": 1}\n  '
        result = _parse_json(raw)
        assert result == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("not json at all")

    def test_nested_fences_handled(self):
        """Markdown fences around valid JSON with nested braces."""
        raw = '```json\n{"list": [{"id": "WO-01"}]}\n```'
        result = _parse_json(raw)
        assert result["list"][0]["id"] == "WO-01"

    def test_fences_with_trailing_content_on_fence_line(self):
        """Closing fence line with trailing content is still stripped."""
        raw = '```json\n{"a": 1}\n```   '
        result = _parse_json(raw)
        assert result == {"a": 1}

    def test_fences_with_extra_blank_lines(self):
        raw = '```json\n\n{"a": 1}\n\n```'
        result = _parse_json(raw)
        assert result == {"a": 1}

    def test_no_fences_multiline(self):
        """Multi-line JSON without fences parses correctly."""
        raw = '{\n  "key": "value",\n  "n": 42\n}'
        result = _parse_json(raw)
        assert result == {"key": "value", "n": 42}

    def test_empty_object(self):
        assert _parse_json("{}") == {}

    def test_fences_only_no_json_raises(self):
        """Fences around garbage should still raise."""
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json("```\nnot json\n```")

    # --- M-10: payload size guard ---

    def test_oversized_payload_rejected(self):
        """Payloads over 10 MB must be rejected before json.loads."""
        huge = '{"x": "' + "A" * (11 * 1024 * 1024) + '"}'
        with pytest.raises(ValueError, match="too large"):
            _parse_json(huge)

    def test_large_but_under_limit_accepted(self):
        """Payloads under 10 MB must still parse normally."""
        data = '{"x": "' + "A" * (1024 * 1024) + '"}'  # ~1 MB
        result = _parse_json(data)
        assert "x" in result


# ---------------------------------------------------------------------------
# _compute_compile_hash
# ---------------------------------------------------------------------------


class TestComputeCompileHash:
    def test_deterministic(self):
        h1 = _compute_compile_hash(b"spec", b"tmpl", "model", "effort")
        h2 = _compute_compile_hash(b"spec", b"tmpl", "model", "effort")
        assert h1 == h2

    def test_length_16(self):
        h = _compute_compile_hash(b"a", b"b", "c", "d")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_inputs_different_hash(self):
        h1 = _compute_compile_hash(b"spec1", b"tmpl", "model", "effort")
        h2 = _compute_compile_hash(b"spec2", b"tmpl", "model", "effort")
        assert h1 != h2

    def test_different_template_different_hash(self):
        h1 = _compute_compile_hash(b"spec", b"tmpl1", "model", "effort")
        h2 = _compute_compile_hash(b"spec", b"tmpl2", "model", "effort")
        assert h1 != h2

    def test_different_model_different_hash(self):
        h1 = _compute_compile_hash(b"spec", b"tmpl", "model-a", "effort")
        h2 = _compute_compile_hash(b"spec", b"tmpl", "model-b", "effort")
        assert h1 != h2

    def test_different_effort_different_hash(self):
        h1 = _compute_compile_hash(b"spec", b"tmpl", "model", "low")
        h2 = _compute_compile_hash(b"spec", b"tmpl", "model", "high")
        assert h1 != h2


# ---------------------------------------------------------------------------
# compile_plan — no WO files on validation failure
# ---------------------------------------------------------------------------


_INVALID_PLAN = {
    "work_orders": [
        {
            "id": "WO-01",
            "title": "T",
            "intent": "I",
            "allowed_files": ["src/a.py"],
            "forbidden": [],
            "acceptance_commands": ['python -c "def foo(:"'],  # E006 syntax error
            "context_files": ["src/a.py"],
            "notes": None,
        },
    ],
}


class TestNoWriteOnFailure:
    @patch("planner.compiler.OpenAIResponsesClient")
    def test_no_wo_files_written_on_error(self, MockClient, tmp_path):
        """When all attempts fail, no WO-*.json files should be written."""
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        tmpl = tmp_path / "template.md"
        tmpl.write_text("{{PRODUCT_SPEC}}")
        outdir = str(tmp_path / "out")
        artdir = str(tmp_path / "art")

        # All attempts return invalid plan
        mock_client = type(MockClient.return_value)()
        mock_client.generate_text = lambda _: LLMResult(text=json.dumps(_INVALID_PLAN))
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=str(spec),
            outdir=outdir,
            template_path=str(tmpl),
            artifacts_dir=artdir,
        )

        assert result.success is False
        assert len(result.errors) > 0
        # No WO files should exist
        if os.path.isdir(outdir):
            wo_files = [f for f in os.listdir(outdir) if f.startswith("WO-")]
            assert wo_files == [], f"WO files should not be written on failure: {wo_files}"
        # But validation_errors should be written
        if os.path.isdir(outdir):
            assert os.path.isfile(os.path.join(outdir, "validation_errors.json"))

    @patch("planner.compiler.OpenAIResponsesClient")
    def test_artifacts_written_even_on_failure(self, MockClient, tmp_path):
        spec = tmp_path / "spec.txt"
        spec.write_text("hello")
        tmpl = tmp_path / "template.md"
        tmpl.write_text("{{PRODUCT_SPEC}}")
        artdir = str(tmp_path / "art")
        outdir = str(tmp_path / "out")

        mock_client = type(MockClient.return_value)()
        mock_client.generate_text = lambda _: LLMResult(text="NOT JSON")
        MockClient.return_value = mock_client

        result = compile_plan(
            spec_path=str(spec),
            outdir=outdir,
            template_path=str(tmpl),
            artifacts_dir=artdir,
        )

        assert result.success is False
        # Compile summary should still be written
        assert os.path.isfile(
            os.path.join(result.artifacts_dir, "compile_summary.json")
        )
        # Prompt rendered should still be written
        assert os.path.isfile(
            os.path.join(result.artifacts_dir, "prompt_attempt_1.txt")
        )
