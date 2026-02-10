# INVARIANTS.md — Non-Negotiable System Constraints

**Scope:** These invariants describe the *structural enforcement layer* —
the deterministic checks applied to LLM output before any side effects.
They do not guarantee end-to-end outcomes. The LLM may produce
nondeterministic output; the enforcement layer's verdicts (accept/reject)
are deterministic given the same LLM output and the same repo state.
Semantic correctness of generated code, host isolation, crash recovery,
and artifact byte-reproducibility are outside this scope — see the
Limitations section in README.md.

---

## 1. Definition of an Invariant

An invariant is a condition that must hold at all times for the system to be
considered correct. If an invariant is violated, the system has a bug — not a
degradation, not an edge case, a bug. Every invariant listed here is either
enforced in code today or is logically required to preserve the correctness
of the existing enforcement mechanisms.

---

## 2. Planner Invariants

### P1. Work order IDs are contiguous from WO-01.

Every emitted plan has IDs `WO-01`, `WO-02`, ..., `WO-NN` with no gaps and
no duplicates.

### P2. Work order JSON conforms to the WorkOrder pydantic schema.

Every field has the correct type. `acceptance_commands` is non-empty.
`context_files` has at most 10 entries. Postconditions use only `file_exists`.
All paths in `allowed_files`, `context_files`, preconditions, and
postconditions are relative, normalized, contain no `..` prefix, no drive
letters, no glob characters.

### P3. Acceptance commands contain no bare shell operators.

No acceptance command, after `shlex.split`, contains a bare token in
`{|, ||, &&, ;, >, >>, <, <<}`.

### P4. `python -c` acceptance commands are syntactically valid Python.

Every acceptance command matching `python -c "..."` passes `ast.parse`
without `SyntaxError`.

### P5. `bash scripts/verify.sh` does not appear in any acceptance command.

Global verification is the factory's responsibility. Including it in
acceptance creates redundancy and bootstrap circularity.

### P6. Every precondition is satisfiable by the cumulative state.

For each WO-N, every `file_exists` precondition path is either in the
initial repo file listing or declared as a `file_exists` postcondition by
some WO-K where K < N. Every `file_absent` precondition path is not in that
cumulative state.

### P7. No work order has contradictory preconditions.

No single work order declares both `file_exists(P)` and `file_absent(P)`
for the same path.

### P8. Every postcondition path is in `allowed_files`.

A work order cannot claim it will create a file it is not permitted to write.

### P9. Every `allowed_files` path has a postcondition.

When a work order declares any postconditions, every path in `allowed_files`
must appear as a `file_exists` postcondition. This ensures downstream
dependency resolution is complete.

### P10. The verify contract is eventually satisfied.

When a `verify_contract` is present, the cumulative state after the last
work order satisfies every condition in `verify_contract.requires`.

### P11. Validation errors use structured, machine-readable codes.

Every error is a `ValidationError` with a `code` field from the defined set
(`E000`–`E006`, `E101`–`E106`, `W101`). Free-form error strings are not
emitted by the validator.

---

## 3. Factory Invariants

### F1. The factory never writes to files outside `allowed_files`.

The TR node rejects any proposed write whose normalized path is not in
the work order's `allowed_files` set.

### F2. The factory never writes to paths that escape the repository root.

Every proposed write path is checked with `is_path_inside_repo` after
`os.path.realpath` resolution.

### F3. All base hashes are checked before any file is written.

The TR node iterates all proposed writes to verify `base_sha256` against the
current file content. If any hash mismatches, no writes are applied. There
are no partial writes on stale context.

### F4. The factory rolls back to the baseline commit on any failure.

On any non-PASS verdict, `git reset --hard <baseline>` and `git clean -fdx`
are executed. This is performed by the finalize node and, on unhandled
exceptions, by the emergency handler in `run.py`.

### F5. All commands are executed with `shell=False`.

`subprocess.run` is called with `shell=False` for every verification command,
acceptance command, and git operation. No shell interpretation occurs.

### F6. Preconditions are checked before the SE LLM is called.

The precondition gate in `se_node` runs before `_read_context_files` and
before the LLM call. If a precondition fails, the LLM is never invoked.

### F7. Postconditions are checked before acceptance commands run.

The postcondition gate in `po_node` runs after global verify and before
the acceptance command loop.

### F8. `verify_exempt` work orders skip `scripts/verify.sh`.

When `verify_exempt` is `True`, the PO node runs only
`python -m compileall -q .`, never `bash scripts/verify.sh` or the fallback
command set.

