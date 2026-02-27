import { useEffect, useRef, useCallback, useState } from "react";

export type PipelineStatus = "idle" | "planning" | "building" | "pushing" | "complete" | "failed";
export type NodeStatus = "idle" | "running" | "pass" | "fail";

export interface WorkOrderNode {
  id: string;
  title: string;
  status: NodeStatus;
  attempt?: number;
  factoryRunId?: string;
}

export interface FileWrittenEvent {
  woId: string;
  path: string;
  lineCount: number;
}

export interface RunState {
  pipelineStatus: PipelineStatus;
  plannerStatus: NodeStatus;
  workOrders: WorkOrderNode[];
  streamText: string;
  filesWritten: FileWrittenEvent[];
  artifactsWritten: string[];
  error?: string;
  woPassCount: number;
  woFailCount: number;
  fileCount: number;
}

const INITIAL_STATE: RunState = {
  pipelineStatus: "idle",
  plannerStatus: "idle",
  workOrders: [],
  streamText: "",
  filesWritten: [],
  artifactsWritten: [],
  woPassCount: 0,
  woFailCount: 0,
  fileCount: 0,
};

type EventHandler = (event: Record<string, unknown>) => void;

export function useRunEvents(runId: string | null): RunState & { reset: () => void } {
  const [state, setState] = useState<RunState>(INITIAL_STATE);
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastSeqRef = useRef<number>(0);

  const reset = useCallback(() => {
    setState(INITIAL_STATE);
    lastSeqRef.current = 0;
  }, []);

  useEffect(() => {
    if (!runId) {
      return;
    }

    reset();
    const url = `/api/v1/runs/${runId}/events?last_seq=${lastSeqRef.current}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    const handleEvent: EventHandler = (event) => {
      const seq = typeof event.seq === "number" ? event.seq : 0;
      if (seq > lastSeqRef.current) {
        lastSeqRef.current = seq;
      }

      const eventType = event.type as string;

      setState((prev) => {
        switch (eventType) {
          case "pipeline_status": {
            const status = event.status as PipelineStatus;
            const newState: Partial<RunState> = { pipelineStatus: status };
            if (event.error) {
              newState.error = String(event.error);
            }
            return { ...prev, ...newState };
          }

          case "planner_status": {
            const status = event.status as string;
            let plannerStatus: NodeStatus = prev.plannerStatus;
            if (status === "attempt_start") {
              plannerStatus = "running";
            } else if (status === "attempt_pass" || status === "done") {
              plannerStatus = "pass";
            } else if (status === "attempt_fail") {
              plannerStatus = prev.plannerStatus;
            }
            return { ...prev, plannerStatus };
          }

          case "planner_chunk": {
            const text = event.text as string;
            return { ...prev, streamText: prev.streamText + text };
          }

          case "planner_reasoning_status": {
            return prev;
          }

          case "work_orders_created": {
            const woList = event.work_orders as Array<{ id: string; title: string }>;
            const workOrders: WorkOrderNode[] = woList.map((wo) => ({
              id: wo.id,
              title: wo.title,
              status: "idle",
            }));
            return { ...prev, workOrders };
          }

          case "wo_status": {
            const woId = event.wo_id as string;
            const status = event.status as string;
            const workOrders = prev.workOrders.map((wo) => {
              if (wo.id !== woId) return wo;
              let nodeStatus: NodeStatus = wo.status;
              let attempt = wo.attempt;
              let factoryRunId = wo.factoryRunId;

              if (status === "queued") {
                nodeStatus = "idle";
              } else if (status === "running") {
                nodeStatus = "running";
                if (event.factory_run_id) {
                  factoryRunId = event.factory_run_id as string;
                }
              } else if (status.startsWith("attempt_")) {
                nodeStatus = "running";
                attempt = parseInt(status.replace("attempt_", ""), 10);
              } else if (status === "pass") {
                nodeStatus = "pass";
              } else if (status === "fail") {
                nodeStatus = "fail";
              }
              return { ...wo, status: nodeStatus, attempt, factoryRunId };
            });

            let woPassCount = prev.woPassCount;
            let woFailCount = prev.woFailCount;
            if (status === "pass") woPassCount++;
            if (status === "fail") woFailCount++;

            return { ...prev, workOrders, woPassCount, woFailCount };
          }

          case "file_written": {
            const woId = event.wo_id as string;
            const files = event.files as Array<{ path: string; line_count: number }>;
            const newFiles: FileWrittenEvent[] = files.map((f) => ({
              woId,
              path: f.path,
              lineCount: f.line_count,
            }));
            return {
              ...prev,
              filesWritten: [...prev.filesWritten, ...newFiles],
              fileCount: prev.fileCount + newFiles.length,
            };
          }

          case "artifact_written": {
            const path = event.path as string;
            return {
              ...prev,
              artifactsWritten: [...prev.artifactsWritten, path],
            };
          }

          case "console": {
            const text = event.text as string;
            return { ...prev, streamText: prev.streamText + text + "\n" };
          }

          case "git_push_started":
          case "git_push_done":
            return prev;

          default:
            return prev;
        }
      });
    };

    es.addEventListener("pipeline_status", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("planner_status", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("planner_chunk", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("planner_reasoning_status", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("work_orders_created", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("wo_status", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("file_written", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("artifact_written", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("console", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("git_push_started", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("git_push_done", (e) => handleEvent(JSON.parse(e.data)));
    es.addEventListener("ping", () => {});
    es.addEventListener("done", (e) => {
      handleEvent(JSON.parse(e.data));
      es.close();
    });
    es.addEventListener("error", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data);
        handleEvent(data);
      } catch {
        setState((prev) => ({ ...prev, error: "SSE connection error" }));
      }
      es.close();
    });

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [runId, reset]);

  return { ...state, reset };
}
