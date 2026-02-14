"""Authoritative defaults for the planner subsystem.

Every tunable parameter and documented constant for the planner lives here.
All planner call sites import from this module (migrated in M-15).

Safety invariants are annotated and must not be exposed via CLI.
Determinism-sensitive values are annotated and must not be changed without
understanding the impact on artifact reproducibility.

See ROADMAP.md Part 4 §4 (inventory table rows 1–20, 42 dup, 45–46, 48, 50)
and CONTROL_SURFACE.md §3.1–§3.4, §3.10 for provenance.

Structured comment convention (parsed by tools/dump_defaults.py):
  # cat:<category> [det] [safety] [— description]
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

DEFAULT_MODEL: str = "gpt-5.2-codex"  # cat:model det — LLM model for plan generation
DEFAULT_REASONING_EFFORT: str = "medium"  # cat:model det — OpenAI reasoning effort parameter
DEFAULT_MAX_OUTPUT_TOKENS: int = 64000  # cat:model — max output token budget
MAX_INCOMPLETE_TOKEN_CAP: int = 65000  # cat:model — cap for incomplete-retry budget doubling

# ---------------------------------------------------------------------------
# Transport (HTTP timeouts for OpenAI Responses API)
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT: float = 30.0  # cat:timeout — HTTP connect timeout (seconds)
READ_TIMEOUT: float = 60.0  # cat:timeout — HTTP read timeout (seconds)
WRITE_TIMEOUT: float = 30.0  # cat:timeout — HTTP write timeout (seconds)
POOL_TIMEOUT: float = 30.0  # cat:timeout — connection pool timeout (seconds)

# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------

MAX_TRANSPORT_RETRIES: int = 3  # cat:retries — HTTP-level retry count (429/502/503/504)
TRANSPORT_RETRY_BASE_S: float = 3.0  # cat:retries — linear backoff base (delay = base * attempt)
MAX_INCOMPLETE_RETRIES: int = 1  # cat:retries — retry count for incomplete responses

# ---------------------------------------------------------------------------
# Polling (background response polling)
# ---------------------------------------------------------------------------

POLL_INTERVAL_S: float = 5.0  # cat:polling — seconds between status polls
POLL_DEADLINE_S: float = 2400.0  # cat:polling — max wait for LLM response (40 min)

# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

OPENAI_API_BASE: str = "https://api.openai.com/v1"  # cat:paths — OpenAI API base URL
RESPONSES_ENDPOINT: str = f"{OPENAI_API_BASE}/responses"  # cat:paths — derived responses endpoint

# ---------------------------------------------------------------------------
# Compile loop
# ---------------------------------------------------------------------------

MAX_COMPILE_ATTEMPTS: int = 5  # cat:retries — 1 initial + up to 2 revision retries

# ---------------------------------------------------------------------------
# Hashing (determinism)
# ---------------------------------------------------------------------------

COMPILE_HASH_HEX_LENGTH: int = 16  # cat:hashing det — compile hash truncation length

# ---------------------------------------------------------------------------
# Paths / conventions
# ---------------------------------------------------------------------------

VERIFY_SCRIPT_PATH: str = "scripts/verify.sh"  # cat:paths — global verify script path (dup of factory)
VERIFY_COMMAND: str = "bash scripts/verify.sh"  # cat:paths — verify command string for E105 validation
WO_ID_PATTERN_STR: str = r"^WO-\d{2}$"  # cat:paths — WO ID regex pattern string
REQUIRED_PLACEHOLDER: str = "{{PRODUCT_SPEC}}"  # cat:paths — required prompt template placeholder
OPTIONAL_PLACEHOLDERS: tuple[str, ...] = ("{{DOCTRINE}}", "{{REPO_HINTS}}")  # cat:paths — optional prompt placeholders
PLANNER_PROMPT_FILENAME: str = "PLANNER_PROMPT.md"  # cat:paths — default prompt template filename

# ---------------------------------------------------------------------------
# Limits (safety)
# ---------------------------------------------------------------------------

SKIP_DIRS: frozenset[str] = frozenset({  # cat:limits safety — dirs excluded from repo file listing
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".mypy_cache", ".tox", ".venv", "venv", ".eggs",
})
MAX_JSON_PAYLOAD_BYTES: int = 10 * 1024 * 1024  # cat:limits safety — max JSON payload before parse (10 MB)
SHELL_OPERATOR_TOKENS: frozenset[str] = frozenset({  # cat:limits safety — banned shell operator tokens
    "|", "||", "&&", ";", ">", ">>", "<", "<<",
})
