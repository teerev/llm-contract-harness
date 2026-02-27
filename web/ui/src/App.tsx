import { useState, useCallback } from "react";
import FileExplorer from "./components/FileExplorer";
import NodeDiagram from "./components/NodeDiagram";
import StreamPanel from "./components/StreamPanel";
import { useRunEvents } from "./hooks/useRunEvents";
import styles from "./App.module.css";

type UIStatus = "idle" | "running" | "complete" | "failed";

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [pushDemo, setPushDemo] = useState(false);
  const [branchName, setBranchName] = useState("");
  const [uiStatus, setUiStatus] = useState<UIStatus>("idle");
  const [runId, setRunId] = useState<string | null>(null);

  const runState = useRunEvents(runId);

  const derivedStatus: UIStatus =
    runState.pipelineStatus === "complete"
      ? "complete"
      : runState.pipelineStatus === "failed"
        ? "failed"
        : runState.pipelineStatus === "idle"
          ? uiStatus
          : "running";

  const isRunning = derivedStatus === "running";

  const canSubmit =
    !isRunning && prompt.trim().length > 0 && (!pushDemo || branchName.trim().length > 0);

  const fileExplorerKey = `${runId ?? "none"}-${runState.filesWritten.length}-${runState.artifactsWritten.length}`;

  const handleRun = useCallback(async () => {
    if (!canSubmit) return;
    setUiStatus("running");
    runState.reset();

    try {
      const res = await fetch("/api/v1/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: prompt.trim(),
          push_to_demo: pushDemo,
          branch_name: branchName.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setRunId(data.run_id);
    } catch {
      setUiStatus("failed");
    }
  }, [canSubmit, prompt, pushDemo, branchName, runState]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      handleRun();
    }
  }

  return (
    <div className={styles.layout}>
      {/* ── Header ── */}
      <header className={styles.header}>
        <div className={styles.promptRow}>
          <textarea
            className={styles.promptInput}
            placeholder="Describe the project you want to build..."
            rows={2}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            className={styles.runBtn}
            disabled={!canSubmit}
            onClick={handleRun}
          >
            {isRunning ? "Running..." : "Run"}
          </button>
        </div>
        <div className={styles.optionsRow}>
          <label className={styles.checkLabel}>
            <input
              type="checkbox"
              checked={pushDemo}
              onChange={(e) => setPushDemo(e.target.checked)}
            />
            Push to demo repo
          </label>
          {pushDemo && (
            <input
              className={styles.branchInput}
              placeholder="Branch name (required)"
              value={branchName}
              onChange={(e) => setBranchName(e.target.value)}
            />
          )}
        </div>
      </header>

      {/* ── Stream Panel ── */}
      <section className={styles.streamPanel}>
        <div className={styles.panelLabel}>Stream</div>
        <StreamPanel text={runState.streamText} isRunning={isRunning} />
      </section>

      {/* ── Node Diagram ── */}
      <section className={styles.diagramPanel}>
        <div className={styles.panelLabel}>Pipeline</div>
        <div className={styles.diagramContent}>
          <NodeDiagram
            plannerStatus={runState.plannerStatus}
            workOrders={runState.workOrders}
            pipelineComplete={runState.pipelineStatus === "complete"}
            pipelineFailed={runState.pipelineStatus === "failed"}
          />
        </div>
      </section>

      {/* ── File Explorer ── */}
      <section className={styles.explorerPanel}>
        <FileExplorer key={fileExplorerKey} runId={runId} />
      </section>

      {/* ── Result Strip ── */}
      <footer className={styles.resultStrip}>
        <span className={styles.statusBadge} data-status={derivedStatus}>
          {derivedStatus === "idle" && "Ready"}
          {derivedStatus === "running" && `Running (${runState.pipelineStatus})...`}
          {derivedStatus === "complete" && "Complete"}
          {derivedStatus === "failed" && "Failed"}
        </span>
        {(derivedStatus === "complete" || derivedStatus === "failed") && (
          <span className={styles.summary}>
            WO: {runState.woPassCount} passed, {runState.woFailCount} failed
            {" · "}
            Files: {runState.fileCount}
          </span>
        )}
        {runState.error && (
          <span className={styles.errorText}>{runState.error}</span>
        )}
      </footer>
    </div>
  );
}
