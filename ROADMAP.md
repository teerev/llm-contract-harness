# ROADMAP

**Last updated:** 2026-02-10

**Completed work:**

- **Structural contract (M1–M6):** E001–E006, E101–E106, verify_contract,
  verify_exempt — all implemented in `planner/validation.py` and
  `planner/compiler.py`.
- **Deterministic contract fixes (M-01–M-10):** All ten milestones from the
  adversarial audit (`FINDINGS_LEDGER.md`). Includes: verify_exempt
  sanitization, BaseException rollback, type guards, shlex error codes,
  atomic save_json, path normalization, NUL/control-char rejection, E105
  normalization, rollback_failed status, JSON size guards.
- **Configuration extraction (M-14–M-19):** 50 constants centralized into
  `planner/defaults.py` and `factory/defaults.py`. Generated
  `docs/CONFIG_DEFAULTS.md`. Config snapshots in run/compile summaries.
  Hardening tests guard against value drift, shadowing, and doc staleness.
- **Credibility blockers (M-20–M-23):** Non-retryable failure stages abort
  immediately (M-20). Duplicate `run_id` refuses instead of overwriting
  prior artifacts (M-21). `verify_exempt=true` requires explicit
  `--allow-verify-exempt` CLI flag (M-22). README.md and INVARIANTS.md
  rewritten with precise determinism language, Limitations section, and
  Security Notice (M-23).

Test suite: 487 passed.

Two tracks of work remain:

1. **Prompt semantic hardening** (Part 2) — prompt template changes to reduce
   LLM-generated acceptance command failures.
2. **Artifact audit & light tidy** (Part 3) — naming/format review and
   optional observability improvements.

---
---

# Known Limitations and Accepted Tradeoffs

Distilled from eight adversarial audits (`1.md`–`8.md`). Each item is
classified as a *fundamental limitation*, *deliberate tradeoff*, or
*missing hardening*. None of these are planned for near-term fixes
unless noted otherwise.

| # | Limitation | Classification | Status |
|---|-----------|----------------|--------|
| C1 | **Unsandboxed execution.** Acceptance commands and LLM-authored `verify.sh` run with the operator's full privileges. No container, no network isolation, no filesystem sandbox. | Deliberate tradeoff | Documented in README Security Notice. Mitigation: run in disposable container. |
| C2 | **Semantic verification weakness.** The enforcement layer validates structure (paths, hashes, scope) but cannot verify that LLM-generated code implements the human intent. The LLM can satisfy all checks with stubs. | Fundamental | Part 2 (M-11) addresses prompt-level mitigation. No mechanical fix exists. |
| C4 | **No crash recovery.** Multi-file TR writes are per-file atomic but not batch-atomic. SIGKILL during writes leaves the repo partially modified. No write-ahead log or automatic rollback on restart. | Missing hardening | Preflight `is_clean` check prevents silent corruption on next run. Manual `git reset --hard && git clean -fdx` required. |
| C5 | **No idempotent re-execution.** Re-running a completed work order calls the LLM again and may produce different code. The schema forbids empty writes, so "nothing to do" cannot be expressed. | Incidental gap | Artifact overwrite is fixed (M-21). Re-execution itself remains unsupported. |
| C8 | **Environment dependency.** Verify and acceptance results depend on `PATH`, Python version, installed packages, locale, and filesystem case sensitivity. No environment snapshot is recorded beyond config defaults. | Fundamental | Inherent to running unsandboxed commands. Recommend pinning Python version and using virtual environments. |
| C10 | **No operational packaging.** No `pyproject.toml`, `requirements.txt`, `Dockerfile`, or CI config. Dependencies are not pinned. No health checks or process supervision. | Missing hardening | Acceptable for prototype / research harness positioning. |
| — | **`forbidden` field is not enforced.** The field name implies a mechanical constraint but it is only natural-language guidance injected into the SE prompt. The LLM can ignore it. | Deliberate (naming misleading) | Documented in README Limitations. |
| — | **`notes` field is unstructured.** Carries both implementation guidance and executable invariants with no separation or validation. | Incidental gap | M-13 (LLM reviewer) can check notes-to-acceptance consistency without requiring structure. |
| — | **Sole-writer assumption.** The factory assumes exclusive access to the target repo. No file locking, no advisory lock on the git index. Concurrent modification is undefined behavior. | Missing documentation | Should be listed as a prerequisite. |

