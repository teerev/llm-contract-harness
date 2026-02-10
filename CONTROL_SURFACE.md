# CONTROL SURFACE AUDIT

> Forensic audit of all behavior-controlling parameters, entry points,
> artifacts, and failure modes in the llm-compiler repository.
>
> Generated from direct code inspection. No speculation.
> Commit basis: current HEAD on `main`.

---

## 1. EXECUTION ENTRY POINTS

### 1.1 `python -m planner compile`

| Property | Value |
|---|---|
| File | `planner/__main__.py` → `planner/cli.py:main()` → `planner/cli.py:_run_compile()` |
| Invocation | `python -m planner compile --spec FILE --outdir DIR [options]` |
| User-facing | Yes |
| Delegates to | `planner/compiler.py:compile_plan()` |
| Exit codes | `0` success, `1` file error / usage, `2` validation errors, `3` API error, `4` JSON parse error |

Exit code logic (`planner/cli.py:77–131`):
- `0` — work orders validated and written.
- `1` — `FileNotFoundError` (missing spec/template), `FileExistsError` (outdir exists, no `--overwrite`), no subcommand, or `RuntimeError` without API keywords.
- `2` — `result.errors` non-empty AND no parse errors (structural/chain validation failure).
- `3` — `RuntimeError` whose message contains `"API"` or `"OPENAI"`, OR any uncaught `Exception` (treated as transport/network).
- `4` — `result.errors` non-empty AND any error contains `"JSON parse"`.

### 1.2 `python -m factory run`

| Property | Value |
|---|---|
| File | `factory/__main__.py:main()` → `factory/run.py:run_cli()` |
| Invocation | `python -m factory run --repo DIR --work-order FILE --out DIR --llm-model MODEL [options]` |
| User-facing | Yes |
| Delegates to | `factory/graph.py:build_graph()` → LangGraph `graph.invoke()` |
| Exit codes | `0` PASS, `1` FAIL or preflight error, `2` unhandled exception |

Exit code logic (`factory/run.py:20–181`, `factory/__main__.py:49–66`):
- `sys.exit(1)` in `__main__.py` — no subcommand, or `--max-attempts < 1`.
- `sys.exit(1)` in `run.py` — work order load failure, not a git repo, dirty working tree, output dir inside repo.
- `sys.exit(2)` in `run.py:155` — unhandled exception during `graph.invoke()` (emergency summary written).
- `sys.exit(1)` in `run.py:181` — verdict is not `"PASS"`.
- Implicit `sys.exit(0)` — verdict is `"PASS"`.

### 1.3 `utils/run_work_orders.sh`

| Property | Value |
|---|---|
| File | `utils/run_work_orders.sh` |
| Invocation | `./utils/run_work_orders.sh --wo-dir DIR --target-repo DIR --artifacts-dir DIR [options]` |
| User-facing | Yes (orchestrator script) |
| Delegates to | `python -m factory run` per work order, then `git add -A && git commit` |
| Exit codes | `1` missing args / missing dir / no WO files / unknown option; inherits factory exit codes; stops on first failure (`break`) |

Behavioral notes:
- When `--no-init` is NOT set: wipes and re-creates `--target-repo` with `git init`, sets local git identity (`factory@aos.local` / `AOS Factory`), seeds with `README.md` and `.gitignore`, and creates an initial commit.
- Commits after each PASS with `git commit --no-verify`.
- Stops the loop on first FAIL (`break`), does NOT continue to remaining WOs.
- Does NOT pass `--llm-temperature` or `--timeout-seconds` to the factory (uses factory defaults: `0` and `600`).

### 1.4 `utils/score_work_orders.py`

| Property | Value |
|---|---|
| File | `utils/score_work_orders.py:main()` |
| Invocation | `python utils/score_work_orders.py` |
| User-facing | Yes (diagnostic tool) |
| Exit codes | None explicitly — implicit `0` on success |
| Arguments | None — reads from hardcoded `WO_DIRS` list |

This is a standalone scorer with no CLI arguments. Directories to scan are hardcoded.

---

## 2. EXISTING CLI FLAGS (EXPLICIT)

### 2.1 Planner: `python -m planner compile`

Parsed in `planner/cli.py:9–59`, consumed in `planner/cli.py:77–89`.

| Flag | Type | Required | Default | Consumed by | Scope |
|---|---|---|---|---|---|
| `--spec` | path | Yes | — | `compiler.compile_plan(spec_path=)` | planner |
| `--outdir` | path | Yes | — | `compiler.compile_plan(outdir=)` | planner |
| `--template` | path | No | `None` → resolves to `planner/PLANNER_PROMPT.md` | `compiler.compile_plan(template_path=)` | planner |
| `--artifacts-dir` | path | No | `None` → resolves to `./examples/artifacts` or `./artifacts` | `compiler.compile_plan(artifacts_dir=)` | planner |
| `--repo` | path | No | `None` (assumes fresh repo) | `compiler.compile_plan(repo_path=)` | planner |
| `--overwrite` | bool | No | `False` | `compiler.compile_plan(overwrite=)` | planner |
| `--print-summary` | bool | No | `False` | `cli.py:126–129` (stdout only) | planner |

**Weakly enforced flags**: None. All parsed flags are consumed.

**NOT exposed as CLI flags** (planner): model name, reasoning effort, max output tokens, all timeout/retry constants, max compile attempts. These are all hardcoded in `planner/openai_client.py` and `planner/compiler.py`.

### 2.2 Factory: `python -m factory run`

Parsed in `factory/__main__.py:9–47`, consumed in `factory/run.py:20–107`.

