# TEST_HARDENING_SUMMARY.md

## What was done

Four surgical test improvements addressing the highest-signal findings
from `ADVERSARIAL_TEST_REVIEW.md`. No production code was changed.

### Action 1: E2E CLI PASS test via `run_cli` (Review §1, §6.1, §7.1)

**File**: `tests/test_end_to_end.py` — `TestEndToEndPassViaCLI`

Calls `run_cli()` directly with a real git repo and patched LLM. Exercises
the full path: `run_cli → preflight → build_graph → graph.invoke → SE → TR
→ PO → finalize → summary write → return`. Asserts:

- Exit code 0 (no `SystemExit` raised)
- stdout contains `"Verdict: PASS"` and `"Run summary:"`
- `run_summary.json` loaded from disk (not graph state) with validated keys:
  `run_id` (16-char str), `work_order_id`, `verdict == "PASS"`,
  `total_attempts == 1`, `baseline_commit` (40-char hex),
  `repo_tree_hash_after` (non-null), `config` (dict), `attempts` (list)
- Attempt record from disk: `attempt_index == 1`, `write_ok == True`,
  `failure_brief is None`
- `work_order.json` artifact exists

**Regression locked**: Removing or breaking `run_cli()` logic, summary
writing, or exit-code behavior would fail this test.

### Action 2: Exit code 2 emergency handler (Review §1, §2.7, §7.2)

**File**: `tests/test_end_to_end.py` — `TestEmergencyHandler`

Patches `factory.run.build_graph` to return a mock graph that raises
`RuntimeError` on `invoke()`. Verifies the last-resort safety net:

- `SystemExit` with code 2
- stderr contains `"Verdict: ERROR"` and the exception message
- `run_summary.json` exists with `verdict == "ERROR"`, `total_attempts == 0`,
  `error` field containing the exception message, `error_traceback` present
- Repo is clean (best-effort rollback succeeded)

**Regression locked**: Removing or breaking the emergency handler would
fail this test.

### Action 3: Content-aware artifact assertions (Review §4, §6.2, §7.6)

**File**: `tests/test_graph.py` — `TestFullPassPath` and
`TestAcceptanceFailureAndRollback` modified in-place.

PASS path artifacts upgraded from `os.path.isfile()` to parsed content:

| Artifact | Now asserts |
|---|---|
| `proposed_writes.json` | `summary` (str), `writes` (non-empty list), each write has `path`, `base_sha256`, `content` |
| `write_result.json` | `write_ok == True`, `touched_files` (non-empty list), `errors` (empty list) |
| `verify_result.json` | List of CmdResult dicts, each with `exit_code == 0`, `command`, `duration_seconds` |
| `acceptance_result.json` | List of CmdResult dicts, each with `exit_code == 0`, `command` |
| `se_prompt.txt` | Exists and non-empty |

Acceptance failure path also strengthened: `failure_brief.json` checked for
`exit_code`, `command`, `primary_error_excerpt`, `constraints_reminder`.
`write_result.json` and `verify_result.json` validated. `acceptance_result.json`
checked for non-zero exit code.

**Regression locked**: Writing garbage JSON artifacts or changing artifact
schemas would fail these tests.

### Action 4: Multi-write failure rollback (Review §3.4, §4, §7.4)

**Two tests** covering complementary aspects of the atomicity guarantee:

1. `tests/test_end_to_end.py` — `TestMultiWriteRollback`:
   Two files (`hello.txt`, `second.txt`) written via full `run_cli` path.
   Acceptance fails. Asserts both files restored to original content,
   `git status --porcelain` empty, `is_clean()` true, out-dir artifacts
   survive with correct `touched_files == ["hello.txt", "second.txt"]` and
   `stage == "acceptance_failed"`.

2. `tests/test_nodes.py` — `TestTRNode.test_multi_file_stale_context_no_partial_writes`:
   Proposal writes files A (correct hash) and B (wrong hash). Asserts
   TR rejects with `stale_context` AND file A is unchanged. This locks
   the "all hashes checked before any writes" invariant (Review §3.4).

**Regression locked**: Breaking rollback atomicity or interleaving
hash-check-then-write per file would fail these tests.

## Quality bar verification

| Condition | Met? | Test |
|---|---|---|
| Breaking `run_cli()` logic causes a test failure | Yes | `test_pass_path_via_run_cli` |
| Breaking emergency handler (exit 2) causes a test failure | Yes | `test_exit_code_2_on_graph_crash` |
| Writing garbage JSON artifacts causes a test failure | Yes | Content assertions in `test_pass_path`, `test_rollback_on_acceptance_failure` |
| Breaking rollback atomicity on multi-write causes a test failure | Yes | `test_multi_write_acceptance_failure_full_rollback`, `test_multi_file_stale_context_no_partial_writes` |

## Adversarial findings NOT addressed (and why)

| Finding | Status | Rationale |
|---|---|---|
| Network guard is test-local, not session-scoped (§2.4) | Not addressed | Requires an `autouse=True, scope="session"` fixture that patches `socket.socket.connect`. This is a global side-effect that could interfere with pytest's own networking (e.g., `pytest-xdist`, coverage upload). Low risk in practice since the LLM is always patched and `OPENAI_API_KEY` is not set in tests. |
| Retry-after-write-applying-failure at graph level (§7.5) | Not addressed | Not in the four mandatory actions. The multi-attempt stop test exists; a full retry-success-after-verify-failure test would add significant value but was out of scope. |
| Fallback verify commands never exercised through graph (§1 note) | Not addressed | All graph tests use `verify.sh`. Testing the 3-command fallback through the graph would require a repo without `scripts/verify.sh` that also passes `compileall`, `pip --version`, and `pytest -q`. Fragile across environments. |
| `test_llm.py` `sys.modules` injection fragility (§2.1) | Not addressed | The existing tests work and the fragility is contained. Replacing them with a cleaner mock pattern is desirable but was not one of the four mandatory actions. |

## Test count

- Before: 119 tests
- After: 123 tests (+4 new, 0 deleted)
- All 123 passing in ~9 seconds
- No production code changes
