"""Safe file writing: atomic writes, overwrite logic, manifest output."""

from __future__ import annotations

import glob
import json
import os
import tempfile


def _atomic_write(path: str, content: str) -> None:
    """Write *content* atomically: temp file → fsync → os.replace."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check_overwrite(outdir: str, overwrite: bool) -> None:
    """Refuse to proceed if outdir has existing WO-*.json or manifest, unless overwrite is set.

    If overwrite is True, delete only WO-*.json and WORK_ORDERS_MANIFEST.json.
    """
    if not os.path.isdir(outdir):
        return  # nothing to check

    wo_files = glob.glob(os.path.join(outdir, "WO-*.json"))
    manifest = os.path.join(outdir, "WORK_ORDERS_MANIFEST.json")
    has_manifest = os.path.isfile(manifest)

    if not wo_files and not has_manifest:
        return  # clean directory

    if not overwrite:
        raise FileExistsError(
            f"Output directory '{outdir}' already contains work order files. "
            "Pass --overwrite to replace them."
        )

    # Delete only WO-*.json and manifest
    for f in wo_files:
        os.unlink(f)
    if has_manifest:
        os.unlink(manifest)


def write_work_orders(
    outdir: str,
    work_orders: list[dict],
    manifest: dict,
) -> list[str]:
    """Write individual WO-*.json files and then WORK_ORDERS_MANIFEST.json.

    Returns list of written file paths.
    """
    os.makedirs(outdir, exist_ok=True)
    written: list[str] = []

    for wo in work_orders:
        wo_id = wo["id"]
        filename = f"{wo_id}.json"
        path = os.path.join(outdir, filename)
        content = json.dumps(wo, indent=2, sort_keys=False) + "\n"
        _atomic_write(path, content)
        written.append(path)

    # Manifest is written LAST
    manifest_path = os.path.join(outdir, "WORK_ORDERS_MANIFEST.json")
    manifest_content = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    _atomic_write(manifest_path, manifest_content)
    written.append(manifest_path)

    return written


def write_json_artifact(path: str, data: object) -> None:
    """Write a JSON artifact file."""
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, content)


def write_text_artifact(path: str, text: str) -> None:
    """Write a text artifact file."""
    _atomic_write(path, text)
