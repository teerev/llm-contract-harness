# Adversarial Audit of Deterministic Wrapper Code (Planner + Factory)

### A. Executive Summary (max 10 bullets)
- **CRITICAL — verify gate can be bypassed by planner-supplied `verify_exempt`** when `verify_contract` is absent: `planner/compiler.py` only recomputes `verify_exempt` if `verify_contract` is truthy, so attacker-controlled work orders can force lightweight verify (`compileall`) instead of global verify in `factory/nodes_po.py`.
- **CRITICAL — TR has a TOCTOU symlink escape window**: `factory/nodes_tr.py` checks `is_path_inside_repo()` and hashes first, then writes later; an attacker can swap path/parent to symlink after checks and before `_atomic_write()`, enabling out-of-repo write.
- **HIGH — malformed planner JSON can crash validation instead of producing structured errors**: non-dict items in `work_orders` can trigger `AttributeError` (`wo.get`) in `planner/validation.py`, escaping deterministic error handling.
- **HIGH — `verify_contract` is untyped and can crash compile path**: non-dict `verify_contract` reaches `.get(...)` in `validate_plan_v2` / `compute_verify_exempt`, causing uncaught exceptions and misclassified CLI errors.
- **HIGH — path schema allows special values (`"."`, embedded NUL)** leading to unhandled exceptions (`IsADirectoryError`, `ValueError`) in TR/SE/PO helpers; this bypasses normal failure classification paths.
- **MEDIUM — outdir-inside-repo guard is only point-in-time**: `run.py` uses `realpath` preflight, but no later revalidation; symlink/rename races can redirect artifacts into repo after check.
- **MEDIUM — rollback is best-effort, not guaranteed**: failure in `git reset --hard`/`git clean -fdx` leaves possible dirty tree; system warns but does not enforce post-rollback cleanliness before exit.
- **MEDIUM — JSON ambiguity and parser inconsistency remain**: duplicate keys are silently accepted (`json.loads` default) in planner/factory parsers; acceptance-command parsing policy differs between planner checks and factory execution.
- **MEDIUM — artifact writes are not uniformly atomic**: many factory artifacts use plain `save_json` / plain text writes; process interruption can leave truncated artifacts.
- **CLOSE CALL — policy bypass for `E105` is already documented by tests**: exact-string check permits variants like `bash  scripts/verify.sh` or `bash ./scripts/verify.sh`; ban is partial by design.

### B. Deterministic Contract: Threat Model

**Adversarial inputs treated as hostile**
- **Planner JSON**: malformed shape, wrong types, duplicate keys, huge payloads, Unicode edge cases, malicious `verify_exempt` and command strings.
- **Filesystem state**: symlinks, raceful path replacement, directory-vs-file mismatches, permissions, dirty git state, unexpected pre-existing artifacts.
- **Subprocess behavior**: missing binaries, timeouts, nonzero exits, environment/PATH manipulation, external concurrent writers.

**Operational meaning of deterministic correctness**
- **Scope safety**: no write outside `repo_root` regardless of planner/proposal payload.
- **Stale-context safety**: writes apply only against exactly the hashed file state they were checked against.
- **Gate ordering safety**: preconditions before SE, hash/scope before TR writes, verify before acceptance, acceptance only on verify success.
- **Rollback safety**: any failed attempt restores baseline commit and clean working tree.
- **Parse/validation safety**: malformed planner/proposal/work-order JSON is classified into deterministic errors, not crashes/misclassification.
- **Artifact safety**: failure/success artifacts are durable and non-partial enough to audit post-mortem.

### C. Trust Boundary Map (with diagram)

```
[spec/template/repo FS] 
      |
      v
planner.compile_plan()
  -> render prompt
  -> LLM raw text (UNTRUSTED)
  -> _parse_json() (json.loads)
  -> parse_and_validate()
  -> validate_plan_v2()
  -> compute_verify_exempt()
  -> write WO files + manifest
      |
      v
[WO-*.json + manifest] (UNTRUSTED TO FACTORY UNTIL RE-VALIDATED)
      |
      v
factory.run_cli()
  -> load_work_order() (Pydantic)
  -> git preflight
  -> graph: SE -> TR -> PO -> finalize (retry loop)
      SE: preconditions + prompt + proposal parse
      TR: path/scope/hash checks -> writes
      PO: verify -> postconditions -> acceptance
      finalize: record + rollback on failure / tree hash on pass
```

