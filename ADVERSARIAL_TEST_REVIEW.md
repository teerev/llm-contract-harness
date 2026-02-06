# ADVERSARIAL_TEST_REVIEW.md

## 0. Executive Verdict (Hard Truth)

- The test suite would **not** catch a serious regression in `run.py`, which is the actual CLI entry point. Graph tests bypass `run.py` entirely; CLI tests only exercise preflight rejections. The normal PASS/FAIL execution path through `run.py` is **untested**.
- The exit-code-2 emergency handler (`run.py` lines 111-155) is **completely dark** — no test exercises it, yet it is the system's last safety net against unhandled crashes.
- `run_summary.json` — the single most important output artifact — is **never read from disk and validated** by any test. It could contain garbage keys, wrong types, or be missing entirely (for non-error paths through `run.py`) and every test would still pass.
- Most artifact assertions in graph tests are **existence-only** (`os.path.isfile`). Artifact content could silently drift or corrupt without breaking any test.
- Only **2 of 7+** distinct failure modes are exercised at the graph integration level (`llm_output_invalid` and `acceptance_failed`). The other five (`verify_failed`, `write_scope_violation`, `stale_context`, `write_failed`, `exception`) are tested only at the isolated node level, leaving graph routing, finalize behavior, and rollback for those paths unverified.
- The "all hash checks before any writes" atomicity guarantee in TR is untested — partial writes on stale_context could occur and no test would detect it.
- The network guard in `test_safety.py` is **test-local**, not session-scoped. It does not actually prevent other tests from making accidental network calls.
- The retry feedback loop is tested for exactly one failure stage (`verify_failed`). Whether other stages produce useful retry prompts is unverified.
- The suite is **overconfident**. It appears comprehensive because it covers many code paths at the unit level, but it lacks the integration-level tests that would catch wiring bugs, artifact corruption, and behavioral regressions visible to downstream consumers.
- A maintainer reading TEST_PLAN.md would believe this suite protects refactoring. It does not — not at the level that matters (observable outputs: exit codes, artifact files, git state after full CLI invocations).

---

## 1. Surface-Area Coverage Audit

### CLI exit codes and stderr/stdout behavior
**Weakly Covered.**
`test_cli.py` tests exit code 0 (`--help`), exit code 1 (preflight rejections, `--max-attempts 0`). Stderr assertions use substring matching (`b"not a git repository"`, `b"uncommitted changes"`, etc.), which is reasonable. **However**: exit code 1 for normal FAIL verdict (through `run.py` line 181) is never tested. Exit code 0 for normal PASS verdict is never tested. The stdout lines `"Verdict: PASS"` and `"Run summary: <path>"` printed by `run.py` (lines 177-178) are never asserted. Exit code 2 for unhandled exceptions is never tested. The `_run_factory` helper in `test_cli.py` removes `OPENAI_API_KEY`, making it impossible to exercise the graph through the CLI without additional machinery.

### Artifact directory layout and filenames
**Weakly Covered.**
`test_util.py` pins exact `ARTIFACT_*` constant string values — this is good and would catch renames. `test_util.py` tests `make_attempt_dir` format (`attempt_1`, `attempt_2`, ...). `test_graph.py` TestFullPassPath checks 5 artifact files exist in `attempt_1/`. TestMaxAttemptsStop checks `attempt_1/`, `attempt_2/`, `attempt_3/` directories exist and contain `se_prompt.txt`. **However**: no test verifies the complete set of expected artifacts for each failure mode. The run-level artifacts (`work_order.json`, `run_summary.json` inside `<out>/<run_id>/`) are never checked because graph tests bypass `run.py`. The `work_order.json` artifact written by `run.py` line 69 is never tested.

