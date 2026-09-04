import { CodeBlock } from "@/components/code-block";
import { Code } from "@/components/inline-code";
import { Section } from "@/components/section";

const parent = `# every repository under ~/src
[review]
username = "review-bot"
effort = "high"
model = "sonnet"`;

const repo = `# this repository only
[review]
model = "fable"
allow_approve = true`;

const resolved = `{
  "key": "review.model",
  "schema_version": 2,
  "source": "/home/you/src/payments/.projector.toml",
  "value": "fable"
}`;

const keys: [string, string, string][] = [
  ["projects.dir", "docs/projects", "every command"],
  ["review.username", "the authenticated user", "start-review-loop"],
  ["review.allow_approve", "false", "start-review-loop"],
];

export function Config() {
  return (
    <Section
      id="config"
      eyebrow="Configuration"
      title={
        <>
          Settings that <em className="text-accent">follow the code.</em>
        </>
      }
      lead={
        <>
          Projector reads <Code>.projector.toml</Code> from your home directory down to the
          repository root, nearest last. Put a value in the file closest to the code it governs.
          Tables merge key by key, so a nearer file overrides one setting without discarding the
          rest.
        </>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <CodeBlock lang="toml" title="~/src/.projector.toml" code={parent} />
        <CodeBlock lang="toml" title="~/src/payments/.projector.toml" code={repo} />
        <CodeBlock lang="json" title="project config get review.model --json" code={resolved} />
        <figure className="overflow-hidden rounded-xl border border-line bg-surface">
          <figcaption className="border-b border-line px-4 py-2 font-mono text-xs text-faint">
            keys Projector reads today
          </figcaption>
          <table className="w-full text-left text-sm">
            <thead className="font-mono text-xs uppercase tracking-[0.12em] text-faint">
              <tr className="border-b border-line">
                <th className="px-4 py-2.5 font-medium">Key</th>
                <th className="px-4 py-2.5 font-medium">Default</th>
                <th className="px-4 py-2.5 font-medium">Read by</th>
              </tr>
            </thead>
            <tbody>
              {keys.map(([key, fallback, reader]) => (
                <tr key={key} className="border-b border-line last:border-b-0">
                  <td className="px-4 py-2.5 font-mono text-[13px] text-fg">{key}</td>
                  <td className="px-4 py-2.5 text-muted">{fallback}</td>
                  <td className="px-4 py-2.5 font-mono text-[13px] text-muted">{reader}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </figure>
      </div>

      <div className="mt-10 grid gap-6 text-[15px] leading-relaxed text-muted lg:grid-cols-2 lg:gap-16">
        <p>
          Keys are not validated. Any key a skill or a script agrees on works, so the same command
          reads settings Projector itself knows nothing about.
        </p>
        <p>
          <Code>get --json</Code> reports which file each value came from, which is the fastest
          way to learn why a setting is not what you expected. <Code>get</Code> exits{" "}
          <Code>1</Code> when a key is unset and no default is given, so a caller can branch on
          the exit status instead of parsing output.
        </p>
      </div>
    </Section>
  );
}
