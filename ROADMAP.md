# ROADMAP

**Last updated:** 2026-02-10

**Completed prerequisite:** M1–M6 structural contract (E001–E006, E101–E106,
verify_contract, verify_exempt) — all implemented in `planner/validation.py`
and `planner/compiler.py`.

Two tracks of work remain, in priority order:

1. **Deterministic contract fixes** (Part 1) — surgical code changes to close
   gaps found by the adversarial audit (see `FINDINGS_LEDGER.md`). These fix
   real contract violations in the deterministic wrapper.
2. **Prompt semantic hardening** (Part 2) — prompt template changes to reduce
   LLM-generated acceptance command failures. These improve LLM output quality
   but do not affect deterministic guarantees.

---

# Part 1: Deterministic Contract Layer Fixes

Provenance: Two independent adversarial audits of the planner and factory code
were compiled into `FINDINGS_LEDGER.md` (AUD-01 through AUD-16). The
milestones below are the actionable subset, scoped to wrapper-only code
changes, ordered by importance.

Each milestone is a single, self-contained code change. Every milestone maps
to one or more findings in `FINDINGS_LEDGER.md`.

**Excluded by owner decision (accepted runtime assumptions):**
- AUD-01, AUD-02, AUD-15 — TOCTOU / concurrent FS mutation (sole-writer assumption)
- AUD-09 — outdir race + case sensitivity (same assumption, plus low practical risk)
- AUD-14 — unsandboxed acceptance commands (in-situ modification is by design)
- AUD-16 — `run_work_orders.sh` (to be deleted)

---

## M-01  Never trust LLM-provided `verify_exempt` [CRITICAL] ✅ DONE

**Fixes:** AUD-03
**Status:** Implemented and tested (2026-02-10).
Code: `planner/compiler.py` — unconditional `verify_exempt` sanitization.
Tests: `TestVerifyExemptSanitisation` in `tests/planner/test_compile_loop.py`
(5 passing + 1 xfail pending M-03). Full suite: 386 passed.

**Why this is #1:** This is the single most important finding in the entire
audit. The planner only overwrites `verify_exempt` when `verify_contract` is
truthy. If the LLM omits `verify_contract` but sets `verify_exempt: true` on
every work order, those values flow through to the emitted WO JSON untouched.
The factory trusts the flag and skips global verification. Acceptance commands
then execute against unverified code. This is a clean bypass of the verify
gate triggered by nothing more than adversarial LLM output — the exact threat
the deterministic wrapper exists to prevent.

**File:** `planner/compiler.py`

**Change:** After the validation-success block (around line 328–333), replace
the conditional `if verify_contract and final_work_orders` with unconditional
`verify_exempt` sanitization:

- If `verify_contract` is a valid dict with `requires`, call
  `compute_verify_exempt` as today.
- Otherwise (absent, `None`, falsy, wrong type), force
  `verify_exempt = False` on every WO dict.
- In both branches, the LLM-provided value is overwritten — never preserved.

**Size:** ~10 lines changed in one function.

**Regression test:**
`test_verify_exempt_forced_false_when_no_contract` — supply WO dicts with
`verify_exempt: true` and no `verify_contract`. Assert every emitted WO has
`verify_exempt: false`.
`test_verify_exempt_computed_when_contract_present` — supply valid
`verify_contract`. Assert `compute_verify_exempt` result is used and the
LLM-provided value is gone.

---

## M-02  Catch `BaseException` in the emergency handler [CRITICAL] ✅ DONE

**Fixes:** AUD-05
**Status:** Implemented and tested (2026-02-10).
Code: `factory/run.py` — `except BaseException`, type-specific exit codes
(130 for KeyboardInterrupt, re-raise for SystemExit).
Tests: `TestBaseExceptionRollback` in `tests/factory/test_end_to_end.py`
(3 tests: rollback on Ctrl-C, dirty-repo restoration, SystemExit preserved).
Full suite: 389 passed.

**Why this matters:** Users routinely press Ctrl-C during long factory runs
(LLM calls take minutes). `KeyboardInterrupt` inherits from `BaseException`,
not `Exception`. The current `except Exception` on run.py line 111 lets it
sail through — no rollback, no summary, repo left dirty with partial writes.
This directly violates the "repo never left in indeterminate state" guarantee
under completely normal user behaviour.

**File:** `factory/run.py`

**Change:**
1. Replace `except Exception as exc:` with `except BaseException as exc:`.
2. Inside the handler, after best-effort rollback + summary write:
   - If `isinstance(exc, KeyboardInterrupt)`: `sys.exit(130)` (standard
     SIGINT exit code).
   - If `isinstance(exc, SystemExit)`: re-raise to preserve the original
     exit code.
   - Otherwise: `sys.exit(2)` as today.
3. Also widen the inner rollback guard from `except Exception` to
   `except BaseException` (rollback itself should not be defeated by a
   second KeyboardInterrupt during cleanup).

**Size:** ~8 lines changed in one function.

**Regression test:**
`test_keyboard_interrupt_triggers_rollback` — monkeypatch `graph.invoke` to
raise `KeyboardInterrupt`. Assert `rollback` was called and repo is clean.
Assert process exit code is 130.

---

