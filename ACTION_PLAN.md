# Planner-Factory Contract Hardening: Action Plan

**Date:** 2026-02-08
**Branch:** `wo_compile`

---

## 1. Current State Snapshot

### 1A. Structural Validation (what exists today)

| Mechanism | Where | Status |
|-----------|-------|--------|
| ID format (`WO-NN`) + contiguity | `planner/validation.py` `validate_plan()` lines 67-79 | Implemented |
| `bash scripts/verify.sh` **required** in every WO's `acceptance_commands` | `validation.py` lines 85-95 | Implemented, with WO-01 bootstrap exemption (line 91) |
| Shell-operator rejection (`\|`, `&&`, `;`, `>`, etc.) | `validation.py` lines 97-112, `SHELL_OPERATOR_TOKENS` line 18 | Implemented |
| Glob char rejection in paths | `validation.py` lines 114-120 | Implemented |
| Pydantic schema conformance (`WorkOrder(**wo)`) | `validation.py` lines 122-126, model in `factory/schemas.py` lines 38-64 | Implemented |
| Path safety (relative, no `..`, no drive letters) | `factory/schemas.py` `_validate_relative_path()` lines 18-31 | Implemented |
| `acceptance_commands` non-empty | `factory/schemas.py` line 56 | Implemented |
| `context_files` max 10 | `factory/schemas.py` `_check_context_constraints()` line 62 | Implemented |
| `context_files` **not** restricted to `allowed_files` (S1 fix) | `factory/schemas.py` line 60-63 — subset check removed | Implemented |

### 1B. What does NOT exist today

- No `preconditions` or `postconditions` fields on `WorkOrder`.
- No cross-work-order dependency tracking (file existence chain).
- No `ast.parse` check for `python -c` acceptance commands.
- No compile retry loop. `compile_plan()` is single-pass: LLM call -> validate -> succeed or fail.
- No `verify_contract` or `verify_exempt` concepts.
- No `--repo` flag on the planner CLI; the planner has no knowledge of target repo state.
- No structured error codes; validation returns free-form `list[str]`.

### 1C. Planner Compile Flow

Single-pass, no retries. `planner/compiler.py` `compile_plan()`:

1. Read spec + template (lines 108-115)
2. Render prompt via `render_prompt()` (line 129)
3. Single LLM call: `client.generate_text(prompt)` (line 137)
4. `_parse_json()` to strip markdown fences (line 144)
5. `parse_and_validate()` -> normalized WOs + error list (line 159)
6. If errors, write `validation_errors.json` and return failure (lines 174-184)
7. If clean, write `WO-NN.json` files to outdir (line 188)

There is no retry, no error feedback, no second LLM call.

### 1D. Factory Verify Semantics

`factory/nodes_po.py` `po_node()`:

1. **Global verify** runs unconditionally (line 80). `_get_verify_commands()` (lines 24-37):
   - If `scripts/verify.sh` exists in repo -> `["bash", "scripts/verify.sh"]`
   - Otherwise -> fallback: `compileall -q .` + `pip --version` + `pytest -q`
2. If verify exits non-zero -> `FailureBrief(stage="verify_failed")`, skip acceptance (line 94-106).
3. **Acceptance commands** run sequentially from `work_order.acceptance_commands` (lines 115-164).
4. Any non-zero exit -> `FailureBrief(stage="acceptance_failed")`.

There is no verify_exempt. There is no precondition check. There is no postcondition check.

### 1E. Identified Contradictions Between Current Code and COMPILER.md

| Topic | Current code | COMPILER.md proposes | Conflict |
|-------|-------------|---------------------|----------|
| Verify in acceptance | **Required** (`validation.py` line 92-94) with WO-01 exemption | **Banned** (rule R7) | Direct inversion; must flip atomically with prompt |
| Prompt line 121 | `acceptance_commands`: "must include `bash scripts/verify.sh`" | Remove this requirement | Must change prompt + validation in lockstep |
| Prompt line 209 | Example JSON shows `bash scripts/verify.sh` in acceptance | Example must omit it | Follows from above |
| `WorkOrder` schema | 8 fields, no conditions | Adds `preconditions`, `postconditions`, `verify_exempt` | Additive; backward compatible via defaults |
| Manifest format | `system_overview` + `work_orders` | Adds `verify_contract` | Additive; old manifests parse cleanly |

---

