# ARCHITECTURE.md

Describes the implemented system as of 2026-02-09, based on source code
inspection of `planner/` and `factory/`.

---

## 1. System Overview

The system compiles a natural-language product specification into a sequence
of work orders, then executes them one at a time against a target git
repository to produce a working software project.

Two subsystems operate in sequence:

- **Planner** (`planner/`): An LLM generates a JSON manifest of work orders
  from a spec file. A deterministic validator checks the manifest for
  structural and cross-work-order consistency. On validation failure, the
  LLM is re-prompted with structured error codes (up to 3 attempts). On
  success, individual `WO-NN.json` files are written to disk.

- **Factory** (`factory/`): A LangGraph state machine executes one work
  order at a time. An LLM proposes file writes; deterministic code validates
  scope and hashes, applies the writes atomically, then runs verification
  and acceptance commands. On failure, the repo is rolled back and the LLM
  is retried with the failure details.

LLMs are used at exactly three points:

| Touchpoint | Module | Purpose |
|-----------|--------|---------|
| Planner generation | `planner/openai_client.py` | Generate work order JSON from spec |
| Planner revision | `planner/openai_client.py` (same client) | Fix validation errors on retry |
| Factory SE node | `factory/llm.py` | Generate file write proposals |

Everything else is deterministic Python code. The planner uses the OpenAI
Responses API with background polling. The factory uses the Chat Completions
API.

---

## 2. Major Components

### 2.1 Planner Subsystem

#### CLI (`planner/cli.py`)

- Entry point: `python -m planner compile`
- Required flags: `--spec`, `--outdir`
- Optional flags: `--template`, `--artifacts-dir`, `--repo`, `--overwrite`,
  `--print-summary`
- The `--repo` flag provides a path to the target repository for
  precondition validation against the actual file listing.
- Exit codes: 0 (success), 1 (general error), 2 (validation error),
  3 (API error), 4 (JSON parse error).

#### Compiler (`planner/compiler.py`)

- Orchestrates: prompt rendering → LLM call → JSON parse → validation →
  retry → verify_exempt computation → file output.
- Retry loop: `MAX_COMPILE_ATTEMPTS = 3`. On validation failure, builds a
  revision prompt containing structured `[E1xx]` error codes and the
  previous response, then re-calls the LLM. Warnings (`W`-prefixed codes)
  do not block compilation.
- On success, calls `compute_verify_exempt()` to inject `verify_exempt`
  into each work order dict before writing.
- Produces per-attempt artifacts: `llm_raw_response_attempt_{N}.txt`,
  `validation_errors_attempt_{N}.json`, `manifest_raw_attempt_{N}.json`.
- Produces `compile_summary.json` with attempt count, errors, and timing.
- Deterministic compile hash: `SHA-256(spec + template + model + reasoning_effort)[:16]`.

#### Validator (`planner/validation.py`)

Two validation layers run in sequence:

**Structural validation** (`validate_plan`): per-work-order checks.

| Code | Check |
|------|-------|
| E000 | Top-level JSON structure (empty list, missing keys) |
| E001 | ID format (`WO-NN`) and contiguity |
| E003 | Shell operators in acceptance commands (`shell=False` incompatible) |
| E004 | Glob characters in file paths |
| E005 | Pydantic `WorkOrder` schema conformance |
| E006 | Python syntax errors in `python -c` acceptance commands (via `ast.parse`) |

**Chain validation** (`validate_plan_v2`): cross-work-order consistency.

| Code | Check |
|------|-------|
| E101 | Precondition unsatisfied (file not in cumulative state) |
| E102 | Contradictory preconditions (same path both `file_exists` and `file_absent`) |
| E103 | Postcondition path not in `allowed_files` |
| E104 | `allowed_files` entry has no corresponding postcondition |
| E105 | `bash scripts/verify.sh` in `acceptance_commands` (banned; factory handles it) |
| E106 | `verify_contract` never fully satisfied by the plan |
| W101 | Acceptance command imports a module not guaranteed to exist (warning only) |

Chain validation tracks a cumulative file state set, initialized from the
target repo's file listing (if `--repo` was provided) and advanced by each
work order's `file_exists` postconditions.

**Additional functions:**
- `compute_verify_exempt()`: marks each work order as `verify_exempt: true`
  if the cumulative state after it does not satisfy all `verify_contract`
  requirements. Returns new dicts; does not mutate input.
