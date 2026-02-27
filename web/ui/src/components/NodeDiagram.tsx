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

const STATUS_LABEL: Record<NodeStatus, string> = {
  idle: "Pending",
  running: "Running",
  pass: "Passed",
  fail: "Failed",
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
      <PipelineNode
        label="Planner"
        status={plannerStatus}
        tooltip={`Planner — ${STATUS_LABEL[plannerStatus]}`}
      />
      <Edge />

      {workOrders.length === 0 ? (
        <div className={styles.placeholder}>Work orders will appear here</div>
      ) : (
        <div className={styles.woGroup}>
          {workOrders.map((wo, idx) => {
            const parts = [`${wo.id}: ${wo.title || "(untitled)"}`];
            parts.push(`Status: ${STATUS_LABEL[wo.status]}`);
            if (wo.attempt) parts.push(`Attempt: ${wo.attempt}`);
            if (wo.factoryRunId) parts.push(`Factory: ${wo.factoryRunId}`);
            return (
              <React.Fragment key={wo.id}>
                <PipelineNode
                  label={wo.title || wo.id}
                  status={wo.status}
                  subtitle={wo.attempt ? `Attempt ${wo.attempt}` : undefined}
                  tooltip={parts.join("\n")}
                />
                {idx < workOrders.length - 1 && <Edge />}
              </React.Fragment>
            );
          })}
        </div>
      )}

      <Edge />
      <PipelineNode
        label="Complete"
        status={finalStatus}
        tooltip={`Pipeline — ${STATUS_LABEL[finalStatus]}`}
      />
    </div>
  );
}

function PipelineNode({
  label,
  status,
  subtitle,
  tooltip,
}: {
  label: string;
  status: NodeStatus;
  subtitle?: string;
  tooltip?: string;
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
      title={tooltip}
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
