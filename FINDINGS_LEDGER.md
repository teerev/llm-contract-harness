# Findings Ledger — Compiled from Audit A (GPT-5.3 Codex) + Audit B (Opus 4.6)

---
ID: AUD-01
Title: TOCTOU symlink escape during TR node writes
Claim:
  A directory component within the repo can be replaced with a symlink between the
  is_path_inside_repo check and the _atomic_write call, causing the write to
  escape the repo root.
Reported by:
  - Audit A: yes (Attack Surface 1, raceful symlink variant; rated CRITICAL)
  - Audit B: yes (E.2; rated LOW)
Agreement:
  partial — both audits identify the same TOCTOU gap between check and write.
  They disagree on severity: A rates CRITICAL (any scope-safety violation is
  critical); B rates LOW (requires concurrent adversarial FS access, outside
  normal single-process operation).
Confidence:
  medium — the race window is real and code-verified, but exploitation requires
  a concurrent process with write access to the repo filesystem during factory
  execution, which is outside the standard operational model.
Affected code:
  - factory/nodes_tr.py::tr_node (path-safety check loop vs write loop)
  - factory/util.py::is_path_inside_repo
  - factory/nodes_tr.py::_atomic_write
Exploit / failure scenario:
  Concurrent process replaces repo_root/src/ with a symlink to /etc/ after
  is_path_inside_repo resolves src/target.py as safe. The subsequent
  _atomic_write follows the symlink and writes to /etc/target.py.
Impact:
  Scope-safety invariant (INV-04 / F2) violated — arbitrary out-of-repo write.
Minimal fix (wrapper-only):
  Re-resolve realpath immediately before each _atomic_write and re-verify
  containment. Pass the resolved path from the check to the write. Optionally
  use O_NOFOLLOW on the final path component.
Regression test:
  test_tr_detects_symlink_flip in tests/factory/test_nodes.py — monkeypatch
  filesystem to swap a directory for a symlink between check and write phases;
  assert TR returns scope failure.
Notes:
  Severity disagreement is legitimate. Under SEVERITY GUIDANCE, this requires
  concurrent FS manipulation (not LLM-only), so CRITICAL may overstate.
  Effective severity: HIGH.

---
ID: AUD-02
Title: TOCTOU between hash-check loop and write loop in TR
Claim:
  The TR node performs all base-hash checks in one loop, then all writes in a
  second loop. An external process can modify a file between these loops,
  violating the stale-context guarantee.
Reported by:
  - Audit A: yes (Attack Surface 2; rated HIGH)
  - Audit B: no (B marks F3 "all hashes checked before any write" as ENFORCED)
Agreement:
  disagree — A treats the two-loop structure as a TOCTOU vulnerability. B treats
  it as correct by design (the factory is the sole writer and the check-all-
  before-write-all pattern is intentional for atomic failure decisions).
Confidence:
  medium — the race window is real (no lock or recheck between loops), but
  exploitation requires an external process modifying the repo during factory
  execution, which violates the factory's operational assumption of sole access.
Affected code:
  - factory/nodes_tr.py::tr_node (hash-check loop, then write loop)