- `extract_file_dependencies()`: parses `python -c` imports via `ast`,
  `bash` script paths, and `python` script paths. Excludes stdlib via a
  hardcoded allowlist.

All validation errors are `ValidationError` dataclass instances with
fields `code`, `wo_id`, `message`, `field`.

#### Prompt Template (`planner/prompt_template.py`)

- Loads a markdown template file.
- Substitutes `{{PRODUCT_SPEC}}` (required) and optional placeholders.
- Default template: `planner/PLANNER_PROMPT.md`.

#### OpenAI Client (`planner/openai_client.py`)

- Uses the Responses API (`/v1/responses`) with `background=true` for
  reliability.
- Submits, then polls until terminal status.
- Transport retries (3 attempts) for HTTP 429/502/503/504 and connection errors.
- Retries once with doubled token budget on `incomplete` status.
- Configurable: model, reasoning effort, max output tokens. Defaults:
  `gpt-5.2-codex`, `medium`, `64000`.

#### IO (`planner/io.py`)

- Atomic file writes via `tempfile.mkstemp` → `fsync` → `os.replace`.
- `write_work_orders()`: writes individual `WO-NN.json` files then
  `WORK_ORDERS_MANIFEST.json` (manifest last).
- `check_overwrite()`: refuses to write into a directory with existing WO
  files unless `--overwrite` is set.

### 2.2 Factory Subsystem

#### CLI / Run (`factory/run.py`, `factory/__main__.py`)

- Entry point: `python -m factory run`
- Required flags: `--repo`, `--work-order`, `--out`, `--llm-model`
- Optional flags: `--max-attempts` (default 2), `--llm-temperature` (default 0),
  `--timeout-seconds` (default 600)
- Preflight checks: repo is a git repo, working tree is clean, output
  directory is not inside the repo.
- Computes deterministic `run_id`: `SHA-256(canonical_json(work_order) + baseline_commit)[:16]`.
- On unhandled exception: best-effort rollback, emergency `run_summary.json`.

#### Graph (`factory/graph.py`)

- LangGraph `StateGraph` with four nodes: `se`, `tr`, `po`, `finalize`.
- Entry point: `se`.
- Routing:
  - After SE: proceed to TR if proposal produced, else finalize.
  - After TR: proceed to PO if writes applied, else finalize.
  - After PO: always finalize.
  - After finalize: END on PASS or exhausted attempts, else retry from SE.
- State is a `TypedDict` (`FactoryState`) with per-attempt fields reset by
  finalize between attempts.

#### SE Node (`factory/nodes_se.py`)

- **Precondition gate**: before any LLM call, checks every
  `work_order.preconditions` entry against the filesystem. On failure,
  returns `FailureBrief(stage="preflight")` with "PLANNER-CONTRACT BUG"
  prefix. The LLM is never called.
- Reads context files from the repo (bounded by 200 KB total).
- Builds a prompt containing: work order details, file contents with SHA-256
  hashes, previous failure brief (if retrying), output format instructions.
- Calls the LLM via `factory/llm.py`.
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
  (`compileall` + `pip --version` + `pytest -q`).
- **Postcondition gate**: after verify, before acceptance. Checks each
  `work_order.postconditions` entry. On failure, returns
  `FailureBrief(stage="acceptance_failed")` (retryable).
- **Acceptance commands**: runs each command from
  `work_order.acceptance_commands` via `subprocess.run(shell=False)`.
- Persists: `verify_result.json`, `acceptance_result.json`.

#### Finalize Node (`factory/graph.py` `_finalize_node`)

- Records the attempt (proposal path, touched files, verify/acceptance
  results, failure brief).
- On FAIL: rolls back via `git reset --hard` + `git clean -fdx`.
- On PASS: computes `repo_tree_hash_after` by staging touched files and
  running `git write-tree`.
- Increments attempt index and resets per-attempt state fields.

#### Schemas (`factory/schemas.py`)

Pydantic models defining the contract surface:

- `Condition`: `kind` (`file_exists` | `file_absent`) + `path`. All paths
  validated as safe relative paths.
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
- `run_command`: executes a command with `subprocess.run(shell=False)`,
  captures output to files, handles `TimeoutExpired` and `OSError`.
- `split_command`: `shlex.split` wrapper.
- `normalize_path`, `is_path_inside_repo`: path safety.
- Artifact filename constants (`ARTIFACT_SE_PROMPT`, etc.).

#### Workspace (`factory/workspace.py`)