## 2. Contract Scope Definition

### 2A. The Contract Claim (Testable)

> **If `validate_plan_v2()` returns zero errors for a plan manifest against a given
> repo file listing, then the factory can ingest and run every work order in sequence
> without encountering a mechanical contradiction.**

"Mechanical contradiction" means any of:
- Acceptance command references a file/module that cannot exist at execution time
- Global verify runs when its prerequisites are structurally unsatisfiable
- A work order expects a file to exist that no prior step creates
- A work order expects a file to be absent that a prior step provably creates
- An acceptance command uses shell features incompatible with `shell=False`
- An acceptance command contains a Python syntax error

### 2B. What "Mechanically Valid Work Order" Means

A work order is **mechanically valid** under the factory's execution model when:

1. **Schema-conformant:** Parses as `WorkOrder(**data)` without error.
2. **IDs contiguous:** `WO-01`, `WO-02`, ... no gaps.
3. **Shell-safe:** No bare shell operators in acceptance commands.
4. **Path-safe:** All paths relative, normalized, no globs.
5. **Preconditions satisfiable:** Every `file_exists` precondition is either in the initial repo or a `file_exists` postcondition of an earlier WO. Every `file_absent` precondition is not contradicted.
6. **Postconditions achievable:** Every postcondition path is in `allowed_files`.
7. **Postconditions complete:** Every `allowed_files` path has a postcondition.
8. **Acceptance dependencies resolvable:** Files imported/referenced in acceptance commands exist in the cumulative state.
9. **Verify contract honored:** Either the cumulative state after this WO satisfies the verify contract, or the WO is marked `verify_exempt`. The contract must be satisfied by the final WO.
10. **No verify in acceptance:** `bash scripts/verify.sh` does not appear in `acceptance_commands` (the factory handles it as a global gate).
11. **Python syntax valid:** `python -c` commands parse under `ast.parse`.

### 2C. What Is OUT OF SCOPE

- **Semantic correctness of generated code.** The SE LLM may write code that compiles but is logically wrong. This is a model-capability problem, not a contract problem.
- **Semantic correctness of acceptance commands.** An acceptance command may test the wrong thing (e.g., wrong argument types). This is addressed by W2 (LLM reviewer), which is staged, not this plan.
- **Runtime environment issues.** Missing `pip` packages, Python version mismatches, disk full, etc. These are deployment concerns.
- **Content of `notes` field.** Notes remain free text. Making notes machine-checkable is a future enhancement.
- **Model capability limits.** The SE LLM may fail to produce correct code within `max_attempts`. The contract only ensures the *instructions* are executable, not that the *execution* will succeed.

---

## 3. Design Decisions: COMPILER.md vs TO_FIX.md

### 3A. Preconditions / Postconditions Schema

**Decision: YES, implement now.**

This is the core structural addition. Without it, the validator cannot reason about cross-WO dependencies, and the bootstrap verify problem has no general solution (only ad-hoc WO-01 exemptions).

The `Condition` model is minimal (`file_exists` | `file_absent` + `path`). It adds two optional list fields to `WorkOrder` with `[]` defaults, so all existing WO JSON files continue to parse. Implementation is pure additive.

### 3B. `verify_contract` / `verify_exempt`

**Decision: YES, implement now (but verify_contract defaults to None for legacy plans).**

This replaces the current WO-01 bootstrap exemption (`validation.py` lines 86-95) with a general mechanism. The current exemption is an ad-hoc special case; `verify_exempt` is computed from the chain and applies to any WO where verify would fail structurally. The factory's PO node reads it to decide whether to run verify.sh or a lightweight fallback.

`verify_contract` is declared by the planner in the manifest. If absent (legacy manifests), the validator skips R6 and `verify_exempt` stays `False` on all WOs (preserving current behavior exactly).

### 3C. Verify Command: Require vs Ban in Acceptance

**Decision: BAN it (COMPILER.md rule R7), replacing the current "require" rule.**

Rationale:
- The factory **already** runs verify unconditionally via `_get_verify_commands()` (PO node line 80). Including it in `acceptance_commands` is redundant execution.
- The WO-01 bootstrap exemption exists solely because the "require" rule creates circularity. Banning it eliminates the exemption entirely.
- This is simpler: one rule (never include) vs two rules (always include, except when bootstrapping).

The flip must be atomic: change `validation.py`, `PLANNER_PROMPT.md`, and example JSON in the same milestone.

