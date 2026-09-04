import { CodeBlock } from "@/components/code-block";
import { ArrowRightIcon } from "@/components/icons";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";

type Item = { value: string; desc: string };

const statuses: Item[] = [
  {
    value: "draft",
    desc: "The plan is still being written. It makes no readiness claim.",
  },
  {
    value: "ready",
    desc: "Complete enough to execute. Every question that blocks implementation is answered or deliberately deferred.",
  },
  {
    value: "in-progress",
    desc: "Implementation has begun. A blocked project stays here with the blocker explained in its plan.",
  },
  {
    value: "completed",
    desc: "No more work needed. The plan records whether it shipped, was abandoned, or was superseded.",
  },
];

const priorities: Item[] = [
  { value: "now", desc: "Deserves the team's current attention." },
  { value: "next", desc: "Becomes current when capacity opens." },
  { value: "later", desc: "Recorded but not scheduled." },
];

function Rail({
  label,
  sub,
  items,
  ordered = false,
  columns,
}: {
  label: string;
  sub: string;
  items: Item[];
  ordered?: boolean;
  columns: string;
}) {
  return (
    <div>
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-sm font-medium text-fg">{label}</span>
        <span className="text-sm text-faint">{sub}</span>
      </div>
      <ol className={`mt-4 grid gap-3 sm:grid-cols-2 ${columns}`}>
        {items.map((item, index) => (
          <li key={item.value} className="rounded-xl border border-line bg-surface p-4">
            <div className="flex items-center">
              <span className="rounded-md bg-accent-soft px-2 py-0.5 font-mono text-xs text-accent">
                {item.value}
              </span>
              {ordered && index < items.length - 1 ? (
                <ArrowRightIcon className="ml-auto h-4 w-4 text-faint" />
              ) : null}
            </div>
            <p className="mt-3 text-sm leading-relaxed text-muted">{item.desc}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

const frontmatter = `---
status: draft
priority: now
---`;

export function Model() {
  return (
    <Section
      id="model"
      eyebrow="The model"
      title={
        <>
          Two fields. <em className="text-accent">Two independent claims.</em>
        </>
      }
      lead={
        <>
          <Code>status</Code> says where the work is in its lifecycle. <Code>priority</Code> says
          when it should happen. Neither implies the other, so a draft can be the current focus
          and in-progress work can be set aside without losing its place.
        </>
      }
    >
      <div className="grid gap-12 lg:grid-cols-[1fr_18rem] lg:gap-16">
        <div className="space-y-10">
          <Rail
            label="status"
            sub="the lifecycle"
            items={statuses}
            ordered
            columns="lg:grid-cols-4"
          />
          <Rail label="priority" sub="the schedule" items={priorities} columns="lg:grid-cols-3" />
        </div>
        <aside className="lg:pt-9">
          <CodeBlock lang="yaml" title="a draft that is the current focus" code={frontmatter} />
          <p className="mt-4 text-sm leading-relaxed text-muted">
            Priority is required unless the status is <Code>completed</Code>. Finished work needs
            no schedule, and <Code>project check</Code> holds every plan to that rule.
          </p>
        </aside>
      </div>
    </Section>
  );
}
