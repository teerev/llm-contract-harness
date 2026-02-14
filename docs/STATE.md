# STATE.md — Current System Behavior

Describes the factual, mechanically-enforced behavior of the system as
implemented in `planner/`, `factory/`, `shared/`, and `llmch/` on
2026-02-14.

---

## 1. System Summary

The system takes a text file containing a product specification, uses an LLM
to generate a sequence of JSON work orders, validates them deterministically,
then executes each work order against a target git repository using a second
LLM to propose file writes, deterministic code to apply and verify those
writes, and git to commit on success or roll back on failure. The planner
retries up to 5 times on validation failure. The factory retries up to N
times (configurable, default 5) per work order on execution failure. All
non-LLM behavior is deterministic.

The primary user interface is the `llmch` CLI (`llmch plan`, `llmch run`,
`llmch run-all`), installable via `pip install -e .`.

---

## 2. Planner Behavior

**Inputs:**
- A product specification text file (`--spec`).
- A prompt template markdown file (default: `planner/PLANNER_PROMPT.md`).
- Optionally, a path to the target repository (`--repo`) for precondition
  validation against its actual file listing.

**Outputs (on success):**
- Canonical output: `artifacts/planner/{run_id}/output/WO-NN.json` files
  and `WORK_ORDERS_MANIFEST.json`.
- Optionally exported to `--outdir` for convenience.
- Per-attempt artifacts in `artifacts/planner/{run_id}/compile/`:
  `prompt_attempt_{N}.txt`, `llm_raw_response_attempt_{N}.txt`,
  `llm_reasoning_attempt_{N}.txt`, `manifest_raw_attempt_{N}.json`,
  `validation_errors_attempt_{N}.json`.
- `compile_summary.json` with attempt count, errors, warnings, timing,
  and defaults snapshot.
- `run.json` manifest with run ID, timestamps, config, input hashes,
  tool version, and provenance.

**Outputs (on failure):**
- `validation_errors.json` (structured, with error codes) in the compile
  artifacts directory.
- `compile_summary.json` with `success: false` and the error list.
- `run.json` updated with failure status.

**Provenance injected into work orders:**
- `planner_run_id`: ULID of the planner run.
- `compile_hash`: deterministic SHA-256 of spec + template + model + effort.
- `manifest_sha256`: SHA-256 of the normalized manifest.
- `bootstrap`: `true` for verify-exempt WOs, `false` for others.

**Validation applied:**