---
---

# Part 2: Planner Prompt Hardening — Semantic Reliability

The structural enforcement layer ensures the wrapper never accepts
structurally invalid output. This part addresses **semantic failures** that
no deterministic validator can catch: the planner writes acceptance commands
that are structurally valid but factually wrong.

## The Problem

The planner generates all work orders in a single LLM pass. It cannot
execute code. Any acceptance command that requires the planner to predict
the runtime output of a function is unreliable. Observed failures:

- **Sudoku WO-05:** Planner hardcoded the expected solution string.
  Executor wrote a solver that produced a different output. 5/5 attempts
  failed identically.
- **Word game WO-04:** Planner asserted `select_word(Random(0)) == 'pear'`.
  Actual output is `'orange'`. 5/5 attempts failed.

Root cause: the planner was asked to produce a test oracle for a
computation it cannot execute (the **oracle problem**).

## Testability Hierarchy

**Tier 1 — Structure (always reliable):** Test that modules, classes, and
functions exist with the right shape. These never require execution.

**Tier 2 — Contract properties (reliable):** Test types, membership,
determinism, structural constraints — WITHOUT asserting specific computed
values.

**Oracle trap (unreliable):** Any assertion that requires predicting the
output of a non-trivial computation.

## Milestones

### M-11: Prompt Testability Hardening

Replace the ACCEPTANCE COMMAND DESIGN PRINCIPLE section in
`planner/PLANNER_PROMPT.md` with a testability hierarchy. Remove scar
tissue (unittest-discover ban, redundant WO-01 MUST NOT bullets). Add
oracle-problem statement to the preamble.

**Files:** `planner/PLANNER_PROMPT.md`

**Acceptance criteria:**
- Prompt contains "YOU DO NOT EXECUTE CODE" and tier 1/tier 2 examples.
- Prompt does NOT contain "unittest discover" or redundant MUST NOT bullets.
- `python -m pytest tests/ -q` passes (no code changes).

### M-12 (future): Acceptance Command Linting

A deterministic W-code warning in `validate_plan_v2` when an acceptance
command contains a hardcoded string literal that looks like a computed
value. Heuristic — requires tuning against real planner output.

### M-13 (future): LLM Reviewer Pass

A second LLM pass reviews acceptance commands against the notes for
semantic consistency. Non-deterministic complement to the deterministic
validator. Staged after M-11.

---

## Appendix: Deferred Architectural Options

### Option C: Factory Owns verify.sh Content

Remove verify.sh from the planner's responsibility. The factory injects a
default or uses the `_get_verify_commands` fallback. Eliminates the WO-01
bootstrapping problem but removes flexibility for custom verification.

### Option D: Schema `kind` Field for Work Order Types

Add optional `kind: "scaffold" | "feature"` to WorkOrder. Scaffold WOs get
different validation rules. Currently premature — only WO-01 is special.

### The `notes` Field Problem

The `notes` field carries both implementation guidance and executable
invariants with no separation. The M1-M6 machinery moved some invariants
into structured fields, but notes still carry most of the implementation
contract. M-13 (LLM reviewer) can check notes-to-acceptance consistency
without requiring notes to be structured.

---

## Appendix: W2 LLM Reviewer Pass — Full Design

Preserved for when M-13 implementation begins.

The reviewer runs AFTER the deterministic compile loop converges, BEFORE
writing final work orders. It reads all work orders as a batch and checks:

1. Acceptance command / notes consistency (function signatures, types).
2. Cross-work-order API coherence.
3. Context_files completeness.

Implementation: new `planner/REVIEWER_PROMPT.md`, `_run_reviewer()` in
`compiler.py`, 1-2 review cycles, optional via `--review / --no-review`.

Cost: ~doubles planner LLM calls. Latency: 30-90 min worst case.
Risk: moderate (hallucinated errors possible).

---
---

# Part 3: Artifact Audit & Light Tidy

**Date:** 2026-02-10

Current artifact naming and format is clean — no renames or format changes
needed (reviewed in detail; all verdicts were "good as-is").

## Recommended Actions

