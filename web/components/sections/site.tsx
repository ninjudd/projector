import type { ReactNode } from "react";
import { CommandList } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS, PROJECTOR_SITE } from "@/lib/links";

const parts: { title: string; body: ReactNode }[] = [
  {
    title: "Projects",
    body: (
      <>
        The home page groups every plan by status. Each project opens with its files, nested
        projects, and the reviews that touched it.
      </>
    ),
  },
  {
    title: "Reviews",
    body: (
      <>
        Guided summaries of large pull requests, which the <Code>summarize-changes</Code> skill
        writes and publishes, one for each head the pull request reaches.
      </>
    ),
  },
  {
    title: "Docs",
    body: (
      <>
        The README and everything under <Code>docs/</Code>. Markdown renders in place, and an HTML
        page, such as an explorer or a WebAssembly playground, runs inside the site.
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

export function Site() {
  return (
    <Section
      id="site"
      title="Publish a site"
      lead={
        <>
          Every repository can serve its projects, its reviews, and its docs as a site on GitHub
          Pages, built from what is already in the repository. <Code>project init</Code> sets it up.
        </>
      }
    >
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-16">
        <ol className="min-w-0 space-y-6">
          {parts.map((part) => (
            <li key={part.title} className="border-t border-line-strong pt-5">
              <h3 className="text-base font-semibold tracking-tight text-fg">{part.title}</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-muted">{part.body}</p>
            </li>
          ))}
        </ol>
        <div className="min-w-0 space-y-5 text-[15px] leading-relaxed text-muted">
          <CommandList commands={["project init", "project site serve"]} />
          <p>
            <Code>project init</Code> turns on Pages and adds the workflow that deploys the site
            whenever the docs change or a review is published. It keeps a private repository&rsquo;s
            site private, and refuses to deploy one that would be public.{" "}
            <Code>project site serve</Code> shows the same site on your machine, with no Pages at
            all, and rebuilds it as you edit.
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2 pt-1">
            <DocLink href={PROJECTOR_SITE}>Projector&rsquo;s own site</DocLink>
            <DocLink href={DOCS.site}>Site guide</DocLink>
          </div>
        </div>
      </div>
    </Section>
  );
}
