# AGENTS.md — Single Source of Truth (READ FIRST)

## 0) Mission (what you are building)
Build a minimal, deterministic **factory harness** in Python that runs a strict **SE → TR → PO** loop using LangGraph:

- **SE (Software Engineer)**: an LLM proposes a **unified diff patch** only.
- **TR (Tool Runner / Applier)**: deterministically validates + applies the patch **in-situ** to a product repo.
- **PO (Verifier / Judge)**: deterministically runs **global verification** + **acceptance commands** and returns PASS/FAIL.
- The harness (not the LLM) controls retries, rollback, routing, artifacts, and termination.

This is a harness, not “autonomous SWE”. **Credibility > cleverness.**

## 1) Read-first rule (MANDATORY)
You must read this entire file before writing any code.
If anything is ambiguous, choose the simplest valid implementation and document it in README → “Assumptions”.

## 2) Non-negotiables (MANDATORY)

### 2.1 Determinism & execution hygiene
- All command execution is done by a deterministic Python subprocess runner (not by the LLM).
- Never use `shell=True`.
- Always capture `stdout`, `stderr`, `exit_code`.
- Always enforce a timeout per command.
- Always run commands with `cwd=repo_root`.
- Use stable sorting for any file listings.
- Never use nondeterministic IDs (no uuid4). Use deterministic `run_id` derived from input hashes.
- Never fetch from the network except for the LLM API call itself (if used).

### 2.2 Git-only + clean-tree preflight (safety + credibility)
- The product repo **must** be a Git repo.
- The working tree must be **clean** before starting:
  - no staged changes
  - no unstaged changes
  - no untracked files
- If not Git or not clean: hard error with a clear message, and do not modify anything.

### 2.3 In-situ edits with strict rollback (transaction semantics)
No workspace copying in this minimal version.

- Record `baseline_commit = git rev-parse HEAD` at preflight.
- Each attempt must start from `baseline_commit`.
- On any failure after applying a patch, rollback deterministically:
  - `git reset --hard <baseline_commit>`
  - `git clean -fd`
- WARNING: `git clean -fd` is destructive. This is why clean-tree preflight is mandatory.

### 2.4 Sharp boundary: LLM proposes, harness decides
- The LLM may only propose a unified diff patch + brief summary.
- The LLM never runs commands and never decides whether failures matter.
- Routing/termination decisions are deterministic and implemented in the harness.

### 2.5 Verification is law (always enforced)
For every attempt:
1) Run **global verification** (see §6.5 exact command rules).
2) Then run `acceptance_commands` from the work order (in order).
Any nonzero exit code is failure.

### 2.6 Patch scope enforcement (computed from diff, not LLM)
- Work orders provide `allowed_files` as explicit relative paths (no globs).
- The harness must compute touched files from the diff headers and enforce:
  - touched files ⊆ allowed_files
- If violation: reject with stage `patch_scope_violation`.
- Do not trust any file list produced by the LLM.

### 2.7 Structured failure feedback (bounded)
- Convert failures into a bounded `FailureBrief` for SE retries.
- Never paste full logs into the LLM prompt.
- Store full logs to files; pass only a short excerpt.

### 2.8 Minimal dependencies
- Dependencies: stdlib + `pydantic` + `langgraph` + whatever is strictly required to call the LLM.
- No DB, no queue, no remote VM logic.

### 2.9 Required package tree (MUST MATCH EXACTLY)
Create exactly these files under `factory/` (no extra Python modules unless absolutely required; if required, justify in README):

factory/
- __main__.py
- graph.py
- llm.py
- nodes_po.py
- nodes_se.py
- nodes_tr.py
- run.py
- schemas.py
- util.py
- workspace.py  (repurposed: git helpers + rollback; NOT a temp workspace copier)

## 3) CLI requirements (must implement)
Command:

`python -m factory run --repo /path/to/product --work-order /path/to/work_order.json --out /path/to/outdir`