Exploit / failure scenario:
  External process modifies file X between the hash-check loop (which confirms
  X's hash matches) and the write loop (which overwrites X). The write succeeds,
  silently clobbering the concurrent change.
Impact:
  Stale-context invariant (INV-05) violated — factory overwrites concurrent
  changes without detection.
Minimal fix (wrapper-only):
  Re-check each file's hash immediately before its _atomic_write; fail on
  mismatch.
Regression test:
  test_tr_detects_prewrite_hash_change in tests/factory/test_nodes.py —
  monkeypatch sha256_file to return a different hash on second call for a target
  file; assert TR returns stale_context failure.
Notes:
  B considers F3 ENFORCED because loop ordering is correct and the factory is
  the sole writer. A's threat model includes external concurrent mutation.
  Disagreement hinges on scope of "adversarial filesystem state."

---
ID: AUD-03
Title: verify_exempt bypass via planner-controlled field
Claim:
  When verify_contract is absent or falsy, the planner does not overwrite
  verify_exempt, allowing an LLM-generated value of verify_exempt=true to pass
  through. The factory trusts this field and skips global verification.
Reported by:
  - Audit A: yes (Executive Summary bullet 1, Attack Surface 10; rated CRITICAL)
  - Audit B: no (B marks F8 "verify_exempt skips verify.sh" as ENFORCED,
    auditing only the factory's behavior, not the planner's computation)
Agreement:
  disagree — A identifies a planner-side gap in verify_exempt computation. B
  verified the factory correctly implements the exempt flag but did not audit
  whether the planner correctly computes or overwrites the flag.
Confidence:
  high — code path is deterministic and verifiable; compiler.py only computes
  verify_exempt when verify_contract is truthy; otherwise the LLM-provided
  value persists into the emitted WO.
Affected code:
  - planner/compiler.py::compute_verify_exempt (conditional recompute)
  - factory/nodes_po.py::po_node (trusts verify_exempt from WO)
Exploit / failure scenario:
  LLM output includes verify_exempt: true for all WOs and omits
  verify_contract. Planner validation does not overwrite verify_exempt (no
  contract to compute from). Factory honors the exempt flag, running only
  compileall instead of full verification. Acceptance commands execute on
  unverified code.
Impact:
  Verify-gate invariant (INV-06) violated — acceptance runs without global
  verification. This is triggered solely by LLM output, with no concurrent FS
  access or special conditions needed.
Minimal fix (wrapper-only):
  In planner/compiler.py, after validation, always overwrite verify_exempt for
  all WOs: compute from valid verify_contract if present, else force
  verify_exempt=False. Never trust the incoming field value.
Regression test:
  test_verify_exempt_overwritten_by_compiler in tests/planner/test_compile_loop.py
  — submit WO payload with verify_exempt: true and missing verify_contract;
  assert output WO has verify_exempt: false.
Notes:
  Meets CRITICAL criteria: deterministic guarantee violated without requiring
  anything beyond adversarial LLM output. B's gap was auditing only the
  factory's response to the flag, not the planner's computation of it.

---
ID: AUD-04
Title: Non-atomic factory artifact writes (save_json)
Claim:
  factory/util.py::save_json uses bare open() + json.dump(), not the atomic
  tempfile + fsync + os.replace pattern. A crash mid-write corrupts artifacts
  including run_summary.json.
Reported by:
  - Audit A: yes (Attack Surface 9; rated MEDIUM)
  - Audit B: yes (Finding #1, E.9; rated HIGH)
Agreement:
  agree — both audits identify the same non-atomic write pattern and contrast
  it with the planner's _atomic_write and factory's _atomic_write in TR.
Confidence:
  high — code path is trivially verifiable; save_json does a bare open/write
  with no atomic-replace semantics.
Affected code:
  - factory/util.py::save_json
  - All callers: nodes_tr.py (write_result), nodes_po.py (verify_result,
    acceptance_result), graph.py (failure_brief), nodes_se.py (proposed_writes,
    failure_brief), run.py (work_order, run_summary, run_config)
Exploit / failure scenario:
  Factory run completes with PASS verdict. save_json begins writing
  run_summary.json. Process killed (kill -9, OOM-killer, power loss) mid-write.
  run_summary.json contains truncated JSON. Post-mortem tooling gets
  JSONDecodeError; the PASS verdict is lost.
Impact:
  Artifact integrity invariant (F12) violated — run_summary.json may be
  corrupt or absent after crash.
Minimal fix (wrapper-only):
  Replace save_json implementation with tempfile + fsync + os.replace (same
  pattern as planner/io.py::_atomic_write and factory/nodes_tr.py::_atomic_write).
Regression test:
  test_save_json_atomic_on_crash in tests/factory/test_util.py — monkeypatch
  os.replace to raise OSError after temp file is written; assert original file
  is unchanged and no temp files remain.
Notes:
  B elevates to HIGH because run_summary.json is the final verdict artifact.
  A rates MEDIUM. Both agree the fix is low-risk since the atomic pattern
  already exists in two other locations in the codebase.

---
ID: AUD-05
Title: BaseException escapes all rollback paths
Claim:
  Both run.py's emergency handler and _finalize_node catch only Exception.
  KeyboardInterrupt (and SystemExit, GeneratorExit) during TR writes leaves the
  repo in a partial-write state with no rollback attempted.
Reported by:
  - Audit A: no
  - Audit B: yes (Finding #2, E.5; rated MEDIUM)
Agreement:
  N/A (single-audit finding)
Confidence:
  high — code inspection confirms `except Exception` on run.py line ~111 and in
  _finalize_node. KeyboardInterrupt inherits from BaseException, not Exception.
Affected code:
  - factory/run.py::run_cli (except Exception block)
  - factory/graph.py::_finalize_node
  - factory/nodes_tr.py (except Exception in write loop)
Exploit / failure scenario:
  User presses Ctrl-C during TR writes. First _atomic_write completes (file A
  modified). KeyboardInterrupt propagates through every `except Exception`
  handler unintercepted. Process exits. File A remains modified. No rollback.
  Repo is dirty.
Impact:
  Rollback-completeness invariant (F4) violated — repo left with partial
  writes and no rollback on KeyboardInterrupt.
Minimal fix (wrapper-only):
  In run.py, change `except Exception` to `except BaseException`. Handle
  KeyboardInterrupt -> exit(130), SystemExit -> re-raise after rollback,
  others -> exit(2).
Regression test:
  test_keyboard_interrupt_triggers_rollback in tests/factory/test_graph.py —
  simulate KeyboardInterrupt during graph execution; assert rollback was
  attempted and repo is clean.
Notes:
  Users commonly Ctrl-C long-running factory runs (LLM calls take minutes).
  This is a realistic failure mode, not merely theoretical.

---
ID: AUD-06
Title: Rollback is best-effort on git command failure
Claim:
  Rollback via git reset --hard + git clean -fdx can fail due to index locks,
  permission errors, or full disk. The system warns but does not enforce
  post-rollback cleanliness or mark rollback failure explicitly in artifacts.
Reported by:
  - Audit A: yes (Attack Surface 4; rated MEDIUM)
  - Audit B: yes (E.4; rated LOW-MEDIUM)
Agreement:
  agree — both identify the best-effort nature and double-try pattern
  (finalize + emergency handler).
Confidence:
  high — code path is straightforward; rollback raises RuntimeError on git
  failure; emergency handler catches, warns, and writes ERROR summary.
Affected code:
  - factory/workspace.py::rollback
  - factory/graph.py::_finalize_node
  - factory/run.py::emergency handler
Exploit / failure scenario:
  .git/index.lock left by concurrent process. git reset --hard fails.
  _finalize_node raises RuntimeError. Emergency handler retries rollback
  (same failure). Warning printed. run_summary.json written with ERROR
  verdict. Repo remains dirty with no rollback_failed marker.
Impact:
  Rollback-completeness invariant (INV-08 / F4) degraded — repo may remain
  dirty after repeated rollback failure with no explicit rollback_failed
  status in summary.
Minimal fix (wrapper-only):
  After rollback attempts, call is_clean(). If False, set explicit
  rollback_failed status in run_summary and exit with deterministic
  remediation instructions.
Regression test:
  test_rollback_failure_sets_explicit_status in tests/factory/test_end_to_end.py
  — monkeypatch _git to fail on clean -fdx; assert run_summary contains
  rollback_failed status and non-clean repo warning.
Notes:
  Both audits agree this is best-effort by design. Minor severity
  disagreement (MEDIUM vs LOW-MEDIUM).

---
ID: AUD-07
Title: E105 verify-command ban uses exact string match
Claim:
  The E105 check compares cmd_str.strip() == VERIFY_COMMAND using exact string
  equality. Equivalent spellings ("bash  scripts/verify.sh", "bash
  ./scripts/verify.sh", "/bin/bash scripts/verify.sh") bypass the check.
Reported by:
  - Audit A: yes (Attack Surface 6, INV-07; rated CLOSE CALL)
  - Audit B: yes (P5, G.4; rated LOW)
Agreement:
  agree — both identify the same bypass and note that existing tests document
  it as an accepted design decision.
Confidence:
  high — trivially verifiable by string comparison logic.
Affected code:
  - planner/validation.py::validate_plan_v2 (E105 check)
Exploit / failure scenario:
  LLM emits acceptance_commands: ["bash  scripts/verify.sh"] (double space).
  E105 check does not match. Factory runs verify.sh as an acceptance command,
  conflating the global verify gate with acceptance testing.
Impact:
  Planner invariant P5 partially violated — verify-command variants survive
  to factory execution as acceptance commands.
Minimal fix (wrapper-only):
  Normalize commands via shlex.split before comparison: compare
  shlex.split(cmd_str) == ["bash", "scripts/verify.sh"]; also normalize the
  path component via posixpath.normpath.
Regression test:
  test_e105_catches_equivalent_verify_invocations in
  tests/planner/test_chain_validation.py — submit acceptance commands with
  double-space, ./-prefix, and absolute bash path; assert all produce E105.
Notes:
  Both audits note existing tests document the bypass. Severity is low
  because E105 is a policy check, not a safety gate — the factory's
  sequencing (verify before acceptance) is the primary control.

---
ID: AUD-08
Title: JSON parsing lacks duplicate-key rejection and size limit
Claim:
  All JSON parsers (planner _parse_json, factory parse_proposal_json,
  load_work_order) use stdlib json.loads without duplicate-key rejection or
  payload size limits. Duplicate keys silently resolve to last-value; no
  maximum input size is enforced.
Reported by:
  - Audit A: yes (Attack Surface 7, INV-14; rated HIGH)
  - Audit B: yes (E.7; rated LOW)
Agreement:
  partial — both identify the same gaps. A rates HIGH (ambiguity + DoS vector).
  B rates LOW (duplicate keys lead to E000 validation failure and are not
  exploitable; size is bounded by LLM API max_output_tokens in practice).
Confidence:
  medium — duplicate-key ambiguity is structurally real but B's argument that
  it is not exploitable (last-value produces empty list -> E000) is sound.
  Size DoS is theoretical given current API limits but unguarded if limits
  change.
Affected code:
  - planner/compiler.py::_parse_json
  - factory/llm.py::parse_proposal_json
  - factory/schemas.py::load_work_order
Exploit / failure scenario:
  (Duplicate keys) LLM emits {"work_orders":[{...}],"work_orders":[]}.
  json.loads takes last value -> empty list -> E000 validation error. Not
  exploitable but semantically ambiguous.
  (Size) LLM returns multi-MB JSON; json.loads loads fully into memory.
  Bounded by API token limits in practice.
Impact:
  Parse/validation safety (INV-14) weakened — ambiguous payloads accepted
  without explicit rejection. DoS is theoretical under current API constraints.
Minimal fix (wrapper-only):
  Add shared load_json_strict(raw, max_bytes=10MB) using object_pairs_hook
  to reject duplicate keys, with explicit byte-length check before parsing.
Regression test:
  test_reject_duplicate_json_keys in tests/planner/test_structural_validation.py
  — submit JSON with duplicate "work_orders" key; assert deterministic
  structured error.
  test_parse_json_rejects_oversized in tests/planner/test_compile_loop.py —
  submit 11MB payload; assert ValueError.
Notes:
  B argues duplicate keys are defense-in-depth only (not exploitable). A's
  HIGH rating may overstate practical risk. Size limit is pure
  defense-in-depth under current API constraints.

---
ID: AUD-09
Title: Outdir-inside-repo guard bypasses (temporal race + case sensitivity)
Claim:
  The run.py preflight check preventing the output directory from being inside
  the repo has two bypass vectors: (a) symlink/rename race after the one-time
  realpath check, and (b) case-insensitive filesystem string comparison on
  macOS APFS.
Reported by:
  - Audit A: yes (Attack Surface 3; race vector; rated MEDIUM)
  - Audit B: yes (E.3; case-insensitive vector; rated MEDIUM)
Agreement:
  agree — both identify weaknesses in the same guard via complementary attack
  vectors.
Confidence:
  medium — race vector requires concurrent FS manipulation; case-insensitive
  vector is platform-specific (macOS APFS default).
Affected code:
  - factory/run.py (outdir check, ~line 50)
Exploit / failure scenario:
  (Race) Symlink swaps outdir parent to point inside repo after preflight
  realpath check.
  (Case) On macOS, --repo /tmp/MyRepo --out /tmp/myrepo/artifacts passes
  case-sensitive string comparison; artifacts end up inside repo and are
  destroyed by rollback or pollute the tree hash.
Impact:
  Outdir-containment invariant (INV-11 / F10) violated — artifacts pollute
  repo tree, get destroyed by rollback, or corrupt the tree hash.
Minimal fix (wrapper-only):
  Use os.path.commonpath for robust containment. Revalidate outdir after
  run_dir creation using realpath of existing directories. On macOS, use
  os.path.samefile for equality and realpath-after-creation for prefix check
  (realpath of an existing directory on case-insensitive FS returns canonical
  casing).
Regression test:
  test_outdir_symlink_race_detected in tests/factory/test_cli.py — replace
  outdir parent with symlink to repo between checks; assert abort.
  test_outdir_inside_repo_case_insensitive (macOS-only) in
  tests/factory/test_cli.py — use differently-cased paths; assert rejection.
Notes:
  A focuses on the temporal race. B focuses on the semantic (case) bypass.
  Combined, the guard has both temporal and semantic weaknesses.

---
ID: AUD-10
Title: Planner path normalization gap (posixpath.normpath not applied)
Claim:
  normalize_work_order strips whitespace and deduplicates but does not call
  posixpath.normpath. Paths like "./src/a.py" and "src/a.py" are treated as
  distinct in chain validation but collapse to the same path in the factory
  schema. This causes false validation errors or missed dependency conflicts.
Reported by:
  - Audit A: no
  - Audit B: yes (E.8; rated LOW-MEDIUM)
Agreement:
  N/A (single-audit finding)
Confidence:
  high — code path is deterministic; normalize_work_order visibly lacks
  normpath; factory schemas.py::_validate_relative_path visibly applies it.
Affected code:
  - planner/validation.py::normalize_work_order
  - planner/validation.py::validate_plan_v2 (P6-P10 checks use non-normalized paths)
  - factory/schemas.py::_validate_relative_path (applies normpath)
Exploit / failure scenario:
  LLM emits "./src/a.py" in WO-01 postconditions and "src/a.py" in WO-02
  preconditions. Planner file_state tracks "./src/a.py"; WO-02 checks
  "src/a.py" in file_state -> False -> spurious E101. Valid plan rejected.
Impact:
  Chain validation invariants P6-P10 partially broken — normpath-variant
  paths cause incorrect dependency tracking (false positives or missed
  conflicts). This is a correctness issue, not a safety issue.
Minimal fix (wrapper-only):
  In normalize_work_order, apply posixpath.normpath to all path-bearing
  fields (allowed_files, context_files, precondition/postcondition paths)
  after stripping whitespace and before deduplication.
Regression test:
  test_normalize_work_order_normpath in
  tests/planner/test_structural_validation.py — submit WO with
  "./src/a.py" and "src/a.py" in path fields; assert normalization
  collapses and deduplicates them.
Notes:
  No security invariant is violated. The gap causes false rejections
  (overly strict) or inconsistent dependency tracking.

---
ID: AUD-11
Title: shlex.split failure silently bypasses planner validation gates
Claim:
  When shlex.split raises ValueError on a command with unmatched quotes,
  the planner's E003 (shell operator) and E006 (syntax) checks are silently
  skipped. The malformed command passes planner validation and reaches the
  factory.
Reported by:
  - Audit A: yes (Attack Surface 6, tangentially; rated MEDIUM)
  - Audit B: yes (P3, P4, G.6; rated LOW-MEDIUM)
Agreement:
  agree — both identify the shlex.split failure path. A treats it as part of
  general command normalization. B provides specific per-invariant analysis
  (P3, P4).
Confidence:
  high — existing tests explicitly document the bypass
  (test_shlex_parse_error_skipped, test_helper_returns_none_on_shlex_error).
Affected code:
  - planner/validation.py (E003 shell-operator check, E006 python-c syntax check)
Exploit / failure scenario:
  LLM emits acceptance command: python -c 'print(1 (unmatched quote).
  shlex.split raises ValueError. E003 check skips the command. E006 check
  returns None. No validation error. Factory po_node catches the split
  failure at runtime, but the planner compile gate has been breached.
Impact:
  Planner validation exhaustiveness (INV-10 / P3 / P4) violated —
  structurally invalid commands pass the compile gate.
Minimal fix (wrapper-only):
  On shlex.split ValueError, emit a dedicated error code (e.g., E007)
  instead of continue/None.
Regression test:
  test_shlex_failure_emits_e007 in tests/planner/test_structural_validation.py
  — submit command with unmatched quotes; assert validation error with
  specific code.
Notes:
  Factory runtime catches these at execution time, so no safety gate is
  ultimately bypassed end-to-end. The issue is that the planner compile
  gate is incomplete.

---
ID: AUD-12
Title: Planner validation crashes on malformed input types
Claim:
  Non-dict elements in the work_orders array cause AttributeError (wo.get) in
  validate_plan. Non-dict verify_contract reaches .get() in validate_plan_v2 /
  compute_verify_exempt, causing uncaught exceptions. These crash the planner
  instead of producing structured validation errors.
Reported by:
  - Audit A: yes (Executive Summary bullets 3-4, Attack Surface 5; rated HIGH)
  - Audit B: no (B marks P2 schema compliance as ENFORCED; does not test
    non-dict WO elements at the validate_plan level before Pydantic)
Agreement:
  disagree — A identifies crash paths before Pydantic validation is reached.
  B verified that WorkOrder(**wo) enforces the schema, but this check occurs
  after validate_plan, which crashes on non-dict inputs before reaching it.
Confidence:
  high — code path is verifiable; validate_plan calls wo.get() without an
  isinstance(wo, dict) guard.
Affected code:
  - planner/validation.py::validate_plan (wo.get on non-dict items)
  - planner/validation.py::validate_plan_v2 (verify_contract.get on non-dict)
  - planner/compiler.py::compute_verify_exempt (verify_contract type assumption)
Exploit / failure scenario:
  LLM emits {"work_orders": [42, "x", {"id":"WO-01",...}]}. validate_plan
  calls 42.get() -> AttributeError. Exception propagates to CLI,
  misclassified as API failure instead of structured validation error.
  Retry logic may not engage correctly.
Impact:
  Parse/validation safety invariant (INV-10) violated — crashes instead of
  structured errors; CLI misclassifies the failure type.
Minimal fix (wrapper-only):
  In parse_and_validate, add isinstance(wo, dict) check for each element in
  work_orders. In validate_plan_v2, add isinstance(verify_contract, dict)
  guard. Return structured ValidationError for type mismatches.
Regression test:
  test_non_object_work_order_element_returns_structured_error in
  tests/planner/test_structural_validation.py — submit [42, "x"] in
  work_orders; assert no crash and structured error returned.
  test_invalid_verify_contract_type_is_validation_error in
  tests/planner/test_chain_validation.py — submit verify_contract: [];
  assert validation error, not exception.
Notes:
  B's P2 audit verifies Pydantic enforcement, which occurs downstream of the
  crash site. The crash happens in validate_plan before the Pydantic
  constructor is ever called.

---
ID: AUD-13
Title: Factory path schema allows special values (".", NUL, control chars)
Claim:
  _validate_relative_path in factory/schemas.py does not reject ".", "./",
  NUL bytes (\x00), or non-printable control characters in paths. These cause
  unhandled IsADirectoryError, ValueError, or other exceptions in downstream
  helpers.
Reported by:
  - Audit A: yes (Executive Summary bullet 5, Attack Surface 5; rated HIGH)
  - Audit B: no
Agreement:
  N/A (single-audit finding)
Confidence:
  high — code path is verifiable; _validate_relative_path checks for ..,
  absolute paths, and glob chars but not ".", NUL, or control characters.
Affected code:
  - factory/schemas.py::_validate_relative_path
  - Downstream: factory/nodes_tr.py, factory/nodes_se.py, factory/nodes_po.py
Exploit / failure scenario:
  LLM emits writes[].path = "." in proposal. posixpath.normpath(".") = ".".
  Passes _validate_relative_path. os.path.join(repo_root, ".") = repo_root.
  open(repo_root) raises IsADirectoryError. Exception escapes deterministic
  failure classification.
Impact:
  Validation safety (INV-10) weakened — unhandled exception instead of
  structured failure. No out-of-scope write occurs, but error handling is
  bypassed.
Minimal fix (wrapper-only):
  In _validate_relative_path, reject normalized "." and "./", any \x00, and
  non-printable control characters. Require at least one path segment
  resembling a filename.
Regression test:
  test_special_paths_rejected_by_schema in tests/factory/test_schemas.py —
  submit ".", "./", "a\0b.py" in allowed_files and writes.path; assert
  validation error.
Notes:
  This is an error-handling gap, not a safety bypass. The writes would fail
  with unstructured exceptions rather than deterministic structured errors.

---
ID: AUD-14
Title: Unsandboxed acceptance command execution
Claim:
  Acceptance commands validated by the planner (E003 shell-operator ban, E006
  syntax check) execute with full process privileges in the factory.
  "python -c 'import os; os.system(\"rm -rf /\")'" passes all planner gates
  and runs unsandboxed.
Reported by:
  - Audit A: no (implicitly acknowledged but not flagged as a finding)
  - Audit B: yes (E.6; rated MEDIUM)
Agreement:
  N/A (single-audit finding)
Confidence:
  high — by design; shell=False prevents shell expansion but does not sandbox
  the subprocess.
Affected code:
  - factory/util.py::run_command (subprocess.run with shell=False)
  - factory/nodes_po.py::po_node (acceptance command execution)
Exploit / failure scenario:
  LLM emits acceptance_commands: ["python -c \"import subprocess;
  subprocess.run(['rm','-rf','/'])\""] . E003 sees no bare shell operators.
  E006 confirms valid Python syntax. Factory executes with full privileges.
Impact:
  No deterministic invariant is technically violated (shell=False is enforced;
  syntax is validated). However, the executed subprocess has full ambient
  authority. This is a defense-in-depth gap.
Minimal fix (wrapper-only):
  Add a timeout to all acceptance commands. Optionally, add an allowlist of
  permitted binary basenames. Full sandboxing requires OS-level controls
  outside wrapper scope.
Regression test:
  test_acceptance_command_timeout in tests/factory/test_nodes.py — assert
  acceptance commands respect a configurable timeout.
Notes:
  B correctly identifies this as by-design — acceptance commands are
  arbitrary test commands. The defense is user review of WO files before
  execution. Full sandboxing (containers, seccomp) is out of scope for
  wrapper-only fixes. Not classified as CRITICAL because shell=False is
  enforced (deterministic gate intact); the risk is in the executed
  program's own behavior.

---
ID: AUD-15
Title: Partial TR writes leave dirty repo window before finalize
Claim:
  If the first of N writes in TR succeeds but a subsequent write fails, the
  first file is modified on disk. The repo remains dirty between TR return and
  finalize rollback. Any exception in the inter-node gap (LangGraph routing)
  leaves the repo permanently dirty.
Reported by:
  - Audit A: no (implicitly covered in rollback discussion)
  - Audit B: yes (Finding #10; rated MEDIUM)
Agreement:
  partial — A discusses rollback generally but does not specifically flag the
  inter-node dirty-state window as a distinct vulnerability.
Confidence:
  high — code inspection confirms the write loop is not transactional; partial
  writes persist until finalize runs.
Affected code:
  - factory/nodes_tr.py::tr_node (write loop)
  - factory/graph.py (routing between TR and finalize)
Exploit / failure scenario:
  TR writes file A successfully, then fails writing file B (permissions error).
  TR returns failure_brief. If LangGraph routing or finalize encounters an
  exception before rollback, file A remains modified permanently.
Impact:
  Rollback-completeness invariant (F4) weakened — dirty repo state exists in
  a window between TR return and finalize invocation.
Minimal fix (wrapper-only):
  On any write failure in the TR write loop, immediately attempt to restore
  previously-written files from their base content. Alternatively, ensure the
  finalize -> rollback path has no unguarded exception paths (see AUD-05).
Regression test:
  test_partial_tr_write_triggers_rollback in tests/factory/test_nodes.py —
  simulate second write failure; assert that finalize rollback restores the
  first file.
Notes:
  In normal operation, LangGraph routing is reliable and finalize runs
  promptly. The risk is in the exception gap, which overlaps with AUD-05
  (BaseException escape). Fixing AUD-05 substantially mitigates this.

---
ID: AUD-16
Title: run_work_orders.sh lacks path safety guards and locale pinning
Claim:
  The run_work_orders.sh script does not guard against dangerous --target-repo
  values ("/", empty, home directory), does not require explicit opt-in for
  destructive operations (rm -rf), and uses sort without LC_ALL=C, causing
  locale-dependent work-order execution ordering.
Reported by:
  - Audit A: yes (Fix Plan item 10, Attack Surface 8; rated LOW)
  - Audit B: no (B analyzed ordering in Python code and found it safe; did not
    audit the shell script)
Agreement:
  partial — A identifies script-level issues. B's ordering analysis covers
  Python code only and correctly concludes ordering is safe there. B did not
  analyze the shell script.
Confidence:
  medium — script-level path guards are verifiable; locale-dependent sort
  ordering is real but only affects edge-case filenames.
Affected code:
  - utils/run_work_orders.sh
Exploit / failure scenario:
  User runs script with --target-repo / by mistake; rm -rf could operate on
  root. Separately, under a non-C locale, WO files with certain name
  characters could sort differently, changing execution order.
Impact:
  Script safety — potentially catastrophic if root path passed.
  Ordering — nondeterministic WO execution order under exotic locales.
Minimal fix (wrapper-only):
  Guard dangerous paths ("/", empty, $HOME) with explicit rejection. Require
  --force-init for rm -rf operations. Set LC_ALL=C before sort.
Regression test:
  test_run_work_orders_refuses_dangerous_target_repo in
  tests/factory/test_cli.py — invoke script with --target-repo /; assert
  nonzero exit and no destructive operation. Assert LC_ALL=C sort produces
  stable ordering independent of caller locale.
Notes:
  Python-side ordering is safe per B's thorough analysis (sets for membership,
  sorted() for iteration, canonical_json_bytes with sort_keys=True). The
  shell script is the remaining ordering vector.

---
