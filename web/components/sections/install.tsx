import type { ReactNode } from "react";
import { CommandList } from "@/components/command";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { INSTALL } from "@/lib/links";

const rows: { title: string; body: ReactNode; commands: string[] }[] = [
  {
    title: "The CLI",
    body: (
      <>
        Requires Python 3.11 or newer and has no runtime dependencies. <Code>pipx</Code> gives
        you an isolated <Code>project</Code> executable.
      </>
    ),
    commands: [INSTALL.pipx, "project --help"],
  },
  {
    title: "Claude Code",
    body: (
      <>
        Adds the repository as a marketplace and installs the plugin at user scope. Invoke a
        skill as <Code>/projector:&lt;skill&gt;</Code>.
      </>
    ),
    commands: INSTALL.claude,
  },
  {
    title: "Codex",
    body: (
      <>
        The same repository and the same skill tree. Invoke a skill as{" "}
        <Code>$&lt;skill&gt;</Code>.
      </>
    ),
    commands: INSTALL.codex,
  },
  {
    title: "From a clone",
    body: (
      <>
        <Code>./install.sh all</Code> installs the CLI and both plugins from the checkout.{" "}
        <Code>./install.sh status</Code> compares the installed command against the files in the
        checkout, so it reports a stale command even when nobody bumped a version.
      </>
    ),
    commands: INSTALL.checkout,
  },
];

export function Install() {
  return (
    <Section
      id="install"
      eyebrow="Install"
      title={
        <>
          Two commands <em className="text-accent">per host.</em>
        </>
      }
      lead="Install the CLI once, then add the plugin to whichever coding agents you use. The core workflows call the local command and need nothing else."
    >
      <div className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface-2/60">
        {rows.map((row) => (
          <div
            key={row.title}
            className="grid gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] lg:gap-12 lg:p-8"
          >
            <div>
              <h3 className="text-lg font-semibold tracking-tight text-fg">{row.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{row.body}</p>
            </div>
            <CommandList commands={row.commands} />
          </div>
        ))}
      </div>
    </Section>
  );
}
