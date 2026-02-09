# Planner–Factory Contract Analysis

## 0. The Bug: What Happened

This document was written in response to two real failures observed when running LLM-generated work orders against the factory. Both failures originated from the same root cause: the planner LLM produced work orders that were schema-valid but operationally broken.

### Incident 1: PermissionError crash (unhandled exception)

**Command:**

```
python -m factory run --repo /Users/user/repos/maze --work-order ./wo/WO-01.json --out ./out --llm-model gpt-4o
```

**Terminal output:**

```
Verdict: ERROR (unhandled exception)
Exception: [Errno 13] Permission denied: './scripts/verify.sh'
Run summary: /Users/user/repos/aos/out/29a3ffa63b34dd85/run_summary.json
```

**Full traceback (from run_summary.json):**

```
Traceback (most recent call last):
  File "/Users/user/repos/aos/factory/run.py", line 110, in run_cli
    final_state = graph.invoke(initial_state)
  File ".../langgraph/pregel/main.py", line 3071, in invoke
    ...
  File "/Users/user/repos/aos/factory/nodes_po.py", line 139, in po_node
    cr = run_command(
  File "/Users/user/repos/aos/factory/util.py", line 101, in run_command
    proc = subprocess.run(
  File ".../subprocess.py", line 548, in run
    with Popen(*popenargs, **kwargs) as process:
  File ".../subprocess.py", line 1026, in __init__
    self._execute_child(args, executable, preexec_fn, close_fds,
  File ".../subprocess.py", line 1955, in _execute_child
    raise child_exception_type(errno_num, err_msg, err_filename)
PermissionError: [Errno 13] Permission denied: './scripts/verify.sh'
During task with name 'po' and id '173aebcb-a9dc-7de5-1198-88e681b51346'
```

**What happened:** The planner-generated WO-01 had `"acceptance_commands": ["./scripts/verify.sh"]` — invoking the script directly, which requires the executable permission bit (`chmod +x`). The factory's TR node writes files as plain text via `_atomic_write()` with no `+x` bit. When the PO node tried to execute `./scripts/verify.sh` via `subprocess.run(..., shell=False)`, the OS raised `PermissionError`. The `run_command` function only caught `subprocess.TimeoutExpired`, not `OSError`, so the exception propagated as an unhandled crash.

**Contributing factors:**

1. The planner prompt said acceptance commands `must include ./scripts/verify.sh` (with the `./` prefix that triggers direct execution).
2. The planner validation enforced `VERIFY_COMMAND = "./scripts/verify.sh"`.
3. The factory's own `_get_verify_commands()` already knew to use `["bash", "scripts/verify.sh"]` (bypassing the `+x` requirement), but acceptance commands from the work order went through a different code path with no such normalization.
4. `run_command()` had no `OSError` handler — only `TimeoutExpired`.

**Why the example work orders didn't crash:** The hand-crafted example work orders never invoke scripts directly. They use `python -c "..."` or `python -m ...` as acceptance commands, which invoke `python` (an executable) and pass the script as an argument.

### Incident 2: verify_failed FAIL (self-contradictory work order)

After fixing Incident 1 (changing `./scripts/verify.sh` → `bash scripts/verify.sh` in the prompt and validation, and adding `OSError` handling in `run_command`), the planner was re-run and the factory was invoked again.

**Command:**

```
python -m factory run --repo /Users/user/repos/maze --work-order ./wo/WO-01.json --out ./out --llm-model gpt-4o
```

**Terminal output:**

```
Verdict: FAIL
Run summary: /Users/user/repos/aos/out/eaac6d4f19491f4c/run_summary.json
```

**The work order that caused it (`wo/WO-01.json`):**

```json
{
  "id": "WO-01",
  "title": "Skeleton project and verification harness",
  "intent": "Establish minimal file layout and a stable verification command that always runs.",
  "allowed_files": [
    "scripts/verify.sh",
    "rlmaze/__init__.py",
    "tests/test_placeholder.py"
  ],
  "forbidden": [
    "Any files not listed in allowed_files"
  ],
  "acceptance_commands": [
    "bash scripts/verify.sh"
  ],
  "context_files": [
    "scripts/verify.sh",
    "rlmaze/__init__.py",
    "tests/test_placeholder.py"
  ],
  "notes": "verify.sh must run `python -m unittest discover -v` and exit nonzero on failure. Placeholder test should always pass."
}
```

