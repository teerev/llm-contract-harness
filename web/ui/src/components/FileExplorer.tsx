import React, { useCallback, useState } from "react";
import FileViewer from "./FileViewer";
import styles from "./FileExplorer.module.css";

const ROOTS = ["work_orders", "artifacts", "repo"] as const;
type Root = (typeof ROOTS)[number];

interface TreeEntry {
  path: string;
  type: "file" | "dir";
  size: number;
  line_count?: number | null;
}

interface SelectedFile {
  root: Root;
  path: string;
}

interface FileData {
  path: string;
  content: string;
  size: number;
  line_count: number;
  truncated: boolean;
}

interface Props {
  runId: string | null;
}

export default function FileExplorer({ runId }: Props) {
  const [expandedRoots, setExpandedRoots] = useState<Set<Root>>(new Set());
  const [trees, setTrees] = useState<Record<string, TreeEntry[]>>({});
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<SelectedFile | null>(null);
  const [fileData, setFileData] = useState<FileData | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());

  const toggleRoot = useCallback(
    async (root: Root) => {
      if (!runId) return;
      const next = new Set(expandedRoots);
      if (next.has(root)) {
        next.delete(root);
        setExpandedRoots(next);
        return;
      }
      next.add(root);
      setExpandedRoots(next);

      if (trees[root]) return;

      setLoading((prev) => new Set(prev).add(root));
      try {
        const res = await fetch(
          `/api/v1/runs/${runId}/tree?root=${root}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setTrees((prev) => ({ ...prev, [root]: data.entries }));
      } catch {
        setTrees((prev) => ({ ...prev, [root]: [] }));
      } finally {
        setLoading((prev) => {
          const s = new Set(prev);
          s.delete(root);
          return s;
        });
      }
    },
    [runId, expandedRoots, trees]
  );

  const toggleDir = useCallback((key: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const selectFile = useCallback(
    async (root: Root, path: string) => {
      if (!runId) return;
      setSelected({ root, path });
      setFileLoading(true);
      setFileData(null);
      try {
        const res = await fetch(
          `/api/v1/runs/${runId}/file?root=${root}&path=${encodeURIComponent(path)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: FileData = await res.json();
        setFileData(data);
      } catch {
        setFileData(null);
      } finally {
        setFileLoading(false);
      }
    },
    [runId]
  );

  function refreshRoot(root: Root) {
    if (!runId) return;
    setTrees((prev) => {
      const next = { ...prev };
      delete next[root];
      return next;
    });
    const next = new Set(expandedRoots);
    next.delete(root);
    setExpandedRoots(next);
    setTimeout(() => toggleRoot(root), 50);
  }

  return (
    <div className={styles.explorer}>
      <div className={styles.treePane}>
        <div className={styles.panelHeader}>
          <span>Files</span>
        </div>
        <div className={styles.treeScroll}>
          {!runId ? (
            <div className={styles.placeholder}>
              Run a pipeline to browse files.
            </div>
          ) : (
            ROOTS.map((root) => (
              <RootNode
                key={root}
                root={root}
                expanded={expandedRoots.has(root)}
                entries={trees[root] || []}
                isLoading={loading.has(root)}
                expandedDirs={expandedDirs}
                selectedPath={
                  selected?.root === root ? selected.path : null
                }
                onToggle={() => toggleRoot(root)}
                onToggleDir={toggleDir}
                onSelect={(path) => selectFile(root, path)}
                onRefresh={() => refreshRoot(root)}
              />
            ))
          )}
        </div>
      </div>
      <div className={styles.viewerPane}>
        <div className={styles.panelHeader}>
          <span>
            {selected
              ? `${selected.root}/${selected.path}`
              : "Viewer"}
          </span>
        </div>
        <div className={styles.viewerScroll}>
          {fileLoading ? (
            <div className={styles.placeholder}>Loading...</div>
          ) : fileData ? (
            <FileViewer data={fileData} />
          ) : (
            <div className={styles.placeholder}>
              Select a file to view its contents.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Root node with collapsible children ── */

interface RootNodeProps {
  root: Root;
  expanded: boolean;
  entries: TreeEntry[];
  isLoading: boolean;
  expandedDirs: Set<string>;
  selectedPath: string | null;
  onToggle: () => void;
  onToggleDir: (key: string) => void;
  onSelect: (path: string) => void;
  onRefresh: () => void;
}

function RootNode({
  root,
  expanded,
  entries,
  isLoading,
  expandedDirs,
  selectedPath,
  onToggle,
  onToggleDir,
  onSelect,
  onRefresh,
}: RootNodeProps) {
  const dirs = entries.filter((e) => e.type === "dir");
  const files = entries.filter((e) => e.type === "file");

  const topLevelDirs = dirs.filter(
    (d) => !d.path.slice(0, -1).includes("/")
  );
  const topLevelFiles = files.filter((f) => !f.path.includes("/"));

  return (
    <div>
      <div className={styles.rootRow} onClick={onToggle}>
        <span className={styles.chevron}>{expanded ? "▾" : "▸"}</span>
        <span className={styles.rootLabel}>{root}</span>
        {expanded && (
          <button
            className={styles.refreshBtn}
            onClick={(e) => {
              e.stopPropagation();
              onRefresh();
            }}
            title="Refresh"
          >
            ↻
          </button>
        )}
      </div>
      {expanded && isLoading && (
        <div className={styles.loadingRow}>Loading...</div>
      )}
      {expanded && !isLoading && entries.length === 0 && (
        <div className={styles.emptyRow}>(empty)</div>
      )}
      {expanded &&
        !isLoading &&
        renderLevel(
          topLevelDirs,
          topLevelFiles,
          dirs,
          files,
          "",
          1,
          root,
          expandedDirs,
          selectedPath,
          onToggleDir,
          onSelect
        )}
    </div>
  );
}

function renderLevel(
  levelDirs: TreeEntry[],
  levelFiles: TreeEntry[],
  allDirs: TreeEntry[],
  allFiles: TreeEntry[],
  _prefix: string,
  depth: number,
  root: string,
  expandedDirs: Set<string>,
  selectedPath: string | null,
  onToggleDir: (key: string) => void,
  onSelect: (path: string) => void
): React.JSX.Element {
  return (
    <>
      {levelDirs.map((dir) => {
        const dirPath = dir.path.endsWith("/")
          ? dir.path.slice(0, -1)
          : dir.path;
        const dirKey = `${root}:${dirPath}`;
        const isOpen = expandedDirs.has(dirKey);
        const dirName = dirPath.split("/").pop() || dirPath;

        const childDirs = allDirs.filter((d) => {
          const dp = d.path.endsWith("/") ? d.path.slice(0, -1) : d.path;
          const parent = dp.substring(0, dp.lastIndexOf("/"));
          return parent === dirPath;
        });
        const childFiles = allFiles.filter((f) => {
          const parent = f.path.substring(0, f.path.lastIndexOf("/"));
          return parent === dirPath;
        });

        return (
          <div key={dirKey}>
            <div
              className={styles.dirRow}
              style={{ paddingLeft: depth * 16 + 8 }}
              onClick={() => onToggleDir(dirKey)}
            >
              <span className={styles.chevron}>
                {isOpen ? "▾" : "▸"}
              </span>
              <span className={styles.dirName}>{dirName}/</span>
            </div>
            {isOpen &&
              renderLevel(
                childDirs,
                childFiles,
                allDirs,
                allFiles,
                dirPath + "/",
                depth + 1,
                root,
                expandedDirs,
                selectedPath,
                onToggleDir,
                onSelect
              )}
          </div>
        );
      })}
      {levelFiles.map((file) => {
        const fileName = file.path.split("/").pop() || file.path;
        const isSelected = file.path === selectedPath;
        return (
          <div
            key={`${root}:${file.path}`}
            className={`${styles.fileRow} ${isSelected ? styles.fileSelected : ""}`}
            style={{ paddingLeft: depth * 16 + 8 }}
            onClick={() => onSelect(file.path)}
          >
            <span className={styles.fileName}>{fileName}</span>
            <span className={styles.fileMeta}>
              {file.line_count != null && `${file.line_count}L`}
              {file.line_count != null && " · "}
              {formatSize(file.size)}
            </span>
          </div>
        );
      })}
    </>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
