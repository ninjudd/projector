import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const steps = [
  {
    title: "You push",
    body: "A new commit lands on one of your pull requests, whichever session opened it.",
  },
  {
    title: "A reviewing session reviews it",
    body: "The session that reviews your repository checks out that exact commit locally, reads the change, and posts what it finds as one comment review. Each pull request keeps its own reviewer, so the next review knows the last, and a large one gets a summary on the repository\u2019s site.",
  },
  {
    title: "The pushing session fixes it",
    body: "The session that pushed the pull request hears about each finding, then checks, fixes, commits, and pushes it, and a branch that conflicts with its base gets the base merged in. It replies with the commit and resolves the thread. Each pull request keeps its own fixer, so the next round knows the last.",
  },
  {
    title: "The draft becomes ready",
    body: "The reviewer verifies each finding on the new head, resolving what is fixed and reopening what is not. When nothing is left open, the pull request is marked ready for a person to review.",
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
          <Code>review-pr</Code> reviews a pull request&rsquo;s current head and each head pushed
          after it, and <Code>review-repo</Code> does the same for every pull request of yours in a
          repository. <Code>fix-pr</Code> fixes what a review found and resolves merge conflicts,
          and the session that pushed a pull request runs it again whenever a review finds
          something. One background process watches the pull requests for every session, so
          nothing needs starting while you keep working. The plans and the CLI do not depend on
          any of them.
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
          Until then the pull request stays a draft, so draft means a review still has something to
          say. A clean review is one model&rsquo;s reading of one commit. It catches what a careful
          first pass catches, and it is not proof that the change is correct, so a human review
          still follows. The rules the skills follow on GitHub, including who may mark a draft
          ready and how findings are labeled, are in the skill documents. A review runs a pull
          request&rsquo;s tests only when it trusts who could push to it, and asks you before
          running code from anyone else.
        </p>
        <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
          <DocLink href={DOCS.reviewRepo}>review-repo</DocLink>
          <DocLink href={DOCS.fixPr}>fix-pr</DocLink>
        </div>
      </div>
    </Section>
  );
}