| Flag | Type | Required | Default | Consumed by | Scope |
|---|---|---|---|---|---|
| `--repo` | path | Yes | — | `run_cli()` → graph state `repo_root` | factory |
| `--work-order` | path | Yes | — | `run_cli()` → `load_work_order()` | factory |
| `--out` | path | Yes | — | `run_cli()` → graph state `out_dir` | factory |
| `--max-attempts` | int | No | `2` | graph state `max_attempts` → routing in `_route_after_finalize` | factory |
| `--llm-model` | string | Yes | — | graph state `llm_model` → `llm.complete(model=)` | factory |
| `--llm-temperature` | float | No | `0` | graph state `llm_temperature` → `llm.complete(temperature=)` | factory |
| `--timeout-seconds` | int | No | `600` | graph state `timeout_seconds` → `run_command(timeout=)`, `llm.complete(timeout=)` | factory |

**Weakly enforced flags**: `--timeout-seconds` is passed to both `llm.complete()` and `run_command()`. The LLM call uses it as the httpx timeout, but the default in `llm.complete()` itself is `120` — when called via the graph, the CLI value (`600`) overrides. If someone called `llm.complete()` directly without passing timeout, they'd get `120`.

### 2.3 Shell script: `utils/run_work_orders.sh`

| Flag | Type | Required | Default | Passed to |
|---|---|---|---|---|
| `--wo-dir` | directory | Yes | — | WO file discovery |
| `--target-repo` | directory | Yes | — | `python -m factory run --repo` |
| `--artifacts-dir` | directory | Yes | — | `python -m factory run --out` |
| `--model` | string | No | `"gpt-5.2"` | `python -m factory run --llm-model` |
| `--max-attempts` | int | No | `5` | `python -m factory run --max-attempts` |
| `--no-init` | bool flag | No | `false` (init enabled) | controls repo wipe/init logic |

**Note**: The shell script's `--model` default (`gpt-5.2`) differs from the planner's hardcoded model (`gpt-5.2-codex`). The shell script does NOT pass `--llm-temperature` or `--timeout-seconds`, so factory defaults apply (`0` and `600`).

---

## 3. IMPLICIT CONFIGURATION (HIDDEN PARAMETERS)

### 3.1 Planner LLM Configuration

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| `DEFAULT_MODEL` | `planner/openai_client.py:25` | `"gpt-5.2-codex"` | Which model generates work orders | **Yes** — model choice directly affects plan quality and cost. v1 flag. |
| `DEFAULT_REASONING_EFFORT` | `planner/openai_client.py:26` | `"medium"` | OpenAI reasoning effort parameter | **Yes** — affects latency, cost, and output quality. v2 flag. |
| `DEFAULT_MAX_OUTPUT_TOKENS` | `planner/openai_client.py:27` | `64000` | Max token budget for LLM response | **Yes** — large plans may need more tokens. v2 flag. |
| `MAX_COMPILE_ATTEMPTS` | `planner/compiler.py:37` | `3` | Number of generate→validate→revise cycles (1 initial + 2 retries) | **Yes** — directly controls reliability vs cost. v1 flag. |

### 3.2 Planner Transport & Polling

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| `CONNECT_TIMEOUT` | `planner/openai_client.py:32` | `30.0` s | httpx connect timeout | No — transport tuning, not user-facing. |
| `READ_TIMEOUT` | `planner/openai_client.py:33` | `60.0` s | httpx read timeout (short because polling) | No — transport tuning. |
| `WRITE_TIMEOUT` | `planner/openai_client.py:34` | `30.0` s | httpx write timeout | No — transport tuning. |
| `POOL_TIMEOUT` | `planner/openai_client.py:35` | `30.0` s | httpx connection pool timeout | No — transport tuning. |
| `MAX_TRANSPORT_RETRIES` | `planner/openai_client.py:37` | `3` | Retries on 429/502/503/504 or transport errors | No — low-level resilience. |
| `TRANSPORT_RETRY_BASE_S` | `planner/openai_client.py:38` | `3.0` s | Base delay for exponential backoff (delay = base * attempt) | No — transport tuning. |
| `POLL_INTERVAL_S` | `planner/openai_client.py:40` | `5.0` s | Seconds between status polls | No — transport tuning. |
| `POLL_DEADLINE_S` | `planner/openai_client.py:41` | `2400.0` s (40 min) | Max time to wait for a response to complete | **Maybe** — very long models may need more. v2 at most. |
| `MAX_INCOMPLETE_RETRIES` | `planner/openai_client.py:43` | `1` | Retry count on "incomplete" (doubles budget) | No — internal resilience. |
| Incomplete retry budget | `planner/openai_client.py:100` | `min(max_output_tokens * 2, 65000)` | Token budget on incomplete retry | No — derived from max_output_tokens. |
| Retryable HTTP codes | `planner/openai_client.py:228` | `{429, 502, 503, 504}` | Which status codes trigger retries | No — fixed by API semantics. |
| Retry-After parsing | `planner/openai_client.py:321–327` | Parses `Retry-After` header, falls back to `0.0` | Delay floor from server hints | No — standard behavior. |

