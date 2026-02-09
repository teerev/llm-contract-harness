# STATE.md — Current System Behavior

Describes the factual, mechanically-enforced behavior of the system as
implemented in `planner/` and `factory/` on 2026-02-09.

---

## 1. System Summary

The system takes a text file containing a product specification, uses an LLM
to generate a sequence of JSON work orders, validates them deterministically,
then executes each work order against a target git repository using a second
LLM to propose file writes, deterministic code to apply and verify those
writes, and git to roll back on failure. The planner retries up to 3 times
on validation failure. The factory retries up to N times (configurable,
default 2) per work order on execution failure. All non-LLM behavior is
deterministic.

---

## 2. Planner Behavior

**Inputs:**
- A product specification text file (`--spec`).
- A prompt template markdown file (default: `planner/PLANNER_PROMPT.md`).
- Optionally, a path to the target repository (`--repo`) for precondition
  validation against its actual file listing.

**Outputs (on success):**
- Individual `WO-NN.json` files in the output directory.
- `WORK_ORDERS_MANIFEST.json` containing the full manifest.
- Per-attempt artifacts in the compile artifacts directory:
  `llm_raw_response_attempt_{N}.txt`, `validation_errors_attempt_{N}.json`,
  `manifest_raw_attempt_{N}.json`.
- `compile_summary.json` with attempt count, errors, warnings, and timing.

**Outputs (on failure):**
- `validation_errors.json` (structured, with error codes) in both the
  artifacts directory and the output directory.
- `compile_summary.json` with `success: false` and the error list.

**Validation applied:**

Structural checks (per work order):
- JSON parsability of LLM output.
- Work order ID format (`WO-NN`) and contiguity from `WO-01`.
- Shell operator tokens in acceptance commands rejected.
- Glob characters in file paths rejected.
- Pydantic `WorkOrder` schema conformance (field types, required fields,
  non-empty acceptance commands, context files capped at 10, postconditions
  restricted to `file_exists`).
- Python syntax check via `ast.parse` on `python -c` acceptance commands.

Chain checks (across work orders):
- Every precondition is satisfied by the initial repo file listing plus the
  cumulative postconditions of prior work orders.
- No work order declares both `file_exists` and `file_absent` for the same
  path in preconditions.
- Every postcondition path is in `allowed_files`.
- Every `allowed_files` path has a corresponding postcondition (when the
  work order declares any postconditions).
- `bash scripts/verify.sh` does not appear in any `acceptance_commands`.
- The `verify_contract` (if present) is fully satisfied by the cumulative
  state after the last work order.
- Acceptance command imports reference files in the cumulative state (warning
  only, non-blocking).

**Failure modes:**
- LLM returns unparseable output (JSON parse error). Retried with the raw
  response and error in a revision prompt.
- LLM returns structurally invalid work orders. Retried with structured
  `[E0xx]`/`[E1xx]` error codes in a revision prompt.
- All retry attempts fail. `CompileResult` returned with `success=False`
  and the final error list.
- LLM API transport failure (HTTP 429/502/503/504, connection errors).
  The OpenAI client retries up to 3 times with exponential backoff. If all
  transport retries fail, `RuntimeError` is raised.
- LLM response incomplete (output truncated). Retried once with doubled
  token budget.

---

## 3. Factory Behavior

**How work orders are executed:**

The factory runs one work order at a time. It requires a clean git repo
(no staged, unstaged, or untracked changes) and an output directory outside
the repo. Execution proceeds through a LangGraph state machine:

1. **SE node**: checks preconditions against the filesystem, reads context
   files, builds a prompt, calls the LLM, parses the response as a
   `WriteProposal`.
2. **TR node**: validates the proposal (scope, path safety, base hashes),
   then applies writes atomically (per-file).
3. **PO node**: runs global verification (or lightweight fallback if
   verify-exempt), checks postconditions, runs acceptance commands.
4. **Finalize node**: on PASS, computes tree hash and terminates. On FAIL,
   rolls back the repo and retries from SE (if attempts remain).

If any node produces a `FailureBrief`, subsequent nodes are skipped and
control passes to finalize.

**What is deterministic vs LLM-driven:**

| Step | Deterministic | LLM-driven |
|------|:------------:|:----------:|
| Preflight (git checks) | Yes | |
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
| Rollback | Yes | |

**How rollback works:**

On any failure, `workspace.rollback()` executes:
1. `git reset --hard <baseline_commit>`
2. `git clean -fdx`

This restores the repo to its exact state at the start of the work order.
The `-fdx` flag removes untracked files including those matching
`.gitignore`. Rollback is idempotent and runs even when no writes were
applied. On unhandled exceptions, rollback is attempted on a best-effort
basis before writing an emergency `run_summary.json`.

**How failures are classified:**

| Stage | Meaning | Retryable |
|-------|---------|:---------:|
| `preflight` | Precondition check failed (plan-level error) | Yes (but futile — same precondition will fail again) |
| `exception` | LLM API call failed | Yes |
| `llm_output_invalid` | LLM response was not valid JSON or did not match `WriteProposal` | Yes |
| `write_scope_violation` | Proposed paths outside `allowed_files`, duplicates, or path escapes repo | Yes |
| `stale_context` | `base_sha256` mismatch (file changed since context was read) | Yes |
| `write_failed` | Atomic write to disk failed (e.g., disk full) | Yes |
| `verify_failed` | Global verification command exited non-zero | Yes |
| `acceptance_failed` | Postcondition check or acceptance command exited non-zero | Yes |

