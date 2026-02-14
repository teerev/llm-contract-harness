"""Target-repo runtime management: dedicated venv for verify/acceptance commands.

The factory must own the Python runtime used inside the target repo so that
verify scripts and acceptance commands (e.g. ``bash scripts/verify.sh``,
``python -m pytest``) resolve to a controlled environment with ``pytest``
installed — regardless of what the harness repo's own ``.venv`` contains.

The venv is created at ``REPO/.llmch_venv/`` and used for all PO-stage
subprocesses via ``venv_env()``.

Hypothesis evidence (verified before implementation):
  H1: Verify/acceptance commands run via ``factory/util.py::run_command``.
  H2: ``run_command`` uses ``cwd=repo_root`` and env from ``_sandboxed_env()``.
  H3: Preflight in ``factory/run.py`` is the single place for repo setup.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LLMCH_VENV_DIR = ".llmch_venv"
"""Name of the per-target-repo venv directory."""

_MARKER_FILE = ".llmch_ok"
"""Sentinel inside the venv indicating a successful setup pass."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_repo_venv(
    repo_root: str,
    *,
    install_pytest: bool = True,
    python: str | None = None,
) -> Path:
    """Ensure a usable venv exists at ``repo_root/.llmch_venv``.

    Creates the venv if missing, upgrades pip, and installs ``pytest``
    (unless *install_pytest* is False — used in tests to avoid network).

    *python* overrides the base interpreter; defaults to ``sys.executable``
    (the harness Python).  The ``--python`` CLI flag surfaces this.

    Returns the venv root path.

    Raises ``RuntimeError`` on any failure so the factory can fail fast
    with an actionable message.
    """
    venv_root = Path(repo_root) / LLMCH_VENV_DIR
    venv_python = _venv_python(venv_root)

    base_python = python or sys.executable

    # Fast path: venv already exists, marker is present, AND the python
    # binary is actually there.  If the marker exists but the binary is
    # missing (e.g. partial delete, disk corruption), we fall through
    # and rebuild.  This guards against H3 (stale marker after rollback
    # or partial install).
    if venv_root.is_dir() and (venv_root / _MARKER_FILE).is_file():
        if venv_python.is_file():
            return venv_root
        # Marker exists but python missing → corrupted; remove stale
        # marker so we rebuild below.
        (venv_root / _MARKER_FILE).unlink(missing_ok=True)

    # --- Create venv --------------------------------------------------
    try:
        subprocess.run(
            [base_python, "-m", "venv", str(venv_root)],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise RuntimeError(
            f"Failed to create venv at {venv_root}:\n{stderr}"
        ) from exc

    if not venv_python.is_file():
        raise RuntimeError(
            f"Venv created but python not found at {venv_python}"
        )

    # --- Upgrade pip (best-effort) ------------------------------------
    try:
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.CalledProcessError:
        pass  # Non-fatal: pip may already be current

    # --- Install pytest -----------------------------------------------
    if install_pytest:
        try:
            subprocess.run(
                [str(venv_python), "-m", "pip", "install", "pytest"],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise RuntimeError(
                f"Failed to install pytest in {venv_root}:\n{stderr}"
            ) from exc

    # --- Write marker -------------------------------------------------
    (venv_root / _MARKER_FILE).write_text("ok\n")

    return venv_root


def venv_env(venv_root: str | Path, base_env: dict[str, str]) -> dict[str, str]:
    """Return a subprocess env dict that activates the venv at *venv_root*.

    - ``PATH`` is prefixed with the venv's ``bin/`` (POSIX) or ``Scripts/``
      (Windows) so that ``python``, ``pytest``, etc. resolve there first.
    - ``VIRTUAL_ENV`` is set.
    - Sandbox overrides (``PYTHONDONTWRITEBYTECODE``, ``PYTEST_ADDOPTS``)
      from *base_env* are preserved.
    - All other entries in *base_env* are kept.

    Cross-platform: uses ``Scripts`` on Windows, ``bin`` elsewhere.
    """
    venv_root = Path(venv_root)
    if sys.platform == "win32":
        bin_dir = str(venv_root / "Scripts")
    else:
        bin_dir = str(venv_root / "bin")

    env = dict(base_env)
    # Prefix PATH so venv python/pytest are found first.
    old_path = env.get("PATH", "")
    env["PATH"] = bin_dir + os.pathsep + old_path
    env["VIRTUAL_ENV"] = str(venv_root)
    return env


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _venv_python(venv_root: Path) -> Path:
    """Return the expected python binary path inside a venv."""
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"
