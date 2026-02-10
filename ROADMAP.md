# ROADMAP

**Last updated:** 2026-02-10

**Completed work:**

- **Structural contract (M1–M6):** E001–E006, E101–E106, verify_contract,
  verify_exempt — all implemented in `planner/validation.py` and
  `planner/compiler.py`.
- **Deterministic contract fixes (M-01–M-10):** All ten milestones from the
  adversarial audit (`FINDINGS_LEDGER.md`) are implemented and tested.
  Includes: verify_exempt sanitization (M-01), BaseException rollback (M-02),
  type guards (M-03), shlex error codes (M-04), atomic save_json (M-05),
  path normalization (M-06), NUL/control-char rejection (M-07), E105
  normalization (M-08), rollback_failed status (M-09), JSON size guards
  (M-10). Test suite: 432 passed.
- **Configuration extraction (M-14–M-19):** All six milestones of the
  Pattern A config centralization are implemented and tested. 50 constants
  centralized into `planner/defaults.py` (26) and `factory/defaults.py` (26).
  13 source files updated with import-only replacements; all original import
  paths preserved via re-exports. Generated `docs/CONFIG_DEFAULTS.md`
  reference from code. Config snapshot artifacts added to both
  `run_summary.json` and `compile_summary.json`. 40 hardening tests guard
  against value drift, shadowing, and doc staleness. Test suite: 472 passed.

Two tracks of work remain:

1. **Prompt semantic hardening** (Part 2) — prompt template changes to reduce
   LLM-generated acceptance command failures. These improve LLM output quality
   but do not affect deterministic guarantees.
2. **Artifact audit & light tidy** (Part 3) — naming/format review and
   optional observability improvements for CLI/cloud preparation.

Part 4 (Configuration extraction) is complete. The milestones, inventory
table, risk register, and open questions are preserved below as reference.

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

---
---

# Part 3: Artifact Audit & Light Tidy (Preparation for CLI / Cloud)

**Date:** 2026-02-10

This section documents the current artifact layout, evaluates naming and
format consistency, and proposes minimal tidy actions to prepare the system
for a future CLI and cloud/DB-backed logging — without redesigning anything.

---

## A) Current Artifact Inventory

### Planner artifacts

Directory: `{artifacts_dir}/{compile_hash}/compile/`

Per-run (written once per compile invocation):
- `prompt_rendered.txt` — the fully-rendered prompt sent to the LLM on the first attempt. Text. Produced by `compiler.py::compile_plan`.
- `compile_summary.json` — top-level compile outcome: hash, model config, attempt count, errors, warnings, timing. JSON. Produced by `compiler.py::_write_summary`.
- `manifest_normalized.json` — the final validated manifest (system_overview + verify_contract + work_orders with verify_exempt injected). JSON. Written only on success. Produced by `compiler.py::compile_plan`.
- `validation_errors.json` — final hard errors + warnings, written only on failure. JSON. Written to both `compile_artifacts/` and `outdir/`. Produced by `compiler.py::compile_plan`.

Per-attempt (one set per compile attempt, up to `MAX_COMPILE_ATTEMPTS=3`):
- `llm_raw_response_attempt_{N}.txt` — the raw LLM output text for attempt N. Text. Produced by `compiler.py::compile_plan`.
- `manifest_raw_attempt_{N}.json` — the parsed (but not validated) JSON from attempt N. JSON. Only written if JSON parsing succeeds. Produced by `compiler.py::compile_plan`.
- `validation_errors_attempt_{N}.json` — errors from attempt N (structural + chain). JSON. Produced by `compiler.py::compile_plan`.

Conditional (only on API-level failures):
- `raw_response_{label}.json` — full API response dump on error/incomplete/timeout. JSON. Written by `openai_client.py::_dump_response`. Labels include `no_text_attempt_{i}`, `incomplete_attempt_{i}`, `unexpected_status_{i}`, `poll_timeout`.

Output directory: `{outdir}/`

Written only on successful compile:
- `WO-{NN}.json` — one file per work order (e.g. `WO-01.json`). JSON. Written atomically by `io.py::write_work_orders`.
- `WORK_ORDERS_MANIFEST.json` — the full manifest (written last). JSON. Written atomically by `io.py::write_work_orders`.


### Factory artifacts

Run directory: `{out_dir}/{run_id}/`

Per-run:
- `work_order.json` — copy of the input work order for reproducibility. JSON. Written by `run.py::run_cli`.
- `run_summary.json` — final outcome: verdict, attempts, config, tree hash, rollback status. JSON. Written by `run.py::run_cli` (normal path) or the emergency handler (error path).

Attempt directory: `{out_dir}/{run_id}/attempt_{M}/`

Per-attempt (one set per factory attempt, up to `max_attempts`):
- `se_prompt.txt` — the fully-rendered SE prompt sent to the factory LLM. Text. Written by `nodes_se.py::se_node` (direct `open`).
- `proposed_writes.json` — the parsed WriteProposal from the SE LLM. JSON. Written by `nodes_se.py::se_node`. Only present if parsing succeeded.
- `raw_llm_response.json` — the raw SE LLM output, written only on parse failure. JSON. Written by `nodes_se.py::se_node`.
- `failure_brief.json` — structured failure info (stage, command, exit code, error excerpt). JSON. Written by SE (on precondition/LLM failure), TR (via `_tr_fail`), and finalize (authoritative overwrite).
- `write_result.json` — TR outcome: write_ok, touched_files, errors. JSON. Written by `nodes_tr.py::tr_node`.
- `verify_result.json` — list of CmdResult dicts for verify commands. JSON. Written by `nodes_po.py::po_node`.
- `acceptance_result.json` — list of CmdResult dicts for acceptance commands. JSON. Written by `nodes_po.py::po_node`.
- `verify_{K}_stdout.txt` / `verify_{K}_stderr.txt` — raw stdout/stderr for verify command K. Binary (written as bytes). Written by `util.py::run_command`.
- `acceptance_{K}_stdout.txt` / `acceptance_{K}_stderr.txt` — raw stdout/stderr for acceptance command K. Binary (written as bytes). Written by `util.py::run_command`.

---

## B) Naming and Clarity Review

### Planner