- Git operations via `subprocess.run(["git", ...], shell=False)`.
- `is_git_repo`, `is_clean`: preflight queries.
- `get_baseline_commit`: `git rev-parse HEAD`.
- `rollback`: `git reset --hard` + `git clean -fdx`.
- `get_tree_hash`: stages touched files, runs `git write-tree`.

---

## 3. Data Flow

```
spec.txt ──► planner compile ──► WO-01.json ... WO-NN.json
                │                        │
                │ (LLM call              │
                │  + validation          │
                │  + retry loop)         │
                ▼                        ▼
         compile artifacts        factory run (per WO)
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
                                   (PASS → done,
                                    FAIL → rollback
                                          + retry)
```

**Validation points** (compile time, no execution):
- JSON parse of LLM output.
- `validate_plan()`: E001-E006 per-WO structural checks.
- `validate_plan_v2()`: E101-E106, W101 cross-WO chain checks.
- `compute_verify_exempt()`: per-WO verify exemption.

**Validation points** (factory runtime, deterministic):
- SE node: precondition gate (`file_exists` / `file_absent` checks).
- TR node: scope, path safety, base-hash batch check.
- PO node: global verify, postcondition gate, acceptance commands.

**Failure handling:**
- Compile: structured errors fed back to LLM via revision prompt (up to 3
  attempts). If all fail, validation errors written to disk.
- Factory SE: LLM exception → `FailureBrief(stage="exception")`. Parse
  failure → `FailureBrief(stage="llm_output_invalid")`. Precondition failure
  → `FailureBrief(stage="preflight")`.
- Factory TR: scope/hash/write failures → `FailureBrief` with appropriate stage.
- Factory PO: verify failure → `FailureBrief(stage="verify_failed")`.
  Postcondition failure → `FailureBrief(stage="acceptance_failed")`.
  Acceptance failure → `FailureBrief(stage="acceptance_failed")`.
- Factory finalize: on any FAIL → `git reset --hard` + `git clean -fdx` →
  retry from SE with the failure brief injected into the prompt.

**Rollback:**
- Performed by `workspace.rollback()` in the finalize node.
- Uses `git reset --hard <baseline>` + `git clean -fdx`.
- Safe even when no writes were applied (idempotent).
- Also performed as best-effort in the emergency exception handler in `run.py`.

---

## 4. Execution Model

**Planner compile loop:**
- Up to `MAX_COMPILE_ATTEMPTS` (3) iterations.
- Each iteration: LLM call → parse → structural validation → chain
  validation → if hard errors, build revision prompt and loop.
- Warnings (W-codes) are non-blocking.
- If first attempt passes, the loop exits immediately with identical
  behavior to a single-pass compile.

**Factory attempt loop:**
- Up to `max_attempts` (default 2) iterations per work order.
- Each iteration: SE → TR → PO → finalize.
- Routing is conditional: SE failure skips TR/PO; TR failure skips PO.
- On failure, finalize rolls back the repo and increments attempt_index.
  The failure brief is preserved in state so SE can include it in the next
  prompt.
- On success, finalize computes `repo_tree_hash_after` and sets verdict to
  PASS.

**Determinism enforcement:**
- `subprocess.run(shell=False)`: no shell interpretation in acceptance or
  verify commands.
- `base_sha256`: every file write must declare the expected current hash.
  All hashes are checked before any writes (batch invariant — no partial
  writes on stale context).
- `allowed_files`: the SE LLM may only propose writes to paths listed in
  the work order. TR enforces this.
- Path safety: all paths validated as relative, no `..`, no drive letters,
  must resolve inside repo root.
- Size limits: 200 KB per file, 500 KB total, 10 context files, 2000-char
  error excerpts.
- Atomic writes: temp file → fsync → os.replace (per file, not transactional
  across files).
- Deterministic run IDs and compile hashes from content hashing.

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
- Satisfy precondition chains across work orders (E101).
- Avoid contradictory preconditions (E102).
- Declare postconditions only for files it's allowed to write (E103).
- Declare postconditions for all files it's allowed to write (E104).
- Not include the verify command in acceptance (E105).
- Produce a plan where the verify contract is eventually satisfied (E106).

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
- Rollback to baseline on any failure.
- All commands run with `shell=False`.
- All artifacts are persisted for post-mortem inspection.

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

- **Target repo language.** The system is designed for Python projects
  (`pytest` as default verifier, `compileall` as fallback, `python -c` for
  acceptance commands) but the factory itself is language-agnostic at the
  execution level (`subprocess.run` with arbitrary commands).