### 3.3 Planner Prompt & Validation

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| `REQUIRED_PLACEHOLDER` | `planner/prompt_template.py:8` | `"{{PRODUCT_SPEC}}"` | Placeholder that must exist in template | No — template contract. |
| `OPTIONAL_PLACEHOLDERS` | `planner/prompt_template.py:9` | `("{{DOCTRINE}}", "{{REPO_HINTS}}")` | Placeholders replaced with empty string if present | No — template contract. |
| Default template path | `planner/prompt_template.py:41–43` | `planner/PLANNER_PROMPT.md` (relative to package dir) | Prompt used for compilation | Already exposed via `--template`. |
| Default artifacts dir | `planner/compiler.py:183–186` | `./examples/artifacts` (if exists), else `./artifacts` | Where compile artifacts are stored | Already exposed via `--artifacts-dir`. |
| `_SKIP_DIRS` | `planner/compiler.py:40–41` | `{".git", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache", ".tox", ".venv", "venv", ".eggs"}` | Directories excluded from repo file listing | No — sane defaults, would be footgun. |
| `VERIFY_COMMAND` | `planner/validation.py:13` | `"bash scripts/verify.sh"` | Expected global verify command string | No — structural contract. |
| `VERIFY_SCRIPT_PATH` | `planner/validation.py:14` | `"scripts/verify.sh"` | Expected verify script path | No — structural contract. |
| `WO_ID_PATTERN` | `planner/validation.py:15` | `re.compile(r"^WO-\d{2}$")` | Work order ID format validation | No — format contract. |
| `SHELL_OPERATOR_TOKENS` | `planner/validation.py:20` | `{"\|", "\|\|", "&&", ";", ">", ">>", "<", "<<"}` | Tokens banned from acceptance commands | No — shell=False enforcement. |
| `_STDLIB_TOP_LEVEL` | `planner/validation.py:301–313` | Large frozenset of stdlib/common package names | Filters out stdlib from dependency analysis | No — internal heuristic. |