- `prompt_rendered.txt` — good as-is. Clearly the rendered prompt.
- `compile_summary.json` — good as-is. Unambiguous summary file.
- `manifest_normalized.json` — good as-is. Clearly the post-validation normalized manifest.
- `manifest_raw_attempt_{N}.json` — good as-is. "raw" clearly means pre-validation.
- `llm_raw_response_attempt_{N}.txt` — good as-is. Clearly the raw LLM text per attempt.
- `validation_errors_attempt_{N}.json` — good as-is. Per-attempt errors.
- `validation_errors.json` — **minor issue**: this name is identical to the per-attempt files but without the `_attempt_{N}` suffix, and it's written to *two* locations (compile artifacts dir AND outdir). The dual-write is intentional (outdir copy is for quick access), but a reader encountering both might be confused about which is authoritative. **Verdict:** acceptable — the outdir copy is a convenience duplicate and `compile_summary.json` links to the artifacts dir. Leave as-is.
- `raw_response_{label}.json` — **minor issue**: this is the only planner artifact not written via `_atomic_write` (uses bare `open`). The naming is fine; the non-atomic write is consistent with its best-effort diagnostic purpose. Leave as-is.
- `WO-{NN}.json` / `WORK_ORDERS_MANIFEST.json` — good as-is. Clear, conventional.

### Factory

- `work_order.json` — good as-is.
- `run_summary.json` — good as-is.
- `se_prompt.txt` — good as-is. "se" is the established node name.
- `proposed_writes.json` — good as-is.
- `raw_llm_response.json` — **note**: only written on parse failure. Name doesn't indicate conditionality, but this is standard practice (absence = success). Good as-is.
- `failure_brief.json` — good as-is. Name matches the `FailureBrief` schema.
- `write_result.json` — good as-is.
- `verify_result.json` / `acceptance_result.json` — good as-is. Consistent `{stage}_result.json` pattern.
- `verify_{K}_stdout.txt` / `verify_{K}_stderr.txt` — good as-is. Index K distinguishes multiple verify commands.
- `acceptance_{K}_stdout.txt` / `acceptance_{K}_stderr.txt` — good as-is.

### Cross-system consistency

- The planner uses `compile_summary.json`; the factory uses `run_summary.json`. The names differ because the operations differ (compile vs run). This is correct and should stay.
- The planner uses `_attempt_{N}` suffixes on filenames; the factory uses `attempt_{M}/` subdirectories. Both approaches are clean and appropriate for their cardinality (planner has 3 attempts in one dir; factory has 2+ attempts each with many files). Good as-is.
- The planner's rendered prompt is `prompt_rendered.txt`; the factory's is `se_prompt.txt`. Different names for the same concept, but justified by context (planner has one prompt; factory has stage-specific prompts). Good as-is.

**Overall naming verdict:** The naming is clear, consistent, and immediately understandable. No renames needed.

---

## C) Format Review

- All structured data is JSON. All human-readable output capture is TXT. This is correct.
- `se_prompt.txt` is written via bare `open(..., "w")` (not `save_json` or `_atomic_write`). This is the only factory artifact not written atomically. **Verdict:** low risk — the prompt is a diagnostic artifact, not a verdict. If it's truncated on crash, the run summary still captures the outcome. Leave as-is, but note for M-05 follow-up if consistency is desired later.
- `verify_{K}_stdout.txt` / `acceptance_{K}_stderr.txt` are written as binary (`"wb"`). This is correct — subprocess output is bytes. The files are plain text in practice but preserving raw bytes avoids encoding issues.
- `run_summary.json` contains embedded absolute paths (`stdout_path`, `stderr_path`, `proposal_path`, `repo_root`, `out_dir`). These are useful for local debugging but would be awkward for cloud ingestion or cross-machine comparison. **Verdict:** not a problem now. When cloud ingestion is added, a post-processor can strip or relativize paths. No format change needed today.
- `compile_summary.json` also contains absolute paths (`spec_path`, `template_path`, `artifacts_dir`, `outdir`). Same verdict.

**Overall format verdict:** Formats are appropriate. No changes needed.

---

## D) Iteration and Lifecycle Check

### Planner iteration

- Each compile attempt writes `llm_raw_response_attempt_{N}.txt`, `manifest_raw_attempt_{N}.json`, and `validation_errors_attempt_{N}.json`. The `{N}` suffix prevents overwriting.
- `compile_summary.json` contains the full `attempt_records` array with per-attempt error lists.
- A reader can reconstruct the full compile history from filenames alone: attempt 1 had errors (see `validation_errors_attempt_1.json`), attempt 2 was clean (see `validation_errors_attempt_2.json` with `[]`), compile succeeded.
- **Verdict:** clean. No issues.

### Factory iteration

- Each factory attempt writes all per-attempt artifacts into `attempt_{M}/`. The subdirectory prevents overwriting.
- `run_summary.json` contains the full `attempts` array with per-attempt records (proposal path, touched files, verify/acceptance results, failure brief).
- A reader can identify which attempt succeeded (the last one with `failure_brief: null` and `write_ok: true`), which failed (non-null `failure_brief`), and where iteration stopped (the total count vs max_attempts).
- **Verdict:** clean. No issues.

### Interrupt / crash lifecycle

- M-02 ensures `KeyboardInterrupt` still writes an emergency `run_summary.json` with `verdict: "ERROR"`. The run is never invisible.
- M-05 ensures `save_json` is atomic, so interrupted writes don't leave truncated JSON.
- M-09 adds `rollback_failed` to the summary, so post-mortem tooling can distinguish clean vs dirty repos.
- The planner writes `compile_summary.json` on every exit path (success, validation failure, parse failure).
- **Verdict:** lifecycle coverage is good after the M-01 through M-10 fixes.

---

## E) Missing Artifacts / Observability Gaps

### E.1 — No planner revision prompt artifact [OPTIONAL]

When the compile loop retries, `_build_revision_prompt` constructs a new prompt containing the errors and the previous response. This revised prompt is sent to the LLM but is never persisted. Only the initial `prompt_rendered.txt` is saved.

**Why it matters:** For debugging retry behavior, you currently have the errors (in `validation_errors_attempt_{N}.json`) and the LLM response (in `llm_raw_response_attempt_{N}.txt`), but not the actual prompt the LLM saw on attempts 2+. This makes it hard to audit whether the revision prompt was well-formed.

**Proposed fix:** In `compiler.py::compile_plan`, write `revision_prompt_attempt_{N}.txt` when `attempt > 1`, immediately after `_build_revision_prompt` returns. One line of code.

**Classification:** OPTIONAL — the error list + previous response are usually sufficient for debugging. The revision prompt can be mentally reconstructed.

### E.2 — No `verify_exempt` decision artifact [OPTIONAL]

