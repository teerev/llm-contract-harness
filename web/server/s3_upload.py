"""Optional end-of-run S3 upload for artifact durability.

Uploads the full artifact tree for a pipeline run to S3 under
``s3://{bucket}/runs/{run_id}/...``.  Skipped when ``LLMCH_S3_BUCKET``
is not set.

This is a Phase-2 addition: the pipeline still writes everything
locally during execution; this module uploads the results afterward.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

S3_BUCKET: str = os.environ.get("LLMCH_S3_BUCKET", "").strip()

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".llmch_venv",
    "node_modules", ".mypy_cache", ".venv", "venv",
})


def upload_run_artifacts(
    run_id: str,
    artifacts_dir: str,
    planner_run_id: str | None = None,
    factory_run_ids: list[str] | None = None,
) -> None:
    """Upload all artifacts for a pipeline run to S3.

    Silently returns if ``LLMCH_S3_BUCKET`` is not set or if boto3 is
    unavailable.  Errors are logged but never raised — S3 upload failure
    must not fail the pipeline.
    """
    if not S3_BUCKET:
        return

    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed — skipping S3 upload")
        return

    try:
        s3 = boto3.client("s3")
        prefix = f"runs/{run_id}"

        # Pipeline artifacts (meta.json, events.jsonl, manifest.json, spec.txt)
        pipeline_dir = os.path.join(artifacts_dir, "pipeline", run_id)
        if os.path.isdir(pipeline_dir):
            _upload_dir(s3, pipeline_dir, f"{prefix}/pipeline/{run_id}", skip_dirs={"repo"})

        # Planner artifacts
        if planner_run_id:
            planner_dir = os.path.join(artifacts_dir, "planner", planner_run_id)
            if os.path.isdir(planner_dir):
                _upload_dir(s3, planner_dir, f"{prefix}/planner/{planner_run_id}")

        # Factory artifacts (one dir per WO)
        for fid in (factory_run_ids or []):
            factory_dir = os.path.join(artifacts_dir, "factory", fid)
            if os.path.isdir(factory_dir):
                _upload_dir(s3, factory_dir, f"{prefix}/factory/{fid}")

        # Repo archive (tar.gz of final repo state, excluding .git)
        repo_dir = os.path.join(pipeline_dir, "repo")
        if os.path.isdir(repo_dir):
            _upload_repo_archive(s3, repo_dir, f"{prefix}/repo.tar.gz")

        logger.info("S3 upload complete: s3://%s/%s/", S3_BUCKET, prefix)

    except Exception:
        logger.exception("S3 upload failed for run %s (non-fatal)", run_id)


def _upload_dir(
    s3,  # noqa: ANN001
    local_dir: str,
    s3_prefix: str,
    skip_dirs: set[str] | None = None,
) -> None:
    """Upload all files in a local directory tree to S3."""
    skip = _SKIP_DIRS | (skip_dirs or set())
    for dirpath, dirnames, filenames in os.walk(local_dir):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fname in filenames:
            local_path = os.path.join(dirpath, fname)
            rel = os.path.relpath(local_path, local_dir)
            s3_key = f"{s3_prefix}/{rel}"
            try:
                s3.upload_file(local_path, S3_BUCKET, s3_key)
            except Exception:
                logger.warning("Failed to upload %s → s3://%s/%s", local_path, S3_BUCKET, s3_key)


def _upload_repo_archive(s3, repo_dir: str, s3_key: str) -> None:  # noqa: ANN001
    """Create a tar.gz of the repo (excluding .git) and upload to S3."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(repo_dir):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                arcname = os.path.relpath(full, repo_dir)
                try:
                    tar.add(full, arcname=arcname)
                except OSError:
                    pass
    buf.seek(0)
    s3.upload_fileobj(buf, S3_BUCKET, s3_key)
