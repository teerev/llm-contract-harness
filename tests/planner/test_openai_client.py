"""Tests for planner/openai_client.py — pure functions, config, missing key.

All tests are offline: no network, no real API key.
"""

from __future__ import annotations

import pytest

from planner.openai_client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    ModelConfig,
    OpenAIResponsesClient,
    _parse_retry_after,
)


# ---------------------------------------------------------------------------
# ModelConfig defaults
# ---------------------------------------------------------------------------


class TestModelConfig:
    def test_defaults(self):
        cfg = ModelConfig()
        assert cfg.model == DEFAULT_MODEL
        assert cfg.reasoning_effort == DEFAULT_REASONING_EFFORT
        assert cfg.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS

    def test_custom_values(self):
        cfg = ModelConfig(model="gpt-4o", reasoning_effort="low", max_output_tokens=1000)
        assert cfg.model == "gpt-4o"
        assert cfg.reasoning_effort == "low"
        assert cfg.max_output_tokens == 1000

    def test_frozen(self):
        cfg = ModelConfig()
        with pytest.raises(AttributeError):
            cfg.model = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


class TestMissingApiKey:
    def test_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAIResponsesClient()

    def test_raises_with_empty_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAIResponsesClient()

    def test_accepts_valid_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-123")
        client = OpenAIResponsesClient()
        assert client._headers["Authorization"] == "Bearer sk-test-key-123"


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_top_level_output_text(self):
        data = {"output_text": "hello"}
        assert OpenAIResponsesClient._extract_text(data) == "hello"

    def test_output_text_stripped(self):
        data = {"output_text": "  hello  "}
        assert OpenAIResponsesClient._extract_text(data) == "hello"

    def test_walk_output_array(self):
        data = {
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "from array"}
                    ],
                },
            ]
        }
        assert OpenAIResponsesClient._extract_text(data) == "from array"

    def test_empty_when_no_text(self):
        data = {"output": [{"type": "reasoning"}]}
        assert OpenAIResponsesClient._extract_text(data) == ""

    def test_empty_dict(self):
        assert OpenAIResponsesClient._extract_text({}) == ""

    def test_prefers_output_text_over_array(self):
        data = {
            "output_text": "top-level",
            "output": [
                {"type": "message", "content": [
                    {"type": "output_text", "text": "nested"}
                ]}
            ],
        }
        assert OpenAIResponsesClient._extract_text(data) == "top-level"

    def test_skips_empty_output_text(self):
        """If output_text is whitespace-only, falls through to array."""
        data = {
            "output_text": "   ",
            "output": [
                {"type": "message", "content": [
                    {"type": "output_text", "text": "from array"}
                ]}
            ],
        }
        assert OpenAIResponsesClient._extract_text(data) == "from array"

    def test_handles_none_output(self):
        data = {"output": None}
        assert OpenAIResponsesClient._extract_text(data) == ""

    def test_handles_non_dict_in_output(self):
        data = {"output": ["not a dict", 42]}
        assert OpenAIResponsesClient._extract_text(data) == ""


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    def test_valid_number(self):
        from unittest.mock import MagicMock

        r = MagicMock()
        r.headers = {"retry-after": "5"}
        assert _parse_retry_after(r) == 5.0

    def test_float_value(self):
        from unittest.mock import MagicMock

        r = MagicMock()
        r.headers = {"retry-after": "2.5"}
        assert _parse_retry_after(r) == 2.5

    def test_missing_header(self):
        from unittest.mock import MagicMock

        r = MagicMock()
        r.headers = {}
        assert _parse_retry_after(r) == 0.0

    def test_non_numeric(self):
        from unittest.mock import MagicMock

        r = MagicMock()
        r.headers = {"retry-after": "not-a-number"}
        assert _parse_retry_after(r) == 0.0
