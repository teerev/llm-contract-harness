# Pipeline Loop Example: From Ambiguous Human Request to Working Code

**Run ID**: `13244556-8917-4985-b03b-07c2d253c464`  
**Date**: February 3, 2026  
**Result**: ✅ SUCCEEDED after 5 iterations  
**Branch**: `aos/validator-lib2`

---

## Executive Summary

This document demonstrates the AOS pipeline's ability to transform ambiguous, poorly-written human requirements into production-quality code through iterative self-correction. The pipeline took 5 iterations to:

1. Parse naive human prose ("i need something that checks if the stuff they type in is actually valid")
2. Generate appropriate Python code with proper type hints
3. Self-correct through 4 failures, each time learning from the feedback
4. Produce final code passing all quality gates: 88% coverage, 11 tests, 30 assertions, clean mypy

---

## 1. The Input: Naive Human Prose

The work order was deliberately written to simulate a non-technical user's request. Key excerpts:

> *"ok so im making this website thing and users keep putting garbage in my forms and it breaks everything. like someone put their phone number in the email box?? and then my code crashed lol"*

> *"i dont really know python that well but something like..."*

> *"**required** - they have to fill this in. but like, empty is different from not there at all right? if they just hit space thats still "something" i think? actually wait no spaces dont count. hmm actually maybe empty string is ok but None isnt?? idk you figure it out"*

**Quality gates configured:**
- `min_assertions: 10` - Require meaningful test assertions
- `coverage_threshold: 85` - Require 85% test coverage
- `min_tests: 8` - Require at least 8 test functions
- `require_type_check: true` - Must pass mypy type checking

---

## 2. Iteration Progression

### Iteration 1: Initial Attempt ❌

**SE Summary**: 
> "Add a small validation library in validator.py with composable field checks... Include pytest coverage with >=8 tests and enough assertions to verify behavior."

**Result**: FAIL - Type checking failed (3 errors)

```
validator.py:168: error: Name "Pattern" already defined (possibly by an import)
test_validator.py:81: error: Too many arguments for "Pattern"
test_validator.py:81: error: Argument 2 to "add_check" has incompatible type
```

**Analysis**: SE created a `Pattern` class that conflicted with `typing.Pattern`. The import `from typing import Pattern` shadowed the class definition.

**Metrics achieved**:
- ✅ Tests exist: 1 file
- ✅ Assertions: 34 meaningful
- ✅ Coverage: 87%
- ✅ Tests: 8 passed
- ❌ **Type checking: 3 errors**

---

### Iteration 2: Fixing Type Issues, Breaks Coverage ❌

**SE Summary**:
> "Implement a small, chainable form validation library... add type hints that satisfy mypy."

**Result**: FAIL - Coverage dropped AND type checking still failing

```
Coverage 84% (below 85% threshold)
Type checking: 3 errors (same Pattern conflict)
```

**Analysis**: SE attempted to fix the type issues but broke test coverage in the process. The `Pattern` naming conflict persisted.

**Metrics achieved**:
- ✅ Tests exist: 1 file
- ✅ Assertions: 27 meaningful
- ❌ **Coverage: 84%** (below 85%)
- ✅ Tests: 9 passed
- ❌ **Type checking: 3 errors**

---

### Iteration 3: Overcorrection ❌

**SE Summary**:
> "fix mypy typing issues by tightening protocols and return types"

**Result**: FAIL - Type checking got WORSE (8 errors)

```
error: Argument 1 to "NumberRange" has incompatible type "int"; expected "Real | None"
(8 total errors)
```

**Analysis**: SE tried to use `numbers.Real` for type hints, but mypy doesn't fully support the numeric tower from the `numbers` module. This is a subtle Python typing gotcha.

**Metrics achieved**:
- ✅ Tests exist: 1 file  
- ✅ Assertions: 40 meaningful
- ✅ Coverage: 88%
- ✅ Tests: 9 passed
- ❌ **Type checking: 8 errors** (regression!)

---

### Iteration 4: Nearly There ❌

**SE Summary**:
> "keep mypy happy by tightening type annotations and avoiding Any leakage"

**Result**: FAIL - Down to 1 type error

```
validator.py:259: error: Incompatible types in assignment (expression has type "Check", variable has type "Required")
```

**Analysis**: Major progress. SE switched from `numbers.Real` to `float` for type hints, which mypy handles correctly. Only one assignment type mismatch remained.

**Metrics achieved**:
- ✅ Tests exist: 1 file
- ✅ Assertions: 43 meaningful
- ✅ Coverage: 91%
- ✅ Tests: 10 passed
- ❌ **Type checking: 1 error**

---

### Iteration 5: Success ✅

**SE Summary**:
> "Improve validator.py typing and semantics for optional fields and chaining; add pytest coverage... ensure mypy-friendly type checking invariant passes."

**Result**: PASS - All invariants satisfied

