# ARCHITECTURE.md

Describes the implemented system as of 2026-02-14, based on source code
inspection of `planner/`, `factory/`, `shared/`, and `llmch/`.

---

## 1. System Overview

The system compiles a natural-language product specification into a sequence
of work orders, then executes them one at a time against a target git
repository to produce a working software project.

Three packages operate in sequence:

- **Planner** (`planner/`): An LLM generates a JSON manifest of work orders
  from a spec file. A deterministic validator checks the manifest for
  structural and cross-work-order consistency. On validation failure, the
  LLM is re-prompted with structured error codes (up to 5 attempts). On
  success, individual `WO-NN.json` files are written to disk with provenance
  metadata.

- **Factory** (`factory/`): A LangGraph state machine executes one work
  order at a time. An LLM proposes file writes; deterministic code validates
  scope and hashes, applies the writes atomically, then runs verification
  and acceptance commands. On failure, the repo is rolled back and the LLM
  is retried with the failure details. On pass, changes are committed to a
  working branch and pushed.

- **Unified CLI** (`llmch/`): A thin subprocess-delegation wrapper exposing
  `llmch plan`, `llmch run`, and `llmch run-all` as the primary user
  interface. Installable via `pip install -e .` as a console script.

LLMs are used at exactly three points:

| Touchpoint | Module | Purpose |
|-----------|--------|---------|
| Planner generation | `planner/openai_client.py` | Generate work order JSON from spec |
| Planner revision | `planner/openai_client.py` (same client) | Fix validation errors on retry |
| Factory SE node | `factory/llm.py` | Generate file write proposals |

Everything else is deterministic Python code. The planner uses the OpenAI
Responses API with SSE streaming (fallback to background polling). The
factory uses the Chat Completions API via the `openai` SDK.

Shared infrastructure lives in `shared/run_context.py`: ULID generation,
SHA-256 hashing, artifact root resolution, `run.json` management, and tool
version detection.

---

## 2. Major Components

### 2.1 Planner Subsystem

#### CLI (`planner/cli.py`)

- Entry point: `python -m planner compile` (or `llmch plan`)
- Required flags: `--spec`
- Optional flags: `--outdir`, `--template`, `--artifacts-dir`, `--repo`,
  `--overwrite`, `--print-summary`, `--verbose`, `--quiet`, `--no-color`
- The `--repo` flag provides a path to the target repository for
  precondition validation against the actual file listing.
- The `--outdir` flag exports work orders to a convenience directory in
  addition to the canonical artifact location.
- Exit codes: 0 (success), 1 (general error), 2 (validation error),
  3 (API error), 4 (JSON parse error).

#### Defaults (`planner/defaults.py`)

All tunable constants for the planner subsystem. Key values:
- `DEFAULT_MODEL = "gpt-5.2-codex"`, `DEFAULT_REASONING_EFFORT = "medium"`
- `DEFAULT_MAX_OUTPUT_TOKENS = 64000`
- `MAX_COMPILE_ATTEMPTS = 5` (1 initial + up to 4 revision retries)
- `MAX_JSON_PAYLOAD_BYTES = 10 MB` (payload size guard)
- Transport/polling timeouts, retry counts, path constants, shell operator
  token set.

#### Compiler (`planner/compiler.py`)

- Orchestrates: prompt rendering → LLM call → JSON parse → validation →
  retry → verify_exempt computation → provenance injection → file output.
- Retry loop: `MAX_COMPILE_ATTEMPTS = 5`. On validation failure, builds a
  revision prompt containing structured `[E0xx/E1xx]` error codes and the
  previous response, then re-calls the LLM. Warnings (`W`-prefixed codes)
  do not block compilation.
- On success, calls `compute_verify_exempt()` to inject `verify_exempt`
  into each work order dict. Never trusts LLM-provided `verify_exempt` —
  always overwrites (M-01 fix).