### 3.4 Factory LLM Client

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| Default `timeout` in `_get_client()` | `factory/llm.py:9` | `120` s | httpx timeout for OpenAI SDK client | **Partially** — overridden by `--timeout-seconds` when called via graph. Direct callers get 120. |
| Default `temperature` in `complete()` | `factory/llm.py:32` | `0` | LLM temperature | Already exposed via `--llm-temperature`. |
| Chat API role | `factory/llm.py:41` | `"user"` (single message) | Always sends single user-role message | No — prompt architecture. |
| None content check | `factory/llm.py:45–46` | Raises `RuntimeError` if `content is None` | Catches empty LLM responses | No — error handling. |
| JSON fence stripping | `factory/llm.py:52–58` | Strips ``` fences from LLM output | Tolerates markdown-wrapped JSON | No — resilience behavior. |

### 3.5 Factory Size Limits & Thresholds

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| `MAX_FILE_WRITE_BYTES` | `factory/schemas.py:122` | `204800` (200 KB) | Max size per individual file write | No — safety limit, raising it is dangerous. |
| `MAX_TOTAL_WRITE_BYTES` | `factory/schemas.py:123` | `512000` (500 KB) | Max total write size per proposal | No — safety limit. |
| `context_files` max entries | `factory/schemas.py:98–99` | `10` | Max context files per work order | No — prompt budget constraint. |
| `MAX_CONTEXT_BYTES` | `factory/nodes_se.py:20` | `204800` (200 KB) | Total bytes budget for reading context files | No — prompt budget constraint. |
| `MAX_EXCERPT_CHARS` | `factory/util.py:54` | `2000` | Truncation limit for stdout/stderr excerpts | No — artifact readability. |
| `run_id` length | `factory/util.py:47` | `hexdigest()[:16]` (16 hex chars) | Length of deterministic run identifier | No — internal format. |

### 3.6 Factory Git Operations

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| `GIT_TIMEOUT_SECONDS` | `factory/workspace.py:11` | `30` | Timeout for all git subprocess calls | No — should be sufficient for any git op. |
| Rollback strategy | `factory/workspace.py:90–107` | `git reset --hard` + `git clean -fdx` | How repo is restored on failure | No — determinism requirement. |
| Tree hash scoping | `factory/workspace.py:55–81` | Stages only `touched_files` (or `git add -A` fallback) | What gets included in tree hash | No — correctness invariant. |

### 3.7 Factory Verification Fallback

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| Verify script detection | `factory/nodes_po.py:24–37` | Checks for `scripts/verify.sh` | Whether to use custom or fallback verify | No — convention-based. |
| Fallback verify commands | `factory/nodes_po.py:33–37` | `["python -m compileall -q .", "python -m pip --version", "python -m pytest -q"]` | Default verification when no verify.sh exists | No — reasonable defaults. |
| Verify-exempt behavior | `factory/nodes_po.py:82–83` | `compileall -q .` only | Lightweight check for bootstrap WOs | No — bootstrap contract. |
| Timeout for verify/acceptance | `factory/nodes_po.py:69` | Uses `state["timeout_seconds"]` | Timeout per command | Already exposed via `--timeout-seconds`. |

### 3.8 Shell Script Implicit Configuration

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| Git user email | `utils/run_work_orders.sh:85` | `"factory@aos.local"` | Git commit identity | No — local convention. Consider parameterizing for CI. |
| Git user name | `utils/run_work_orders.sh:86` | `"AOS Factory"` | Git commit identity | No — local convention. |
| `--no-verify` on commit | `utils/run_work_orders.sh:128` | Always set | Skips git pre-commit hooks | No — intentional for generated code. |
| Seed files | `utils/run_work_orders.sh:88–97` | `README.md`, `.gitignore` | Initial repo content | No — bootstrap convention. |
| Stop-on-first-failure | `utils/run_work_orders.sh:134` | `break` | Halts WO processing on first FAIL | **Maybe** — a `--continue-on-failure` flag could be useful. v2. |

### 3.9 Score Script Configuration

| Parameter | Location | Value | Controls | Should be CLI-exposed? |
|---|---|---|---|---|
| `WO_DIRS` | `utils/score_work_orders.py:17` | `["./wo", "./wo2", "./wo3", "./wo4"]` | Directories to scan for work orders | **Yes** — currently completely hardcoded with no CLI args. |
| `classify_layer` keywords | `utils/score_work_orders.py:21–42` | Various string patterns | Layer classification heuristic | No — scorer internals. |
| Scoring weights | `utils/score_work_orders.py:290` | `0.45 * seam_stability + 0.35 * layer_score + 0.20 * meaningful_rate` | Global quality score formula | No — scorer internals. |
| Verdict thresholds | `utils/score_work_orders.py:302–307` | `median_e <= 8 && c_global >= 0.75` → "good", etc. | Quality verdict classification | No — scorer internals. |

### 3.10 Environment Variables

| Variable | Location(s) | Required | Controls |
|---|---|---|---|
| `OPENAI_API_KEY` | `factory/llm.py:15`, `planner/openai_client.py:75` | Yes (both sides) | Authentication for OpenAI API |

This is the ONLY environment variable in the entire codebase. There are no other env-var-based configuration points.

---

## 4. LLM CONTROL SURFACES (PLANNER VS FACTORY)

### 4.1 Planner-Side LLM Usage

| Aspect | Detail |
|---|---|
| **API** | OpenAI **Responses API** (`POST /v1/responses`) with `background: true` + polling (`GET /v1/responses/{id}`). Implemented in `planner/openai_client.py`. |
| **Client** | Custom `OpenAIResponsesClient` class using raw `httpx` HTTP calls. NOT using the `openai` Python SDK. |
| **Model selection** | Hardcoded: `DEFAULT_MODEL = "gpt-5.2-codex"` (`openai_client.py:25`). NOT configurable via CLI. Stored in `ModelConfig` dataclass. |
| **Reasoning effort** | `DEFAULT_REASONING_EFFORT = "medium"` (`openai_client.py:26`). Passed as `{"reasoning": {"effort": "medium"}}` in payload. NOT configurable via CLI. |
| **Max output tokens** | `DEFAULT_MAX_OUTPUT_TOKENS = 64000` (`openai_client.py:27`). On incomplete, retries with `min(64000 * 2, 65000) = 65000`. |
| **Temperature** | NOT PRESENT. The Responses API payload does not include a `temperature` field. Reasoning models use `reasoning_effort` instead. |
| **Prompt source** | `planner/PLANNER_PROMPT.md` rendered via `prompt_template.py:render_prompt()`. On retry, `compiler.py:_build_revision_prompt()` constructs a correction prompt with errors + previous response + original spec. |
| **Retry logic** | Up to `MAX_COMPILE_ATTEMPTS = 3` iterations (`compiler.py:230`). Each retry uses a revision prompt. Transport retries are separate (up to 3 per HTTP call). Incomplete retries (budget doubling) are up to 1. |
| **Failure classification** | JSON parse error → retry with revision prompt. Validation hard errors → retry with revision prompt. Warnings → do not trigger retry. Incomplete response → retry with larger budget. Transport errors → retry at HTTP level. |
| **Determinism assumptions** | Compile hash is computed from `(spec_bytes, template_bytes, model, reasoning_effort)` — does NOT include max_output_tokens or attempt number. Same inputs → same hash, but LLM output is non-deterministic. |
| **Malformed output handling** | `_parse_json()` strips markdown fences, then `json.loads()`. On failure → `ValidationError(code="E000")` → revision prompt on next attempt. |

### 4.2 Factory-Side LLM Usage

| Aspect | Detail |
|---|---|
| **API** | OpenAI **Chat Completions API** (`client.chat.completions.create()`). Implemented in `factory/llm.py`. |
| **Client** | Official `openai` Python SDK. Instantiated fresh per call via `_get_client()`. |
| **Model selection** | CLI-provided via `--llm-model` (required flag). Flows through graph state to `llm.complete(model=)`. |
| **Temperature** | CLI-provided via `--llm-temperature` (default `0`). Flows through graph state to `llm.complete(temperature=)`. |
| **Timeout** | CLI-provided via `--timeout-seconds` (default `600`). Passed to `llm.complete(timeout=)` which passes to `openai.OpenAI(timeout=)`. Also used for `run_command()` subprocess timeout. |
| **Prompt source** | `factory/FACTORY_PROMPT.md` rendered by `nodes_se.py:_build_prompt()` with work-order fields, context-file contents, and optional failure brief. |
| **Retry logic** | Graph-level retry: `_route_after_finalize()` (`graph.py:72–78`) loops back to SE node if verdict is FAIL and `attempt_index <= max_attempts`. Failure brief from prior attempt is included in next SE prompt. NO transport-level retries in `factory/llm.py`. |
| **Failure classification** | See `ALLOWED_STAGES` in `schemas.py:169–178`: `preflight`, `llm_output_invalid`, `write_scope_violation`, `stale_context`, `write_failed`, `verify_failed`, `acceptance_failed`, `exception`. Each maps to a `FailureBrief`. |
| **Determinism assumptions** | `temperature=0` by default. Run ID is deterministic from `(work_order, baseline_commit)`. Tree hash is deterministic from staged files. |
| **Malformed output handling** | `parse_proposal_json()` strips markdown fences, then `json.loads()`. On failure → `FailureBrief(stage="llm_output_invalid")` → retry via graph loop. On success, Pydantic `WriteProposal(**parsed)` validates schema. |

### 4.3 Asymmetry Analysis

| Dimension | Planner | Factory | Why |
|---|---|---|---|
| API variant | Responses API (background + polling) | Chat Completions (synchronous) | Planner needs long reasoning; factory prompts are simpler. |
| HTTP client | Raw `httpx` | Official `openai` SDK | Planner needs polling; SDK doesn't support background responses. |
| Transport retries | Yes (3 attempts, exponential backoff) | **None** | Factory relies on graph-level retry; planner has no graph. |
| Model configurable? | **No** (hardcoded) | **Yes** (CLI flag) | Planner model choice is considered architectural; factory model is operational. |
| Temperature | Not applicable (uses reasoning effort) | Configurable (default 0) | Different API capabilities. |
| Retry mechanism | LLM-level revision prompts (up to 2 retries) | Graph loop with failure feedback (up to N-1 retries) | Planner fixes validation errors; factory fixes execution errors. |
| Incomplete handling | Retries with 2x budget (up to 65K tokens) | **Not applicable** (Chat API doesn't have "incomplete") | Different API semantics. |

---

## 5. ARTIFACTS & OUTPUTS

### 5.1 Factory Per-Run Artifacts

Directory pattern: `{out_dir}/{run_id}/`

| Artifact | File | When Written | Trigger | Overwrite | Relied on by later stages |
|---|---|---|---|---|---|
| Work order snapshot | `work_order.json` | Start of run | Always | Yes (exist_ok dir) | No — post-mortem only |
| Run summary | `run_summary.json` | End of run OR on exception | Always (normal: `run.py:174`, emergency: `run.py:143`) | Yes | No — post-mortem only |

### 5.2 Factory Per-Attempt Artifacts

Directory pattern: `{out_dir}/{run_id}/attempt_{N}/`

| Artifact | File | When Written | Trigger | Overwrite | Relied on by later stages |
|---|---|---|---|---|---|
| SE prompt | `se_prompt.txt` | SE node, always | Before LLM call (`nodes_se.py:226`) | Yes | No — auditability |
| Proposed writes | `proposed_writes.json` | SE node, on successful parse | `WriteProposal` validated (`nodes_se.py:273`) | Yes | Checked by finalize for existence |
| Raw LLM response | `raw_llm_response.json` | SE node, on parse failure | JSON parse or schema validation error (`nodes_se.py:264`) | Yes | No — debugging |
| Failure brief | `failure_brief.json` | SE, TR, PO, or finalize | Any failure stage (`nodes_se.py:244`, `nodes_tr.py:69`, finalize `graph.py:110`) | Yes (finalize always overwrites SE's write-ahead) | Fed into next SE prompt as retry context |
| Write result | `write_result.json` | TR node, always | After scope/hash checks (`nodes_tr.py:70` or `184`) | Yes | No — auditability |
| Verify result | `verify_result.json` | PO node, always | After verify commands (`nodes_po.py:107` or `114`) | Yes | No — auditability |
| Acceptance result | `acceptance_result.json` | PO node, always | After acceptance commands (`nodes_po.py:165`, `190`, `201`) | Yes | No — auditability |
| Verify stdout/stderr | `verify_{N}_stdout.txt`, `verify_{N}_stderr.txt` | PO node, per command | Each verify command (`nodes_po.py:94–95`) | Yes | No — debugging |
| Acceptance stdout/stderr | `acceptance_{N}_stdout.txt`, `acceptance_{N}_stderr.txt` | PO node, per command | Each acceptance command (`nodes_po.py:177–178`) | Yes | No — debugging |

### 5.3 Planner Compile Artifacts

Directory pattern: `{artifacts_dir}/{compile_hash}/compile/`

| Artifact | File | When Written | Trigger | Overwrite | Relied on by later stages |
|---|---|---|---|---|---|
| Rendered prompt | `prompt_rendered.txt` | Start of compile | Always (`compiler.py:216`) | Yes | No — auditability |
| Raw LLM response | `llm_raw_response_attempt_{N}.txt` | Per attempt | After LLM call (`compiler.py:233`) | Yes | No — debugging |
| Raw manifest | `manifest_raw_attempt_{N}.json` | Per attempt, on successful parse | After JSON parse (`compiler.py:266`) | Yes | No — debugging |
| Validation errors | `validation_errors_attempt_{N}.json` | Per attempt | After validation (`compiler.py:251` or `292`) | Yes | No — debugging |
| Normalized manifest | `manifest_normalized.json` | On success | Final validated manifest (`compiler.py:346`) | Yes | No — auditability |
| Compile summary | `compile_summary.json` | Always (success or failure) | End of compile (`compiler.py:387`) | Yes | No — auditability |
| API failure dumps | `raw_response_{label}.json` | On API failure | Various failure labels in `openai_client.py:299–311` | Yes | No — debugging |

### 5.4 Planner Output Artifacts

Directory pattern: `{outdir}/`

| Artifact | File | When Written | Trigger | Overwrite |
|---|---|---|---|---|
| Work order files | `WO-{NN}.json` (e.g., `WO-01.json`) | On success | `io.py:70–76` | Only if `--overwrite` |
| Manifest | `WORK_ORDERS_MANIFEST.json` | On success (written LAST) | `io.py:79–81` | Only if `--overwrite` |
| Validation errors | `validation_errors.json` | On validation failure | `compiler.py:321–322` | Yes |

### 5.5 Shell Script Artifacts

| Artifact | When | Notes |
|---|---|---|
| `README.md` in target repo | Repo init (unless `--no-init`) | Seed content |
| `.gitignore` in target repo | Repo init (unless `--no-init`) | Contains `__pycache__/`, `.pytest_cache/`, `*.pyc` |
| Git commits | After each PASS | `git commit --no-verify -m "$WO_NAME: applied by factory"` |

### 5.6 Score Script Artifacts

None. Output is printed to stdout only (human-readable summary + `---JSON---` delimiter + JSON blob).

---

## 6. FAILURE MODES & EXIT SEMANTICS

### 6.1 Factory Failure Stages

All factory failures produce a `FailureBrief` (defined in `factory/schemas.py:181–193`) with a `stage` field from the `ALLOWED_STAGES` set.

| Stage | Trigger | Classification | Retryable | Exit Code | Artifacts Written |
|---|---|---|---|---|---|
| `preflight` | Precondition `file_exists` false, or `file_absent` true | Planner-contract error | No (fundamentally broken plan) | `1` (FAIL verdict) | `failure_brief.json` |
| `exception` | LLM API call raises exception | Execution error | Yes (transient possible) | `1` (FAIL verdict) | `failure_brief.json` |
| `llm_output_invalid` | LLM response fails JSON parse or `WriteProposal` schema validation | LLM error | Yes (retry with feedback) | `1` (FAIL verdict) | `failure_brief.json`, `raw_llm_response.json` |
| `write_scope_violation` | File not in `allowed_files`, duplicate paths, path escapes repo | Contract error | Yes (retry with feedback) | `1` (FAIL verdict) | `failure_brief.json`, `write_result.json` |
| `stale_context` | `base_sha256` mismatch for any file | Context error | Yes (retry with feedback) | `1` (FAIL verdict) | `failure_brief.json`, `write_result.json` |
| `write_failed` | `_atomic_write()` raises exception | Execution error | Yes (transient possible) | `1` (FAIL verdict) | `failure_brief.json`, `write_result.json` |
| `verify_failed` | Global verify command exits non-zero | Execution error | Yes (retry with feedback) | `1` (FAIL verdict) | `failure_brief.json`, `verify_result.json`, stdout/stderr files |
| `acceptance_failed` | Acceptance command exits non-zero, postcondition false, or command parse error | Execution error | Yes (retry with feedback) | `1` (FAIL verdict) | `failure_brief.json`, `acceptance_result.json`, stdout/stderr files |

### 6.2 Factory Graph-Level Failures

| Trigger | What Happens | Exit Code | Artifacts |
|---|---|---|---|
| All attempts exhausted (FAIL) | `_route_after_finalize` → END | `1` | `run_summary.json` with `verdict: "FAIL"` |
| Unhandled exception in `graph.invoke()` | Emergency handler: best-effort rollback, emergency `run_summary.json` | `2` | `run_summary.json` with `verdict: "ERROR"`, `error`, `error_traceback` |
| Rollback failure in emergency handler | Warning printed, manual restore instructions | `2` (same path) | Best-effort summary (may also fail) |

### 6.3 Factory Preflight Failures

| Trigger | Where | Exit Code | Artifacts |
|---|---|---|---|
| Work order load failure | `run.py:30–33` | `1` | None |
| Not a git repo | `run.py:38–40` | `1` | None |
| Dirty working tree | `run.py:42–48` | `1` | None |
| Output dir inside repo | `run.py:50–57` | `1` | None |
| `--max-attempts < 1` | `__main__.py:56–61` | `1` | None |

### 6.4 Planner Failure Modes

| Trigger | Classification | Exit Code | Artifacts |
|---|---|---|---|
| Spec file not found | File error | `1` | None |
| Template file not found | File error | `1` | None |
| Output dir exists (no `--overwrite`) | File error | `1` | None |
| `OPENAI_API_KEY` not set | API error | `3` | None |
| API transport failure (after retries) | API error | `3` | `raw_response_{label}.json` |
| Polling deadline exceeded (40 min) | API error | `3` | `raw_response_poll_timeout.json` |
| Response incomplete (after retry) | API error | `3` | `raw_response_incomplete_attempt_{N}.json` |
| JSON parse error (all attempts) | Parse error | `4` | `llm_raw_response_attempt_{N}.txt`, `validation_errors_attempt_{N}.json`, `compile_summary.json` |
| Validation errors (all attempts) | Validation error | `2` | `validation_errors.json` (in both outdir and artifacts), `compile_summary.json` |

### 6.5 Rollback Semantics

| Scenario | Rollback Action | Location |
|---|---|---|
| Factory: attempt FAIL | `git reset --hard {baseline} && git clean -fdx` | `graph.py:130` → `workspace.py:89–107` |
| Factory: unhandled exception | Best-effort `rollback()`, may fail | `run.py:119–128` |
| Planner | No rollback needed (writes to output dir, not repo) | N/A |
| Shell script | No automatic rollback; manual `git reset` needed | N/A |

### 6.6 Timeout-Induced Failures

| Location | Timeout Value | What Happens |
|---|---|---|
| Factory `run_command()` | `--timeout-seconds` (default 600) | `subprocess.TimeoutExpired` → `CmdResult` with `exit_code=-1`, `[TIMEOUT]` in stderr | 
| Factory `llm.complete()` | `--timeout-seconds` (default 600) | httpx timeout → exception → `FailureBrief(stage="exception")` |
| Factory git operations | `GIT_TIMEOUT_SECONDS=30` | `subprocess.TimeoutExpired` → `RuntimeError` |
| Planner HTTP calls | `CONNECT=30, READ=60, WRITE=30, POOL=30` | httpx exception → transport retry (up to 3) |
| Planner polling | `POLL_DEADLINE_S=2400` (40 min) | Raises `RuntimeError` after deadline |

---

## 7. CLI EXPOSURE RECOMMENDATIONS (ACTIONABLE)

### 7.1 Minimal Viable CLI Surface (v1)

These flags address the most impactful gaps — parameters that users will need to change for different projects, models, or environments.

| Proposed Flag | Target Command | Maps to | Current Default | Default Preserved? | Risk |
|---|---|---|---|---|---|
| `--model` | `planner compile` | `planner/openai_client.py:DEFAULT_MODEL` | `"gpt-5.2-codex"` | Yes | **Low** — already CLI-exposed on factory side |
| `--max-compile-attempts` | `planner compile` | `planner/compiler.py:MAX_COMPILE_ATTEMPTS` | `3` | Yes | **Low** — bounded integer, easy to validate |
| `--max-output-tokens` | `planner compile` | `planner/openai_client.py:DEFAULT_MAX_OUTPUT_TOKENS` | `64000` | Yes | **Low** — bounded by API limits |
| `--reasoning-effort` | `planner compile` | `planner/openai_client.py:DEFAULT_REASONING_EFFORT` | `"medium"` | Yes | **Low** — enum {low, medium, high} |
| `--wo-dirs` | `score_work_orders.py` | `utils/score_work_orders.py:WO_DIRS` | `["./wo", "./wo2", "./wo3", "./wo4"]` | Yes (if no flag given) | **Low** — read-only tool |

### 7.2 Power-User CLI Surface (v2)

These flags address secondary control points that advanced users or CI pipelines may need.

| Proposed Flag | Target Command | Maps to | Current Default | Default Preserved? | Risk |
|---|---|---|---|---|---|
| `--poll-deadline` | `planner compile` | `planner/openai_client.py:POLL_DEADLINE_S` | `2400` (40 min) | Yes | **Low** — timeout, easily validated |
| `--factory-prompt` | `factory run` | `factory/nodes_se.py:_TEMPLATE_PATH` | `factory/FACTORY_PROMPT.md` | Yes | **Medium** — wrong prompt = broken proposals |
| `--continue-on-failure` | `run_work_orders.sh` | Stop-on-first-failure `break` logic | `false` (stops) | Yes | **Medium** — partial results may confuse |
| `--max-context-bytes` | `factory run` | `factory/nodes_se.py:MAX_CONTEXT_BYTES` | `204800` (200 KB) | Yes | **Medium** — too large = token overflow |
| `--max-file-write-bytes` | `factory run` | `factory/schemas.py:MAX_FILE_WRITE_BYTES` | `204800` (200 KB) | Yes | **Medium** — safety limit, raising is risky |
| `--max-total-write-bytes` | `factory run` | `factory/schemas.py:MAX_TOTAL_WRITE_BYTES` | `512000` (500 KB) | Yes | **Medium** — safety limit |
| `--git-timeout` | `factory run` | `factory/workspace.py:GIT_TIMEOUT_SECONDS` | `30` | Yes | **Low** — edge case for large repos |
| `--max-excerpt-chars` | `factory run` | `factory/util.py:MAX_EXCERPT_CHARS` | `2000` | Yes | **Low** — artifact verbosity |
| `--skip-dirs` | `planner compile` | `planner/compiler.py:_SKIP_DIRS` | Set of 9 directory names | Yes (append, not replace) | **Low** — repo listing filter |

### 7.3 Unified `llmc` CLI Sketch

Based on the above, a unified CLI would have two top-level commands mirroring the current modules:

```
llmc plan   --spec FILE --outdir DIR [--model MODEL] [--reasoning-effort EFFORT]
            [--max-output-tokens N] [--max-compile-attempts N] [--template FILE]
            [--artifacts-dir DIR] [--repo DIR] [--overwrite] [--print-summary]

