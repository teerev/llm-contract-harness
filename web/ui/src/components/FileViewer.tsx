import styles from "./FileViewer.module.css";

interface FileData {
  path: string;
  content: string;
  size: number;
  line_count: number;
  truncated: boolean;
}

interface Props {
  data: FileData;
}

export default function FileViewer({ data }: Props) {
  const isJson = data.path.endsWith(".json");
  let displayContent = data.content;

  if (isJson) {
    try {
      displayContent = JSON.stringify(JSON.parse(data.content), null, 2);
    } catch {
      // not valid JSON, show raw
    }
  }

  const lines = displayContent.split("\n");
  const gutterWidth = String(lines.length).length;

  return (
    <div className={styles.viewer}>
      {data.truncated && (
        <div className={styles.truncationBanner}>
          File truncated to 1 MB ({formatSize(data.size)} total)
        </div>
      )}
      <table className={styles.codeTable}>
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className={styles.codeLine}>
              <td
                className={styles.lineNumber}
                style={{ minWidth: gutterWidth * 8 + 16 }}
              >
                {i + 1}
              </td>
              <td className={styles.lineContent}>
                {line || "\u00A0"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
