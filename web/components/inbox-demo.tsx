"use client";

import { useEffect, useState, type KeyboardEvent, type ReactNode } from "react";

// A working drawing of Projector's panel in Claude Code, in its dark theme.
// Its rows, drawers, digits, and colors follow docs/plugins.md, so a click
// here does what a click does in the real panel, up to the prompt it fills.

const WIDTH = 88;

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

type Item = {
  id: Kind;
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
    id: "question",
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
    id: "request",
    kind: "request",
    subject: "Check the streamed export on your iPhone",
    pr: "#345",
    age: "1m",
    detail: [
      "1. Open the app, then export the largest project you have.",
      "   The share sheet should appear within a second.",
      "2. Watch the progress bar. It should move steadily, not jump",
      "   from empty to full at the end.",
    ],
    options: ["Looks good", "Some feedback"],
    start: "Install #345's build of the app on your iPhone and open it",
  },
  {
    id: "notice",
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
];

type Row = {
  number: string;
  state: string;
  tone: Color;
  title: string;
  age: string;
  author?: string;
  draft?: boolean;
};

const YOURS: Row[] = [
  { number: "#341", state: "clean", tone: "green", title: "Retry failed webhook deliveries", age: "1h" },
  {
    number: "#344",
    state: "findings",
    tone: "yellow",
    title: "Cache the parsed config between runs",
    age: "8m",
  },
  {
    number: "#345",
    state: "draft",
    tone: "fg",
    title: "Stream the export instead of buffering",
    age: "2m",
    draft: true,
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
  },
];

const SNOOZE = ["15 min", "1 hour", "4 hours", "1 day"];

type Open = { type: "pr"; number: string } | { type: "item"; id: Kind } | null;

type StartState = "none" | "starting" | "ready";

function Seg({ tone = "fg", className = "", children }: { tone?: Color; className?: string; children: ReactNode }) {
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
      className="rounded-[2px] text-left hover:bg-[#e8e6e3] hover:text-[#141414] focus-visible:bg-[#e8e6e3] focus-visible:text-[#141414] focus-visible:outline-none [&:hover>span]:text-[#141414] [&:focus-visible>span]:text-[#141414]"
    >
      <span className={color.accent}>{digit}</span>
      <span className={color.fg}>: {label}</span>
    </button>
  );
}

function quote(subject: string): string {
  return `"${subject}"`;
}