llmc build  --repo DIR --work-order FILE --out DIR --llm-model MODEL
            [--llm-temperature FLOAT] [--max-attempts N] [--timeout-seconds N]

llmc run    --wo-dir DIR --target-repo DIR --artifacts-dir DIR
            [--model MODEL] [--max-attempts N] [--no-init] [--continue-on-failure]

llmc score  [--wo-dirs DIR [DIR ...]]
```

---

## 8. NON-GOALS / THINGS THAT SHOULD NOT BE CLI FLAGS

The following parameters should **remain internal** and NOT be exposed as CLI flags:

### 8.1 Transport Internals (Planner)

- `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `WRITE_TIMEOUT`, `POOL_TIMEOUT` — httpx transport tuning. Exposing these would create a debugging surface area without meaningful user benefit. If they need tuning, it's a code change.
- `MAX_TRANSPORT_RETRIES`, `TRANSPORT_RETRY_BASE_S` — retry policy for HTTP errors. These implement standard resilience patterns; changing them risks silent data loss or API abuse.
- `POLL_INTERVAL_S` — polling frequency. Too aggressive = rate limiting; too slow = wasted time. Current value is well-tuned.
- `MAX_INCOMPLETE_RETRIES` — budget-doubling retry. Only matters for edge cases; the incomplete retry budget formula is coupled to this.
- Retryable HTTP status codes (`{429, 502, 503, 504}`) — defined by HTTP/API semantics, not user preference.

