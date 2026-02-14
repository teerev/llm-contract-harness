"""OpenAI Responses API client with background polling for reliability.

Strategy: submit with background=true, then poll GET /v1/responses/{id}
until status is terminal. This avoids long-lived HTTP connections that
get dropped by server-side load balancers.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from planner.defaults import (  # noqa: F401 — re-exported for backward compat
    CONNECT_TIMEOUT,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    MAX_INCOMPLETE_RETRIES,
    MAX_INCOMPLETE_TOKEN_CAP,
    MAX_TRANSPORT_RETRIES,
    OPENAI_API_BASE,
    POLL_DEADLINE_S,
    POLL_INTERVAL_S,
    POOL_TIMEOUT,
    READ_TIMEOUT,
    RESPONSES_ENDPOINT,
    TRANSPORT_RETRY_BASE_S,
    WRITE_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Artifacts directory for dumping raw responses on failure
# ---------------------------------------------------------------------------
DUMP_DIR: Optional[str] = None  # set by compiler before calling


def _log(msg: str) -> None:
    """Log an OpenAI transport message to stderr.

    Uses the Console object at module level if set, else falls back to
    bare print. The Console is set by the CLI before calling compile_plan.
    """
    if _CONSOLE is not None:
        _CONSOLE.step("LLM", msg)
    else:
        print(f"  [openai] {msg}", file=sys.stderr)


_CONSOLE: Optional[object] = None  # set by cli.py before compile_plan

_DIM = "\033[2m"
_RESET = "\033[0m"


def _use_color() -> bool:
    """Check if stderr supports color (for reasoning output)."""
    try:
        return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    except Exception:
        return False


def _log_reasoning_start() -> None:
    """Print the opening marker for streamed reasoning output."""
    if _use_color():
        sys.stderr.write(f"\n{_DIM}    [reasoning] {_RESET}")
    else:
        sys.stderr.write("\n    [reasoning] ")
    sys.stderr.flush()


def _log_reasoning_delta(delta: str) -> None:
    """Print a reasoning text delta inline (no newline).

    Each delta is individually wrapped in DIM so that the style stays
    uniform even when the text contains markdown-like emphasis (e.g.
    ``**bold**``).  Previously a single DIM was set in
    ``_log_reasoning_start`` and deltas were written raw; any content
    that reset ANSI state (or terminal behaviour on newlines) caused
    parts of the stream to render bright-white instead of dim.

    Root cause: reasoning was rendered without per-chunk dim wrapping,
    so bold/emphasis segments displayed brighter than the dim base style.
    """
    if _use_color():
        sys.stderr.write(f"{_DIM}{delta}{_RESET}")
    else:
        sys.stderr.write(delta)
    sys.stderr.flush()


def _log_reasoning_end() -> None:
    """Print the closing marker for streamed reasoning output."""
    sys.stderr.write("\n\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelConfig:
    model: str = DEFAULT_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


@dataclass(frozen=True)
class LLMResult:
    """Output from a generate_text call: output text + optional reasoning."""
    text: str
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OpenAIResponsesClient:
    """Reliable client: background submission + polling."""

    def __init__(self, cfg: ModelConfig | None = None) -> None:
        self.cfg = cfg or ModelConfig()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT,
            read=READ_TIMEOUT,
            write=WRITE_TIMEOUT,
            pool=POOL_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Public API — returns LLMResult (text + reasoning)
    # ------------------------------------------------------------------

    def generate_text(self, prompt: str) -> LLMResult:
        """Submit prompt, stream or poll until done, return output text + reasoning.

        Uses SSE streaming by default (real-time reasoning output).
        Falls back to background polling if the stream connection fails.
        Retries once with a larger token budget on incomplete.
        """
        budgets = [
            self.cfg.max_output_tokens,
            min(self.cfg.max_output_tokens * 2, MAX_INCOMPLETE_TOKEN_CAP),
        ]

        for i, budget in enumerate(budgets):
            _log(f"Attempt {i+1}: model={self.cfg.model} "
                 f"reasoning={self.cfg.reasoning_effort} max_out={budget}")

            # Try streaming first; fall back to polling on connection failure.
            try:
                data = self._submit_and_stream(prompt, budget)
            except (
                httpx.RemoteProtocolError,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.WriteTimeout,
            ) as stream_exc:
                _log(f"Stream failed ({stream_exc}), falling back to background poll")
                data = self._submit_and_poll(prompt, budget)

            status = data.get("status", "unknown")
            resp_id = data.get("id", "?")

            usage = data.get("usage", {})
            reasoning_tok = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
            output_tok = usage.get("output_tokens", 0)
            _log(f"Response {resp_id}: status={status} "
                 f"output_tokens={output_tok} reasoning_tokens={reasoning_tok}")

            if status == "completed":
                text = self._extract_text(data)
                if text:
                    reasoning = self._extract_reasoning(data)
                    return LLMResult(text=text, reasoning=reasoning)
                self._dump_response(data, f"no_text_attempt_{i}")
                raise RuntimeError(
                    f"Response completed but no output text found. "
                    f"id={resp_id} usage={usage}"
                )

            if status == "incomplete":
                details = data.get("incomplete_details", {})
                reason = details.get("reason", "unknown")
                _log(f"Incomplete: reason={reason} "
                     f"reasoning_tok={reasoning_tok}/{output_tok}")
                self._dump_response(data, f"incomplete_attempt_{i}")

                if reason == "max_output_tokens" and i < MAX_INCOMPLETE_RETRIES:
                    _log(f"Will retry with larger budget: {budgets[i+1]}")
                    continue
                raise RuntimeError(
                    f"Response incomplete: {reason}. "
                    f"id={resp_id} usage={usage} details={details}"
                )

            # Any other terminal status
            error = data.get("error")
            self._dump_response(data, f"unexpected_status_{i}")
            raise RuntimeError(
                f"Unexpected response status={status}. "
                f"id={resp_id} error={error}"
            )

        raise RuntimeError("Exhausted all attempts")

    # ------------------------------------------------------------------
    # Streaming submit (real-time reasoning + output via SSE)
    # ------------------------------------------------------------------

    def _submit_and_stream(self, prompt: str, max_output_tokens: int) -> dict:
        """Submit with stream=true, parse SSE events, print reasoning live.

        Returns the full response dict (reconstructed from the final
        response.completed event).  Reasoning summary deltas are printed
        to the terminal via _log_reasoning as they arrive.
        """
        payload = {
            "model": self.cfg.model,
            "input": prompt,
            "reasoning": {
                "effort": self.cfg.reasoning_effort,
                "summary": "auto",
            },
            "max_output_tokens": max_output_tokens,
            "stream": True,
        }

        # Streaming needs a long read timeout — reasoning can take 30 min.
        stream_timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT,
            read=POLL_DEADLINE_S,  # reuse the poll deadline as max wait
            write=WRITE_TIMEOUT,
            pool=POOL_TIMEOUT,
        )

        final_response: dict = {}
        output_text_parts: list[str] = []
        reasoning_parts: list[str] = []
        _reasoning_started = False

        _log("Streaming response...")

        with httpx.Client(timeout=stream_timeout) as client:
            with client.stream(
                "POST",
                RESPONSES_ENDPOINT,
                headers=self._headers,
                json=payload,
            ) as resp:
                if resp.status_code != 200:
                    resp.read()
                    raise RuntimeError(
                        f"API error {resp.status_code}: {resp.text[:1000]}"
                    )

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw.strip() == "[DONE]":
                        break

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    # --- Reasoning summary deltas (printed live) ---
                    if etype == "response.reasoning_summary_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            if not _reasoning_started:
                                _reasoning_started = True
                                _log_reasoning_start()
                            _log_reasoning_delta(delta)
                            reasoning_parts.append(delta)

                    elif etype == "response.reasoning_summary_text.done":
                        if _reasoning_started:
                            _log_reasoning_end()

                    # --- Output text deltas (accumulated silently) ---
                    elif etype == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            output_text_parts.append(delta)

                    # --- Terminal events ---
                    elif etype in (
                        "response.completed",
                        "response.failed",
                        "response.incomplete",
                    ):
                        final_response = event.get("response", {})

        if not final_response:
            raise RuntimeError("Stream ended without a terminal response event")

        return final_response

    # ------------------------------------------------------------------
    # Background submit + poll (fallback)
    # ------------------------------------------------------------------

    def _submit_and_poll(self, prompt: str, max_output_tokens: int) -> dict:
        """Submit with background=true, then poll until terminal.

        Used as a fallback when streaming fails (e.g., connection drops
        from a server-side load balancer).
        """
        payload = {
            "model": self.cfg.model,
            "input": prompt,
            "reasoning": {
                "effort": self.cfg.reasoning_effort,
                "summary": "auto",
            },
            "max_output_tokens": max_output_tokens,
            "background": True,
        }

        # --- Submit ---
        data = self._post_with_retries(RESPONSES_ENDPOINT, payload)
        resp_id = data.get("id")
        status = data.get("status", "unknown")
        _log(f"Submitted: id={resp_id} status={status}")

        if not resp_id:
            raise RuntimeError(f"No response id returned: {self._short(data)}")

        # If already terminal (can happen for fast/cached responses)
        if status in ("completed", "incomplete", "failed", "cancelled"):
            return data

        # --- Poll ---
        poll_url = f"{RESPONSES_ENDPOINT}/{resp_id}"
        deadline = time.monotonic() + POLL_DEADLINE_S
        polls = 0

        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_S)
            polls += 1
            elapsed = int(time.monotonic() + POLL_DEADLINE_S - deadline)

            data = self._get_with_retries(poll_url)
            status = data.get("status", "unknown")

            if polls % 4 == 1 or status not in ("queued", "in_progress"):
                _log(f"Poll #{polls} ({elapsed}s): status={status}")

            if status in ("completed", "incomplete", "failed", "cancelled"):
                return data

        # Deadline exceeded
        self._dump_response(data, "poll_timeout")
        raise RuntimeError(
            f"Polling deadline exceeded ({POLL_DEADLINE_S}s). "
            f"Last status={status} id={resp_id}"
        )

    # ------------------------------------------------------------------
    # HTTP helpers with transport retries
    # ------------------------------------------------------------------

    def _post_with_retries(self, url: str, json_body: dict) -> dict:
        return self._request_with_retries("POST", url, json_body=json_body)

    def _get_with_retries(self, url: str) -> dict:
        return self._request_with_retries("GET", url)

    def _request_with_retries(
        self, method: str, url: str, *, json_body: dict | None = None
    ) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_TRANSPORT_RETRIES + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    if method == "POST":
                        r = client.post(url, headers=self._headers, json=json_body)
                    else:
                        r = client.get(url, headers=self._headers)

                # Handle retryable HTTP status codes
                if r.status_code in (429, 502, 503, 504):
                    retry_after = _parse_retry_after(r)
                    delay = max(retry_after, TRANSPORT_RETRY_BASE_S * attempt)
                    _log(f"{method} {r.status_code}. Retry in {delay:.0f}s "
                         f"(attempt {attempt}/{MAX_TRANSPORT_RETRIES})")
                    if attempt >= MAX_TRANSPORT_RETRIES:
                        raise RuntimeError(
                            f"API returned {r.status_code} after "
                            f"{MAX_TRANSPORT_RETRIES} attempts: {r.text[:500]}"
                        )
                    time.sleep(delay)
                    continue

                if r.status_code < 200 or r.status_code >= 300:
                    raise RuntimeError(
                        f"API error {r.status_code}: {r.text[:1000]}"
                    )
                return r.json()

            except (
                httpx.RemoteProtocolError,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.WriteTimeout,
            ) as exc:
                last_exc = exc
                delay = TRANSPORT_RETRY_BASE_S * attempt
                _log(f"Transport error: {exc}. "
                     f"Retry in {delay:.0f}s "
                     f"(attempt {attempt}/{MAX_TRANSPORT_RETRIES})")
                if attempt >= MAX_TRANSPORT_RETRIES:
                    raise RuntimeError(
                        f"Transport failed after {MAX_TRANSPORT_RETRIES} "
                        f"attempts: {exc}"
                    ) from exc
                time.sleep(delay)

        raise RuntimeError(f"Unreachable (last_exc={last_exc})")

    # ------------------------------------------------------------------
    # Output extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(data: dict) -> str:
        """Extract output text from response, trying multiple paths."""
        # Fast path: top-level output_text
        out_text = data.get("output_text")
        if isinstance(out_text, str) and out_text.strip():
            return out_text.strip()

        # Walk output array
        for item in data.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            for content in item.get("content", []) or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "output_text":
                    txt = content.get("text", "")
                    if isinstance(txt, str) and txt.strip():
                        return txt.strip()
        return ""

    @staticmethod
    def _extract_reasoning(data: dict) -> str:
        """Extract reasoning/thinking text from response output array.

        The Responses API returns reasoning as output items with
        type="reasoning", each containing a "summary" list of
        {"type": "summary_text", "text": "..."} objects.
        """
        parts: list[str] = []
        for item in data.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "reasoning":
                continue
            for entry in item.get("summary", []) or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "summary_text":
                    txt = entry.get("text", "")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @staticmethod
    def _dump_response(data: dict, label: str) -> None:
        """Write raw response JSON to DUMP_DIR for post-mortem debugging."""
        dump_dir = DUMP_DIR
        if not dump_dir:
            return
        try:
            os.makedirs(dump_dir, exist_ok=True)
            path = os.path.join(dump_dir, f"raw_response_{label}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            _log(f"Dumped response to {path}")
        except Exception:
            pass  # best-effort

    @staticmethod
    def _short(obj: Any) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False)[:2000]
        except Exception:
            return repr(obj)[:2000]


def _parse_retry_after(r: httpx.Response) -> float:
    """Parse Retry-After header if present."""
    val = r.headers.get("retry-after", "")
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