**What the executor LLM wrote (from `proposed_writes.json`, attempt 1):**

```json
{
  "summary": "Created a skeleton project with a placeholder test and a verification script.",
  "writes": [
    {
      "path": "rlmaze/__init__.py",
      "base_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "content": "# This is the rlmaze package initialization file.\n"
    },
    {
      "path": "scripts/verify.sh",
      "base_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "content": "#!/bin/bash\n\n# Run the unittest discovery\npython -m unittest discover -v\n\n# Capture the exit code\nexit_code=$?\n\n# Exit with the same code as the test run\nexit $exit_code\n"
    },
    {
      "path": "tests/test_placeholder.py",
      "base_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "content": "import unittest\n\nclass TestPlaceholder(unittest.TestCase):\n    def test_placeholder(self):\n        self.assertTrue(True)\n\nif __name__ == '__main__':\n    unittest.main()\n"
    }
  ]
}
```

**Failure details (from run_summary.json, both attempts identical):**

```json
{
  "stage": "verify_failed",
  "command": "bash scripts/verify.sh",
  "exit_code": 5,
  "primary_error_excerpt": "[stderr]\n\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n",
  "constraints_reminder": "Global verification must pass before acceptance."
}
```

**What happened:** The executor LLM followed the `notes` field literally: it wrote `verify.sh` containing `python -m unittest discover -v`. It also placed the placeholder test at `tests/test_placeholder.py`. However, `python -m unittest discover` (without the `-s tests` flag) searches only the current directory (`.`) for files matching `test*.py`. Since the test file is in the `tests/` subdirectory, `unittest discover` found zero tests and exited with code 5 (the standard "no tests ran" exit code).

The executor behaved correctly — it faithfully implemented a self-contradictory work order. The planner LLM created a work order whose `notes` specified a test runner command that couldn't discover tests placed in the directory structure the work order itself defined. Both attempts produced the identical failure.

### Contrast: The hand-crafted WO-01 that works

For comparison, the hand-crafted `examples/work_orders/WO-01.json` avoids both problems:

```json
{
  "id": "WO-01",
  "title": "Freeze global verification script",
  "intent": "Create scripts/verify.sh that runs 'python -m compileall -q .' to establish the global verification command for all subsequent work orders.",
  "allowed_files": [
    "scripts/verify.sh"
  ],
  "forbidden": [
    "Do not create any files other than scripts/verify.sh",
    "Do not add pytest or any test runner yet"
  ],
  "acceptance_commands": [
    "python -c \"import os; assert os.path.isfile('scripts/verify.sh'), 'verify.sh not found'\""
  ],
  "context_files": [
    "scripts/verify.sh"
  ],
  "notes": "scripts/verify.sh must contain exactly three lines: '#!/usr/bin/env bash', 'set -euo pipefail', and 'python -m compileall -q .' — nothing else. If the file already exists, overwrite it with this exact content."
}
```

Key differences:
- **Acceptance command:** Uses `python -c "assert os.path.isfile(...)"` — an independent check, not `bash scripts/verify.sh` (avoids the bootstrapping circularity).
- **Scope:** Only touches `scripts/verify.sh` — no package skeleton, no tests. Minimal entropy.
- **Notes:** Specifies the exact, byte-level content of `verify.sh` — zero degrees of freedom for the executor.
- **Verify content:** Uses `python -m compileall -q .` — a command that succeeds on any valid Python project regardless of test layout.

---

## 1. Root Cause at the System-Design Level

The specific bug — `unittest discover` missing `-s tests` — is a symptom. The deeper problem is a **missing contract layer between the planner's free-text fields and the factory's mechanical execution**.

### The implicit assumptions

The system currently relies on a chain of three trust boundaries:

1. **The planner prompt** tells the planner LLM what shape work orders should have (JSON schema, sequencing rules, compositionality doctrine).
2. **The planner validation** (`validation.py`) enforces structural invariants post-hoc (ID contiguity, presence of verify command, schema conformance, no globs).
3. **The factory executor** treats every work order as a literal contract: it runs acceptance commands as-is, writes to allowed files, and expects exit 0.

The gap sits between (1) and (3). The planner prompt tells the LLM to produce acceptance commands and notes, and validation checks that a verify command string is present — but **nobody checks whether the notes, acceptance commands, and allowed files are internally consistent as a runnable system**. Specifically:

- **`notes` is free text that carries executable semantics.** WO-01's notes say "verify.sh must run `python -m unittest discover -v`". The executor LLM reads that literally and produces a `verify.sh` that runs exactly that command. But the notes also say to put tests under `tests/`, and `unittest discover -v` (without `-s tests`) won't find them there. The planner LLM produced a *self-contradictory* work order, and nothing in the system catches that.

- **`verify.sh` is a file that the planner instructs the executor to write, but whose contents define the global invariant for all subsequent work orders.** This means the planner is delegating the definition of a system-wide invariant to a free-text `notes` field, which the executor LLM then interprets. There are two LLM inference boundaries between the intent and the behavior.

- **Acceptance commands are validated for syntax (shlex-parseable) but not for semantic coherence with the work order's own writes.** The factory can't know whether `bash scripts/verify.sh` will pass for what the executor wrote — but nobody checks whether the *planner's own instructions* would produce a self-consistent result.

### Where global invariants are (and aren't) defined

The system has exactly one global invariant mechanism: the existence and content of `scripts/verify.sh`. This is handled in two separate places:

- **`_get_verify_commands`** in the factory: if the file exists, run `bash scripts/verify.sh`; otherwise fall back to `compileall + pip + pytest`. This is a **factory-side convention** baked into Python code.
- **`PLANNER_PROMPT.md`**: tells the planner that every work order must include `bash scripts/verify.sh` in its acceptance commands. This is a **planner-side convention** baked into prose.

But neither side specifies **what verify.sh should contain**. The factory treats it as an opaque executable. The planner tells the executor LLM what to put in it via `notes`. So the content of the single most critical file in the entire system is defined by a free-text field interpreted by a different LLM in a different context.

---

## 2. WO-01 Is Implicitly Special — and This Is Currently Leaky

WO-01 is structurally unique in several ways:

**In the LLM-generated work orders (`./wo/`):**
- WO-01 is the only work order that creates `scripts/verify.sh`.
- WO-01 defines the global verification behavior that all subsequent work orders depend on.
- WO-01 is the only work order where acceptance = verification (its only acceptance command is `bash scripts/verify.sh`, and the file it writes *is* the verify script).

**In the hand-crafted example work orders (`./examples/work_orders/`):**
- WO-01 is explicitly titled "Freeze global verification script."
- WO-01's acceptance command is *not* `bash scripts/verify.sh` — it's `python -c "assert os.path.isfile('scripts/verify.sh')"`. This sidesteps the bootstrapping problem: you can't verify with verify.sh when verify.sh is what you're creating.
- WO-01's notes are byte-precise: "scripts/verify.sh must contain exactly three lines: ..." — zero degrees of freedom for the executor.

This contrast reveals the design leak:

- The hand-crafted WO-01 was designed by someone who understood the factory's execution model and the bootstrapping problem. It uses a trivial acceptance command (file-existence check) and specifies verify.sh content exactly.
- The LLM-generated WO-01 was produced by a planner that knows the *schema rules* but not the *execution model*. It puts `bash scripts/verify.sh` as the acceptance command (which validation requires), making verify.sh both the thing being tested and the test runner. It then specifies verify.sh content loosely in notes, leaving room for a broken implementation.

**The implicit specialness is leaky** because the prompt and validation treat WO-01 identically to all other work orders. There is no mechanism to tell the planner "WO-01 has a bootstrapping constraint that later work orders don't."

---

## 3. The Role of the `notes` Field

### Current usage

In the LLM-generated work orders, `notes` carries the bulk of the implementation contract. Examples:

- WO-01: "verify.sh must run `python -m unittest discover -v` and exit nonzero on failure."
- WO-03: specifies maze text format, `load_maze` return schema, env behavior, determinism requirements
- WO-04: specifies entire `train_q_learning` function signature, return types, and dict key names

### The problem

`notes` is carrying **two different kinds of information** with no separation:

1. **Implementation guidance** — how to structure code, what function signatures to use, what data formats to expect. This is appropriate for `notes`.

2. **Executable invariants** — things that *must be true* for the work order to succeed, but which are not enforced by any acceptance command. The `unittest discover -v` instruction is a perfect example: the notes specify the verify command, but no acceptance command checks that it works.

The factory's SE node (`nodes_se.py` line 96–98) injects notes directly into the executor LLM prompt:

```python
if work_order.notes:
    lines.append(f"## Notes\n{work_order.notes}")
    lines.append("")
```

Notes are free text with no structure, no validation, and no separation of concerns. They pass through two LLM boundaries (planner → JSON → executor → code) with no mechanical check on coherence.

### Is this dangerous?

Yes, in a specific way: **notes are the only channel for global semantics that don't fit in acceptance commands, but they have zero enforcement**. The planner LLM uses notes to communicate things like "verify.sh must run X" — information that is operationally critical but invisible to the validation layer. If the planner gets notes wrong, the executor will faithfully implement the wrong thing, and the failure will be late and opaque (a mysterious exit code, not a clear contract violation).

---

## 4. Possible Ways Forward

### Option A: Planner Prompt Refinement Only

**What:** Add explicit instructions to `PLANNER_PROMPT.md` for the bootstrapping work order. Prescribe the exact content of `verify.sh` (or at minimum, the exact command it must run). Explain that WO-01 must not use `bash scripts/verify.sh` as its own acceptance command if it's the work order that creates verify.sh.

**Trade-offs:**
- **Impact on factory code:** None.
- **Risk of breaking working behavior:** Zero — purely additive guidance.
- **Robustness improvement:** Moderate. Depends on the planner LLM following instructions reliably. A smarter model will obey; a weaker model may still drift. No mechanical backstop.
- **Conceptual complexity:** Low — just more prescriptive prose.

**Weakness:** This is the "hope the LLM reads carefully" approach. It addresses the specific failure but doesn't prevent the general class of "self-contradictory notes" errors.

### Option B: Planner Prompt + Validation Linting

**What:** In addition to prompt changes, extend `validation.py` to check cross-field coherence. For example:

- If `scripts/verify.sh` is in `allowed_files`, warn or error if `bash scripts/verify.sh` is simultaneously the acceptance command (bootstrapping circularity).
- If a work order's only acceptance command is the verify command, flag it as having no per-work-order acceptance (a weak test).
- Check that `context_files` entries that appear in `notes` references are also in `allowed_files` (primitive notes-to-schema consistency).

**Trade-offs:**
- **Impact on factory code:** None — this lives entirely in the planner.
- **Risk of breaking working behavior:** Low, if new checks are warnings rather than hard errors initially.
- **Robustness improvement:** Meaningful. Catches an entire class of structural errors before execution. The bootstrapping circularity check alone would have prevented this failure.
- **Conceptual complexity:** Moderate. Validation starts encoding knowledge about *how the factory works*, which creates a coupling between planner validation and factory execution semantics.

### Option C: Prescribe verify.sh Content in the Factory, Not the Planner

**What:** Remove the planner's responsibility for defining verify.sh content entirely. Instead, the factory provides a default verify.sh at run start (or the `_get_verify_commands` fallback becomes the only path, and verify.sh is never written by a work order). The planner's WO-01 focuses purely on project skeleton (package, types, stubs), and the verification strategy is a factory-level decision.

**Trade-offs:**
- **Impact on factory code:** Moderate — `_get_verify_commands` or `run_cli` needs to be extended. The fallback already exists (`compileall + pip + pytest`), so this is mostly a matter of making it the primary path or generating verify.sh mechanically.
- **Risk of breaking working behavior:** Moderate. Changes the fundamental assumption that `verify.sh` is part of the project under construction. Existing example work orders would need updating.
- **Robustness improvement:** High for this specific failure class. Eliminates the "two LLMs defining a global invariant via free text" problem entirely.
- **Conceptual complexity:** Moderate. Raises questions about who owns verification policy — the product spec or the factory?