### 8.2 Safety Limits

- `MAX_FILE_WRITE_BYTES` and `MAX_TOTAL_WRITE_BYTES` — these are **safety guardrails**. Raising them increases the blast radius of malformed LLM output. If they must be raised, it should be a deliberate code change with review, not a flag.
- `context_files` max (10 entries) — this is a prompt-budget constraint. More context files = more tokens = higher cost and risk of context confusion. The limit is structural.
- `MAX_CONTEXT_BYTES` (200 KB) — same rationale. Prompt budget.

### 8.3 Structural Contracts

- `VERIFY_COMMAND` / `VERIFY_SCRIPT_PATH` — the `scripts/verify.sh` convention is baked into the planner prompt, factory detection logic, and validation rules. Changing it via CLI without updating all three would break the system.
- `WO_ID_PATTERN` (`WO-NN`) — format contract between planner and factory. Changing it breaks all validation.
- `SHELL_OPERATOR_TOKENS` — enforcement of `shell=False` execution. Relaxing this would allow shell injection.
- `ALLOWED_STAGES` — failure taxonomy. Adding new stages requires corresponding handling code.
- `_STDLIB_TOP_LEVEL` — heuristic for dependency analysis. False negatives produce spurious warnings (W101), not hard errors. Safe to extend in code, dangerous to expose.
- `REQUIRED_PLACEHOLDER` / `OPTIONAL_PLACEHOLDERS` — template contract.