1. **[OPTIONAL] Persist revision prompts on retry** — write
   `revision_prompt_attempt_{N}.txt` in `compiler.py` when `attempt > 1`.
2. **[OPTIONAL] Add environment snapshot to summaries** — add
   `python_version`, `platform` to `compile_summary.json` and
   `run_summary.json`.
3. **[COSMETIC] Make `se_prompt.txt` write atomic** — replace bare `open`
   with atomic write helper.
4. **[DEFERRED] Per-attempt timing** — useful for performance work but
   requires state plumbing.

---
---

# Deferred Work

These items are not planned for near-term implementation but are recorded
for future reference.

### `llmc` unified CLI wrapper

A single `llmc plan`, `llmc run`, `llmc batch` entry point. After the
required-vs-default boundary is decided for each config constant, this
becomes a straightforward wiring exercise: import from `defaults.py`,
add argparse flags, pass to existing entry points.

### Required-vs-default boundary

For each of the 50 config constants in `planner/defaults.py` and
`factory/defaults.py`, decide: stays internal, becomes a CLI default
(overridable), or stays hardcoded but documented. Also decide whether
`--timeout-seconds` should be split into `--llm-timeout` and
`--cmd-timeout`.

### Pattern C runtime config file

A TOML/YAML config file (`llmc.toml`) that overrides `defaults.py`
values. Loaded at startup before CLI parsing. CLI flags override config
values (CLI > config > defaults).

### `OPENAI_API_BASE` / `OPENAI_ORG_ID` environment variable support

Currently only `OPENAI_API_KEY` is read. Supporting additional env vars
for API base URL, org ID, and proxy configuration is future work.

---
---

# Part 5: Bootstrap / Verify Ownership Diagnosis

**Date:** 2026-02-11

## TASK 1 — Map of All Verify/Bootstrap Logic

### Location 1: `factory/defaults.py` lines 81–89

```
VERIFY_SCRIPT_PATH = "scripts/verify.sh"
VERIFY_FALLBACK_COMMANDS = [compileall, pip, pytest]
VERIFY_EXEMPT_COMMAND = [compileall]
```

- **Subsystem:** Factory (constants)
- **IR-driven:** No. These are hardcoded policy constants.
- **What it does:** Defines the path of the verify script, fallback commands
  when verify.sh is absent, and the lightweight command run when
  `verify_exempt=True`.

### Location 2: `factory/nodes_po.py::_get_verify_commands` lines 29–38

- **Subsystem:** Factory
- **IR-driven:** No. Runtime filesystem probe.
- **What it does:** Checks whether `scripts/verify.sh` exists on disk. If
  yes, returns `["bash", "scripts/verify.sh"]`. If no, returns the fallback
  commands from defaults. This is a **factory-internal policy decision** —
  no IR field controls which branch is taken.

### Location 3: `factory/nodes_po.py::po_node` lines 81–86

- **Subsystem:** Factory
- **IR-driven:** Yes — reads `work_order.verify_exempt`.
- **What it does:** If `verify_exempt` is True, runs only `VERIFY_EXEMPT_COMMAND`
  (compileall). Otherwise, calls `_get_verify_commands`. The comment says
  "e.g. WO-01 bootstrap" but the code is generic — any WO with
  `verify_exempt=True` gets the lightweight path. **This is clean IR-driven
  logic.**

### Location 4: `factory/run.py` lines 287–311 (M-22 block)

- **Subsystem:** Factory
- **IR-driven:** Partially. Reads `verify_exempt` from the WO, but then
  applies **implicit bootstrap detection**: it checks whether the WO's
  postconditions create `scripts/verify.sh`. If yes, it auto-honors
  `verify_exempt=True` even without `--allow-verify-exempt`. If no, it
  overrides `verify_exempt` to False (with a warning) unless the operator
  passed `--allow-verify-exempt`.
- **What it does:** This is a **factory-side special case for verify.sh**.
  The factory is inspecting WO content to detect "bootstrap" semantics and
  making a policy decision. This breaks the principle that the factory is
  a generic IR executor.

### Location 5: `planner/defaults.py` lines 75–76

```
VERIFY_SCRIPT_PATH = "scripts/verify.sh"
VERIFY_COMMAND = "bash scripts/verify.sh"
```

