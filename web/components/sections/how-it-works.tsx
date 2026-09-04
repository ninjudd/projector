import { CodeBlock } from "@/components/code-block";
import { CommandList } from "@/components/command";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";

const steps = [
  {
    title: "Adopt the convention",
    body: (
      <>
        Creates <Code>docs/projects/README.md</Code>, the one file that explains the format to
        everyone who opens the repository. It never replaces an existing convention file.
      </>
    ),
    commands: ["project init"],
  },
  {
    title: "Create a project",
    body: (
      <>
        One directory, one lowercase <Code>readme.md</Code>. The path is the name, so{" "}
        <Code>payments/invoices</Code> is a nested project with its own status and its own plan.
      </>
    ),
    commands: [
      "project create payments --status ready --priority next",
      "project create invoices --parent payments",
    ],
  },
  {
    title: "Move it through the lifecycle",
    body: (
      <>
        Status and priority change in frontmatter, in the same pull request that makes the claim
        true. The directory never moves, so a citation like{" "}
        <Code>payments/readme.md § 4</Code> stays valid for the life of the project.
      </>
    ),
    commands: ["project status payments in-progress", "project done payments"],
  },
];

const tree = `docs/projects/
├── README.md
└── payments/
    ├── readme.md          status + priority frontmatter
    ├── design.md
    └── invoices/
        └── readme.md      nested project: payments/invoices`;

const plan = `---
status: in-progress
priority: now
---

# Ledger-backed payments

## 1. Outcome

Every invoice is issued from the ledger service, and the
legacy invoice table is read-only.

## 2. Decisions

Backfill runs once, behind a flag, before the cutover.`;

export function HowItWorks() {
  return (
    <Section
      id="how-it-works"
      eyebrow="How it works"
      title={
        <>
          Adopt it in <em className="text-accent">three commands.</em>
        </>
      }
      lead="Run init from anywhere inside a Git repository. From then on, every project is a directory you can browse on GitHub and a record the CLI can query."
    >
      <div className="grid gap-12 lg:grid-cols-[1fr_1.05fr] lg:gap-16">
        <ol className="space-y-12">
          {steps.map((step, index) => (
            <li key={step.title} className="grid grid-cols-[2rem_1fr] gap-4">
              <span className="mt-1 font-mono text-xs text-accent">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div>
                <h3 className="text-xl font-semibold tracking-tight text-fg">{step.title}</h3>
                <p className="mt-2 text-[15px] leading-relaxed text-muted">{step.body}</p>
                <div className="mt-4">
                  <CommandList commands={step.commands} />
                </div>
              </div>
            </li>
          ))}
        </ol>
        <div className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          <figure className="overflow-hidden rounded-xl border border-line bg-surface">
            <figcaption className="border-b border-line px-4 py-2 font-mono text-xs text-faint">
              tree docs/projects
            </figcaption>
            <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-6 text-fg">
              {tree}
            </pre>
          </figure>
          <CodeBlock lang="yaml" title="docs/projects/payments/readme.md" code={plan} />
        </div>
      </div>
    </Section>
  );
}