Structural checks (per work order):
- JSON parsability of LLM output (with 10 MB payload size guard).
- Work order ID format (`WO-NN`) and contiguity from `WO-01`.
- Non-dict elements in `work_orders` array produce `E000` (not crash).
- Shell operator tokens in acceptance commands rejected (E003).
- Glob characters in file paths rejected (E004).
- Pydantic `WorkOrder` schema conformance (field types, required fields,
  non-empty acceptance commands, context files capped at 10, postconditions
  restricted to `file_exists`, paths validated against `..`, `\`, NUL,
  control chars, `.`) (E005).
- Python syntax check via `ast.parse` on `python -c` acceptance commands (E006).
- Unparseable acceptance commands (shlex.split failure) rejected (E007).

Chain checks (across work orders):
- Every precondition is satisfied by the initial repo file listing plus the
  cumulative postconditions of prior work orders (E101). Path fields are
  normalized via `posixpath.normpath` before comparison.
- No work order declares both `file_exists` and `file_absent` for the same
  path in preconditions (E102).
- Every postcondition path is in `allowed_files` (E103).
- Every `allowed_files` path has a corresponding postcondition (when the
  work order declares any postconditions) (E104).
- `bash scripts/verify.sh` does not appear in any `acceptance_commands`,
  using normalized shlex.split + normpath comparison (E105).
- The `verify_contract` (if present and a valid dict) is fully satisfied by
  the cumulative state after the last work order. Non-dict `verify_contract`
  produces E000 (E106).
- Acceptance command imports reference files in the cumulative state (W101,
  warning only, non-blocking).

**verify_exempt computation:**
- The planner always overwrites `verify_exempt` — never trusts LLM-provided
  values.
- If `verify_contract` is a valid dict with `requires`, each WO is marked
  exempt if the cumulative state after it does not satisfy all requirements.
- If `verify_contract` is absent, `None`, or non-dict, all WOs get
  `verify_exempt=False`.

**Bootstrap skip:**
- If `--repo` is provided and `scripts/verify.sh` already exists in the
  repo, the planner filters out any WO whose postconditions create
  `scripts/verify.sh` and renumbers the remaining WOs.

**Failure modes:**
- LLM returns unparseable output (JSON parse error). Retried with the raw
  response and error in a revision prompt.
- LLM returns structurally invalid work orders. Retried with structured
  `[E0xx]`/`[E1xx]` error codes in a revision prompt.
- All retry attempts fail (up to 5). `CompileResult` returned with
  `success=False` and the final error list.
- LLM API transport failure (HTTP 429/502/503/504, connection errors).
  The OpenAI client retries up to 3 times with exponential backoff. If all
  transport retries fail, `RuntimeError` is raised.
- LLM response incomplete (output truncated). Retried once with doubled
  token budget.
- SSE streaming failure. Falls back to background polling automatically.

---

## 3. Factory Behavior

**How work orders are executed:**

The factory runs one work order at a time. It requires a clean git repo
(no staged, unstaged, or untracked changes; at least one commit; not in
detached HEAD; not on a protected branch). Execution proceeds through a
LangGraph state machine on a working branch:

1. **SE node**: checks preconditions against the filesystem, reads context
   files, builds a prompt from `FACTORY_PROMPT.md`, calls the LLM, parses
   the response as a `WriteProposal`.
2. **TR node**: validates the proposal (scope, path safety, base hashes),
   then applies writes atomically (per-file).
3. **PO node**: runs global verification (or lightweight fallback if
   verify-exempt), checks postconditions, runs acceptance commands. All
   subprocesses use a sandboxed environment.
4. **Finalize node**: on PASS, detects repo drift, computes tree hash, and
   terminates. On FAIL, rolls back the repo and retries from SE (if attempts
   remain). Non-retryable stages (`preflight`, `write_failed`) abort
   immediately without retry.

If any node produces a `FailureBrief`, subsequent nodes are skipped and
control passes to finalize.

**Post-execution (PASS path):**
1. Scoped commit: `git add -- <touched_files>` + `git commit --no-verify`.
   Only proposal-intended files are committed.
2. Clean: `git clean -fdx` removes verification artifacts (`__pycache__/`,
   `.pytest_cache/`, etc.) so the repo is clean for the next WO.
3. Push: `git push -u origin <branch>` (if enabled and commit succeeded).
   Push failures do not change the verdict.

**What is deterministic vs LLM-driven:**

| Step | Deterministic | LLM-driven |
|------|:------------:|:----------:|
| Preflight (git checks) | Yes | |
| Verify-exempt policy | Yes | |
| Precondition gate | Yes | |
| Context file reading | Yes | |
| Prompt construction | Yes | |
| Write proposal generation | | Yes |
| Proposal parsing (JSON) | Yes | |
| Scope check | Yes | |
| Path safety check | Yes | |
| Base-hash check | Yes | |
| Atomic file write | Yes | |
| Global verification | Yes | |
| Postcondition gate | Yes | |
| Acceptance commands | Yes | |
| Repo drift detection | Yes | |
| Scoped commit | Yes | |
| Post-commit clean | Yes | |
| Rollback | Yes | |

**How rollback works:**

On any failure, `workspace.rollback()` executes:
1. `git reset --hard <baseline_commit>`
2. `git clean -fdx`

This restores the repo to its exact state at the start of the work order.
The `-fdx` flag removes untracked files including those matching
`.gitignore`. Rollback is idempotent and runs even when no writes were
applied. On unhandled exceptions (including `KeyboardInterrupt`), rollback
is attempted on a best-effort basis by the `BaseException` emergency handler,
which also writes an emergency `run_summary.json` with `rollback_failed`
status if cleanup fails.

**How failures are classified:**

| Stage | Meaning | Retryable |
|-------|---------|:---------:|
| `preflight` | Precondition check failed (plan-level error) | No (non-retryable) |
| `exception` | LLM API call failed | Yes |
| `llm_output_invalid` | LLM response was not valid JSON or did not match `WriteProposal` | Yes |
| `write_scope_violation` | Proposed paths outside `allowed_files`, duplicates, or path escapes repo | Yes |
| `stale_context` | `base_sha256` mismatch (file changed since context was read) | Yes |
| `write_failed` | Atomic write to disk failed (e.g., disk full) | No (non-retryable) |
| `verify_failed` | Global verification command exited non-zero | Yes |
| `acceptance_failed` | Postcondition check or acceptance command exited non-zero | Yes |

Every failure produces a `FailureBrief` with stage, command, exit code,
error excerpt (truncated to 2000 chars), and a constraints reminder. The
failure brief is persisted as `failure_brief.json` and fed back to the SE
prompt on retry.

**Verify-exempt policy:**

When a work order has `verify_exempt=true`:
1. If `--allow-verify-exempt` CLI flag is passed → honored.
2. Else if trusted planner bootstrap provenance (`provenance.bootstrap=true`
   + `provenance.planner_run_id` present) → auto-honored with warning.
3. Else → fail fast with actionable error mentioning `--allow-verify-exempt`.

When `verify_exempt=false` → never blocked.

---

## 4. Determinism Boundary

**Fully deterministic (same inputs produce same outputs):**
- Compile hash computation (content-addressable).
- All validation checks (E000-E007, E101-E106, W101).
- `verify_exempt` computation.
- Prompt template rendering.
- WorkOrder schema parsing and validation (including path normalization).
- TR node: scope checks, path safety, base-hash verification, file writes.
- PO node: command execution via `subprocess.run(shell=False, env=sandboxed)`.
- Finalize node: rollback, tree hash computation, drift detection.
- All artifact persistence (atomic writes).

**NOT deterministic:**
- Run IDs (ULID-based: timestamp + random component).
- Planner LLM: the content of the generated work order manifest.
- Planner LLM revision: whether and how the LLM fixes validation errors.
- Factory SE LLM: the content of the `WriteProposal` (which files, what
  code).

**Reproducibility guarantees that exist:**
- Given the same spec, template, model name, and reasoning effort, the
  compile hash is deterministic.
- Validation results are deterministic for a given work order sequence and
  repo file listing.
- The factory will always roll back to the exact baseline state on failure.
- Artifact directories are immutable (ULID-based, never overwritten).

**Reproducibility guarantees that do NOT exist:**
- Two planner runs with the same spec may produce different work orders
  (LLM non-determinism, even at temperature 0 — API-level non-determinism).
- Two factory runs of the same work order may produce different code (SE
  LLM non-determinism).
- The number of compile attempts is not deterministic (depends on whether
  the LLM produces valid output on the first try).
- Run IDs are not deterministic (ULID timestamp + random).

---

## 5. Current Guarantees

Each item corresponds to a specific mechanism in code.

- **No work order can be written to disk with invalid schema.** The pydantic
  `WorkOrder` model enforces field types, path safety (no `..`, `\`, NUL,
  control chars, `.`), non-empty acceptance commands, context file limit
  of 10, and postconditions restricted to `file_exists`.
  (`factory/schemas.py` model validators.)

- **No work order can be written to disk with a shell operator in an
  acceptance command.** `shlex.split` tokenization + `SHELL_OPERATOR_TOKENS`
  check. (`planner/validation.py` E003.)

- **No work order can be written to disk with an unparseable acceptance
  command.** `shlex.split` failure produces E007 error.
  (`planner/validation.py` E007.)

- **No work order can be written to disk with a Python syntax error in a
  `python -c` acceptance command.** `ast.parse` check.
  (`planner/validation.py` E006.)

- **No work order can be written to disk with unsatisfied preconditions**
  (when preconditions are declared and `--repo` is provided). Cumulative
  state tracking with normalized paths in `validate_plan_v2`.
  (`planner/validation.py` E101.)

- **No work order can be written to disk with postconditions referencing
  files outside `allowed_files`** (when postconditions are declared).
  (`planner/validation.py` E103.)

- **No work order can be written to disk with `bash scripts/verify.sh` in
  acceptance commands** (including whitespace/path variants).
  (`planner/validation.py` E105, normalized match.)

- **Non-dict work order elements and non-dict verify_contract values
  produce structured E000 errors, not crashes.**
  (`planner/validation.py` type guards in `parse_and_validate`,
  `validate_plan`, `validate_plan_v2`.)

- **`verify_exempt` is always overwritten by the planner.** LLM-provided
  values are never preserved. (`planner/compiler.py` M-01 block.)

- **The factory will not write files outside `allowed_files`.** TR node
  scope check. (`factory/nodes_tr.py`.)

- **The factory will not write files whose content has changed since
  context was read.** TR node base-hash batch check — all hashes verified
  before any write. (`factory/nodes_tr.py`.)

- **The factory will not write paths that escape the repository root.**
  `is_path_inside_repo` check in TR node. (`factory/nodes_tr.py`.)

- **The factory rolls back to the baseline commit on any failure, including
  KeyboardInterrupt.** `git reset --hard` + `git clean -fdx`. Finalize
  node + `BaseException` emergency handler. (`factory/workspace.py`,
  `factory/graph.py`, `factory/run.py`.)

- **Factory commits contain only proposal-touched files.** Scoped
  `git add -- <files>` on the PASS path. (`factory/workspace.py`
  `git_commit` with `touched_files`.)

- **The working tree is clean after a PASS commit.** `clean_untracked()`
  removes verification artifacts. (`factory/run.py` + `factory/workspace.py`.)

- **Factory preconditions are checked before the SE LLM is called.**
  If a `file_exists` precondition is false or a `file_absent` precondition
  is true, the LLM is never invoked. (`factory/nodes_se.py` precondition
  gate.)

- **Factory postconditions are checked before acceptance commands run.**
  If a declared `file_exists` postcondition is not satisfied after writes,
  acceptance commands are skipped. (`factory/nodes_po.py` postcondition
  gate.)

- **Work orders marked `verify_exempt` skip the full global verify and
  run only `python -m compileall -q .`.** (`factory/nodes_po.py`
  verify_exempt branch.)

- **All commands are executed with `shell=False` in a sandboxed environment.**
  No shell interpretation. `PYTHONDONTWRITEBYTECODE=1` and pytest cache
  suppression. (`factory/util.py` `run_command` + `_sandboxed_env`.)

- **All artifacts are persisted atomically for post-mortem inspection.**
  `save_json` uses tempfile + fsync + os.replace. Every LLM prompt, raw
  response, proposal, write result, verify result, acceptance result,
  failure brief, and run summary is written to immutable artifact
  directories. (`factory/util.py`, used throughout nodes and `factory/run.py`.)

- **`OSError` during command execution does not crash the factory.** Caught
  in `run_command`, returned as `CmdResult` with `exit_code=-1`.
  (`factory/util.py` `run_command` except-OSError handler.)

- **Repo drift is detected and recorded.** Files modified/created outside
  `touched_files` are recorded in the attempt record on PASS.
  (`factory/graph.py` `_finalize_node` + `factory/workspace.py`
  `detect_repo_drift`.)

- **The factory never commits to main or master.** Protected branch check
  in preflight. (`factory/run.py`.)

---

## 6. Explicit Non-Guarantees

- **The planner does not guarantee semantically correct acceptance
  commands.** Acceptance commands may assert wrong values, test the wrong
  properties, or fail to test the stated intent. The validator checks
  syntax and import dependencies, not semantic correctness.

- **The planner does not guarantee convergence.** The compile retry loop
  may exhaust all 5 attempts without producing a valid plan. LLM output
  is non-deterministic; the revision prompt does not guarantee the LLM
  will fix the cited errors.

- **The factory does not guarantee that the SE LLM will produce correct
  code.** The code may compile, pass verification, and pass acceptance
  commands while being logically wrong.

- **The factory does not guarantee that retrying a failed work order will
  succeed.** The failure brief is fed back to the SE prompt, but the LLM
  may repeat the same mistake or introduce new ones.

- **The system does not guarantee that the `notes` field is consistent
  with other work order fields.** Notes are free text with no parsing or
  validation.

- **The system does not guarantee that precondition/postcondition chains
  are validated when work orders lack condition fields.** If a work order
  has empty `preconditions` and `postconditions`, chain rules R1-R4 are
  not applied for that work order.

- **The system does not guarantee that acceptance command dependency
  warnings (W101) are complete.** The import extraction covers `python -c`
  imports, `bash` script paths, and `python` script paths. It does not
  handle `python -m`, dynamic imports, or non-Python commands.

- **File writes are not transactional across files.** If a multi-file
  write proposal fails partway through, some files may have been written
  before rollback restores the repo. The batch base-hash check prevents
  *stale* partial writes but not *interrupted* partial writes.

- **The system does not manage Python dependencies.** If the target repo
  requires packages not already installed, verification and acceptance
  commands may fail for reasons unrelated to code correctness.

- **The system does not guarantee identical LLM output across runs.**
  Even with temperature 0, the OpenAI API does not guarantee deterministic
  responses. Two compiles of the same spec may produce different work
  orders. Two factory runs of the same work order may produce different
  code.

- **Run IDs are not deterministic.** ULID-based run IDs contain a
  timestamp and random component. The `compile_hash` is deterministic
  (content-addressable), but `run_id` varies across runs.

- **Cross-WO API coherence is not enforced.** The planner may define
  different function names across work orders (e.g., `initial_state` in
  one WO's tests and `init_game` in another WO's notes). The deterministic
  validator does not check semantic consistency across WOs.