- Injects provenance into each work order: `planner_run_id`, `compile_hash`,
  `manifest_sha256`, and `bootstrap` (true for verify-exempt WOs).
- If the target repo already contains `scripts/verify.sh`, filters out
  the bootstrap WO and renumbers remaining WOs.
- Run ID: 26-character ULID (sortable, collision-resistant).
- Writes `run.json` early (incomplete), updates on completion.
- Produces per-attempt artifacts in `artifacts/planner/{run_id}/compile/`:
  `prompt_attempt_{N}.txt`, `llm_raw_response_attempt_{N}.txt`,
  `llm_reasoning_attempt_{N}.txt`, `manifest_raw_attempt_{N}.json`,
  `validation_errors_attempt_{N}.json`.
- Canonical output: `artifacts/planner/{run_id}/output/WO-*.json` +
  `WORK_ORDERS_MANIFEST.json`.
- Produces `compile_summary.json` with attempt count, errors, timing, and
  defaults snapshot.
- Deterministic compile hash: `SHA-256(spec + template + model + reasoning_effort)[:16]`.

#### Validator (`planner/validation.py`)

Two validation layers run in sequence:

**Structural validation** (`validate_plan`): per-work-order checks.

| Code | Check |
|------|-------|
| E000 | Top-level JSON structure (empty list, missing keys, non-dict elements) |
| E001 | ID format (`WO-NN`) and contiguity |
| E003 | Shell operators in acceptance commands (`shell=False` incompatible) |
| E004 | Glob characters in file paths |
| E005 | Pydantic `WorkOrder` schema conformance |
| E006 | Python syntax errors in `python -c` acceptance commands (via `ast.parse`) |
| E007 | Unparseable acceptance command (`shlex.split` failure) |

**Chain validation** (`validate_plan_v2`): cross-work-order consistency.

| Code | Check |
|------|-------|
| E101 | Precondition unsatisfied (file not in cumulative state) |
| E102 | Contradictory preconditions (same path both `file_exists` and `file_absent`) |
| E103 | Postcondition path not in `allowed_files` |
| E104 | `allowed_files` entry has no corresponding postcondition |
| E105 | `bash scripts/verify.sh` in `acceptance_commands` (banned; normalized via `shlex.split`) |
| E106 | `verify_contract` never fully satisfied by the plan |
| W101 | Acceptance command imports a module not guaranteed to exist (warning only) |

Chain validation tracks a cumulative file state set, initialized from the
target repo's file listing (if `--repo` was provided) and advanced by each
work order's `file_exists` postconditions.

**Additional functions:**
- `compute_verify_exempt()`: marks each work order as `verify_exempt: true`
  if the cumulative state after it does not satisfy all `verify_contract`
  requirements. Returns new dicts; does not mutate input. Guarded against
  non-dict `verify_contract` (M-03).
- `normalize_work_order()`: strips whitespace, applies `posixpath.normpath`
  to all path fields, deduplicates list fields (M-06).
- `extract_file_dependencies()`: parses `python -c` imports via `ast`,
  `bash` script paths, and `python` script paths. Excludes stdlib via a
  hardcoded allowlist.

All validation errors are `ValidationError` dataclass instances with
fields `code`, `wo_id`, `message`, `field`. Type guards prevent crashes
on non-dict work order elements (M-03).

#### Prompt Template (`planner/prompt_template.py`)

- Loads a markdown template file.
- Substitutes `{{PRODUCT_SPEC}}` (required) and optional placeholders.
- Default template: `planner/PLANNER_PROMPT.md`.

#### OpenAI Client (`planner/openai_client.py`)

- Primary mode: SSE streaming (`stream=true`) for real-time reasoning
  output. Reasoning summary deltas are printed to stderr as they arrive.
- Fallback mode: background polling (`background=true`) if the stream
  connection fails (load balancer drops, timeout, etc.).
- Transport retries (3 attempts) for HTTP 429/502/503/504 and connection errors.
- Retries once with doubled token budget on `incomplete` status.
- Configurable via `planner/defaults.py`: model, reasoning effort, max
  output tokens. Defaults: `gpt-5.2-codex`, `medium`, `64000`.
