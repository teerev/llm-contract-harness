"""Authoritative defaults for the planner subsystem.

Every tunable parameter and documented constant for the planner lives here.
All planner call sites import from this module (migrated in M-15).

Safety invariants are annotated and must not be exposed via CLI.
Determinism-sensitive values are annotated and must not be changed without
understanding the impact on artifact reproducibility.

See ROADMAP.md Part 4 §4 (inventory table rows 1–20, 42 dup, 45–46, 48, 50)
and CONTROL_SURFACE.md §3.1–§3.4, §3.10 for provenance.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

# DETERMINISM — feeds into compile hash; changing this changes artifact dirs.
DEFAULT_MODEL: str = "gpt-5.2-codex"

# DETERMINISM — feeds into compile hash; changing this changes artifact dirs.
DEFAULT_REASONING_EFFORT: str = "medium"

DEFAULT_MAX_OUTPUT_TOKENS: int = 64000

# Cap applied when retrying on incomplete response: min(budget * 2, this).
# Not a named constant in openai_client.py today (inline 65000).
MAX_INCOMPLETE_TOKEN_CAP: int = 65000

# ---------------------------------------------------------------------------
# Transport (HTTP timeouts for OpenAI Responses API)
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT: float = 30.0
READ_TIMEOUT: float = 60.0
WRITE_TIMEOUT: float = 30.0
POOL_TIMEOUT: float = 30.0

# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------

MAX_TRANSPORT_RETRIES: int = 3
TRANSPORT_RETRY_BASE_S: float = 3.0  # linear backoff: delay = base * attempt

MAX_INCOMPLETE_RETRIES: int = 1  # retry once with larger budget on incomplete

# ---------------------------------------------------------------------------
# Polling (background response polling)
# ---------------------------------------------------------------------------

POLL_INTERVAL_S: float = 5.0       # seconds between status polls
POLL_DEADLINE_S: float = 2400.0    # 40 min — high reasoning can take 15-30 min

# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

OPENAI_API_BASE: str = "https://api.openai.com/v1"
RESPONSES_ENDPOINT: str = f"{OPENAI_API_BASE}/responses"

# ---------------------------------------------------------------------------
# Compile loop
# ---------------------------------------------------------------------------

MAX_COMPILE_ATTEMPTS: int = 3  # 1 initial + up to 2 retries

# ---------------------------------------------------------------------------
# Hashing (determinism)
# ---------------------------------------------------------------------------

# DETERMINISM — changing this breaks artifact directory deduplication.
COMPILE_HASH_HEX_LENGTH: int = 16

# ---------------------------------------------------------------------------
# Paths / conventions
# ---------------------------------------------------------------------------

# Verify script path — duplicated in factory/defaults.py (authoritative).
# Kept here for planner-side validation (E105, chain validator).
VERIFY_SCRIPT_PATH: str = "scripts/verify.sh"

# Verify command string used in E105 validation rule.
VERIFY_COMMAND: str = "bash scripts/verify.sh"

# WO ID regex pattern string (compiled in validation.py).
WO_ID_PATTERN_STR: str = r"^WO-\d{2}$"

# Prompt template placeholders.
REQUIRED_PLACEHOLDER: str = "{{PRODUCT_SPEC}}"
OPTIONAL_PLACEHOLDERS: tuple[str, ...] = ("{{DOCTRINE}}", "{{REPO_HINTS}}")

# Prompt template filename (resolved relative to the planner package dir).
PLANNER_PROMPT_FILENAME: str = "PLANNER_PROMPT.md"

# ---------------------------------------------------------------------------
# Limits (safety)
# ---------------------------------------------------------------------------

# SAFETY INVARIANT — do not expose via CLI.
# Directories excluded from repo file listing during chain validation.
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".mypy_cache", ".tox", ".venv", "venv", ".eggs",
})

# SAFETY INVARIANT — do not expose via CLI.
# Maximum JSON payload size before json.loads (M-10 defense-in-depth).
MAX_JSON_PAYLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB

# SAFETY INVARIANT — do not expose via CLI.
# Shell operator tokens banned from acceptance commands (shell=False execution).
SHELL_OPERATOR_TOKENS: frozenset[str] = frozenset({
    "|", "||", "&&", ";", ">", ">>", "<", "<<",
})
