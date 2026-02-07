"""Template loading and placeholder substitution."""

from __future__ import annotations

import os


REQUIRED_PLACEHOLDER = "{{PRODUCT_SPEC}}"
OPTIONAL_PLACEHOLDERS = ("{{DOCTRINE}}", "{{REPO_HINTS}}")


def load_template(path: str) -> str:
    """Read template file and return its contents."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def render_prompt(template: str, spec_text: str) -> str:
    """Replace placeholders in *template* with provided values.

    ``{{PRODUCT_SPEC}}`` is required — raises if not found.
    ``{{DOCTRINE}}`` and ``{{REPO_HINTS}}`` are replaced with empty string if present.
    """
    if REQUIRED_PLACEHOLDER not in template:
        raise ValueError(
            f"Template does not contain required placeholder: {REQUIRED_PLACEHOLDER}"
        )
    result = template.replace(REQUIRED_PLACEHOLDER, spec_text)
    for ph in OPTIONAL_PLACEHOLDERS:
        result = result.replace(ph, "")
    return result


def resolve_template_path(explicit: str | None) -> str:
    """Return the template path, applying defaults if *explicit* is None."""
    if explicit is not None:
        if not os.path.isfile(explicit):
            raise FileNotFoundError(f"Template file not found: {explicit}")
        return explicit
    # Default: planner/PLANNER_PROMPT.md (relative to the planner package)
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(pkg_dir, "PLANNER_PROMPT.md")
    if os.path.isfile(default):
        return default
    raise FileNotFoundError(
        "No --template provided and default "
        f"'{default}' does not exist. "
        "Pass --template explicitly."
    )