| Step | Input type | Validation/gate | Side effects | Failure behavior |
|---|---|---|---|---|
| Planner read spec/template | File bytes | `open` + UTF-8 decode in `planner/compiler.py` | none | raises exception to CLI |
| Planner LLM output parse | Raw text | `_parse_json()` fence-strip + `json.loads` | writes raw response artifacts | parse errors become `E000` + retry; other type errors may crash later |
| Planner structural validation | `work_orders` objects | `parse_and_validate()` + `validate_plan()` + `WorkOrder(**wo)` | writes validation artifacts | returns structured errors, except untyped element crashes |
| Planner chain validation | normalized WOs + `verify_contract` + repo listing | `validate_plan_v2()` | none | hard errors block emission; malformed `verify_contract` can crash |
| Planner emission | validated manifest/WOs | `check_overwrite` + `write_work_orders` | writes `WO-*.json`, manifest, summaries | atomic per-file, not transactional across whole set |
| Factory WO load | WO JSON file | `load_work_order()` Pydantic model | none | exits 1 on parse/validation error |
| Factory preflight | repo/out paths | `is_git_repo`, `is_clean`, outdir-not-inside-repo check | creates run dir + `work_order.json` | preflight exceptions outside graph are not uniformly summarized |
| SE node | validated WO + repo fs | precondition checks via `os.path.isfile`; proposal parse into `WriteProposal` | writes prompt/proposal/failure artifacts | returns `failure_brief`; certain path edge-cases can throw |
| TR node | proposal writes | duplicate path, allowed scope, `is_path_inside_repo`, base hash checks | atomic writes to repo, write_result artifact | returns failure brief or writes; race windows remain |
| PO node | modified repo | verify commands, postcondition file_exists checks, acceptance command execution | verify/acceptance logs/artifacts | first failure returns `failure_brief` |
| Finalize | attempt state | fail=>rollback, pass=>tree hash | appends attempt record; rollback/git ops | rollback exceptions bubble to emergency handler |
| Emergency handler | graph exception | best-effort rollback + run_summary write | writes ERROR summary | if rollback/summary write fails, warns and exits 2 |

### D. Invariant Table (code-derived)

