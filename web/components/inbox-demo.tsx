"use client";

import { useEffect, useState, type KeyboardEvent, type ReactNode } from "react";

// A working drawing of Projector's panel in Claude Code, in its dark theme.
// Its rows, drawers, digits, and colors follow docs/plugins.md, so a click
// here does what a click does in the real panel, up to the prompt it fills.

const WIDTH = 92;

const color = {
  fg: "text-[#e8e6e3]",
  subtle: "text-[#6b6b6b]",
  inactive: "text-[#999999]",
  accent: "text-[#d77757]",
  red: "text-[#f2777a]",
  yellow: "text-[#e5c07b]",
  orange: "text-[#f99157]",
  green: "text-[#99c794]",
  blue: "text-[#8fc1e3]",
  fixer: "text-[#6ca0dc]",
  cyan: "text-[#66cccc]",
  question: "text-[#b1b9f9]",
  request: "text-[rgb(255,190,140)]",
} as const;

type Color = keyof typeof color;

type Kind = "question" | "request" | "notice";

type ItemState = "open" | "dismissed" | "snoozed";

type StartState = "none" | "starting" | "ready";

type Item = {
  id: string;
  kind: Kind;
  subject: string;
  pr: string;
  age: string;
  detail: string[];
  options: string[];
  start?: string;
};

const ITEMS: Item[] = [
  {
    id: "lifetime",
    kind: "question",
    subject: "Decline the finding about the cache's lifetime?",
    pr: "#344",
    age: "5m",
    detail: [
      "The review asks the cache to expire after an hour. The cache",
      "already reloads when the config file's timestamp changes, which",
      "covers the stale read the finding names.",
    ],
    options: [
      "Decline it: the file's timestamp already invalidates the cache",
      "Add a one-hour lifetime as well",
    ],
  },
  {
    id: "split",
    kind: "question",
    subject: "Split #347 into two pull requests?",
    pr: "#347",
    age: "9m",
    detail: [
      "#347 moves the settings screen into a sheet and also renames the",
      "keys it stores. The rename touches every reader of the settings,",
      "so a reviewer would hold two unrelated changes at once.",
    ],
    options: [
      "Split it: move the screen first, rename the keys after",
      "Keep it as one pull request",
    ],
  },
  {
    id: "ios",
    kind: "request",
    subject: "Try #345's build on your iPhone",
    pr: "#345",
    age: "1m",
    detail: [
      "1. Export the largest project you have.",
      "   The share sheet should appear within a second.",
      "2. Watch the progress bar. It should move steadily, not jump",
      "   from empty to full at the end.",
    ],
    options: ["Looks good", "Some feedback"],
    start: "Install #345's build of the app on your iPhone and open it",
  },
  {
    id: "scope",
    kind: "request",
    subject: "Let gh push workflow changes",
    pr: "#347",
    age: "14m",
    detail: [
      "#347 edits .github/workflows/ci.yml, and GitHub refuses that push",
      "without the workflow scope, which only you can grant.",
      "",
      "1. Run gh auth refresh -h github.com -s workflow",
      "2. Approve the scope in the browser window it opens.",
    ],
    options: ["Done"],
  },
  {
    id: "merged",
    kind: "notice",
    subject: "Merged main into #344 to clear a conflict",
    pr: "#344",
    age: "3m",
    detail: [
      "main moved the config loader into its own module. The merge",
      "keeps that move and points the cache at the new loader.",
      "Tests pass.",
    ],
    options: [],
  },
  {
    id: "clean",
    kind: "notice",
    subject: "#341 came back clean and is ready for you to merge",
    pr: "#341",
    age: "22m",
    detail: [
      "The review of the head found nothing, so #341 carries the clean",
      "label. Its two earlier findings were fixed in 9c2e41a.",
    ],
    options: [],
  },
];

type Finding = { line: string; who: string; text: string };

type Row = {
  number: string;
  state: string;
  tone: Color;
  title: string;
  age: string;
  author?: string;
  draft?: boolean;
  role: "fixer" | "reviewer";
  branch: string;
  add: number;
  del: number;
  files: number;
  checks: [string, Color];
  labels: [string, Color][];
  findings: { file: string; threads: Finding[] }[];
};

