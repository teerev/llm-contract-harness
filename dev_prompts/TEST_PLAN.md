# TEST_PLAN.md — Unit Test Suite for `./factory`

## Overview

This test suite provides comprehensive, deterministic, fast unit and integration
tests for the factory harness. All tests run without network access. The LLM
call path is always stubbed.

## File Layout

```
tests/
  conftest.py          — shared fixtures (tmp git repo, work orders, LLM stubs)
  test_util.py         — A) pure unit tests for util.py helpers
  test_schemas.py      — A) Pydantic model validation & edge cases
  test_llm.py          — A) LLM module: parse_proposal_json, key handling
  test_workspace.py    — B) git helpers with real temporary repos
  test_nodes.py        — C) SE / TR / PO node tests with patched LLM
  test_graph.py        — C/D) graph integration: full PASS/FAIL paths, routing, artifacts
  test_cli.py          — B) CLI entrypoint, preflight rejections
  test_safety.py       — E) network guard, filesystem write guard
```

## Test Categories

### A) Pure Unit Tests (no git, no subprocess)

| Module          | What's tested                                        | Risk area                     |
|-----------------|------------------------------------------------------|-------------------------------|
| `util.py`       | `truncate()` boundary (at, below, above limit)       | Off-by-one in marker          |
|                 | `sha256_bytes`, `sha256_file` (existing + missing)   | Empty-file hash sentinel      |
|                 | `canonical_json_bytes` determinism                    | Key ordering                  |
|                 | `compute_run_id` determinism + length                 | Hash stability                |
|                 | `save_json` / `load_json` round-trip                  | Sorted keys, trailing newline |
|                 | `split_command` (shlex edge cases)                    | Quoted args, empty string     |
|                 | `normalize_path`, `is_path_inside_repo`               | Traversal attack              |
|                 | `make_attempt_dir` format, `ARTIFACT_*` constants     | Single source of truth        |
| `schemas.py`    | `WorkOrder` path validation (absolute, drive, `..`)   | Injection / escape            |
|                 | `WorkOrder` context_files ⊆ allowed_files             | Subset enforcement            |
|                 | `WorkOrder` acceptance_commands non-empty              | Empty list rejection          |
|                 | `WriteProposal` size limits (per-file, total)         | Boundary values               |
|                 | `FailureBrief` stage validation (all 8 + invalid)     | Stage enum drift              |
|                 | `load_work_order` from file                           | File I/O + validation         |
| `llm.py`        | `parse_proposal_json` (bare JSON, fenced, nested)     | Markdown fence stripping      |
|                 | Missing `OPENAI_API_KEY` → RuntimeError               | Error message exact match     |
|                 | `complete()` patched: returns content correctly       | No real network call          |

### B) Preflight and CLI Tests (tmp repo + subprocess)

| Test                       | What's tested                                     | Observable behavior       |
|----------------------------|---------------------------------------------------|---------------------------|
| `--help` / `run --help`   | Exit code, stdout contains usage                  | Exit code 0               |
| No subcommand              | Prints help, exit code 1                          | Exit code 1               |
| `--max-attempts 0`         | Stderr message, exit code 1                       | Exact message              |
| Not a git repo             | Stderr "not a git repository", exit 1             | Repo unchanged             |
| Dirty repo (untracked)     | Stderr "uncommitted changes", exit 1              | Repo unchanged             |
| Dirty repo (staged)        | Same as above                                     | Repo unchanged             |
| Outdir inside repo         | Stderr "must not be inside", exit 1               | Repo unchanged             |
| Invalid work order JSON    | Stderr "Failed to load", exit 1                   | Repo unchanged             |

### C) Graph and Node Integration Tests (tmp git repo + patched LLM)

| Test                     | Nodes exercised    | Key assertions                                                 |
|--------------------------|--------------------|----------------------------------------------------------------|
| **PASS path**            | SE → TR → PO → fin | Exit 0, verdict PASS, run_summary.json, all artifacts present  |
| **LLM output invalid**  | SE → fin           | Stage `llm_output_invalid`, raw_llm_response.json saved        |
| **LLM exception**       | SE → fin           | Stage `exception`, failure_brief.json write-ahead               |
| **Write scope violation**| SE → TR → fin      | Stage `write_scope_violation`, write_result.json, repo clean    |
| **Stale context**        | SE → TR → fin      | Stage `stale_context`, no files written, repo clean             |
| **Verify failure**       | SE → TR → PO → fin | Stage `verify_failed`, verify_result.json, rollback correct     |
| **Acceptance failure**   | SE → TR → PO → fin | Stage `acceptance_failed`, rollback correct, artifacts remain   |
| **Max-attempts stop**    | SE → fin (×N)      | Stops after N, correct attempt count in summary                 |
| **Retry with feedback**  | SE(1) → SE(2)      | Second SE prompt contains failure_brief from first attempt      |

### D) Artifact Forensics Tests

- JSON keys and types stable for: `run_summary.json`, `write_result.json`,
  `verify_result.json`, `acceptance_result.json`, `failure_brief.json`
- Artifact filenames exactly match `ARTIFACT_*` constants
- Attempt directory naming: `attempt_1`, `attempt_2`, ...
- `se_prompt.txt` always created even on failure

### E) Negative Safety Tests

- **Network guard**: monkeypatch `socket.socket` to raise on connect — confirms
  no test accidentally makes a real network call
- **Filesystem guard**: after preflight-rejection tests, confirm the product
  repo has zero modifications (no new files, no changed files)

## Patching Strategy

The LLM is patched at `factory.llm.complete` — the narrowest stable import path.
This is what `nodes_se.py` calls via `from factory import llm; llm.complete(...)`.
Patching at this level avoids coupling to OpenAI internals.

## Fixture Design

- `init_git_repo(tmp_path)` → creates a real git repo with an initial committed file
- `write_work_order(tmp_path, overrides)` → writes a minimal valid work order JSON
- `make_valid_proposal(repo_root, wo)` → builds a `WriteProposal`-compatible dict
  with correct `base_sha256` values computed from actual file contents
- `run_factory(...)` → invokes `run_cli` with constructed args in a subprocess
  or via direct function call with captured stdout/stderr

## Risk Areas Requiring Special Attention

1. **Rollback correctness**: After failed acceptance, the repo must be byte-identical
   to baseline. Test with `git diff HEAD` and `git status --porcelain`.
2. **Write-ahead artifacts**: SE writes `failure_brief.json` eagerly; finalize
   overwrites it. Tests must not assume single-write semantics.
3. **Attempt indexing**: Starts at 1, increments per attempt. Off-by-one would
   cause artifact dir collisions or premature termination.
4. **`git clean -fdx`**: Removes gitignored files too. Tests that create `.pyc`
   or `__pycache__` inside the repo must expect them to be cleaned.
5. **Empty-bytes sha256**: The sentinel hash for new files. If the LLM uses a
   wrong hash, `stale_context` fires. Tests must use the exact value.
