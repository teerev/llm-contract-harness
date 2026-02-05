from __future__ import annotations

import json
import os
import re
from typing import Any

from factory.schemas import PatchProposal, model_validate

try:
    from openai import OpenAI

    _OPENAI_IMPORT_ERROR: Exception | None = None
except Exception as e:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_IMPORT_ERROR = e


_DIFF_HEADER_RE = re.compile(r"(?m)^(diff --git |\+\+\+ )")


def _must_have_diff_headers(unified_diff: str) -> None:
    if unified_diff.strip() == "":
        raise ValueError("unified_diff must be non-empty")
    if _DIFF_HEADER_RE.search(unified_diff) is None:
        raise ValueError("unified_diff must contain diff headers (e.g. 'diff --git' or '+++')")


def parse_patch_proposal(raw_llm_output: str) -> PatchProposal:
    """
    Strictly parse the LLM output as JSON with keys {unified_diff, summary} only.
    No code-fence stripping or recovery is attempted (by design).
    """
    obj = json.loads(raw_llm_output)
    proposal = model_validate(PatchProposal, obj)  # type: ignore[assignment]
    _must_have_diff_headers(proposal.unified_diff)
    return proposal


class LLMClient:
    def __init__(self, *, model: str, temperature: float):
        if OpenAI is None:  # pragma: no cover
            raise RuntimeError(
                "openai package is required but could not be imported"
            ) from _OPENAI_IMPORT_ERROR
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._temperature = float(temperature)

    @property
    def model(self) -> str:
        return self._model

    @property
    def temperature(self) -> float:
        return self._temperature

    def complete(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=self._temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content
        return content if isinstance(content, str) else ""