- Returns `LLMResult(text, reasoning)` — both output text and reasoning
  summary.

#### IO (`planner/io.py`)

- Atomic file writes via `tempfile.mkstemp` → `fsync` → `os.replace`.
- `write_work_orders()`: writes individual `WO-NN.json` files then
  `WORK_ORDERS_MANIFEST.json` (manifest last).
- `check_overwrite()`: refuses to write into a directory with existing WO
  files unless `--overwrite` is set.

### 2.2 Factory Subsystem

#### CLI / Run (`factory/run.py`, `factory/__main__.py`)

- Entry point: `python -m factory run` (or `llmch run`)
- Required flags: `--repo`, `--work-order`
- Optional flags: `--llm-model` (default `gpt-5.2`), `--max-attempts`
  (default 5), `--llm-temperature` (default 0), `--timeout-seconds`
  (default 600), `--branch`, `--create-branch`, `--reuse-branch`,
  `--commit-hash`, `--no-push`, `--allow-verify-exempt`, `--out`,
  `--artifacts-dir`, `--verbose`, `--quiet`, `--no-color`
- Preflight checks: repo is a git repo, has at least one commit, working
  tree is clean, not on a protected branch (main/master), not in detached
  HEAD state.
- Sets local git identity (`user.name`, `user.email`) without touching
  global config.
- Run ID: 26-character ULID.
- Writes `run.json` early (incomplete), updates on completion.
- Verify-exempt policy (`_check_verify_exempt_policy`): auto-honors
  `verify_exempt=true` for trusted planner bootstrap WOs (provenance with
  `bootstrap=true` + `planner_run_id`). Fails fast for others unless
  `--allow-verify-exempt` is passed.
- On PASS: scoped commit (only `touched_files`), then `clean_untracked()`
  to remove verification artifacts, then push to remote.
- On unhandled exception: catches `BaseException` (including
  `KeyboardInterrupt`), best-effort rollback, emergency `run_summary.json`
  with `rollback_failed` status.

#### Defaults (`factory/defaults.py`)

All tunable constants for the factory subsystem. Key values:
- `DEFAULT_MAX_ATTEMPTS = 5`, `DEFAULT_LLM_MODEL = "gpt-5.2"`
- `GIT_AUTO_COMMIT = True`, `GIT_AUTO_PUSH = True`
- `GIT_BRANCH_PREFIX = "factory/"`, `GIT_PROTECTED_BRANCHES = {"main", "master"}`
- Size limits: 200 KB per file, 500 KB total, 10 context files
- `MAX_JSON_PAYLOAD_BYTES = 10 MB` (payload size guard)
- Artifact filename constants, verify command paths, allowed failure stages.

#### Console (`factory/console.py`)

Shared structured terminal output used by both planner and factory CLIs.
Supports three verbosity levels (`quiet`, `normal`, `verbose`) and
auto-detected ANSI color. Methods: `header()`, `kv()`, `attempt_start()`,
`step()`, `error_block()`, `verdict()`, `warning()`, `error()`,
`rollback_notice()`, etc.

#### Graph (`factory/graph.py`)

- LangGraph `StateGraph` with four nodes: `se`, `tr`, `po`, `finalize`.
- Entry point: `se`.
- Routing:
  - After SE: proceed to TR if proposal produced, else finalize.
  - After TR: proceed to PO if writes applied, else finalize.
  - After PO: always finalize.
  - After finalize: END on PASS, non-retryable failure, or exhausted
    attempts; else retry from SE.
- Non-retryable stages: `{"preflight", "write_failed"}` — abort immediately.
- State is a `TypedDict` (`FactoryState`) with per-attempt fields reset by
  finalize between attempts.

#### SE Node (`factory/nodes_se.py`)

