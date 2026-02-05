---
title: "P3.02 - PNG export (optional)"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "src/lsystem/render_svg.py"
  - "src/lsystem/cli.py"

# Quality gates
min_assertions: 4
coverage_threshold: 70
min_tests: 3
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pip install -e '.[png]'"
  - "python -m lsystem render fern --output /tmp/test_fern.png --format png"
  - "test -f /tmp/test_fern.png"
  - "file /tmp/test_fern.png | grep -q PNG"
  - "pytest tests/test_png.py -v"
---

# P3.02 - PNG Export (Optional)

## Goal

Add optional PNG export capability for users who need raster images instead of SVG.

## Scope

### In Scope
- Create `src/lsystem/render_png.py`
- Add `cairosvg` or `pillow` as optional dependency (extra)
- Implement `render_png(segments, width, height, ...) -> bytes`
- Implement `save_png(content: bytes, path: Path) -> None`
- Extend CLI with `--format svg|png` option
- Update `pyproject.toml` with optional `[png]` extra

### Out of Scope
- Other raster formats (JPEG, WebP, etc.)
- Resolution/DPI settings (fixed at 1:1 pixel mapping)
- Anti-aliasing configuration
- Transparency options

## Design Constraints

- PNG export must be optional (not required for core functionality)
- Use `pip install lsystem-plants[png]` to install with PNG support
- If PNG dependencies not installed, `--format png` should fail gracefully with helpful message
- PNG output need not be byte-for-byte deterministic (renderer may vary)
- SVG remains the primary, recommended output format

## Acceptance Criteria

- [ ] `pip install -e '.[png]'` installs PNG rendering dependencies
- [ ] `render_png()` produces valid PNG bytes
- [ ] `save_png()` writes PNG file to disk
- [ ] `python -m lsystem render fern --format png` creates PNG file
- [ ] `--format svg` still works (default)
- [ ] Missing PNG deps: clear error message suggesting `pip install lsystem-plants[png]`
- [ ] PNG file is valid image (can be opened by image viewers)

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Recommended approach: render SVG first, then convert to PNG using `cairosvg`
- Alternative: use `pillow` to draw lines directly (more control, more code)
- `cairosvg` is simpler: `cairosvg.svg2png(bytestring=svg_bytes)`
- Add to `pyproject.toml`:
  ```toml
  [project.optional-dependencies]
  png = ["cairosvg>=2.5.0"]
  ```
- Wrap imports in try/except to handle missing optional deps

## Suggested Tests

- `test_png_render_produces_bytes`: `render_png()` returns non-empty bytes
- `test_png_valid_format`: Output bytes start with PNG magic number
- `test_png_cli_creates_file`: CLI with `--format png` creates file
- `test_png_missing_deps_message`: Without deps, helpful error shown
- `test_svg_still_default`: Without `--format`, SVG is created

## Public APIs

- `lsystem.render_png.render_png(segments: list[Segment], width: int = 800, height: int = 600, ...) -> bytes`
- `lsystem.render_png.save_png(content: bytes, path: Path) -> None`
- `lsystem.render_png.PNG_AVAILABLE: bool` (True if deps installed)
