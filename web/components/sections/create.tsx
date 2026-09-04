import type { ReactNode } from "react";
import { CodeBlock } from "@/components/code-block";
import { CommandList } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS, EXAMPLE } from "@/lib/links";

const commands = [
  "project init",
  "project create session-switcher --priority now",
  "project status session-switcher ready",
];

// The first lines of docs/projects/session-switcher/readme.md as it merged in
// ninjudd/trip#13.
const plan = `---
status: ready
priority: now
---

# Session Switcher

## 1. Outcome

The session list becomes the hub of trip rather than a special case of
\`enter\`. Three changes, one interaction model:

- **The detach key opens a chooser.** Pressing it inside an attached session
  (\`TRIP_DETACH_KEY\`, \`^\\\` by default) detaches the *view* and shows the
  interactive session list.`;

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

export function Create() {
  return (
    <Section
      id="create"
      title="Create a project"
      lead={
        <>
          A project is a directory under <Code>docs/projects/</Code> with a lowercase{" "}
          <Code>readme.md</Code>. The file opens with two frontmatter fields and continues as an
          ordinary Markdown plan.
        </>
      }
    >
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-16">
        <div className="min-w-0 space-y-5 text-[15px] leading-relaxed text-muted">
          <p>
            <Code>project init</Code> writes the convention file once per repository, and{" "}
            <Code>project create</Code> makes the directory and the plan. <Code>project status</Code>{" "}
            changes one frontmatter field, <Code>project done</Code> marks the project completed,
            and <Code>project list</Code> groups what exists by priority. Add <Code>--json</Code> to
            any command for scripts and agents.
          </p>
          <p>
            Because the plan is a file in the repository, it comes with every clone, along with its
            history. A status change is a one-line diff in the pull request that makes it true, so
            the plan and the code are reviewed together. Two people working on two projects edit
            two different files, so there is no shared status list to conflict on. The directory
            never moves, so links to a plan keep working. There is no server and no account.
          </p>
          <p>
            <Code>status</Code> tracks progress: <Code>draft</Code>, <Code>ready</Code>,{" "}
            <Code>in-progress</Code>, or <Code>completed</Code>. <Code>priority</Code> tracks when
            you want to work on it: <Code>now</Code>, <Code>next</Code>, or <Code>later</Code>. The
            two are independent. A draft can be the current priority while it is still being
            written, and in-progress work can drop to <Code>later</Code> without pretending it never
            started. A completed project needs no priority.
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
            <DocLink href={DOCS.convention}>Project convention</DocLink>
            <DocLink href={DOCS.cli}>CLI reference</DocLink>
          </div>
        </div>
        <div className="min-w-0 space-y-4">
          <CommandList commands={commands} />
          <CodeBlock
            lang="markdown"
            title="docs/projects/session-switcher/readme.md · ninjudd/trip#13"
            code={plan}
          />
          <p className="text-sm text-muted">
            The excerpt is the first lines of a real plan.{" "}
            <a
              href={EXAMPLE.plan}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-line-strong underline-offset-4 transition-colors hover:text-fg"
            >
              Read the whole file on GitHub.
            </a>
          </p>
        </div>
      </div>
    </Section>
  );
}
