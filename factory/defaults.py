"""Authoritative defaults for the factory subsystem.

Every tunable parameter and documented constant for the factory lives here.
All factory call sites import from this module (migrated in M-16).

Safety invariants are annotated and must not be exposed via CLI.
Determinism-sensitive values are annotated and must not be changed without
understanding the impact on artifact reproducibility.

See ROADMAP.md Part 4 §4 (inventory table rows 21–44, 47, 49)
and CONTROL_SURFACE.md §2.1, §3.3, §3.5–§3.7, §3.10, §5.1 for provenance.

Structured comment convention (parsed by tools/dump_defaults.py):
  # cat:<category> [det] [safety] [— description]
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# CLI argparse defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_ATTEMPTS: int = 2  # cat:retries — max SE→TR→PO cycle attempts
DEFAULT_LLM_TEMPERATURE: float = 0  # cat:model — LLM temperature for factory SE calls
DEFAULT_TIMEOUT_SECONDS: int = 600  # cat:timeout — per-command timeout (LLM + subprocess)

# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

DEFAULT_LLM_TIMEOUT: int = 120  # cat:timeout — per-request HTTP timeout (seconds)

# ---------------------------------------------------------------------------
# Hashing (determinism)
# ---------------------------------------------------------------------------

RUN_ID_HEX_LENGTH: int = 16  # cat:hashing det — run_id hash truncation length

# ---------------------------------------------------------------------------
# Limits (safety)
# ---------------------------------------------------------------------------

MAX_FILE_WRITE_BYTES: int = 200 * 1024  # cat:limits safety — max single file write (200 KB)
MAX_TOTAL_WRITE_BYTES: int = 500 * 1024  # cat:limits safety — max total write per proposal (500 KB)
MAX_JSON_PAYLOAD_BYTES: int = 10 * 1024 * 1024  # cat:limits safety — max JSON payload before parse (10 MB)
MAX_CONTEXT_BYTES: int = 200 * 1024  # cat:limits safety — total context-file budget (200 KB)
MAX_CONTEXT_FILES: int = 10  # cat:limits safety — max entries in context_files per WO
MAX_EXCERPT_CHARS: int = 2000  # cat:limits — error excerpt truncation limit

# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

GIT_TIMEOUT_SECONDS: int = 30  # cat:timeout safety — timeout for git subprocess calls

# ---------------------------------------------------------------------------
# Artifact filenames
# ---------------------------------------------------------------------------

ARTIFACT_SE_PROMPT: str = "se_prompt.txt"  # cat:artifacts — SE prompt artifact filename
ARTIFACT_PROPOSED_WRITES: str = "proposed_writes.json"  # cat:artifacts — parsed proposal artifact
ARTIFACT_RAW_LLM_RESPONSE: str = "raw_llm_response.json"  # cat:artifacts — raw LLM output (on parse failure)
ARTIFACT_WRITE_RESULT: str = "write_result.json"  # cat:artifacts — TR write outcome artifact
ARTIFACT_VERIFY_RESULT: str = "verify_result.json"  # cat:artifacts — PO verify results artifact
ARTIFACT_ACCEPTANCE_RESULT: str = "acceptance_result.json"  # cat:artifacts — PO acceptance results artifact
ARTIFACT_FAILURE_BRIEF: str = "failure_brief.json"  # cat:artifacts — structured failure info artifact
ARTIFACT_WORK_ORDER: str = "work_order.json"  # cat:artifacts — work order copy (per-run)
ARTIFACT_RUN_SUMMARY: str = "run_summary.json"  # cat:artifacts — final run summary (per-run)

# ---------------------------------------------------------------------------
# Paths / conventions (verify commands)
# ---------------------------------------------------------------------------

VERIFY_SCRIPT_PATH: str = "scripts/verify.sh"  # cat:paths — global verify script path (authoritative)
VERIFY_FALLBACK_COMMANDS: list[list[str]] = [  # cat:paths — fallback when verify.sh absent
    ["python", "-m", "compileall", "-q", "."],
    ["python", "-m", "pip", "--version"],
    ["python", "-m", "pytest", "-q"],
]
VERIFY_EXEMPT_COMMAND: list[list[str]] = [  # cat:paths — lightweight check when verify_exempt=True
    ["python", "-m", "compileall", "-q", "."],
]
FACTORY_PROMPT_FILENAME: str = "FACTORY_PROMPT.md"  # cat:paths — SE prompt template filename

# ---------------------------------------------------------------------------
# Safety (non-overridable enums / sets)
# ---------------------------------------------------------------------------

ALLOWED_STAGES: frozenset[str] = frozenset({  # cat:safety safety — allowed FailureBrief stage values
    "preflight",
    "llm_output_invalid",
    "write_scope_violation",
    "stale_context",
    "write_failed",
    "verify_failed",
    "acceptance_failed",
    "exception",
})
