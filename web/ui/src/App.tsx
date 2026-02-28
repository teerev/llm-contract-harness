import { useState, useCallback, useEffect } from "react";
import FileExplorer from "./components/FileExplorer";
import NodeDiagram from "./components/NodeDiagram";
import StreamPanel from "./components/StreamPanel";
import ResultStrip from "./components/ResultStrip";
import { useRunEvents } from "./hooks/useRunEvents";
import styles from "./App.module.css";

type UIStatus = "idle" | "running" | "complete" | "failed";

interface Quota {
  ip_remaining: number;
  ip_limit: number;
  global_remaining: number;
  global_limit: number;
}

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [pushDemo, setPushDemo] = useState(true);
  const [uiStatus, setUiStatus] = useState<UIStatus>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [demoRemoteConfigured, setDemoRemoteConfigured] = useState<boolean | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);

  const runState = useRunEvents(runId);

  const fetchQuota = useCallback(() => {
    fetch("/api/v1/quota")
      .then((r) => r.json())
      .then((d) => setQuota(d))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/v1/config")
      .then((res) => res.json())
      .then((data) => {
        const configured = data.demo_remote_configured ?? false;
        setDemoRemoteConfigured(configured);
        setPushDemo(configured);
      })
      .catch(() => setDemoRemoteConfigured(false));
    fetchQuota();
  }, [fetchQuota]);

  const derivedStatus: UIStatus =
    runState.pipelineStatus === "complete"
      ? "complete"
      : runState.pipelineStatus === "failed"
        ? "failed"
        : runState.pipelineStatus === "idle"
          ? uiStatus
          : "running";

  const isRunning = derivedStatus === "running";

  const quotaExhausted =
    quota !== null && (quota.ip_remaining <= 0 || quota.global_remaining <= 0);

  const canSubmit = !isRunning && prompt.trim().length > 0 && !quotaExhausted;

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
        }),
      });

      const data = await res.json();

      if (data.quota) {
        setQuota(data.quota);
      }

      if (!res.ok) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }

      setRunId(data.run_id);
    } catch (err) {
      setUiStatus("failed");
      setSubmitError(err instanceof Error ? err.message : "Failed to start run");
      fetchQuota();
    }
  }, [canSubmit, prompt, pushDemo, runState, fetchQuota]);

  // Refresh quota when pipeline finishes
  useEffect(() => {
    if (runState.pipelineStatus === "complete" || runState.pipelineStatus === "failed") {
      fetchQuota();
    }
  }, [runState.pipelineStatus, fetchQuota]);

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
      {/* ── Error banner ── */}
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
          {quota && (
            <span className={`${styles.quotaInfo} ${quotaExhausted ? styles.quotaExhausted : ""}`}>
              Your runs: {quota.ip_remaining}/{quota.ip_limit} remaining today
              {" · "}
              Global: {quota.global_remaining}/{quota.global_limit} remaining today
            </span>
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
        <NodeDiagram
          plannerStatus={runState.plannerStatus}
          workOrders={runState.workOrders}
          pipelineComplete={runState.pipelineStatus === "complete"}
          pipelineFailed={runState.pipelineStatus === "failed"}
        />
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