### 3D. W1: Compile Retry Loop

**Decision: YES, implement now.**

This is essential infrastructure for convergence. When the validator rejects a plan, the errors are fed back to the planner LLM for self-correction. Without it, every validation failure requires a manual re-run.

**Enhancement over TO_FIX.md's W1 proposal:** Use structured error codes (not prose) in the revision prompt. Each validation error gets a code (e.g., `E101: precondition unsatisfied`) and a machine-readable payload (`{wo_id, field, path}`). This gives the planner LLM precise, unambiguous instructions for what to fix, improving convergence rate.

The retry limit is 2 additional attempts (3 total). Each attempt's raw response and errors are persisted as artifacts.

### 3E. W2: LLM Reviewer Pass

**Decision: STAGE for later. Do not implement in this plan.**

Rationale:
- W2 addresses *semantic* errors (wrong argument types in acceptance commands). The validator in this plan addresses *structural* errors (missing files, broken chains).
- W2 requires a new prompt template, an additional LLM call, and a review-revision loop. It is a substantial addition with moderate risk (reviewer may hallucinate errors).
- W2 benefits from the validator being in place first: the reviewer can focus on semantics because structural errors are already caught.
- W2 is a natural follow-on after the validator and retry loop prove stable.

### 3F. Acceptance Command Dependency Extraction (R5)

**Decision: Implement as WARNINGS initially, promote to ERRORS after validation against real planner output.**

Rationale:
- The `extract_file_dependencies()` function parses `python -c` imports and `bash` script paths. It is intentionally conservative but may have false positives on edge cases (e.g., conditional imports, `__init__.py` resolution ambiguity).
- Warnings are included in the validation output and the revision prompt, so the planner LLM sees them, but they do not block compilation.
- After running against 3+ real specs, review the warning set. If false positive rate is <5%, promote to errors.

### 3G. `ast.parse` for `python -c` Commands

**Decision: YES, implement now as a hard error. Trivial, zero risk, high value.**

This would have caught the WO-06 SyntaxError documented in TO_FIX.md. It is a single function (~15 lines) that parses the Python code string from every `python -c` acceptance command. A SyntaxError from `ast.parse` becomes a validation error. No false positives possible: if it doesn't parse, it won't execute.

---

## 4. Milestone Plan

### M1: Structured Validation Errors + `ast.parse` Check

**Goal:** Replace free-form error strings with structured error objects carrying machine-readable codes. Add `ast.parse` check for `python -c` commands. All changes are internal to `planner/validation.py` and its tests; no schema changes, no prompt changes, no factory changes.

**Changes:**

| File | Change |
|------|--------|
| `planner/validation.py` | Define `ValidationError` dataclass with fields `code: str`, `wo_id: str \| None`, `message: str`, `field: str \| None`. Refactor `validate_plan()` to return `list[ValidationError]` instead of `list[str]`. Add error codes `E001`-`E006` for existing checks. Add new function `_check_python_c_syntax(cmd_str) -> ValidationError \| None` using `ast.parse`. Wire it into the per-WO loop after the shell-operator check. |
| `planner/compiler.py` | Update callers of `parse_and_validate` to handle `ValidationError` objects. Serialize to strings for artifact JSON (backward compatible output). |
| `tests/test_validation.py` (new) | Tests for every existing rule + the new `ast.parse` check, asserting on error codes. |

**Error code table (existing rules, formalized):**

| Code | Rule | Field |
|------|------|-------|
| `E001` | ID format / contiguity | `id` |
| `E002` | Verify command missing in acceptance | `acceptance_commands` |
| `E003` | Shell operator in acceptance command | `acceptance_commands` |
| `E004` | Glob character in path | `allowed_files` / `context_files` |
| `E005` | Pydantic schema validation failed | (varies) |
| `E006` | Python syntax error in `python -c` command (NEW) | `acceptance_commands` |

**Acceptance criteria:**
- `python -m pytest tests/test_validation.py -q` passes.
- `python -m pytest tests/ -q` (full suite) passes with zero regressions.
- Manually verify: `compile_plan()` against an existing spec produces identical WO output (behavior unchanged for valid plans).

**Tests required:**
- `test_ast_parse_valid_python_c` — valid `python -c` command passes.
- `test_ast_parse_invalid_python_c` — syntax error in `python -c` yields `E006`.
- `test_ast_parse_non_python_c_ignored` — `bash` commands are not checked.
- One test per existing rule confirming the new error code.

