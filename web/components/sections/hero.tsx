import { Command } from "@/components/command";
import { ArrowUpRightIcon } from "@/components/icons";
import { Panel, type PanelLine, type Tone } from "@/components/panel";
import { DOCS, GITHUB, INSTALL } from "@/lib/links";

const WIDTH = 74;
const HEADER = "3 pull requests · 1 review request · 1 question · 1 request";

function row(
  number: string,
  state: string,
  tone: Tone,
  title: string,
  age: string,
  draft = false,
): PanelLine {
  const left = `   ${number} `;
  const text = ` ${title}`;
  const pad = WIDTH - left.length - state.length - text.length - age.length;
  return [
    { text: left, italic: draft },
    { text: state, tone, italic: draft },
    { text: text + " ".repeat(Math.max(1, pad)) + age },
  ];
}

function item(subject: string, pr: string, age: string, tone: Tone): PanelLine {
  const text = `   - ${subject}`;
  const tail = `${pr}  ${age}`;
  const pad = WIDTH - text.length - tail.length;
  return [{ text: text + " ".repeat(Math.max(1, pad)) + tail, tone }];
}

// An example of the panel above Claude Code's prompt: three pull requests the
// session fixes, one it reviews, and the inbox below them.
const lines: PanelLine[] = [
  [
    { text: "  /projector".padEnd(WIDTH - HEADER.length), tone: "muted" },
    { text: HEADER, tone: "muted" },
  ],
  row("#341", "clean", "clean", "Retry failed webhook deliveries", "1h"),
  row("#344", "findings", "changes", "Cache the parsed config between runs", "8m"),
  row("#345", "draft", "fg", "Stream the export instead of buffering", "2m", true),
  [],
  row("#339", "reviewing", "reviewing", "ada Add a dry-run flag to the importer", "20m"),
  [],
  item("Decline the finding about the cache's lifetime?", "#344", "5m", "question"),
  item("Check the streamed export on staging", "#345", "1m", "request"),
  [{ text: "ctrl+x tab to focus".padStart(WIDTH), tone: "dim" }],
];

export function Hero() {
  return (
    <section>
      <div className="mx-auto grid max-w-6xl items-center gap-14 px-6 pb-20 pt-16 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] lg:pb-28 lg:pt-24">
        <div className="min-w-0">
          <h1 className="font-display text-6xl leading-[0.98] tracking-tight text-fg sm:text-7xl">
            Agents plan, build, and review.
            <br />
            <em className="text-accent">You make the calls.</em>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
            Projector gives Claude Code and Codex a workflow for a whole project. Agents write the
            plan into the repository, build it, review each other&rsquo;s pull requests, and fix
            what the reviews find. You review the plan, answer the questions only you can, and
            decide what merges. Every question waits for you in a panel above your prompt, and
            every pull request comes with a summary that walks you through the change.
          </p>
          <div className="mt-8 max-w-xl">
            <Command text={INSTALL.script} />
          </div>
          <p className="mt-3 font-mono text-xs text-faint">
            Python 3.11 or newer · MIT license · plugins for Claude Code and Codex
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-x-7 gap-y-3 text-sm">
            <a href="#loop" className="font-medium text-fg transition-colors hover:text-accent">
              How it works
            </a>
            <a
              href={DOCS.index}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-muted transition-colors hover:text-fg"
            >
              Read the docs
              <ArrowUpRightIcon className="h-4 w-4" />
            </a>
            <a
              href={GITHUB}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-muted transition-colors hover:text-fg"
            >
              GitHub
              <ArrowUpRightIcon className="h-4 w-4" />
            </a>
          </div>
        </div>
        <div className="min-w-0">
          <Panel caption="Claude Code · Projector's panel" lines={lines} />
          <p className="mt-3 text-sm text-muted">
            An example of the panel. Each row is a pull request and its state. Below the rows, a
            question and a request wait in your inbox while the agents keep working.{" "}
            <a
              href="#panel"
              className="text-fg underline decoration-line-strong underline-offset-4 transition-colors hover:text-accent"
            >
              Try a working one below.
            </a>
          </p>
        </div>
      </div>
    </section>
  );
}