### Artifact JSON structure and stable fields
**Weakly Covered.**
`test_graph.py` TestArtifactForensics checks attempt record keys and types from **graph state** (not from disk). `test_graph.py` TestArtifactForensics checks `write_result.json` keys from disk for a scope violation, which is good. `test_graph.py` TestAcceptanceFailureAndRollback reads `failure_brief.json` and checks the `stage` key. **However**: `run_summary.json` JSON shape is never validated. `verify_result.json` content is never validated — it could be an empty list or malformed and tests would pass. `acceptance_result.json` content is never validated. `proposed_writes.json` content is never validated from disk. The "run_summary_keys" test (line 314) checks `final["attempts"][0]` from graph state, NOT from the actual file on disk — these are different things and could diverge.

### FailureBrief.stage taxonomy and routing
**Weakly Covered.**
`test_schemas.py` validates all 8 stage values are accepted and invalid values are rejected. Node tests produce `exception`, `llm_output_invalid`, `write_scope_violation`, `stale_context`, `write_failed`, `verify_failed`, `acceptance_failed`. The `preflight` stage exists in `ALLOWED_STAGES` but is never produced by any production code and never tested as a real output. **However**: routing for each stage through the full graph is only tested for `llm_output_invalid` (SE → finalize) and `acceptance_failed` (SE → TR → PO → finalize). The other stages' routing through the graph is tested only at the unit level via `_route_after_se`/`_route_after_tr` — this does not verify that the routing functions are correctly wired into the graph via `add_conditional_edges`.

### Retry and max-attempts behavior
**Weakly Covered.**
`test_graph.py` TestMaxAttemptsStop verifies stopping after `max_attempts=3` with `llm_output_invalid`. Attempt index incrementing is tested (`indices == [1, 2]`). **However**: retry after a write-applying failure (verify_failed, acceptance_failed with max_attempts > 1) is not tested at graph level — only acceptance_failed with max_attempts=1 is tested (no retry occurs). The test for retry with `max_attempts=3` only exercises the `llm_output_invalid` path, which means TR and PO are never invoked during retry. Whether the retry loop correctly resets state, re-reads context files, and starts from a clean baseline after a rollback is untested.

### Git rollback semantics
**Covered (with gaps).**
`test_workspace.py` tests rollback restores files, removes untracked, and leaves repo clean — using real git. `test_graph.py` TestAcceptanceFailureAndRollback verifies rollback through the full graph: checks `is_clean(repo)` and file content matches original. **However**: rollback after `verify_failed` through the graph is untested (writes were applied, verify fails — does rollback actually happen?). Rollback after `write_scope_violation` through the graph is untested (no writes applied — rollback should be safe no-op). The spec says `git clean -fd` but the implementation uses `git clean -fdx` — no test distinguishes between these flags, and the behavioral difference (removing gitignored files) is not exercised.

### Verification vs acceptance distinction
**Weakly Covered.**
`test_nodes.py` TestPONode tests verify failure and acceptance failure separately. `test_nodes.py` TestGetVerifyCommands tests the `scripts/verify.sh` vs fallback logic. **However**: no test verifies that when verify fails, acceptance is NOT run. The PO node returns early on verify failure with `acceptance_results: []`, but no test asserts `len(result["acceptance_results"]) == 0` on the verify_failed path. The fallback verification commands (`compileall`, `pip --version`, `pytest -q`) are never exercised through the graph — all graph tests create a `verify.sh`.

### Preflight rejection behavior
**Covered.**
`test_cli.py` exercises: not a git repo (exit 1), dirty repo with untracked file (exit 1), dirty repo with staged change (exit 1), outdir inside repo (exit 1), invalid work order JSON (exit 1), missing work order (exit 1). `test_preflight_does_not_modify_repo` verifies no filesystem changes after rejection. This is the strongest area of the test suite.

### Unhandled exception path (exit code 2)
**Not Covered.**
`run.py` lines 111-155 implement an emergency handler: best-effort rollback, emergency `run_summary.json` with `"ERROR"` verdict, `sys.exit(2)`. None of this is tested. If `graph.invoke()` raises an exception (e.g., LangGraph bug, state corruption), the handler is the only thing standing between the user and a completely invisible failure. It also has a nested try/except for rollback failure (lines 121-128) that prints manual recovery instructions — also untested.

