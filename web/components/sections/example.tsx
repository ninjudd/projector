import type { ReactNode } from "react";
import { CodeBlock } from "@/components/code-block";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { EXAMPLE } from "@/lib/links";

// The frontmatter hunk from ninjudd/trip#16.
const diff = `-status: ready
+status: completed
 priority: now`;

// git log --oneline -- docs/projects/session-switcher/readme.md, in ninjudd/trip.
const log = `39814bc Ground the terminal's keyboard protocol on every re-render (#18)
5d7c2a5 Order the chooser by when sessions were last opened (#17)
3cc4333 Implement the session switcher (#16)
66de6c6 Plan the session switcher (#13)`;

const steps: { pr: number; title: string; body: ReactNode }[] = [
  {
    pr: 13,
    title: "The plan merges on its own",
    body: (
      <>
        The pull request added the plan and no code, so the design was reviewed before anything
        was built. Its frontmatter said <Code>ready</Code> and <Code>now</Code>. Among the decisions
        it listed as worth arguing with: sort the current workspace first, so its sessions hold the
        low digits.
      </>
    ),
  },
  {
    pr: 16,
    title: "Implementation marks it completed",
    body: (
      <>
        The pull request that built the switcher set the status to <Code>completed</Code> in the
        same change. Two things the plan got wrong were recorded as a new section 9 rather than by
        editing the sections that had been reviewed. A retry the plan specified could never fire,
        so it became an allocation, and a daemon response the plan relied on turned out to be dead
        code.
      </>
    ),
  },
  {
    pr: 17,
    title: "A decision changes after use",
    body: (
      <>
        After a day with the switcher, the ordering changed from current workspace first to most
        recently opened first, like a task switcher. Section 9.10 records the new rule and the
        create gesture it retired. Section 3.5 still holds the original reasoning, so the tradeoff
        can be read from both sides.
      </>
    ),
  },
  {
    pr: 18,
    title: "A later fix lands the same way",
    body: (
      <>
        A keyboard-protocol leak found in use was fixed and recorded as section 9.11. The file has
        not moved since the first pull request.
      </>
    ),
  },
];

export function Example() {
  return (
    <Section
      id="example"
      title="An example: the session switcher in trip"
      lead={
        <>
          <a
            href={EXAMPLE.repo}
            target="_blank"
            rel="noreferrer"
            className="text-fg underline decoration-line-strong underline-offset-4 transition-colors hover:text-accent"
          >
            trip
          </a>{" "}
          is a terminal session runtime that keeps its plans with Projector. Its session-switcher
          project went from plan to completion in four pull requests, and the plan recorded each
          step.
        </>
      }
    >
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:gap-16">
        <ol className="min-w-0 space-y-9">
          {steps.map((step, index) => (
            <li key={step.pr} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-4">
              <span className="mt-1 font-mono text-sm text-accent">{index + 1}</span>
              <div className="min-w-0">
                <h3 className="text-lg font-semibold tracking-tight text-fg">{step.title}</h3>
                <p className="mt-2 text-[15px] leading-relaxed text-muted">{step.body}</p>
                <a
                  href={EXAMPLE.pr(step.pr)}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-3 inline-flex items-center gap-1 font-mono text-sm text-muted transition-colors hover:text-fg"
                >
                  ninjudd/trip#{step.pr}
                  <ArrowUpRightIcon className="h-3.5 w-3.5" />
                </a>
              </div>
            </li>
          ))}
        </ol>
        <div className="min-w-0 space-y-4 lg:sticky lg:top-24 lg:self-start">
          <CodeBlock
            lang="diff"
            title="ninjudd/trip#16 · docs/projects/session-switcher/readme.md"
            code={diff}
          />
          <figure className="overflow-hidden rounded-xl border border-line bg-surface">
            <figcaption className="border-b border-line px-4 py-2 font-mono text-xs text-faint">
              git log --oneline -- docs/projects/session-switcher/readme.md
            </figcaption>
            <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-6 text-fg">{log}</pre>
          </figure>
          <a
            href={EXAMPLE.plan}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm font-medium text-fg transition-colors hover:text-accent"
          >
            Read the plan as it stands today
            <ArrowUpRightIcon className="h-4 w-4" />
          </a>
        </div>
      </div>

      {/*
        Drawn from the description of ninjudd/projector#24, the pull request
        that planned Projector, and from the completion record in
        docs/projects/projector/readme.md. Written in Justin's voice for him
        to edit.
      */}
      <div className="mt-16 max-w-2xl border-t border-line pt-8">
        <h3 className="text-lg font-semibold tracking-tight text-fg">Why I built it</h3>
        <p className="mt-3 text-[15px] leading-relaxed text-muted">
          Before Projector, the repositories I work in kept their plans in shared{" "}
          <Code>now.md</Code>, <Code>next.md</Code>, and <Code>later.md</Code> files. Every branch
          that changed a project&rsquo;s status edited the same three files, so unrelated branches
          conflicted with each other, and the plans sat next to my personal agent configuration,
          where nothing about them could be reused. Projector started as a plan for fixing that,
          written in the format it proposed, before the CLI existed.
        </p>
        <p className="mt-3 text-sm text-muted">
          <a
            href="https://github.com/ninjudd"
            target="_blank"
            rel="noreferrer"
            className="text-fg transition-colors hover:text-accent"
          >
            Justin Balthrop
          </a>
        </p>
      </div>
    </Section>
  );
}
