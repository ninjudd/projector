import type { ReactNode } from "react";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";

const tools: {
  title: string;
  command: string;
  body: ReactNode;
  href: string;
  link: string;
}[] = [
  {
    title: "Require a clean review to merge",
    command: "project init --require-review",
    body: (
      <>
        In a repository that relies on Projector alone to sign off pull requests, a ruleset makes
        GitHub block a merge until the current head has a clean review. Each review posts the{" "}
        <Code>projector/review</Code> status on the commit it read.
      </>
    ),
    href: DOCS.requireReview,
    link: "CLI reference",
  },
  {
    title: "Write prose people can read",
    command: "/projector:rewrite the install section of the README",
    body: (
      <>
        Every plan, review, summary, and pull request body follows one writing guide.{" "}
        <Code>write</Code> applies it to any prose you ask for, and <Code>rewrite</Code> revises
        text that exists and writes it back where it lives.
      </>
    ),
    href: DOCS.writing,
    link: "Writing guide",
  },
  {
    title: "Write tests that catch bugs",
    command: "/projector:write-tests audit tests/test_site.py",
    body: (
      <>
        Each test names the bug it catches. <Code>write-tests</Code> writes those tests, and deletes
        or rewrites the ones that catch nothing. The spec, build, and review skills follow the same
        testing guide.
      </>
    ),
    href: DOCS.testing,
    link: "Testing guide",
  },
  {
    title: "Split large work into a stack",
    command: "gh stack view",
    body: (
      <>
        Dependent work too large for one review becomes a stack of draft pull requests that GitHub
        records with <Code>gh stack</Code>. A fix on a lower layer is carried up through every layer
        above it, so the stack stays mergeable as a unit.
      </>
    ),
    href: DOCS.plugins,
    link: "Plugin guide",
  },
  {
    title: "Put a branch's iOS app on your phone",
    command: "project ios build 12",
    body: (
      <>
        Builds pull request 12 in a worktree of its own, installs it on your iPhone, iPad, or Mac,
        and opens it. <Code>project ios crashes</Code> and <Code>project ios logs</Code> bring the
        device&rsquo;s reports back.
      </>
    ),
    href: DOCS.ios,
    link: "project ios",
  },
  {
    title: "Find a pull request's worktree",
    command: 'cd "$(project worktree 12)"',
    body: (
      <>
        Prints the folder of the worktree that has pull request 12&rsquo;s branch checked out. An
        agent&rsquo;s worktree is often named for the agent rather than the branch, so this saves
        reading <Code>git worktree list</Code>.
      </>
    ),
    href: DOCS.worktree,
    link: "project worktree",
  },
];

export function Tools() {
  return (
    <Section
      id="tools"
      title="More for the same workflow"
      lead={
        <>
          The plugin and the <Code>project</Code> command carry smaller tools that the skills use
          and that you can run yourself.
        </>
      }
      size="tight"
    >
      <ul className="grid gap-x-10 gap-y-10 md:grid-cols-2 lg:grid-cols-3">
        {tools.map((tool) => (
          <li key={tool.title} className="flex min-w-0 flex-col border-t border-line-strong pt-5">
            <h3 className="text-base font-semibold tracking-tight text-fg">{tool.title}</h3>
            <pre className="mt-3 whitespace-pre-wrap break-words rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12.5px] leading-6 text-fg">
              {tool.command}
            </pre>
            <p className="mt-3 text-sm leading-relaxed text-muted">{tool.body}</p>
            <a
              href={tool.href}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex items-center gap-1 text-sm text-muted transition-colors hover:text-fg"
            >
              {tool.link}
              <ArrowUpRightIcon className="h-3.5 w-3.5" />
            </a>
          </li>
        ))}
      </ul>
    </Section>
  );
}