---

## 2. Over-Mocking and Unrealistic Tests

### test_llm.py — `sys.modules["openai"]` injection
**File**: `test_llm.py`, lines 73-84.
**Mocked component**: The entire `openai` module is replaced in `sys.modules` with a MagicMock.
**What it hides**: The test verifies that `complete()` calls `client.chat.completions.create()`, but the mock is so deep that it cannot detect: wrong import paths, version-incompatible API calls, incorrect timeout wiring to the httpx transport, or the actual structure of an OpenAI response object. The cleanup logic (`del sys.modules["openai"]`) is fragile — if the test fails between injection and cleanup, subsequent tests could see a mock openai module. This test provides false confidence that the OpenAI integration works.

### test_graph.py — `factory.llm.complete` patch in all graph tests
**File**: `test_graph.py`, all test classes.
**Mocked component**: `factory.llm.complete` is patched to return pre-constructed JSON strings.
**What it hides**: This is the correct patch point and avoids network calls. However, because ALL graph tests use this patch, the boundary between SE prompt construction and LLM invocation is never tested end-to-end. If `se_node` passed wrong kwargs to `llm.complete()` (e.g., wrong `timeout` parameter name), no test would detect it. The mock also always succeeds or raises — it never returns a valid JSON response that happens to violate WriteProposal constraints (e.g., empty writes list, oversized content), which would be a realistic LLM failure mode.

### test_nodes.py — `_atomic_write` patch for write failure
**File**: `test_nodes.py`, line 292.
**Mocked component**: `factory.nodes_tr._atomic_write` is patched to raise `OSError`.
**What it hides**: The test verifies the TR node handles write failures with the correct stage, but it never tests whether `_atomic_write`'s cleanup logic (temp file removal on failure, line 38-42 of `nodes_tr.py`) actually works. If the temp file cleanup were broken, orphan `.tmp` files would accumulate in the repo, potentially polluting future runs. No test exercises the `_atomic_write` failure-and-cleanup path with a real filesystem.

### test_safety.py — Network guard is test-scoped, not session-scoped
**File**: `test_safety.py`, lines 24-58.
**Mocked component**: `socket.socket.connect` via `monkeypatch`.
**What it hides**: The `monkeypatch` in `test_socket_connect_blocked` only applies during that single test function. It does NOT protect other tests from accidentally making network calls. The `test_llm_complete_does_not_call_network` test similarly only guards within its own scope. If a future test accidentally constructs a real OpenAI client with a valid key (e.g., from env leakage), it would make a real network call and no safety net would catch it. A session-scoped `autouse` fixture in `conftest.py` would be required for actual protection.

### test_graph.py — graph tests bypass run.py
**File**: `test_graph.py`, all tests.
**Mocked component**: N/A — but `run.py` is implicitly skipped.
**What it hides**: All graph tests call `graph.invoke(initial_state)` directly, constructing the initial state manually. This means: `run.py`'s work order loading → validation → preflight → state construction → graph invocation → summary writing pipeline is never tested as a unit. The manual state construction in tests could mask bugs in `run.py`'s state initialization. For example, if `run.py` forgot to set `attempt_index: 1`, the graph tests would still pass because they set it manually.

---

## 3. "Delete This Code" Thought Experiments

### 1. Remove rollback from `_finalize_node` (graph.py line 130)

If `rollback(repo_root, baseline)` were deleted from `_finalize_node`:
- **`test_graph.py` TestAcceptanceFailureAndRollback WOULD fail** — it checks `is_clean(repo)` and file content after acceptance failure. This is the one test that catches rollback removal.
- **But only for acceptance_failed.** Rollback after `verify_failed` through the full graph is untested. If someone added a condition like `if verdict == "FAIL" and failure_brief["stage"] == "acceptance_failed"` before rollback, only the acceptance path would be protected.

### 2. Remove `write_result.json` emission from TR (nodes_tr.py lines 184-186)

