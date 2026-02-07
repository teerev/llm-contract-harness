# TO_FIX: Planner–Factory Contract Hardening

**Date:** 2026-02-07
**Branch:** `wo_compile`
**Status:** Partially complete. Structural fixes done; semantic gap remains open.

This document describes the current state of work to enforce consistency between
the **planner** (LLM that generates work order JSON) and the **factory** (automated
executor that implements work orders via the SE → TR → PO graph loop). It is written
so that another engineer or LLM can resume the work.

See also: `PLANNER_FACTORY_CONTRACT.md` in the repo root for the original design
analysis that motivated these changes.

---

## System Architecture (brief)

Two LLM-driven stages operate in sequence:

1. **Planner** (`planner/` directory):
   - Takes a product spec (`spec.txt`) and a prompt template (`planner/PLANNER_PROMPT.md`)
   - Calls an LLM via `planner/openai_client.py` to generate a JSON manifest of work orders
   - Validates output via `planner/validation.py` (ID contiguity, verify command presence, schema, shell operators)
   - Writes individual `WO-NN.json` files to an output directory

2. **Factory** (`factory/` directory):
   - Executes one work order at a time via a LangGraph state machine (`factory/graph.py`)
   - **SE node** (`factory/nodes_se.py`): reads `context_files` from the target repo, builds a prompt, calls an executor LLM, parses a `WriteProposal`
   - **TR node** (`factory/nodes_tr.py`): validates scope (files in `allowed_files`), checks base-hashes, atomically writes files
   - **PO node** (`factory/nodes_po.py`): runs global verification (`bash scripts/verify.sh`), then runs acceptance commands from the work order
   - On failure, rolls back to baseline commit and retries (up to `max_attempts`)

The **contract surface** between planner and factory is the `WorkOrder` pydantic schema
(`factory/schemas.py` lines 38–71):

```
WorkOrder:
  id: str
  title: str
  intent: str
  allowed_files: list[str]
  forbidden: list[str]
  acceptance_commands: list[str]
  context_files: list[str]        # may include read-only upstream deps (max 10)
  notes: Optional[str]
```

---

## Issues Discovered and Their Status

### FIXED Issues

| # | Issue | Root cause | Fix applied | Files changed |
|---|-------|-----------|-------------|---------------|
| **I1** | `PermissionError` crash: acceptance command `./scripts/verify.sh` invoked directly; file has no `+x` bit because factory writes plain text | Prompt said `./scripts/verify.sh`; factory runs `shell=False` which needs execute bit for direct invocation | Prompt changed to `bash scripts/verify.sh`; execution constraint section explains plain-text writes | `planner/PLANNER_PROMPT.md` lines 44–46, `planner/validation.py` line 11 |
| **I2** | `verify_failed`: `python -m unittest discover -v` found zero tests (tests in `tests/` subdir, no `-s` flag) | Planner wrote self-contradictory WO-01: notes said `unittest discover`, but test file was under `tests/` | Prompt now prescribes exact verify.sh content; bans `unittest discover` | `planner/PLANNER_PROMPT.md` lines 126–152 |
| **I3** | WO-01 bootstrapping circularity: `bash scripts/verify.sh` was both the acceptance command and the file being created | Validation required verify command in every WO, including the one creating it | Prompt adds WO-01 bootstrapping contract; validation exempts WO-01 when `scripts/verify.sh` is in `allowed_files` | `planner/PLANNER_PROMPT.md` lines 126–152, `planner/validation.py` lines 86–95 |
| **I4** | Verify-only acceptance: work orders with no independent per-feature test | No prompt guidance about acceptance command quality | Prompt adds "Acceptance Command Design Principle" section requiring independent acceptance | `planner/PLANNER_PROMPT.md` lines 154–173 |
| **I5** | Shell pipes in acceptance commands: `cmd1 \| cmd2` treated as literal args by `shell=False` | Prompt didn't explain `shell=False` constraint | Prompt adds no-shell-features rule; validation rejects bare shell operator tokens via `shlex.split` | `planner/PLANNER_PROMPT.md` lines 47–50, `planner/validation.py` lines 15–18 and 97–112 |
| **I6** | Acceptance command contradicts notes: WO-08 omitted required CLI flags | Consequence of I5 — complex piped commands hid argument mismatches | Fixed by I5 fix — simpler `python -c` commands are easier for the planner to get right | (same as I5) |
| **I7** | `OSError` not caught in `run_command`: `PermissionError`/`FileNotFoundError` crashed entire run instead of returning failed `CmdResult` | `run_command` only caught `TimeoutExpired`, not `OSError` | Added `except OSError` handler that returns `CmdResult` with `exit_code=-1` | `factory/util.py` lines 145–163 |