**Weakness:** Some product specs genuinely need custom verify scripts (e.g., specs requiring specific test runners, linting, type checking). A fixed factory-side verify removes that flexibility.

### Option D: Schema Extension — Explicit "Global Convention" Work Order Type

**What:** Add an optional `kind` field (or equivalent) to the work order schema, with values like `"scaffold"` or `"feature"`. Scaffold work orders have different validation rules: their acceptance commands don't need to include the verify command (because they're creating it), and their `notes` field could be validated more strictly (e.g., requiring exact file content specifications rather than loose descriptions).

**Trade-offs:**
- **Impact on factory code:** Small — the schema gains an optional field; the PO node could skip global verification for scaffold work orders or use the fallback commands.
- **Risk of breaking working behavior:** Low if `kind` defaults to `"feature"` and existing work orders don't need updating.
- **Robustness improvement:** Meaningful. Makes WO-01's specialness explicit rather than implicit. Validation rules can be type-specific.
- **Conceptual complexity:** Moderate. Introduces the concept of work order types, which has to be explained to both the planner LLM and the factory.

### Option E: Self-Verifying Acceptance Commands

**What:** Instead of relying on notes to describe what verify.sh should do, require WO-01's acceptance commands to *test* that verify.sh does what it should. For example, acceptance commands for WO-01 could include:

- A command that checks verify.sh exists
- A command that runs verify.sh and asserts exit 0
- A command that asserts the test runner discovers at least 1 test

This is closer to the hand-crafted example approach but formalized: acceptance commands should never just be "run the verify" — they should include at least one *independent* correctness assertion.

**Trade-offs:**
- **Impact on factory code:** None — purely planner-side.
- **Risk of breaking working behavior:** None.
- **Robustness improvement:** High for WO-01 specifically. The "at least 1 test discovered" check would have caught the failure. Generalizable to other work orders: acceptance commands should be *independently verifiable*, not just restatements of the verify command.
- **Conceptual complexity:** Low — it's a prompt-level design principle, not a schema change.

**Weakness:** Requires the planner LLM to be creative about acceptance commands. More prescriptive guidance in the prompt helps, but the planner could still produce acceptance commands that are too weak.

### Option F: Hybrid — Prescribed verify.sh + Prompt Hardening + Validation

**What:** Combine elements:

1. The planner prompt prescribes the exact content of verify.sh (or the factory provides it) — eliminates the bootstrapping problem.
2. The planner prompt adds a principle: "every work order must include at least one acceptance command beyond the global verify" — eliminates verify-only work orders.
3. Validation gains a bootstrapping check (if verify.sh is in allowed_files, acceptance must not be *only* `bash scripts/verify.sh`).

**Trade-offs:**
- **Impact on factory code:** Minimal (validation only).
- **Risk of breaking working behavior:** Low — additive changes, existing examples already conform.
- **Robustness improvement:** High. Addresses the root cause (bootstrapping), the symptom (self-contradictory notes), and the general case (every work order has independent acceptance).
- **Conceptual complexity:** Low to moderate. Each piece is simple; together they form a coherent tightening of the planner–executor contract.

---

## 5. Summary Observations

The fundamental tension is:

- The **factory** is designed to be maximally literal and minimal. It trusts work orders and executes them mechanically. This is a strength.
- The **planner** is responsible for producing work orders that are internally consistent, self-testing, and composable. But it currently does this through unstructured prose (`notes`) with minimal post-hoc validation.
- The **contract surface** between the two is the JSON schema plus the verify command string constant. Everything else (what verify.sh should contain, how acceptance commands relate to writes, whether a work order is a bootstrapping step) is implicit.

The hand-crafted example work orders succeeded because they were written by someone who held the factory's execution model in their head while designing the work orders. The planner LLM doesn't have that model — it has a prompt that describes the *schema* but not the *runtime semantics*. Closing this gap can be done at the prompt level, the validation level, the schema level, or some combination. The right choice depends on how much you want to rely on prompt engineering versus mechanical enforcement.