export function InboxDemo() {
  const [open, setOpen] = useState<Open>({ type: "item", id: "question" });
  const [states, setStates] = useState<Record<Kind, ItemState>>({
    question: "open",
    request: "open",
    notice: "open",
  });
  const [snoozing, setSnoozing] = useState(false);
  const [confirmDismiss, setConfirmDismiss] = useState(false);
  const [dismissedRows, setDismissedRows] = useState<string[]>([]);
  const [files, setFiles] = useState<Record<string, boolean>>({ cache: true, docs: false });
  const [start, setStart] = useState<StartState>("none");
  const [prompt, setPrompt] = useState("");
  // A started request reads starting until the agent is done, then ready.
  useEffect(() => {
    if (start !== "starting") return;
    const timer = setTimeout(() => {
      setStart("ready");
    }, 1600);
    return () => {
      clearTimeout(timer);
    };
  }, [start]);

  function close() {
    setOpen(null);
    setSnoozing(false);
    setConfirmDismiss(false);
  }

  function toggle(next: Open) {
    setSnoozing(false);
    setConfirmDismiss(false);
    if (open && next && open.type === next.type) {
      if (
        (open.type === "pr" && next.type === "pr" && open.number === next.number) ||
        (open.type === "item" && next.type === "item" && open.id === next.id)
      ) {
        setOpen(null);
        return;
      }
    }
    setOpen(next);
  }

  function setState(id: Kind, state: ItemState) {
    setStates((current) => ({ ...current, [id]: state }));
  }

  function reset() {
    setOpen({ type: "item", id: "question" });
    setStates({ question: "open", request: "open", notice: "open" });
    setSnoozing(false);
    setConfirmDismiss(false);
    setDismissedRows([]);
    setFiles({ cache: true, docs: false });
    setStart("none");
    setPrompt("");
  }

  const openItem = open?.type === "item" ? ITEMS.find((item) => item.id === open.id) : undefined;
  const counts = {
    question: states.question === "open" ? 1 : 0,
    request: states.request === "open" ? 1 : 0,
    notice: states.notice === "open" ? 1 : 0,
  };
  const yours = YOURS.filter((row) => !dismissedRows.includes(row.number));
  const header = [
    `${String(yours.length)} pull request${yours.length === 1 ? "" : "s"}`,
    "1 review request",
    counts.question ? "1 question" : "",
    counts.request ? "1 request" : "",
    counts.notice ? "1 notice" : "",
  ]
    .filter(Boolean)
    .join(" · ");

  // The options an item's drawer offers, and what each does when pressed.
  function itemOptions(
    item: Item,
  ): { digit: string; label: string; recommended?: boolean; onPress: () => void }[] {
    const state = states[item.id];
    if (state !== "open") return [];
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
    const list: { digit: string; label: string; recommended?: boolean; onPress: () => void }[] =
      [];
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

  function itemActions(item: Item): { digit: string; label: string; onPress: () => void }[][] {
    const state = states[item.id];
    if (snoozing) {
      return [[{ digit: "9", label: "Cancel", onPress: () => { setSnoozing(false); } }]];
    }
    if (state === "dismissed") {
      return [[{ digit: "8", label: "Restore", onPress: () => { setState(item.id, "open"); } }]];
    }
    if (state === "snoozed") {
      return [[{ digit: "9", label: "Unsnooze", onPress: () => { setState(item.id, "open"); } }]];
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

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!openItem || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "0") {
      close();
      event.preventDefault();
      return;
    }
    const actions = [...itemOptions(openItem), ...itemActions(openItem).flat()];
    const match = actions.find((action) => action.digit === event.key);
    if (match) {
      match.onPress();
      event.preventDefault();
    }
  }

  function itemTone(item: Item): Color {
    if (states[item.id] !== "open") return "subtle";
    return item.kind === "notice" ? "inactive" : item.kind;
  }

  function itemTail(item: Item): string {
    const status = item.start && start !== "none" ? `${start}  ` : "";
    return `${status}${item.pr}  ${item.age}`;
  }

  function itemLine(item: Item, bullet: string): ReactNode {
    const left = `   ${bullet} ${item.subject}`;
    const tail = itemTail(item);
    const struck = states[item.id] === "dismissed";
    const startTone: Color = start === "ready" ? "green" : itemTone(item);
    const status = item.start && start !== "none" ? start : "";
    return (
      <button
        type="button"
        onClick={() => {
          toggle({ type: "item", id: item.id });
        }}
        className="block w-full text-left hover:bg-white/5 focus-visible:bg-white/10 focus-visible:outline-none"
      >
        <Seg tone={itemTone(item)} className={struck ? "line-through" : ""}>
          {left}
        </Seg>
        {spaces(WIDTH - left.length - tail.length)}
        {status ? <Seg tone={startTone}>{status}  </Seg> : null}
        <Seg tone={itemTone(item)}>{`${item.pr}  ${item.age}`}</Seg>
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
          className="block w-full text-left hover:bg-white/5 focus-visible:bg-white/10 focus-visible:outline-none"
          aria-expanded={isOpen}
        >
          {"   "}
          <Seg className={`${row.draft ? "italic" : ""} ${isOpen ? "bg-[#e8e6e3] !text-[#141414]" : ""}`}>
            {row.number}
          </Seg>{" "}
          <Seg tone={row.tone} className={row.draft ? "italic" : ""}>
            {row.state}
          </Seg>{" "}
          {author ? <Seg tone="inactive">{author}</Seg> : null}
          <Seg>{row.title}</Seg>
          {spaces(WIDTH - left.length - row.age.length)}
          <Seg tone="subtle">{row.age}</Seg>
        </button>
        {isOpen && row.number === "#344" ? prDrawer(row) : null}
        {isOpen && row.number !== "#344" ? simpleDrawer(row) : null}
      </div>
    );
  }

  function drawerActions(row: Row) {
    const pad = "        ";
    if (confirmDismiss) {
      return (
        <span className="block">
          {pad}
          <Action
            digit="8"
            label="Yes, dismiss it"
            onPress={() => {
              setDismissedRows((current) => [...current, row.number]);
              close();
            }}
          />
          {"  "}
          <Action
            digit="9"
            label="No, keep it"
            onPress={() => {
              setConfirmDismiss(false);
            }}
          />
        </span>
      );
    }
    return (
      <span className="block">
        {pad}
        <Action
          digit="7"
          label="Discuss"
          onPress={() => {
            setPrompt(`${row.number} `);
          }}
        />
        {"  "}
        <Action
          digit="8"
          label="Dismiss"
          onPress={() => {
            setConfirmDismiss(true);
          }}
        />
      </span>
    );
  }

  function links() {
    return (
      <span className="block">
        {"        "}
        <Seg className="underline decoration-white/30 underline-offset-2">open in github</Seg>
        {"  "}
        <Seg className="underline decoration-white/30 underline-offset-2">open in projector</Seg>
      </span>
    );
  }

  function prDrawer(row: Row) {
    return (
      <div>
        <span className="block">
          {"        "}
          <Seg tone="fixer">fixer</Seg> · <Seg tone="cyan">config-cache</Seg> ·{" "}
          <Seg tone="green">+84</Seg> <Seg tone="red">−12</Seg> in 5 files ·{" "}
          <Seg tone="green">checks passing</Seg> · <Seg tone="red">bug</Seg>
        </span>
        {drawerActions(row)}
        {links()}
        <button
          type="button"
          onClick={() => {
            setFiles((current) => ({ ...current, cache: !current.cache }));
          }}
          className="block w-full text-left hover:bg-white/5"
        >
          {"        "}
          {files.cache ? "-" : "+"} src/config/cache.ts <Seg tone="yellow">2 findings</Seg>
        </button>
        {files.cache ? (
          <>
            <span className="block">
              {"          "}:48 · grace · P2 · The cache keeps a stale config after an edit
            </span>
            <span className="block">
              {"          "}:91 · grace · P3 · Name the unit of the lifetime setting
            </span>
          </>
        ) : null}
        <button
          type="button"
          onClick={() => {
            setFiles((current) => ({ ...current, docs: !current.docs }));
          }}
          className="block w-full text-left hover:bg-white/5"
        >
          {"        "}
          {files.docs ? "-" : "+"} docs/config.md <Seg tone="yellow">1 finding</Seg>
        </button>
        {files.docs ? (
          <span className="block">
            {"          "}:12 · grace · P3 · Say where the cache file lives
          </span>
        ) : null}
      </div>
    );
  }

  function simpleDrawer(row: Row) {
    const role = row.author ? "reviewer" : "fixer";
    return (
      <div>
        <span className="block">
          {"        "}
          <Seg tone={row.author ? "accent" : "fixer"}>{role}</Seg> ·{" "}
          <Seg tone="cyan">{row.author ? "importer-dry-run" : "webhook-retry"}</Seg> ·{" "}
          <Seg tone="green">+{row.author ? "61" : "142"}</Seg>{" "}
          <Seg tone="red">−{row.author ? "3" : "18"}</Seg> in {row.author ? "3" : "6"} files ·{" "}
          <Seg tone="green">checks passing</Seg>
        </span>
        {drawerActions(row)}
        {links()}
      </div>
    );
  }

  function itemDrawer(item: Item) {
    const options = itemOptions(item);
    const actions = itemActions(item);
    const state = states[item.id];
    return (
      <div>
        <span className="block">
          {"   "}
          <button
            type="button"
            onClick={close}
            className="hover:bg-[#e8e6e3] hover:text-[#141414] focus-visible:outline-none"
          >
            <Seg tone="accent">0</Seg>
          </button>
          <Seg tone={itemTone(item)}>: {item.subject}</Seg>
          {spaces(WIDTH - 5 - item.subject.length - itemTail(item).length)}
          {item.start && start !== "none" ? (
            <Seg tone={start === "ready" ? "green" : itemTone(item)}>{start}  </Seg>
          ) : null}
          <Seg tone={itemTone(item)}>{`${item.pr}  ${item.age}`}</Seg>
        </span>
        <span className="block"> </span>
        {item.detail.map((line, index) => (
          <span key={index} className="block">
            {"      "}
            {line}
          </span>
        ))}
        {item.start ? (
          <>
            <span className="block"> </span>
            <span className="block">
              {"      "}
              <Seg tone={start === "ready" ? "green" : "fg"}>
                {start === "none" ? "Start:" : start === "starting" ? "Starting:" : "Ready:"}
              </Seg>{" "}
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
            <Action digit={option.digit} label={option.label} onPress={option.onPress} />
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
            {line.map((action, position) => (
              <span key={action.label}>
                {position > 0 ? "  " : ""}
                <Action digit={action.digit} label={action.label} onPress={action.onPress} />
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
        <div className="overflow-x-auto p-4 font-mono text-[12px] leading-6 whitespace-pre sm:px-5">
          <div className="w-max min-w-full">
            {openItem ? (
              itemDrawer(openItem)
            ) : (
              <>
                <span className="block">
                  <Seg tone="subtle">{"  /projector"}</Seg>
                  {spaces(WIDTH - 12 - header.length - 5)}
                  <Seg tone="subtle">{header}  12s</Seg>
                </span>
                {yours.map(prRow)}
                <span className="block"> </span>
                {THEIRS.map(prRow)}
                <span className="block"> </span>
                {ITEMS.map((item) => (
                  <div key={item.id}>{itemLine(item, "-")}</div>
                ))}
              </>
            )}
            <span className="block">
              <Seg tone="subtle">{"ctrl+x tab to focus".padStart(WIDTH)}</Seg>
            </span>
          </div>
        </div>
      </div>
      <div className="rounded-xl border border-line-strong bg-[#141414] px-4 py-3 font-mono text-[12.5px] leading-6 text-[#e8e6e3] sm:px-5">
        <div className="flex gap-2">
          <span className="select-none text-[#8d877a]">&gt;</span>
          <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
            {prompt ? (
              prompt
            ) : (
              <span className="text-[#6b6b6b]">
                Pick an option or Discuss, and the panel fills the prompt for you to send.
              </span>
            )}
            <span className="ml-px inline-block h-4 w-2 translate-y-0.5 animate-pulse bg-[#e8e6e3]/70" />
          </span>
        </div>
      </div>
    </div>
  );
}
