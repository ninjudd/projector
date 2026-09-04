import { ArrowRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";

const skills = [
  {
    name: "plan-project",
    body: "Turn a requested outcome into a durable plan that another person or agent can execute without reconstructing the conversation.",
  },
  {
    name: "work-project",
    body: "Implement a project while keeping its plan and status current. The plan is the intent; the repository is the evidence.",
  },
  {
    name: "finish-project",
    body: "Verify the acceptance criteria, record whether the work shipped, was abandoned, or was superseded, and mark it completed in the same change.",
  },
  {
    name: "start-review-loop",
    body: "Review each exact pushed SHA locally and publish verified findings as labeled reviews. A pull request stays in draft until its head is clean.",
  },
  {
    name: "start-fix-loop",
    body: "Watch for review findings, verify and fix each one, then reply with the commit, push, and resolve the thread.",
  },
  {
    name: "gh-stack",
    body: "Create, push, rebase, and navigate stacks of dependent pull requests with the gh-stack extension when work crosses a reviewability boundary.",
  },
];

function Host({ name, note, invoke }: { name: string; note: string; invoke: string }) {
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface-2/60 p-6">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-lg font-semibold tracking-tight text-fg">{name}</h3>
        <a
          href="#install"
          className="inline-flex items-center gap-1 text-sm text-muted transition-colors hover:text-fg"
        >
          Install
          <ArrowRightIcon className="h-3.5 w-3.5" />
        </a>
      </div>
      <p className="mt-1 text-sm text-muted">{note}</p>
      <pre className="mt-5 overflow-x-auto rounded-lg border border-line bg-surface px-4 py-3 font-mono text-[13px] text-fg">
        {invoke}
      </pre>
    </div>
  );
}

export function Agents() {
  return (
    <Section
      id="agents"
      eyebrow="Agent workflows"
      title={
        <>
          Skills your agents <em className="text-accent">already know.</em>
        </>
      }
      lead="Projector packages one canonical skill tree for Claude Code and Codex. Skills express the workflow; the CLI supplies the mechanics. No MCP server required."
    >
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {skills.map((skill) => (
          <li key={skill.name} className="rounded-2xl border border-line bg-surface p-6">
            <h3 className="font-mono text-sm font-medium text-fg">
              <span className="text-accent">/</span>
              {skill.name}
            </h3>
            <p className="mt-3 text-sm leading-relaxed text-muted">{skill.body}</p>
          </li>
        ))}
      </ul>

      <div className="mt-16">
        <h3 className="text-xl font-semibold tracking-tight text-fg">
          Equal targets, one source tree
        </h3>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted">
          Both plugin manifests point at the same <Code>skills/</Code> directory. A host loads the
          same instructions without a generated copy or a host-specific fork, so a fix lands in
          both places at once.
        </p>
        <div className="mt-8 grid gap-6 lg:grid-cols-2">
          <Host
            name="Claude Code"
            note="Skills are namespaced by the plugin."
            invoke="/projector:plan-project plan a safer deploy workflow"
          />
          <Host
            name="Codex"
            note="Skills are invoked directly by name."
            invoke="$plan-project plan a safer deploy workflow"
          />
        </div>
      </div>
    </Section>
  );
}
