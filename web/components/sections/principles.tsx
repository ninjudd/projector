import { Section } from "@/components/section";

const principles = [
  {
    title: "Git is the database.",
    body: "A clone contains every plan and its history. Projector needs no service, account, daemon, or generated index.",
  },
  {
    title: "One project, one permanent home.",
    body: "A status change edits frontmatter. It never moves a file or maintains a second representation of the same plan.",
  },
  {
    title: "Concurrent projects change different files.",
    body: "Listing and prioritization are queries, so two branches working on two projects never contend on a shared now.md, next.md, or later.md.",
  },
  {
    title: "Plans grow recursively.",
    body: "Every project is a directory. It can take on design notes, decisions, and nested projects without changing shape.",
  },
  {
    title: "Built for people and agents.",
    body: "Concise human output, stable JSON with a schema version, and skills that call the same commands you can inspect and run yourself.",
  },
  {
    title: "Policy stays local.",
    body: "Projector ships useful defaults without embedding anyone's GitHub accounts, home-directory layout, or review rules.",
  },
];

export function Principles() {
  return (
    <Section
      id="why"
      eyebrow="Why Projector"
      title={
        <>
          Plans that stay <em className="text-accent">where the code is.</em>
        </>
      }
      lead="Project trackers drift from the repository they describe. Projector keeps intent next to the code that carries it out, under version control, reviewed in the same pull request."
    >
      <ol className="grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
        {principles.map((principle, index) => (
          <li key={principle.title} className="bg-surface p-7">
            <span className="font-mono text-xs text-accent">
              {String(index + 1).padStart(2, "0")}
            </span>
            <h3 className="mt-4 text-lg font-semibold tracking-tight text-fg">
              {principle.title}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">{principle.body}</p>
          </li>
        ))}
      </ol>
    </Section>
  );
}
