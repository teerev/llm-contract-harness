---
title: "P2.02 - Complete CLI implementation"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "src/lsystem/core.py"
  - "src/lsystem/presets.py"
  - "src/lsystem/render_svg.py"

# Quality gates
min_assertions: 5
coverage_threshold: 70
min_tests: 5
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pip install -e ."
  - "python -m lsystem --help"
  - "python -m lsystem list"
  - "python -m lsystem render fern --output /tmp/test_fern.svg"
  - "test -f /tmp/test_fern.svg"
  - "pytest tests/test_cli.py -v"
---

# P2.02 - Complete CLI Implementation

## Goal

Implement a full-featured command-line interface for generating L-system plant images.

## Scope

### In Scope
- Create `src/lsystem/__main__.py` for `python -m lsystem` invocation
- Create `src/lsystem/cli.py` with CLI logic
- Implement subcommands:
  - `list`: Show available presets with descriptions
  - `render <preset>`: Generate SVG from a preset
- CLI options for `render`:
  - `--output PATH`: Output file path (default: `<preset>.svg`)
  - `--iterations N`: Override preset's iteration count
  - `--angle DEGREES`: Override preset's angle
  - `--step LENGTH`: Override preset's step length
  - `--width PIXELS`: Canvas width (default: 800)
  - `--height PIXELS`: Canvas height (default: 600)
  - `--stroke COLOR`: Stroke color (default: forest green)
  - `--stroke-width N`: Line thickness

### Out of Scope
- GUI
- Interactive mode
- Custom L-system definition via CLI (use library API for that)
- PNG output (separate work order)

## Design Constraints

- Use `argparse` from stdlib (no external CLI libraries like Click)
- Exit codes: 0 for success, 1 for user error, 2 for internal error
- Helpful error messages for invalid inputs
- `--help` at all levels
- Validate file paths are writable before processing

## Acceptance Criteria

- [ ] `python -m lsystem --help` shows usage information
- [ ] `python -m lsystem list` prints available presets with descriptions
- [ ] `python -m lsystem render fern` creates `fern.svg` in current directory
- [ ] `python -m lsystem render fern --output custom.svg` creates `custom.svg`
- [ ] `--iterations`, `--angle`, `--step` override preset defaults
- [ ] Invalid preset name prints error and exits with code 1
- [ ] `--width` and `--height` control canvas size
- [ ] Progress or completion message printed to stderr

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Use `argparse.ArgumentParser` with subparsers for `list` and `render`
- `__main__.py` should just call `cli.main()`
- For `list`, format output nicely (name + description per line)
- Print to stderr for status messages, only file paths to stdout if needed
- Catch exceptions and convert to user-friendly error messages

## Suggested Tests

- `test_cli_help`: `--help` exits 0 and contains expected text
- `test_cli_list`: `list` command shows preset names
- `test_cli_render_preset`: `render fern` creates SVG file
- `test_cli_render_custom_output`: `--output` flag respected
- `test_cli_invalid_preset`: Unknown preset exits with code 1
- `test_cli_override_iterations`: `--iterations` changes output
- `test_cli_render_custom_dimensions`: `--width` and `--height` work

## Public APIs

- `lsystem.cli.main(argv: list[str] | None = None) -> int`: CLI entry point
- Entry point in `pyproject.toml`: `lsystem = "lsystem.cli:main"`
