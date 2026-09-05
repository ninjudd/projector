import { Command } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Terminal, type TermLine } from "@/components/terminal";
import { DOCS, EXAMPLE, GITHUB, INSTALL } from "@/lib/links";

// Real output from `project list` and `project check` in ninjudd/trip.
const lines: TermLine[] = [
  { kind: "cmd", text: "project list" },
  { kind: "label", text: "now:" },
  { kind: "row", name: "resume", status: "ready", title: "Resume" },
  { kind: "label", text: "completed:" },
  { kind: "row", name: "session-switcher", status: "completed", title: "Session Switcher" },
  { kind: "blank" },
  { kind: "cmd", text: "project check" },
  { kind: "out", text: "Project plans are valid." },
];

export function Hero() {
  return (
    <section>
      <div className="mx-auto grid max-w-6xl items-center gap-14 px-6 pb-20 pt-16 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:pb-28 lg:pt-24">
        <div className="min-w-0">
          <h1 className="font-display text-6xl leading-[0.98] tracking-tight text-fg sm:text-7xl">
            Project plans that
            <br />
            <em className="text-accent">live in Git.</em>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
            Projector keeps each project&rsquo;s plan as a Markdown file in the repository, under{" "}
            <Code>docs/projects/</Code>. Developers and coding agents read and update it with the
            same command-line tool.
          </p>
          <div className="mt-8 max-w-xl">
            <Command text={INSTALL.pipx} />
          </div>
          <p className="mt-3 font-mono text-xs text-faint">
            Python 3.11 or newer · MIT license · plugins for Claude Code and Codex
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-x-7 gap-y-3 text-sm">
            <a href="#create" className="font-medium text-fg transition-colors hover:text-accent">
              How it works
            </a>
            <a
              href={DOCS.index}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-muted transition-colors hover:text-fg"
            >
              Read the docs
              <ArrowUpRightIcon className="h-4 w-4" />
            </a>
            <a
              href={GITHUB}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-muted transition-colors hover:text-fg"
            >
              GitHub
              <ArrowUpRightIcon className="h-4 w-4" />
            </a>
          </div>
        </div>
        <div className="min-w-0">
          <Terminal title="ninjudd/trip" lines={lines} />
          <p className="mt-3 text-sm text-muted">
            Output from{" "}
            <a
              href={EXAMPLE.projects}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-line-strong underline-offset-4 transition-colors hover:text-fg"
            >
              the plans in ninjudd/trip
            </a>
            , the project used as the example below.
          </p>
        </div>
      </div>
    </section>
  );
}