`compute_verify_exempt` determines which work orders skip global verification. The computed flags end up in the emitted `WO-{NN}.json` files, but the decision rationale (which verify_contract requirements were satisfied by each WO's cumulative postconditions) is not persisted separately.

**Why it matters:** If a WO unexpectedly gets `verify_exempt: true` or `false`, there's no artifact showing the cumulative state at each step.

**Proposed fix:** None needed now. The M-01 fix ensures `verify_exempt` is always overwritten by the compiler, and the decision is deterministic from `verify_contract` + postconditions (both in the manifest). A future CLI could compute and display this on demand rather than persisting it.

**Classification:** OPTIONAL — deterministically reproducible from existing artifacts.

### E.3 — No factory attempt-level timing [OPTIONAL]

`run_summary.json` has total timing via individual command `duration_seconds`, but no per-attempt wall-clock start/end timestamps. If an attempt takes 5 minutes and you want to know where the time went (LLM call? verify? acceptance?), you can sum command durations but can't see LLM call latency (not captured).

**Why it matters:** Performance debugging. The SE LLM call is often the slowest step and its duration is invisible.

**Proposed fix:** In `_finalize_node` (graph.py), add `attempt_start_timestamp` and `attempt_end_timestamp` to the attempt record. Requires threading a start time through the state or recording it in `se_node`. Low complexity but touches state plumbing.

**Classification:** OPTIONAL — not needed for correctness or auditing, useful for performance work.

### E.4 — No environment snapshot [OPTIONAL]

Neither `compile_summary.json` nor `run_summary.json` captures Python version, platform, or key dependency versions. This makes cross-machine reproducibility harder.

**Why it matters:** A factory run that passes on macOS Python 3.12 might fail on Linux Python 3.11 due to stdlib differences. Without an environment snapshot, this is invisible.

**Proposed fix:** Add a small `environment` dict to both summaries: `python_version`, `platform`, `pydantic_version`. ~5 lines in `compiler.py` and `run.py`.

**Classification:** OPTIONAL — useful for cloud/multi-machine use, not needed for single-machine debugging.

### E.5 — `se_prompt.txt` not written atomically [COSMETIC]

This is the only factory artifact written via bare `open(..., "w")` instead of `save_json` or `_atomic_write`. In practice this doesn't matter (it's a diagnostic artifact, not a verdict), but it's an inconsistency.

**Classification:** COSMETIC — not worth fixing unless you're already touching `nodes_se.py`.

---

## F) Recommended Minimal Tidy Actions

1. **[OPTIONAL] Persist revision prompts on retry** — write `revision_prompt_attempt_{N}.txt` in `compiler.py` when `attempt > 1`. One `write_text_artifact` call. Improves retry debugging. (Ref: E.1)

2. **[OPTIONAL] Add environment snapshot to summaries** — add `python_version`, `platform` to `compile_summary.json` and `run_summary.json`. ~5 lines per file. Prepares for cross-machine and cloud use. (Ref: E.4)

3. **[COSMETIC] Make `se_prompt.txt` write atomic** — replace bare `open` in `nodes_se.py:226` with a call through `save_json`'s sibling or a text-atomic-write helper. Consistency only. (Ref: E.5)

4. **[NO ACTION] Naming** — all artifact names are clear and consistent. No renames needed. (Ref: Section B)

5. **[NO ACTION] Formats** — JSON for structured data, TXT for human-readable. Correct throughout. (Ref: Section C)

6. **[NO ACTION] Iteration lifecycle** — planner uses `_attempt_{N}` suffixes, factory uses `attempt_{M}/` subdirectories. Both are clean and non-overwriting. (Ref: Section D)

7. **[NO ACTION] Absolute paths in summaries** — they're useful for local debugging. Relativize later when cloud ingestion is built, not now. (Ref: Section C)

8. **[DEFERRED] Per-attempt timing** — useful for performance work but requires state plumbing. Not worth doing as a tidy action. (Ref: E.3)

9. **[DEFERRED] verify_exempt decision trace** — deterministically reproducible from existing artifacts. Not worth a new artifact. (Ref: E.2)

---
---

# Part 4: Configuration Extraction (Pattern A Centralization) ✅ COMPLETE

**Status:** All six milestones (M-14 through M-19) implemented and tested.
Test suite: 472 passed. Zero behavioral changes.

> Source of truth: `CONTROL_SURFACE.md` (forensic control-surface audit).
> Every parameter, location, and value cited below is traceable to a named
> section of that document. Section references use the notation `[CS §N.N]`.

This part defined a milestone-by-milestone refactor to extract all
configuration defaults and tunable parameters out of buried locations into
a "Pattern A" arrangement: centralized default-value modules (one per
subsystem), with generated CONFIG documentation derived from code.

## 1. Goals

- **Centralize all default values** into dedicated `defaults.py` modules
  (one per subsystem), so that every tunable parameter and documented
  constant is discoverable in a single file rather than scattered across
  implementation modules.
- **Improve discoverability** for future contributors: anyone reading a
  defaults module gets the full picture of what the subsystem's knobs are,
  their current values, categories, and whether they are safety invariants.
- **Prepare for a clean wrapper CLI (`llmc`)** by making it trivial to
  import defaults and wire them to argparse without hunting through
  implementation code.
- **Generate `docs/CONFIG_DEFAULTS.md`** from the defaults modules so that
  documentation is always derived from code and never stale.
- **Preserve exact runtime behavior**: every constant keeps its current
  numeric/string value, every import path that tests rely on continues to
  resolve, and every determinism invariant remains untouched.

## 2. Non-Goals

- **No behavior changes.** Defaults, semantics, and exit codes remain
  identical. This is a pure mechanical extraction.
- **No CLI redesign yet.** We are not adding, removing, or renaming CLI
  flags. The `llmc` wrapper will be a separate future milestone.
- **No runtime config file.** No TOML/YAML/JSON config loader is
  introduced. That is "Pattern C" and is deferred.
- **No broad renaming.** Constants keep their current Python names. If a
  name is `MAX_FILE_WRITE_BYTES` in `schemas.py` today, it will be
  `MAX_FILE_WRITE_BYTES` in `factory/defaults.py` tomorrow. The only
  change is the authoritative home.
- **No new abstraction layer.** We do not introduce a `Config` dataclass
  that threads through the call graph ("Pattern B"). Constants remain
  module-level symbols.
- **No cross-subsystem imports between planner and factory.** If both
  subsystems share a value (e.g., `MAX_JSON_PAYLOAD_BYTES`), it is
  duplicated in each defaults module with a comment noting the
  correspondence, not merged into a shared module — unless a shared module
  is explicitly justified below.
- **`utils/` is out of scope.** The files in `utils/` (`run_work_orders.sh`,
  `score_work_orders.py`) are legacy and will be deleted. They are excluded
  from this extraction entirely.

## 3. Proposed Pattern A Layout

### New files

