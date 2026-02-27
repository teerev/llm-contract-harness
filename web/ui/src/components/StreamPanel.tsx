import React, { useEffect, useRef, useState, useCallback } from "react";
import styles from "./StreamPanel.module.css";

interface Props {
  text: string;
  isRunning: boolean;
}

export default function StreamPanel({ text, isRunning }: Props): React.JSX.Element {
  const containerRef = useRef<HTMLPreElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
      shouldAutoScrollRef.current = isAtBottom;
    };

    container.addEventListener("scroll", handleScroll);
    return () => container.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (shouldAutoScrollRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [text]);

  const handleCopy = useCallback(async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard may be blocked in some contexts
    }
  }, [text]);

  const placeholder = isRunning
    ? "Connected. Waiting for events..."
    : "Planner reasoning and factory output will appear here...";

  return (
    <>
      <div className={styles.toolbar}>
        {text && (
          <button
            className={styles.copyBtn}
            onClick={handleCopy}
            title="Copy stream output"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        )}
      </div>
      <pre ref={containerRef} className={styles.container}>
        {text || <span className={styles.placeholderText}>{placeholder}</span>}
        {isRunning && text && <span className={styles.cursor}>&#9611;</span>}
      </pre>
    </>
  );
}
