import { Command } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { InboxDemo } from "@/components/inbox-demo";
import { DOCS, GITHUB, INSTALL } from "@/lib/links";

const tries: { label: string; body: string }[] = [
  {
    label: "#344",
    body: "Open its drawer to see the findings, gathered under their files.",
  },
  {
    label: "A question",
    body: "Pick an option, and the panel fills in your prompt.",
  },
  {
    label: "The iPhone request",
    body: "Press Start, and the agent installs the build on your phone.",
  },
  {
    label: "A notice",
    body: "Read it, then dismiss it, snooze it, or ask about it.",
  },
];

export function Hero() {
  return (
    <section>
      <div className="mx-auto max-w-6xl px-6 pb-20 pt-16 lg:pb-24 lg:pt-20">
        <div className="grid gap-8 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:items-start lg:gap-16">
          <div className="min-w-0">
            <h1 className="font-display text-6xl leading-[0.98] tracking-tight text-fg sm:text-7xl">
              Agents plan, build, and review.
              <br />
              <em className="text-accent">You make the calls.</em>
            </h1>
            <div className="mt-8 max-w-xl">
              <Command text={INSTALL.script} />
            </div>
            <p className="mt-3 font-mono text-xs text-faint">
              Python 3.11 or newer · MIT license · plugins for Claude Code and
              Codex
            </p>
            <div className="mt-5 flex flex-wrap items-center gap-x-7 gap-y-3 text-sm">
              <a
                href="#loop"
                className="font-medium text-fg transition-colors hover:text-accent"
              >
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
          <p className="min-w-0 text-lg leading-relaxed text-muted lg:pt-2">
            Projector gives Claude Code and Codex a workflow for projects.
            Agents write the plan into the repository, build it, review each
            other&rsquo;s pull requests, and fix what the reviews find. You
            review the plan, answer the questions only you can, and decide what
            merges. Every question waits for you in a panel above your prompt,
            and every pull request comes with a summary that walks you through
            the change.
          </p>
        </div>

        <div className="mt-14 grid gap-8 lg:grid-cols-[minmax(0,1fr)_17rem] lg:gap-10">
          <InboxDemo />
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight text-fg">
              Try the panel
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              This is Projector&rsquo;s panel as it sits above your prompt in
              Claude Code. Every line opens.
            </p>
            <ul className="mt-5 space-y-4">
              {tries.map((item) => (
                <li
                  key={item.label}
                  className="border-t border-line pt-3 text-sm leading-relaxed"
                >
                  <span className="font-medium text-fg">{item.label}.</span>{" "}
                  <span className="text-muted">{item.body}</span>
                </li>
              ))}
            </ul>
            <a
              href="#panel"
              className="mt-5 inline-block text-sm font-medium text-fg transition-colors hover:text-accent"
            >
              What each line means ↓
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