### F9. `OSError` during command execution does not crash the factory.

`run_command` catches `OSError` (including `PermissionError` and
`FileNotFoundError`) and returns a `CmdResult` with `exit_code=-1`.

### F10. The output directory is not inside the repository.

`run.py` preflight rejects runs where the output directory path is equal to
or inside the repo root. This prevents artifacts from polluting the git
working tree and being affected by rollback.

### F11. The repository must be a clean git working tree at run start.

`run.py` preflight checks `git status --porcelain` returns empty output.
The run is rejected if any staged, unstaged, or untracked changes exist.

### F12. Every factory run produces a `run_summary.json`.

On normal completion (PASS or FAIL), `run.py` writes `run_summary.json`.
On unhandled exception, the emergency handler writes an emergency summary
with `verdict: "ERROR"` and the traceback.

---

## 4. Planner–Factory Contract Invariants

### C1. The WorkOrder pydantic model is the contract surface.

Both planner and factory parse work orders through the same
`WorkOrder(**data)` model in `factory/schemas.py`. Any JSON that does not
parse as a `WorkOrder` is rejected by both sides.

### C2. `verify_exempt` is computed by the planner, consumed by the factory.

The planner's `compute_verify_exempt` injects `verify_exempt` into each
work order dict based on the `verify_contract` and cumulative postconditions.
The factory's PO node reads it. The factory does not compute it.

### C3. Preconditions declared by the planner are enforced by the factory.

The planner validates precondition chains at compile time (P6). The factory
re-checks each precondition against the actual filesystem at runtime (F6).
If a precondition fails at runtime, the error message identifies it as a
`PLANNER-CONTRACT BUG`.

### C4. Postconditions declared by the planner are enforced by the factory.

The planner validates postcondition achievability at compile time (P8, P9).
The factory checks postcondition satisfaction at runtime after writes (F7).
Postcondition failures at runtime are classified as executor errors
(`stage="acceptance_failed"`), not plan-level errors.

### C5. Violation of a compile-time invariant prevents work order output.

If any hard-error validation check (E-code) fails after all compile
attempts, no `WO-NN.json` files are written. The compile returns
`success=False` with the error list.

### C6. Violation of a factory-time invariant produces a `FailureBrief`.

Every runtime check failure (scope, hash, precondition, postcondition,
verify, acceptance) produces a `FailureBrief` with a `stage` field from
the fixed set `{preflight, llm_output_invalid, write_scope_violation,
stale_context, write_failed, verify_failed, acceptance_failed, exception}`.

---

## 5. LLM Interaction Invariants

### L1. LLM output is never executed as code by the system.

The planner LLM output is parsed as JSON. The factory SE LLM output is
parsed as JSON. Neither output is passed to `eval`, `exec`, or a shell.
Commands in `acceptance_commands` are defined by the planner (a prior,
separate LLM call), not by the SE LLM being evaluated.

### L2. LLM output is always validated before use.

Planner output passes through `parse_and_validate` and `validate_plan_v2`
before any work order is written to disk. Factory SE output passes through
`WriteProposal` pydantic parsing and TR node checks before any file is
written to the repo.

### L3. LLM failure does not leave the repository in a dirty state.

If the SE LLM call fails (exception, timeout, invalid output), no writes
have been applied. If writes were applied and a later stage fails, the
finalize node rolls back.

### L4. LLM non-determinism does not violate deterministic invariants.

The LLM may produce different output on each call. All deterministic
invariants (scope, hashes, paths, schema) are checked after each LLM call.
Non-deterministic LLM output cannot bypass deterministic enforcement.

### L5. The system defends against LLM path traversal.

Proposed write paths are validated as relative, normalized, free of `..`
prefixes, and resolving inside the repo root. An LLM that proposes
`../../etc/passwd` is rejected by both schema validation and TR node checks.

### L6. The system defends against LLM scope violation.

An LLM that proposes writes to files not in `allowed_files` is rejected by
the TR node. The rejection occurs before any writes are applied.

### L7. The system defends against LLM stale-context exploitation.

The base-hash batch check prevents an LLM from writing to a file whose
content has changed since the context was read. All hashes are verified
before any writes, preventing partial-write attacks where a correct hash
on file A enables a stale write on file B.

---

## 6. Invariant Enforcement Mechanisms

