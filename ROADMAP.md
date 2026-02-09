# Planner Prompt Hardening: Semantic Reliability Action Plan

**Date:** 2026-02-09
**Prerequisite:** M1-M6 from `ACTION_PLAN.md` (structural contract, all implemented).

---

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

## 8. Milestone Plan

### M7: Prompt Testability Hardening

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
Mitigation: the M5 compile retry loop catches structural errors in the
output; the testability guidance reduces the surface area for semantic errors
but cannot eliminate them.

### M8 (future, not in this plan): Acceptance Command Linting

A deterministic check in `validate_plan_v2` that **warns** when an acceptance
command contains a hardcoded string literal that looks like a computed value
(long hex strings, solution grids, multi-word strings not in the notes).
This is a heuristic, not a type check, so it should be a warning (W-code)
rather than a hard error.  Staged for later because it requires tuning the
heuristic against real planner output to avoid false positives.

### M9 (future, not in this plan): W2 LLM Reviewer Pass

The second LLM pass that reviews acceptance commands against the notes and
flags semantic inconsistencies.  This is the non-deterministic complement to
the deterministic validator.  Staged for later because M7 (prompt hardening)
should be tried first — it's cheaper and may be sufficient.  See Appendix B
below for the full design.

---

## Appendix A: Issue Registry (from TO_FIX.md)

Complete history of planner-factory contract issues.  All structural issues
(I1-I8) are resolved.  I9 (semantic acceptance errors) is the open class
that M7-M9 address.

### Fixed Issues

| # | Issue | Root cause | Fix | Status |
|---|-------|-----------|-----|--------|
| **I1** | `PermissionError`: `./scripts/verify.sh` invoked directly; no `+x` bit | Prompt said `./scripts/verify.sh`; factory runs `shell=False` | Prompt changed to `bash scripts/verify.sh`; execution constraint added | Fixed (pre-M1) |
| **I2** | `verify_failed`: `unittest discover` found zero tests | Planner wrote self-contradictory WO-01: notes said `unittest discover`, tests were under `tests/` | Prompt prescribes exact verify.sh content | Fixed (pre-M1); now also handled structurally by verify_contract/verify_exempt (M2-M6) |
| **I3** | WO-01 bootstrap circularity: `bash scripts/verify.sh` was both acceptance and the file being created | Validation required verify in every WO, including the one creating it | WO-01 bootstrap exemption in validation | Fixed (pre-M1); superseded by R7 ban + verify_exempt (M3-M6) |
| **I4** | Verify-only acceptance: WOs with no independent per-feature test | No prompt guidance about acceptance command quality | Prompt adds acceptance command design principle | Fixed (pre-M1); M7 replaces with testability hierarchy |
| **I5** | Shell pipes in acceptance commands | Prompt didn't explain `shell=False` constraint | Prompt adds no-shell rule; validation rejects shell operators (E003) | Fixed (pre-M1); covered by E003 in M1 |
| **I6** | Acceptance command contradicts notes (WO-08 omitted CLI flags) | Complex piped commands hid argument mismatches | Fixed by I5 fix | Fixed (pre-M1) |
| **I7** | `OSError` not caught in `run_command` | `run_command` only caught `TimeoutExpired` | Added `except OSError` handler in `factory/util.py` | Fixed (pre-M1) |
| **I8** | `context_files ⊆ allowed_files` was too restrictive | Executor couldn't see upstream modules | Subset constraint removed; context_files can include read-only deps | Fixed (pre-M1) |

### Open: I9 — Planner Writes Semantically Wrong Acceptance Commands

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

1. **M7 (prompt testability hardening):** Tells the planner to avoid exact
   output assertions.  Reduces the surface area for oracle errors.  Cheap,
   deterministic.

2. **M8 (acceptance command linting):** Deterministic heuristic that warns on
   hardcoded string literals in acceptance commands.  Catches obvious cases.

3. **M9 (LLM reviewer pass):** A second LLM reviews acceptance commands
   against the notes for semantic consistency.  Catches subtle cases (wrong
   types, wrong signatures) that no deterministic check can find.

---

## Appendix B: W2 LLM Reviewer Pass — Full Design (from TO_FIX.md)

This is the detailed design for M9, preserved here for when implementation
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

The reviewer runs AFTER the deterministic compile loop (M5) converges, but
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
   - After the M5 loop converges, call `_run_reviewer()`.
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

## Appendix C: Deferred Architectural Options (from TO_FIX.md)

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

## Appendix D: The `notes` Field Problem (from TO_FIX.md)

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
- M9 LLM reviewer pass can check notes-to-acceptance consistency without
  requiring notes to be structured.
- Prompt testability guidance (M7) reduces the impact of notes errors by
  steering acceptance commands away from oracle-dependent assertions.