**Final Metrics**:
| Check | Result | Value |
|-------|--------|-------|
| tests_exist | ✅ | 1 test file |
| compileall | ✅ | No syntax errors |
| tests_nontrivial | ✅ | 30 meaningful assertions |
| coverage | ✅ | **88%** (threshold: 85%) |
| tests_changed_with_src | ✅ | Both updated |
| pytest_results | ✅ | **11 tests passed** |
| hypothesis_tests | ✅ | Advisory (none found) |
| type_checking | ✅ | **mypy clean** |

---

## 3. The Evolution of Code Quality

### Type Errors Progression

| Iteration | Type Errors | Primary Issue |
|-----------|-------------|---------------|
| 1 | 3 | `Pattern` name conflict with `typing.Pattern` |
| 2 | 3 | Same conflict (not addressed) |
| 3 | 8 | Used `numbers.Real` (mypy incompatible) |
| 4 | 1 | Assignment type mismatch |
| 5 | 0 | ✅ All resolved |

### Coverage Progression

| Iteration | Coverage | Tests | Assertions |
|-----------|----------|-------|------------|
| 1 | 87% | 8 | 34 |
| 2 | 84% ❌ | 9 | 27 |
| 3 | 88% | 9 | 40 |
| 4 | 91% | 10 | 43 |
| 5 | 88% | 11 | 30 |

---

## 4. Key Design Decisions Made by SE

Despite the vague input, the SE made sensible design decisions:

### Interpreting Ambiguous Requirements

**User said**: *"required - they have to fill this in. but like, empty is different from not there at all right? ... idk you figure it out"*

**SE decided**:
```python
def validate(self, field: str, value: Any, data: Mapping[str, Any]) -> List[str]:
    if field not in data:
        return [self.message]
    if value is None:
        return [self.message]
    if isinstance(value, str) and value.strip() == "":
        return [self.message]
    return []
```

- Missing key → required fails
- `None` → required fails  
- Whitespace-only string → required fails
- Empty string `""` → required fails (whitespace stripped)

### Creating a Clean API

**User said**: *"something like checker.add_check(...).add_check(...) on one line but thats not super important"*

**SE delivered**:
```python
def add_check(self: _TValidator, field: str, check: Check) -> _TValidator:
    self._checks.setdefault(field, []).append(check)
    return self  # Enables chaining
```

### Protocol-Based Type Safety

To satisfy mypy without overly constraining the API, SE used a Protocol:
```python
class Check(Protocol):
    """A validation check for a single field."""
    def validate(self, field: str, value: Any, data: Mapping[str, Any]) -> List[str]:
        ...
```

This allows any class with a matching `validate` method to be used as a check.

---

## 5. Final Output

### validator.py (212 lines)

A well-structured validation library with:
- `ValidationResult` dataclass with `.ok` property
- `Check` protocol for extensibility
- 6 built-in check types: `Required`, `TypeIs`, `Email`, `Length`, `NumberRange`, `Pattern`
- `Validator` class with chainable `add_check()` and `run()` methods
- Proper type hints throughout

### test_validator.py (105 lines)

Comprehensive test suite with:
- 11 test functions
- 30 meaningful assertions
- Coverage of all check types
- Edge cases (whitespace, type mismatches, etc.)

---

## 6. Lessons Demonstrated

### 1. Iterative Self-Correction Works

The pipeline recovered from:
- Naming conflicts (`Pattern` vs `typing.Pattern`)
- Coverage regressions (84% → 88%)
- Overcorrection (3 errors → 8 errors → 1 error → 0)

### 2. Type Checking Catches Real Issues

The `require_type_check: true` gate caught subtle bugs that tests alone missed:
- Import shadowing
- Incompatible type usage (`numbers.Real` vs `float`)
- Assignment type mismatches

### 3. Quality Gates Create Pressure for Good Code

By requiring:
- 85% coverage: SE couldn't take shortcuts
- 10+ assertions: Tests had to be meaningful
- mypy passing: Type hints had to be correct

### 4. Ambiguous Input → Reasonable Output

Despite phrases like "idk you figure it out", the SE:
- Made sensible default decisions
- Created a clean, Pythonic API
- Documented behavior in docstrings

---

## 7. Artifacts Reference

All artifacts for this run are stored at:
```
/tmp/aos/workspaces/13244556-8917-4985-b03b-07c2d253c464/artifacts/
```

| File | Description |
|------|-------------|
| `se_packet_iter_N.json` | SE's proposed changes for iteration N |
| `tool_report_iter_N.json` | TR's execution results (commands + invariants) |
| `po_report_iter_N.json` | PO's pass/fail decision and required fixes |
| `run_summary.json` | Final run outcome |

---

## 8. Conclusion

This example demonstrates that the AOS pipeline can:

1. **Parse Ambiguity**: Convert poorly-written human prose into structured requirements
2. **Self-Correct**: Learn from failures and progressively fix issues
3. **Maintain Quality**: Enforce coverage, type safety, and test meaningfulness
4. **Produce Real Code**: Output production-ready Python with proper structure

The 5-iteration journey from "idk you figure it out" to clean, typed, tested code validates the pipeline's design philosophy: **strict quality gates + iterative feedback = reliable automated code generation**.
