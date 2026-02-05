---
title: "P0.01 - Initialize project structure"
repo: "~/repos/lsystem-plants"
acceptance_commands:
  - "pip install -e ."
  - "python -c 'import lsystem; print(lsystem.__version__)'"
---



# P0.01 - Initialize Project Structure

## Goal

Create the foundational Python project structure with proper packaging, dependency management, and a minimal installable package for the L-system plant generator.

## Scope

### In Scope
- Create `pyproject.toml` with project metadata and dependencies
- Create `src/lsystem/` package directory with `__init__.py`
- Add initial development dependencies (pytest, ruff, mypy)
- Create a basic `README.md` with project description
- Create `.gitignore` for Python artifacts
- Ensure package is pip-installable in editable mode

### Out of Scope
- CLI implementation (later work order)
- L-system logic
- Rendering logic
- Any runtime dependencies beyond stdlib

## Design Constraints

- Use `src/` layout for package structure
- Use `pyproject.toml` (PEP 517/518) rather than `setup.py`
- Python 3.10+ required
- No external runtime dependencies in this work order (rendering deps added later)

## Acceptance Criteria

- [ ] `pyproject.toml` exists with valid metadata and build system configuration
- [ ] `src/lsystem/__init__.py` exists and defines `__version__`
- [ ] `pip install -e .` succeeds without errors
- [ ] `python -c "import lsystem; print(lsystem.__version__)"` prints version string
- [ ] `README.md` contains project name and brief description
- [ ] `.gitignore` covers standard Python artifacts

## Notes for Implementation

- Keep dependencies minimal; add only what's needed for the skeleton
- Use `hatchling` or `setuptools` as build backend
- Version should be `0.1.0` initially
- Package name: `lsystem-plants` (import as `lsystem`)

## Suggested Tests

- `test_package_importable`: Verify `import lsystem` succeeds
- `test_version_defined`: Verify `lsystem.__version__` is a valid semver string

## Public APIs

- `lsystem.__version__`: Package version string
