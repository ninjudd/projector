import type { ReactNode } from "react";
import { CodeBlock } from "@/components/code-block";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { EXAMPLE } from "@/lib/links";

// The frontmatter hunk from audiofield/field#166.
const diff = `-status: draft
-priority: later
+status: in-progress
+priority: now`;

// The plan's history in audiofield/field, from the first chunk to the later fix.
const log = `5cd6b9f Keep a loop take running when the heartbeat falls behind its wraps (recording.md §15.4) (#204)
ca065ec Punch-out, the region loop, and loop takes (recording.md chunk 4) (#170)
2a44085 Move the metronome into the engine and count a take in on the exact sample (recording.md chunk 3) (#169)
7c84226 Land recorded takes: the record entry, takes.yaml, and placement on the track (recording.md chunk 2) (#167)
e7f862b Open the daemon's input side: arm, record, stop, crash-safe capture (recording.md chunk 1) (#166)`;

// The first line and the closing counts of the last review of audiofield/field#204.
const review = `Projector review · 0.7.8 · model claude-opus-5-5 · effort xhigh · CLEAN

4 finding threads: 4 resolved, 0 open
Covered 6 of 6 changed files; none generated.`;

const steps: { pr: number; title: string; body: ReactNode }[] = [
  {
    pr: 135,
    title: "The plan merges before any code",
    body: (
      <>
        The pull request added the recording plan, and the plan for the real-time engine it waited
        behind, with no code. Every review thread on it was resolved before it merged, so the
        design was argued out before anything was built.
      </>
    ),
  },
  {
    pr: 166,
    title: "Four pull requests build it, and the plan records each",
    body: (
      <>
        The first chunk moved the plan to <Code>in-progress</Code> and <Code>now</Code>. Each of
        the four chunks appended a section on what its build settled, sections 12 to 15, instead of
        editing the sections that had been reviewed.
      </>
    ),
  },
  {
    pr: 167,
    title: "A review catches a misplaced take",
    body: (
      <>
        The review of the second chunk found that the code that lands a take counted its start from
        the track, while the audio graph counted it from the take&rsquo;s own entry. So a take could
        sound somewhere other than where <Code>field record</Code> said it did. The fix kept the
        graph&rsquo;s reading, added a test that checks both, and replied with the commit,{" "}
        <Code>888d33d</Code>. The next review signed off that head.
      </>
    ),
  },
  {
    pr: 204,
    title: "A later fix runs the whole loop",
    body: (
      <>
        A month later, a bug found in use came back as its own pull request, and amended section
        15.4 of the plan. Six reviews read it, one for each head. Four found something, and each
        reply named the commit that fixed it. Two came back clean, the last on the head that merged.
      </>
    ),
  },
];

export function Example() {
  return (
    <Section
      id="example"
      title="An example: recording in field"
      lead={
        <>
          <a
            href={EXAMPLE.repo}
            target="_blank"
            rel="noreferrer"
            className="text-fg underline decoration-line-strong underline-offset-4 transition-colors hover:text-accent"
          >
            field
          </a>{" "}
          is a music production system that keeps its plans with Projector. Its recording project
          added <Code>field record</Code>, which captures a live performance into a track as takes.
          Agents built it in four pull requests after the plan, reviewed each one, and fixed what
          the reviews found.
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
                  audiofield/field#{step.pr}
                  <ArrowUpRightIcon className="h-3.5 w-3.5" />
                </a>
              </div>
            </li>
          ))}
        </ol>
        <div className="min-w-0 space-y-4 lg:sticky lg:top-24 lg:self-start">
          <CodeBlock
            lang="diff"
            title="audiofield/field#166 · docs/projects/recording/readme.md"
            code={diff}
          />
          <figure className="overflow-hidden rounded-xl border border-line bg-surface">
            <figcaption className="border-b border-line px-4 py-2 font-mono text-xs text-faint">
              The plan&rsquo;s history, from the first chunk to the later fix
            </figcaption>
            <pre className="whitespace-pre-wrap break-words p-4 font-mono text-[12.5px] leading-6 text-fg">{log}</pre>
          </figure>
          <figure className="overflow-hidden rounded-xl border border-line bg-surface">
            <figcaption className="border-b border-line px-4 py-2 font-mono text-xs text-faint">
              audiofield/field#204 · the last review
            </figcaption>
            <pre className="whitespace-pre-wrap break-words p-4 font-mono text-[12.5px] leading-6 text-fg">
              {review}
            </pre>
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

      {/* Written in Justin's voice for him to edit. */}
      <div className="mt-16 max-w-2xl border-t border-line pt-8">
        <h3 className="text-lg font-semibold tracking-tight text-fg">Why I built it</h3>
        <p className="mt-3 text-[15px] leading-relaxed text-muted">
          My plans used to live wherever I happened to write them: Linear, Apple Notes, Notion,
          Google Docs, and Sheets. None of those places sat next to the code, so a coding agent
          could read a plan only when I pasted it in, and nothing kept the plan current as the work
          changed it. Projector keeps each plan in the repository it describes, where every clone,
          every review, and every agent session can read it. It started as a plan of its own,
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
