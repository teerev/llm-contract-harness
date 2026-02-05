---
title: "P3.01 - Documentation and example outputs"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "README.md"
  - "src/lsystem/presets.py"
  - "src/lsystem/cli.py"

# Quality gates
min_assertions: 3
coverage_threshold: 70
min_tests: 2
max_test_failures: 0
require_hypothesis: false
require_type_check: false
shell_policy: warn

acceptance_commands:
  - "pip install -e ."
  - "python -m lsystem render fern --output examples/fern.svg"
  - "python -m lsystem render bush --output examples/bush.svg"
  - "python -m lsystem render tree --output examples/tree.svg"
  - "test -f examples/fern.svg"
  - "test -f examples/bush.svg"
  - "test -f examples/tree.svg"
  - "pytest tests/test_examples.py -v"
---

# P3.01 - Documentation and Example Outputs

## Goal

Create comprehensive documentation and generate example output images for human verification and showcase.

## Scope

### In Scope
- Update `README.md` with:
  - Project overview and features
  - Installation instructions
  - Quick start guide
  - CLI usage examples
  - Library API usage examples
  - Description of L-system concepts
  - Links to example outputs
- Create `examples/` directory with generated SVG files
- Generate one SVG per preset
- Add visual descriptions for human verification

### Out of Scope
- API reference documentation (docstrings are sufficient)
- Hosted documentation site
- Tutorials or videos

## Design Constraints

- README should be self-contained (no external links required to understand basics)
- Example SVGs should be small enough to include in repo (< 500KB each)
- Use relative links to example files
- Include code examples that can be copy-pasted

## Acceptance Criteria

- [ ] `README.md` includes installation section with `pip install -e .`
- [ ] `README.md` includes CLI quick start with example commands
- [ ] `README.md` includes Python API example (5-10 lines)
- [ ] `README.md` explains L-system basics (axiom, rules, expansion)
- [ ] `examples/` directory contains at least 3 SVG files
- [ ] Each example SVG renders correctly in a browser
- [ ] README references example files with relative paths

## Human Verification Checklist

After running the acceptance commands, a human reviewer should verify:

1. **fern.svg**:
   - [ ] Displays a recognizable fern-like or fractal plant structure
   - [ ] Has multiple levels of branching
   - [ ] Branches become smaller toward the tips
   - [ ] Overall shape is roughly triangular or plume-like

2. **bush.svg**:
   - [ ] Shows a bushy, multi-branched plant
   - [ ] Has denser branching than the fern
   - [ ] Branches spread outward in multiple directions
   - [ ] Looks like a shrub or bush silhouette

3. **tree.svg** (or third preset):
   - [ ] Has a distinct vertical trunk or stem
   - [ ] Shows clear hierarchical branching
   - [ ] Visually distinct from fern and bush
   - [ ] Recognizable as a plant or tree form

4. **General quality**:
   - [ ] All SVGs render without errors
   - [ ] Lines are visible and not too thin
   - [ ] Plant is centered in the canvas
   - [ ] No obvious visual artifacts or clipping

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Create `examples/` directory if it doesn't exist
- Run CLI commands to generate SVGs during this work order
- README sections:
  1. Title and badges
  2. Overview
  3. Installation
  4. Quick Start (CLI)
  5. Python API
  6. Available Presets (with example images)
  7. How L-systems Work (brief)
  8. Contributing/License

## Suggested Tests

- `test_examples_directory_exists`: `examples/` directory present
- `test_example_svgs_exist`: All preset SVGs exist in examples/
- `test_example_svgs_valid`: Each SVG file starts with valid SVG header
- `test_readme_exists`: README.md exists and has content

## Public APIs

No new public APIs. Documentation only.
