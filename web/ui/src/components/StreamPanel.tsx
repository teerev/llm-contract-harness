import React, { useEffect, useRef, useState, useCallback } from "react";
import styles from "./StreamPanel.module.css";

interface Props {
  text: string;
  isRunning: boolean;
}

export default function StreamPanel({ text, isRunning }: Props): React.JSX.Element {
  const containerRef = useRef<HTMLPreElement>(null);
  const userScrolledUpRef = useRef(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      userScrolledUpRef.current = distanceFromBottom > 80;
    };

    container.addEventListener("scroll", handleScroll);
    return () => container.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    if (userScrolledUpRef.current) return;

    requestAnimationFrame(() => {
      if (containerRef.current) {
        containerRef.current.scrollTop = containerRef.current.scrollHeight;
      }
    });
  }, [text]);

  // Re-engage auto-scroll when a new run starts
  useEffect(() => {
    if (isRunning) {
      userScrolledUpRef.current = false;
    }
  }, [isRunning]);

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

  const scrollToBottom = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
      userScrolledUpRef.current = false;
    }
  }, []);

  const placeholder = isRunning
    ? "Connected. Waiting for events..."
    : "Planner reasoning and factory output will appear here...";

  return (
    <>
      <div className={styles.toolbar}>
        {text && userScrolledUpRef.current && isRunning && (
          <button
            className={styles.followBtn}
            onClick={scrollToBottom}
            title="Scroll to latest output"
          >
            ↓ Follow
          </button>
        )}
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
