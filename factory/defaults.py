"""Authoritative defaults for the factory subsystem.

Every tunable parameter and documented constant for the factory lives here.
All factory call sites import from this module (migrated in M-16).

Safety invariants are annotated and must not be exposed via CLI.
Determinism-sensitive values are annotated and must not be changed without
understanding the impact on artifact reproducibility.

See ROADMAP.md Part 4 §4 (inventory table rows 21–44, 47, 49)
and CONTROL_SURFACE.md §2.1, §3.3, §3.5–§3.7, §3.10, §5.1 for provenance.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# CLI argparse defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_ATTEMPTS: int = 2
DEFAULT_LLM_TEMPERATURE: float = 0
DEFAULT_TIMEOUT_SECONDS: int = 600  # conflated: LLM + subprocess timeout

# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

DEFAULT_LLM_TIMEOUT: int = 120  # per-request HTTP timeout (seconds)

# ---------------------------------------------------------------------------
# Hashing (determinism)
# ---------------------------------------------------------------------------

# DETERMINISM — changing this breaks run_id / artifact directory reproducibility.
RUN_ID_HEX_LENGTH: int = 16

# ---------------------------------------------------------------------------
# Limits (safety)
# ---------------------------------------------------------------------------

# SAFETY INVARIANT — do not expose via CLI.
MAX_FILE_WRITE_BYTES: int = 200 * 1024    # 200 KB per file

# SAFETY INVARIANT — do not expose via CLI.
MAX_TOTAL_WRITE_BYTES: int = 500 * 1024   # 500 KB total

# SAFETY INVARIANT — do not expose via CLI.
# Maximum JSON payload size before json.loads (M-10 defense-in-depth).
# Note: planner has its own copy in planner/defaults.py.
MAX_JSON_PAYLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB

# SAFETY INVARIANT — do not expose via CLI.
MAX_CONTEXT_BYTES: int = 200 * 1024   # 200 KB total context-file budget

# SAFETY INVARIANT — do not expose via CLI.
MAX_CONTEXT_FILES: int = 10           # max entries in context_files per WO

# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

MAX_EXCERPT_CHARS: int = 2000

# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

# SAFETY INVARIANT — do not expose via CLI.
GIT_TIMEOUT_SECONDS: int = 30

# ---------------------------------------------------------------------------
# Artifact filenames
# ---------------------------------------------------------------------------

# Per-attempt artifact filenames
ARTIFACT_SE_PROMPT: str = "se_prompt.txt"
ARTIFACT_PROPOSED_WRITES: str = "proposed_writes.json"
ARTIFACT_RAW_LLM_RESPONSE: str = "raw_llm_response.json"
ARTIFACT_WRITE_RESULT: str = "write_result.json"
ARTIFACT_VERIFY_RESULT: str = "verify_result.json"
ARTIFACT_ACCEPTANCE_RESULT: str = "acceptance_result.json"
ARTIFACT_FAILURE_BRIEF: str = "failure_brief.json"

# Per-run artifact filenames
ARTIFACT_WORK_ORDER: str = "work_order.json"
ARTIFACT_RUN_SUMMARY: str = "run_summary.json"

# ---------------------------------------------------------------------------
# Paths / conventions (verify commands)
# ---------------------------------------------------------------------------

# Path to the global verify script checked by the PO node.
# Authoritative definition — planner/defaults.py has a duplicate for validation.
VERIFY_SCRIPT_PATH: str = "scripts/verify.sh"

# Fallback verify commands when scripts/verify.sh does not exist.
VERIFY_FALLBACK_COMMANDS: list[list[str]] = [
    ["python", "-m", "compileall", "-q", "."],
    ["python", "-m", "pip", "--version"],
    ["python", "-m", "pytest", "-q"],
]

# Lightweight verify command used when verify_exempt is True.
VERIFY_EXEMPT_COMMAND: list[list[str]] = [
    ["python", "-m", "compileall", "-q", "."],
]

# Factory prompt template filename (resolved relative to factory package dir).
FACTORY_PROMPT_FILENAME: str = "FACTORY_PROMPT.md"

# ---------------------------------------------------------------------------
# Safety (non-overridable enums / sets)
# ---------------------------------------------------------------------------

# SAFETY INVARIANT — do not expose via CLI.
# Allowed failure-brief stage values.
ALLOWED_STAGES: frozenset[str] = frozenset({
    "preflight",
    "llm_output_invalid",
    "write_scope_violation",
    "stale_context",
    "write_failed",
    "verify_failed",
    "acceptance_failed",
    "exception",
})
