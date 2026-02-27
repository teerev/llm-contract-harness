import React, { useCallback, useState, useRef, useEffect } from "react";
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

interface FlatItem {
  key: string;
  kind: "root" | "dir" | "file";
  root: Root;
  path?: string;
  depth: number;
}

export default function FileExplorer({ runId }: Props) {
  const [expandedRoots, setExpandedRoots] = useState<Set<Root>>(new Set());
  const [trees, setTrees] = useState<Record<string, TreeEntry[]>>({});
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<SelectedFile | null>(null);
  const [fileData, setFileData] = useState<FileData | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [treeErrors, setTreeErrors] = useState<Record<string, string>>({});
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const treeRef = useRef<HTMLDivElement>(null);

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
      setTreeErrors((prev) => { const n = { ...prev }; delete n[root]; return n; });
      try {
        const res = await fetch(`/api/v1/runs/${runId}/tree?root=${root}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setTrees((prev) => ({ ...prev, [root]: data.entries }));
      } catch (err) {
        setTrees((prev) => ({ ...prev, [root]: [] }));
        setTreeErrors((prev) => ({ ...prev, [root]: err instanceof Error ? err.message : "Load failed" }));
      } finally {
        setLoading((prev) => {
          const s = new Set(prev);
          s.delete(root);
          return s;
        });
      }
    },
    [runId, expandedRoots, trees],
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
      setFileError(null);
      try {
        const res = await fetch(
          `/api/v1/runs/${runId}/file?root=${root}&path=${encodeURIComponent(path)}`,
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: FileData = await res.json();
        setFileData(data);
      } catch (err) {
        setFileData(null);
        setFileError(err instanceof Error ? err.message : "Failed to load file");
      } finally {
        setFileLoading(false);
      }
    },
    [runId],
  );

  function refreshRoot(root: Root) {
    if (!runId) return;
    setTrees((prev) => {
      const next = { ...prev };
      delete next[root];
      return next;
    });
    setTreeErrors((prev) => { const n = { ...prev }; delete n[root]; return n; });
    const next = new Set(expandedRoots);
    next.delete(root);
    setExpandedRoots(next);
    setTimeout(() => toggleRoot(root), 50);
  }

  // Build flat item list for keyboard navigation
  const flatItems = buildFlatList(expandedRoots, expandedDirs, trees, loading);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!flatItems.length) return;
      const idx = focusKey ? flatItems.findIndex((i) => i.key === focusKey) : -1;

      switch (e.key) {
        case "ArrowDown": {
          e.preventDefault();
          const next = Math.min(idx + 1, flatItems.length - 1);
          setFocusKey(flatItems[next].key);
          break;
        }
        case "ArrowUp": {
          e.preventDefault();
          const prev = Math.max(idx - 1, 0);
          setFocusKey(flatItems[prev].key);
          break;
        }
        case "ArrowRight": {
          e.preventDefault();
          if (idx < 0) break;
          const item = flatItems[idx];
          if (item.kind === "root") {
            if (!expandedRoots.has(item.root)) toggleRoot(item.root);
          } else if (item.kind === "dir") {
            if (!expandedDirs.has(item.key)) toggleDir(item.key);
          }
          break;
        }
        case "ArrowLeft": {
          e.preventDefault();
          if (idx < 0) break;
          const item = flatItems[idx];
          if (item.kind === "root") {
            if (expandedRoots.has(item.root)) toggleRoot(item.root);
          } else if (item.kind === "dir") {
            if (expandedDirs.has(item.key)) toggleDir(item.key);
          }
          break;
        }
        case "Enter": {
          e.preventDefault();
          if (idx < 0) break;
          const item = flatItems[idx];
          if (item.kind === "root") toggleRoot(item.root);
          else if (item.kind === "dir") toggleDir(item.key);
          else if (item.kind === "file" && item.path) selectFile(item.root, item.path);
          break;
        }
      }
    },
    [flatItems, focusKey, expandedRoots, expandedDirs, toggleRoot, toggleDir, selectFile],
  );

  // Scroll focused item into view
  useEffect(() => {
    if (!focusKey || !treeRef.current) return;
    const el = treeRef.current.querySelector(`[data-key="${CSS.escape(focusKey)}"]`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [focusKey]);

  return (
    <div className={styles.explorer}>
      <div className={styles.treePane}>
        <div className={styles.panelHeader}>
          <span>Files</span>
        </div>
        <div
          ref={treeRef}
          className={styles.treeScroll}
          tabIndex={0}
          onKeyDown={handleKeyDown}
          role="tree"
        >
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
                error={treeErrors[root]}
                expandedDirs={expandedDirs}
                selectedPath={selected?.root === root ? selected.path : null}
                focusKey={focusKey}
                onToggle={() => toggleRoot(root)}
                onToggleDir={toggleDir}
                onSelect={(path) => selectFile(root, path)}
                onRefresh={() => refreshRoot(root)}
                onFocus={setFocusKey}
              />
            ))
          )}
        </div>
      </div>
      <div className={styles.viewerPane}>
        <div className={styles.panelHeader}>
          <span>
            {selected ? `${selected.root}/${selected.path}` : "Viewer"}
          </span>
        </div>
        <div className={styles.viewerScroll}>
          {fileLoading ? (
            <div className={styles.placeholder}>
              <span className={styles.spinnerSmall} /> Loading file...
            </div>
          ) : fileError ? (
            <div className={styles.errorState}>
              <span className={styles.errorIcon}>!</span>
              <span>{fileError}</span>
              {selected && (
                <button
                  className={styles.retryBtn}
                  onClick={() => selectFile(selected.root, selected.path)}
                >
                  Retry
                </button>
              )}
            </div>
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

/* ── Flat list builder for keyboard nav ── */

function buildFlatList(
  expandedRoots: Set<Root>,
  expandedDirs: Set<string>,
  trees: Record<string, TreeEntry[]>,
  loading: Set<string>,
): FlatItem[] {
  const items: FlatItem[] = [];
  for (const root of ROOTS) {
    items.push({ key: `root:${root}`, kind: "root", root, depth: 0 });
    if (!expandedRoots.has(root) || loading.has(root)) continue;
    const entries = trees[root] || [];
    const dirs = entries.filter((e) => e.type === "dir");
    const files = entries.filter((e) => e.type === "file");
    const topDirs = dirs.filter((d) => !d.path.slice(0, -1).includes("/"));
    const topFiles = files.filter((f) => !f.path.includes("/"));
    buildFlatLevel(items, topDirs, topFiles, dirs, files, root, 1, expandedDirs);
  }
  return items;
}

function buildFlatLevel(
  items: FlatItem[],
  levelDirs: TreeEntry[],
  levelFiles: TreeEntry[],
  allDirs: TreeEntry[],
  allFiles: TreeEntry[],
  root: Root,
  depth: number,
  expandedDirs: Set<string>,
) {
  for (const dir of levelDirs) {
    const dirPath = dir.path.endsWith("/") ? dir.path.slice(0, -1) : dir.path;
    const dirKey = `${root}:${dirPath}`;
    items.push({ key: dirKey, kind: "dir", root, path: dirPath, depth });
    if (expandedDirs.has(dirKey)) {
      const childDirs = allDirs.filter((d) => {
        const dp = d.path.endsWith("/") ? d.path.slice(0, -1) : d.path;
        const parent = dp.substring(0, dp.lastIndexOf("/"));
        return parent === dirPath;
      });
      const childFiles = allFiles.filter((f) => {
        const parent = f.path.substring(0, f.path.lastIndexOf("/"));
        return parent === dirPath;
      });
      buildFlatLevel(items, childDirs, childFiles, allDirs, allFiles, root, depth + 1, expandedDirs);
    }
  }
  for (const file of levelFiles) {
    items.push({ key: `${root}:${file.path}`, kind: "file", root, path: file.path, depth });
  }
}

/* ── Root node with collapsible children ── */

interface RootNodeProps {
  root: Root;
  expanded: boolean;
  entries: TreeEntry[];
  isLoading: boolean;
  error?: string;
  expandedDirs: Set<string>;
  selectedPath: string | null;
  focusKey: string | null;
  onToggle: () => void;
  onToggleDir: (key: string) => void;
  onSelect: (path: string) => void;
  onRefresh: () => void;
  onFocus: (key: string) => void;
}

function RootNode({
  root,
  expanded,
  entries,
  isLoading,
  error,
  expandedDirs,
  selectedPath,
  focusKey,
  onToggle,
  onToggleDir,
  onSelect,
  onRefresh,
  onFocus,
}: RootNodeProps) {
  const dirs = entries.filter((e) => e.type === "dir");
  const files = entries.filter((e) => e.type === "file");
  const topLevelDirs = dirs.filter((d) => !d.path.slice(0, -1).includes("/"));
  const topLevelFiles = files.filter((f) => !f.path.includes("/"));
  const rootKey = `root:${root}`;

  return (
    <div>
      <div
        className={`${styles.rootRow} ${focusKey === rootKey ? styles.focused : ""}`}
        data-key={rootKey}
        onClick={() => { onFocus(rootKey); onToggle(); }}
      >
        <span className={styles.chevron}>{expanded ? "\u25BE" : "\u25B8"}</span>
        <span className={styles.rootLabel}>{root}</span>
        {expanded && (
          <button
            className={styles.refreshBtn}
            onClick={(e) => { e.stopPropagation(); onRefresh(); }}
            title="Refresh"
          >
            &#8635;
          </button>
        )}
      </div>
      {expanded && isLoading && (
        <div className={styles.loadingRow}>
          <span className={styles.spinnerSmall} /> Loading...
        </div>
      )}
      {expanded && !isLoading && error && (
        <div className={styles.errorRow}>
          <span>{error}</span>
          <button className={styles.retryBtnSmall} onClick={onRefresh}>Retry</button>
        </div>
      )}
      {expanded && !isLoading && !error && entries.length === 0 && (
        <div className={styles.emptyRow}>(empty)</div>
      )}
      {expanded &&
        !isLoading &&
        !error &&
        renderLevel(
          topLevelDirs,
          topLevelFiles,
          dirs,
          files,
          1,
          root,
          expandedDirs,
          selectedPath,
          focusKey,
          onToggleDir,
          onSelect,
          onFocus,
        )}
    </div>
  );
}

function renderLevel(
  levelDirs: TreeEntry[],
  levelFiles: TreeEntry[],
  allDirs: TreeEntry[],
  allFiles: TreeEntry[],
  depth: number,
  root: string,
  expandedDirs: Set<string>,
  selectedPath: string | null,
  focusKey: string | null,
  onToggleDir: (key: string) => void,
  onSelect: (path: string) => void,
  onFocus: (key: string) => void,
): React.JSX.Element {
  return (
    <>
      {levelDirs.map((dir) => {
        const dirPath = dir.path.endsWith("/") ? dir.path.slice(0, -1) : dir.path;
        const dirKey = `${root}:${dirPath}`;
        const isOpen = expandedDirs.has(dirKey);
        const dirName = dirPath.split("/").pop() || dirPath;
        const isFocused = focusKey === dirKey;

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
              className={`${styles.dirRow} ${isFocused ? styles.focused : ""}`}
              style={{ paddingLeft: depth * 16 + 8 }}
              data-key={dirKey}
              onClick={() => { onFocus(dirKey); onToggleDir(dirKey); }}
            >
              <span className={styles.chevron}>{isOpen ? "\u25BE" : "\u25B8"}</span>
              <span className={styles.dirName}>{dirName}/</span>
            </div>
            {isOpen &&
              renderLevel(
                childDirs,
                childFiles,
                allDirs,
                allFiles,
                depth + 1,
                root,
                expandedDirs,
                selectedPath,
                focusKey,
                onToggleDir,
                onSelect,
                onFocus,
              )}
          </div>
        );
      })}
      {levelFiles.map((file) => {
        const fileName = file.path.split("/").pop() || file.path;
        const isSelected = file.path === selectedPath;
        const fileKey = `${root}:${file.path}`;
        const isFocused = focusKey === fileKey;
        return (
          <div
            key={fileKey}
            className={`${styles.fileRow} ${isSelected ? styles.fileSelected : ""} ${isFocused ? styles.focused : ""}`}
            style={{ paddingLeft: depth * 16 + 8 }}
            data-key={fileKey}
            onClick={() => { onFocus(fileKey); onSelect(file.path); }}
          >
            <span className={styles.fileName}>{fileName}</span>
            <span className={styles.fileMeta}>
              {file.line_count != null && `${file.line_count}L`}
              {file.line_count != null && " \u00B7 "}
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
