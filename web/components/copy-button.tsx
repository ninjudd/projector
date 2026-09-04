"use client";

import { useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon } from "./icons";

export function CopyButton({ text, className = "" }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be denied; the text is still selectable.
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? "Copied" : "Copy to clipboard"}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-faint transition-colors hover:bg-surface-2 hover:text-fg ${className}`}
    >
      {copied ? (
        <CheckIcon className="h-4 w-4 text-accent" />
      ) : (
        <CopyIcon className="h-4 w-4" />
      )}
    </button>
  );
}
