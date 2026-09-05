import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const steps = [
  {
    title: "You push",
    body: "A new commit lands on a pull request you opened from the session.",
  },
  {
    title: "The review loop reviews it",
    body: "It checks out that exact commit locally, reads the change, and posts what it finds as one comment review.",
  },
  {
    title: "The fix loop fixes it",
    body: "Each finding is checked, fixed, committed, and pushed. The loop replies with the commit and resolves the thread.",
  },
  {
    title: "The draft becomes ready",
    body: "When a review of the current head finds nothing, the pull request is marked ready for a person to review.",
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

export function ReviewLoop() {
  return (
    <Section
      id="reviews"
      title="Automated reviews"
      lead={
        <>
          Two optional skills in the same plugin, <Code>start-review-loop</Code> and{" "}
          <Code>start-fix-loop</Code>, review pull requests in the background while you keep
          working. The plans and the CLI do not depend on them.
        </>
      }
      size="tight"
    >
      <ol className="grid gap-8 md:grid-cols-2 lg:grid-cols-4">
        {steps.map((step, index) => (
          <li key={step.title} className="border-t border-line-strong pt-5">
            <span className="font-mono text-sm text-accent">{index + 1}</span>
            <h3 className="mt-2 text-base font-semibold tracking-tight text-fg">{step.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">{step.body}</p>
          </li>
        ))}
      </ol>
      <div className="mt-10 max-w-2xl space-y-4 text-[15px] leading-relaxed text-muted">
        <p>
          Until then the pull request stays a draft, so draft means the loops still have something
          to say. A clean loop is one model&rsquo;s reading of one commit. It catches what a careful
          first pass catches, and it is not proof that the change is correct, so a human review
          still follows. The rules the loops follow on GitHub, including who may mark a draft ready
          and how findings are labeled, are in the skill documents.
        </p>
        <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
          <DocLink href={DOCS.reviewLoop}>Review loop</DocLink>
          <DocLink href={DOCS.fixLoop}>Fix loop</DocLink>
        </div>
      </div>
    </Section>
  );
}
