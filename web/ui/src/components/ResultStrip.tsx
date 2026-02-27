import React from "react";
import type { PipelineStatus, PushResult } from "../hooks/useRunEvents";
import styles from "./ResultStrip.module.css";

interface Props {
  pipelineStatus: PipelineStatus;
  woPassCount: number;
  woFailCount: number;
  fileCount: number;
  error?: string;
  pushResult?: PushResult;
}

export default function ResultStrip({
  pipelineStatus,
  woPassCount,
  woFailCount,
  fileCount,
  error,
  pushResult,
}: Props): React.JSX.Element {
  const isComplete = pipelineStatus === "complete";
  const isFailed = pipelineStatus === "failed";
  const isRunning = !isComplete && !isFailed && pipelineStatus !== "idle";
  const showSummary = isComplete || isFailed;

  return (
    <footer className={styles.strip}>
      <span className={styles.statusBadge} data-status={pipelineStatus}>
        {pipelineStatus === "idle" && "Ready"}
        {pipelineStatus === "planning" && "Planning..."}
        {pipelineStatus === "building" && "Building..."}
        {pipelineStatus === "pushing" && "Pushing..."}
        {pipelineStatus === "complete" && "Complete"}
        {pipelineStatus === "failed" && "Failed"}
      </span>

      {showSummary && (
        <span className={styles.summary}>
          WO: {woPassCount} passed{woFailCount > 0 && `, ${woFailCount} failed`}
          {" · "}
          Files: {fileCount}
        </span>
      )}

      {pushResult && (
        <span className={pushResult.ok ? styles.pushSuccess : styles.pushError}>
          {pushResult.ok ? (
            <>
              Pushed to{" "}
              {pushResult.url ? (
                <a
                  href={pushResult.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={styles.pushLink}
                >
                  {pushResult.branch}
                </a>
              ) : (
                <span>{pushResult.branch}</span>
              )}
              {pushResult.commitSha && (
                <span className={styles.commitSha}> ({pushResult.commitSha})</span>
              )}
            </>
          ) : (
            <>Push failed: {pushResult.error || "unknown error"}</>
          )}
        </span>
      )}

      {error && !pushResult?.error && (
        <span className={styles.errorText}>{error}</span>
      )}

      {isRunning && <span className={styles.spinner} />}
    </footer>
  );
}