Flags:
- `--max-attempts` (default 2)
- `--llm-model` (string; required)
- `--llm-temperature` (default 0)
- `--timeout-seconds` (default 600)

Behavior:
- Print final verdict and the path to `<out>/<run_id>/run_summary.json`.

## 4) Schemas (Pydantic) — minimal, strict (define in factory/schemas.py)

### 4.1 WorkOrder
Fields:
- `id: str`
- `title: str`
- `intent: str`
- `allowed_files: list[str]`  (explicit relative paths only)
- `forbidden: list[str]`
- `acceptance_commands: list[str]`
- `context_files: list[str]`
- `notes: str | None = None`

Validation rules:
- For each path in `allowed_files` and `context_files`:
  - must be relative
  - must not contain drive letters
  - normalized path must not start with `..`
  - must not be absolute
- `acceptance_commands` must be non-empty.
- `context_files` must be bounded:
  - max 10 files
  - max 200 KB total read bytes (truncate context file reading deterministically if needed)
- Simplest rule for subset:
  - require `context_files ⊆ allowed_files` (strict and safe). Document in README.

### 4.2 PatchProposal
- `unified_diff: str`
- `summary: str`

### 4.3 FailureBrief
- `stage: str`  (one of stages below)
- `command: str | None`
- `exit_code: int | None`
- `primary_error_excerpt: str`
- `constraints_reminder: str`

Allowed stages:
- `preflight`
- `llm_output_invalid`
- `patch_scope_violation`
- `patch_apply_failed`
- `verify_failed`
- `acceptance_failed`
- `exception`

### 4.4 CmdResult
- `command: list[str]`
- `exit_code: int`
- `stdout_trunc: str`
- `stderr_trunc: str`
- `stdout_path: str`
- `stderr_path: str`
- `duration_seconds: float`

### 4.5 AttemptRecord
- `attempt_index: int`
- `baseline_commit: str`
- `patch_path: str`
- `touched_files: list[str]`
- `apply_ok: bool`
- `verify: list[CmdResult]`
- `acceptance: list[CmdResult]`
- `failure_brief: FailureBrief | None`

### 4.6 RunSummary
- `run_id: str`
- `repo_path: str`
- `work_order_path: str`
- `work_order_hash: str`
- `repo_baseline_commit: str`
- `repo_tree_hash_before: str`
- `repo_tree_hash_after: str`
- `max_attempts: int`
- `attempts: list[AttemptRecord]`
- `verdict: str`  (`"PASS"` or `"FAIL"`)
- `ended_stage: str`
- `started_utc: str`
- `ended_utc: str`

## 5) Deterministic IDs and hashing (must implement)

### 5.1 Canonical JSON hashing for work orders
- Load the work order JSON.
- Re-serialize to canonical JSON bytes:
  - sorted keys
  - no whitespace (separators=(",", ":"))
  - UTF-8 encoding
- `work_order_hash = sha256(canonical_bytes).hexdigest()`