| File | Purpose |
|---|---|
| `planner/defaults.py` | Authoritative home for all planner-side tunable parameters: LLM model config, transport/polling constants, compile-loop limits, path conventions. Safety invariants are also here but annotated `# SAFETY INVARIANT — do not expose`. |
| `factory/defaults.py` | Authoritative home for all factory-side tunable parameters: CLI argparse defaults, LLM defaults, size limits, timeout constants, git config, artifact filenames, path conventions. Safety invariants annotated. |
| `tools/dump_defaults.py` | Generator script: imports both `defaults.py` modules, introspects them, and writes `docs/CONFIG_DEFAULTS.md`. Runnable via `python tools/dump_defaults.py`. |
| `docs/CONFIG_DEFAULTS.md` | Generated (not hand-edited) reference of all configuration candidates with current values, categories, and safety annotations. A `.gitignore` comment or header warns that it is auto-generated. |

### Why no `common/defaults.py`

The only truly duplicated value across planner and factory is
`MAX_JSON_PAYLOAD_BYTES` (10 MB, appearing in both `factory/llm.py:50` and
`planner/compiler.py:70` [CS §3.5]). Introducing a shared package for one
constant creates an import dependency between subsystems that currently have
none. Instead, each subsystem declares its own copy with a cross-reference
comment. If future work surfaces more shared values, a `common/` package
can be added then.

### What belongs in each defaults module

