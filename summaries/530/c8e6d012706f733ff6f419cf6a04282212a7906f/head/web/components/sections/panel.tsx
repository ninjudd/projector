import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const kinds: { title: string; tool: string; tone: string; body: ReactNode }[] =
  [
    {
      title: "Questions",
      tool: "ask",
      tone: "text-[#5560c9] dark:text-[#b1b9f9]",
      body: (
        <>
          A decision only you can make, with the agent&rsquo;s recommendation
          first. Press a digit to answer, or reply in your own words.
        </>
      ),
    },
    {
      title: "Requests",
      tool: "request",
      tone: "text-[#b4592a] dark:text-[rgb(255,190,140)]",
      body: (
        <>
          Work only you can do, such as signing in or trying a build on your
          phone. Installing that build waits until you press{" "}
          <strong className="font-medium text-fg">Start</strong>.
        </>
      ),
    },
    {
      title: "Notices",
      tool: "tell",
      tone: "text-muted",
      body: (
        <>
          News you should not miss, such as a conflict an agent merged away.
          Read it, then dismiss it.
        </>
      ),
    },
  ];

const states: { word: string; tone: string; italic?: boolean; body: string }[] =
  [
    {
      word: "conflict",
      tone: "text-[#c43d3d] dark:text-[#f2777a]",
      body: "The branch conflicts with its base.",
    },
    {
      word: "failing",
      tone: "text-[#c43d3d] dark:text-[#f2777a]",
      body: "The head's checks fail.",
    },
    {
      word: "changes",
      tone: "text-[#9a6b00] dark:text-[#e5c07b]",
      body: "A review requests changes.",
    },
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
    {
      word: "draft",
      tone: "text-fg",
      italic: true,
      body: "Its author is still working on it.",
    },
    {
      word: "ready",
      tone: "text-[#2b6f9e] dark:text-[#8fc1e3]",
      body: "It waits for a review of its current head.",
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
          In Claude Code, Projector&rsquo;s panel sits above your prompt. Agents
          post what they need from you to its inbox and keep working: a question
          to answer, a request for work only you can do, or a notice to read.
          Above the inbox, each pull request your session fixes or reviews shows
          its state. The panel at the top of this page works the same way.
        </>
      }
    >
      <ul className="grid gap-10 md:grid-cols-3">
        {kinds.map((kind) => (
          <li
            key={kind.title}
            className="min-w-0 border-t border-line-strong pt-5"
          >
            <h3 className="flex items-baseline gap-2 text-lg font-semibold tracking-tight">
              <span className={kind.tone}>{kind.title}</span>
              <Code>{kind.tool}</Code>
            </h3>
            <p className="mt-3 text-[15px] leading-relaxed text-muted">
              {kind.body}
            </p>
          </li>
        ))}
      </ul>
      <p className="mt-8 max-w-3xl text-[15px] leading-relaxed text-muted">
        Every item offers{" "}
        <strong className="font-medium text-fg">
          I don&rsquo;t understand
        </strong>
        . Say what you don&rsquo;t follow, and the agent rewrites the item in
        place. <strong className="font-medium text-fg">Snooze</strong> puts an
        item off for up to a day, and a dismissed item can be restored until it
        leaves the list.
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
          <h3 className="text-lg font-semibold tracking-tight text-fg">
            A drawer holds the rest
          </h3>
          <p>
            Click a pull request&rsquo;s number to open its drawer: whether your
            session fixes or reviews it, its branch, its size, its checks, and
            its labels. Under those come links to the pull request on GitHub and
            to its summary, and each unresolved finding, gathered under its
            file. <strong className="font-medium text-fg">Discuss</strong> puts
            the number in your prompt, and{" "}
            <strong className="font-medium text-fg">Dismiss</strong> stops the
            session watching it.
          </p>
          <p>
            Your own pull requests come first, then the ones that ask for your
            review. The panel works from the keyboard too:{" "}
            <Code>ctrl+x tab</Code> moves into it, and Esc gives the keyboard
            back.
          </p>
          <p>
            The panel is part of the Claude Code plugin. In Codex or a terminal,{" "}
            <Code>project console</Code> shows every live session&apos;s pull
            requests and items, and answers and discussions reach each session
            as a new turn, as they do from the panel.
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
            <DocLink href={DOCS.panel}>The panel</DocLink>
            <DocLink href={DOCS.inboxPanel}>The inbox</DocLink>
            <DocLink href={DOCS.inbox}>project inbox</DocLink>
            <DocLink href={DOCS.console}>project console</DocLink>
          </div>
        </div>
      </div>
    </Section>
  );
}