**Risk:** Low. Pure refactor of error representation + one additive check. Rollback: revert the single commit.

---

### M2: Schema Extension — `Condition`, `preconditions`, `postconditions`

**Goal:** Add the `Condition` model and new optional fields to `WorkOrder`. All existing WO JSON continues to parse. No validation logic yet; just the schema.

**Changes:**

| File | Change |
|------|--------|
| `factory/schemas.py` | Add `Condition(BaseModel)` with `kind: Literal["file_exists", "file_absent"]` and `path: str` (validated by `_validate_relative_path`). Add `preconditions: list[Condition] = []`, `postconditions: list[Condition] = []`, `verify_exempt: bool = False` to `WorkOrder`. Add `_check_postconditions_file_exists_only` model validator. |
| `tests/test_schemas.py` | Add tests for `Condition` model validation, postcondition kind restriction, backward compatibility (WO without new fields parses cleanly). |

**Acceptance criteria:**
- `python -m pytest tests/test_schemas.py -q` passes with new tests.
- Existing `load_work_order()` on old-format JSON files returns a `WorkOrder` with `preconditions=[]`, `postconditions=[]`, `verify_exempt=False`.
- Full suite passes with zero regressions.

**Tests required:**
- `test_condition_file_exists_valid` / `test_condition_file_absent_valid`
- `test_condition_invalid_path_rejected`
- `test_postcondition_file_absent_rejected` (only `file_exists` allowed)
- `test_wo_backward_compatible` (old JSON without new fields parses)
- `test_wo_with_conditions_parses`

**Risk:** Low. Additive schema change with defaults. Rollback: revert commit.

---

### M3: Plan-Time Chain Validator (`validate_plan_v2`)

**Goal:** Implement the cross-work-order chain validator with rules R1-R7 and `verify_exempt` computation. This is the core deliverable.

**Changes:**

| File | Change |
|------|--------|
| `planner/validation.py` | Add `validate_plan_v2(work_orders, verify_contract, repo_file_listing) -> list[ValidationError]`. Implements rules R1 (precondition satisfiability), R2 (no contradictions), R3 (postcondition achievability), R4 (allowed-files coverage), R5 (acceptance command dependencies — as warnings), R6 (verify-exempt computation + verify contract reachability), R7 (ban verify in acceptance). Add `extract_file_dependencies(cmd_str) -> list[str]` with stdlib allowlist. Add `compute_verify_exempt(work_orders, verify_contract, repo_file_listing) -> list[dict]`. |
| `planner/validation.py` | New error codes: `E101`-`E107` for chain rules, `W101` for R5 dependency warnings. |
| `tests/test_validator.py` (new) | Regression tests for all 7 rules + positive cases. Fixture-based. |
| `tests/fixtures/plans/` (new dir) | JSON fixtures for valid and invalid plans (see section 5). |

**Error code table (new chain rules):**

| Code | Rule | Description |
|------|------|-------------|
| `E101` | R1 | Precondition unsatisfied |
| `E102` | R2 | Contradictory preconditions (same path, both exists and absent) |
| `E103` | R3 | Postcondition path not in `allowed_files` |
| `E104` | R4 | `allowed_files` entry has no postcondition |
| `E105` | R7 | `bash scripts/verify.sh` in `acceptance_commands` |
| `E106` | R6 | Verify contract never fully satisfied by plan |
| `W101` | R5 | Acceptance command depends on file not in cumulative state (warning) |

**Acceptance criteria:**
- `python -m pytest tests/test_validator.py -q` passes all cases.
- The 5 mandatory failure cases pass (see section 5): bootstrap circularity (`E105`), missing dependency (`E101`), contradictory constraints (`E102`), missing file path (`E103`), unverifiable acceptance (`W101`).
- Valid plan fixture passes with zero errors.
- `validate_plan_v2` is callable independently (not yet wired into `compile_plan`).

**Tests required (minimum):**
- `test_r1_missing_dependency` — `E101`
- `test_r2_contradictory_preconditions` — `E102`
- `test_r3_postcondition_outside_allowed` — `E103`
- `test_r4_allowed_file_no_postcondition` — `E104`
- `test_r5_unverifiable_acceptance_warns` — `W101`
- `test_r6_verify_contract_never_satisfied` — `E106`
- `test_r7_verify_in_acceptance_banned` — `E105`
- `test_verify_exempt_computed_correctly`
- `test_valid_two_wo_plan_passes` — zero errors
- `test_valid_plan_against_nonempty_repo` — precondition satisfied by initial repo