- **Precondition gate**: before any LLM call, checks every
  `work_order.preconditions` entry against the filesystem. On failure,
  returns `FailureBrief(stage="preflight")` with "PLANNER-CONTRACT BUG"
  prefix. The LLM is never called.
- Reads context files from the repo (bounded by 200 KB total).
- Builds a prompt from `FACTORY_PROMPT.md` template containing: work order
  details, file contents with SHA-256 hashes, previous failure brief (if
  retrying), output format instructions.
- Calls the LLM via `factory/llm.py` (Chat Completions API).
- Parses the response as a `WriteProposal` (pydantic model).
- Persists: `se_prompt.txt`, `proposed_writes.json`.

#### TR Node (`factory/nodes_tr.py`)

- Validates the `WriteProposal` against the `WorkOrder`:
  1. Duplicate path check.
  2. Scope check: all paths in `allowed_files`.
  3. Path safety: all paths resolve inside repo root.
  4. Base-hash check: all files checked before any writes (batch invariant).
- Applies writes atomically (per-file: `tempfile` → `fsync` → `os.replace`).
- Persists: `write_result.json`.

#### PO Node (`factory/nodes_po.py`)

- **Global verification**: if `work_order.verify_exempt` is `True`, runs
  only `python -m compileall -q .`. Otherwise, runs `_get_verify_commands()`:
  `bash scripts/verify.sh` if it exists, else fallback
  (`compileall` + `pip --version` + `pytest -q`). The `verify_exempt` field
  is treated as an opaque IR field — the factory does not inspect WO content
  to infer intent.
- **Postcondition gate**: after verify, before acceptance. Checks each
  `work_order.postconditions` entry. On failure, returns
  `FailureBrief(stage="acceptance_failed")` (retryable).
- **Acceptance commands**: runs each command from
  `work_order.acceptance_commands` via `subprocess.run(shell=False)`.
- All subprocesses use a sandboxed environment (`_sandboxed_env`) with
  `PYTHONDONTWRITEBYTECODE=1` and pytest cache suppression.
- Persists: `verify_result.json`, `acceptance_result.json`, per-command
  `verify_{K}_stdout.txt`/`stderr.txt`, `acceptance_{K}_stdout.txt`/`stderr.txt`.

#### Finalize Node (`factory/graph.py` `_finalize_node`)

- Records the attempt (proposal path, touched files, verify/acceptance
  results, failure brief).
- On FAIL: rolls back via `git reset --hard` + `git clean -fdx`.
- On PASS: detects repo drift (unexpected files outside `touched_files`),
  records `repo_drift` in the attempt record, computes
  `repo_tree_hash_after` by staging touched files and running
  `git write-tree`.
- Increments attempt index and resets per-attempt state fields.

#### Schemas (`factory/schemas.py`)

Pydantic v2 models defining the contract surface:

