import React, { useRef, useEffect, useState, useCallback } from "react";
import type { NodeStatus, WorkOrderNode } from "../hooks/useRunEvents";
import styles from "./NodeDiagram.module.css";

interface Props {
  plannerStatus: NodeStatus;
  workOrders: WorkOrderNode[];
  pipelineComplete: boolean;
  pipelineFailed: boolean;
}

interface Edge {
  id: string;
  d: string;
  color: string;
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

function bezierPath(x1: number, y1: number, x2: number, y2: number): string {
  const dx = Math.abs(x2 - x1) * 0.4;
  return `M ${x1},${y1} C ${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
}

function edgeColor(status: NodeStatus): string {
  switch (status) {
    case "running":
      return "var(--accent)";
    case "pass":
      return "var(--green)";
    case "fail":
      return "var(--red)";
    default:
      return "var(--border)";
  }
}

export default function NodeDiagram({
  plannerStatus,
  workOrders,
  pipelineComplete,
  pipelineFailed,
}: Props): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const plannerRef = useRef<HTMLDivElement>(null);
  const repoRef = useRef<HTMLDivElement>(null);
  const woRefs = useRef(new Map<string, HTMLDivElement>());
  const [edges, setEdges] = useState<Edge[]>([]);
  const [svgSize, setSvgSize] = useState({ w: 0, h: 0 });

  const repoStatus: NodeStatus = pipelineFailed
    ? "fail"
    : pipelineComplete
      ? "pass"
      : "idle";

  const computeEdges = useCallback(() => {
    const cEl = containerRef.current;
    const pEl = plannerRef.current;
    const rEl = repoRef.current;
    if (!cEl || !pEl || !rEl) return;

    const cR = cEl.getBoundingClientRect();
    setSvgSize({ w: cR.width, h: cR.height });

    if (workOrders.length === 0) {
      setEdges([]);
      return;
    }

    const pR = pEl.getBoundingClientRect();
    const rR = rEl.getBoundingClientRect();

    const pRightX = pR.right - cR.left;
    const pCY = pR.top + pR.height / 2 - cR.top;
    const rLeftX = rR.left - cR.left;
    const rCY = rR.top + rR.height / 2 - cR.top;

    const next: Edge[] = [];

    for (const wo of workOrders) {
      const el = woRefs.current.get(wo.id);
      if (!el) continue;
      const wR = el.getBoundingClientRect();
      const wLeftX = wR.left - cR.left;
      const wRightX = wR.right - cR.left;
      const wCY = wR.top + wR.height / 2 - cR.top;

      next.push({
        id: `p-${wo.id}`,
        d: bezierPath(pRightX, pCY, wLeftX, wCY),
        color: "var(--green)",
      });

      next.push({
        id: `${wo.id}-r`,
        d: bezierPath(wRightX, wCY, rLeftX, rCY),
        color: edgeColor(wo.status),
      });
    }

    setEdges(next);
  }, [workOrders]);

  const computeRef = useRef(computeEdges);
  computeRef.current = computeEdges;

  useEffect(() => {
    requestAnimationFrame(() => computeRef.current());
  }, [workOrders, plannerStatus, pipelineComplete, pipelineFailed]);

  useEffect(() => {
    const cEl = containerRef.current;
    if (!cEl) return;
    const ro = new ResizeObserver(() => computeRef.current());
    ro.observe(cEl);
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={containerRef} className={styles.container}>
      {svgSize.w > 0 && svgSize.h > 0 && (
        <svg
          className={styles.edgeLayer}
          viewBox={`0 0 ${svgSize.w} ${svgSize.h}`}
        >
          {edges.map((e) => (
            <path
              key={e.id}
              d={e.d}
              stroke={e.color}
              className={styles.edgePath}
            />
          ))}
        </svg>
      )}

      <div className={styles.leftCol}>
        <DiagramNode
          ref={plannerRef}
          label="Planner"
          status={plannerStatus}
          tooltip={`Planner — ${STATUS_LABEL[plannerStatus]}`}
        />
      </div>

      <div className={styles.middleCol}>
        {workOrders.length === 0 ? (
          <div className={styles.placeholder}>
            Work orders will appear here
          </div>
        ) : (
          workOrders.map((wo) => {
            const tip = [
              `${wo.id}: ${wo.title || "(untitled)"}`,
              `Status: ${STATUS_LABEL[wo.status]}`,
              wo.attempt ? `Attempt: ${wo.attempt}` : "",
              wo.factoryRunId ? `Factory: ${wo.factoryRunId}` : "",
            ]
              .filter(Boolean)
              .join("\n");

            return (
              <DiagramNode
                key={wo.id}
                ref={(el: HTMLDivElement | null) => {
                  if (el) woRefs.current.set(wo.id, el);
                  else woRefs.current.delete(wo.id);
                }}
                label={wo.title || wo.id}
                status={wo.status}
                subtitle={wo.title ? wo.id : undefined}
                tooltip={tip}
              />
            );
          })
        )}
      </div>

      <div className={styles.rightCol}>
        <DiagramNode
          ref={repoRef}
          label="Repo"
          status={repoStatus}
          tooltip={`Repo — ${STATUS_LABEL[repoStatus]}`}
        />
      </div>
    </div>
  );
}

const DiagramNode = React.forwardRef<
  HTMLDivElement,
  { label: string; status: NodeStatus; subtitle?: string; tooltip?: string }
>(function DiagramNode({ label, status, subtitle, tooltip }, ref) {
  const borderColor = STATUS_COLORS[status];
  const bgColor = STATUS_BG[status];
  const textColor = status === "idle" ? "var(--text-dim)" : borderColor;

  return (
    <div
      ref={ref}
      className={styles.node}
      style={{ borderColor, backgroundColor: bgColor, color: textColor }}
      title={tooltip}
    >
      <span className={styles.nodeLabel}>{label}</span>
      {subtitle && <span className={styles.nodeSubtitle}>{subtitle}</span>}
      {status === "running" && <span className={styles.pulse} />}
    </div>
  );
});