| Invariant ID | Description (precise) | Enforcement location(s) | Enforcement mechanism | Potential bypass attempt(s) | Status | Evidence |
|---|---|---|---|---|---|---|
| INV-01 | WO ids must be `WO-NN` contiguous from `WO-01` | `planner/validation.py` | `WO_ID_PATTERN` + expected index check (`E001`) | malformed ids/out-of-order list | ENFORCED | `validate_plan()` emits `E001` before emission |
| INV-02 | Paths in WO/proposal must be relative, non-`..`, non-glob | `factory/schemas.py`; planner reuses `WorkOrder` schema | `_validate_relative_path` in Pydantic validators | `../../etc/passwd`, absolute, glob chars | ENFORCED (core traversal) | rejects absolute/drive/`..`/glob; planner `E005` on schema fail |
| INV-03 | Proposal writes must stay within allowed_files | `factory/nodes_tr.py` | normalized touched set subset of normalized allowed set | write extra file in proposal | ENFORCED | `out_of_scope` -> `write_scope_violation` |
| INV-04 | Proposal path must resolve inside repo | `factory/nodes_tr.py` + `factory/util.py` | `is_path_inside_repo(realpath(join(...)))` | direct symlink-to-outside path | PARTIAL | static symlink blocked, raceful swap not blocked |
| INV-05 | Stale context must be detected before writes | `factory/nodes_tr.py` | compare `sha256_file(actual)` to `base_sha256` for all writes before write loop | external process edits after hash check | BROKEN | no lock/recheck between hash loop and write loop |
| INV-06 | Verify should be global gate before acceptance | `factory/nodes_po.py` | verify commands run first; nonzero -> failure_brief and return | planner sets `verify_exempt=true` without contract | BROKEN | `verify_exempt` trusted from WO when planner omits verify contract |
| INV-07 | `bash scripts/verify.sh` must not appear in acceptance commands | `planner/validation.py` | exact `cmd_str.strip() == VERIFY_COMMAND` (`E105`) | `bash  scripts/verify.sh`, `bash ./scripts/verify.sh` | PARTIAL | tests document bypasses as accepted behavior |
| INV-08 | Failed attempts rollback repo to baseline | `factory/graph.py`, `factory/workspace.py`, `factory/run.py` | finalize fail->`rollback`; emergency handler retries rollback | git reset/clean failure | PARTIAL | best-effort only; dirty repo possible after repeated rollback failure |
| INV-09 | Output artifacts should persist for post-mortem | planner I/O and factory util/run | planner uses atomic writes; factory writes JSON/text files | crash mid-write in factory | PARTIAL | `factory.util.save_json` not atomic |
| INV-10 | Malformed planner output should produce structured validation errors | `planner/compiler.py`, `planner/validation.py` | parse error -> `E000` + retries | non-dict list elements, malformed `verify_contract` type | BROKEN | multiple type assumptions can throw uncaught exceptions |
| INV-11 | Outdir artifacts must not be inside repo | `factory/run.py` | `realpath` + prefix check | symlink/rename race after preflight | PARTIAL | only checked once before graph execution |
| INV-12 | Deterministic run identity | `factory/util.py` | `compute_run_id(sha256(canonical_json + baseline))` | key order manipulation | ENFORCED | `canonical_json_bytes(...sort_keys=True)` + baseline hash |
| INV-13 | Planner/factory command execution avoids shell metachar expansion | `factory/util.py`, `factory/workspace.py` | `subprocess.run(..., shell=False)` | shell operator injection (`|`, `;`) | ENFORCED (shell expansion) | shell disabled in command runner and git wrappers |
| INV-14 | JSON parse semantics should be unambiguous | planner `_parse_json`; factory `parse_proposal_json`; `load_work_order` | plain `json.loads`/`json.load` | duplicate keys, NaN/Infinity, huge payload | BROKEN | no duplicate-key rejection or size guard |

### E. Attack Surfaces & Attempted Breaks (deep dive)

#### 1) Path traversal + symlink escape (planner + factory)
- **What I tried**: submit `../../etc/passwd` in `allowed_files` / proposal `writes[].path`, and separately target an in-repo path that is or becomes a symlink to outside repo.
- **Why it might work**: traversal strings or symlink indirection could move write target outside repo.
- **Where it would pass/fail**:
  - Traversal string fails in `_validate_relative_path` (schema validation).
  - Static symlink target fails `is_path_inside_repo` in TR.
  - Raceful symlink swap can occur after `is_path_inside_repo` check and before `_atomic_write`.
- **Actual outcome**:
  - Direct traversal: blocked (good).
  - Static symlink escape: blocked (good).
  - Symlink race: **not blocked**.
- **Severity if real**: **CRITICAL** for raceful variant.
- **Fix (minimal deterministic fix)**: re-resolve and verify realpath immediately before each write and again after temp-file creation; fail if target/parent realpath changed; prefer `openat`/`O_NOFOLLOW`-style no-follow operations for final path.

#### 2) TOCTOU during TR write (hash checked then file swapped)
- **What I tried**: external process modifies file after base-hash loop but before write loop.
- **Why it might work**: TR performs all hash checks first, then all writes; no lock or pre-write recheck.
- **Where it would pass/fail**: `factory/nodes_tr.py` hash loop (step 3) and write loop (step 4).
- **Actual outcome**: stale-context guarantee can be bypassed; write may clobber unobserved concurrent change.
- **Severity if real**: **HIGH**.
- **Fix**: for each file, recheck hash immediately before `_atomic_write`; optionally lock repo or lock target path around check+write critical section.

#### 3) Outdir inside repo edge cases (realpath, symlinked outdir)
- **What I tried**: static outdir under repo, symlinked outdir pointing into repo, and post-check symlink swap.
- **Why it might work**: path checks can be lexical or point-in-time.
- **Where it would pass/fail**: `factory/run.py` preflight outdir check uses `realpath` and prefix comparison once.
- **Actual outcome**:
  - static inside/symlink-inside: blocked by current check.
  - post-check path redirection race: not revalidated.
