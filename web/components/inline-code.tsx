import type { ReactNode } from "react";

export function Code({ children }: { children: ReactNode }) {
  return (
    <code className="inline-block max-w-full break-words rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.86em] text-fg">
      {children}
    </code>
  );
}
