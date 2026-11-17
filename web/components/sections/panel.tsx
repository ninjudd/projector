import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { InboxDemo } from "@/components/inbox-demo";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const kinds: { title: string; tool: string; tone: string; body: ReactNode }[] = [
  {
    title: "Questions",
    tool: "ask",
    tone: "text-[#5560c9] dark:text-[#b1b9f9]",
    body: (
      <>
        A decision only you can make, with the answer the agent recommends first and up to four
        others. Press a digit, and the panel puts the question and that answer in your prompt to
        send or edit. Write your own reply instead, and the agent answers it, then withdraws or
        revises the question. Meanwhile it keeps building what your answer does not block.
      </>
    ),
  },
  {
    title: "Requests",
    tool: "request",
    tone: "text-[#b4592a] dark:text-[rgb(255,190,140)]",
    body: (
      <>
        Work only you can do: sign in to a service, run a command the agent may not, or try a build
        on your phone. A request can carry a start action, such as installing that build, which the
        agent runs only when you press <strong className="font-medium text-fg">Start</strong>. The
        option you pick, such as <strong className="font-medium text-fg">Looks good</strong>, marks
        it done, and your notes go with it.
      </>
    ),
  },
  {
    title: "Notices",
    tool: "tell",
    tone: "text-muted",
    body: (
      <>
        News you should not miss among everything else the agent says, such as a conflict it
        merged away or a failure that needs you. Read it, then dismiss it. Nothing goes back to the
        agent.
      </>
    ),
  },
];

const states: { word: string; tone: string; italic?: boolean; body: string }[] = [
  {
    word: "conflict",
    tone: "text-[#c43d3d] dark:text-[#f2777a]",
    body: "The branch conflicts with its base.",
  },
  { word: "failing", tone: "text-[#c43d3d] dark:text-[#f2777a]", body: "The head's checks fail." },
  { word: "changes", tone: "text-[#9a6b00] dark:text-[#e5c07b]", body: "A review requests changes." },
  {
    word: "findings",
    tone: "text-[#9a6b00] dark:text-[#e5c07b]",
    body: "Review threads are unresolved. Each one is a finding.",
  },
  {
    word: "reviewing",
    tone: "text-[#c25a12] dark:text-[#f99157]",
    body: "A review of the head is running.",
  },
  {
    word: "clean",
    tone: "text-[#3d7a35] dark:text-[#99c794]",
    body: "A review signed off the current head.",
  },
  { word: "draft", tone: "text-fg", italic: true, body: "Its author is still working on it." },
  {
    word: "ready",
    tone: "text-[#2b6f9e] dark:text-[#8fc1e3]",
    body: "It waits for its first review.",
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
      title="Answer your agents from one inbox"
      lead={
        <>
          In Claude Code, Projector&rsquo;s panel sits above your prompt. Agents post what they need
          from you to its inbox and keep working: a question to answer, a request for work only you
          can do, or a notice to read. Above the inbox, each pull request your session fixes or
          reviews shows its state. Try it: click any line, press a digit, or open a pull
          request&rsquo;s number.
        </>
      }
    >
      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_19rem] lg:gap-12">
        <InboxDemo />
        <ul className="space-y-8">
          {kinds.map((kind) => (
            <li key={kind.title} className="min-w-0 border-t border-line-strong pt-4 first:border-t-0 first:pt-0">
            <h3 className="flex items-baseline gap-2 text-lg font-semibold tracking-tight">
              <span className={kind.tone}>{kind.title}</span>
              <Code>{kind.tool}</Code>
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">{kind.body}</p>
          </li>
          ))}
        </ul>
      </div>
      <p className="mt-8 max-w-3xl text-[15px] leading-relaxed text-muted">
        Every item offers <strong className="font-medium text-fg">I don&rsquo;t understand</strong>.
        Say what you don&rsquo;t follow, and the agent rewrites the item in place.{" "}
        <strong className="font-medium text-fg">Snooze</strong> puts an item off for up to a day,
        and a dismissed item can be restored until it leaves the list.
      </p>

      <div className="mt-16 grid gap-12 lg:grid-cols-2 lg:gap-16">
        <div className="min-w-0">
          <h3 className="text-lg font-semibold tracking-tight text-fg">
            Each row says what a pull request is waiting for
          </h3>
          <dl className="mt-4 grid grid-cols-[6rem_minmax(0,1fr)] gap-x-4 gap-y-2 text-[15px]">
            {states.map((state) => (
              <div key={state.word} className="contents">
                <dt
                  className={`font-mono text-sm leading-relaxed ${state.tone} ${state.italic ? "italic" : ""}`}
                >
                  {state.word}
                </dt>
                <dd className="leading-relaxed text-muted">{state.body}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="min-w-0 space-y-4 text-[15px] leading-relaxed text-muted">
          <h3 className="text-lg font-semibold tracking-tight text-fg">A drawer holds the rest</h3>
          <p>
            Click a pull request&rsquo;s number to open its drawer: whether your session fixes or
            reviews it, its branch, its size, its checks, and its labels. Under those come links to
            the pull request on GitHub and to its summary, and each unresolved finding, gathered
            under its file. <strong className="font-medium text-fg">Discuss</strong> puts the
            number in your prompt, and <strong className="font-medium text-fg">Dismiss</strong>{" "}
            stops the session watching it.
          </p>
          <p>
            Your own pull requests come first, then the ones that ask for your review. The panel
            works from the keyboard too: <Code>ctrl+x tab</Code> moves into it, and Esc gives the
            keyboard back.
          </p>
          <p>
            The panel is part of the Claude Code plugin. In Codex or a terminal,{" "}
            <Code>project inbox</Code> reaches the same inbox, and Codex gets the same pull request
            events as prompts.
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
            <DocLink href={DOCS.panel}>The panel</DocLink>
            <DocLink href={DOCS.inboxPanel}>The inbox</DocLink>
            <DocLink href={DOCS.inbox}>project inbox</DocLink>
          </div>
        </div>
      </div>
    </Section>
  );
}