## M-03  Type-guard planner validation against non-dict inputs [HIGH] ✅ DONE

**Fixes:** AUD-12
**Status:** Implemented and tested (2026-02-10).
Code: `planner/validation.py` — isinstance guards in `parse_and_validate`,
`validate_plan`, `validate_plan_v2`, and `compute_verify_exempt`.
Tests: 5 tests in `TestE000Structural` (`test_structural_validation.py`),
5 tests in `TestVerifyContractTypeGuard` (`test_chain_validation.py`),
plus M-01 xfail resolved to passing `test_wrong_type_contract_rejected_not_crash`.
Full suite: 400 passed.

**Why:** If the LLM emits `"work_orders": [42, "hello"]`, the planner calls
`42.get("id")` and crashes with `AttributeError`. The CLI's broad
`except Exception` catches it but misclassifies it (exit code 3 = API error
instead of 2 = validation error). The retry loop may not engage. The core
requirement — "never silently accept invalid planner output" — is not met
because the system crashes instead of producing a structured rejection.

**Files:** `planner/validation.py`

**Changes:**
1. In `validate_plan`, before the normalization loop, check each element:
   if not `isinstance(wo, dict)`, append a `ValidationError(code=E000, ...)`
   and skip that element. Do not call `normalize_work_order` on non-dicts.
2. In `validate_plan_v2`, guard the `verify_contract.get(...)` call: if
   `verify_contract is not None and not isinstance(verify_contract, dict)`,
   return a single `ValidationError(code=E000, ...)` immediately.
3. In `compute_verify_exempt`, add the same `isinstance` guard at entry — if
   `verify_contract` is not a dict, force `verify_exempt = False` for all WOs
   (consistent with M-01).

**Size:** ~15 lines of guard clauses across 3 functions.

**Regression test:**
`test_non_dict_work_order_elements` — pass `[42, "x", []]` as work_orders.
Assert structured errors returned (not `AttributeError`).
`test_non_dict_verify_contract` — pass `verify_contract=[]`. Assert
structured error, not crash.

---

## M-04  Emit error code on `shlex.split` failure instead of silent skip [HIGH] ✅ DONE

**Fixes:** AUD-11
**Status:** Implemented and tested (2026-02-10).
Code: `planner/validation.py` — new `E007_SHLEX` constant; E007 emitted in
the E003 shell-operator loop and in `_check_python_c_syntax` on `ValueError`.
Tests: `TestE007Shlex` (6 tests) + 2 updated existing tests in
`test_structural_validation.py`. Full suite: 406 passed.

**Why:** A command with unmatched quotes (`python -c 'print(1`) silently
passes E003 and E006 because `shlex.split` raises `ValueError` and the
handler does `continue` / `return None`. This means the planner's compile
gate is incomplete — it emits a WO with a structurally unparseable command.
The factory catches it at runtime, so no safety gate is ultimately bypassed
end-to-end, but the planner contract promises structured validation and this
is a hole.

**File:** `planner/validation.py`

**Changes:**
1. Add new error code constant: `E007_SHLEX = "E007"`.
2. In `validate_plan`, in the E003 shell-operator loop: replace the bare
   `continue` on `ValueError` with an `errors.append(ValidationError(
   code=E007_SHLEX, ...))` + `continue`.
3. In `_check_python_c_syntax`: replace `return None` on `ValueError` with
   returning a `ValidationError(code=E007_SHLEX, ...)`.

**Size:** ~10 lines changed across 2 locations + 1 constant.

**Regression test:**
`test_unparseable_command_emits_e007` — submit `["echo 'unterminated"]` as
acceptance commands. Assert `E007` in returned errors.

---

## M-05  Make `save_json` atomic [HIGH]

**Fixes:** AUD-04

**Why:** `factory/util.py::save_json` uses bare `open()` + `json.dump()`.
A kill -9, OOM-killer, or power loss mid-write corrupts the artifact — most
critically `run_summary.json`, which is the final verdict. The exact
`tempfile + fsync + os.replace` pattern already exists in two places in this
codebase (`planner/io.py::_atomic_write`, `factory/nodes_tr.py::_atomic_write`).
This is a copy-paste fix. Under normal user behaviour (Ctrl-C, covered by
M-02), the non-atomic write is also a risk if the `save_json` for the
emergency summary is itself interrupted.

**File:** `factory/util.py`

**Change:** Replace the `save_json` body with the atomic pattern:
`tempfile.mkstemp` in parent dir → write + `flush` + `fsync` → `os.replace`.
On `BaseException`, `unlink` the temp file and re-raise. Identical to the
existing `_atomic_write` functions.

**Size:** ~12 lines replacing 4.

**Regression test:**
`test_save_json_atomic_on_crash` — write a file, then monkeypatch
`os.replace` to raise `OSError`. Assert original file is unchanged and no
temp files remain.

---

## M-06  Apply `posixpath.normpath` in `normalize_work_order` [MEDIUM]

**Fixes:** AUD-10