### 8.4 Determinism Invariants

- `run_id` computation (`sha256(work_order + baseline_commit)[:16]`) — changing the hash would break artifact directory reproducibility.
- `compile_hash` computation — same rationale.
- `canonical_json_bytes` format (sorted keys, minimal separators) — determinism contract.
- Rollback strategy (`git reset --hard` + `git clean -fdx`) — changing this breaks the clean-slate guarantee.
- Tree hash scoping (stage only `touched_files`) — prevents verification artifacts from polluting the hash.

### 8.5 Prompt Architecture

- Single `"user"` role message in factory LLM calls — the prompt is designed for single-turn. Adding system messages would require prompt redesign.
- JSON fence stripping in both `factory/llm.py` and `planner/compiler.py` — resilience behavior, not configuration.
- Revision prompt structure (`_build_revision_prompt`) — tightly coupled to validation error format.

### 8.6 Git Configuration (Shell Script)

- `user.email` / `user.name` in `run_work_orders.sh` — these are local repo config, not global. Parameterizing them as CLI flags would add complexity for a CI-only concern. If needed for cloud deployment, use environment variables or git config at the CI level.

---

## Appendix A: Cross-Reference of Artifact Filename Constants

All artifact filenames are defined in `factory/util.py:198–208`:

```
ARTIFACT_SE_PROMPT        = "se_prompt.txt"
ARTIFACT_PROPOSED_WRITES  = "proposed_writes.json"
ARTIFACT_RAW_LLM_RESPONSE = "raw_llm_response.json"
ARTIFACT_WRITE_RESULT     = "write_result.json"
ARTIFACT_VERIFY_RESULT    = "verify_result.json"
ARTIFACT_ACCEPTANCE_RESULT = "acceptance_result.json"
ARTIFACT_FAILURE_BRIEF    = "failure_brief.json"
ARTIFACT_WORK_ORDER       = "work_order.json"
ARTIFACT_RUN_SUMMARY      = "run_summary.json"
```

