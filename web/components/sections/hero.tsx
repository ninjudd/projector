import { Command } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Terminal, type TermLine } from "@/components/terminal";
import { DOCS, GITHUB, INSTALL } from "@/lib/links";

const chips = ["MIT licensed", "Python 3.11+, zero dependencies", "Claude Code + Codex"];

const lines: TermLine[] = [
  { kind: "cmd", text: "project list" },
  { kind: "label", text: "now:" },
  { kind: "row", name: "payments", status: "in-progress", title: "Ledger-backed payments" },
  { kind: "label", text: "next:" },
  { kind: "row", name: "payments/invoices", status: "ready", title: "Issue from the ledger" },
  { kind: "row", name: "search-relevance", status: "draft", title: "Rank results by recency" },
  { kind: "label", text: "later:" },
  { kind: "row", name: "audit-log", status: "ready", title: "Record every mutation" },
  { kind: "label", text: "completed:" },
  { kind: "row", name: "adopt-projector", status: "completed", title: "Adopt Projector" },
  { kind: "blank" },
  { kind: "cmd", text: "project status payments/invoices in-progress" },
  { kind: "out", text: "docs/projects/payments/invoices/readme.md" },
  { kind: "cmd", text: "project check" },
  { kind: "out", text: "Project plans are valid." },
];

export function Hero() {
  return (
    <section className="relative overflow-hidden">
      <div className="paper pointer-events-none absolute inset-0" aria-hidden="true" />
      <div
        className="beam pointer-events-none absolute -top-40 right-[-8%] h-[38rem] w-[38rem]"
        aria-hidden="true"
      />
      <div className="relative mx-auto grid max-w-6xl items-center gap-14 px-6 pb-24 pt-20 lg:grid-cols-[1fr_1.15fr] lg:pb-32 lg:pt-28">
        <div>
          <ul className="flex flex-wrap gap-2">
            {chips.map((chip) => (
              <li
                key={chip}
                className="rounded-full border border-line bg-surface px-3 py-1 font-mono text-xs text-muted"
              >
                {chip}
              </li>
            ))}
          </ul>
          <h1 className="mt-8 font-display text-6xl leading-[0.98] tracking-tight text-fg sm:text-7xl">
            Project plans that
            <br />
            <em className="text-accent">live in Git.</em>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
            Projector gives every project a permanent home under <Code>docs/projects/</Code>, one
            command for people and coding agents, and review loops that carry a pull request to
            a clean head. No service, no account, no index to keep in sync.
          </p>
          <div className="mt-10 max-w-xl">
            <Command text={INSTALL.pipx} />
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-x-7 gap-y-3 text-sm">
            <a
              href={DOCS.index}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 font-medium text-fg transition-colors hover:text-accent"
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
              View on GitHub
              <ArrowUpRightIcon className="h-4 w-4" />
            </a>
            <a href="#how-it-works" className="text-muted transition-colors hover:text-fg">
              See how it works
            </a>
          </div>
        </div>
        <Terminal title="~/src/payments-service" lines={lines} />
      </div>
    </section>
  );
}
