import type { ReactNode } from "react";

// The colors Projector's panel draws in Claude Code's dark theme: red when the
// branch cannot merge, yellow when a review wants something, orange while a
// review runs, green once a review signs the head off, and light blue while a
// pull request waits for its first review. Questions take the theme's
// permission color and requests a pastel peach.
const tones = {
  fg: "text-term-fg",
  muted: "text-term-muted",
  dim: "text-term-muted/70",
  conflict: "text-[#f2777a]",
  changes: "text-[#e5c07b]",
  reviewing: "text-[#f99157]",
  clean: "text-[#99c794]",
  ready: "text-[#8fc1e3]",
  question: "text-[#b1b9f9]",
  request: "text-[rgb(255,190,140)]",
  notice: "text-term-muted",
} as const;

export type Tone = keyof typeof tones;

export type Segment = { text: string; tone?: Tone; italic?: boolean; inverse?: boolean };

export type PanelLine = Segment[];

function renderSegment(segment: Segment, index: number): ReactNode {
  const classes = [
    tones[segment.tone ?? "fg"],
    segment.italic ? "italic" : "",
    segment.inverse ? "bg-term-fg text-term-bg" : "",
  ].join(" ");
  return (
    <span key={index} className={classes}>
      {segment.text}
    </span>
  );
}

export function Panel({
  caption,
  lines,
  className = "",
}: {
  caption: string;
  lines: PanelLine[];
  className?: string;
}) {
  return (
    <figure
      className={`overflow-hidden rounded-xl border border-line-strong bg-term-bg text-term-fg ${className}`}
    >
      <figcaption className="border-b border-white/10 px-4 py-2 font-mono text-xs text-term-muted">
        {caption}
      </figcaption>
      <pre className="overflow-x-auto p-4 font-mono text-[11.5px] leading-6 sm:px-5">
        <code>
          {lines.map((line, index) => (
            <span key={index} className="block">
              {line.length === 0 ? " " : line.map(renderSegment)}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}