Planner artifacts do NOT use centralized constants — filenames are inline strings in `compiler.py` and `openai_client.py`.

## Appendix B: Default Value Discrepancies

| Parameter | Location 1 | Value 1 | Location 2 | Value 2 | Impact |
|---|---|---|---|---|---|
| LLM model | `planner/openai_client.py:25` | `"gpt-5.2-codex"` | `utils/run_work_orders.sh:20` | `"gpt-5.2"` | Different models for planning vs execution |
| Max attempts | `factory/__main__.py:30` | `2` | `utils/run_work_orders.sh:21` | `5` | Shell script is more aggressive than direct CLI |
| LLM timeout | `factory/llm.py:9` | `120` s | `factory/__main__.py:46` | `600` s | CLI default overrides function default; direct callers get 120 |

## Appendix C: Error Code Registry (Planner Validation)

Defined in `planner/validation.py:28–46`:

| Code | Constant | Scope | Description |
|---|---|---|---|
| `E000` | `E000_STRUCTURAL` | Per-manifest | Top-level structure error (empty list, not a dict, missing `work_orders` key) |
| `E001` | `E001_ID` | Per-WO | ID format or contiguity error |
| `E002` | `E002_VERIFY` | Per-WO | **REMOVED** — formerly required verify in acceptance; now banned |
| `E003` | `E003_SHELL_OP` | Per-WO | Shell operator token in acceptance command |
| `E004` | `E004_GLOB` | Per-WO | Glob character in path field |
| `E005` | `E005_SCHEMA` | Per-WO | Pydantic `WorkOrder` schema validation failure |
| `E006` | `E006_SYNTAX` | Per-WO | Python syntax error in `python -c` acceptance command |
| `E101` | `E101_PRECOND` | Cross-WO | Precondition not satisfiable by cumulative state |
| `E102` | `E102_CONTRADICTION` | Per-WO | Same path in both `file_exists` and `file_absent` preconditions |
| `E103` | `E103_POST_OUTSIDE` | Per-WO | Postcondition path not in `allowed_files` |
| `E104` | `E104_NO_POSTCOND` | Per-WO | `allowed_files` entry has no `file_exists` postcondition |
| `E105` | `E105_VERIFY_IN_ACC` | Per-WO | `bash scripts/verify.sh` appears in `acceptance_commands` |
| `E106` | `E106_VERIFY_CONTRACT` | Cross-WO | Verify contract never fully satisfied by plan |
| `W101` | `W101_ACCEPTANCE_DEP` | Cross-WO | Acceptance command depends on file not in cumulative state (warning only) |
