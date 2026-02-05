---
title: "P0.02 - Pytest and linting infrastructure"
repo: https://github.com/teerev/lsystem-plants
clone_branch: aos/lsystem
push_branch: aos/lsystem
max_iterations: 10
context_files:
  - "pyproject.toml"

# Quality gates
min_assertions: 2
coverage_threshold: 60
min_tests: 1
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pip install -e ."
  - "pytest -v"
  - "ruff check src/"
  - "mypy src/lsystem --ignore-missing-imports"
---

# P0.02 - Pytest and Linting Infrastructure

## Goal

Establish the test infrastructure with pytest and configure linting/type-checking tools for code quality.

## Scope

### In Scope
- Create `tests/` directory structure
- Add `tests/conftest.py` with shared fixtures
- Configure pytest in `pyproject.toml`
- Add `pytest-cov` for coverage reporting
- Configure `ruff` for linting in `pyproject.toml`
- Configure `mypy` for type checking in `pyproject.toml`
- Create sample test to verify setup works

### Out of Scope
- Comprehensive test suites (those come with features)
- CI/CD configuration

## Design Constraints

- Tests must be discoverable via `pytest` with no extra arguments
- Use `pytest-cov` for coverage; aim for coverage reporting from the start
- Fixtures should be composable and well-documented
- Test files follow `test_*.py` naming convention
- Ruff rules should be sensible defaults (not overly strict)

## Acceptance Criteria

- [ ] `pytest` discovers and runs tests successfully
- [ ] `tests/conftest.py` exists with at least one fixture (e.g., `tmp_path` wrapper)
- [ ] `pytest --cov=lsystem` produces coverage report
- [ ] `pyproject.toml` contains `[tool.pytest.ini_options]`
- [ ] `ruff check src/` passes with no errors
- [ ] `mypy src/lsystem` passes with no errors
- [ ] At least one passing test exists

## Notes for Implementation

- **PROJECT LAYOUT**: Source files go in `src/lsystem/`. Imports use `from lsystem import ...` (NOT `from src.lsystem`). Tests go in `tests/`.
- Add `pytest`, `pytest-cov`, `ruff`, `mypy` to dev dependencies
- Set `testpaths = ["tests"]` in pytest config
- Add a `tmp_output_dir` fixture for file I/O tests (SVG output testing later)

## Suggested Tests

- `test_sanity_check`: A trivial assertion that always passes
- `test_fixture_available`: Verify a conftest fixture is injectable

## Public APIs

No new public APIs. Test and lint infrastructure only.
