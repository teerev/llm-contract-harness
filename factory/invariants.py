"""
Factory-owned invariant checks that run independent of SE.

These checks cannot be gamed by SE because SE cannot modify this file.
The invariant harness provides an independent verification layer that runs
after acceptance commands, checking properties that the SE cannot circumvent.

This module is the foundation for Layer 2 of the 3-layer verification gate:
- Layer 1: SE-authored tests (low trust - SE controls both code and tests)
- Layer 2: Factory-owned invariants (medium trust - SE cannot modify these checks)
- Layer 3: Verifier LLM (high trust - independent adversarial review)
"""

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

from .schemas import InvariantResult, InvariantReport


# =============================================================================
# Individual Invariant Checks
# =============================================================================

def _check_tests_exist(workspace: Path) -> InvariantResult:
    """
    Check that test files exist in the workspace.
    
    This prevents SE from submitting code without any tests at all.
    Looks for standard pytest naming conventions: test_*.py and *_test.py
    """
    test_patterns = [
        "tests/**/test_*.py",
        "tests/**/*_test.py",
        "**/test_*.py",
        "**/*_test.py",
    ]
    
    test_files: list[Path] = []
    for pattern in test_patterns:
        test_files.extend(workspace.glob(pattern))
    
    # Deduplicate (patterns may overlap)
    test_files = list(set(test_files))
    
    if not test_files:
        return InvariantResult(
            passed=False,
            check_name="tests_exist",
            message="No test files found. Expected test_*.py or *_test.py files.",
            details={"patterns_checked": test_patterns}
        )
    
    return InvariantResult(
        passed=True,
        check_name="tests_exist",
        message=f"Found {len(test_files)} test file(s).",
        details={"test_files": [str(f.relative_to(workspace)) for f in sorted(test_files)[:10]]}
    )


def _check_compileall(workspace: Path, timeout_sec: int = 60) -> InvariantResult:
    """
    Check that all Python files compile without syntax errors.
    
    Uses Python's compileall module to verify syntax validity of all .py files.
    This catches syntax errors before acceptance commands run, providing
    early feedback on broken code.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(workspace)],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        
        if result.returncode != 0:
            # Parse stderr to extract useful error info
            error_output = result.stderr.strip() or result.stdout.strip()
            return InvariantResult(
                passed=False,
                check_name="compileall",
                message="Python syntax errors detected.",
                details={
                    "returncode": result.returncode,
                    "stderr": error_output[:1000],
                }
            )
        
        return InvariantResult(
            passed=True,
            check_name="compileall",
            message="All Python files compile successfully.",
            details={}
        )
    except subprocess.TimeoutExpired:
        return InvariantResult(
            passed=False,
            check_name="compileall",
            message="Compileall timed out.",
            details={"timeout_sec": timeout_sec}
        )


def _check_tests_nontrivial(workspace: Path, min_assertions: int = 1) -> InvariantResult:
    """
    Check that test files contain meaningful assertions, not just `assert True`.
    
    Uses AST parsing to count assert statements and detect trivial ones.
    This prevents SE from gaming the tests_exist check by writing empty/trivial tests.
    
    Args:
        workspace: Path to the workspace directory
        min_assertions: Minimum number of meaningful assertions required
        
    Returns:
        InvariantResult indicating whether tests are non-trivial
    """
    test_files = list(workspace.glob("**/test_*.py")) + list(workspace.glob("**/*_test.py"))
    # Deduplicate
    test_files = list(set(test_files))
    
    if not test_files:
        return InvariantResult(
            passed=False,
            check_name="tests_nontrivial",
            message="No test files to analyze.",
            details={}
        )
    
    total_asserts = 0
    trivial_asserts = 0  # assert True, assert 1, etc.
    files_analyzed = 0
    
    for tf in test_files:
        try:
            source = tf.read_text()
            tree = ast.parse(source)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Assert):
                    total_asserts += 1
                    # Check for trivial assertions
                    if isinstance(node.test, ast.Constant):
                        if node.test.value in (True, 1, ""):
                            trivial_asserts += 1
            
            files_analyzed += 1
        except SyntaxError:
            pass  # compileall check handles syntax errors
    
    meaningful_asserts = total_asserts - trivial_asserts
    
    if meaningful_asserts < min_assertions:
        return InvariantResult(
            passed=False,
            check_name="tests_nontrivial",
            message=f"Only {meaningful_asserts} meaningful assertions found (minimum: {min_assertions}). "
                    f"Total: {total_asserts}, Trivial: {trivial_asserts}.",
            details={
                "total_asserts": total_asserts,
                "trivial_asserts": trivial_asserts,
                "meaningful_asserts": meaningful_asserts,
                "min_required": min_assertions,
                "files_analyzed": files_analyzed,
            }
        )
    
    return InvariantResult(
        passed=True,
        check_name="tests_nontrivial",
        message=f"Found {meaningful_asserts} meaningful assertions across {files_analyzed} files.",
        details={
            "total_asserts": total_asserts,
            "meaningful_asserts": meaningful_asserts,
            "files_analyzed": files_analyzed,
        }
    )


# =============================================================================
# Main Invariant Runner
# =============================================================================

def run_invariants(
    workspace: Path,
    se_packet: dict[str, Any],
    work_order: dict[str, Any],
) -> InvariantReport:
    """
    Run all invariant checks against the workspace.
    
    This function is called by the TR node AFTER acceptance commands,
    providing an independent verification layer that cannot be gamed by SE.
    
    Args:
        workspace: Path to the workspace directory where SE changes were applied
        se_packet: The SE's output packet containing writes and assumptions
        work_order: The work order configuration
        
    Returns:
        InvariantReport with all_passed flag and list of individual results
    """
    results: list[InvariantResult] = []
    
    # M2: Check that test files exist
    results.append(_check_tests_exist(workspace))
    
    # M3: Check that all Python files compile (syntax check)
    results.append(_check_compileall(workspace))
    
    # M5: Check that tests contain meaningful assertions (not just `assert True`)
    min_assertions = work_order.get("min_assertions", 1)
    results.append(_check_tests_nontrivial(workspace, min_assertions=min_assertions))
    
    # Future invariant checks:
    # - M6: _check_coverage
    # - M8: _check_tests_changed_with_src
    
    # Compute overall pass/fail
    all_passed = all(r.passed for r in results) if results else True
    
    return InvariantReport(all_passed=all_passed, results=results)
