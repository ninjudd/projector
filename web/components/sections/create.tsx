import type { ReactNode } from "react";
import { CodeBlock } from "@/components/code-block";
import { CommandList } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { Terminal, type TermLine } from "@/components/terminal";
import { DOCS, EXAMPLE } from "@/lib/links";

const commands = [
  "project init",
  "project create count-in --priority now",
  "project status count-in ready",
];

// Part of `project list` in audiofield/field.
const list: TermLine[] = [
  { kind: "cmd", text: "project list" },
  { kind: "label", text: "now:" },
  { kind: "row", name: "arming", status: "in-progress", title: "Arming" },
  { kind: "row", name: "instrument-knobs", status: "in-progress", title: "Instrument knobs" },
  { kind: "row", name: "sequencer", status: "ready", title: "Sequencer" },
  { kind: "out", text: "  …" },
  { kind: "label", text: "completed:" },
  { kind: "row", name: "recording", status: "completed", title: "Recording" },
];

// The first lines of docs/projects/recording/readme.md in audiofield/field.
const plan = `---
status: completed
---

**Outcome:** Shipped: chunks 1–4 (#166, #167, #169, #170) — the
daemon's input side with crash-safe capture, takes that land in the
project and build in place, the metronome and count-in in the engine,
punch-out, and loop takes over the region loop. …

# Recording

**Goal:** \`field record\` — capture a live performance into a track as
takes, through the same daemon that plays: armed, crash-safe,
latency-placed, and committed beside its bytes the way every take
already is.`;

function DocLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-sm font-medium text-fg transition-colors hover:text-accent"
    >
      {children}
      <ArrowUpRightIcon className="h-4 w-4" />
    </a>
  );
}

export function Create() {
  return (
    <Section
      id="plans"
      title="Every plan is a file in the repository"
      lead={
        <>
          Under the loop, each project is a directory under <Code>docs/projects/</Code> with a
          lowercase <Code>readme.md</Code>. The file opens with two frontmatter fields and continues
          as an ordinary Markdown plan. You and the agents change it with the same{" "}
          <Code>project</Code> command.
        </>
      }
    >
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-16">
        <div className="min-w-0 space-y-5 text-[15px] leading-relaxed text-muted">
          <CommandList commands={commands} />
          <p>
            <Code>project init</Code> writes the convention once per repository, and Projector&rsquo;s
            rules into <Code>AGENTS.md</Code>, linked as <Code>CLAUDE.md</Code>, so every agent
            session follows them, with the plugin or without it. <Code>project create</Code> makes the
            directory and the plan. <Code>project status</Code> changes one frontmatter field,{" "}
            <Code>project done</Code> marks the project completed, and <Code>project list</Code>{" "}
            groups what exists by priority. Add <Code>--json</Code> to any of them for scripts and
            agents.
          </p>
          <p>
            Because the plan is a file in the repository, it comes with every clone, along with its
            history. A status change is a one-line diff in the pull request that makes it true, so
            the plan and the code are reviewed together. Two people working on two projects edit
            two different files, so there is no shared status list to conflict on. The directory
            never moves, so links to a plan keep working. There is no server and no account.
          </p>
          <p>
            <Code>status</Code> tracks progress: <Code>draft</Code>, <Code>ready</Code>,{" "}
            <Code>in-progress</Code>, or <Code>completed</Code>. <Code>priority</Code> tracks when
            you want to work on it: <Code>now</Code>, <Code>next</Code>, or <Code>later</Code>. The
            two are independent. A draft can be the current priority while it is still being
            written, and in-progress work can drop to <Code>later</Code> without pretending it never
            started. A completed project needs no priority.
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
            <DocLink href={DOCS.convention}>Project convention</DocLink>
            <DocLink href={DOCS.cli}>CLI reference</DocLink>
          </div>
        </div>
        <div className="min-w-0 space-y-4">
          <Terminal title="audiofield/field" lines={list} />
          <CodeBlock
            lang="markdown"
            title="audiofield/field · docs/projects/recording/readme.md"
            code={plan}
          />
          <p className="text-sm text-muted">
            Both are from{" "}
            <a
              href={EXAMPLE.projects}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-line-strong underline-offset-4 transition-colors hover:text-fg"
            >
              the plans in field
            </a>
            , the repository the next section follows. The plan excerpt skips the rest of its outcome
            and the start of its body.{" "}
            <a
              href={EXAMPLE.plan}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-line-strong underline-offset-4 transition-colors hover:text-fg"
            >
              Read the whole file on GitHub.
            </a>
          </p>
        </div>
      </div>
    </Section>
  );
}