- **Severity if real**: **MEDIUM-HIGH**.
- **Fix**: use `os.path.commonpath` for robust containment check and revalidate outdir invariants immediately before each artifact write or hold an opened directory fd.

#### 4) Rollback failure (git commands failing, clean -fdx surprises)
- **What I tried**: assume `git reset --hard` / `git clean -fdx` failures (permissions/locks).
- **Why it might work**: rollback is critical but external git failures are possible.
- **Where it would pass/fail**: `factory/workspace.py::rollback`; `factory/graph.py::_finalize_node`; `factory/run.py` emergency handler.
- **Actual outcome**: rollback is attempted twice (finalize and emergency path), but can still fail; system warns and exits with possible dirty repo.
- **Severity if real**: **HIGH**.
- **Fix**: after rollback attempts, explicitly run `is_clean`; if false, mark `rollback_failed` in summary and fail hard with remediation details.

#### 5) Uncaught exception paths (where exceptions skip rollback)
- **What I tried**: non-dict WO entries, malformed `verify_contract` type, path edge values causing `ValueError`/`IsADirectoryError`.
- **Why it might work**: several code paths assume dict/file semantics without defensive type/path checks.
- **Where it would pass/fail**:
  - Planner: `validate_plan` assumes each WO has `.get`.
  - Planner: `validate_plan_v2` assumes `verify_contract` is dict when non-None.
  - Factory: path functions can throw on NUL / directory paths.
- **Actual outcome**:
  - Planner may crash and CLI misclassifies as API failure.
  - Factory graph exceptions do trigger emergency rollback once graph starts.
  - Pre-graph preflight/init exceptions are not uniformly summarized.
- **Severity if real**: **HIGH**.
- **Fix**: add strict type guards and local exception-to-structured-error translation at each boundary before side effects.

#### 6) `shell=False` but still dangerous (argument injection, weird quoting)
- **What I tried**: operator tokens, weird quoting, verify-command variants.
- **Why it might work**: shell disabled prevents shell expansion, but policy checks may still be bypassed by equivalent argv forms.
- **Where it would pass/fail**:
  - E003 checks shell operators in tokenized acceptance strings.
  - E105 uses exact string compare after strip.
  - Factory executes tokenized command directly.
- **Actual outcome**:
  - shell metachar expansion is blocked (good).
  - policy-level check (`E105`) is bypassable by equivalent spellings.
  - planner allows unmatched-quote commands to survive to runtime (documented behavior).
- **Severity if real**: **MEDIUM**.
- **Fix**: normalize commands by argv (`shlex.split`) and compare normalized tuple; reject parse failures early in planner validation.

#### 7) JSON parsing ambiguities (duplicate keys, large payloads, unicode tricks)
- **What I tried**: duplicate keys and oversized JSON.
- **Why it might work**: stdlib `json.loads` accepts duplicate keys silently (last wins), and size is unbounded.
- **Where it would pass/fail**: planner `_parse_json`, factory `parse_proposal_json`, `load_work_order`.
- **Actual outcome**: ambiguous payloads accepted; no duplicate-key detection; no explicit payload cap.
- **Severity if real**: **HIGH**.
- **Fix**: custom JSON loader with `object_pairs_hook` duplicate-key rejection + max input bytes at parser boundary.

#### 8) Ordering nondeterminism (sets, `os.walk`, dict order assumptions)
- **What I tried**: inspect ordering-sensitive loops and output fields.
- **Why it might work**: nondeterministic ordering/time fields can alter artifacts and behavior.
- **Where it would pass/fail**:
  - planner repo listing via `os.walk` into set.
  - compile summary includes timestamps/duration.
  - `utils/run_work_orders.sh` sorts under caller locale.
- **Actual outcome**:
  - core gating uses membership sets (mostly stable).
  - artifact outputs include nondeterministic time fields by design.
  - script order can be locale-sensitive in edge filename sets.
- **Severity if real**: **LOW-MEDIUM**.
- **Fix**: separate deterministic metadata from runtime timing, force `LC_ALL=C` in script sorting, and document non-deterministic fields explicitly.