### OPEN Issues

#### I8: `context_files ⊆ allowed_files` was too restrictive — FIXED (S1)

**Status: FIXED.** The subset constraint was removed from `factory/schemas.py`.
`context_files` may now include read-only upstream dependencies outside `allowed_files`.

**What was changed:**
- `factory/schemas.py`: Removed the subset enforcement from `_check_context_constraints`
  (the max-10 limit is preserved)
- `planner/PLANNER_PROMPT.md`: `context_files` description updated to say it must include
  all `allowed_files` and may also include read-only upstream dependencies
- `tests/test_schemas.py`: `test_context_files_subset_enforced` replaced with
  `test_context_files_not_restricted_to_allowed`

**Original failures this addresses:**
- WO-05: executor couldn't see `maze_rl/env.py`, guessed `env.height` → `AttributeError`
- WO-04: planner couldn't provide upstream module context to executor

#### I9: Planner writes semantically wrong acceptance commands

**The problem:**

The planner generates all work orders in a single LLM pass. It defines APIs in early
work orders (via `notes` fields) and writes acceptance commands in later work orders
that exercise those APIs. But the planner has no mechanism to verify its acceptance
commands are correct against the APIs it defined.

Observed failures:

- WO-04 acceptance calls `load_maze_text(BUILTIN_MAZES['SIMPLE'])` where `BUILTIN_MAZES['SIMPLE']`
  is a dict, but `load_maze_text` expects a string. The planner confused two different
  data representations it had itself defined.

- WO-05 acceptance uses `train_q_learning(m, episodes=5, seed=0)` with default keyword
  args, but the notes specify many more required parameters. The acceptance command doesn't
  match the function signature the planner itself prescribed.

**Why this is hard to fix:**

This is a *semantic* error, not a *structural* one. The planner doesn't run code — it
generates JSON in a single forward pass. It can't test its acceptance commands against
the APIs from earlier work orders because those APIs don't exist yet when it's planning.

**Possible approaches:**

1. **Prompt hardening** — Tell the planner to keep acceptance commands simple: test imports,
   basic construction, and attribute existence only. Defer complex integration testing to
   later work orders that can test the assembled system. This reduces the surface area for
   semantic errors but limits acceptance command power.

2. **Two-phase planning** — After the first LLM pass generates work orders, run a second
   LLM pass that reviews each work order's acceptance commands against the accumulated API
   context from all preceding work orders. This adds latency and cost but provides a
   semantic consistency check.