const YOURS: Row[] = [
  {
    number: "#347",
    state: "conflict",
    tone: "red",
    title: "Move settings into a sheet",
    age: "6m",
    role: "fixer",
    branch: "settings-sheet",
    add: 212,
    del: 140,
    files: 9,
    checks: ["checks failing", "red"],
    labels: [["ui", "blue"]],
    findings: [],
  },
  {
    number: "#345",
    state: "draft",
    tone: "fg",
    title: "Stream the export instead of buffering",
    age: "2m",
    draft: true,
    role: "fixer",
    branch: "stream-export",
    add: 96,
    del: 41,
    files: 4,
    checks: ["checks passing", "green"],
    labels: [],
    findings: [],
  },
  {
    number: "#344",
    state: "findings",
    tone: "yellow",
    title: "Cache the parsed config between runs",
    age: "8m",
    role: "fixer",
    branch: "config-cache",
    add: 84,
    del: 12,
    files: 5,
    checks: ["checks passing", "green"],
    labels: [["bug", "red"]],
    findings: [
      {
        file: "src/config/cache.ts",
        threads: [
          {
            line: ":48",
            who: "grace",
            text: "P2 · The cache keeps a stale config after an edit",
          },
          {
            line: ":91",
            who: "grace",
            text: "P3 · Name the unit of the lifetime setting",
          },
        ],
      },
      {
        file: "docs/config.md",
        threads: [
          {
            line: ":12",
            who: "grace",
            text: "P3 · Say where the cache file lives",
          },
        ],
      },
    ],
  },
  {
    number: "#341",
    state: "clean",
    tone: "green",
    title: "Retry failed webhook deliveries",
    age: "22m",
    role: "fixer",
    branch: "webhook-retry",
    add: 142,
    del: 18,
    files: 6,
    checks: ["checks passing", "green"],
    labels: [["clean", "green"]],
    findings: [],
  },
];

const THEIRS: Row[] = [
  {
    number: "#339",
    state: "reviewing",
    tone: "orange",
    title: "Add a dry-run flag to the importer",
    age: "20m",
    author: "ada",
    role: "reviewer",
    branch: "importer-dry-run",
    add: 61,
    del: 3,
    files: 3,
    checks: ["checks passing", "green"],
    labels: [["reviewing", "orange"]],
    findings: [],
  },
];

// The inbox lists its items newest first, whatever their kind.
const NEWEST_FIRST = [...ITEMS].sort(
  (a, b) => parseInt(a.age, 10) - parseInt(b.age, 10),
);

const SNOOZE = ["15 min", "1 hour", "4 hours", "1 day"];

type Open =
  { type: "pr"; number: string } | { type: "item"; id: string } | null;

type Press = {
  digit: string;
  label: string;
  recommended?: boolean;
  onPress: () => void;
};

function Seg({
  tone = "fg",
  className = "",
  children,
}: {
  tone?: Color;
  className?: string;
  children: ReactNode;
}) {
  return <span className={`${color[tone]} ${className}`}>{children}</span>;
}

function spaces(count: number): string {
  return " ".repeat(Math.max(1, count));
}

function Action({
  digit,
  label,
  onPress,
}: {
  digit: string;
  label: string;
  onPress: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPress}
      className="rounded-[2px] text-left hover:bg-[#e8e6e3] focus-visible:bg-[#e8e6e3] focus-visible:outline-none [&:focus-visible>span]:text-[#141414] [&:hover>span]:text-[#141414]"
    >
      <span className={color.accent}>{digit}</span>
      <span className={color.fg}>: {label}</span>
    </button>
  );
}

function quote(subject: string): string {
  return `"${subject}"`;
}

const FRESH: Record<string, ItemState> = Object.fromEntries(
  ITEMS.map((item) => [item.id, "open" as ItemState]),
);

const lineButton =
  "block w-full text-left hover:bg-white/5 focus-visible:bg-white/10 focus-visible:outline-none";

