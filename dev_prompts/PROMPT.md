# PROMPT.md — Cursor one-shot generation wrapper

You are an expert Python engineer.

You MUST:
1) Read `AGENTS.md` fully before writing any code.
2) Implement the repository EXACTLY as specified in `AGENTS.md`.
3) Do not invent requirements, files, commands, stages, schemas, or behaviors not explicitly required by `AGENTS.md`.
4) If anything is ambiguous, choose the simplest valid interpretation and document it in `README.md` under “Assumptions”.

Strict compliance:
- Follow the required package tree and file-creation boundary exactly.
- Follow the CLI contract exactly.
- Follow the determinism, preflight, rollback, and verification rules exactly.
- Follow the LLM I/O contract exactly.
- Follow artifacts + JSON requirements exactly.

When finished, follow the Output Contract in `AGENTS.md` exactly:
- Output ONLY the requested file list and full file contents, with no extra commentary.
