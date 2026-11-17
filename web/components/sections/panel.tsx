import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Panel as PanelMock, type PanelLine, type Tone } from "@/components/panel";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const states: { word: string; tone: Tone; italic?: boolean; body: string }[] = [
  { word: "conflict", tone: "conflict", body: "The branch conflicts with its base." },
  { word: "failing", tone: "conflict", body: "The head's checks fail." },
  { word: "changes", tone: "changes", body: "A review requests changes." },
  { word: "findings", tone: "changes", body: "Review threads are unresolved. Each one is a finding." },
  { word: "reviewing", tone: "reviewing", body: "A review of the head is running." },
  { word: "clean", tone: "clean", body: "A review signed off the current head." },
  { word: "draft", tone: "fg", italic: true, body: "Its author is still working on it." },
  { word: "ready", tone: "ready", body: "It waits for its first review." },
];

const toneColor: Record<Tone, string> = {
  fg: "text-fg",
  muted: "text-muted",
  dim: "text-faint",
  conflict: "text-[#c43d3d] dark:text-[#f2777a]",
  changes: "text-[#9a6b00] dark:text-[#e5c07b]",
  reviewing: "text-[#c25a12] dark:text-[#f99157]",
  clean: "text-[#3d7a35] dark:text-[#99c794]",
  ready: "text-[#2b6f9e] dark:text-[#8fc1e3]",
  question: "text-[#5560c9] dark:text-[#b1b9f9]",
  request: "text-[#b4592a] dark:text-[rgb(255,190,140)]",
  notice: "text-muted",
};

// A question's drawer, as docs/plugins.md shows it.
const drawer: PanelLine[] = [
  [
    { text: "   0: Decline the finding about the retry budget?        ", tone: "question" },
    { text: "#337  5m", tone: "question" },
  ],
  [],
  [{ text: "      The review asks for a retry budget per host. The loop" }],
  [{ text: "      caps retries at three in total, which covers the case" }],
  [{ text: "      the finding names." }],
  [],
  [{ text: "      1: Decline it: the total cap already bounds retries" }],
  [{ text: "      (recommended)", tone: "muted" }],
  [{ text: "      2: Add a budget of two retries per host" }],
  [],
  [{ text: "      6: I don't understand", tone: "muted" }],
  [{ text: "      7: Discuss  8: Dismiss  9: Snooze", tone: "muted" }],
];

const tools: { name: string; body: string }[] = [
  {
    name: "ask",
    body: "a question, with the answer the agent recommends and up to four others.",
  },
  {
    name: "tell",
    body: "news that needs no answer, such as a conflict the agent merged away.",
  },
  {
    name: "request",
    body: "work only you can do, such as signing in to a service or checking a build on your phone.",
  },
];

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

export function Panel() {
  return (
    <Section
      id="panel"
      title="See what needs you, and only that"
      lead={
        <>
          In Claude Code, Projector&rsquo;s panel sits above the prompt. It lists each pull request
          your session fixes or reviews, with its state, and an inbox of what the agents need from
          you. Run <Code>/projector</Code> to show or hide it.
        </>
      }
    >
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-16">
        <div className="min-w-0 space-y-8">
          <div>
            <h3 className="text-base font-semibold tracking-tight text-fg">
              Each row says what a pull request is waiting for
            </h3>
            <dl className="mt-4 grid grid-cols-[6rem_minmax(0,1fr)] gap-x-4 gap-y-2 text-[15px]">
              {states.map((state) => (
                <div key={state.word} className="contents">
                  <dt
                    className={`font-mono text-sm leading-relaxed ${toneColor[state.tone]} ${state.italic ? "italic" : ""}`}
                  >
                    {state.word}
                  </dt>
                  <dd className="leading-relaxed text-muted">{state.body}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-4 text-[15px] leading-relaxed text-muted">
              Click a pull request to open its drawer: its review threads under their files, its
              checks, and links to it on GitHub and on the repository&rsquo;s Projector site.
            </p>
          </div>
          <div>
            <h3 className="text-base font-semibold tracking-tight text-fg">
              Agents post to the inbox and keep working
            </h3>
            <ul className="mt-4 space-y-3 text-[15px] leading-relaxed text-muted">
              {tools.map((tool) => (
                <li key={tool.name}>
                  <Code>{tool.name}</Code> posts {tool.body}
                </li>
              ))}
            </ul>
            <p className="mt-4 text-[15px] leading-relaxed text-muted">
              To answer, press an option&rsquo;s digit. The panel puts the question and that answer
              in your prompt, for you to send or change first. When you write your own reply
              instead, the agent answers it, then withdraws the item or revises it.
            </p>
          </div>
          <p className="text-[15px] leading-relaxed text-muted">
            The panel is part of the Claude Code plugin. In Codex or a terminal,{" "}
            <Code>project inbox</Code> reaches the same inbox, and Codex gets the same pull request
            events as prompts.
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <DocLink href={DOCS.panel}>The panel</DocLink>
            <DocLink href={DOCS.inbox}>project inbox</DocLink>
          </div>
        </div>
        <div className="min-w-0 lg:sticky lg:top-24 lg:self-start">
          <PanelMock caption="A question's drawer" lines={drawer} />
          <p className="mt-3 text-sm text-muted">
            The first option is always the agent&rsquo;s recommendation. Press <Code>1</Code> and
            send, and the agent goes on with that answer.
          </p>
        </div>
      </div>
    </Section>
  );
}