#### 9) Partial artifact writes (atomicity, crash mid-write)
- **What I tried**: inspect durability semantics of planner and factory artifact writes.
- **Why it might work**: plain writes can leave truncated files on crash/kill.
- **Where it would pass/fail**:
  - Planner artifact writes use `_atomic_write` (good per-file).
  - Factory `save_json` and prompt/stdout/stderr writes are non-atomic.
- **Actual outcome**: factory artifacts can be partially written; planner WO set is not transactionally atomic across files.
- **Severity if real**: **MEDIUM**.
- **Fix**: implement atomic `save_json` (tmp + fsync + replace) and use it consistently for all JSON/text artifacts.

#### 10) Verify/acceptance sequencing (any way acceptance runs when it shouldn’t)
- **What I tried**: trace graph routing and PO stage ordering; test verify bypass vectors.
- **Why it might work**: acceptance should only happen after full verify; exemptions may weaken this.
- **Where it would pass/fail**:
  - Graph routes TR->PO only if no failure brief.
  - PO runs verify first, then postcondition gate, then acceptance.
  - `verify_exempt` path substitutes global verify with compile-only check.
- **Actual outcome**:
  - sequencing itself is enforced.
  - **global verify strength is bypassable** through planner-controlled `verify_exempt` when contract missing.
- **Severity if real**: **CRITICAL**.
- **Fix**: planner must always overwrite `verify_exempt`; if no valid verify contract, force `verify_exempt=False` for all WOs.

### F. “Compiler-ness” Reality Check (strictly technical)

1) **Source language**  
Informal spec text (`--spec`) plus prompt template (`PLANNER_PROMPT.md`) and optional repo file listing context; this is not a formal grammar language.

2) **IR**  
Primary IR is the parsed/normalized plan manifest object (`system_overview`, `verify_contract`, `work_orders`) plus derived chain state (`file_state`) during validation. Emitted WO JSON files are serialized IR fragments consumed by factory.

3) **Deterministic passes**  
Planner: JSON parse -> normalize -> structural validation (`E0xx`) -> chain validation (`E1xx/W1xx`) -> optional `verify_exempt` derivation -> emission.  
Factory: WO schema parse -> preflight -> SE preconditions -> TR scope/path/hash gates -> PO verify/postcondition/acceptance gates -> finalize rollback/tree-hash.

4) **Runtime (factory execution model)**  
A deterministic control graph (`SE -> TR -> PO -> finalize`, with bounded retries) where only SE proposal generation is probabilistic; all transitions and gates are deterministic on current state.

5) **Guaranteed vs probabilistic**  
- **Guaranteed (when code path is well-typed and no races):** gate ordering, scope checks, shell=False command invocation, rollback attempt semantics.  
- **Probabilistic / non-guaranteed:** LLM outputs; runtime environment for subprocesses; race-dependent FS behavior; timestamped artifact values.

6) **What is compiler-like vs where analogy breaks**  
- **Compiler-like:** explicit IR, pass pipeline, deterministic validations, machine-readable diagnostics, artifact emission.  
- **Breaks:** source language is natural language; no formal parsing guarantees; optimization/codegen analog is LLM proposal (non-deterministic); runtime mutates real FS and invokes subprocesses with ambient environment.

### G. Fix Plan (only wrapper fixes; no prompt hacks)

1) **Strict JSON boundary parser (duplicate keys + size caps)**  
- **Files**: `planner/compiler.py`, `factory/llm.py`, `factory/schemas.py`  
- **Minimal sketch**: add shared helper `load_json_strict(raw, max_bytes)` using `object_pairs_hook` to reject duplicate keys and explicit byte-length check before parsing.  
- **Why it closes hole**: removes ambiguous key override and trivial memory DoS vector at trust boundary.  
- **Overfitting/false-positive risk**: low; only rejects structurally ambiguous/nonconforming JSON.

