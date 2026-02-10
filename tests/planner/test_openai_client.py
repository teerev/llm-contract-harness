"""Tests for planner/openai_client.py — pure functions, config, missing key,
and (mocked) generate_text / _submit_and_poll / _request_with_retries.

All tests are offline: no network, no real API key.  The conftest.py
autouse fixture blocks httpx.Client globally; these tests mock at the
method level to avoid touching the network.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

import planner.openai_client as oai_mod
from planner.openai_client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    MAX_INCOMPLETE_RETRIES,
    MAX_TRANSPORT_RETRIES,
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
        r = MagicMock()
        r.headers = {"retry-after": "5"}
        assert _parse_retry_after(r) == 5.0

    def test_float_value(self):
        r = MagicMock()
        r.headers = {"retry-after": "2.5"}
        assert _parse_retry_after(r) == 2.5

    def test_missing_header(self):
        r = MagicMock()
        r.headers = {}
        assert _parse_retry_after(r) == 0.0

    def test_non_numeric(self):
        r = MagicMock()
        r.headers = {"retry-after": "not-a-number"}
        assert _parse_retry_after(r) == 0.0


# ---------------------------------------------------------------------------
# Helper: build a client with mocked internals
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    """Create an OpenAIResponsesClient with a fake API key.

    The conftest blocks httpx.Client globally, so all methods that do
    real HTTP (like _request_with_retries) must be patched in each test
    that exercises them.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    # Temporarily restore httpx.Client for __init__ (it builds a Timeout)
    monkeypatch.setattr(httpx, "Timeout", httpx.Timeout)
    return OpenAIResponsesClient()


