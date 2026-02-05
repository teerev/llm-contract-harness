---
title: "P2.03 - Golden output testing"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "src/lsystem/core.py"
  - "src/lsystem/turtle.py"
  - "src/lsystem/render_svg.py"
  - "src/lsystem/presets.py"

# Quality gates
min_assertions: 8
coverage_threshold: 80
min_tests: 6
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pip install -e ."
  - "pytest tests/test_golden.py -v"
  - "pytest tests/test_determinism.py -v"
---

# P2.03 - Golden Output Testing

## Goal

Establish golden output tests that verify deterministic end-to-end rendering and catch any unintended changes to output.

## Scope

### In Scope
- Create `tests/fixtures/` directory for golden test data
- Create golden segment data for each preset (JSON format)
- Create golden SVG files for reference
- Implement comparison tests: generated output vs. golden files
- Create helper to regenerate golden files when intentional changes are made
- Add comprehensive determinism tests

### Out of Scope
- Visual regression testing (pixel comparison)
- Fuzzing or property-based tests
- Performance benchmarks

## Design Constraints

- **Golden strategy**: Compare normalized segment lists (not raw SVG strings)
  - Round segment coordinates to 2 decimal places
  - Store as JSON: `[{"start": [x1, y1], "end": [x2, y2]}, ...]`
- SVG golden files are secondary (for human inspection)
- Golden files must be committed to repo
- Regeneration script should require explicit flag to prevent accidental overwrites

## Acceptance Criteria

- [ ] `tests/fixtures/golden/` directory exists with golden data
- [ ] Golden segment JSON for each preset (at least 3)
- [ ] Golden SVG file for each preset (at least 3)
- [ ] Test compares generated segments to golden JSON (normalized)
- [ ] Test verifies SVG file is created and non-empty
- [ ] `python -m pytest --update-golden` or similar flag regenerates golden files
- [ ] Determinism test: expand + interpret + render 10 times, all identical
- [ ] Hash-based quick check: SHA256 of canonical segment JSON matches expected

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Use `pytest` fixtures to load golden data
- Segment normalization function:
  ```
  def normalize_segments(segments):
      return [{"start": [round(s.start[0], 2), round(s.start[1], 2)],
               "end": [round(s.end[0], 2), round(s.end[1], 2)]}
              for s in segments]
  ```
- Store golden files as `tests/fixtures/golden/<preset>_segments.json`
- Store reference SVGs as `tests/fixtures/golden/<preset>.svg`
- Use `conftest.py` fixture to provide path to fixtures directory

## Suggested Tests

- `test_fern_matches_golden`: Fern preset segments match golden JSON
- `test_bush_matches_golden`: Bush preset segments match golden JSON
- `test_tree_matches_golden`: Tree/third preset matches golden JSON
- `test_svg_created_for_each_preset`: SVG files are valid and non-empty
- `test_expansion_determinism`: Same system expanded 10x produces identical strings
- `test_full_pipeline_determinism`: expand -> interpret -> render is deterministic
- `test_golden_hash_fern`: SHA256 of normalized segments matches expected hash
- `test_golden_hash_bush`: SHA256 of normalized segments matches expected hash

## Public APIs

No new public APIs. Test infrastructure only.

Helper script (internal):
- `python -m tests.update_golden` or pytest flag to regenerate fixtures