2) **Planner shape hardening for `work_orders` and `verify_contract`**  
- **Files**: `planner/validation.py`, `planner/compiler.py`  
- **Minimal sketch**: in `parse_and_validate`, ensure each `work_orders[i]` is dict; add typed validator for `verify_contract` (`dict` with `requires: list[dict{kind,path}]` or `None`). Return structured `ValidationError` instead of throwing.  
- **Why it closes hole**: converts crash paths into deterministic validation failures and keeps retry loop functional.  
- **Risk**: low-medium; may reject currently tolerated malformed plans.

3) **Never trust planner-provided `verify_exempt`**  
- **Files**: `planner/compiler.py`  
- **Minimal sketch**: after validation, always overwrite `verify_exempt` for all WOs: compute from valid `verify_contract` else force `False`; ignore incoming field value.  
- **Why it closes hole**: blocks verify-gate bypass from hostile planner output.  
- **Risk**: low; behavior becomes stricter and aligned with gate intent.

4) **Path validator hardening (`"."`, NUL/control chars)**  
- **Files**: `factory/schemas.py`  
- **Minimal sketch**: reject normalized `"."`, any `\x00`, and non-printable control chars; optionally require at least one non-separator path segment resembling a file path token.  
- **Why it closes hole**: prevents directory/null-byte exception escapes and file-vs-directory ambiguity at runtime.  
- **Risk**: medium; could reject edge filenames with control chars (desirable for safety).

5) **TR anti-TOCTOU write guard**  
- **Files**: `factory/nodes_tr.py`, `factory/util.py`  
- **Minimal sketch**: for each write, do check-and-write in one per-file critical section: re-hash immediately before write; verify resolved target/parent unchanged and inside repo; fail on mismatch.  
- **Why it closes hole**: narrows race window and prevents hash-then-swap and symlink-flip escapes.  
- **Risk**: medium; may increase false stale-context failures under heavy concurrent mutation (acceptable for deterministic safety).

6) **Outdir containment revalidation + robust containment API**  
- **Files**: `factory/run.py`  
- **Minimal sketch**: replace prefix logic with `commonpath`; re-check outdir containment before each top-level artifact write (`work_order.json`, `run_summary.json`).  
- **Why it closes hole**: prevents lexical corner cases and reduces symlink-race artifact pollution into repo.  
- **Risk**: low.

7) **Atomic artifact writes in factory**  
- **Files**: `factory/util.py`, `factory/nodes_se.py`  
- **Minimal sketch**: implement atomic `save_text`/`save_json` (tmp + fsync + replace) and use everywhere currently using plain `open(...,"w")`.  
- **Why it closes hole**: prevents torn/truncated forensic artifacts on interruption.  
- **Risk**: low.

8) **Rollback verification and explicit rollback failure classification**  
- **Files**: `factory/workspace.py`, `factory/graph.py`, `factory/run.py`  
- **Minimal sketch**: after rollback, assert `is_clean`; on failure emit explicit `rollback_failed` status and fail with deterministic remediation block.  
- **Why it closes hole**: transforms best-effort cleanup into auditable invariant with explicit failure mode.  
- **Risk**: low-medium; may fail runs that previously limped through dirty state.

9) **Planner/factory command-policy normalization**  
- **Files**: `planner/validation.py`  
- **Minimal sketch**: for `E105`, compare tokenized normalized argv (`["bash","scripts/verify.sh"]`) and normalized path; treat parse failure as validation error instead of silent skip.  
- **Why it closes hole**: closes known bypasses and resolves planner-vs-factory parsing inconsistency.  
- **Risk**: medium; stricter command validation may reject existing permissive inputs.

10) **Script hardening for execution wrapper (`run_work_orders.sh`)**  
- **Files**: `utils/run_work_orders.sh`  
- **Minimal sketch**: guard dangerous paths (`/`, empty, home), require explicit `--force-init` for `rm -rf`, and set `LC_ALL=C` for deterministic sort.  
- **Why it closes hole**: removes catastrophic path foot-guns and locale-driven ordering variance.  
- **Risk**: low.

### H. Regression Tests to Prove Each Fix

1) **Duplicate-key rejection in planner/factory JSON parsing**  
- **Test name**: `test_reject_duplicate_json_keys`  
- **Location**: `tests/planner/test_structural_validation.py`, `tests/factory/test_llm.py`  
- **Asserts**: duplicate keys trigger deterministic structured error (planner) or explicit parse exception (factory).  
- **Adversarial input**: `{"work_orders":[],"work_orders":[{"id":"WO-01",...}]}` and duplicated `writes` key.

