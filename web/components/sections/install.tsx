import type { ReactNode } from "react";
import { CommandList } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS, INSTALL } from "@/lib/links";

const rows: { title: string; body: ReactNode; commands: string[] }[] = [
  {
    title: "The CLI",
    body: (
      <>
        Requires Python 3.11 or newer and nothing else. <Code>pipx</Code> gives you an isolated{" "}
        <Code>project</Code> command.
      </>
    ),
    commands: [INSTALL.pipx, "project --help"],
  },
  {
    title: "Claude Code",
    body: (
      <>
        Adds the repository as a plugin marketplace and installs the plugin for your user. Skills
        are invoked as <Code>/projector:&lt;skill&gt;</Code>.
      </>
    ),
    commands: INSTALL.claude,
  },
  {
    title: "Codex",
    body: (
      <>
        The same repository, installed as a Codex plugin. Skills are invoked as{" "}
        <Code>$&lt;skill&gt;</Code>.
      </>
    ),
    commands: INSTALL.codex,
  },
];

function DocLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-0.5 text-fg transition-colors hover:text-accent"
    >
      {children}
      <ArrowUpRightIcon className="h-3.5 w-3.5" />
    </a>
  );
}

export function Install() {
  return (
    <Section
      id="install"
      title="Install"
      lead="Install the CLI first. Then add the plugin for the coding agents you use."
      size="tight"
    >
      <div className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
        {rows.map((row) => (
          <div
            key={row.title}
            className="grid gap-5 p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] lg:gap-12"
          >
            <div>
              <h3 className="text-lg font-semibold tracking-tight text-fg">{row.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{row.body}</p>
            </div>
            <CommandList commands={row.commands} />
          </div>
        ))}
      </div>
      <p className="mt-6 max-w-2xl text-sm leading-relaxed text-muted">
        Installing from a clone, and checking whether an installed command is stale, are covered in
        the <DocLink href={DOCS.plugins}>plugin guide</DocLink>. Settings, such as a different
        projects directory, live in <Code>.projector.toml</Code> files and are described in the{" "}
        <DocLink href={DOCS.cli}>CLI reference</DocLink>.
      </p>
    </Section>
  );
}