**Why:** The planner's chain validator tracks cumulative file state using
raw (non-normpath'd) strings. `"./src/a.py"` and `"src/a.py"` are treated
as distinct by the planner but collapse to the same path in the factory's
Pydantic validator. This means the chain validator's dependency tracking
(P6–P10) can produce false rejections or — worse — miss genuine cross-WO
conflicts where two WOs both declare the same physical file under different
string representations. The factory schema already applies `normpath`; the
planner should too, so the two sides agree.

**File:** `planner/validation.py`

**Change:** In `normalize_work_order`, after `_strip_strings`, apply
`posixpath.normpath` to every string in `allowed_files`, `context_files`,
and every `path` value inside `preconditions` / `postconditions` dicts.
Then deduplicate as today.

**Size:** ~12 lines added to one function.

**Regression test:**
`test_normalize_collapses_dotslash_paths` — submit `["./src/a.py",
"src/a.py"]` in `allowed_files`. Assert result is `["src/a.py"]` (single
element after normpath + dedup).

---

## M-07  Reject `"."`, NUL, and control chars in path validator [MEDIUM]

**Fixes:** AUD-13

**Why:** `_validate_relative_path` in `factory/schemas.py` rejects `..`,
absolute paths, and glob chars, but accepts `"."` (which `normpath` keeps
as-is), NUL bytes, and control characters. These cause `IsADirectoryError`,
`ValueError`, or other unhandled exceptions downstream instead of structured
Pydantic validation errors. No out-of-scope write can occur, but the error
path escapes deterministic classification.

**File:** `factory/schemas.py`

**Change:** After the existing `normpath` + `startswith("..")` check, add:
1. `if normalized == ".": raise ValueError("path must not be '.'")`.
2. `if "\x00" in p: raise ValueError("path must not contain NUL")`.
3. `if any(ord(c) < 0x20 for c in p): raise ValueError("path contains
   control characters")`.

**Size:** ~6 lines added to one function.

**Regression test:**
`test_dot_path_rejected`, `test_nul_path_rejected`,
`test_control_char_path_rejected` in `tests/factory/test_schemas.py`.

---

## M-08  Normalize E105 verify-command match via `shlex.split` [LOW]

**Fixes:** AUD-07

**Why:** The E105 check uses `cmd_str.strip() == "bash scripts/verify.sh"`.
Double spaces, `./` prefixes, and absolute paths bypass it. Both audits flag
this; both note that existing tests document it as accepted. The factory's
own sequencing (verify runs before acceptance regardless) is the primary
control. Still, tightening the match makes the planner contract honest
rather than leaky-by-documented-design.

**File:** `planner/validation.py`

**Change:** Replace the exact string comparison with a normalized one:
```
try:
    tokens = shlex.split(cmd_str)
except ValueError:
    continue  # shlex errors handled by E007 (M-04)
normalized = tokens[:1] + [posixpath.normpath(t) for t in tokens[1:]]
if normalized == ["bash", "scripts/verify.sh"]:
    errors.append(...)
```

**Size:** ~6 lines replacing 2.

**Regression test:**
`test_e105_catches_double_space`, `test_e105_catches_dotslash` — submit
`"bash  scripts/verify.sh"` and `"bash ./scripts/verify.sh"`. Assert E105
for both.

---

## M-09  Add explicit `rollback_failed` status to run summary [LOW]

**Fixes:** AUD-06

**Why:** If `git reset --hard` or `git clean -fdx` fails, the current code
warns on stderr but still writes the emergency summary with `verdict: ERROR`
and no machine-readable indicator that rollback failed. Downstream tooling
(scoring scripts, CI) cannot distinguish "error with clean repo" from "error
with dirty repo". The fix is small and purely additive.

**Files:** `factory/run.py`

**Change:** In the emergency handler, after the rollback try/except block,
call `is_clean(repo_root)`. If `False`, set `summary_dict["rollback_failed"]
= True` and include the remediation command string. The normal-path summary
(line 163+) should set `rollback_failed = False` explicitly.

**Size:** ~8 lines added.

**Regression test:**
`test_rollback_failure_marked_in_summary` — monkeypatch `rollback` to raise.
Assert `run_summary.json` contains `"rollback_failed": true`.

---

## M-10  Add JSON payload size guard [LOW]

**Fixes:** AUD-08 (size component only)

**Why:** Neither `_parse_json` (planner) nor `parse_proposal_json` (factory)
enforces a maximum payload size before calling `json.loads`. In practice the
LLM API's `max_output_tokens` caps the response at ~256 KB, so this is pure
defense-in-depth. However, if the API limit is ever raised or the system is
used with a local model, an adversarial multi-GB response would OOM the
process. The fix is a one-line guard.

Duplicate-key rejection (`object_pairs_hook`) from AUD-08 is NOT recommended
at this time. Audit B demonstrated that duplicate keys are not exploitable
(they produce `E000` validation failures), and adding a custom JSON parser
introduces more surface area than it removes. The size guard alone is
sufficient.

**Files:** `planner/compiler.py`, `factory/llm.py`

**Change:** Before the `json.loads` call in both `_parse_json` and
`parse_proposal_json`, add:
```
if len(text) > 10 * 1024 * 1024:
    raise ValueError(f"JSON payload too large: {len(text)} bytes (max 10 MB)")
```

**Size:** 2 lines added per file (4 total).

**Regression test:**
`test_parse_json_rejects_oversized` — pass 11 MB string. Assert `ValueError`.

---

## Part 1 priority assessment

Based on the requirement that the deterministic wrapper must not silently
accept invalid planner output, must not bypass verification, and must not
leave the repo dirty under normal user behaviour:

**Non-negotiable (do these or the wrapper contract is provably broken):**

- **M-01** — verify_exempt bypass. This is an actual, exploitable hole in
  the core verification gate. An adversarial (or merely confused) LLM can
  disable verification for every work order. The fix is 10 lines.
- **M-02** — BaseException rollback escape. Every user who has ever pressed
  Ctrl-C during a factory run has hit this. The repo is left dirty with no
  rollback and no summary. The fix is 8 lines.

**Strongly recommended (the planner contract has real gaps without these):**

- **M-03** — Type guards. The planner crashes on non-dict WO elements instead
  of producing structured errors. The CLI misclassifies the failure. The
  retry loop does not engage. This is a gap in "never silently accept invalid
  planner output" because the crash IS a silent acceptance — the error is
  unstructured and the exit code is wrong.
- **M-04** — shlex error code. The compile gate silently passes commands it
  cannot parse. The factory catches them at runtime, but the planner's
  contract claims exhaustive structural validation and this is a documented
  hole.
- **M-05** — Atomic save_json. The pattern exists in two other places in the
  same codebase. Leaving factory artifacts non-atomic is inconsistent and
  fragile, especially for `run_summary.json` which is the final verdict.

**Good hygiene (worth doing, not urgent):**

- **M-06** — Path normpath. Fixes a real planner/factory inconsistency that
  can cause false rejections. No safety impact.
- **M-07** — Special path rejection. Turns unstructured exceptions into
  structured validation errors. No safety impact.
- **M-08** — E105 normalization. Tightens a policy check. The factory's own
  sequencing is the real gate.
- **M-09** — Rollback status. Purely additive observability. Helpful for CI.
- **M-10** — JSON size guard. Pure defense-in-depth against a theoretical
  threat that is currently blocked by API limits.

---
---

# Part 2: Planner Prompt Hardening — Semantic Reliability

The deterministic contract (Part 1) ensures the wrapper never accepts
structurally invalid output. This part addresses a class of **semantic
failures** that no deterministic validator can catch: the planner writes
acceptance commands that are structurally valid but factually wrong.

## 1. Problem

M1-M6 solved the structural contract: if the planner emits a work order with
unsatisfied preconditions, missing postconditions, or shell-incompatible
commands, the validator catches it deterministically.  That machinery is
working.

What remains is a class of **semantic failures in acceptance commands** that
no deterministic validator can catch.  Two observed instances:

- **Sudoku (WO-05):** The planner hardcoded the expected solution string for
  a specific puzzle.  The executor wrote a solver that ran without errors but
  produced a different (possibly correct, possibly incorrect) output.  The
  acceptance command asserted exact equality against a string the planner
  fabricated without running the code.  Unfixable by the executor across 5
  attempts.

- **Word game (WO-04):** The planner asserted
  `select_word(random.Random(0)) == 'pear'`.  The actual output of
  `random.Random(0).choice(WORDS)` is `'orange'`.  The planner guessed the
  output of a nondeterministic function.  Identical failure across 5 attempts.

Both failures share the same root cause: **the planner was asked to produce a
test oracle (an expected output value) for a computation it cannot execute.**
The planner is not an interpreter.  It generates code structure in a single
forward pass.  Any acceptance command that requires the planner to mentally
simulate code execution is unreliable.

## 2. Diagnosis: Why the Current Prompt Allows This

The ACCEPTANCE COMMAND DESIGN PRINCIPLE section (lines 173-192) says:

> Every work order MUST include at least one acceptance command that
> independently verifies the work order's specific intent.

It then lists "good patterns" — all of which are tier-1 existence checks
(`import X`, `hasattr`, `os.path.isfile`).  It lists "bad patterns" — both
of which are about the verify command, not about assertion quality.

The prompt gives **no guidance whatsoever** about what kinds of assertions the
planner can reliably produce.  It says "verify intent" but doesn't say "here's
what you can and can't verify without running code."  The planner fills this
gap by writing the most specific assertion it can think of — which often means
hardcoding exact outputs of functions it defined but cannot execute.

## 3. Principle: The Oracle Problem

A traditional compiler can type-check a program without running it, but it
cannot determine the program's output for a given input — that requires
execution.  The planner is in the same position: it can reason about the
*structure* of code (what modules exist, what functions are defined, what
types they accept) but it cannot reliably predict *runtime values*.

This is the **oracle problem**: the planner is being asked to produce a test
oracle (an expected output value) for a computation it has no ability to
evaluate.  The failure is not stochastic noise — it's a systematic
consequence of asking a non-executing system to assert execution results.

The fix is not "don't hardcode *this particular* value."  The fix is to give
the planner a **testability discipline** — a clear statement of which
assertions it can reliably produce, derived from what information it has
access to.

## 4. Testability Hierarchy

The planner has access to the following information at generation time:

- The module structure it designed (file paths, package layout)
- The API signatures it specified (function names, parameter names, return types)
- The data contracts it defined (field names, type constraints, invariants)

It does **not** have access to:

- Runtime return values of any function
- The behavior of standard library functions on specific inputs
- Floating-point precision or platform-dependent behavior

From this, two tiers of reliable acceptance assertions follow:

### Tier 1: Existence and Structure (always reliable)

The planner designed the module layout and API surface.  It knows with
certainty what files, modules, classes, and functions will exist.

**Reliable assertions:**
- `from package.module import ClassName`
- `assert hasattr(ClassName, 'method_name')`
- `assert callable(ClassName.method_name)`
- `import os; assert os.path.isfile('path/to/file.py')`

**Why reliable:** These test the structure the planner itself defined.  No
execution is needed.  If the executor follows the notes, these assertions
pass.  If the executor deviates, these catch it.

### Tier 2: Contract Properties (reliable)

The planner defined the API contracts: what types functions return, what
invariants hold, what constraints the data satisfies.  These can be tested
with **property assertions** that don't require knowing specific output
values.

**Reliable assertions:**
- `assert isinstance(f(trivial_input), expected_type)` — return type
- `assert result in known_collection` — membership in planner-defined set
- `assert f(x) == f(x)` — determinism / idempotence
- `assert len(result) == expected_length` — structural constraint
- `assert all(k in result for k in required_keys)` — dict shape
- `assert 0 <= value <= 1` — range constraint

**Why reliable:** These test *properties of the contract*, not specific
computed values.  The planner knows the type contract because it wrote it.
The planner knows the membership set because it defined it.  No mental
simulation of execution is required.

### What to avoid (the oracle trap)

Any assertion that requires the planner to predict the output of a
non-trivial computation:
- `assert solve(puzzle) == 'specific_solution_string'`
- `assert select_word(Random(0)) == 'pear'`
- `assert compute_score(data) == 42.7`
- `assert hash(result) == 'abc123...'`

These fail whenever the planner's mental simulation diverges from the actual
execution — which is frequent, because the planner is not an interpreter.

## 5. What to Remove (scar tissue)

The following prompt content is incident-specific and now redundant with the
M1-M6 machinery:

### 5a. Lines 170-171: Ban on `unittest discover`

```
Do NOT use `python -m unittest discover` (it requires explicit `-s <dir>` flags that
vary by project layout and is a known source of silent failures).
```

**Why remove:** This was a patch for a specific WO-01 failure where the
planner wrote a verify.sh containing `unittest discover` that couldn't find
tests in a `tests/` subdirectory.  The verify_contract + verify_exempt
machinery now handles this structurally: if verify.sh can't run because its
preconditions aren't met, the WO is flagged.  Banning one specific test
runner is overfitting — a different runner could have the same misconfiguration
problem, and the ban doesn't prevent it.

**Action:** Remove lines 170-171.  The structural machinery (verify_contract,
verify_exempt, R6) handles the general case.

### 5b. Lines 44-46: Overly specific `./scripts/verify.sh` example

```
Writes files as **plain text only** — no file-permission bits (e.g. chmod +x) are set.
  Therefore, acceptance commands must NEVER invoke scripts directly (e.g. `./scripts/verify.sh`).
  Always use an explicit interpreter: `bash scripts/verify.sh`, `python script.py`, etc.
```

**Why trim:** The constraint is correct — the factory writes plain text with no
permission bits.  But the three-line explanation with the specific
`./scripts/verify.sh` example is incident-driven padding.  This can be stated
in one line.

**Action:** Condense to:
`- Writes files as **plain text only** (no permission bits). Always invoke
scripts via an explicit interpreter (e.g. \`bash script.sh\`, \`python script.py\`),
never directly (\`./script.sh\`).`

### 5c. Lines 163-167: Redundant "WO-01 MUST NOT" bullets

Three of four bullets are now enforced by the validator:
- "Include `bash scripts/verify.sh` in acceptance" → enforced by R7/E105
- "Bundle project skeleton" → already in CORE OPTIMIZATION GOAL (minimal scope)

**Action:** Keep only the one bullet the validator cannot enforce: "Leave
verify.sh content up to interpretation via loose notes."  Remove the rest.

## 6. What to Add

### 6a. New section: ACCEPTANCE COMMAND TESTABILITY (replace current section)

Replace lines 173-192 (the current ACCEPTANCE COMMAND DESIGN PRINCIPLE) with
a new section that incorporates the testability hierarchy.  This is the core
change.

**Proposed content:**

```
────────────────────────────────
ACCEPTANCE COMMAND TESTABILITY (MANDATORY)
────────────────────────────────

Every work order MUST include at least one acceptance command that independently
verifies the work order's specific intent.

The factory runs `bash scripts/verify.sh` automatically as a global regression
gate.  Do NOT include it in `acceptance_commands`.

YOU DO NOT EXECUTE CODE.  You generate work orders in a single pass without
running any function you define.  Therefore, you cannot know the runtime output
of any non-trivial computation.  This constrains what you can reliably assert.

TIER 1 — Structure (always use):
  Test that modules, classes, and functions exist with the right shape.
  These assertions verify the contract you designed; they never require
  simulating execution.

    python -c "from mypackage.module import MyClass"
    python -c "from mypackage.module import MyClass; assert hasattr(MyClass, 'solve')"
    python -c "from mypackage.module import MyClass; assert callable(MyClass.solve)"
    python -c "import os; assert os.path.isfile('path/to/expected_file.py')"

TIER 2 — Contract properties (use when testing behavior):
  Test properties of the API contract — types, membership, determinism,
  structural constraints — WITHOUT asserting specific computed values.

    # Return type:
    python -c "from mod import f; assert isinstance(f(simple_input), expected_type)"

    # Membership (value is in a set you defined):
    python -c "from mod import WORDS, pick; assert pick() in WORDS"

    # Determinism (same input → same output):
    python -c "import random; from mod import f; assert f(random.Random(0)) == f(random.Random(0))"

    # Length / shape:
    python -c "from mod import parse; r = parse('input'); assert len(r) == 9"

    # Key presence:
    python -c "from mod import load; d = load(); assert 'name' in d and 'score' in d"

NEVER hardcode specific return values of non-trivial computations:

    # BAD — you cannot know the output of solve() without running it:
    python -c "from mod import solve; assert solve(puzzle) == 'specific_string'"

    # BAD — you cannot predict random.choice() without executing it:
    python -c "from mod import pick; assert pick(random.Random(0)) == 'pear'"

    # GOOD — same intent, testable without knowing the value:
    python -c "from mod import solve, is_valid; assert is_valid(solve(puzzle))"
    python -c "from mod import WORDS, pick; assert pick(random.Random(0)) in WORDS"

If you cannot express acceptance for a feature using tier 1-2 assertions,
the feature's acceptance criteria are not yet rigorous.  Split it into
smaller work orders or defer it.
```

### 6b. The oracle-problem statement in the preamble

Add a single sentence to the CORE OPTIMIZATION GOAL section, after line 32:

```
- **Testable without execution**
  - You do not run code.  Acceptance commands must test API contracts and
    structural properties, never specific computed values.
```

This plants the principle early, before the detailed rules.

## 7. Motivation: Why This Is General, Not Overfitting

Each proposed change is derived from a **structural fact about the planner's
execution model**, not from a specific failure:

| Change | Derived from | Specific failure it would have prevented |
|--------|-------------|------------------------------------------|
| Testability hierarchy | The planner does not execute code → it cannot produce reliable test oracles for runtime values | Word-game `'pear'`, sudoku solution string, any future hardcoded-output assertion |
| Oracle-problem statement | Same structural fact, stated as a design principle | Same class |
| Remove `unittest discover` ban | verify_contract + verify_exempt now handle the general case structurally | (Cleanup; the machinery already prevents the failure) |
| Condense `./scripts` example | The rule is correct; the verbosity was incident-driven | (Cleanup; no behavioral change) |
| Remove redundant WO-01 MUST NOT bullets | R7/E105 validator enforces these mechanically | (Cleanup; no behavioral change) |

The testability hierarchy generalizes because it's the planner equivalent of
a type system: it defines a space of assertions the planner can reliably
produce (those that don't require execution) and marks everything else as
unsafe.  This holds regardless of the spec domain — a sudoku solver, a word
game, a web app, or a compiler all have the same structure/execution
boundary.

## 8. Prompt Milestones

### M-11: Prompt Testability Hardening

**Goal:** Replace the current ACCEPTANCE COMMAND DESIGN PRINCIPLE section with
the testability hierarchy. Remove identified scar tissue. Add the oracle-
problem statement to the preamble.

**Files changed:**
- `planner/PLANNER_PROMPT.md`

**Changes:**

1. In CORE OPTIMIZATION GOAL (after line 32): add the "Testable without
   execution" bullet.

2. In EXECUTION CONSTRAINT (lines 44-46): condense the plain-text/permission
   explanation to one line.

3. In WO-01 BOOTSTRAPPING CONTRACT: remove the three redundant "MUST NOT"
   bullets (lines 164-166), keep "Leave verify.sh content up to
   interpretation via loose notes."  Remove the `unittest discover` ban
   (lines 170-171).

4. Replace ACCEPTANCE COMMAND DESIGN PRINCIPLE section (lines 173-192)
   entirely with the new ACCEPTANCE COMMAND TESTABILITY section from 6a above.

**Acceptance criteria:**
- `python -m pytest tests/ -q` passes (no code changes, only prompt).
- Prompt contains "YOU DO NOT EXECUTE CODE" and the tier 1/tier 2 examples.
- Prompt does NOT contain "unittest discover" or the redundant MUST NOT
  bullets.
- The example JSON in OUTPUT FORMAT is unchanged (it already uses tier-1
  assertions).

**Risk:** Low for the cleanup (removing scar tissue that the validator now
handles). Medium for the testability section (LLM behavior is stochastic).
Mitigation: the compile retry loop catches structural errors in the output;
the testability guidance reduces the surface area for semantic errors but
cannot eliminate them.

### M-12 (future): Acceptance Command Linting

A deterministic check in `validate_plan_v2` that **warns** when an acceptance
command contains a hardcoded string literal that looks like a computed value
(long hex strings, solution grids, multi-word strings not in the notes).
This is a heuristic, not a type check, so it should be a warning (W-code)
rather than a hard error.  Staged for later because it requires tuning the
heuristic against real planner output to avoid false positives.

### M-13 (future): W2 LLM Reviewer Pass

The second LLM pass that reviews acceptance commands against the notes and
flags semantic inconsistencies.  This is the non-deterministic complement to
the deterministic validator.  Staged for later because M-11 (prompt hardening)
should be tried first — it's cheaper and may be sufficient.  See Appendix B
below for the full design.

---

## Appendix A: Open Issue — Planner Writes Semantically Wrong Acceptance Commands

All prior structural contract issues (I1–I8) are resolved and verified in
the codebase: E003 shell-operator ban, E105 verify-in-acceptance ban,
verify_contract/verify_exempt machinery, except-OSError handler in
`factory/util.py`, context_files subset constraint removal, etc.

The remaining open class is I9:

### I9

**The problem:** The planner generates all work orders in a single LLM pass.
It defines APIs in early WOs (via `notes` fields) and writes acceptance
commands in later WOs that exercise those APIs.  The planner has no mechanism
to verify its acceptance commands are correct against the APIs it defined.

**Observed failures (historical, from maze_rl spec):**

- WO-04 acceptance calls `load_maze_text(BUILTIN_MAZES['SIMPLE'])` where
  `BUILTIN_MAZES['SIMPLE']` is a dict, but `load_maze_text` expects a string.
  The planner confused two different data representations it had itself
  defined.

- WO-05 acceptance uses `train_q_learning(m, episodes=5, seed=0)` with
  default keyword args, but the notes specify many more required parameters.
  The acceptance command doesn't match the function signature the planner
  itself prescribed.

**Observed failures (recent, from sudoku + word_game specs):**

- Sudoku WO-05: planner hardcoded the expected solution string for a puzzle.
  The executor wrote a solver that ran without errors but the output didn't
  match the fabricated string.  5/5 attempts failed identically.

- Word game WO-04: planner asserted `select_word(Random(0)) == 'pear'`.
  Actual output of `Random(0).choice(WORDS)` is `'orange'`.  The planner
  guessed the output of a nondeterministic function.  5/5 attempts failed.

**Why the executor cannot fix these:** The executor LLM can only change the
code it writes — it cannot change the acceptance command.  If the acceptance
command asserts a wrong value, the executor will produce correct code that
fails the wrong test.  Retrying is pointless because the bug is in the test,
not the code.

**Mitigation layers (in priority order):**

1. **M-11 (prompt testability hardening):** Tells the planner to avoid exact
   output assertions.  Reduces the surface area for oracle errors.  Cheap,
   deterministic.

2. **M-12 (acceptance command linting):** Deterministic heuristic that warns on
   hardcoded string literals in acceptance commands.  Catches obvious cases.

3. **M-13 (LLM reviewer pass):** A second LLM reviews acceptance commands
   against the notes for semantic consistency.  Catches subtle cases (wrong
   types, wrong signatures) that no deterministic check can find.

---

## Appendix B: W2 LLM Reviewer Pass — Full Design

This is the detailed design for M-13, preserved here for when implementation
begins.

### What the Reviewer Would Check

A second LLM pass reads ALL work orders as a batch and answers:

1. **Acceptance command / notes consistency:** For each work order, does the
   acceptance command use the APIs as described in the notes?  Do function
   signatures match?  Do argument types align with what earlier work orders
   produce?

2. **Cross-work-order API coherence:** If WO-03 defines `BUILTIN_MAZES` as
   a dict mapping names to text strings, and WO-04's acceptance command
   treats `BUILTIN_MAZES['SIMPLE']` as a parsed maze dict, flag the
   contradiction.

3. **Context_files completeness:** If a work order's notes say "use
   GridworldEnv from env.py" but `maze_rl/env.py` is not in `context_files`,
   flag it.  (Overlaps with R5/W101 deterministic checks but the LLM can
   catch subtler cases.)

### Where the Reviewer Fits in the Codebase

The reviewer runs AFTER the deterministic compile loop converges, but
BEFORE writing the final work orders to disk.

```
# In compile_plan(), after the retry loop produces validated work_orders:

if ENABLE_REVIEWER:
    review_errors = _run_reviewer(client, work_orders)
    if review_errors:
        # Feed review errors back to the planner for one final revision
        prompt = _build_review_revision_prompt(spec_text, work_orders, review_errors)
        raw_response = client.generate_text(prompt)
        parsed = _parse_json(raw_response)
        work_orders, errors = parse_and_validate(parsed)
        # ... (may need another deterministic loop here)
```

### Implementation Steps

1. **Create reviewer prompt template:**
   - New file: `planner/REVIEWER_PROMPT.md`.
   - The prompt takes the full list of work orders as JSON and asks the LLM to:
     - For each acceptance command, verify argument types and counts match the
       function signatures described in `notes` (both the current WO and
       earlier WOs).
     - Report each inconsistency as a structured error:
       `{"work_order_id": "WO-04", "error": "load_maze_text expects str, got dict"}`
   - Output format: JSON array of error objects, or empty array if no issues.

2. **Add `_run_reviewer()` to `planner/compiler.py`:**
   - Takes `client` and `work_orders` list.
   - Builds the reviewer prompt with all work orders serialized as JSON.
   - Calls `client.generate_text()`.
   - Parses the response as a JSON array of errors.
   - Returns the error list.

3. **Add review-revision loop to `compile_plan()`:**
   - After the compile loop converges, call `_run_reviewer()`.
   - If errors found, build a revision prompt that includes the original spec,
     the current work orders, and the reviewer's findings.
   - Send back to the planner LLM for one final revision attempt.
   - Re-run deterministic validation on the revised output.
   - Maximum 1-2 review cycles.

4. **Artifact persistence:**
   - Save `reviewer_prompt.txt`, `reviewer_response.json`,
     `reviewer_errors.json` in the compile artifacts directory.

5. **Make it optional:**
   - Add `--review / --no-review` CLI flag to `planner/cli.py`.
   - Default to enabled.  Users can skip it for speed during iteration.

### Cost / Risk Assessment

- **LLM cost:** One additional LLM call for the review, plus potentially one
  more for revision.  Total planner cost roughly doubles.
- **Latency:** 10-30 minutes for the reviewer call, plus 10-30 for revision
  if needed.  Total compile time could be 30-90 minutes worst case.
- **Risk:** Moderate.  The reviewer LLM may hallucinate errors (false
  positives) or miss real ones (false negatives).  Mitigation: the reviewer
  should output specific, verifiable claims ("WO-04 acceptance calls
  load_maze_text with a dict; WO-03 notes say it returns a string").  The
  planner can then decide whether to act on each claim.
- **Complexity:** Moderate.  Adds a new prompt template, a new LLM call, and
  a review loop to the compiler.  The reviewer is a separate concern — it
  doesn't generate work orders, it only critiques them.

### Open Questions

- **Should the reviewer use the same model as the planner, or a different
  one?** Using the same model risks the same blind spots.  A different model
  (e.g., a non-reasoning model for the review, since it's a checking task
  not a generation task) could provide genuine diversity but adds
  configuration complexity.

- **Can the reviewer be run in parallel with deterministic validation?**
  Yes — the reviewer doesn't depend on the deterministic loop.  But running
  them sequentially lets you skip the reviewer entirely when the deterministic
  loop catches errors (cheaper).

- **Should the reviewer check notes for internal consistency too?** E.g.,
  WO-05's notes say "use GridworldEnv" but allowed_files doesn't include the
  file where GridworldEnv lives.  This overlaps with R5/W101 but the LLM can
  catch subtler cases.

---

## Appendix C: Deferred Architectural Options

These options were identified during the original contract analysis.  They are
NOT urgent and may never be needed, but are preserved here for reference.

### Option C: Factory Owns verify.sh Content

**Idea:** Remove verify.sh from the planner's responsibility.  The factory
either injects a default verify.sh before the first run, or removes the
verify.sh convention entirely and uses the `_get_verify_commands` fallback
(already exists in `factory/nodes_po.py`: `compileall + pip + pytest`).

**Pros:** Eliminates the entire bootstrapping problem structurally.
Simplifies WO-01.  No risk of planner producing broken verify.sh content.

**Cons:** Removes flexibility for specs that need custom verification (type
checking, linting, etc.).  Changes the fundamental assumption that verify.sh
is part of the product under construction.

**When to consider:** If prompt hardening for WO-01 proves insufficient
across many different specs, despite the verify_contract/verify_exempt
machinery.

### Option D: Schema `kind` Field for Work Order Types

**Idea:** Add optional `kind: "scaffold" | "feature"` to the WorkOrder
schema.  Scaffold WOs get different validation rules and the PO node uses
fallback verification.

**Pros:** Makes WO-01's structural specialness explicit.  Validation rules
can be type-specific.  Extensible.

**Cons:** Introduces a type system that both planner and factory must
understand.  Only one known case of structural specialness (WO-01) — may be
premature.

**When to consider:** If more cases of structurally-special work orders emerge
beyond WO-01.  The precondition/postcondition + verify_exempt system has so
far handled WO-01's specialness without needing a `kind` field.

---

## Appendix D: The `notes` Field Problem

The `notes` field carries **two different kinds of information** with no
separation:

1. **Implementation guidance** — how to structure code, what function
   signatures to use, what data formats to expect.  This is appropriate for
   `notes`.

2. **Executable invariants** — things that *must be true* for the work order
   to succeed, but which are not enforced by any acceptance command or
   validator.  Example: "verify.sh must run `python -m unittest discover -v`"
   — operationally critical but invisible to the validation layer.

The factory's SE node injects notes directly into the executor LLM prompt
as free text with no structure, no validation, and no separation of concerns.
Notes pass through two LLM boundaries (planner → JSON → executor → code)
with no mechanical check on coherence.

**Current status:** The M1-M6 machinery moved some invariants OUT of notes
and INTO structured fields (preconditions, postconditions, verify_contract).
But notes still carry most of the implementation contract (function
signatures, data formats, behavioral requirements).  The notes field remains
the largest source of unstructured, unenforced semantics in the system.

**Future options:**
- Structured notes subsections (e.g., `api_contracts` as a separate field
  with typed entries for function signatures) — high complexity, unclear ROI.
- M-13 LLM reviewer pass can check notes-to-acceptance consistency without
  requiring notes to be structured.
- Prompt testability guidance (M-11) reduces the impact of notes errors by
  steering acceptance commands away from oracle-dependent assertions.