3. **Accept and compensate** — Increase `max_attempts` and rely on the executor's retry
   loop. When an acceptance command fails, the failure brief is fed back to the executor
   LLM. However, this DOES NOT HELP when the acceptance command itself is wrong (the
   executor can't change the acceptance command — only its own code).

4. **Fix S1 first** — Many of the semantic errors are compounded by the executor not being
   able to see upstream modules. If the executor could read `maze_rl/env.py` while writing
   `maze_rl/qlearn.py`, it would at least write code that matches the actual API, even if
   the acceptance command is wrong. This doesn't fix wrong acceptance commands but reduces
   the blast radius.

**Recommendation:** Fix S1 first (it's clean, low-risk, and directly caused failures).
Then add prompt guidance for simpler acceptance commands. Two-phase planning is the
most robust solution but is a significant architectural addition.

---

## Possible Larger Structural Changes

These are architectural options identified during analysis that are NOT yet implemented
and are NOT urgent, but may become relevant as the system matures.

### Option C: Factory Owns verify.sh Content

**Idea:** Remove verify.sh from the planner's responsibility. The factory either:
- Injects a default verify.sh before the first run, or
- Removes the verify.sh convention entirely and uses `_get_verify_commands` fallback
  (already exists in `factory/nodes_po.py` lines 33–37: `compileall + pip + pytest`)

**Pros:**
- Eliminates the entire bootstrapping problem (I2, I3) structurally
- Simplifies WO-01 — it becomes a pure project skeleton step
- No risk of planner producing broken verify.sh content

**Cons:**
- Removes flexibility for specs that need custom verification (type checking, linting, etc.)
- Changes the fundamental assumption that `verify.sh` is part of the product under construction
- Existing example work orders (`examples/work_orders/WO-01.json`) would need updating

**Files affected:** `factory/nodes_po.py`, `factory/run.py`, `planner/PLANNER_PROMPT.md`, `planner/validation.py`

**When to consider:** If prompt hardening for WO-01 proves insufficient across many different specs.

### Option D: Schema `kind` Field for Work Order Types

**Idea:** Add optional `kind: "scaffold" | "feature"` to the `WorkOrder` schema. Scaffold
work orders (WO-01) get different validation rules: no verify command requirement, stricter
notes validation. The PO node uses fallback verification for scaffold work orders.

**Pros:**
- Makes WO-01's structural specialness explicit rather than implicit
- Validation rules can be type-specific
- Extensible if more work order types emerge

**Cons:**
- Introduces a type system that both the planner LLM and factory must understand
- Only one known case of structural specialness (WO-01) — may be premature

**Files affected:** `factory/schemas.py`, `factory/nodes_po.py`, `planner/PLANNER_PROMPT.md`, `planner/validation.py`

**When to consider:** If more cases of structurally-special work orders emerge beyond WO-01.

---

## Workplan W1: Deterministic Feedback Loop (compile → validate → revise)

### Motivation

The planner currently generates all work orders in a single LLM pass. If the output
has validation errors (shell operators, syntax errors, structural problems), the compile
fails and the user must manually re-run. There is no mechanism for the planner LLM to
see its own mistakes and self-correct.

A deterministic feedback loop would: generate → validate → if errors, feed them back
to the LLM → regenerate → validate → ... until convergence or a retry limit.

### What can be checked deterministically (no LLM needed)

These checks run on raw JSON and acceptance command strings — no code execution required:

1. **Everything `validation.py` already checks:**
   - ID contiguity (`WO-01`, `WO-02`, ... without gaps)
   - Verify command presence (with WO-01 bootstrap exemption)
   - Shell operator tokens in acceptance commands
   - Schema conformance via pydantic `WorkOrder(**wo)`
   - Glob characters in paths

2. **NEW — Python syntax validation for `python -c` commands:**
   - Extract the Python code from every acceptance command that starts with `python -c`
   - Run `ast.parse(code)` on it
   - This would have caught the WO-06 `SyntaxError` (the `with` statement on a single line)
   - Implementation: add a new check in `validation.py` after the shell operator check

3. **NEW — Cross-work-order structural checks:**
   - Every file in `allowed_files` for WO-N should appear in `context_files` for WO-N
     (the executor needs to see what it's editing)
   - Files referenced in acceptance commands via imports (e.g., `from maze_rl.env import ...`)
     should exist in `allowed_files` of some earlier work order (the module must have been
     created by a prior step)

### Where the loop fits in the codebase

The integration point is `planner/compiler.py`, specifically `compile_plan()`.

Current flow (lines 134–184):

```
prompt = render_prompt(template_text, spec_text)     # line 129
raw_response = client.generate_text(prompt)          # line 137
parsed = _parse_json(raw_response)                   # line 144
work_orders, errors = parse_and_validate(parsed)     # line 159
if errors: return result (failure)                   # line 174
```

Proposed flow with feedback loop:

```
prompt = render_prompt(template_text, spec_text)
for attempt in range(MAX_COMPILE_RETRIES):
    raw_response = client.generate_text(prompt)
    parsed = _parse_json(raw_response)
    work_orders, errors = parse_and_validate(parsed)   # includes new checks
    if not errors:
        break
    # Build a revision prompt with the errors
    prompt = _build_revision_prompt(template_text, spec_text, raw_response, errors)
# proceed with work_orders (or fail if still errors after retries)
```

### Implementation steps

1. **Add `ast.parse` check to `planner/validation.py`:**
   - New function: for each acceptance command, if it matches `python -c "..."`,
     extract the Python code and call `ast.parse()`. Report `SyntaxError` as a
     validation error.
   - Wire it into `validate_plan()` in the per-work-order loop.

2. **Add `_build_revision_prompt()` to `planner/compiler.py`:**
   - Takes the original spec, the LLM's previous JSON output, and the error list.
   - Produces a prompt like: "You previously generated these work orders: [JSON].
     They failed validation with these errors: [errors]. Please fix the errors and
     output the corrected JSON. Preserve work orders that had no errors."

3. **Add retry loop to `compile_plan()` in `planner/compiler.py`:**
   - Wrap lines 134–184 in a `for attempt in range(MAX_COMPILE_RETRIES)` loop.
   - On each iteration, if `errors` is non-empty, build a revision prompt and call
     `client.generate_text()` again.
   - Persist each attempt's raw response and errors as separate artifacts
     (e.g., `llm_raw_response_attempt_1.txt`, `validation_errors_attempt_1.json`).
   - `MAX_COMPILE_RETRIES` should be small (2–3). The first attempt usually works;
     the revision is just for catching mechanical errors.

4. **Update `compile_summary.json`:**
   - Add `compile_attempts` count and per-attempt error lists to the summary artifact.

### Cost / risk assessment

- **LLM cost:** One additional API call per retry (only when validation fails). At
  `high` reasoning effort, each call costs ~$0.10–0.50. Cheap relative to the cost
  of a failed factory run.
- **Latency:** 10–30 minutes per retry at `high` effort. Acceptable since the user
  said they don't care about planner speed.
- **Risk:** Low. The retry loop is purely additive. If the first attempt passes
  validation, the loop exits immediately with zero behavioral change.
- **Files affected:** `planner/compiler.py`, `planner/validation.py`. No factory changes.

---

## Workplan W2: LLM Reviewer Pass (semantic consistency check)

### Motivation

Deterministic checks (W1) catch structural and syntactic errors but cannot catch
**semantic** errors — cases where the planner writes acceptance commands that misuse
APIs it defined in earlier work orders. Examples:

- WO-04 calls `load_maze_text(BUILTIN_MAZES['SIMPLE'])` — but `BUILTIN_MAZES['SIMPLE']`
  is a dict, and `load_maze_text` expects a string. The planner confused its own
  return types.

- WO-05 calls `train_q_learning(m, episodes=5, seed=0)` with too few arguments.
  The planner's own `notes` for WO-05 specifies many more required parameters.

These errors survive all deterministic checks because they require understanding the
*meaning* of the APIs defined in `notes`, not just the structure of the JSON.

### What the reviewer would check

A second LLM pass reads ALL work orders as a batch and answers:

1. **Acceptance command / notes consistency:** For each work order, does the acceptance
   command use the APIs as described in the notes? Do function signatures match? Do
   argument types align with what earlier work orders produce?

2. **Cross-work-order API coherence:** If WO-03 defines `BUILTIN_MAZES` as a dict
   mapping names to text strings, and WO-04's acceptance command treats
   `BUILTIN_MAZES['SIMPLE']` as a parsed maze dict, flag the contradiction.

3. **Context_files completeness:** If a work order's notes say "use GridworldEnv from
   env.py" but `maze_rl/env.py` is not in `context_files`, flag it. (This overlaps
   with W1 deterministic checks but the LLM can catch subtler cases.)

### Where the reviewer fits in the codebase

The reviewer runs AFTER the deterministic feedback loop (W1) converges, but BEFORE
writing the final work orders to disk.

```
# In compile_plan(), after the W1 loop produces validated work_orders:

if ENABLE_REVIEWER:
    review_errors = _run_reviewer(client, work_orders)
    if review_errors:
        # Feed review errors back to the planner for one final revision
        prompt = _build_review_revision_prompt(spec_text, work_orders, review_errors)
        raw_response = client.generate_text(prompt)
        parsed = _parse_json(raw_response)
        work_orders, errors = parse_and_validate(parsed)
        # ... (may need another W1 loop here)
```

### Implementation steps

1. **Create reviewer prompt template:**
   - New file: `planner/REVIEWER_PROMPT.md` (or inline in compiler.py).
   - The prompt takes the full list of work orders as JSON and asks the LLM to:
     - For each acceptance command, verify argument types and counts match the
       function signatures described in `notes` (both the current WO and earlier WOs).
     - Report each inconsistency as a structured error:
       `{"work_order_id": "WO-04", "error": "load_maze_text expects str, got dict from BUILTIN_MAZES"}`
   - Output format: JSON array of error objects, or empty array if no issues.

2. **Add `_run_reviewer()` to `planner/compiler.py`:**
   - Takes `client` and `work_orders` list.
   - Builds the reviewer prompt with all work orders serialized as JSON.
   - Calls `client.generate_text()`.
   - Parses the response as a JSON array of errors.
   - Returns the error list.

3. **Add review-revision loop to `compile_plan()`:**
   - After W1 converges, call `_run_reviewer()`.
   - If errors found, build a revision prompt that includes the original spec,
     the current work orders, and the reviewer's findings.
   - Send back to the planner LLM for one final revision attempt.
   - Re-run W1 deterministic validation on the revised output.
   - Maximum 1–2 review cycles (the reviewer should catch most issues on first pass).

4. **Artifact persistence:**
   - Save `reviewer_prompt.txt`, `reviewer_response.json`, `reviewer_errors.json`
     in the compile artifacts directory.

5. **Make it optional:**
   - Add `--review / --no-review` CLI flag to `planner/cli.py`.
   - Default to enabled. Users can skip it for speed during iteration.

### Cost / risk assessment

- **LLM cost:** One additional LLM call for the review, plus potentially one more
  for revision. At `high` reasoning, ~$0.10–0.50 per call. Total planner cost
  roughly doubles.
- **Latency:** 10–30 minutes for the reviewer call, plus 10–30 for revision if
  needed. Total compile time could be 30–90 minutes in the worst case. Acceptable
  per user's stated preference for correctness over speed.
- **Risk:** Moderate. The reviewer LLM may itself hallucinate errors (false positives)
  or miss real ones (false negatives). The revision prompt must be carefully designed
  to avoid the planner "fixing" things that weren't broken. Mitigation: the reviewer
  should output specific, verifiable claims ("WO-04 acceptance calls load_maze_text
  with a dict; WO-03 notes say it returns a dict from BUILTIN_MAZES"). The planner
  can then decide whether to act on each claim.
- **Complexity:** Moderate. Adds a new prompt template, a new LLM call, and a review
  loop to the compiler. The reviewer is a separate concern from the planner — it
  doesn't generate work orders, it only critiques them.
- **Files affected:** `planner/compiler.py`, new `planner/REVIEWER_PROMPT.md`,
  `planner/cli.py` (new flag). No factory changes.

### Dependencies

- W2 should be implemented AFTER W1. The deterministic feedback loop catches cheap
  errors first, so the reviewer only needs to focus on semantic issues. Without W1,
  the reviewer would waste tokens flagging syntax errors that `ast.parse` could have
  caught for free.
- W2 benefits from fix S1 (relaxed `context_files`). With richer context available
  to the executor, some semantic mismatches in acceptance commands become less critical
  — the executor can write correct code even if the acceptance command is imperfect.
  But the acceptance command itself would still be wrong, so W2 remains valuable.

### Open questions

- **Should the reviewer use the same model as the planner, or a different one?**
  Using the same model risks the same blind spots. A different model (e.g., a
  non-reasoning model for the review, since it's a checking task not a generation
  task) could provide genuine diversity. However, this adds configuration complexity.

- **Can the reviewer be run in parallel with deterministic validation?**
  Yes — the reviewer doesn't depend on W1. But running them sequentially lets you
  skip the reviewer entirely when W1 catches errors (cheaper).

- **Should the reviewer check the planner's notes for internal consistency too?**
  E.g., WO-05's notes say "use GridworldEnv" but WO-05's allowed_files doesn't
  include the file where GridworldEnv lives. This is a context_files completeness
  issue that overlaps with S1 / W1.

---

## Current State of Modified Files

### `planner/PLANNER_PROMPT.md` (219 lines)

Changes from original:
- **Lines 44–46:** Plain-text / no-chmod execution constraint
- **Lines 47–50:** No-shell-features rule (`shell=False` explanation)
- **Line 117:** `acceptance_commands` field now says `bash scripts/verify.sh` (was `./scripts/verify.sh`)
- **Lines 126–152:** NEW SECTION "WO-01 BOOTSTRAPPING CONTRACT" — prescribes exact verify.sh content, explains bootstrapping circularity, bans `unittest discover`
- **Lines 154–173:** NEW SECTION "ACCEPTANCE COMMAND DESIGN PRINCIPLE" — requires independent per-feature acceptance beyond global verify
- **Line 206:** Example JSON shows `bash scripts/verify.sh` (was `./scripts/verify.sh`)

### `planner/validation.py` (150 lines)

Changes from original:
- **Line 7:** Added `import shlex`
- **Line 11:** `VERIFY_COMMAND = "bash scripts/verify.sh"` (was `"./scripts/verify.sh"`)
- **Line 12:** Added `VERIFY_SCRIPT_PATH = "scripts/verify.sh"`
- **Lines 15–18:** Added `SHELL_OPERATOR_TOKENS` frozenset
- **Lines 86–95:** WO-01 bootstrap exemption: skips verify command check when WO-01 creates verify.sh
- **Lines 97–112:** Shell operator validation: `shlex.split` each acceptance command, reject bare operator tokens (`|`, `&&`, `>`, etc.)

### `factory/util.py` (214 lines)

Changes from original:
- **Lines 145–163:** Added `except OSError` handler in `run_command` — catches `PermissionError`, `FileNotFoundError`, etc. and returns a failed `CmdResult` with `exit_code=-1` instead of crashing the run

---

## Recommended Next Steps (in priority order)

1. **DONE — S1: Relax `context_files ⊆ allowed_files`** — Subset constraint removed from
   `factory/schemas.py`. Prompt updated. Test updated. The planner can now include
   read-only upstream dependencies in `context_files`.

2. **W1: Deterministic feedback loop** — Highest priority remaining work. Adds `ast.parse`
   validation for `python -c` commands and a compile-retry loop in `compiler.py`. Low risk,
   catches an entire class of syntax errors that currently waste factory runs. See detailed
   workplan above.

3. **W2: LLM reviewer pass** — Implement after W1. Catches semantic API mismatches that
   deterministic checks cannot. Higher cost and complexity than W1 but addresses the I9
   class of failures that no amount of prompt hardening can fully eliminate. See detailed
   workplan above.

4. **Prompt: simplify acceptance command guidance** — Add to `PLANNER_PROMPT.md` that
   acceptance commands should test imports, basic construction, and simple assertions only.
   Complex multi-module integration flows should be deferred to later work orders. Can be
   done independently of W1/W2.

5. **Option C or D** — Evaluate after running the system on 3+ different specs. If WO-01
   keeps causing issues, implement C. If new types of special work orders emerge, implement D.

---

## Test Suite

All 123 existing tests pass with the current changes:

```
python -m pytest tests/ -x -q
# 123 passed in ~9s
```

The test suite does NOT currently cover:
- The new validation rules in `planner/validation.py` (shell operator rejection, bootstrap exemption)
- The `OSError` handler in `factory/util.py`

Adding targeted tests for these would improve confidence in the changes.
