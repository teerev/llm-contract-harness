"""Tests for factory/llm.py — no network calls, ever."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from factory.llm import complete, parse_proposal_json


# ---------------------------------------------------------------------------
# parse_proposal_json
# ---------------------------------------------------------------------------


class TestParseProposalJson:
    def test_bare_json(self):
        raw = '{"summary": "test", "writes": []}'
        result = parse_proposal_json(raw)
        assert result == {"summary": "test", "writes": []}

    def test_fenced_json(self):
        raw = '```json\n{"summary": "test", "writes": []}\n```'
        result = parse_proposal_json(raw)
        assert result == {"summary": "test", "writes": []}

    def test_fenced_no_language(self):
        raw = '```\n{"summary": "test", "writes": []}\n```'
        result = parse_proposal_json(raw)
        assert result == {"summary": "test", "writes": []}

    def test_whitespace_around(self):
        raw = '  \n{"summary": "test", "writes": []}  \n'
        result = parse_proposal_json(raw)
        assert result["summary"] == "test"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_proposal_json("not valid json at all")

    def test_fenced_with_nested_backticks(self):
        # Content contains triple backtick but not at start of a line closing fence
        inner = '{"summary": "has ``` in it", "writes": []}'
        raw = f"```json\n{inner}\n```"
        result = parse_proposal_json(raw)
        assert result["summary"] == "has ``` in it"

    # --- M-10: payload size guard ---

    def test_oversized_payload_rejected(self):
        """Payloads over 10 MB must be rejected before json.loads."""
        huge = '{"summary": "' + "A" * (11 * 1024 * 1024) + '", "writes": []}'
        with pytest.raises(ValueError, match="too large"):
            parse_proposal_json(huge)

    def test_large_but_under_limit_accepted(self):
        """Payloads under 10 MB must still parse normally."""
        data = '{"summary": "' + "A" * (1024 * 1024) + '", "writes": []}'
        result = parse_proposal_json(data)
        assert "summary" in result


# ---------------------------------------------------------------------------
# _get_client / complete — key handling
# ---------------------------------------------------------------------------


class TestApiKeyHandling:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY environment variable is not set"):
            complete("prompt", "model")

    def test_complete_calls_openai(self, monkeypatch):
        """Verify complete() plumbs through to the OpenAI client correctly."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "the response"
        mock_client.chat.completions.create.return_value = mock_response

        with patch("factory.llm.openai", create=True) as mock_openai_mod:
            # Make import openai succeed inside _get_client
            import sys
            mock_openai_mod.OpenAI.return_value = mock_client
            sys.modules["openai"] = mock_openai_mod

            try:
                result = complete("my prompt", "gpt-test", temperature=0.5, timeout=60)
            finally:
                # Clean up the injected module
                if "openai" in sys.modules and sys.modules["openai"] is mock_openai_mod:
                    del sys.modules["openai"]

        assert result == "the response"
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-test"
        assert call_kwargs["temperature"] == 0.5

    def test_none_content_raises(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_client.chat.completions.create.return_value = mock_response

        with patch("factory.llm.openai", create=True) as mock_openai_mod:
            import sys
            mock_openai_mod.OpenAI.return_value = mock_client
            sys.modules["openai"] = mock_openai_mod

            try:
                with pytest.raises(RuntimeError, match="LLM returned None content"):
                    complete("prompt", "model")
            finally:
                if "openai" in sys.modules and sys.modules["openai"] is mock_openai_mod:
                    del sys.modules["openai"]
