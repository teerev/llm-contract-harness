"""Tests for factory/runtime.py — target-repo venv management.

Coverage:
  1. venv_env: PATH prefixed, VIRTUAL_ENV set, sandbox vars preserved.
  2. ensure_repo_venv (no network): venv creation + python invocation.
  3. ensure_repo_venv: pip-install-pytest command would be invoked (mocked).
  4. PO node passes venv env to run_command (mock assertion on call site).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.runtime import (
    LLMCH_VENV_DIR,
    _MARKER_FILE,
    _venv_python,
    ensure_repo_venv,
    venv_env,
)


# ---------------------------------------------------------------------------
# venv_env — unit tests
# ---------------------------------------------------------------------------


class TestVenvEnv:
    """Test that venv_env produces correct PATH and VIRTUAL_ENV."""

    def test_path_is_prefixed(self, tmp_path):
        venv_root = tmp_path / ".llmch_venv"
        venv_root.mkdir()
        base_env = {"PATH": "/usr/bin:/bin", "HOME": "/home/test"}

        result = venv_env(venv_root, base_env)

        expected_bin = str(venv_root / "bin")
        assert result["PATH"].startswith(expected_bin + os.pathsep)
        # Original PATH is preserved after the prefix
        assert "/usr/bin:/bin" in result["PATH"]

    def test_virtual_env_set(self, tmp_path):
        venv_root = tmp_path / ".llmch_venv"
        venv_root.mkdir()
        base_env = {"PATH": "/usr/bin"}

        result = venv_env(venv_root, base_env)

        assert result["VIRTUAL_ENV"] == str(venv_root)

    def test_sandbox_vars_preserved(self, tmp_path):
        venv_root = tmp_path / ".llmch_venv"
        venv_root.mkdir()
        base_env = {
            "PATH": "/usr/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
        }

        result = venv_env(venv_root, base_env)

        assert result["PYTHONDONTWRITEBYTECODE"] == "1"
        assert result["PYTEST_ADDOPTS"] == "-p no:cacheprovider"

    def test_base_env_not_mutated(self, tmp_path):
        venv_root = tmp_path / ".llmch_venv"
        venv_root.mkdir()
        base_env = {"PATH": "/usr/bin", "FOO": "bar"}
        original = dict(base_env)

        venv_env(venv_root, base_env)

        assert base_env == original

    def test_accepts_string_path(self, tmp_path):
        venv_root = str(tmp_path / ".llmch_venv")
        os.makedirs(venv_root)
        base_env = {"PATH": "/usr/bin"}

        result = venv_env(venv_root, base_env)

        assert "VIRTUAL_ENV" in result
        assert result["VIRTUAL_ENV"] == venv_root


# ---------------------------------------------------------------------------
# ensure_repo_venv — venv creation (no network: install_pytest=False)
# ---------------------------------------------------------------------------


class TestEnsureRepoVenv:
    """Test venv creation without hitting the network.

    Uses install_pytest=False to avoid requiring pip network access.
    Verifies:
      - .llmch_venv directory is created
      - Python inside it is invocable
      - Marker file is written
    """

    def test_creates_venv(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        venv_root = ensure_repo_venv(str(repo), install_pytest=False)

        assert venv_root.is_dir()
        assert venv_root.name == LLMCH_VENV_DIR
        assert (venv_root / _MARKER_FILE).is_file()

    def test_python_is_invocable(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        venv_root = ensure_repo_venv(str(repo), install_pytest=False)
        python = _venv_python(venv_root)

        # The venv python must be able to print its own executable path.
        result = subprocess.run(
            [str(python), "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0
        exe_path = result.stdout.decode().strip()
        # The executable should be inside the venv
        assert LLMCH_VENV_DIR in exe_path

    def test_idempotent_when_marker_exists(self, tmp_path):
        """Second call with existing marker should be a fast no-op."""
        repo = tmp_path / "repo"
        repo.mkdir()

        venv_root_1 = ensure_repo_venv(str(repo), install_pytest=False)
        # Touch the marker to prove it exists
        assert (venv_root_1 / _MARKER_FILE).is_file()

        # Second call should return the same path without re-creating
        venv_root_2 = ensure_repo_venv(str(repo), install_pytest=False)
        assert venv_root_1 == venv_root_2


# ---------------------------------------------------------------------------
# ensure_repo_venv — pip install pytest (mocked)
# ---------------------------------------------------------------------------


class TestEnsureRepoVenvPytestInstall:
    """Verify that install_pytest=True invokes pip install pytest."""

    def test_pip_install_pytest_called(self, tmp_path):
        """When install_pytest=True and venv is fresh, pip install pytest
        must be invoked with the venv python."""
        repo = tmp_path / "repo"
        repo.mkdir()

        calls = []
        original_run = subprocess.run

        def _capture_run(cmd, **kwargs):
            calls.append(cmd)
            return original_run(cmd, **kwargs)

        with patch("factory.runtime.subprocess.run", side_effect=_capture_run):
            ensure_repo_venv(str(repo), install_pytest=True)

        # Find the "pip install pytest" call:
        # command shape is [<python>, "-m", "pip", "install", "pytest"]
        pip_calls = [
            c for c in calls
            if len(c) >= 5 and c[-1] == "pytest" and "pip" in c
        ]
        assert len(pip_calls) >= 1, (
            f"Expected a 'pip install pytest' call, got: {calls!r}"
        )

    def test_no_pip_call_when_disabled(self, tmp_path):
        """With install_pytest=False, pip install pytest must NOT be called."""
        repo = tmp_path / "repo"
        repo.mkdir()

        calls = []
        original_run = subprocess.run

        def _capture_run(cmd, **kwargs):
            calls.append(cmd)
            return original_run(cmd, **kwargs)

        with patch("factory.runtime.subprocess.run", side_effect=_capture_run):
            ensure_repo_venv(str(repo), install_pytest=False)

        pip_pytest_calls = [
            c for c in calls
            if len(c) >= 5 and c[-1] == "pytest" and "pip" in c
        ]
        assert pip_pytest_calls == [], (
            f"pip install pytest should not be called: {calls!r}"
        )


# ---------------------------------------------------------------------------
# PO node uses venv env — integration check (mocked run_command)
# ---------------------------------------------------------------------------


class TestPONodeUsesVenvEnv:
    """Verify that nodes_po.po_node passes command_env to run_command.

    Modified call site: factory/nodes_po.py::po_node (verify + acceptance).
    """

    def test_verify_receives_command_env(self, tmp_path):
        """When state has command_env, verify run_command calls use it."""
        from factory.nodes_po import po_node

        repo = str(tmp_path / "repo")
        os.makedirs(repo)
        attempt_dir = str(tmp_path / "out" / "run1" / "attempt_1")

        fake_env = {"PATH": "/fake/bin:/usr/bin", "VIRTUAL_ENV": "/fake"}
        state = {
            "work_order": {
                "id": "WO-01",
                "title": "Test",
                "intent": "Test",
                "allowed_files": ["hello.txt"],
                "forbidden": [],
                "acceptance_commands": ["python -c 'print(1)'"],
                "context_files": [],
                "notes": None,
                "verify_exempt": True,
            },
            "repo_root": repo,
            "timeout_seconds": 30,
            "attempt_index": 1,
            "run_id": "run1",
            "out_dir": str(tmp_path / "out"),
            "command_env": fake_env,
        }

        captured_envs = []
        fake_result = MagicMock()
        fake_result.exit_code = 0
        fake_result.model_dump.return_value = {
            "command": ["python", "-m", "compileall", "-q", "."],
            "exit_code": 0,
            "stdout_trunc": "",
            "stderr_trunc": "",
            "stdout_path": "",
            "stderr_path": "",
            "duration_seconds": 0.1,
        }

        original_run_command = None

        def _mock_run_command(cmd, cwd, timeout, stdout_path, stderr_path, env=None):
            captured_envs.append(env)
            return fake_result

        with patch("factory.nodes_po.run_command", side_effect=_mock_run_command):
            po_node(state)

        # At least one run_command call should have received our fake_env
        assert any(e is fake_env for e in captured_envs), (
            f"Expected command_env to be passed to run_command, got: {captured_envs!r}"
        )

    def test_no_command_env_falls_back(self, tmp_path):
        """When state has no command_env, run_command receives env=None."""
        from factory.nodes_po import po_node

        repo = str(tmp_path / "repo")
        os.makedirs(repo)

        state = {
            "work_order": {
                "id": "WO-01",
                "title": "Test",
                "intent": "Test",
                "allowed_files": ["hello.txt"],
                "forbidden": [],
                "acceptance_commands": ["python -c 'print(1)'"],
                "context_files": [],
                "notes": None,
                "verify_exempt": True,
            },
            "repo_root": repo,
            "timeout_seconds": 30,
            "attempt_index": 1,
            "run_id": "run1",
            "out_dir": str(tmp_path / "out"),
            # No command_env key
        }

        captured_envs = []
        fake_result = MagicMock()
        fake_result.exit_code = 0
        fake_result.model_dump.return_value = {
            "command": ["python", "-m", "compileall", "-q", "."],
            "exit_code": 0, "stdout_trunc": "", "stderr_trunc": "",
            "stdout_path": "", "stderr_path": "", "duration_seconds": 0.1,
        }

        def _mock_run_command(cmd, cwd, timeout, stdout_path, stderr_path, env=None):
            captured_envs.append(env)
            return fake_result

        with patch("factory.nodes_po.run_command", side_effect=_mock_run_command):
            po_node(state)

        # All calls should have env=None (fallback to _sandboxed_env inside run_command)
        assert all(e is None for e in captured_envs), (
            f"Expected env=None (fallback), got: {captured_envs!r}"
        )


# ---------------------------------------------------------------------------
# run_command respects env parameter
# ---------------------------------------------------------------------------


class TestRunCommandEnvParam:
    """Verify that run_command uses the provided env dict."""

    def test_custom_env_used(self, tmp_path):
        """When env is passed, subprocess.run should receive it."""
        from factory.util import run_command

        stdout_path = str(tmp_path / "stdout.txt")
        stderr_path = str(tmp_path / "stderr.txt")

        # Use a real command that prints PATH
        result = run_command(
            cmd=["python", "-c", "import os; print(os.environ.get('MY_TEST_VAR', ''))"],
            cwd=str(tmp_path),
            timeout=30,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            env={
                **os.environ,
                "MY_TEST_VAR": "hello_from_test",
            },
        )

        assert result.exit_code == 0
        assert "hello_from_test" in result.stdout_trunc

    def test_default_env_when_none(self, tmp_path):
        """When env=None, the default sandboxed env is used."""
        from factory.util import run_command

        stdout_path = str(tmp_path / "stdout.txt")
        stderr_path = str(tmp_path / "stderr.txt")

        result = run_command(
            cmd=["python", "-c", "import os; print(os.environ.get('PYTHONDONTWRITEBYTECODE', ''))"],
            cwd=str(tmp_path),
            timeout=30,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            # env=None (default)
        )

        assert result.exit_code == 0
        assert "1" in result.stdout_trunc