- `Condition`: `kind` (`file_exists` | `file_absent`) + `path`. All paths
  validated as safe relative paths (no `..`, no `\`, no NUL, no control
  chars, no `.`, no absolute paths, no drive letters, no glob characters).
- `WorkOrder`: `id`, `title`, `intent`, `preconditions`, `postconditions`,
  `allowed_files`, `forbidden`, `acceptance_commands`, `context_files`,
  `notes`, `verify_exempt`. Postconditions restricted to `file_exists` only.
  Context files capped at 10.
- `FileWrite`: `path`, `base_sha256`, `content`.
- `WriteProposal`: `summary` + `writes` (non-empty). Size limits: 200 KB
  per file, 500 KB total.
- `FailureBrief`: `stage` (from fixed set), optional `command`/`exit_code`,
  `primary_error_excerpt`, `constraints_reminder`.
- `CmdResult`: command, exit code, truncated stdout/stderr, file paths,
  duration.

#### Utilities (`factory/util.py`)

- `sha256_bytes`, `sha256_file`: hashing.
- `canonical_json_bytes`: deterministic JSON serialization.
- `compute_run_id`: deterministic run ID from work order + baseline commit.
- `save_json`: atomic JSON writes (`tempfile` → `fsync` → `os.replace`).
- `run_command`: executes a command with `subprocess.run(shell=False)` and
  sandboxed environment, captures output to files, handles `TimeoutExpired`
  and `OSError`.
- `split_command`: `shlex.split` wrapper.
- `normalize_path`, `is_path_inside_repo`: path safety.
- `_sandboxed_env`: builds subprocess environment with
  `PYTHONDONTWRITEBYTECODE=1` and `PYTEST_ADDOPTS=-p no:cacheprovider`.
- Artifact filename constants.

#### Workspace (`factory/workspace.py`)

- Git operations via `subprocess.run(["git", ...], shell=False)`.
- `is_git_repo`, `is_clean`, `has_commits`: preflight queries.
- `get_baseline_commit`: `git rev-parse HEAD`.
- `rollback`: `git reset --hard` + `git clean -fdx`.
- `clean_untracked`: `git clean -fdx` (post-commit cleanup on PASS).
- `detect_repo_drift`: identifies modified/untracked files outside
  `touched_files` via `git status --porcelain`.
- `get_tree_hash`: stages touched files, runs `git write-tree`.
- `git_commit`: scoped staging (`git add -- <files>`) or full staging
  (`git add -A` fallback). Handles "nothing to commit" from both stdout
  and stderr.
- `ensure_working_branch`: creates or reuses a branch from a baseline
  commit. Supports `require_exists` and `require_new` modes.
- `ensure_git_identity`: sets local `user.name`/`user.email`.
- `git_push_branch`: pushes to first discovered remote with `-u`.

### 2.3 Shared Infrastructure

#### Run Context (`shared/run_context.py`)

Cross-subsystem utilities used by both planner and factory:
- `generate_ulid()`: 26-character, lexicographically sortable, collision-
  resistant run IDs.
- `utc_now_iso()`: UTC timestamps with microseconds.
- `sha256_bytes`, `sha256_file`, `sha256_json`: hashing.
- `resolve_artifacts_root()`: CLI > `$ARTIFACTS_DIR` > `./artifacts`.
- `write_run_json`, `read_run_json`: atomic `run.json` management.
- `get_tool_version()`: git commit hash + dirty flag of the tool repo.

### 2.4 Unified CLI

#### `llmch/__main__.py`

Thin subprocess-delegation wrapper. Three subcommands:
- `llmch plan` → `python -m planner compile`
- `llmch run` → `python -m factory run`
- `llmch run-all` → sequential `factory run` calls for all `WO-*.json`
  files in a directory.

`run-all` discovers WO files, sorts numerically by WO index, delegates
each via the shared `_build_factory_cmd()`, and stops on first failure.
First WO honors `--create-branch`; subsequent WOs auto-switch to
`--reuse-branch`. Extra args after `--` forwarded to each invocation.

Installable as console script via `pyproject.toml`:
`[project.scripts] llmch = "llmch.__main__:main"`.

---

## 3. Data Flow

```
spec.txt ──► llmch plan ──► WO-01.json ... WO-NN.json
                │                        │
                │ (LLM call              │
                │  + validation          │
                │  + retry loop)         │
                ▼                        ▼
         compile artifacts     llmch run / run-all (per WO)
                                         │
                              ┌──────────┼──────────┐
                              ▼          ▼          ▼
                             SE         TR         PO
                          (LLM call) (writes)  (verify +
                                               acceptance)
                              │          │          │
                              └──────────┼──────────┘
                                         ▼
                                     finalize
                                   (PASS → commit
                                          + clean
                                          + push,
                                    FAIL → rollback
                                          + retry)
```

**Validation points** (compile time, no execution):
- JSON parse of LLM output (with 10 MB payload size guard).
- `validate_plan()`: E000-E007 per-WO structural checks.
- `validate_plan_v2()`: E101-E106, W101 cross-WO chain checks.
- `compute_verify_exempt()`: per-WO verify exemption.

**Validation points** (factory runtime, deterministic):
- SE node: precondition gate (`file_exists` / `file_absent` checks).
- TR node: scope, path safety, base-hash batch check.
- PO node: global verify, postcondition gate, acceptance commands.

**Failure handling:**
- Compile: structured errors fed back to LLM via revision prompt (up to 5
  attempts). If all fail, validation errors written to disk.
- Factory SE: LLM exception → `FailureBrief(stage="exception")`. Parse
  failure → `FailureBrief(stage="llm_output_invalid")`. Precondition failure
  → `FailureBrief(stage="preflight")` (non-retryable).
- Factory TR: scope/hash/write failures → `FailureBrief` with appropriate
  stage. `write_failed` is non-retryable.
- Factory PO: verify failure → `FailureBrief(stage="verify_failed")`.
  Postcondition failure → `FailureBrief(stage="acceptance_failed")`.
  Acceptance failure → `FailureBrief(stage="acceptance_failed")`.
- Factory finalize: on any FAIL → `git reset --hard` + `git clean -fdx` →
  retry from SE with the failure brief injected into the prompt.
- Emergency handler: catches `BaseException` (including `KeyboardInterrupt`),
  best-effort rollback, emergency `run_summary.json` with `rollback_failed`
  status. Exit code 130 for SIGINT, re-raise for `SystemExit`.

**Rollback:**
- Performed by `workspace.rollback()` in the finalize node.
- Uses `git reset --hard <baseline>` + `git clean -fdx`.
- Safe even when no writes were applied (idempotent).
- Also performed as best-effort in the `BaseException` emergency handler.

**Post-commit cleanup:**
- After scoped commit on PASS, `clean_untracked()` removes verification
  artifacts (`__pycache__/`, `.pytest_cache/`, etc.) so the repo is clean
  for the next work order.

---

## 4. Execution Model

**Planner compile loop:**
- Up to `MAX_COMPILE_ATTEMPTS` (5) iterations.
- Each iteration: LLM call → parse → structural validation → chain
  validation → if hard errors, build revision prompt and loop.
- Warnings (W-codes) are non-blocking.
- If first attempt passes, the loop exits immediately.

**Factory attempt loop:**
- Up to `max_attempts` (default 5) iterations per work order.
- Each iteration: SE → TR → PO → finalize.
- Routing is conditional: SE failure skips TR/PO; TR failure skips PO.
- Non-retryable stages (`preflight`, `write_failed`) abort immediately.
- On failure, finalize rolls back the repo and increments attempt_index.
  The failure brief is preserved in state so SE can include it in the next
  prompt.
- On success, finalize detects repo drift, computes `repo_tree_hash_after`,
  and sets verdict to PASS.

**Git workflow:**
- Factory creates or reuses a working branch (auto-generated or explicit).
  Protected branches (main/master) are never committed to.
- On PASS: scoped `git add -- <touched_files>` + `git commit --no-verify`,
  then `git clean -fdx` to remove verification artifacts, then
  `git push -u origin <branch>`.
- Branch naming: `factory/{planner_run_id}/{session_ulid}` (with provenance)
  or `factory/adhoc/{session_ulid}` (without).

**Determinism enforcement:**
- `subprocess.run(shell=False)`: no shell interpretation in acceptance or
  verify commands.
- `base_sha256`: every file write must declare the expected current hash.
  All hashes are checked before any writes (batch invariant — no partial
  writes on stale context).
- `allowed_files`: the SE LLM may only propose writes to paths listed in
  the work order. TR enforces this.
- Path safety: all paths validated as relative, no `..`, no `\`, no drive
  letters, no NUL, no control chars, must resolve inside repo root.
- Size limits: 200 KB per file, 500 KB total, 10 context files, 10 MB
  JSON payload, 2000-char error excerpts.
- Atomic writes: temp file → fsync → os.replace (per file, not transactional
  across files).
- Deterministic compile hashes from content hashing. ULID-based run IDs
  for artifact directories (sortable, collision-resistant).
- Subprocess environment sandboxing: `PYTHONDONTWRITEBYTECODE=1` +
  pytest cache suppression.

---

## 5. Trust Boundaries

**What the planner LLM is trusted to do:**
- Produce valid JSON matching the manifest schema.
- Decompose a spec into a reasonable sequence of work orders.
- Write acceptance commands that test the stated intent.

**What the planner LLM is NOT trusted to do (enforced by the validator):**
- Produce contiguous IDs (E001).
- Avoid shell operators in acceptance commands (E003).
- Avoid glob characters in paths (E004).
- Conform to the WorkOrder pydantic schema (E005).
- Write syntactically valid Python in `python -c` commands (E006).
- Write parseable shell syntax in commands (E007).
- Satisfy precondition chains across work orders (E101).
- Avoid contradictory preconditions (E102).
- Declare postconditions only for files it's allowed to write (E103).
- Declare postconditions for all files it's allowed to write (E104).
- Not include the verify command in acceptance (E105).
- Produce a plan where the verify contract is eventually satisfied (E106).
- Set `verify_exempt` correctly (M-01: always overwritten by planner).

**What the factory SE LLM is trusted to do:**
- Produce a JSON `WriteProposal` with valid structure.
- Write code that implements the work order's intent.

**What the factory SE LLM is NOT trusted to do (enforced by TR and PO):**
- Write to files outside `allowed_files` (TR scope check).
- Write to files whose content has changed since context was read (TR hash
  check).
- Write paths that escape the repo root (TR path safety check).
- Produce files that satisfy postconditions (PO postcondition gate).
- Produce code that passes global verification (PO verify).
- Produce code that passes acceptance commands (PO acceptance).

**What the factory enforces unconditionally (no trust involved):**
- Preconditions hold before SE runs (precondition gate — plan-level error
  if violated).
- Rollback to baseline on any failure (including `KeyboardInterrupt`).
- All commands run with `shell=False` in sandboxed environment.
- All artifacts are persisted for post-mortem inspection.
- Scoped commits contain only proposal-touched files.
- Post-commit cleanup removes verification artifacts.

---

## 6. Out of Scope by Design

- **Semantic correctness of generated code.** The SE LLM may write code that
  compiles, passes verification, and passes acceptance commands, but is
  logically wrong. The architecture enforces structural correctness of the
  plan and mechanical execution of the contract, not the quality of the
  LLM-generated code.

- **Semantic correctness of acceptance commands.** The planner LLM may write
  an acceptance command that tests the wrong thing (e.g., asserts a
  hardcoded output value that the planner fabricated without running the
  code). The validator checks that acceptance commands are syntactically
  valid and that their imports reference files that will exist, but cannot
  verify that the assertions are factually correct.

- **Content validation of the `notes` field.** Notes are free text injected
  into the SE prompt. The system does not parse, validate, or enforce
  consistency of notes content.

- **Transactional multi-file writes.** File writes are atomic per-file
  (temp → fsync → replace) but not transactional across files. If a write
  fails partway through a proposal, some files may have been written.
  Rollback via git handles this, but there is no application-level
  transaction.

- **Dependency management.** The system does not install packages, manage
  virtual environments, or resolve import dependencies beyond the stdlib
  allowlist in the validator.

- **Concurrent execution.** Work orders are executed strictly sequentially.
  There is no parallelism within or across work orders.

- **OS-level sandboxing.** Acceptance commands and LLM-authored code run
  with the operator's full privileges. `shell=False` prevents shell
  injection but does not sandbox the subprocess. A container or equivalent
  isolation is recommended for untrusted workloads.

- **Target repo language.** The system is designed for Python projects
  (`pytest` as default verifier, `compileall` as fallback, `python -c` for
  acceptance commands) but the factory itself is language-agnostic at the
  execution level (`subprocess.run` with arbitrary commands).