If `save_json({"write_ok": True, ...}, ...)` were deleted from the success path:
- **`test_graph.py` TestFullPassPath WOULD fail** — it checks `os.path.isfile(ARTIFACT_WRITE_RESULT)`.
- **But** it only checks existence. If the content changed (e.g., `write_ok` became a string instead of bool, or `touched_files` were omitted), no test would fail.
- The **failure-path** `write_result.json` is checked for keys in `test_write_result_keys`, so the failure path is better protected than the success path.

### 3. Remove the stale-context hash check from TR (nodes_tr.py lines 147-164)

If the entire hash-comparison loop were deleted:
- **`test_nodes.py` TestTRNode.test_stale_context WOULD fail** — it directly calls `tr_node` with a wrong hash and expects `stage="stale_context"`.
- **But no graph-level test would fail.** All graph tests provide correct hashes via `make_valid_proposal_json()`. A graph-level regression where stale context detection is wired incorrectly (e.g., finalize doesn't rollback after stale_context) would go undetected.

### 4. Remove the "all hashes checked before any writes" ordering guarantee (nodes_tr.py)

Currently, TR checks ALL base hashes (lines 147-164) BEFORE applying ANY writes (lines 169-181). If someone interleaved check-then-write per file (check file A → write file A → check file B → fail stale_context), file A would be modified before the stale detection on file B. **No test would fail.** The test `test_stale_context` uses a single-file proposal, so interleaving is indistinguishable from batch-checking. No multi-file stale_context test exists.

### 5. Stop writing `se_prompt.txt` in SE node (nodes_se.py lines 179-181)

If the prompt file write were removed:
- **`test_nodes.py` test_prompt_file_created WOULD fail** — it explicitly checks the file exists.
- **`test_graph.py` TestMaxAttemptsStop WOULD fail** — it checks `os.path.isfile(ARTIFACT_SE_PROMPT)` for each attempt.
- This behavior is well-locked. Good.

### 6. Remove `run_summary.json` writing from run.py (lines 174-175)

If `save_json(summary_dict, summary_path)` were removed:
- **No test would fail.** Graph tests don't go through `run.py`. CLI tests only test preflight rejections and never reach the summary-writing code. The `run_summary.json` output — the primary artifact consumed by downstream tooling — is completely unprotected.

### 7. Change finalize to NOT reset per-attempt fields (graph.py lines 141-147)

If finalize stopped resetting `proposal`, `touched_files`, `write_ok`, etc. between attempts:
- **Probably no test would fail.** The retry test (`TestMaxAttemptsStop`) uses `llm_output_invalid` which never sets these fields meaningfully. A retry after a real write (verify_failed with `max_attempts > 1`) is not tested at graph level. Stale proposal data from attempt 1 leaking into attempt 2's state would go undetected.

---

## 4. Artifact Forensics Weaknesses

### Existence-only assertions
The following artifact checks in `test_graph.py` TestFullPassPath are **existence-only** with no content validation:

| Artifact | Assertion | What could silently drift |
|---|---|---|
| `se_prompt.txt` | `os.path.isfile(...)` | Prompt format, context file inclusion, forbidden list |
| `proposed_writes.json` | `os.path.isfile(...)` | JSON shape, summary field, writes array structure |
| `write_result.json` | `os.path.isfile(...)` on PASS path | `write_ok` value, `touched_files` list, `errors` list |
| `verify_result.json` | `os.path.isfile(...)` | CmdResult shape, exit codes, stdout/stderr paths |
| `acceptance_result.json` | `os.path.isfile(...)` | Same as verify_result.json |

Only `failure_brief.json` is read and checked for `stage` (in TestAcceptanceFailureAndRollback). Only `write_result.json` on the failure path has its keys validated (in TestArtifactForensics).

### Missing assertions on JSON keys/types
- **`run_summary.json`**: Never loaded from disk. The "run_summary_keys" test checks `final["attempts"][0]` from graph state — this is the attempt record inside the LangGraph state dict, NOT the file. `run.py` constructs `summary_dict` independently (lines 163-172) with keys like `run_id`, `work_order_id`, `verdict`, `total_attempts`, `baseline_commit`, `repo_tree_hash_after`, `config`, `attempts`. None of these are validated by any test.
- **`verify_result.json`**: Written by PO node. Contains a list of `CmdResult.model_dump()` dicts. No test ever reads this file.
- **`acceptance_result.json`**: Same situation as verify_result.json.
- **`proposed_writes.json`**: Written by SE node. Contains `WriteProposal.model_dump()`. Never read by any test.

### Artifacts per failure mode — untested combinations
No test verifies the exact artifact set for these failure scenarios:

| Failure mode | Expected artifacts | Tested? |
|---|---|---|
| `llm_output_invalid` | se_prompt.txt, raw_llm_response.json, failure_brief.json | Partially (node test checks existence of raw_llm_response and failure_brief) |
| `write_scope_violation` | se_prompt.txt, proposed_writes.json, write_result.json, failure_brief.json | Only write_result.json keys tested (via direct node call) |
| `stale_context` | se_prompt.txt, proposed_writes.json, write_result.json, failure_brief.json | Not tested for artifacts |
| `verify_failed` | se_prompt.txt, proposed_writes.json, write_result.json, verify_result.json, failure_brief.json | Not tested at graph level |
| `write_failed` | se_prompt.txt, proposed_writes.json, write_result.json, failure_brief.json | Not tested for artifacts |
| PASS | se_prompt.txt, proposed_writes.json, write_result.json, verify_result.json, acceptance_result.json | Existence-only |
| Unhandled exception (run.py) | run_summary.json with ERROR verdict | Not tested at all |

### Crash-resilience artifacts
SE writes `failure_brief.json` eagerly (write-ahead, nodes_se.py lines 198, 222) before finalize overwrites it (graph.py line 110). TEST_PLAN.md acknowledges this ("Tests must not assume single-write semantics"), but no test verifies that the write-ahead and finalize-written versions are consistent, or that the write-ahead artifact survives if the process crashes between SE and finalize.

---

## 5. Retry and LLM-Feedback Blind Spots

### Retry prompt integrity
`test_nodes.py` `test_previous_failure_brief_in_prompt` (line 184) is the **only test** for retry feedback. It verifies:
- `"Previous Attempt FAILED" in prompt` (**yes**)
- `"verify_failed" in prompt` (**yes**)

It does **NOT** verify:
- The error excerpt (`"test failed"`) appears in the prompt
- The command (`"pytest"`) appears in the prompt
- The constraints_reminder (`"fix tests"`) appears in the prompt
- The exit code (`1`) appears in the prompt

If `_build_prompt` were changed to include only the stage name and drop all other FailureBrief fields, this test would still pass. The LLM would receive a useless retry prompt with no actionable information.

### Different failure stages produce different prompts
Only `verify_failed` is tested as a retry trigger. The following are untested:
- Does `stale_context` retry prompt include the hash mismatch details?
- Does `write_scope_violation` retry prompt include the out-of-scope file paths?
- Does `acceptance_failed` retry prompt include the failing command and its stderr?
- Does `exception` retry prompt include the exception message?

If the prompt construction had a bug that only surfaced for certain stages (e.g., `command` field is None for some stages and the template crashes), no test would catch it.

### FailureBrief clearing behavior
After a failed attempt, `_finalize_node` keeps `failure_brief` in state for the retry SE prompt. After a successful attempt, PO returns `failure_brief: None`, so finalize should clear it. **No test verifies the multi-attempt scenario where attempt 1 fails and attempt 2 succeeds.** If finalize accidentally kept the stale failure_brief from attempt 1 in the final state, no test would detect it.

### Context re-reading on retry
After rollback, the repo is at `baseline_commit`. SE should re-read context files (with their original hashes) on retry. No test verifies that context files are re-read from the rolled-back state rather than cached from the previous attempt.

---

## 6. False Confidence Index

1. **`run.py` is untested for normal execution.** Graph tests bypass it; CLI tests only test rejections. A maintainer sees "graph integration tests" and "CLI tests" and assumes the end-to-end path is covered. It is not. `run_summary.json`, exit codes for PASS/FAIL, stdout messages — all untested.

2. **Artifact existence checks masquerade as content checks.** TestFullPassPath asserts 5 artifact files exist. This looks thorough in a test report, but the files could contain `{}` or `null` and every assertion would pass.

3. **Node-level tests create an illusion of path coverage.** Every failure stage is tested at the node level, so a coverage report shows high line coverage. But the graph routing, finalize behavior, and rollback for most of these stages are only exercised in isolation — not through the actual graph.

4. **TEST_PLAN.md lists "Retry with feedback" as tested.** The plan says "Second SE prompt contains failure_brief from first attempt." The actual test only checks two substrings. The semantic content of the retry prompt (error details, command info, exit codes) is unverified.

5. **The network guard provides false safety.** `test_safety.py` makes it look like the suite prevents network calls. It only prevents them within its own test function. Other tests rely on `OPENAI_API_KEY` being absent, which is a weaker guarantee.

6. **TestArtifactForensics checks graph state, not disk.** The "run_summary_keys" test name suggests it validates the run summary JSON file. It actually validates `final["attempts"][0]` from in-memory graph state. A bug in `run.py`'s summary serialization would be invisible.

7. **Rollback is tested for one path, assumed for all.** TestAcceptanceFailureAndRollback proves rollback works for acceptance failure. A maintainer extrapolates this to all failure paths. But rollback after `verify_failed` or `write_failed` through the graph is untested. If rollback were conditional on the failure stage, only the `acceptance_failed` path would be protected.

8. **`compute_run_id` is tested but never verified in context.** Unit tests prove it's deterministic and produces 16-char hex. But no test verifies that the artifact directory name actually uses this run_id. The graph tests use hardcoded `run_id="testrun"` or `run_id="test"`.

9. **Clean-tree preflight feels comprehensive.** Tests cover untracked files, staged changes, and non-git repos. But there is no test for unstaged modifications to a tracked file without staging. (`test_dirty_repo_staged` stages the change; `test_dirty_repo_untracked` creates a new file. Neither tests a modified-but-unstaged tracked file through the CLI specifically — though `test_workspace.py` `test_unstaged_change` does cover the workspace helper.)

10. **The write-ahead pattern is documented but untested.** TEST_PLAN.md says "SE writes `failure_brief.json` eagerly; finalize overwrites it." No test verifies the write-ahead artifact exists before finalize runs, or that it matches what finalize ultimately writes. If the write-ahead were removed, the crash-resilience guarantee would silently disappear.

---

## 7. High-Value Missing Tests (Max 8)

### 1. End-to-end CLI test: PASS verdict through `run.py`
**Behavior locked**: Exit code 0, stdout contains `"Verdict: PASS"` and `"Run summary:"`, `run_summary.json` exists on disk with correct keys (`run_id`, `work_order_id`, `verdict`, `total_attempts`, `baseline_commit`, `repo_tree_hash_after`, `config`, `attempts`), `work_order.json` artifact exists.
**Why existing tests don't cover it**: Graph tests bypass `run.py`. CLI tests only exercise preflight rejections.
**Requires**: Real git repo, patched LLM (either via env manipulation or subprocess with monkeypatch). Could potentially be done by running `run.py:run_cli` directly with a mock-patched `factory.llm.complete` — no subprocess needed.

### 2. Exit code 2 emergency handler test
**Behavior locked**: When `graph.invoke()` raises, exit code is 2, stderr contains `"ERROR"`, `run_summary.json` exists with `verdict: "ERROR"`, error traceback is captured, best-effort rollback is attempted.
**Why existing tests don't cover it**: No test forces the graph to raise an exception at the `run.py` level.
**Requires**: Patch `factory.graph.build_graph` or `graph.invoke` to raise. Can be unit-level with patching.

### 3. Verify-failed rollback through full graph
**Behavior locked**: When writes are applied and verification fails, the repo is rolled back to baseline (clean tree, original file content), failure_brief has `stage: "verify_failed"`, and artifacts survive in out_dir.
**Why existing tests don't cover it**: Only `acceptance_failed` rollback is tested at graph level. Verify failure is tested at node level only.
**Requires**: Real git repo, patched LLM, a verify.sh that fails. Similar setup to TestAcceptanceFailureAndRollback but with failing verify.

### 4. Multi-file stale_context atomicity test
**Behavior locked**: When proposal writes files A and B, and file B has a stale hash, NEITHER file is written. The "all checks before any writes" invariant is preserved.
**Why existing tests don't cover it**: `test_stale_context` uses a single-file proposal. No test uses a multi-file proposal where the second file has a bad hash.
**Requires**: Real git repo with two committed files, proposal that writes both with second file having wrong hash. Unit-level (direct tr_node call). Verify file A is unchanged.

### 5. Retry succeeds after first-attempt failure (full graph)
**Behavior locked**: Attempt 1 fails (e.g., verify_failed), rollback occurs, attempt 2 succeeds (different LLM response), verdict is PASS, both attempt dirs exist, attempt 1 has failure_brief, attempt 2 does not, `repo_tree_hash_after` is set.
**Why existing tests don't cover it**: The only multi-attempt graph test uses `llm_output_invalid` (never reaches TR/PO). No test exercises a retry that changes the LLM response between attempts.
**Requires**: Real git repo, patched LLM with `side_effect=[bad_response, good_response]`.

### 6. `run_summary.json` content validation
**Behavior locked**: The file has exactly the keys: `run_id` (str), `work_order_id` (str), `verdict` (str, one of PASS/FAIL), `total_attempts` (int), `baseline_commit` (str, 40-char hex), `repo_tree_hash_after` (str|null), `config` (dict with specific keys), `attempts` (list of attempt records).
**Why existing tests don't cover it**: `run_summary.json` is never read from disk. "run_summary_keys" test checks in-memory graph state.
**Requires**: Can be unit-level by calling `run_cli` with patched LLM and reading the file.

### 7. Acceptance not run when verify fails (PO node)
**Behavior locked**: When global verification fails, the PO node returns immediately without running acceptance commands. `acceptance_results` is empty.
**Why existing tests don't cover it**: `test_verify_failure` checks `stage == "verify_failed"` but does not assert `len(result["acceptance_results"]) == 0`. If PO were changed to run acceptance even after verify failure, no test would break.
**Requires**: Unit-level. Call `po_node` with failing verify, assert `acceptance_results == []`.

### 8. Session-scoped network guard conftest fixture
**Behavior locked**: No test in the entire session can make a real network call, regardless of environment variables.
**Why existing tests don't cover it**: Current network guard is test-local. A leaked `OPENAI_API_KEY` in CI env could cause accidental API calls in graph tests that patch at the wrong level.
**Requires**: `autouse=True, scope="session"` fixture in `conftest.py` that patches `socket.socket.connect`.

---

## 8. Final Recommendation

**"Test suite provides false confidence and must be strengthened before further work."**

The suite has good unit-level coverage of individual components (schemas, hashing, path helpers, workspace), but the integration boundary between `run.py` and the graph is completely untested. The primary output artifact (`run_summary.json`) is never validated from disk. The emergency exit-code-2 handler — the system's last safety net — has zero test coverage. Most artifact assertions check existence rather than content, meaning artifacts could corrupt silently. A refactoring that changes `run.py`'s summary format, breaks graph wiring for uncommon failure paths, or removes the emergency handler would pass the entire suite. The missing tests identified in Section 7 — particularly items 1, 2, and 6 — must be added before this suite can be trusted to protect against regressions.
