"""Thin LLM wrapper — official ``openai`` package, Chat Completions only."""

from __future__ import annotations

import json
import os


def _get_client():  # noqa: ANN202 — return type is openai.OpenAI
    """Return an OpenAI client; fail fast on missing key or package."""
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
    return openai.OpenAI(api_key=api_key)


def complete(prompt: str, model: str, temperature: float = 0) -> str:
    """Call the LLM and return ``choices[0].message.content``."""
    client = _get_client()
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
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # drop closing fence
        text = "\n".join(lines).strip()
    return json.loads(text)