def _completed_response(text: str = "hello world", resp_id: str = "resp_1") -> dict:
    """Build a minimal 'completed' API response."""
    return {
        "id": resp_id,
        "status": "completed",
        "output_text": text,
        "usage": {"output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 50}},
    }


def _incomplete_response(reason: str = "max_output_tokens", resp_id: str = "resp_1") -> dict:
    return {
        "id": resp_id,
        "status": "incomplete",
        "incomplete_details": {"reason": reason},
        "usage": {"output_tokens": 64000, "output_tokens_details": {"reasoning_tokens": 60000}},
    }


def _queued_response(resp_id: str = "resp_1") -> dict:
    return {"id": resp_id, "status": "queued"}


def _in_progress_response(resp_id: str = "resp_1") -> dict:
    return {"id": resp_id, "status": "in_progress"}


def _failed_response(resp_id: str = "resp_1") -> dict:
    return {"id": resp_id, "status": "failed", "error": {"message": "server error"}}


# ---------------------------------------------------------------------------
# generate_text
# ---------------------------------------------------------------------------


class TestGenerateText:
    """Test the high-level generate_text method by mocking _submit_and_poll."""

    def test_completed_returns_text(self, client):
        with patch.object(client, "_submit_and_poll", return_value=_completed_response("the text")):
            result = client.generate_text("prompt")
        assert result == "the text"

    def test_completed_but_no_text_raises(self, client):
        """Status=completed but _extract_text returns '' → RuntimeError."""
        resp = _completed_response()
        resp["output_text"] = ""
        resp.pop("output", None)
        with patch.object(client, "_submit_and_poll", return_value=resp):
            with pytest.raises(RuntimeError, match="no output text"):
                client.generate_text("prompt")

    def test_incomplete_max_tokens_retries_with_larger_budget(self, client):
        """First call incomplete (max_output_tokens), second completed → success."""
        responses = [
            _incomplete_response("max_output_tokens"),
            _completed_response("fixed output"),
        ]
        with patch.object(client, "_submit_and_poll", side_effect=responses) as mock_poll:
            result = client.generate_text("prompt")

        assert result == "fixed output"
        assert mock_poll.call_count == 2
        # Second call should have a larger budget
        first_budget = mock_poll.call_args_list[0][0][1]
        second_budget = mock_poll.call_args_list[1][0][1]
        assert second_budget > first_budget

    def test_incomplete_non_retryable_reason_raises(self, client):
        """Incomplete with reason != max_output_tokens → RuntimeError immediately."""
        resp = _incomplete_response("content_filter")
        with patch.object(client, "_submit_and_poll", return_value=resp):
            with pytest.raises(RuntimeError, match="content_filter"):
                client.generate_text("prompt")

    def test_incomplete_max_tokens_exhausts_retries(self, client):
        """Incomplete twice (beyond MAX_INCOMPLETE_RETRIES) → RuntimeError."""
        responses = [
            _incomplete_response("max_output_tokens"),
            _incomplete_response("max_output_tokens"),
        ]
        with patch.object(client, "_submit_and_poll", side_effect=responses):
            with pytest.raises(RuntimeError, match="incomplete"):
                client.generate_text("prompt")

    def test_failed_status_raises(self, client):
        with patch.object(client, "_submit_and_poll", return_value=_failed_response()):
            with pytest.raises(RuntimeError, match="Unexpected response status"):
                client.generate_text("prompt")

    def test_unknown_status_raises(self, client):
        resp = {"id": "resp_1", "status": "cancelled", "usage": {}}
        with patch.object(client, "_submit_and_poll", return_value=resp):
            with pytest.raises(RuntimeError, match="Unexpected response status"):
                client.generate_text("prompt")


# ---------------------------------------------------------------------------
# _submit_and_poll
# ---------------------------------------------------------------------------


class TestSubmitAndPoll:
    """Test polling logic by mocking _post_with_retries and _get_with_retries."""

    def test_already_completed_no_polling(self, client, monkeypatch):
        """POST returns completed immediately → no GET polls."""
        monkeypatch.setattr(oai_mod, "POLL_INTERVAL_S", 0.0)
        with patch.object(client, "_post_with_retries", return_value=_completed_response()):
            with patch.object(client, "_get_with_retries") as mock_get:
                data = client._submit_and_poll("prompt", 1000)

        assert data["status"] == "completed"
        mock_get.assert_not_called()

    def test_polls_until_completed(self, client, monkeypatch):
        """POST returns queued, GET returns in_progress then completed."""
        monkeypatch.setattr(oai_mod, "POLL_INTERVAL_S", 0.0)
        monkeypatch.setattr(oai_mod, "POLL_DEADLINE_S", 100.0)

        get_responses = [
            _in_progress_response(),
            _in_progress_response(),
            _completed_response("done"),
        ]
        with patch.object(client, "_post_with_retries", return_value=_queued_response()):
            with patch.object(client, "_get_with_retries", side_effect=get_responses) as mock_get:
                data = client._submit_and_poll("prompt", 1000)

        assert data["status"] == "completed"
        assert mock_get.call_count == 3

    def test_no_response_id_raises(self, client, monkeypatch):
        """POST returns no id → RuntimeError."""
        monkeypatch.setattr(oai_mod, "POLL_INTERVAL_S", 0.0)
        with patch.object(client, "_post_with_retries", return_value={"status": "queued"}):
            with pytest.raises(RuntimeError, match="No response id"):
                client._submit_and_poll("prompt", 1000)

    def test_deadline_exceeded_raises(self, client, monkeypatch):
        """Polling past deadline → RuntimeError."""
        monkeypatch.setattr(oai_mod, "POLL_INTERVAL_S", 0.0)
        monkeypatch.setattr(oai_mod, "POLL_DEADLINE_S", 0.0)  # immediate deadline

        with patch.object(client, "_post_with_retries", return_value=_queued_response()):
            with patch.object(client, "_get_with_retries", return_value=_in_progress_response()):
                with pytest.raises(RuntimeError, match="Polling deadline"):
                    client._submit_and_poll("prompt", 1000)

    def test_already_failed_returns_immediately(self, client, monkeypatch):
        """POST returns 'failed' → returned without polling."""
        monkeypatch.setattr(oai_mod, "POLL_INTERVAL_S", 0.0)
        with patch.object(client, "_post_with_retries", return_value=_failed_response()):
            with patch.object(client, "_get_with_retries") as mock_get:
                data = client._submit_and_poll("prompt", 1000)

        assert data["status"] == "failed"
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# _request_with_retries
# ---------------------------------------------------------------------------


class TestRequestWithRetries:
    """Test HTTP retry logic by mocking httpx.Client.

    We need to bypass the conftest block for these tests since they
    exercise the actual httpx.Client creation and request logic.
    """

    @pytest.fixture(autouse=True)
    def _restore_httpx_client(self, monkeypatch):
        """Allow httpx.Client to be a MagicMock for these tests."""
        # We replace httpx.Client with a factory that returns a mock
        # context manager.  Individual tests set up the mock's return
        # values to simulate HTTP responses.
        self._mock_client_instance = MagicMock()
        # Make it a context manager
        self._mock_client_instance.__enter__ = MagicMock(return_value=self._mock_client_instance)
        self._mock_client_instance.__exit__ = MagicMock(return_value=False)

        mock_client_cls = MagicMock(return_value=self._mock_client_instance)
        monkeypatch.setattr(httpx, "Client", mock_client_cls)

    def _make_response(self, status_code: int = 200, json_data: dict | None = None,
                       text: str = "", headers: dict | None = None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.text = text or json.dumps(json_data or {})
        resp.headers = headers or {}
        return resp

    def test_success_on_first_try(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp = self._make_response(200, {"id": "r1"})
        self._mock_client_instance.get.return_value = resp

        data = client._request_with_retries("GET", "http://example.com")
        assert data == {"id": "r1"}

    def test_429_retried_then_succeeds(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp_429 = self._make_response(429, text="rate limited", headers={})
        resp_ok = self._make_response(200, {"id": "r1"})
        self._mock_client_instance.get.side_effect = [resp_429, resp_ok]

        data = client._request_with_retries("GET", "http://example.com")
        assert data == {"id": "r1"}
        assert self._mock_client_instance.get.call_count == 2

    def test_429_exhausts_retries(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp_429 = self._make_response(429, text="rate limited", headers={})
        self._mock_client_instance.get.side_effect = [resp_429] * MAX_TRANSPORT_RETRIES

        with pytest.raises(RuntimeError, match="429"):
            client._request_with_retries("GET", "http://example.com")

    @pytest.mark.parametrize("status_code", [502, 503, 504])
    def test_retryable_status_codes(self, client, monkeypatch, status_code):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp_err = self._make_response(status_code, text="server error")
        resp_ok = self._make_response(200, {"ok": True})
        self._mock_client_instance.get.side_effect = [resp_err, resp_ok]

        data = client._request_with_retries("GET", "http://example.com")
        assert data == {"ok": True}

    def test_non_retryable_4xx_raises_immediately(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp_401 = self._make_response(401, text="unauthorized")
        self._mock_client_instance.get.return_value = resp_401

        with pytest.raises(RuntimeError, match="401"):
            client._request_with_retries("GET", "http://example.com")
        # Should NOT retry — only one call
        assert self._mock_client_instance.get.call_count == 1

    def test_connect_error_retried(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp_ok = self._make_response(200, {"ok": True})
        self._mock_client_instance.get.side_effect = [
            httpx.ConnectError("connection refused"),
            resp_ok,
        ]

        data = client._request_with_retries("GET", "http://example.com")
        assert data == {"ok": True}

    def test_read_timeout_retried(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp_ok = self._make_response(200, {"ok": True})
        self._mock_client_instance.get.side_effect = [
            httpx.ReadTimeout("timed out"),
            resp_ok,
        ]

        data = client._request_with_retries("GET", "http://example.com")
        assert data == {"ok": True}

    def test_transport_error_exhausts_retries(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        self._mock_client_instance.get.side_effect = [
            httpx.ConnectError("fail")
        ] * MAX_TRANSPORT_RETRIES

        with pytest.raises(RuntimeError, match="Transport failed"):
            client._request_with_retries("GET", "http://example.com")

    def test_post_sends_json_body(self, client, monkeypatch):
        monkeypatch.setattr(oai_mod, "TRANSPORT_RETRY_BASE_S", 0.0)
        resp_ok = self._make_response(200, {"id": "r1"})
        self._mock_client_instance.post.return_value = resp_ok

        data = client._request_with_retries(
            "POST", "http://example.com", json_body={"input": "hello"}
        )
        assert data == {"id": "r1"}
        self._mock_client_instance.post.assert_called_once()


# ---------------------------------------------------------------------------
# _dump_response
# ---------------------------------------------------------------------------


class TestDumpResponse:
    def test_writes_to_dump_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(oai_mod, "DUMP_DIR", str(tmp_path))
        OpenAIResponsesClient._dump_response({"key": "val"}, "test_label")
        path = tmp_path / "raw_response_test_label.json"
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert data == {"key": "val"}

    def test_noop_when_dump_dir_none(self, monkeypatch):
        monkeypatch.setattr(oai_mod, "DUMP_DIR", None)
        # Should not raise
        OpenAIResponsesClient._dump_response({"key": "val"}, "test_label")

    def test_best_effort_on_write_error(self, tmp_path, monkeypatch):
        """If writing fails, _dump_response swallows the error."""
        # Point to a file (not a dir) so os.makedirs fails or write fails
        bad_path = str(tmp_path / "not_a_dir")
        with open(bad_path, "w") as f:
            f.write("block")
        monkeypatch.setattr(oai_mod, "DUMP_DIR", bad_path)
        # Should not raise
        OpenAIResponsesClient._dump_response({"key": "val"}, "label")


# ---------------------------------------------------------------------------
# _short
# ---------------------------------------------------------------------------


class TestShort:
    def test_truncates_long_json(self):
        big = {"data": "x" * 5000}
        result = OpenAIResponsesClient._short(big)
        assert len(result) <= 2000

    def test_handles_non_serializable(self):
        result = OpenAIResponsesClient._short(object())
        assert isinstance(result, str)
        assert len(result) <= 2000
