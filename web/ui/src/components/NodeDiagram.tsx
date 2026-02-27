import React from "react";
import type { NodeStatus, WorkOrderNode } from "../hooks/useRunEvents";
import styles from "./NodeDiagram.module.css";

interface Props {
  plannerStatus: NodeStatus;
  workOrders: WorkOrderNode[];
  pipelineComplete: boolean;
  pipelineFailed: boolean;
}

const STATUS_COLORS: Record<NodeStatus, string> = {
  idle: "var(--text-dim)",
  running: "var(--accent)",
  pass: "var(--green)",
  fail: "var(--red)",
};

const STATUS_BG: Record<NodeStatus, string> = {
  idle: "var(--bg-input)",
  running: "var(--bg-input)",
  pass: "rgba(34, 197, 94, 0.1)",
  fail: "rgba(239, 68, 68, 0.1)",
};

export default function NodeDiagram({
  plannerStatus,
  workOrders,
  pipelineComplete,
  pipelineFailed,
}: Props): React.JSX.Element {
  const finalStatus: NodeStatus = pipelineFailed
    ? "fail"
    : pipelineComplete
      ? "pass"
      : "idle";

  return (
    <div className={styles.container}>
      <Node label="Planner" status={plannerStatus} />
      <Edge />

      {workOrders.length === 0 ? (
        <div className={styles.placeholder}>Work orders will appear here</div>
      ) : (
        <div className={styles.woGroup}>
          {workOrders.map((wo, idx) => (
            <React.Fragment key={wo.id}>
              <Node
                label={wo.title || wo.id}
                status={wo.status}
                subtitle={wo.attempt ? `Attempt ${wo.attempt}` : undefined}
              />
              {idx < workOrders.length - 1 && <Edge />}
            </React.Fragment>
          ))}
        </div>
      )}

      <Edge />
      <Node label="Complete" status={finalStatus} />
    </div>
  );
}

function Node({
  label,
  status,
  subtitle,
}: {
  label: string;
  status: NodeStatus;
  subtitle?: string;
}): React.JSX.Element {
  const borderColor = STATUS_COLORS[status];
  const bgColor = STATUS_BG[status];
  const textColor = status === "idle" ? "var(--text-dim)" : borderColor;

  return (
    <div
      className={styles.node}
      style={{
        borderColor,
        backgroundColor: bgColor,
        color: textColor,
      }}
    >
      <span className={styles.nodeLabel}>{label}</span>
      {subtitle && <span className={styles.nodeSubtitle}>{subtitle}</span>}
      {status === "running" && <span className={styles.pulse} />}
    </div>
  );
}

function Edge(): React.JSX.Element {
  return <div className={styles.edge} />;
}
