"""Thin LLM wrapper — official ``openai`` package, Chat Completions only."""

from __future__ import annotations

import json
import os

from factory.defaults import (  # noqa: F401 — re-exported for backward compat
    DEFAULT_LLM_TIMEOUT,
    MAX_JSON_PAYLOAD_BYTES,
)


def _get_client(timeout: int = DEFAULT_LLM_TIMEOUT):  # noqa: ANN202 — return type is openai.OpenAI
    """Return an OpenAI client; fail fast on missing key or package.

    *timeout* is the per-request timeout in seconds passed to the underlying
    ``httpx`` transport used by the ``openai`` SDK.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Set it before running the factory harness."
        )
    try:
        import openai  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "The 'openai' package is required but not installed. "
            "Install it with: pip install openai"
        )
    return openai.OpenAI(api_key=api_key, timeout=timeout)


def complete(
    prompt: str, model: str, temperature: float = 0, timeout: int = DEFAULT_LLM_TIMEOUT
) -> str:
    """Call the LLM and return ``choices[0].message.content``.

    *timeout* is the per-request timeout in seconds.  Defaults to 120 s.
    """
    client = _get_client(timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError("LLM returned None content in response")
    return content


def parse_proposal_json(raw: str) -> dict:
    """Parse the raw LLM output as JSON, stripping markdown fences if present."""
    if len(raw) > MAX_JSON_PAYLOAD_BYTES:
        raise ValueError(
            f"JSON payload too large: {len(raw)} bytes (max {MAX_JSON_PAYLOAD_BYTES})"
        )
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # drop closing fence
        text = "\n".join(lines).strip()
    return json.loads(text)