**Risk:** Medium. This is the most logic-dense milestone. Mitigation: `validate_plan_v2` is a pure function with no side effects, making it easy to test in isolation. Rollback: revert commit; existing `validate_plan` is untouched and still wired in.

---

### M4: Prompt Update + Validation Rule Flip (Atomic)

**Goal:** Update `PLANNER_PROMPT.md` to match the new contract (conditions, no verify in acceptance, verify_contract in manifest). Simultaneously flip the validation rule from "require verify in acceptance" to "ban verify in acceptance". Also update the example JSON in the prompt.

This milestone **must** be a single atomic commit. Changing the prompt without the validation flip (or vice versa) creates an inconsistent state.

**Changes:**

| File | Change |
|------|--------|
| `planner/PLANNER_PROMPT.md` | (1) Add `preconditions` and `postconditions` to "WORK ORDER DESIGN RULES" section (after line 126). (2) Change line 121 from `must include \`bash scripts/verify.sh\`` to "Do NOT include `bash scripts/verify.sh` — the factory runs it automatically as a global gate." (3) Replace WO-01 BOOTSTRAPPING section (lines 128-155) with updated text explaining conditions-based bootstrap. (4) Add `verify_contract` to OUTPUT FORMAT (after line 200). (5) Update example JSON (lines 200-214) to include `preconditions`, `postconditions`, omit verify from acceptance. |
| `planner/validation.py` | In `validate_plan()`: remove the "require verify in acceptance" rule (lines 85-95). The `is_bootstrap` exemption is deleted entirely. R7 (ban verify in acceptance) is now enforced via `validate_plan_v2` which will be wired in M5. During M4, the old rule is simply removed (loosening, not tightening — safe). |
| `planner/validation.py` | Remove `VERIFY_COMMAND` constant usage from the "require" check. Keep the constant for R7 in `validate_plan_v2`. |

**Acceptance criteria:**
- `python -m pytest tests/ -q` passes (existing tests that checked verify-command presence must be updated or removed).
- Manually inspect the prompt: no reference to "must include `bash scripts/verify.sh`" in acceptance. WO-01 section describes conditions. OUTPUT FORMAT shows `verify_contract`.
- The planner prompt is self-consistent: every field mentioned in DESIGN RULES appears in the example JSON.

**Tests required:**
- Update any existing test in `tests/` that asserts verify-command presence in acceptance (search for `VERIFY_COMMAND` references in tests).
- `test_wo_without_verify_in_acceptance_passes` — acceptance without verify is now valid.

**Risk:** Medium. Prompt changes affect LLM output stochastically. Mitigation: the validator (M3) catches structural problems in LLM output; the retry loop (M5) auto-corrects them. Rollback: revert the single commit; old prompt + old validation rule are restored together.

---

### M5: Compile Integration + Retry Loop (W1)

**Goal:** Wire `validate_plan_v2` into `compile_plan()`. Add the iterative feedback loop: on validation failure, build a structured revision prompt and re-call the LLM. Add `--repo` flag to the planner CLI.

**Changes:**

| File | Change |
|------|--------|
| `planner/compiler.py` | (1) Add `repo_path: str \| None = None` parameter to `compile_plan()`. (2) If provided, build `repo_file_listing` via `os.walk`. (3) After `parse_and_validate()`, call `validate_plan_v2()` with chain rules. (4) If `verify_contract` present and no errors, call `compute_verify_exempt()` to inject `verify_exempt` into WO dicts. (5) Wrap the LLM-call-through-validation block in a `for attempt in range(MAX_COMPILE_ATTEMPTS)` loop (default 3). On failure, call `_build_revision_prompt()`. (6) Persist per-attempt artifacts: `llm_raw_response_attempt_{N}.txt`, `validation_errors_attempt_{N}.json`. (7) Update `_write_summary` to include `compile_attempts`, per-attempt error lists. |
| `planner/compiler.py` | New function `_build_revision_prompt(template_text, spec_text, previous_json, errors: list[ValidationError]) -> str`. Formats errors as structured list with codes: `"[E101] WO-03: precondition file_exists('src/models.py') unsatisfied — no prior WO declares this as a postcondition."` Instructs the LLM to fix only the cited errors and preserve everything else. |
| `planner/cli.py` | Add `--repo` flag to the `compile` subparser. Pass to `compile_plan()`. |
| `planner/io.py` | If needed, update `write_work_orders` to handle `verify_exempt` and condition fields in output JSON. |

