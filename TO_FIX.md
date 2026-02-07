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
  context_files: list[str]        # MUST be subset of allowed_files (enforced at line 64)
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

#### I8: `context_files ⊆ allowed_files` is too restrictive

**Current state of the code:**

`factory/schemas.py` lines 60–71 enforce:

```python
@model_validator(mode="after")
def _check_context_constraints(self) -> "WorkOrder":
    if len(self.context_files) > 10:
        raise ValueError("context_files must have at most 10 entries")
    allowed_set = set(self.allowed_files)
    for cf in self.context_files:
        if cf not in allowed_set:
            raise ValueError(
                f"context_files must be a subset of allowed_files: "
                f"{cf!r} not in allowed_files"
            )
    return self
```

And `planner/PLANNER_PROMPT.md` line 122 tells the planner:

```
- `context_files`: list of strings, subset of allowed_files
```

**The problem:**

The executor LLM (SE node) can only see files listed in `context_files`. But `context_files`
must be a subset of `allowed_files` (files the executor is allowed to *write*). This means
the executor **cannot read files it is not allowed to edit**.

In practice, work orders frequently need to *read* upstream modules to understand their APIs
without *writing* to them. Two real failures were caused by this:

- **WO-05 failure** (`out2/` run): `train_q_learning` in `maze_rl/qlearn.py` referenced
  `env.height` / `env.rows` — attributes that don't exist on `GridEnv`. The executor
  couldn't see `maze_rl/env.py` (not in `context_files` because not in `allowed_files`)
  so it guessed attribute names. Both attempts failed with `AttributeError`.

- **WO-04 failure** (`out4/` run): The acceptance command called
  `load_maze_text(BUILTIN_MAZES['SIMPLE'])` but `BUILTIN_MAZES['SIMPLE']` returns a dict,
  not a string. `load_maze_text` expects a string. The planner wrote a self-contradictory
  acceptance command because it lost track of its own API definitions across work orders.

**Where the constraint lives:**

1. Schema: `factory/schemas.py` line 64 — hard validator
2. Prompt: `planner/PLANNER_PROMPT.md` line 122 — tells the planner the rule
3. SE node: `factory/nodes_se.py` line 32 — reads `work_order.context_files` to build prompt

**What needs to change (proposed fix S1):**

Option A — Add a new field `read_context` (or similar) to the schema that is NOT
constrained to `allowed_files`. The SE node would read from the union of `context_files`
and `read_context`. The TR node scope check (`factory/nodes_tr.py` line 100) would
continue to check only `allowed_files`. The planner prompt would explain that
`read_context` provides read-only visibility into upstream modules.

Option B — Remove the subset constraint entirely from `context_files`. Let `context_files`
contain any repo-relative path. The SE node already reads them as read-only context (no
writes are generated for context-only files). The TR node scope check is separate and
would continue enforcing that *writes* are within `allowed_files`.

Option B is simpler but changes the semantics of `context_files` from "files I will edit
and need context for" to "files I need to see." Option A is more explicit but adds a new
schema field.

**Impact analysis for either option:**

- `factory/schemas.py`: Remove or relax the subset validator (lines 64–70)
- `planner/PLANNER_PROMPT.md`: Update field description (line 122) and explain read-only context
- `planner/validation.py`: No change needed (it doesn't enforce this constraint)
- `factory/nodes_se.py`: No change needed (`_read_context_files` already just reads whatever is listed)
- `factory/nodes_tr.py`: No change needed (scope check uses `allowed_files`, not `context_files`)
- Existing work orders: Still valid (subset is still allowed, just no longer required)
- Tests: `tests/test_schemas.py` likely has tests for the subset constraint that would need updating

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

1. **S1: Relax `context_files ⊆ allowed_files`** — This is the most impactful remaining fix.
   It directly caused failures in WO-04 and WO-05 across multiple runs. Clean schema change,
   low risk, no factory logic changes needed.

2. **Prompt: simplify acceptance command guidance** — Add to `PLANNER_PROMPT.md` that
   acceptance commands should test imports, basic construction, and simple assertions only.
   Complex multi-module integration flows should be deferred to later work orders.

3. **Option C or D** — Evaluate after running the system on 3+ different specs. If WO-01
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
