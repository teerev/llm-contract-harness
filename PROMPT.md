# PROMPT.md — One-shot code generation prompt

You are an expert Python engineer.

**You MUST read `AGENTS.md` fully first and follow it exactly.**
Do not begin implementing until you have read AGENTS.md.

Now generate the complete codebase described in AGENTS.md, implementing the deterministic SE → TR → PO factory harness with in-situ git-based rollback and the exact global verification rules.

Critical reminders (must comply):
- Git repos only + clean working tree preflight; refuse otherwise.
- LLM outputs JSON with keys `{unified_diff, summary}` only; strict parsing; unified diff must be applyable by `git apply`.
- No `shell=True` anywhere; deterministic command runner with timeouts.
- Patch scope enforced from diff headers; only `allowed_files`.
- On failure after patch apply: rollback to baseline using `git reset --hard <baseline>` then `git clean -fd`.
- Global verify is EXACTLY: if scripts/verify.sh exists run `bash scripts/verify.sh`, else run:
  1) python -m compileall -q .
  2) python -m pip --version
  3) python -m pytest -q
- Output must follow the Output Contract in AGENTS.md (files only, no extra commentary).
