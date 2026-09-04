import { CodeBlock } from "@/components/code-block";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";
import { DOCS } from "@/lib/links";
import { ArrowUpRightIcon } from "@/components/icons";

const commands: [string, string][] = [
  ["init", "Adopt the convention by creating docs/projects/README.md."],
  ["list", "Group projects by priority. Filter with --status and --priority."],
  ["show <project>", "Print a plan with its frontmatter."],
  ["search <query>", "Search names, metadata, plans, and supplemental files."],
  ["create <project>", "Create one directory and one readme.md. --parent nests it."],
  ["edit <project>", "Open the plan in $VISUAL or $EDITOR."],
  ["status <project> <status>", "Change only the status scalar."],
  ["priority <project> <priority>", "Change only the priority scalar."],
  ["done <project>", "Shorthand for status completed."],
  ["check", "Validate every plan: frontmatter, casing, links, symlinks."],
  ["config get|list|paths", "Read layered .projector.toml settings."],
];

const json = `{
  "projects": [
    {
      "name": "payments",
      "owner": null,
      "path": "docs/projects/payments/readme.md",
      "priority": "now",
      "status": "in-progress",
      "title": "Ledger-backed payments"
    }
  ],
  "schema_version": 2
}`;

const guarantees = [
  {
    title: "A schema version in every response",
    body: "JSON output carries schema_version 2, so a script can refuse a shape it does not recognize instead of guessing.",
  },
  {
    title: "Clean streams",
    body: "stdout is JSON only when you ask for it. Diagnostics go to stderr.",
  },
  {
    title: "Safe writes, no commits",
    body: "Mutations write through a temporary file, preserve unrelated frontmatter, and refuse the update if the plan changed after Projector read it. Committing is yours.",
  },
  {
    title: "Exit codes that mean something",
    body: "65 for a validation failure, 66 for a missing projects directory, 69 when edit has no terminal or editor.",
  },
];

export function Cli() {
  return (
    <Section
      id="cli"
      eyebrow="The CLI"
      title={
        <>
          One command for <em className="text-accent">the whole lifecycle.</em>
        </>
      }
      lead={
        <>
          The framework is Projector. The command it installs is <Code>project</Code>. It finds the
          Git root from wherever you are, reads the plans under <Code>docs/projects/</Code>, and
          never needs the network.
        </>
      }
    >
      <div className="grid gap-12 lg:grid-cols-[1.1fr_1fr] lg:gap-16">
        <div>
          <dl className="overflow-hidden rounded-xl border border-line bg-surface">
            {commands.map(([command, description]) => (
              <div
                key={command}
                className="grid gap-1 border-b border-line px-4 py-3 last:border-b-0 sm:grid-cols-[15rem_1fr] sm:gap-4"
              >
                <dt className="font-mono text-[13px] text-fg">
                  <span className="text-faint">project </span>
                  {command}
                </dt>
                <dd className="text-sm text-muted">{description}</dd>
              </div>
            ))}
          </dl>
          <a
            href={DOCS.cli}
            target="_blank"
            rel="noreferrer"
            className="mt-5 inline-flex items-center gap-1 text-sm font-medium text-fg transition-colors hover:text-accent"
          >
            Full CLI reference
            <ArrowUpRightIcon className="h-4 w-4" />
          </a>
        </div>
        <div>
          <CodeBlock lang="json" title="project list --priority now --json" code={json} />
          <ul className="mt-8 space-y-5">
            {guarantees.map((item) => (
              <li key={item.title} className="grid grid-cols-[0.75rem_1fr] gap-3">
                <span className="mt-2 h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                <div>
                  <h3 className="text-[15px] font-semibold text-fg">{item.title}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{item.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  );
}
