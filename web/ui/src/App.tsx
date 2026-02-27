import { useState } from "react";
import styles from "./App.module.css";

type Status = "idle" | "running" | "complete" | "failed";

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [pushDemo, setPushDemo] = useState(false);
  const [branchName, setBranchName] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [_runId, setRunId] = useState<string | null>(null);

  const canSubmit =
    status !== "running" && prompt.trim().length > 0 && (!pushDemo || branchName.trim().length > 0);

  async function handleRun() {
    if (!canSubmit) return;
    setStatus("running");
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
      setStatus("failed");
    }
  }

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
            {status === "running" ? "Running..." : "Run"}
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
        <pre className={styles.streamContent}>
          {status === "idle"
            ? "Planner reasoning and factory output will appear here..."
            : "Connected. Waiting for events..."}
        </pre>
      </section>

      {/* ── Node Diagram ── */}
      <section className={styles.diagramPanel}>
        <div className={styles.panelLabel}>Pipeline</div>
        <div className={styles.diagramContent}>
          <div className={styles.node} data-status="idle">Planner</div>
          <div className={styles.edge} />
          <div className={styles.nodePlaceholder}>Work orders will appear here</div>
          <div className={styles.edge} />
          <div className={styles.node} data-status="idle">Complete</div>
        </div>
      </section>

      {/* ── File Explorer ── */}
      <section className={styles.explorerPanel}>
        <div className={styles.treePane}>
          <div className={styles.panelLabel}>Files</div>
          <ul className={styles.treeList}>
            <li className={styles.treeRoot}>work_orders</li>
            <li className={styles.treeRoot}>artifacts</li>
            <li className={styles.treeRoot}>repo</li>
          </ul>
        </div>
        <div className={styles.viewerPane}>
          <div className={styles.panelLabel}>Viewer</div>
          <pre className={styles.viewerContent}>
            Select a file to view its contents.
          </pre>
        </div>
      </section>

      {/* ── Result Strip ── */}
      <footer className={styles.resultStrip}>
        <span className={styles.statusBadge} data-status={status}>
          {status === "idle" && "Ready"}
          {status === "running" && "Running..."}
          {status === "complete" && "Complete"}
          {status === "failed" && "Failed"}
        </span>
      </footer>
    </div>
  );
}
