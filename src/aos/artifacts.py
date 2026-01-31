"""
Artifact management for AOS.

Artifacts are files created during a run that capture the state
of each phase (SE, TR, PO) for debugging and auditing.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .db import Artifact


def save_artifact(
    session: Session,
    run_id: UUID,
    artifact_dir: Path,
    name: str,
    data: dict[str, Any],
    content_type: str = "application/json",
) -> Artifact:
    """
    Save a JSON artifact to disk and record in database.
    
    Args:
        session: SQLAlchemy session
        run_id: The run this artifact belongs to
        artifact_dir: Directory to save artifacts in
        name: Artifact name (e.g., "se_packet_iter_1.json")
        data: Data to serialize as JSON
        content_type: MIME type
    
    Returns:
        The created Artifact record
    """
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    
    # Serialize to JSON
    content = json.dumps(data, indent=2, default=str)
    content_bytes = content.encode("utf-8")
    
    # Write file
    file_path = artifact_dir / name
    file_path.write_text(content)
    
    # Compute hash
    sha256 = hashlib.sha256(content_bytes).hexdigest()
    
    # Record in database
    artifact = Artifact(
        run_id=run_id,
        name=name,
        path=str(file_path),
        content_type=content_type,
        bytes=len(content_bytes),
        sha256=sha256,
        created_at=datetime.utcnow(),
    )
    session.add(artifact)
    session.flush()
    
    return artifact


def save_iteration_artifacts(
    session: Session,
    run_id: UUID,
    artifact_dir: Path,
    iteration: int,
    se_packet: dict | None,
    tool_report: dict | None,
    po_report: dict | None,
) -> list[Artifact]:
    """
    Save all artifacts for an iteration.
    
    Args:
        session: SQLAlchemy session
        run_id: The run this belongs to
        artifact_dir: Directory to save artifacts in
        iteration: Iteration number
        se_packet: SE output (may be None)
        tool_report: TR output (may be None)
        po_report: PO output (may be None)
    
    Returns:
        List of created Artifact records
    """
    artifacts = []
    
    if se_packet:
        artifacts.append(save_artifact(
            session, run_id, artifact_dir,
            f"se_packet_iter_{iteration}.json",
            se_packet,
        ))
    
    if tool_report:
        artifacts.append(save_artifact(
            session, run_id, artifact_dir,
            f"tool_report_iter_{iteration}.json",
            tool_report,
        ))
    
    if po_report:
        artifacts.append(save_artifact(
            session, run_id, artifact_dir,
            f"po_report_iter_{iteration}.json",
            po_report,
        ))
    
    return artifacts


def save_run_summary(
    session: Session,
    run_id: UUID,
    artifact_dir: Path,
    summary: dict[str, Any],
) -> Artifact:
    """
    Save the final run summary artifact.
    """
    return save_artifact(
        session, run_id, artifact_dir,
        "run_summary.json",
        summary,
    )
