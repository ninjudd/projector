import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const steps: { title: string; invoke: string; body: ReactNode }[] = [
  {
    title: "Ask for a plan",
    invoke: "/projector:plan-project a session chooser opened by the detach key",
    body: (
      <>
        The agent reads the repository and the existing projects first, asks only what the code
        cannot answer, and writes the plan with <Code>project create</Code>. It leaves the change
        for ordinary review, so the plan can go up as its own pull request, the way trip&rsquo;s
        did.
      </>
    ),
  },
  {
    title: "Come back and build it",
    invoke: "/projector:work-project session-switcher",
    body: (
      <>
        A later session starts from the file, not from the earlier conversation. The agent moves
        the status to <Code>in-progress</Code> in the implementation branch, builds the work, and
        appends what implementation settled to the plan instead of rewriting the sections that
        were reviewed.
      </>
    ),
  },
  {
    title: "Finish it",
    invoke: "/projector:finish-project session-switcher",
    body: (
      <>
        The agent checks each acceptance criterion in the plan against the repository, records
        whether the work shipped, was abandoned, or was superseded, and runs{" "}
        <Code>project done</Code> in the change that completes it.
      </>
    ),
  },
];

export function Agents() {
  return (
    <Section
      id="agents"
      title="Use with Claude Code or Codex"
      lead={
        <>
          Projector&rsquo;s plugin adds skills to Claude Code and Codex. A skill is a written
          procedure the agent follows, and each one works through the same <Code>project</Code>{" "}
          command you use, so what the agent saves is a file you can open and edit. No MCP server is
          involved.
        </>
      }
    >
      <ol className="grid gap-8 lg:grid-cols-3">
        {steps.map((step, index) => (
          <li key={step.title} className="min-w-0">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-sm text-accent">{index + 1}</span>
              <h3 className="text-lg font-semibold tracking-tight text-fg">{step.title}</h3>
            </div>
            <pre className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[13px] text-fg">
              {step.invoke}
            </pre>
            <p className="mt-4 text-[15px] leading-relaxed text-muted">{step.body}</p>
          </li>
        ))}
      </ol>
      <p className="mt-10 max-w-2xl text-[15px] leading-relaxed text-muted">
        In Codex the same skills are <Code>$plan-project</Code>, <Code>$work-project</Code>, and{" "}
        <Code>$finish-project</Code>. The plugin also carries the two review skills described next,
        and <Code>gh-stack</Code>, for splitting a large change into a chain of dependent pull
        requests.{" "}
        <a
          href={DOCS.plugins}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-0.5 text-fg transition-colors hover:text-accent"
        >
          Plugin guide
          <ArrowUpRightIcon className="h-3.5 w-3.5" />
        </a>
      </p>
    </Section>
  );
}