**`planner/defaults.py`:**
- Model selection: `DEFAULT_MODEL`, `DEFAULT_REASONING_EFFORT`, `DEFAULT_MAX_OUTPUT_TOKENS`
- Transport: `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `WRITE_TIMEOUT`, `POOL_TIMEOUT`, `MAX_TRANSPORT_RETRIES`, `TRANSPORT_RETRY_BASE_S`
- Polling: `POLL_INTERVAL_S`, `POLL_DEADLINE_S`, `MAX_INCOMPLETE_RETRIES`
- API endpoint: `OPENAI_API_BASE`, `RESPONSES_ENDPOINT`
- Compile loop: `MAX_COMPILE_ATTEMPTS`
- Repo scan exclusions: `SKIP_DIRS`
- Compile hash truncation length
- Path conventions: default template path, required/optional placeholders
- JSON safety: `MAX_JSON_PAYLOAD_BYTES`
- Incomplete-retry token cap: `MAX_INCOMPLETE_TOKEN_CAP` (the hardcoded `65000`)

**`factory/defaults.py`:**
- CLI argparse defaults: `DEFAULT_MAX_ATTEMPTS`, `DEFAULT_LLM_TEMPERATURE`, `DEFAULT_TIMEOUT_SECONDS`
- LLM client: `DEFAULT_LLM_TIMEOUT` (the `120` in `llm.py:9,32`)
- Size limits (safety): `MAX_FILE_WRITE_BYTES`, `MAX_TOTAL_WRITE_BYTES`, `MAX_JSON_PAYLOAD_BYTES`, `MAX_CONTEXT_BYTES`, `MAX_CONTEXT_FILES`
- Truncation: `MAX_EXCERPT_CHARS`
- Git: `GIT_TIMEOUT_SECONDS`
- Hashing: `RUN_ID_HEX_LENGTH` (the `16` in `util.py:48`)
- Artifact filenames: all `ARTIFACT_*` constants
- Path conventions: verify script path, fallback verify commands, verify-exempt command, factory prompt template path
- Validation constants imported from `schemas.py`: `ALLOWED_STAGES`

## 4. Inventory Table: Config Candidates

Each row is a configuration candidate extracted from `CONTROL_SURFACE.md`.

> **Legend:** Det. = Determinism impact. Safety = Safety invariant.
> Proposed home is the `defaults.py` module where the authoritative
> definition will live.

| # | Canonical key name | Current location(s) | Subsystem | Current value | Category | Det. | Safety | Proposed home | Notes / gotchas |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `default_model` | `planner/openai_client.py:25` | planner | `"gpt-5.2-codex"` | model | Yes (compile hash) | No | `planner/defaults.py` | Feeds into compile hash [CS §4.1] |
| 2 | `default_reasoning_effort` | `planner/openai_client.py:26` | planner | `"medium"` | model | Yes (compile hash) | No | `planner/defaults.py` | Feeds into compile hash [CS §4.1] |
| 3 | `default_max_output_tokens` | `planner/openai_client.py:27` | planner | `64000` | model | No | No | `planner/defaults.py` | Doubled on incomplete retry [CS §3.2] |
| 4 | `max_incomplete_token_cap` | `planner/openai_client.py:100` (inline `65000`) | planner | `65000` | model | No | No | `planner/defaults.py` | Hardcoded in `min(budget*2, 65000)` — not a named constant today |
| 5 | `connect_timeout` | `planner/openai_client.py:32` | planner | `30.0` | timeout | No | No | `planner/defaults.py` | |
| 6 | `read_timeout` | `planner/openai_client.py:33` | planner | `60.0` | timeout | No | No | `planner/defaults.py` | |
| 7 | `write_timeout` | `planner/openai_client.py:34` | planner | `30.0` | timeout | No | No | `planner/defaults.py` | |
| 8 | `pool_timeout` | `planner/openai_client.py:35` | planner | `30.0` | timeout | No | No | `planner/defaults.py` | |
| 9 | `max_transport_retries` | `planner/openai_client.py:37` | planner | `3` | retries | No | No | `planner/defaults.py` | |
| 10 | `transport_retry_base_s` | `planner/openai_client.py:38` | planner | `3.0` | retries | No | No | `planner/defaults.py` | Linear (not exponential) backoff [CS §3.2] |
| 11 | `poll_interval_s` | `planner/openai_client.py:40` | planner | `5.0` | polling | No | No | `planner/defaults.py` | Tests monkeypatch this to `0.0` |
| 12 | `poll_deadline_s` | `planner/openai_client.py:41` | planner | `2400.0` | polling | No | No | `planner/defaults.py` | Tests monkeypatch this |
| 13 | `max_incomplete_retries` | `planner/openai_client.py:43` | planner | `1` | retries | No | No | `planner/defaults.py` | |
| 14 | `openai_api_base` | `planner/openai_client.py:19` | planner | `"https://api.openai.com/v1"` | paths | No | No | `planner/defaults.py` | `RESPONSES_ENDPOINT` is derived from this |
| 15 | `max_compile_attempts` | `planner/compiler.py:37` | planner | `3` | retries | No | No | `planner/defaults.py` | Tests import this from `compiler` |
| 16 | `planner_skip_dirs` | `planner/compiler.py:40-41` | planner | `{".git", "__pycache__", ...}` (9 entries) | paths | No | Yes | `planner/defaults.py` | Internal safety list [CS §8.3] |
| 17 | `planner_max_json_payload_bytes` | `planner/compiler.py:70` | planner | `10 * 1024 * 1024` | limits | No | Yes | `planner/defaults.py` | M-10 defense-in-depth [CS §3.5] |
| 18 | `compile_hash_hex_length` | `planner/compiler.py:63` | planner | `16` | hashing | Yes | No | `planner/defaults.py` | Changing breaks artifact dedup [CS §8.2] |
| 19 | `required_placeholder` | `planner/prompt_template.py:8` | planner | `"{{PRODUCT_SPEC}}"` | paths | No | No | `planner/defaults.py` | Template convention |
| 20 | `optional_placeholders` | `planner/prompt_template.py:9` | planner | `("{{DOCTRINE}}", "{{REPO_HINTS}}")` | paths | No | No | `planner/defaults.py` | |
| 21 | `default_max_attempts` | `factory/__main__.py:30` | factory | `2` | retries | No | No | `factory/defaults.py` | |
| 22 | `default_llm_temperature` | `factory/__main__.py:39` | factory | `0` | model | No | No | `factory/defaults.py` | Also in `factory/llm.py:32` sig default |
| 23 | `default_timeout_seconds` | `factory/__main__.py:45` | factory | `600` | timeout | No | No | `factory/defaults.py` | Conflated: LLM + subprocess [CS §2.1] |
| 24 | `default_llm_timeout` | `factory/llm.py:9,32` | factory | `120` | timeout | No | No | `factory/defaults.py` | Overridden at call site by CLI value |
| 25 | `max_file_write_bytes` | `factory/schemas.py:130` | factory | `200 * 1024` | limits | No | Yes | `factory/defaults.py` | Safety invariant [CS §8.1] |
| 26 | `max_total_write_bytes` | `factory/schemas.py:131` | factory | `500 * 1024` | limits | No | Yes | `factory/defaults.py` | Safety invariant [CS §8.1] |
| 27 | `factory_max_json_payload_bytes` | `factory/llm.py:50` | factory | `10 * 1024 * 1024` | limits | No | Yes | `factory/defaults.py` | M-10 [CS §3.5] |
| 28 | `max_context_bytes` | `factory/nodes_se.py:20` | factory | `200 * 1024` | limits | No | Yes | `factory/defaults.py` | Prompt budget [CS §8.1] |
| 29 | `max_context_files` | `factory/schemas.py:106` | factory | `10` | limits | No | Yes | `factory/defaults.py` | Schema constraint [CS §8.1] |
| 30 | `max_excerpt_chars` | `factory/util.py:55` | factory | `2000` | limits | No | No | `factory/defaults.py` | Display concern |
| 31 | `git_timeout_seconds` | `factory/workspace.py:11` | factory | `30` | timeout | No | Yes | `factory/defaults.py` | Internal safety bound [CS §8.1] |
| 32 | `run_id_hex_length` | `factory/util.py:48` (inline `[:16]`) | factory | `16` | hashing | Yes | No | `factory/defaults.py` | Changing breaks run_id reproducibility [CS §8.2] |
| 33 | `artifact_se_prompt` | `factory/util.py:217` | factory | `"se_prompt.txt"` | artifacts | No | No | `factory/defaults.py` | Tests assert exact values |
| 34 | `artifact_proposed_writes` | `factory/util.py:218` | factory | `"proposed_writes.json"` | artifacts | No | No | `factory/defaults.py` | |
| 35 | `artifact_raw_llm_response` | `factory/util.py:219` | factory | `"raw_llm_response.json"` | artifacts | No | No | `factory/defaults.py` | |
| 36 | `artifact_write_result` | `factory/util.py:220` | factory | `"write_result.json"` | artifacts | No | No | `factory/defaults.py` | |
| 37 | `artifact_verify_result` | `factory/util.py:221` | factory | `"verify_result.json"` | artifacts | No | No | `factory/defaults.py` | |
| 38 | `artifact_acceptance_result` | `factory/util.py:222` | factory | `"acceptance_result.json"` | artifacts | No | No | `factory/defaults.py` | |
| 39 | `artifact_failure_brief` | `factory/util.py:223` | factory | `"failure_brief.json"` | artifacts | No | No | `factory/defaults.py` | |
| 40 | `artifact_work_order` | `factory/util.py:224` | factory | `"work_order.json"` | artifacts | No | No | `factory/defaults.py` | |
| 41 | `artifact_run_summary` | `factory/util.py:225` | factory | `"run_summary.json"` | artifacts | No | No | `factory/defaults.py` | |
| 42 | `verify_script_path` | `factory/nodes_po.py:27`, `planner/validation.py:15` | factory + planner | `"scripts/verify.sh"` | paths | No | No | `factory/defaults.py` (authoritative); planner imports or duplicates with cross-ref | Appears in two subsystems |
| 43 | `verify_fallback_commands` | `factory/nodes_po.py:33-36` | factory | `[["python","-m","compileall","-q","."], ...]` (3 cmds) | paths | No | No | `factory/defaults.py` | |
| 44 | `verify_exempt_command` | `factory/nodes_po.py:83` | factory | `[["python","-m","compileall","-q","."]]` | paths | No | No | `factory/defaults.py` | |
| 45 | `verify_command_string` | `planner/validation.py:14` | planner | `"bash scripts/verify.sh"` | paths | No | No | `planner/defaults.py` | Validation rule string |
| 46 | `wo_id_pattern` | `planner/validation.py:16` | planner | `r"^WO-\d{2}$"` | paths | No | No | `planner/defaults.py` | Compiled regex — move the pattern string |
| 47 | `allowed_stages` | `factory/schemas.py:177-186` | factory | `frozenset({...})` (8 stages) | safety | No | Yes | `factory/defaults.py` | [CS §8.3] — keep non-overridable |
| 48 | `shell_operator_tokens` | `planner/validation.py:21` | planner | `frozenset({...})` (8 tokens) | safety | No | Yes | `planner/defaults.py` | [CS §8.3] — keep non-overridable |
| 49 | `factory_prompt_template_path` | `factory/nodes_se.py:65-67` | factory | (derived: same dir as module + `FACTORY_PROMPT.md`) | paths | No | No | `factory/defaults.py` | Relative to package; define the filename constant, not the absolute path |
| 50 | `planner_prompt_template_path` | `planner/prompt_template.py:41-42` | planner | (derived: same dir as module + `PLANNER_PROMPT.md`) | paths | No | No | `planner/defaults.py` | Same pattern as above |

---

## 5. Milestones

### M-14: Scaffolding — Add defaults modules with no call-site changes ✅ DONE

**Rationale:** Lay down the file structure and populate it with the
authoritative value of every constant, annotated with category and
safety/determinism tags. No production imports are changed, so this is
zero-risk and instantly revertible.

**Files created:**
- `planner/defaults.py`
- `factory/defaults.py`

**Mechanical steps:**
1. Create `planner/defaults.py`. Copy every constant from the inventory
   table (rows 1–20, 42 planner duplicate, 45–46, 48, 50) into it as
   module-level assignments. Group by category with section comments:
   `# --- Model ---`, `# --- Transport ---`, `# --- Polling ---`,
   `# --- Compile loop ---`, `# --- Paths ---`, `# --- Limits (safety) ---`,
   `# --- Hashing (determinism) ---`. Safety invariants get a
   `# SAFETY INVARIANT — do not expose via CLI` comment.