export function InboxDemo() {
  const [open, setOpen] = useState<Open>(null);
  const [states, setStates] = useState<Record<string, ItemState>>(FRESH);
  const [snoozing, setSnoozing] = useState(false);
  const [confirmDismiss, setConfirmDismiss] = useState(false);
  const [dismissedRows, setDismissedRows] = useState<string[]>([]);
  const [openFiles, setOpenFiles] = useState<string[]>(["src/config/cache.ts"]);
  const [start, setStart] = useState<StartState>("none");
  const [prompt, setPrompt] = useState("");

  // A started request reads starting until the agent has installed the
  // build, then ready.
  useEffect(() => {
    if (start !== "starting") return;
    const timer = setTimeout(() => {
      setStart("ready");
    }, 1800);
    return () => {
      clearTimeout(timer);
    };
  }, [start]);

  function close() {
    setOpen(null);
    setSnoozing(false);
    setConfirmDismiss(false);
  }

  function toggle(next: Exclude<Open, null>) {
    setSnoozing(false);
    setConfirmDismiss(false);
    const same =
      open !== null &&
      ((open.type === "pr" &&
        next.type === "pr" &&
        open.number === next.number) ||
        (open.type === "item" && next.type === "item" && open.id === next.id));
    setOpen(same ? null : next);
  }

  function setState(id: string, state: ItemState) {
    setStates((current) => ({ ...current, [id]: state }));
  }

  function reset() {
    setOpen(null);
    setStates(FRESH);
    setSnoozing(false);
    setConfirmDismiss(false);
    setDismissedRows([]);
    setOpenFiles(["src/config/cache.ts"]);
    setStart("none");
    setPrompt("");
  }

  const openItem =
    open?.type === "item"
      ? ITEMS.find((item) => item.id === open.id)
      : undefined;
  const yours = YOURS.filter((row) => !dismissedRows.includes(row.number));
  const theirs = THEIRS.filter((row) => !dismissedRows.includes(row.number));
  const openRow =
    open?.type === "pr"
      ? [...yours, ...theirs].find((row) => row.number === open.number)
      : undefined;
  const count = (kind: Kind) =>
    ITEMS.filter((item) => item.kind === kind && states[item.id] === "open")
      .length;
  const plural = (n: number, word: string) =>
    n ? `${String(n)} ${word}${n === 1 ? "" : "s"}` : "";
  const header = [
    plural(yours.length, "pull request"),
    plural(theirs.length, "review request"),
    plural(count("question"), "question"),
    plural(count("request"), "request"),
    plural(count("notice"), "notice"),
  ]
    .filter(Boolean)
    .join(" · ");

  function itemOptions(item: Item): Press[] {
    if (states[item.id] !== "open") return [];
    if (snoozing) {
      return SNOOZE.map((length, index) => ({
        digit: String(index + 1),
        label: length,
        onPress: () => {
          setState(item.id, "snoozed");
          close();
        },
      }));
    }
    const list: Press[] = [];
    let digit = 1;
    if (item.start && start === "none") {
      list.push({
        digit: String(digit++),
        label: "Start",
        onPress: () => {
          setStart("starting");
        },
      });
    }
    item.options.forEach((option, index) => {
      list.push({
        digit: String(digit++),
        label: option,
        recommended: item.kind === "question" && index === 0,
        onPress: () => {
          setPrompt(`${quote(item.subject)}\n\n${option}`);
        },
      });
    });
    return list;
  }

  function itemActions(item: Item): Press[][] {
    const state = states[item.id];
    if (snoozing) {
      return [
        [
          {
            digit: "9",
            label: "Cancel",
            onPress: () => {
              setSnoozing(false);
            },
          },
        ],
      ];
    }
    if (state === "dismissed") {
      return [
        [
          {
            digit: "8",
            label: "Restore",
            onPress: () => {
              setState(item.id, "open");
            },
          },
        ],
      ];
    }
    if (state === "snoozed") {
      return [
        [
          {
            digit: "9",
            label: "Unsnooze",
            onPress: () => {
              setState(item.id, "open");
            },
          },
        ],
      ];
    }
    return [
      [
        {
          digit: "6",
          label: "I don't understand",
          onPress: () => {
            setPrompt(`${quote(item.subject)}\n\nI don't understand `);
          },
        },
      ],
      [
        {
          digit: "7",
          label: "Discuss",
          onPress: () => {
            setPrompt(`${quote(item.subject)}\n\n`);
          },
        },
        {
          digit: "8",
          label: "Dismiss",
          onPress: () => {
            setState(item.id, "dismissed");
            close();
          },
        },
        {
          digit: "9",
          label: "Snooze",
          onPress: () => {
            setSnoozing(true);
          },
        },
      ],
    ];
  }

  function prActions(row: Row): Press[] {
    if (confirmDismiss) {
      return [
        {
          digit: "8",
          label: "Yes, dismiss it",
          onPress: () => {
            setDismissedRows((current) => [...current, row.number]);
            close();
          },
        },
        {
          digit: "9",
          label: "No, keep it",
          onPress: () => {
            setConfirmDismiss(false);
          },
        },
      ];
    }
    return [
      {
        digit: "7",
        label: "Discuss",
        onPress: () => {
          setPrompt(`${row.number} `);
        },
      },
      {
        digit: "8",
        label: "Dismiss",
        onPress: () => {
          setConfirmDismiss(true);
        },
      },
    ];
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    let presses: Press[];
    if (openItem) {
      if (event.key === "0") {
        close();
        event.preventDefault();
        return;
      }
      presses = [...itemOptions(openItem), ...itemActions(openItem).flat()];
    } else if (openRow) {
      presses = prActions(openRow);
    } else {
      return;
    }
    const match = presses.find((press) => press.digit === event.key);
    if (match) {
      match.onPress();
      event.preventDefault();
    }
  }

  function itemTone(item: Item): Color {
    if (states[item.id] !== "open") return "subtle";
    return item.kind === "notice" ? "inactive" : item.kind;
  }

  function statusOf(item: Item): string {
    return item.start && start !== "none" ? start : "";
  }

  function tail(item: Item): ReactNode {
    const status = statusOf(item);
    return (
      <>
        {status ? (
          <Seg tone={start === "ready" ? "green" : itemTone(item)}>
            {status}{" "}
          </Seg>
        ) : null}
        <Seg tone={itemTone(item)}>{`${item.pr}  ${item.age}`}</Seg>
      </>
    );
  }

  function tailLength(item: Item): number {
    const status = statusOf(item);
    return (
      (status ? status.length + 1 : 0) + item.pr.length + 2 + item.age.length
    );
  }

  function itemLine(item: Item) {
    const left = `   - ${item.subject}`;
    const struck = states[item.id] === "dismissed";
    return (
      <button
        key={item.id}
        type="button"
        onClick={() => {
          toggle({ type: "item", id: item.id });
        }}
        className={lineButton}
      >
        <Seg tone={itemTone(item)} className={struck ? "line-through" : ""}>
          {left}
        </Seg>
        {spaces(WIDTH - left.length - tailLength(item))}
        {tail(item)}
      </button>
    );
  }

  function prRow(row: Row) {
    const author = row.author ? `${row.author} ` : "";
    const left = `   ${row.number} ${row.state} ${author}${row.title}`;
    const isOpen = open?.type === "pr" && open.number === row.number;
    return (
      <div key={row.number}>
        <button
          type="button"
          onClick={() => {
            toggle({ type: "pr", number: row.number });
          }}
          className={lineButton}
          aria-expanded={isOpen}
        >
          {"   "}
          <span
            className={`${row.draft ? "italic" : ""} ${isOpen ? "bg-[#e8e6e3] text-[#141414]" : color.fg}`}
          >
            {row.number}
          </span>{" "}
          <Seg tone={row.tone} className={row.draft ? "italic" : ""}>
            {row.state}
          </Seg>{" "}
          {author ? <Seg tone="inactive">{author}</Seg> : null}
          <Seg>{row.title}</Seg>
          {spaces(WIDTH - left.length - row.age.length)}
          <Seg tone="subtle">{row.age}</Seg>
        </button>
        {isOpen ? prDrawer(row) : null}
      </div>
    );
  }

  function prDrawer(row: Row) {
    const pad = "        ";
    return (
      <div>
        <span className="block">
          {pad}
          <Seg tone={row.role === "fixer" ? "fixer" : "accent"}>
            {row.role}
          </Seg>{" "}
          · <Seg tone="cyan">{row.branch}</Seg> ·{" "}
          <Seg tone="green">+{row.add}</Seg> <Seg tone="red">−{row.del}</Seg> in{" "}
          {row.files} files · <Seg tone={row.checks[1]}>{row.checks[0]}</Seg>
          {row.labels.map(([name, tone]) => (
            <span key={name}>
              {" "}
              · <Seg tone={tone}>{name}</Seg>
            </span>
          ))}
        </span>
        <span className="block">
          {pad}
          {prActions(row).map((press, position) => (
            <span key={press.label}>
              {position > 0 ? "  " : ""}
              <Action
                digit={press.digit}
                label={press.label}
                onPress={press.onPress}
              />
            </span>
          ))}
        </span>
        <span className="block">
          {pad}
          <Seg className="underline decoration-white/30 underline-offset-2">
            open in github
          </Seg>
          {"  "}
          <Seg className="underline decoration-white/30 underline-offset-2">
            open in projector
          </Seg>
        </span>
        {row.findings.map((group) => {
          const shown = openFiles.includes(group.file);
          const n = group.threads.length;
          return (
            <div key={group.file}>
              <button
                type="button"
                onClick={() => {
                  setOpenFiles((current) =>
                    shown
                      ? current.filter((file) => file !== group.file)
                      : [...current, group.file],
                  );
                }}
                className={lineButton}
              >
                {pad}
                {shown ? "-" : "+"} {group.file}{" "}
                <Seg tone="yellow">
                  {n} finding{n === 1 ? "" : "s"}
                </Seg>
              </button>
              {shown
                ? group.threads.map((thread) => (
                    <span key={thread.line} className="block">
                      {pad}
                      {"  "}
                      {thread.line} · {thread.who} · {thread.text}
                    </span>
                  ))
                : null}
            </div>
          );
        })}
      </div>
    );
  }

  function itemDrawer(item: Item) {
    const options = itemOptions(item);
    const actions = itemActions(item);
    const state = states[item.id];
    const label =
      start === "none"
        ? "Start:"
        : start === "starting"
          ? "Starting:"
          : "Ready:";
    return (
      <div>
        <button
          type="button"
          onClick={close}
          aria-label={`Close the drawer of ${item.subject}`}
          className={lineButton}
        >
          {"   "}
          <Seg tone="accent">0</Seg>
          <Seg tone={itemTone(item)}>: {item.subject}</Seg>
          {spaces(WIDTH - 6 - item.subject.length - tailLength(item))}
          {tail(item)}
        </button>
        <span className="block"> </span>
        {item.detail.map((line, index) => (
          <span key={index} className="block">
            {"      "}
            {line || " "}
          </span>
        ))}
        {item.start ? (
          <>
            <span className="block"> </span>
            <span className="block">
              {"      "}
              <Seg tone={start === "ready" ? "green" : "fg"}>{label}</Seg>{" "}
              {item.start}
            </span>
          </>
        ) : null}
        {state === "dismissed" && item.kind === "request" ? (
          <>
            <span className="block"> </span>
            <span className="block">{"      "}Declined</span>
          </>
        ) : null}
        {options.length > 0 ? <span className="block"> </span> : null}
        {options.map((option) => (
          <span key={option.digit + option.label} className="block">
            {"      "}
            <Action
              digit={option.digit}
              label={option.label}
              onPress={option.onPress}
            />
            {option.recommended ? (
              <span className="block">
                {"      "}
                <Seg tone="inactive">(recommended)</Seg>
              </span>
            ) : null}
          </span>
        ))}
        <span className="block"> </span>
        {actions.map((line, index) => (
          <span key={index} className="block">
            {"      "}
            {line.map((press, position) => (
              <span key={press.label}>
                {position > 0 ? "  " : ""}
                <Action
                  digit={press.digit}
                  label={press.label}
                  onPress={press.onPress}
                />
              </span>
            ))}
          </span>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div
        role="group"
        aria-label="A working example of Projector's panel"
        tabIndex={0}
        onKeyDown={onKeyDown}
        className="overflow-hidden rounded-xl border border-line-strong bg-[#141414] text-[#e8e6e3] focus-visible:outline-2 focus-visible:outline-accent"
      >
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-2 font-mono text-xs text-[#8d877a]">
          <span>Claude Code · Projector&rsquo;s panel</span>
          <button
            type="button"
            onClick={reset}
            className="rounded px-2 py-0.5 transition-colors hover:bg-white/10 hover:text-[#e8e6e3]"
          >
            Reset
          </button>
        </div>
        <div className="overflow-x-auto whitespace-pre p-4 font-mono text-[12px] leading-[1.3] sm:px-5">
          <div className="w-max min-w-full">
            {openItem ? (
              itemDrawer(openItem)
            ) : (
              <>
                <span className="block">
                  <Seg tone="subtle">{"  /projector"}</Seg>
                  {spaces(WIDTH - 12 - header.length - 4)}
                  <Seg tone="subtle">{header} 12s</Seg>
                </span>
                {yours.map(prRow)}
                {theirs.length > 0 ? <span className="block"> </span> : null}
                {theirs.map(prRow)}
                <span className="block"> </span>
                {NEWEST_FIRST.map(itemLine)}
              </>
            )}
            <span className="block">
              <Seg tone="subtle">{"ctrl+x tab to focus".padStart(WIDTH)}</Seg>
            </span>
          </div>
        </div>
      </div>
      <div className="rounded-xl border border-line-strong bg-[#141414] px-4 py-3 font-mono text-[12.5px] leading-[1.35] text-[#e8e6e3] sm:px-5">
        <div className="flex gap-2">
          <span className="select-none text-[#8d877a]">&gt;</span>
          <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
            {prompt ? (
              prompt
            ) : (
              <span className="text-[#6b6b6b]">
                Pick an option or Discuss, and the panel fills the prompt for
                you to send.
              </span>
            )}
            <span className="ml-px inline-block h-4 w-2 translate-y-0.5 animate-pulse bg-[#e8e6e3]/70" />
          </span>
        </div>
      </div>
    </div>
  );
}
