import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";

const steps = [
  {
    title: "Push",
    body: "A new SHA lands on a pull request the authenticated account owns.",
  },
  {
    title: "Review the exact head",
    body: "The review loop checks out that SHA locally and inspects it. A review starts only when inspection actually begins.",
  },
  {
    title: "Publish verified findings",
    body: "Findings go up as one labeled COMMENT review on that head. The pull request stays in draft.",
  },
  {
    title: "Fix each finding",
    body: "The fix loop verifies a finding, commits the fix, replies with the commit, pushes, and resolves the thread.",
  },
  {
    title: "Clean head, ready for humans",
    body: "When a review of the head finds nothing, the pull request is marked ready. That transition is the sign-off.",
  },
];

export function ReviewLoop() {
  return (
    <Section
      id="review"
      eyebrow="Review loops"
      title={
        <>
          Draft means <em className="text-accent">changes are needed.</em>
        </>
      }
      lead="Two background loops carry a pull request from first push to a clean head while you keep working. The draft state is the verdict, so the moment a pull request turns ready, it has actually been reviewed."
    >
      <ol className="grid gap-8 md:grid-cols-2 lg:grid-cols-5">
        {steps.map((step, index) => (
          <li key={step.title} className="border-t border-line-strong pt-5">
            <span className="font-mono text-xs text-accent">
              {String(index + 1).padStart(2, "0")}
            </span>
            <h3 className="mt-3 text-base font-semibold tracking-tight text-fg">{step.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">{step.body}</p>
          </li>
        ))}
      </ol>

      <div className="mt-16 grid gap-10 lg:grid-cols-[1fr_1fr] lg:gap-16">
        <blockquote className="border-l-2 border-accent pl-6">
          <p className="font-display text-3xl leading-tight tracking-tight text-fg sm:text-4xl">
            Never mark your own draft ready yourself; a clean review is what clears it.
          </p>
          <footer className="mt-4 font-mono text-xs text-faint">
            AGENTS.md, the Projector contributor instructions
          </footer>
        </blockquote>
        <div className="space-y-5 text-[15px] leading-relaxed text-muted">
          <p>
            The loops review as the operator: the account whose pull requests are watched and
            whose branches carry the fixes. Identity follows the authenticated token, so a loop
            never scopes itself to pull requests it cannot push to.
          </p>
          <p>
            A real <Code>APPROVE</Code> is posted only when <Code>review.allow_approve</Code> is
            set and the author is someone else. On your own pull request the loop stays with
            labeled comments, which GitHub would insist on anyway.
          </p>
          <p>
            Every finding is verified against the checked-out head before it is published, and
            every fix is committed, pushed, and resolved in that order, so the thread on GitHub
            always points at the commit that answered it.
          </p>
        </div>
      </div>
    </Section>
  );
}
