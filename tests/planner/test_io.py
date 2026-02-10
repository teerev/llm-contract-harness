"""Tests for planner/io.py — atomic writes, overwrite logic, manifest output."""

from __future__ import annotations

import json
import os

import pytest

from planner.io import (
    check_overwrite,
    write_json_artifact,
    write_text_artifact,
    write_work_orders,
)


# ---------------------------------------------------------------------------
# check_overwrite
# ---------------------------------------------------------------------------


class TestCheckOverwrite:
    def test_nonexistent_dir_passes(self, tmp_path):
        """No directory at all → no error."""
        check_overwrite(str(tmp_path / "nope"), overwrite=False)

    def test_empty_dir_passes(self, tmp_path):
        """Existing but empty dir → no error."""
        check_overwrite(str(tmp_path), overwrite=False)

    def test_dir_with_unrelated_files_passes(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hi")
        check_overwrite(str(tmp_path), overwrite=False)

    def test_dir_with_wo_files_raises_without_flag(self, tmp_path):
        (tmp_path / "WO-01.json").write_text("{}")
        with pytest.raises(FileExistsError, match="overwrite"):
            check_overwrite(str(tmp_path), overwrite=False)

    def test_dir_with_manifest_raises_without_flag(self, tmp_path):
        (tmp_path / "WORK_ORDERS_MANIFEST.json").write_text("{}")
        with pytest.raises(FileExistsError, match="overwrite"):
            check_overwrite(str(tmp_path), overwrite=False)

    def test_overwrite_deletes_wo_and_manifest(self, tmp_path):
        (tmp_path / "WO-01.json").write_text("{}")
        (tmp_path / "WO-02.json").write_text("{}")
        (tmp_path / "WORK_ORDERS_MANIFEST.json").write_text("{}")
        (tmp_path / "readme.txt").write_text("keep me")

        check_overwrite(str(tmp_path), overwrite=True)

        assert not (tmp_path / "WO-01.json").exists()
        assert not (tmp_path / "WO-02.json").exists()
        assert not (tmp_path / "WORK_ORDERS_MANIFEST.json").exists()
        assert (tmp_path / "readme.txt").exists()  # unrelated preserved


# ---------------------------------------------------------------------------
# write_work_orders
# ---------------------------------------------------------------------------


def _sample_wo(wo_id: str) -> dict:
    return {
        "id": wo_id,
        "title": f"Test {wo_id}",
        "intent": "test",
        "allowed_files": ["a.py"],
        "forbidden": [],
        "acceptance_commands": ['python -c "assert True"'],
        "context_files": ["a.py"],
        "notes": None,
    }


class TestWriteWorkOrders:
    def test_creates_wo_files_and_manifest(self, tmp_path):
        outdir = str(tmp_path / "out")
        wos = [_sample_wo("WO-01"), _sample_wo("WO-02")]
        manifest = {"work_orders": wos}

        written = write_work_orders(outdir, wos, manifest)

        assert os.path.isfile(os.path.join(outdir, "WO-01.json"))
        assert os.path.isfile(os.path.join(outdir, "WO-02.json"))
        assert os.path.isfile(os.path.join(outdir, "WORK_ORDERS_MANIFEST.json"))
        assert len(written) == 3

    def test_wo_files_contain_valid_json(self, tmp_path):
        outdir = str(tmp_path / "out")
        wos = [_sample_wo("WO-01")]
        write_work_orders(outdir, wos, {"work_orders": wos})

        with open(os.path.join(outdir, "WO-01.json")) as f:
            data = json.load(f)
        assert data["id"] == "WO-01"

    def test_manifest_written_last(self, tmp_path):
        """Manifest path is the last element in the returned list."""
        outdir = str(tmp_path / "out")
        wos = [_sample_wo("WO-01"), _sample_wo("WO-02")]
        written = write_work_orders(outdir, wos, {"work_orders": wos})
        assert written[-1].endswith("WORK_ORDERS_MANIFEST.json")

    def test_creates_outdir_if_missing(self, tmp_path):
        outdir = str(tmp_path / "deep" / "nested" / "out")
        wos = [_sample_wo("WO-01")]
        write_work_orders(outdir, wos, {"work_orders": wos})
        assert os.path.isdir(outdir)


# ---------------------------------------------------------------------------
# write_json_artifact / write_text_artifact
# ---------------------------------------------------------------------------


class TestArtifactWriters:
    def test_write_json_artifact(self, tmp_path):
        path = str(tmp_path / "data.json")
        write_json_artifact(path, {"key": "value"})
        with open(path) as f:
            data = json.load(f)
        assert data == {"key": "value"}

    def test_write_text_artifact(self, tmp_path):
        path = str(tmp_path / "data.txt")
        write_text_artifact(path, "hello world")
        with open(path) as f:
            assert f.read() == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "c.json")
        write_json_artifact(path, [1, 2, 3])
        assert os.path.isfile(path)
