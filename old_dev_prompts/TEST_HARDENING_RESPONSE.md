# TEST_HARDENING_RESPONSE.md

## Overall verdict

**Yes — the tests are meaningfully better now.** The new coverage is higher-signal (exercises the real `run_cli()` entrypoint), asserts on **on-disk artifacts** rather than in-memory state, and locks in two critical safety properties: **emergency crash handling (exit code 2 + summary written)** and **rollback atomicity across multi-file writes**.

## What improved (with concrete evidence)

### 1) Real orchestration path is now tested end-to-end

`tests/test_end_to_end.py` adds a PASS-path test that calls `factory.run.run_cli()` directly and patches `factory.llm.complete` to avoid network.

Why this matters:
- Previously, it’s easy for unit/integration tests to pass while `run_cli()` wiring (preflight, run_id/out_dir handling, run summary write, exit behavior) is broken.
- This test asserts **stdout** contains the expected final messaging *and* then loads and validates `run_summary.json` **from disk** (not graph state).

Evidence:
- `tests/test_end_to_end.py::TestEndToEndPassViaCLI::test_pass_path_via_run_cli`
- It verifies top-level summary keys including `run_id` (16 chars), `baseline_commit`, `repo_tree_hash_after`, `attempts`, and the new `config` dict (reflecting the recent R9 work).

### 2) Emergency “crash” path is now locked (exit code 2)

The suite now tests the “graph.invoke blew up” scenario by patching `factory.run.build_graph` so `invoke()` raises.

Why this matters:
- The emergency path is the difference between “repo left dirty, no artifacts, no clue” and “repo rollback attempted + deterministic ERROR summary exists.”

Evidence:
- `tests/test_end_to_end.py::TestEmergencyHandler::test_exit_code_2_on_graph_crash`
- Asserts `SystemExit.code == 2`, stderr contains `"Verdict: ERROR"` + exception message, `run_summary.json` exists with `verdict == "ERROR"`, and repo is clean (`factory.workspace.is_clean`).

### 3) Artifact validation is content-aware (not just “file exists”)

`tests/test_graph.py` hardened its assertions so that on PASS it parses artifacts (`proposed_writes.json`, `write_result.json`, `verify_result.json`, `acceptance_result.json`) and validates their shape and success exit codes.

Why this matters:
- Prevents “garbage JSON artifacts” regressions and catches schema drift (accidental renames/removals of keys) earlier.

Evidence:
- `tests/test_graph.py::TestFullPassPath::test_pass_path` now checks:
  - `proposed_writes.json` has `summary` + non-empty `writes[]` with `path/base_sha256/content`
  - `write_result.json` has `write_ok == True`, `touched_files` non-empty, `errors` empty
  - verify/acceptance results are lists of command-result dicts with `exit_code == 0`
  - `se_prompt.txt` exists and is non-empty

### 4) Multi-write safety/atomicity is exercised at two layers

There are now two complementary tests that cover rollback atomicity and the “hash-check all before write any” invariant:

Evidence:
- `tests/test_end_to_end.py::TestMultiWriteRollback::test_multi_write_acceptance_failure_full_rollback`
  - Writes *two* files via `run_cli`, forces acceptance failure, asserts:
    - both files restored to original content
    - repo is clean
    - `touched_files == ["hello.txt", "second.txt"]` in attempt record
- `tests/test_nodes.py::TestTRNode::test_multi_file_stale_context_no_partial_writes`
  - Builds a proposal with A correct hash, B wrong hash; asserts neither file changes and stage is `stale_context`.

## What I like less / remaining weaknesses

### A) Some assertions are mildly environment-brittle

- `baseline_commit` asserted to be length 40 in `test_end_to_end.py`. That assumes SHA-1 repos; SHA-256 repos would break this despite correct behavior.
  - This is a *test brittleness* risk, not a harness risk.

### B) Test git helper doesn’t check return codes

`tests/conftest.py::_git()` calls `subprocess.run(...)` but does not assert return codes. If a git command fails (e.g., permissions, missing git), failures might show up later in less obvious ways.

### C) The hardening summary claims “No production code changed”

That’s not something a summary can guarantee. It should be treated as a review-time statement verified by diff / CI, not as an invariant.

## Net: are we better off?

**Yes.** The new tests are focused on the highest-value failure modes for a harness like this:
- “Does the real CLI orchestration work?”
- “If orchestration crashes, do we still leave artifacts and attempt rollback?”
- “Do the artifacts have correct structure and content?”
- “Do multi-write attempts roll back cleanly and deterministically?”

These are exactly the places where silent regressions tend to be the most damaging.

## Small follow-ups (optional)

If you want to tighten further without adding much fragility:
- **Relax SHA-1 assumption**: in `test_end_to_end.py`, validate commit hash as hex and length ∈ {40, 64} instead of exactly 40.
- **Assert `_git` success**: in `tests/conftest.py`, raise on nonzero return code in `_git()` so failures are immediate and obvious.
- **Ensure failure excerpts include both streams**: if you care about R12 behavior, add a unit test that runs a command writing to both stdout/stderr and asserts `primary_error_excerpt` includes both labels.