2. Create `factory/defaults.py`. Copy every constant from rows 21–44,
   47, 49. Same grouping conventions.
3. Each defaults module includes a module docstring stating:
   `"""Authoritative defaults for <subsystem>. Do not edit call sites yet."""`
4. Add a `# TODO(M-15–M-16): update call sites to import from here` at the
   top of each module.

**Files touched:** New files only. No existing file is modified.

**Tests to run:** Full test suite (`python -m pytest`). Must pass
unchanged — no imports or behaviors changed.

**Acceptance criteria:**
- Two new files exist with every inventory-table value present.
- `python -c "from planner.defaults import DEFAULT_MODEL"` works.
- `python -c "from factory.defaults import DEFAULT_MAX_ATTEMPTS"` works.
- Full test suite green with zero changes to existing files.

---

### M-15: Migrate planner defaults into `planner/defaults.py` ✅ DONE

**Rationale:** The planner has the densest cluster of buried constants
(13+ values in `openai_client.py` alone [CS §3.1, §3.2]). Moving these
first delivers the highest bang-for-buck because the planner module
structure is simpler (no LangGraph state threading).

**Files touched:**
- `planner/defaults.py` — remove `TODO` header; this is now the source.
- `planner/openai_client.py` — replace inline definitions of rows 1–14
  with `from planner.defaults import ...`. The module-level symbols
  (`DEFAULT_MODEL`, etc.) must remain available under the same names for
  backward compatibility. Achieve this via `from planner.defaults import
  DEFAULT_MODEL` (re-exports the same name at the same module path).
- `planner/compiler.py` — replace definition of `MAX_COMPILE_ATTEMPTS`
  (row 15), `_SKIP_DIRS` (row 16), `MAX_JSON_PAYLOAD_BYTES` (row 17),
  and the inline `[:16]` (row 18 — extract to a constant
  `COMPILE_HASH_HEX_LENGTH`). Import from `planner.defaults`.
- `planner/prompt_template.py` — replace `REQUIRED_PLACEHOLDER` (row 19)
  and `OPTIONAL_PLACEHOLDERS` (row 20) with imports from
  `planner/defaults.py`.
- `planner/validation.py` — replace `VERIFY_COMMAND` (row 45),
  `VERIFY_SCRIPT_PATH` (duplicate of row 42), `WO_ID_PATTERN` source
  string (row 46), and `SHELL_OPERATOR_TOKENS` (row 48) with imports.
  The compiled `re.compile(...)` stays in `validation.py`; only the
  pattern string constant moves. If the raw string and compiled regex
  share the same name, rename the raw string to `WO_ID_PATTERN_STR` in
  defaults and keep `WO_ID_PATTERN = re.compile(...)` in `validation.py`.

**Backward-compatibility shims:**
- In `planner/openai_client.py`, after the import, keep a comment:
  `# Backward compat: these names were historically defined here.`
  No alias needed if the import re-exports the same name.
- In `planner/compiler.py`, same pattern.

**Determinism safety:** `DEFAULT_MODEL` and `DEFAULT_REASONING_EFFORT`
feed into `_compute_compile_hash()` [CS §4.1]. The values are unchanged;
only the import path changes. Verify that `compile_hash` output is
identical for the same inputs — the existing
`tests/planner/test_compile_loop.py` already exercises this.

**Tests to run / update:**
- `tests/planner/test_openai_client.py` — imports `DEFAULT_MODEL`,
  `DEFAULT_REASONING_EFFORT`, `DEFAULT_MAX_OUTPUT_TOKENS`,
  `MAX_INCOMPLETE_RETRIES`, `MAX_TRANSPORT_RETRIES` from
  `planner.openai_client`. These will still resolve because
  `openai_client.py` re-imports from `planner.defaults`. No test change
  needed.
- `tests/planner/test_compile_loop.py` — imports `MAX_COMPILE_ATTEMPTS`
  from `planner.compiler`. Same re-import pattern; no test change needed.
- `tests/planner/test_structural_validation.py` — imports
  `SHELL_OPERATOR_TOKENS` from `planner.validation`. No change needed.
- Run full planner test suite: `python -m pytest tests/planner/`.

**Acceptance criteria:**
- `planner/openai_client.py` has zero inline constant definitions for
  any value in the inventory (all imported).
- `planner/compiler.py` has zero inline constant definitions for
  `MAX_COMPILE_ATTEMPTS`, `_SKIP_DIRS`, `MAX_JSON_PAYLOAD_BYTES`.
- `grep -r "= 3$" planner/compiler.py` returns no hits for the compile
  attempts constant.
- `python -m pytest tests/planner/` green.
- `python -c "from planner.defaults import DEFAULT_MODEL; print(DEFAULT_MODEL)"` prints `gpt-5.2-codex`.

---

### M-16: Migrate factory defaults into `factory/defaults.py` ✅ DONE

**Rationale:** The factory has more constants but they are spread across
more files (7 modules). This milestone is larger but still mechanical.

**Files touched:**
- `factory/defaults.py` — remove `TODO` header; this is now the source.
- `factory/__main__.py` — import `DEFAULT_MAX_ATTEMPTS`,
  `DEFAULT_LLM_TEMPERATURE`, `DEFAULT_TIMEOUT_SECONDS` from
  `factory.defaults` and use in argparse `default=` kwargs.
- `factory/llm.py` — import `DEFAULT_LLM_TIMEOUT` (row 24) and
  `MAX_JSON_PAYLOAD_BYTES` (row 27) from `factory.defaults`. Update
  function signatures to `timeout: int = DEFAULT_LLM_TIMEOUT`. Keep
  `MAX_JSON_PAYLOAD_BYTES` importable from `llm.py` via re-export.
- `factory/schemas.py` — import `MAX_FILE_WRITE_BYTES` (row 25),
  `MAX_TOTAL_WRITE_BYTES` (row 26), `MAX_CONTEXT_FILES` (row 29),
  `ALLOWED_STAGES` (row 47) from `factory.defaults`. The Pydantic
  validators reference these at class-body parse time, so the import
  must be module-level (no lazy import).