**Acceptance criteria:**
- `python -m pytest tests/ -q` passes.
- Manual end-to-end test: `python -m planner compile --spec spec.txt --outdir ./out_test --repo ~/repos/sudoku --overwrite`. Inspect artifacts: `compile_summary.json` shows `compile_attempts`; `validation_errors_attempt_1.json` exists if first attempt had errors; final WO files include `preconditions` and `postconditions`.
- If first LLM attempt has validation errors, second attempt is automatically triggered (visible in artifacts).
- If no errors on first attempt, loop exits immediately (single attempt, same behavior as today).

**Tests required:**
- `test_compile_plan_with_repo` — mock LLM, valid output, repo_file_listing is passed through.
- `test_compile_retry_on_validation_error` — mock LLM returns invalid JSON on first call, valid on second. Assert 2 attempts in summary.
- `test_compile_max_retries_exhausted` — mock LLM returns invalid every time. Assert failure with all errors.
- `test_revision_prompt_contains_error_codes` — verify the revision prompt includes structured `[E1xx]` codes.

**Risk:** Medium. The retry loop adds LLM calls (cost + latency) but only when validation fails. The loop is purely additive: if first attempt passes, behavior is identical to today. Rollback: revert commit; `compile_plan()` returns to single-pass.

---

### M6: Factory Runtime Gates

**Goal:** Add precondition checking before SE runs and postcondition checking after TR writes. Add `verify_exempt` support in PO node. These are safety nets; if the plan-time validator did its job, they should never fire.

**Changes:**

| File | Change |
|------|--------|
| `factory/nodes_se.py` | At top of `se_node()`, before `_read_context_files()`: iterate `work_order.preconditions`. For each `file_exists` condition, check `os.path.isfile()`. For each `file_absent`, check `not os.path.isfile()`. On failure, return `FailureBrief(stage="preflight", ...)` with message prefix `PLANNER-CONTRACT BUG:`. |
| `factory/nodes_po.py` | (1) After verify, before acceptance: iterate `work_order.postconditions`. For each `file_exists`, check `os.path.isfile()`. On failure, return `FailureBrief(stage="acceptance_failed", ...)` — this is retryable (executor error). (2) In verify section: read `work_order.verify_exempt`. If `True`, run only `[["python", "-m", "compileall", "-q", "."]]` instead of `_get_verify_commands()`. |
| `tests/test_nodes.py` | Add tests for precondition gate, postcondition gate, verify_exempt behavior. |

**Acceptance criteria:**
- `python -m pytest tests/ -q` passes.
- Unit test: WO with `precondition: file_exists("x.py")` against a repo where `x.py` does not exist -> `FailureBrief(stage="preflight")` with "PLANNER-CONTRACT BUG" in excerpt.
- Unit test: WO with `verify_exempt=True` -> PO node runs `compileall` only, not `bash scripts/verify.sh`.
- Unit test: WO with empty preconditions/postconditions -> no-op (backward compatible).

**Tests required:**
- `test_precondition_file_exists_satisfied` — passes through to LLM call.
- `test_precondition_file_exists_fails` — returns preflight FailureBrief.
- `test_precondition_file_absent_fails` — returns preflight FailureBrief.
- `test_postcondition_file_exists_fails` — returns acceptance_failed FailureBrief.
- `test_verify_exempt_skips_verify_sh` — compileall only.
- `test_verify_exempt_false_runs_verify_sh` — normal verify behavior.
- `test_empty_conditions_noop` — backward compatible no-op.

**Risk:** Low. All checks are additive. When conditions are empty (old-format WOs), the gates are no-ops. Rollback: revert commit.

---

### Summary: Milestone Dependency Graph

```
M1 (structured errors + ast.parse)
 |
 v
M2 (schema: Condition + WO fields)
 |
 v
M3 (chain validator: validate_plan_v2)
 |
 +------> M4 (prompt + rule flip) -----> M5 (compile integration + retry loop)
 |
 +------> M6 (factory runtime gates) [can run in parallel with M4-M5]
```