| Invariant | Enforced in | Violation surfaces as |
|-----------|-------------|----------------------|
| P1 (ID contiguity) | `planner/validation.py` `validate_plan` E001 | Compile error; no WO files written |
| P2 (schema) | `factory/schemas.py` `WorkOrder` validators; `planner/validation.py` E005 | Compile error (planner) or load failure (factory) |
| P3 (shell operators) | `planner/validation.py` `validate_plan` E003 | Compile error |
| P4 (python -c syntax) | `planner/validation.py` `_check_python_c_syntax` E006 | Compile error |
| P5 (no verify in acceptance) | `planner/validation.py` `validate_plan_v2` E105 | Compile error |
| P6 (precondition chain) | `planner/validation.py` `validate_plan_v2` E101 | Compile error |
| P7 (no contradictions) | `planner/validation.py` `validate_plan_v2` E102 | Compile error |
| P8 (postcond in allowed) | `planner/validation.py` `validate_plan_v2` E103 | Compile error |
| P9 (allowed has postcond) | `planner/validation.py` `validate_plan_v2` E104 | Compile error |
| P10 (verify contract) | `planner/validation.py` `validate_plan_v2` E106 | Compile error |
| P11 (structured errors) | `planner/validation.py` `ValidationError` dataclass | N/A (structural; no runtime violation) |
| F1 (scope) | `factory/nodes_tr.py` `tr_node` scope check | `FailureBrief(stage="write_scope_violation")` |
| F2 (path safety) | `factory/nodes_tr.py` `tr_node` + `factory/util.py` `is_path_inside_repo` | `FailureBrief(stage="write_scope_violation")` |
| F3 (hash batch check) | `factory/nodes_tr.py` `tr_node` hash loop | `FailureBrief(stage="stale_context")` |
| F4 (rollback) | `factory/workspace.py` `rollback`; `factory/graph.py` `_finalize_node`; `factory/run.py` emergency handler | Git repo restored to baseline |
| F5 (shell=False) | `factory/util.py` `run_command` `subprocess.run(shell=False)` | N/A (structural; shell never invoked) |
| F6 (precond before LLM) | `factory/nodes_se.py` `se_node` precondition gate | `FailureBrief(stage="preflight")` |
| F7 (postcond before acceptance) | `factory/nodes_po.py` `po_node` postcondition gate | `FailureBrief(stage="acceptance_failed")` |
| F8 (verify_exempt) | `factory/nodes_po.py` `po_node` verify_exempt branch | Compileall runs instead of verify.sh |
| F9 (OSError caught) | `factory/util.py` `run_command` except-OSError | `CmdResult(exit_code=-1)` |
| F10 (out != repo) | `factory/run.py` preflight check | `sys.exit(1)` before graph invocation |
| F11 (clean tree) | `factory/run.py` preflight via `workspace.is_clean` | `sys.exit(1)` before graph invocation |
| F12 (run summary) | `factory/run.py` normal path + emergency handler | `run_summary.json` always written |
| C1 (shared schema) | `factory/schemas.py` imported by both `planner/validation.py` and factory nodes | Parse error on either side |
| C2 (verify_exempt flow) | `planner/validation.py` `compute_verify_exempt`; `factory/nodes_po.py` reads field | Incorrect verify behavior if violated |
| C3 (precond enforced) | `planner/validation.py` E101; `factory/nodes_se.py` precondition gate | Compile error or `FailureBrief(stage="preflight")` |
| C4 (postcond enforced) | `planner/validation.py` E103/E104; `factory/nodes_po.py` postcondition gate | Compile error or `FailureBrief(stage="acceptance_failed")` |
| C5 (no output on error) | `planner/compiler.py` `compile_plan` early return on errors | No WO files; `success=False` |
| C6 (FailureBrief on violation) | All factory nodes return `FailureBrief` on failure | Structured failure with `stage` from fixed set |
| L1 (no eval) | System-wide: JSON parsing only, no `eval`/`exec` | N/A (structural) |
| L2 (validated before use) | `planner/validation.py`; `factory/schemas.py`; `factory/nodes_tr.py` | Rejection before side effects |
| L3 (no dirty state) | `factory/graph.py` `_finalize_node` rollback; SE failure returns before writes | Git repo clean after failure |
| L4 (determinism unbreakable) | All checks run after LLM output, before side effects | Deterministic rejection regardless of LLM output |
| L5 (path traversal) | `factory/schemas.py` `_validate_relative_path`; `factory/nodes_tr.py` `is_path_inside_repo` | Schema error or `FailureBrief(stage="write_scope_violation")` |
| L6 (scope violation) | `factory/nodes_tr.py` scope check | `FailureBrief(stage="write_scope_violation")` |
| L7 (stale context) | `factory/nodes_tr.py` batch hash check | `FailureBrief(stage="stale_context")` |