- `factory/nodes_se.py` — import `MAX_CONTEXT_BYTES` (row 28) and
  `FACTORY_PROMPT_FILENAME` (row 49, a new constant for just the filename
  `"FACTORY_PROMPT.md"`) from `factory.defaults`. The path derivation
  `os.path.join(os.path.dirname(...), FACTORY_PROMPT_FILENAME)` stays
  in `nodes_se.py`.
- `factory/nodes_po.py` — import `VERIFY_SCRIPT_PATH` (row 42),
  `VERIFY_FALLBACK_COMMANDS` (row 43), `VERIFY_EXEMPT_COMMAND` (row 44)
  from `factory.defaults`.
- `factory/util.py` — import `MAX_EXCERPT_CHARS` (row 30),
  `RUN_ID_HEX_LENGTH` (row 32), and all `ARTIFACT_*` constants
  (rows 33–41) from `factory.defaults`. The inline `[:16]` in
  `compute_run_id` becomes `[:RUN_ID_HEX_LENGTH]`.
- `factory/workspace.py` — import `GIT_TIMEOUT_SECONDS` (row 31) from
  `factory.defaults`.

**Backward-compatibility shims:**
- Every consuming module re-exports the imported name so that existing
  `from factory.util import ARTIFACT_SE_PROMPT` continues to work.
  This is critical: 6 test files import `ARTIFACT_*` constants from
  `factory.util`, not from `factory.defaults`.

**Determinism safety:** `RUN_ID_HEX_LENGTH` replaces a hardcoded `[:16]`
slice. The numeric value (`16`) is identical. Unit test coverage
(`tests/factory/test_util.py`) already asserts deterministic run_id
output. Verify the test still passes.

**Tests to run / update:**
- `tests/factory/test_util.py` — imports `MAX_EXCERPT_CHARS` and all
  `ARTIFACT_*` constants from `factory.util`. No change needed (re-exports).
  Also asserts exact artifact filename values (lines 226-234). These
  tests serve as the drift guard.
- `tests/factory/test_schemas.py` — imports `MAX_FILE_WRITE_BYTES`,
  `MAX_TOTAL_WRITE_BYTES`, `ALLOWED_STAGES` from `factory.schemas`.
  No change needed (re-exports).
- `tests/factory/test_workspace.py` — imports `GIT_TIMEOUT_SECONDS`
  from `factory.workspace` and asserts `== 30`. No change needed
  (re-export).
- `tests/factory/test_end_to_end.py`, `test_graph.py`, `test_nodes.py`
  — import `ARTIFACT_*` from `factory.util`. No change needed.
- Run full factory test suite: `python -m pytest tests/factory/`.

**Acceptance criteria:**
- Every constant in the factory inventory (rows 21–49) has its
  authoritative definition in `factory/defaults.py` and only there.
- `grep -rn "^MAX_FILE_WRITE_BYTES = " factory/` returns exactly one
  hit, in `factory/defaults.py`.
- All existing `from factory.util import ARTIFACT_*` statements in
  tests still resolve.
- `python -m pytest tests/factory/` green.

---

### M-17: Add defaults documentation generator ✅ DONE

**Rationale:** The generated `docs/CONFIG_DEFAULTS.md` ensures that
documentation stays in sync with code. It also serves as the reference
for future `llmc` CLI flag wiring.

**Files created:**
- `tools/dump_defaults.py`
- `docs/CONFIG_DEFAULTS.md` (generated output)

**Mechanical steps:**
1. `tools/dump_defaults.py` imports `planner.defaults` and
   `factory.defaults`.
2. For each module, it iterates module-level names (excluding `_`-prefixed
   and imports). For each name, it captures:
   - Name
   - Value (repr, truncated to 120 chars for collections)
   - Type
   - Docstring annotation (if we adopt a convention — see below)
3. Output is a Markdown file with a header warning `<!-- AUTO-GENERATED
   by tools/dump_defaults.py — do not hand-edit -->`, one table per
   subsystem, columns: Name, Value, Type, Category, Safety, Notes.
4. Category and safety annotations can be encoded as trailing comments
   in each defaults module using a convention:
   `DEFAULT_MODEL = "gpt-5.2-codex"  # category:model det:yes`
   The generator parses these structured comments.
5. Optionally add a `Makefile` target or script alias:
   `python tools/dump_defaults.py > docs/CONFIG_DEFAULTS.md`.

**Files touched:**
- `planner/defaults.py`, `factory/defaults.py` —
  add structured trailing comments to each constant if not already
  present from M-14.

**Tests to run:**
- Run the generator and diff the output against expected structure.
- This milestone should include one test:
  `tests/test_dump_defaults.py` — verify the generator runs without
  error and the output contains entries for both subsystems.

**Acceptance criteria:**
- `python tools/dump_defaults.py` produces valid Markdown to stdout.
- `docs/CONFIG_DEFAULTS.md` exists and contains rows for all 50
  inventory items.
- The generated document includes the auto-generated warning header.

---

### M-18: Add resolved config snapshot artifacts ✅ DONE

**Rationale:** For post-mortem debugging and reproducibility, each run
should record the effective configuration — defaults plus any CLI
overrides. This milestone adds a `config_snapshot` field or artifact
WITHOUT changing any behavior or control flow.

**Placement justification:** This comes after M-15–M-16 because the snapshot
should import from `defaults.py` modules to capture the full set of
defaults, not repeat hardcoded values.

**Files touched:**
- `factory/run.py` — the existing `run_config` dict (line 71-78) already
  captures CLI-provided values. Extend it with a `defaults` sub-dict
  that records every value from `factory.defaults` that is relevant to
  the run (model defaults, timeouts, size limits, etc.). Write this as
  part of the existing `run_summary.json`. No new artifact file.
- `planner/compiler.py` — the existing `_write_summary()` already
  records `model`, `reasoning_effort`, `max_output_tokens` (lines
  389-391). Extend the summary with a `defaults_snapshot` key that
  includes all `planner.defaults` values (transport, polling, compile
  loop). No new artifact file.

**Behavioral change:** None. The `run_summary.json` and
`compile_summary.json` gain additional keys. Consumers that read these
files will see more data but no existing keys change.

**Tests to run / update:**
- `tests/factory/test_end_to_end.py` — verify `run_summary.json` now
  contains a `config.defaults` key (add a lightweight assertion).
- `tests/planner/test_compile_loop.py` — verify `compile_summary.json`
  now contains `defaults_snapshot` (add a lightweight assertion).
- Run full test suite.

**Acceptance criteria:**
- `run_summary.json` includes a `config.defaults` key with at least
  `max_file_write_bytes`, `git_timeout_seconds`, `max_context_bytes`.
- `compile_summary.json` includes a `defaults_snapshot` key with at
  least `poll_deadline_s`, `max_transport_retries`, `max_compile_attempts`.