M1 and M2 are prerequisites. M3 is the core. M4 and M5 are the integration milestones. M6 can be done in parallel with M4/M5 since it depends only on M2 (the schema).

---

## 5. Regression Suite Strategy

### 5A. Fixture Location

```
tests/
  fixtures/
    plans/
      valid_bootstrap_and_skeleton.json
      valid_single_wo_existing_repo.json
      invalid_bootstrap_circularity.json
      invalid_missing_dependency.json
      invalid_contradictory_preconditions.json
      invalid_postcond_outside_allowed.json
      invalid_unverifiable_acceptance.json
      invalid_verify_never_satisfied.json
      invalid_python_syntax.json
      invalid_allowed_no_postcondition.json
    repo_listings/
      empty.json          # []
      sudoku_baseline.json # ["scripts/verify.sh", ...]
```

Each `plans/*.json` file is a complete manifest (`system_overview`, `verify_contract`, `work_orders`). Each `repo_listings/*.json` is a JSON array of relative file paths representing the initial repo state.

### 5B. Fixture Test Runner

`tests/test_validator.py` loads fixtures and asserts:

```python
@pytest.mark.parametrize("fixture_name,expected_codes", [
    ("invalid_bootstrap_circularity", {"E105"}),
    ("invalid_missing_dependency", {"E101"}),
    ("invalid_contradictory_preconditions", {"E102"}),
    ("invalid_postcond_outside_allowed", {"E103"}),
    ("invalid_unverifiable_acceptance", {"W101"}),
    ("invalid_verify_never_satisfied", {"E106"}),
    ("invalid_python_syntax", {"E006"}),
    ("invalid_allowed_no_postcondition", {"E104"}),
])
def test_invalid_plan_rejected(fixture_name, expected_codes):
    plan = load_fixture(f"plans/{fixture_name}.json")
    repo = load_fixture("repo_listings/empty.json")
    errors = validate_plan_v2(plan["work_orders"], plan.get("verify_contract"), set(repo))
    actual_codes = {e.code for e in errors}
    assert expected_codes <= actual_codes, f"Expected {expected_codes}, got {actual_codes}"


@pytest.mark.parametrize("fixture_name", [
    "valid_bootstrap_and_skeleton",
    "valid_single_wo_existing_repo",
])
def test_valid_plan_passes(fixture_name):
    plan = load_fixture(f"plans/{fixture_name}.json")
    repo_name = plan.get("_test_repo_listing", "empty")
    repo = load_fixture(f"repo_listings/{repo_name}.json")
    errors = validate_plan_v2(plan["work_orders"], plan.get("verify_contract"), set(repo))
    assert errors == []
```

### 5C. CI Execution

```bash
python -m pytest tests/ -x -q
```

All fixture-based tests run as part of the normal test suite. No special CI configuration needed. The fixtures are static JSON files checked into the repo.

### 5D. Mandatory Fixture Coverage (the 5 required failure cases)

| # | Case | Fixture | Expected Code | What It Tests |
|---|------|---------|---------------|---------------|
| 1 | Bootstrap verify circularity | `invalid_bootstrap_circularity.json` | `E105` | WO-01 creates verify.sh and has `bash scripts/verify.sh` in acceptance |
| 2 | Missing dependency | `invalid_missing_dependency.json` | `E101` | WO-02 declares `file_exists("src/models.py")` as precondition; no prior WO creates it |
| 3 | Contradictory constraints | `invalid_contradictory_preconditions.json` | `E102` | WO-01 has both `file_exists("x.py")` and `file_absent("x.py")` in preconditions |
| 4 | Missing file path | `invalid_postcond_outside_allowed.json` | `E103` | Postcondition declares `file_exists("src/b.py")` but `src/b.py` not in `allowed_files` |
| 5 | Unverifiable acceptance | `invalid_unverifiable_acceptance.json` | `W101` | Acceptance imports `mypackage.solver` but no WO creates `mypackage/solver.py` |

Plus two positive fixtures:
| # | Case | Fixture | Expected |
|---|------|---------|----------|
| 6 | Well-formed bootstrap + skeleton | `valid_bootstrap_and_skeleton.json` | Zero errors |
| 7 | Single WO against existing repo | `valid_single_wo_existing_repo.json` | Zero errors |