### 5.2 Stable repo tree hash (before/after)
Implement stable tree hashing:
- Walk repo root recursively and include regular files only.
- Exclude directories: `.git`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.venv`, `venv`, `env`, `node_modules`.
- Exclude the outdir if it is inside repo (but preflight must prevent that anyway).
- Sort all relative file paths.
- Hash bytes of: `relpath + b"\0" + file_bytes + b"\0"` for each file in order.

### 5.3 config_hash
Compute `config_hash = sha256(f"{llm_model}|{llm_temperature}|{max_attempts}|{timeout_seconds}".encode("utf-8")).hexdigest()`

### 5.4 run_id
Compute:
`run_id = sha256((work_order_hash + repo_tree_hash_before + config_hash).encode("utf-8")).hexdigest()[:12]`

No timestamps in run_id. Timestamps may be used only for human-readable logs.

## 6) Core behavior spec (MUST FOLLOW)

### 6.1 Preflight steps
Given `repo_path`, `work_order_path`, `out_dir`:
- Resolve and validate paths.
- Ensure out_dir exists (create).
- Ensure out_dir is NOT inside repo (hard error if it is).
- Verify repo is a git repo (e.g., `git rev-parse --is-inside-work-tree`).
- Verify working tree is clean:
  - `git status --porcelain` must be empty.
- Record:
  - baseline_commit = `git rev-parse HEAD`
  - repo_tree_hash_before

### 6.2 Attempt loop
For attempt_index in 1..max_attempts:

1) **SE node**:
   - Construct an SE prompt that includes:
     - WorkOrder intent + constraints
     - allowed_files list
     - forbidden list
     - context_files contents (bounded)
     - FailureBrief if present
     - explicit instruction: output JSON with keys `unified_diff` and `summary` only
   - Call LLM via `llm.complete()`.
   - Strictly parse output:
     - must parse to PatchProposal
     - unified_diff must be non-empty and contain diff headers
   - If parse fails: FailureBrief(stage="llm_output_invalid") and this attempt is a FAIL with no repo changes.

2) **TR node**:
   - Compute touched files from diff headers (must implement robustly):
     - accept `diff --git a/<path> b/<path>` lines
     - accept `+++ b/<path>` lines as fallback
   - Normalize touched file paths.
   - Enforce touched files ⊆ allowed_files.
   - If violation: FailureBrief(stage="patch_scope_violation") and FAIL.
   - Apply patch using git (no shell=True):
     - Prefer: `git apply --whitespace=nowarn --unsafe-paths` is NOT allowed. Do NOT use unsafe-paths.
     - Use: `git apply --whitespace=nowarn`
   - If apply fails: FailureBrief(stage="patch_apply_failed") and FAIL.
   - Record apply_result.json.

3) **PO node**:
   - Run global verification commands (see §6.5).
   - If any verify command fails: FailureBrief(stage="verify_failed") and FAIL.
   - If verify passes, run acceptance commands in order.
   - If any acceptance command fails: FailureBrief(stage="acceptance_failed") and FAIL.
   - If all pass: PASS.

4) **On FAIL after patch apply**:
   - Roll back deterministically:
     - `git reset --hard <baseline_commit>`
     - `git clean -fd`
   - Continue if attempts remain.

5) **On PASS**:
   - Leave changes in repo (no auto-commit in this minimal version).
   - Compute `repo_tree_hash_after`.
   - End success.

### 6.3 Termination
- PASS only if verify + all acceptance commands succeed in an attempt.
- Otherwise FAIL after max attempts.
- ended_stage for RunSummary should be:
  - `"success"` on PASS
  - else the final FailureBrief.stage (or `"exception"`)

### 6.4 Deterministic command runner requirements
Implement one runner used everywhere:
- Takes `command: list[str]`, `cwd: Path`, `timeout_seconds: int`.
- Uses `subprocess.run` with:
  - `text=True`, `encoding="utf-8"`, `errors="replace"`,
  - `capture_output=True`,
  - `timeout=...`,
  - `check=False`
- Writes full stdout/stderr to files (paths returned in CmdResult).
- Stores truncated versions in CmdResult:
  - truncation rule: last 200 lines OR last 8000 chars (choose simplest deterministic method).
- Measures duration using monotonic clock.

### 6.5 EXACT global verification command rules (NO AMBIGUITY)
Global verification is defined as:

- If `<repo>/scripts/verify.sh` exists and is a file:
  - Run it as: `["bash", "scripts/verify.sh"]`
- Else run this fixed fallback list, in order (each as list[str]):

1) `["python", "-m", "compileall", "-q", "."]`
2) `["python", "-m", "pip", "--version"]`
3) `["python", "-m", "pytest", "-q"]`

Important notes:
- Do NOT try to “detect” whether pytest exists. Just run it.
- If pytest is not installed and this command fails, the harness reports verify_failed.
- This is intentional: the product repo must provide a sane verify path (prefer scripts/verify.sh).
- Document this in README clearly.

### 6.6 Acceptance command execution rules
- Each acceptance command is provided as a string in WorkOrder.
- Convert each command string into argv deterministically:
  - Use `shlex.split()` (POSIX mode) to create `list[str]`.
  - Never run through a shell.
- Run each with the deterministic runner in repo root.
- Any nonzero exit code => acceptance_failed.

## 7) Artifacts spec (minimal, exact)
Write artifacts under:

`<out>/<run_id>/attempt_<N>/`

Per attempt:
- `proposed_patch.diff` (write exactly the unified diff)
- `apply_result.json` (whether apply succeeded, touched_files, git apply stderr excerpt if failed)
- `verify_result.json` (list[CmdResult] from global verify)
- `acceptance_result.json` (list[CmdResult] from acceptance commands)
- `failure_brief.json` (if fail)
- Full logs: for each command, write stdout/stderr files (paths referenced in CmdResult)

At end write:
- `<out>/<run_id>/run_summary.json`

JSON requirements:
- UTF-8
- pretty-printed
- stable keys ordering where feasible

## 8) LangGraph requirements (graph structure)
Define a LangGraph that encodes the loop. The harness must be readable and boring.

State should include at least:
- repo_path, out_dir, work_order, work_order_path
- attempt_index, max_attempts
- baseline_commit
- failure_brief (optional)
- patch_proposal (optional)
- attempt_records (list)
- verdict (optional)

Flow:
START → SE → TR → PO
- If PO PASS → END success
- If PO FAIL and attempts remain → SE (with failure_brief)
- If PO FAIL and attempts exhausted → END failure

Routing decisions are deterministic and based only on state.

## 9) File responsibilities (must follow)
- `schemas.py`: pydantic models + load/save helpers.
- `util.py`: hashing, truncation, json IO, canonical json bytes, command runner, path validation helpers.
- `workspace.py`: git helpers (is_git_repo, is_clean, baseline, rollback).
- `llm.py`: thin LLM wrapper (instantiate model, complete()) + strict parsing helper.
- `nodes_se.py`: SE prompt construction + LLM call + parse to PatchProposal.
- `nodes_tr.py`: diff touched-file parsing + scope checks + git apply + apply_result emission.
- `nodes_po.py`: run verify + acceptance + FailureBrief extraction.
- `graph.py`: LangGraph definition and routing.
- `run.py`: CLI entry logic (invoked by __main__), orchestrates run, artifact dirs, run_summary.
- `__main__.py`: argparse wiring, calls run.run_cli().

## 10) Implementation order (MANDATORY)
Implement in this order:
1) schemas.py
2) util.py
3) workspace.py
4) llm.py
5) nodes_se.py
6) nodes_tr.py
7) nodes_po.py
8) graph.py
9) run.py + __main__.py
10) README.md + example work order JSON

## 11) Output contract (MANDATORY)
When you finish, output ONLY:
1) A file list
2) Full contents of each file under `factory/` (exactly)
3) A minimal README.md
4) A minimal example work order JSON

No extra commentary.

## 12) Compliance checklist (self-check before final output)
- [ ] Only required `factory/` files were created (plus README + example JSON).
- [ ] `python -m factory --help` wiring is correct.
- [ ] No `shell=True` anywhere.
- [ ] Git repo + clean-tree preflight enforced.
- [ ] Rollback uses baseline commit + `git clean -fd`.
- [ ] Diff touched-file parsing implemented; scope enforcement uses it.
- [ ] Global verify command rules match §6.5 exactly.
- [ ] Acceptance commands use shlex.split and never a shell.
- [ ] Artifacts emitted to the specified paths.
- [ ] FailureBrief is bounded; full logs written to files; only excerpts sent to LLM.