- **Subsystem:** Planner (constants)
- **IR-driven:** No. Used by E105 validation.

### Location 6: `planner/validation.py::validate_plan_v2` lines 519–537 (E105)

- **Subsystem:** Planner
- **IR-driven:** No. Static policy: ban `bash scripts/verify.sh` (or
  equivalent) in acceptance commands.
- **What it does:** Prevents the LLM from putting the global verify command
  in acceptance_commands. **Planner-only, clean.**

### Location 7: `planner/validation.py::validate_plan_v2` lines 644–674 (E106)

- **Subsystem:** Planner
- **IR-driven:** Yes — reads `verify_contract` from the manifest.
- **What it does:** Checks that the verify_contract's requirements are all
  satisfied by the cumulative postconditions of the plan. **Planner-only,
  clean, fully IR-driven.**

### Location 8: `planner/validation.py::compute_verify_exempt` lines 677–717

- **Subsystem:** Planner
- **IR-driven:** Yes — reads `verify_contract` and WO postconditions.
- **What it does:** For each WO, computes whether the cumulative
  postconditions satisfy the verify_contract requirements. If not yet
  satisfied, the WO is marked `verify_exempt=True` (verification would
  fail because the verify infrastructure isn't complete yet). **Planner-only,
  clean, fully IR-driven.**

### Location 9: `planner/compiler.py` lines 406–422 (M-01 block)

- **Subsystem:** Planner
- **IR-driven:** Yes — reads `verify_contract`.
- **What it does:** Always overwrites `verify_exempt` — either via
  `compute_verify_exempt` (if contract is valid) or forces False. Never
  trusts LLM-provided values. **Planner-only, clean.**

### Location 10: `planner/compiler.py` lines 424–455 (bootstrap skip)

- **Subsystem:** Planner
- **IR-driven:** Partially. Detects "bootstrap" by checking if a WO's
  postconditions create `scripts/verify.sh`, then cross-references
  against `repo_file_listing`.
- **What it does:** If the repo already contains `scripts/verify.sh`,
  filters out the bootstrap WO and renumbers the remaining WOs. Records
  `bootstrap_skipped` and `bootstrap_reason` in `CompileResult`.
- **Assessment:** This is a **planner-side special case for verify.sh**.
  It's better here than in the factory (the planner adapts the plan to
  the repo state), but it still uses a hardcoded path.

### Location 11: `planner/PLANNER_PROMPT.md` lines 142–169

- **Subsystem:** Planner (prompt template)
- **What it does:** Instructs the LLM to always emit WO-01 as a bootstrap
  WO that creates `scripts/verify.sh` with specific content, and prescribes
  exact preconditions/postconditions/acceptance for it.
- **Assessment:** This is the **root source** of the bootstrap concern.
  The prompt hardcodes that WO-01 = bootstrap verify.sh.

### Location 12: `factory/nodes_se.py` lines 166–211 (precondition gate)

- **Subsystem:** Factory
- **IR-driven:** Yes — reads `work_order.preconditions`.
- **What it does:** Enforces `file_exists` and `file_absent` preconditions
  at runtime. If a WO has `file_absent("scripts/verify.sh")` and the file
  exists, this gate fails with "PLANNER-CONTRACT BUG".
- **Assessment:** Fully generic. No verify.sh special-casing. The failure
  is driven entirely by the IR.

---

## TASK 2 — End-to-End Scenario Analysis

### S1: Empty repo, no verify.sh, planner generates, factory executes

1. **Planner:** LLM emits WO-01 with `postconditions: [file_exists("scripts/verify.sh")]` and `preconditions: [file_absent("scripts/verify.sh")]`.
2. **Planner validation:** E106 checks verify_contract reachability — passes (WO chain eventually creates verify.sh + test files).
3. **Planner `compute_verify_exempt`:** WO-01's cumulative state doesn't yet satisfy the full verify_contract (e.g., test files missing) → `verify_exempt=True`.
4. **Factory M-22 (run.py):** WO-01 has `verify_exempt=True`. Factory detects postconditions contain `scripts/verify.sh` → auto-honors exempt (bootstrap detection). No `--allow-verify-exempt` needed.
5. **Factory SE:** Precondition `file_absent("scripts/verify.sh")` is true (empty repo) → passes.
6. **Factory PO:** `verify_exempt=True` → runs only compileall, not full verify. Correct — verify.sh was just created, can't verify itself.
7. **Result:** Works correctly. No issues.

