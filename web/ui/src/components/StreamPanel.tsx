import React, { useEffect, useRef } from "react";
import styles from "./StreamPanel.module.css";

interface Props {
  text: string;
  isRunning: boolean;
}

export default function StreamPanel({ text, isRunning }: Props): React.JSX.Element {
  const containerRef = useRef<HTMLPreElement>(null);
  const shouldAutoScrollRef = useRef(true);

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

  const placeholder = isRunning
    ? "Connected. Waiting for events..."
    : "Planner reasoning and factory output will appear here...";

  return (
    <pre ref={containerRef} className={styles.container}>
      {text || placeholder}
      {isRunning && text && <span className={styles.cursor}>▋</span>}
    </pre>
  );
}
