import { useState, useCallback, useEffect } from "react";
import FileExplorer from "./components/FileExplorer";
import NodeDiagram from "./components/NodeDiagram";
import StreamPanel from "./components/StreamPanel";
import ResultStrip from "./components/ResultStrip";
import { useRunEvents } from "./hooks/useRunEvents";
import styles from "./App.module.css";

type UIStatus = "idle" | "running" | "complete" | "failed";

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [pushDemo, setPushDemo] = useState(false);
  const [branchName, setBranchName] = useState("");
  const [uiStatus, setUiStatus] = useState<UIStatus>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [demoRemoteConfigured, setDemoRemoteConfigured] = useState<boolean | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const runState = useRunEvents(runId);

  useEffect(() => {
    fetch("/api/v1/config")
      .then((res) => res.json())
      .then((data) => setDemoRemoteConfigured(data.demo_remote_configured ?? false))
      .catch(() => setDemoRemoteConfigured(false));
  }, []);

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
    setSubmitError(null);
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
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(text || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setRunId(data.run_id);
    } catch (err) {
      setUiStatus("failed");
      setSubmitError(err instanceof Error ? err.message : "Failed to start run");
    }
  }, [canSubmit, prompt, pushDemo, branchName, runState]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      handleRun();
    }
  }

  const pushDisabled = demoRemoteConfigured === false;
  const showDisconnectBanner =
    runId && !runState.sseConnected && runState.error && derivedStatus === "running";

  return (
    <div className={styles.layout}>
      {/* ── Error banner (always rendered to keep grid child count stable) ── */}
      <div className={styles.errorBannerSlot}>
        {(submitError || showDisconnectBanner) && (
          <div className={styles.errorBanner}>
            <span>{submitError || runState.error}</span>
            {showDisconnectBanner && (
              <button className={styles.reconnectBtn} onClick={runState.reconnect}>
                Reconnect
              </button>
            )}
            {submitError && (
              <button
                className={styles.reconnectBtn}
                onClick={() => { setSubmitError(null); setUiStatus("idle"); }}
              >
                Dismiss
              </button>
            )}
          </div>
        )}
      </div>

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
          <div className={styles.runBtnGroup}>
            <button
              className={styles.runBtn}
              disabled={!canSubmit}
              onClick={handleRun}
            >
              {isRunning ? "Running..." : "Run"}
            </button>
            <kbd className={styles.kbdHint}>
              {navigator.platform?.includes("Mac") ? "\u2318" : "Ctrl"}+Enter
            </kbd>
          </div>
        </div>
        <div className={styles.optionsRow}>
          <label
            className={`${styles.checkLabel} ${pushDisabled ? styles.disabled : ""}`}
            title={pushDisabled ? "Demo remote not configured (set LLMCH_DEMO_REMOTE_URL)" : undefined}
          >
            <input
              type="checkbox"
              checked={pushDemo}
              onChange={(e) => setPushDemo(e.target.checked)}
              disabled={pushDisabled}
            />
            Push to demo repo
          </label>
          {pushDemo && !pushDisabled && (
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
      <ResultStrip
        pipelineStatus={runState.pipelineStatus}
        woPassCount={runState.woPassCount}
        woFailCount={runState.woFailCount}
        fileCount={runState.fileCount}
        error={runState.error}
        pushResult={runState.pushResult}
      />
    </div>
  );
}
