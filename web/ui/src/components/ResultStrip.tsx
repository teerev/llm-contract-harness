import React, { useCallback, useState } from "react";
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
  const [copied, setCopied] = useState(false);

  const handleCopyUrl = useCallback(async () => {
    if (!pushResult?.url) return;
    try {
      await navigator.clipboard.writeText(pushResult.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may fail in some contexts
    }
  }, [pushResult?.url]);

  // Show prominent success banner when push succeeded
  if (isComplete && pushResult?.ok && pushResult.url) {
    return (
      <footer className={styles.successBanner}>
        <span className={styles.successIcon}>✓</span>
        <span className={styles.successText}>
          Success! Your code is live at{" "}
          <a
            href={pushResult.url}
            target="_blank"
            rel="noopener noreferrer"
            className={styles.successLink}
          >
            {pushResult.url}
          </a>
        </span>
        <button className={styles.copyBtn} onClick={handleCopyUrl} title="Copy URL">
          {copied ? "Copied!" : "Copy"}
        </button>
      </footer>
    );
  }

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

      {pushResult && pushResult.ok === false && (
        <span className={styles.pushError}>
          Push failed: {pushResult.error || "unknown error"}
        </span>
      )}

      {error && !pushResult?.error && (
        <span className={styles.errorText}>{error}</span>
      )}

      {isRunning && <span className={styles.spinner} />}
    </footer>
  );
}