2) **`work_orders` element type hardening**  
- **Test name**: `test_non_object_work_order_element_returns_structured_error`  
- **Location**: `tests/planner/test_structural_validation.py`  
- **Asserts**: no crash; returns `ValidationError` with stable code for index/type mismatch.  
- **Adversarial input**: `{"work_orders":[42,"x",{"id":"WO-01",...}]}`.

3) **`verify_contract` schema/type hardening**  
- **Test name**: `test_invalid_verify_contract_type_is_validation_error`  
- **Location**: `tests/planner/test_chain_validation.py` / `tests/planner/test_compile_loop.py`  
- **Asserts**: compile returns validation error, not exception/misclassified API error.  
- **Adversarial input**: `"verify_contract": []` and `"verify_contract": {"requires":"not-a-list"}`.

4) **Ignore planner-supplied `verify_exempt`**  
- **Test name**: `test_verify_exempt_overwritten_by_compiler`  
- **Location**: `tests/planner/test_compile_loop.py`  
- **Asserts**: planner-provided `verify_exempt:true` is overwritten to computed value or `False` when no contract.  
- **Adversarial input**: WO payload includes `verify_exempt: true` + missing `verify_contract`.

5) **Reject `"."` / NUL path values**  
- **Test name**: `test_special_paths_rejected_by_schema`  
- **Location**: `tests/factory/test_schemas.py`  
- **Asserts**: validation error for `"."`, `"./"`, `"a\0b.py"` in `allowed_files`, `context_files`, and proposal `writes.path`.  
- **Adversarial input**: crafted WO/proposal JSON with those paths.

6) **TOCTOU protection in TR**  
- **Test name**: `test_tr_detects_prewrite_hash_change` and `test_tr_detects_symlink_flip`  
- **Location**: `tests/factory/test_nodes.py`  
- **Asserts**: TR returns `stale_context`/scope failure when target changes between initial hash check and write.  
- **Adversarial input**: monkeypatch `sha256_file`/filesystem hook to mutate target or parent symlink between phases.

7) **Outdir containment revalidation**  
- **Test name**: `test_outdir_symlink_race_detected`  
- **Location**: `tests/factory/test_cli.py`  
- **Asserts**: run aborts with explicit containment error when outdir is redirected into repo after initial preflight.  
- **Adversarial input**: replace outdir parent with symlink to repo between checks.

8) **Atomic artifact writes**  
- **Test name**: `test_save_json_is_atomic_under_interruption`  
- **Location**: `tests/factory/test_util.py`  
- **Asserts**: never leaves malformed JSON at final path when write interrupted/fails before replace.  
- **Adversarial input**: monkeypatch write/fsync to raise mid-write.

9) **Rollback verification path**  
- **Test name**: `test_rollback_failure_sets_explicit_status`  
- **Location**: `tests/factory/test_end_to_end.py`  
- **Asserts**: forced rollback failure yields deterministic `rollback_failed` in summary and non-clean repo warning.  
- **Adversarial input**: monkeypatch `_git` to fail on `clean -fdx`.

10) **E105 normalization closes command spelling bypasses**  
- **Test name**: `test_e105_catches_equivalent_verify_invocations`  
- **Location**: `tests/planner/test_chain_validation.py`  
- **Asserts**: `bash  scripts/verify.sh`, `bash ./scripts/verify.sh`, and similar normalized forms are all rejected.  
- **Adversarial input**: acceptance command list with spacing/path variants.

11) **Script safety hardening (`run_work_orders.sh`)**  
- **Test name**: `test_run_work_orders_refuses_dangerous_target_repo`  
- **Location**: `tests/factory/test_cli.py` (or dedicated script test harness)  
- **Asserts**: script exits nonzero for `--target-repo /` or empty path and preserves deterministic WO sort under `LC_ALL=C`.  
- **Adversarial input**: dangerous `--target-repo` values and locale variations.

---

This audit intentionally focused on deterministic wrapper integrity under adversarial planner output and hostile filesystem/process conditions, not on LLM quality.
