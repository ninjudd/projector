import type { ReactNode } from "react";

export type TermLine =
  | { kind: "cmd"; text: string }
  | { kind: "out"; text: string }
  | { kind: "label"; text: string }
  | { kind: "row"; name: string; status: string; title: string }
  | { kind: "blank" };

function renderLine(line: TermLine, index: number): ReactNode {
  switch (line.kind) {
    case "cmd":
      return (
        <span key={index} className="block">
          <span className="text-accent">$ </span>
          <span className="text-term-fg">{line.text}</span>
        </span>
      );
    case "label":
      return (
        <span key={index} className="block font-semibold text-term-fg">
          {line.text}
        </span>
      );
    case "row":
      return (
        <span key={index} className="block">
          {"  "}
          <span className="text-term-fg">{line.name.padEnd(28)}</span>{" "}
          <span className="text-term-muted">{line.status.padEnd(12)}</span>{" "}
          <span className="text-term-fg/80">{line.title}</span>
        </span>
      );
    case "out":
      return (
        <span key={index} className="block text-term-fg/90">
          {line.text}
        </span>
      );
    case "blank":
      return (
        <span key={index} className="block">
          {" "}
        </span>
      );
  }
}

export function Terminal({
  title,
  lines,
  className = "",
}: {
  title: string;
  lines: TermLine[];
  className?: string;
}) {
  return (
    <div
      className={`overflow-hidden rounded-xl border border-line-strong bg-term-bg text-term-fg shadow-2xl shadow-black/25 ${className}`}
    >
      <div className="flex items-center gap-2 border-b border-white/10 px-4 py-3">
        <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
        <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
        <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
        <span className="ml-3 font-mono text-xs text-term-muted">{title}</span>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-[12.5px] leading-6 sm:px-5">
        <code>
          {lines.map(renderLine)}
          <span className="block">
            <span className="text-accent">$ </span>
            <span className="cursor" aria-hidden="true" />
          </span>
        </code>
      </pre>
    </div>
  );
}