Every failure produces a `FailureBrief` with stage, command, exit code,
error excerpt (truncated to 2000 chars), and a constraints reminder. The
failure brief is persisted as `failure_brief.json` and fed back to the SE
prompt on retry.

---

## 4. Determinism Boundary

**Fully deterministic (same inputs produce same outputs):**
- Compile hash computation.
- Run ID computation.
- All validation checks (E001-E006, E101-E106, W101).
- `verify_exempt` computation.
- Prompt template rendering.
- WorkOrder schema parsing and validation.
- TR node: scope checks, path safety, base-hash verification, file writes.
- PO node: command execution via `subprocess.run(shell=False)`.
- Finalize node: rollback, tree hash computation.
- All artifact persistence.

**Dependent on LLM behavior (non-reproducible across runs):**
- Planner LLM: the content of the generated work order manifest.
- Planner LLM revision: whether and how the LLM fixes validation errors.
- Factory SE LLM: the content of the `WriteProposal` (which files, what
  code).

**Reproducibility guarantees that exist:**
- Given the same work order JSON and the same baseline commit, the factory
  run ID is deterministic.
- Given the same spec, template, model name, and reasoning effort, the
  compile hash is deterministic.
- Validation results are deterministic for a given work order sequence and
  repo file listing.
- The factory will always roll back to the exact baseline state on failure.

**Reproducibility guarantees that do NOT exist:**
- Two planner runs with the same spec may produce different work orders
  (LLM non-determinism, even at temperature 0 — API-level non-determinism).
- Two factory runs of the same work order may produce different code (SE
  LLM non-determinism).
- The number of compile attempts is not deterministic (depends on whether
  the LLM produces valid output on the first try).

---

## 5. Current Guarantees

Each item corresponds to a specific mechanism in code.

- **No work order can be written to disk with invalid schema.** The pydantic
  `WorkOrder` model enforces field types, path safety, non-empty acceptance
  commands, context file limit of 10, and postconditions restricted to
  `file_exists`. (`factory/schemas.py` model validators.)

- **No work order can be written to disk with a shell operator in an
  acceptance command.** `shlex.split` tokenization + `SHELL_OPERATOR_TOKENS`
  check. (`planner/validation.py` E003.)

- **No work order can be written to disk with a Python syntax error in a
  `python -c` acceptance command.** `ast.parse` check.
  (`planner/validation.py` E006.)

- **No work order can be written to disk with unsatisfied preconditions**
  (when preconditions are declared and `--repo` is provided). Cumulative
  state tracking in `validate_plan_v2`. (`planner/validation.py` E101.)

- **No work order can be written to disk with postconditions referencing
  files outside `allowed_files`** (when postconditions are declared).
  (`planner/validation.py` E103.)

- **No work order can be written to disk with `bash scripts/verify.sh` in
  acceptance commands.** (`planner/validation.py` E105.)

- **The factory will not write files outside `allowed_files`.** TR node
  scope check. (`factory/nodes_tr.py`.)

- **The factory will not write files whose content has changed since
  context was read.** TR node base-hash batch check — all hashes verified
  before any write. (`factory/nodes_tr.py`.)

- **The factory will not write paths that escape the repository root.**
  `is_path_inside_repo` check in TR node. (`factory/nodes_tr.py`.)

- **The factory rolls back to the baseline commit on any failure.**
  `git reset --hard` + `git clean -fdx`. (`factory/workspace.py`
  `rollback`, called by `_finalize_node` in `factory/graph.py`.)

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

- **All commands are executed with `shell=False`.** No shell interpretation
  in acceptance commands, verify commands, or any other subprocess call.
  (`factory/util.py` `run_command`.)

- **All artifacts are persisted for post-mortem inspection.** Every LLM
  prompt, raw response, proposal, write result, verify result, acceptance
  result, failure brief, and run summary is written to the artifacts
  directory. (`factory/util.py` artifact constants, used throughout nodes
  and `factory/run.py`.)

- **`OSError` during command execution does not crash the factory.** Caught
  in `run_command`, returned as `CmdResult` with `exit_code=-1`.
  (`factory/util.py` `run_command` except-OSError handler.)

---

## 6. Explicit Non-Guarantees

- **The planner does not guarantee semantically correct acceptance
  commands.** Acceptance commands may assert wrong values, test the wrong
  properties, or fail to test the stated intent. The validator checks
  syntax and import dependencies, not semantic correctness.

- **The planner does not guarantee convergence.** The compile retry loop
  may exhaust all 3 attempts without producing a valid plan. LLM output
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
  has empty `preconditions` and `postconditions` (including all
  old-format work orders), chain rules R1-R4 are not applied for that
  work order. R5 (import dependencies) and R7 (verify ban) still apply.

- **The system does not guarantee that acceptance command dependency
  warnings (W101) are complete.** The import extraction covers `python -c`
  imports, `bash` script paths, and `python` script paths. It does not
  handle `python -m`, dynamic imports, or non-Python commands.

- **The system does not guarantee that `verify_exempt` is correct when
  `verify_contract` is absent.** If the planner does not emit a
  `verify_contract`, all work orders default to `verify_exempt=False`
  and the factory runs global verify unconditionally.

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
