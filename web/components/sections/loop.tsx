import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const steps: { title: string; invoke: string; body: ReactNode }[] = [
  {
    title: "Spec it",
    invoke: "/spec a count-in before each take",
    body: (
      <>
        The agent reads the repository and its existing projects, asks only what the code cannot
        answer, and writes the plan to <Code>docs/projects/&lt;name&gt;/readme.md</Code>. It opens
        the plan as a draft pull request and asks you in the inbox to review it, so the design is
        settled before any code.
      </>
    ),
  },
  {
    title: "Build it",
    invoke: "/implement count-in",
    body: (
      <>
        A later session starts from the plan, not from the earlier conversation. It builds the
        work, appends what the build settled to the plan, and opens a draft pull request. Once its
        work is done, it marks the pull request ready with <Code>project review ready</Code>, which
        asks for a review.
      </>
    ),
  },
  {
    title: "Review it",
    invoke: "/review-repo",
    body: (
      <>
        Another session reviews each pull request once it is ready. It checks out the exact head,
        reads the change against the plan and the repository&rsquo;s rules, and posts one review,
        linked to a summary of the change.
        A clean review labels the pull request <Code>clean</Code>. The next push removes the label,
        so each new head waits for its own review.
      </>
    ),
  },
  {
    title: "Fix what the review finds",
    invoke:
      "FINDING acme/app#12 docs/x.md:40 [bugbot]: P2 the link is broken — run fix-pr on acme/app#12 in subagent fix-12",
    body: (
      <>
        A daemon watches the pull requests, and wakes the session that pushed one with each
        finding, conflict, and failing check. That session runs <Code>fix-pr</Code>: it verifies
        each finding, fixes and pushes the real ones, replies with the commit, and declines the
        rest with evidence. The reviewer then checks each fix on the new head.
      </>
    ),
  },
  {
    title: "You decide what merges",
    invoke: "/finish count-in",
    body: (
      <>
        Projector opens pull requests and never merges them. You read the plan, the review, and its
        summary, and merge when you agree. The pull request that carries the last of the
        project&rsquo;s code runs <Code>project done</Code>, so the plan reads{" "}
        <Code>completed</Code> once it merges.
      </>
    ),
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

export function Loop() {
  return (
    <Section
      id="loop"
      title="From plan to merge, the agents do the work between"
      lead={
        <>
          Projector&rsquo;s plugin adds skills to Claude Code and Codex. A skill is a written
          procedure the agent follows. Together they carry a project from its plan to a pull
          request you can merge, and each stops for you only where you have to decide.
        </>
      }
    >
      <ol className="divide-y divide-line border-y border-line">
        {steps.map((step, index) => (
          <li
            key={step.title}
            className="grid gap-4 py-7 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:gap-12"
          >
            <div className="grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] gap-x-2">
              <span className="mt-0.5 font-mono text-sm text-accent">{index + 1}</span>
              <div className="min-w-0">
                <h3 className="text-lg font-semibold tracking-tight text-fg">{step.title}</h3>
                <p className="mt-2 text-[15px] leading-relaxed text-muted">{step.body}</p>
              </div>
            </div>
            <pre className="min-w-0 self-start whitespace-pre-wrap break-words rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[13px] leading-6 text-fg lg:mt-1">
              {step.invoke}
            </pre>
          </li>
        ))}
      </ol>
      <div className="mt-12 grid gap-8 lg:grid-cols-2 lg:gap-16">
        <p className="text-[15px] leading-relaxed text-muted">
          There is no loop for you to start. Opening or pushing a pull request subscribes the session
          to fix it, and one background process per machine polls GitHub for every session. In
          Claude Code the events arrive as prompts, and in Codex through its own queue. The session
          that wrote a pull request does not review it unless you ask.
        </p>
        <p className="text-[15px] leading-relaxed text-muted">
          A clean review is one model&rsquo;s reading of one commit. It catches what a careful first
          pass catches, and it is not proof that the change is correct. A review runs a pull
          request&rsquo;s tests only when it trusts everyone who could push to it, and asks you
          before it runs anyone else&rsquo;s code.
        </p>
      </div>
      <div className="mt-8 flex flex-wrap gap-x-6 gap-y-2">
        <DocLink href={DOCS.plugins}>Plugin guide</DocLink>
        <DocLink href={DOCS.reviewPr}>review-pr</DocLink>
        <DocLink href={DOCS.fixPr}>fix-pr</DocLink>
        <DocLink href={DOCS.watch}>project watch</DocLink>
      </div>
    </Section>
  );
}