### S2: Repo already has verify.sh, planner generates WO that creates it

1. **Planner (Location 10):** Detects `scripts/verify.sh` in `repo_file_listing`. Finds bootstrap WO (postconditions create verify.sh). Filters it out. Renumbers remaining WOs.
2. **Planner validation:** Runs on the filtered set. verify_contract may now fail E106 if verify.sh was in the contract requirements — but it's already in `repo_file_listing` which seeds `file_state`, so the requirement is pre-satisfied.
3. **Factory:** Receives WO set without bootstrap WO. No `file_absent("scripts/verify.sh")` precondition to trip. Works correctly.
4. **Result:** Works correctly. The planner's bootstrap-skip logic (Location 10) handles this.

### S3: User runs planner twice, second run includes bootstrap again

Same as S2. The second planner run has `repo_file_listing` containing `scripts/verify.sh` (from the first run's factory execution). Location 10 filters the bootstrap WO. Works.

### S4: Factory runs on different branch where verify.sh already exists

1. **Factory M-22:** If the WO has `verify_exempt=True`, factory detects bootstrap (postconditions contain verify.sh) → auto-honors.
2. **Factory SE:** Precondition `file_absent("scripts/verify.sh")` is FALSE → precondition gate fails with "PLANNER-CONTRACT BUG".
3. **Result:** Fails correctly. The WO was planned for an empty repo; the branch already has verify.sh. The precondition catches this.

But wait — this only happens if the user runs a bootstrap WO against a repo that already has verify.sh. If the planner was re-run with `--repo` pointing to this branch, Location 10 would have filtered the bootstrap WO. The failure only occurs if the user manually feeds stale WOs to the factory. The precondition gate catches this, which is correct behavior.

---

## TASK 3 — Who Owns the Bootstrap Concern?

### Diagnosis: Split/implicit across both, with one factory-side smell

The bootstrap concern is **mostly planner-owned** (B), with one
**factory-side special case** that breaks generality:

**Clean (planner-only, IR-driven):**
- The prompt instructs the LLM to emit a bootstrap WO (Location 11)
- The planner computes `verify_exempt` from `verify_contract` + postconditions (Location 8, 9)
- The planner filters the bootstrap WO when verify.sh already exists (Location 10)
- The planner bans verify.sh in acceptance commands (Location 6)
- The planner checks verify_contract reachability (Location 7)

**Clean (factory, IR-driven):**
- PO node reads `verify_exempt` from the WO and chooses verify commands accordingly (Location 3)
- SE node enforces preconditions generically (Location 12)
- `_get_verify_commands` probes the filesystem for verify.sh — this is a factory internal policy about *how* to verify, not *whether* to verify. It's generic and applies to all WOs equally (Location 2).

**The smell (factory, NOT purely IR-driven):**
- **Location 4 (M-22 in run.py):** The factory inspects WO postconditions to detect whether they create `scripts/verify.sh`, and uses this to auto-honor `verify_exempt=True` without `--allow-verify-exempt`. This is implicit bootstrap detection inside the factory. It makes the factory aware of verify.sh semantics that should be a planner concern.

### Is verify.sh "special" in the factory?

**Yes, in two ways:**

1. `_get_verify_commands` checks for `scripts/verify.sh` on disk. This is
   **defensible** — the factory needs to know how to run global verification,
   and probing for verify.sh is a reasonable convention. It applies to all
   WOs equally and is not WO-01 specific.

2. The M-22 block in `run.py` detects bootstrap WOs by inspecting
   postconditions for `scripts/verify.sh`. This is **the design flaw** —
   the factory is doing content-based inference about WO intent rather
   than trusting the IR field (`verify_exempt`).

### Is there implicit "WO-01" semantics?

No. Nothing in the factory checks `work_order.id == "WO-01"`. The bootstrap
detection is postcondition-based, not ID-based. But the postcondition check
is still a form of implicit policy — the factory is interpreting WO content
rather than just executing IR fields.

### Root cause classification

**(d) The M-22 block creates coupling.** The planner correctly computes
`verify_exempt` and the factory's PO node correctly reads it. The M-22
block in `run.py` then second-guesses this by default-overriding
`verify_exempt` to False unless it can detect a "bootstrap" WO or the
operator passes `--allow-verify-exempt`. This creates a situation where:
- The planner says `verify_exempt=True` (correct, computed from
  verify_contract)
- The factory says "I don't trust that, let me check if this looks like a
  bootstrap WO" (implicit policy override)

The defense for M-22 is safety: an operator running the factory standalone
(without the planner's M-01 guarantee) might have a WO with a
maliciously-set `verify_exempt=True`. M-22 is a belt-and-suspenders
override. But the bootstrap auto-detection is the part that smells.

---

## TASK 4 — Proposed Fix ✅ DONE

**Status:** Implemented (2026-02-11). Removed bootstrap auto-detection from
`factory/run.py` M-22 block. Cleaned up WO-01-specific comment in
`factory/nodes_po.py`. Full suite: 523 passed.

### Recommended ownership model: Planner owns bootstrap semantics, factory trusts IR

The cleanest model:
1. The **planner** is solely responsible for computing `verify_exempt`
   (already true via M-01).
2. The **factory** trusts `verify_exempt` as an IR field and acts on it.
3. The factory's `--allow-verify-exempt` flag is a valid safety control
   for standalone use. Keep it.
4. **Remove the bootstrap auto-detection from the factory.** The M-22
   block should not inspect postconditions for `scripts/verify.sh`.

### Minimal patch plan

**File: `factory/run.py` — simplify the M-22 block (lines ~287–311)**

Current (problematic):
```python
if wo_dict.get("verify_exempt") and not getattr(args, "allow_verify_exempt", False):
    postcond_paths = {c.get("path", "") for c in wo_dict.get("postconditions", []) ...}
    is_bootstrap = _fd.VERIFY_SCRIPT_PATH in postcond_paths
    if is_bootstrap:
        con.step("M-22", "verify_exempt=true auto-honored — bootstrap")
    else:
        con.warning("overriding to false")
        wo_dict["verify_exempt"] = False
```

Proposed (clean):
```python
if wo_dict.get("verify_exempt") and not getattr(args, "allow_verify_exempt", False):
    con.warning(
        "work order has verify_exempt=true but --allow-verify-exempt "
        "was not passed. Overriding to false — full verification will run."
    )
    wo_dict["verify_exempt"] = False
```

This removes the bootstrap auto-detection entirely. The rule becomes simple
and auditable: `verify_exempt` is honored if and only if the operator passes
`--allow-verify-exempt`. No content inspection, no implicit policy.

**Impact on `run_work_orders.sh` or equivalent orchestrator:**

The orchestrator that runs WOs sequentially would pass
`--allow-verify-exempt` because it trusts the planner's M-01 computation.
This is a one-flag change in the calling script. The safety default (no flag
= full verification) protects standalone/manual use.

**No other files need to change.** The planner-side logic (Locations 6–10)
is clean. The factory PO node (Location 3) is clean. Only the M-22 block
needs simplification.

### Optional: rename `--allow-verify-exempt` to `--trust-planner-exempt`

The current flag name describes the mechanism. A rename to
`--trust-planner-exempt` would make the intent clearer: "I trust that the
planner computed verify_exempt correctly." This is optional and cosmetic.

### Summary

| Component | Status | Action |
|-----------|--------|--------|
| Planner prompt (WO-01 bootstrap) | Clean — instructs LLM | None |
| Planner E105 (ban verify in acceptance) | Clean — IR-independent policy | None |
| Planner E106 (verify_contract reachability) | Clean — IR-driven | None |
| Planner compute_verify_exempt | Clean — IR-driven | None |
| Planner M-01 (never trust LLM exempt) | Clean — IR-driven | None |
| Planner bootstrap-skip (Location 10) | Acceptable — planner-side adaptation | None |
| Factory PO node verify_exempt branch | Clean — IR-driven | None |
| Factory _get_verify_commands | Clean — runtime convention | None |
| Factory SE precondition gate | Clean — IR-driven | None |
| **Factory M-22 bootstrap detection** | **Smell — implicit content inspection** | **Remove bootstrap auto-detection** |
