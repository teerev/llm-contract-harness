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