- All existing tests green.

---

### M-19: Hardening — assert defaults in one place, prevent drift ✅ DONE

**Rationale:** After extraction, the risk is that someone adds a new
constant to an implementation module instead of `defaults.py`, or
changes a value in `defaults.py` without realizing it's load-bearing.
This milestone adds guard tests.

**Files created / touched:**
- `tests/test_defaults_canonical.py` — new test file. Contains:
  1. **Value-pinning tests:** For every safety invariant and
     determinism-sensitive value, assert the exact numeric/string value.
     This prevents accidental changes. Examples:
     ```
     assert factory_defaults.MAX_FILE_WRITE_BYTES == 200 * 1024
     assert factory_defaults.RUN_ID_HEX_LENGTH == 16
     assert planner_defaults.DEFAULT_MODEL == "gpt-5.2-codex"
     assert planner_defaults.COMPILE_HASH_HEX_LENGTH == 16
     ```
  2. **No-shadow tests:** For each defaults module, assert that no
     constant defined in `defaults.py` is also defined (shadowed) in
     the consuming module. This uses `inspect.getsource()` to verify
     the consuming module does not contain `<NAME> = ` (regex match
     that excludes import statements and re-exports).
  3. **Completeness tests:** For each defaults module, assert the
     count of public constants matches the inventory table count.
     This prevents silent additions without updating the inventory.

**Existing test adjustments:**
- `tests/factory/test_workspace.py:145` — the test
  `assert GIT_TIMEOUT_SECONDS == 30` is a value-pinning test. After M-19,
  this pinning lives in `test_defaults_canonical.py`. The old assertion
  can be removed from `test_workspace.py` or left as redundant (prefer
  leaving it — no harm, extra safety).
- `tests/factory/test_util.py:226-234` — the exact artifact filename
  assertions are value-pinning tests. Same treatment: keep or reference.

**Tests to run:** `python -m pytest tests/test_defaults_canonical.py`
plus full suite.

**Acceptance criteria:**
- `test_defaults_canonical.py` contains at least one pinning assertion
  for every row in the inventory table marked `Safety=Yes` or `Det.=Yes`.
- Any attempt to change `RUN_ID_HEX_LENGTH` to `32` (for example)
  causes a test failure in exactly one place.
- Full test suite green.

---

## 6. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Import cycle between planner and factory.** The planner's `validation.py` already imports `factory.schemas.WorkOrder`. If `factory/defaults.py` ever imports from `planner/`, a cycle forms. | Low | High | Constraint: `factory/defaults.py` and `planner/defaults.py` MUST NOT import from each other. The `VERIFY_SCRIPT_PATH` duplication (row 42) is intentional — each subsystem owns its copy. |
| R2 | **Tests break because they import constants from implementation modules, not defaults.** E.g., `from planner.openai_client import DEFAULT_MODEL`. | Medium | Medium | M-15/M-16 re-export all constants from the original module. Test imports continue to resolve. We do not force tests to change their import paths. |
| R3 | **Accidental value change during copy.** A typo in `defaults.py` (e.g., `64000` → `6400`) silently changes behavior. | Medium | High | M-19 adds value-pinning tests. During M-14 review, require side-by-side diff of every constant against the inventory table. |
| R4 | **Determinism breakage if `RUN_ID_HEX_LENGTH` or `COMPILE_HASH_HEX_LENGTH` is changed.** Artifact directories would not match prior runs. | Low (only on accidental change) | Critical | M-19 pins both values. `CONTROL_SURFACE.md` §8.2 already flags these. The defaults module annotates them `# DETERMINISM — changing this breaks artifact reproducibility`. |
| R5 | **Pydantic validators reference constants at class-body parse time.** If `factory/defaults.py` has an import error, `factory/schemas.py` becomes unimportable. | Low | High | M-16 testing includes `python -c "from factory.schemas import WorkOrder"` as a smoke test. `factory/defaults.py` must have zero third-party dependencies. |
| R6 | **Generated `docs/CONFIG_DEFAULTS.md` drifts if generator is not re-run.** | Medium | Low | M-17 includes a CI check: `python tools/dump_defaults.py | diff - docs/CONFIG_DEFAULTS.md`. If the diff is non-empty, CI fails. (Or equivalently, M-19 adds a test that runs the generator and asserts no diff.) |
| R7 | **Monkeypatching in tests.** Tests like `test_openai_client.py` monkeypatch `POLL_INTERVAL_S` directly on the `openai_client` module object. After M-15, the value lives in `defaults.py` but the module-level name in `openai_client.py` is a re-imported reference. Monkeypatching `oai_mod.POLL_INTERVAL_S = 0.0` patches the module's local binding, which still works. | Low | Medium | Verify by running tests. Python's `monkeypatch.setattr(module, "NAME", val)` patches the module's namespace, not the source. As long as `openai_client.py` reads the name from its own module namespace (not via `defaults.POLL_INTERVAL_S` at call time), monkeypatching works. Ensure each consuming module accesses the constant via its own module-level name, not via `defaults.CONSTANT`. |

---

## 7. Open Questions (Deferred)

### Deferred: required-vs-default boundary

The `CONTROL_SURFACE.md` §7 proposes which parameters should become CLI
flags. This planning document deliberately does not resolve that boundary.
After Pattern A extraction is complete, a follow-up decision document should:
- For each row in the inventory table, decide: stays internal, becomes a
  CLI default (overridable), or stays hardcoded but documented.
- Decide if `--timeout-seconds` should be split into `--llm-timeout` and
  `--cmd-timeout` [CS §2.1, §7.2].

### Deferred: Pattern C runtime config file

A future milestone could introduce a TOML/YAML config file
(`llmc.toml` or `.llmc/config.toml`) that:
- Overrides defaults from `defaults.py` modules.
- Is loaded at startup before CLI parsing.
- CLI flags override config file values (CLI > config > defaults).

This is explicitly out of scope for Pattern A. The defaults modules are
designed so that Pattern C can be layered on later: the config file loader
would `import planner.defaults` and selectively override values.

### Deferred: `llmc` unified CLI wrapper

The `CONTROL_SURFACE.md` §7 sketches `llmc plan`, `llmc run`,
`llmc batch`. After Pattern A + the required-vs-default decision,
implementing `llmc` becomes a straightforward wiring exercise: import
defaults, add argparse flags, pass to existing entry points.

### Deferred: `OPENAI_API_BASE` / `OPENAI_ORG_ID` environment variable support

`CONTROL_SURFACE.md` §3.8 and §3.9 note that only `OPENAI_API_KEY` is
read. Supporting additional env vars for API base, org ID, etc. is a
future feature, not part of this extraction.
