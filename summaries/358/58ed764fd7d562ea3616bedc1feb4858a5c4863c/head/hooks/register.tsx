import { atom, read, update } from 'claude-code'
import type { EngineInterface, FsEntry, Register, RenderChildren } from 'claude-code'

import type {
  Checks,
  Item,
  LoopKind,
  Notice,
  PullRequest,
  Question,
  Row,
  SeenLoop,
  Sending,
  Snapshot,
  Thread,
} from './types'
import { buildQuery, buildThreadsQuery, parseResponse, parseThreads, type Fetched } from './github'
import { findWatchers, keyOf, parseTracked, type Env, type TrackedPr } from './parse'
import {
  ASK_DESCRIPTION,
  ASK_SCHEMA,
  askArguments,
  forMainLoop,
  isShownAt,
  isSnoozedAt,
  letterAt,
  liveAgent,
  needsDelivery,
  proposalOf,
  quoteOf,
  readPrompt,
  relayNote,
  replyNote,
  RESTORE_DESCRIPTION,
  RESTORE_SCHEMA,
  restoreArguments,
  REVISE_DESCRIPTION,
  REVISE_SCHEMA,
  reviseArguments,
  signatureOf,
  span,
  storeOf,
  TELL_DESCRIPTION,
  TELL_SCHEMA,
  tellArguments,
  typedLetter,
  WITHDRAW_DESCRIPTION,
  WITHDRAW_SCHEMA,
  withdrawArguments,
  wokeBetween,
  type Proposal,
} from './inbox'

type Engine = EngineInterface
type Ran = { exitCode: number; stdout: string; stderr: string }

const TICK_MS = 5_000
const POLL_MS = 30_000
const EMPTY: Snapshot = { rows: [], viewer: null, fetchedAt: null, error: null }
const ASK_TOOL = 'mcp__projector__ask'
const TELL_TOOL = 'mcp__projector__tell'
const WITHDRAW_TOOL = 'mcp__projector__withdraw'
const REVISE_TOOL = 'mcp__projector__revise'
const RESTORE_TOOL = 'mcp__projector__restore'
const TAGLINE = 'Our minds are projectors, casting ideas onto the world.'

// Watching first: a pull request both loops track is listed once, where its
// watch loop puts it.
const KINDS: readonly LoopKind[] = ['watching', 'reviewing']

const CHECK_COLORS: Record<Checks, 'success' | 'error' | 'warning'> = {
  passing: 'success',
  failing: 'error',
  pending: 'warning',
}

/**
 * The theme color of an open question's line: the one Claude Code gives its
 * own permission prompts, since an agent's question usually asks leave to do
 * something.
 */
const QUESTION_COLOR = 'permission'

/**
 * The color of an open notice's line: the theme's mid gray, which stays
 * apart from the `subtle` of a resolved or snoozed item. A plain `gray` would
 * draw in the default color.
 */
const NOTICE_COLOR = 'inactive'

/** The kinds of item, in the order the header counts them. */
const ITEM_KINDS: readonly Item['kind'][] = ['question', 'notice']

const KIND_COLORS: Record<Item['kind'], string> = { question: QUESTION_COLOR, notice: NOTICE_COLOR }

/**
 * The color of a pull request's branch on its drawer's facts line: the
 * terminal palette's own cyan, slot 6, so it follows the person's palette as
 * a named color would. Claude Code draws a plain name such as `cyan` in the
 * default color and refuses `ansi:cyan` on a plugin's Text, but takes
 * `ansi256()`.
 */
const BRANCH_COLOR = 'ansi256(6)'

/**
 * The color of what a loop does with a pull request, on its drawer's facts
 * line: watching in the terminal palette's blue, slot 4, in the same form as
 * `BRANCH_COLOR`, and reviewing in the theme's `claude` orange.
 */
const LOOP_COLORS: Record<LoopKind, string> = { watching: 'ansi256(4)', reviewing: 'claude' }

/** The key that closes an open item's drawer, from its line at the drawer's top. */
const CLOSE_HOTKEY = '0'

/**
 * How far an open item's drawer is indented: the width of the `0: ` the
 * engine draws before the item's line, so the drawer's lines start under the
 * item's text.
 */
const DRAWER_INDENT = `${CLOSE_HOTKEY}: `.length

/**
 * In a terminal the panel is inset `PANEL_INSET` columns in the terminal's
 * own background, then padded `PANEL_PADDING` columns at each side in its
 * own, where its header and key hint start. The lines between them, the pull
 * requests, the items and their drawers, sit `LINE_PADDING` columns further
 * in at each side.
 */
const PANEL_INSET = 1
const PANEL_PADDING = 1
const LINE_PADDING = 1

/**
 * The panel's last line names its keys, at its right, so it ends where the
 * ages do: the chord that takes the keyboard into the panel, and once the
 * panel has it, the keys that work there. The arrows stay out, because which
 * of them moves between lines depends on whether the panel scrolls.
 */
const FOCUS_HINT = 'ctrl+x tab to focus'
const FOCUSED_HINT = 'esc to return · enter to open'

/** What the panel's header starts with, at its left: the command that shows and hides the panel. */
const HEADER_NAME = '/projector'

/**
 * The panel's background by Claude Code theme: a step darker than the
 * terminal's own, so the panel reads as a sunken surface. Any other theme,
 * an ANSI one included, takes `OTHER_BACKGROUND`.
 */
const THEME_BACKGROUNDS: Record<string, string> = {
  dark: 'rgb(20,20,20)',
  'dark-daltonized': 'rgb(20,20,20)',
  light: 'rgb(240,240,240)',
  'light-daltonized': 'rgb(240,240,240)',
}
const OTHER_BACKGROUND = 'inverseText'

/** The setting that colors the panel, and its value that leaves the color to the theme. */
const BACKGROUND_KEY = 'panel.background'
const AUTO = 'auto'

/**
 * The names `panel.background` takes besides `auto` and a literal color, and
 * what each draws. Claude Code draws any theme key by name, but the panel
 * refuses the others by choice: most are text colors that look wrong as a
 * background, and a misspelled name, which is no key, draws nothing at all.
 * `subtle` is refused too, as the color of the panel's own muted text, which
 * would vanish on it. `black` and `white` are literal colors: `inverseText`
 * flips between the two with the theme, and `ansi256(0)` is the terminal
 * palette's black, often the terminal's own background, which would make the
 * panel vanish.
 */
const BACKGROUND_ALIASES = new Map([
  ['sidebar', 'composerSidebarBackground'],
  ['black', 'rgb(0,0,0)'],
  ['white', 'rgb(255,255,255)'],
])

/**
 * The literal colors `panel.background` takes, written as Claude Code reads
 * them: `#rgb` or `#rrggbb`, and `rgb(r,g,b)` or `ansi256(n)`, with a space
 * allowed after `(`, each comma, and before `)`. Each number is at most 255.
 */
const HEX_COLOR = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i
const NUMBERED_COLORS = [/^rgb\( ?(\d{1,3}), ?(\d{1,3}), ?(\d{1,3}) ?\)$/, /^ansi256\( ?(\d{1,3}) ?\)$/]

/** What the panel draws for a `panel.background` value other than `auto`, or undefined when it takes none. */
function backgroundFor(value: string): string | undefined {
  const alias = BACKGROUND_ALIASES.get(value)
  if (alias !== undefined) return alias
  if (HEX_COLOR.test(value)) return value
  const numbers = NUMBERED_COLORS.map(pattern => pattern.exec(value)).find(match => match !== null)
  return numbers?.slice(1).every(number => Number(number) <= 255) === true ? value : undefined
}

/** The lengths a drawer offers to snooze an item for, within the CLI's cap of a day. */
const SNOOZES = [
  { label: '15 min', minutes: 15 },
  { label: '1 hour', minutes: 60 },
  { label: '4 hours', minutes: 240 },
  { label: '1 day', minutes: 1440 },
] as const

const seen = atom({ plugin: 'projector', key: 'seenLoops' } as const, [])
const snapshot = atom({ plugin: 'projector', key: 'panelSnapshot' } as const, EMPTY)
const now = atom({ plugin: 'projector', key: 'panelNow' } as const, 0)
const isShown = atom({ plugin: 'projector', key: 'isPanelShown' } as const, false)
const drawer = atom({ plugin: 'projector', key: 'panelDrawer' } as const, null)
const asked = atom({ plugin: 'projector', key: 'panelAsked' } as const, null)
const drawerThreads = atom({ plugin: 'projector', key: 'panelThreads' } as const, null)
const items = atom({ plugin: 'projector', key: 'panelInbox' } as const, [])
const collapsed = atom({ plugin: 'projector', key: 'panelCollapsed' } as const, [])
const sending = atom({ plugin: 'projector', key: 'panelSending' } as const, {})
const themeBackground = atom({ plugin: 'projector', key: 'panelThemeBackground' } as const, OTHER_BACKGROUND)
const background = atom({ plugin: 'projector', key: 'panelBackground' } as const, AUTO)
const isFocused = atom({ plugin: 'projector', key: 'isPanelFocused' } as const, false)
/**
 * The deliveries the release before the panel's rename recorded, under the
 * key it gave them. A submit it made can still wait behind a running turn
 * when the session updates, so the panel takes them over rather than letting
 * their claims lapse and delivering those answers a second time.
 */
const oldSending = atom({ plugin: 'projector', key: 'bandSending' } as const, {})

// The session keeps its state across a reload or an update of the plugin,
// so a value an earlier version wrote can have another shape.
const isKind = (kind: unknown): kind is LoopKind => KINDS.some(one => one === kind)
const current = (value: Snapshot): Snapshot =>
  Array.isArray(value.rows) ? { ...value, viewer: value.viewer ?? null } : EMPTY

const messageOf = (error: unknown) => (error instanceof Error ? error.message : String(error))

let envLoad: Promise<Env> | undefined
let inFlight: Promise<void> | undefined
let isQueued = false
let attemptedAt = 0
const unreadable = new Set<string>()

/** The store's directory as the panel last read it, so it reads again only after a change. */
let listed: string | undefined
let isChecking = false
/** The answers this copy of the module is claiming or delivering, by reference. */
const delivering = new Set<string>()
/** The items and pull requests the panel is dismissing or snoozing, so a second press does nothing. */
const acting = new Set<string>()
/** When the last tick ran, so an item whose snooze ends between two ticks wakes once. */
let tickedAt: number | undefined
/**
 * The answers questions' drawers put in the prompt, oldest first, so a sent
 * quote answers the question you chose when another shares its text. A
 * revision marks its question's proposals stale, so an answer chosen from the
 * old text answers nothing. A proposal lasts until a prompt you send quotes
 * its question or holds the quote it was chosen under.
 */
let proposed: Proposal[] = []
/**
 * Where the panel's focus ring last landed, as `ui.focus` reported it: the
 * panel's site, and the key of the panel's element under the ring, absent when
 * the ring is on none of them. The engine says nothing when the panel hands
 * the keyboard back, so the ring can be stale, and the engine refuses a move
 * once the panel no longer holds the keyboard.
 */
let ring: { requestId: string; key: string | undefined } | undefined

/**
 * Records what a question's drawer put in the prompt: an option, or an empty
 * answer for discuss. It takes the place of the question's last fresh pick,
 * and of any stale pick that put the same text in the prompt, since a sent
 * prompt cannot tell the two apart.
 */
function propose(question: Question, answer: string): void {
  const quote = quoteOf(question)
  const isKept = (one: Proposal) =>
    one.isStale === true ? one.quote !== quote || one.answer !== answer : one.ref !== question.ref
  proposed = [...proposed.filter(isKept), { ref: question.ref, quote, answer }]
}

/** The few variables the panel's paths are written with, read once per load. */
function envOf($: Engine): Promise<Env> {
  envLoad ??= (async () => {
    const values: Record<string, string | undefined> = {
      HOME: await $.env.get('HOME'),
      TMPDIR: await $.env.get('TMPDIR'),
      XDG_CONFIG_HOME: await $.env.get('XDG_CONFIG_HOME'),
      XDG_DATA_HOME: await $.env.get('XDG_DATA_HOME'),
      XDG_STATE_HOME: await $.env.get('XDG_STATE_HOME'),
      CLAUDE_CONFIG_DIR: await $.env.get('CLAUDE_CONFIG_DIR'),
    }
    return (name: string) => (values[name] === '' ? undefined : values[name])
  })()
  return envLoad
}

async function canonical($: Engine, path: string): Promise<string | undefined> {
  try {
    const stat = await $.fs.stat(path, { resolve: true })
    return stat.realPath ?? path
  } catch {
    return undefined
  }
}

/** Runs the `project` CLI; rejects when it cannot start or is still running at the 15-second timeout. */
const project = ($: Engine, args: readonly string[]): Promise<Ran> =>
  $.process.run(['project', ...args], { timeoutMs: 15_000 })

/** The CLI's error, as its last line of standard error says it. */
const errorOf = (ran: Ran) =>
  (ran.stderr.trim().split('\n').pop() ?? '').replace(/^project: /, '') || `project exited ${ran.exitCode}`

/**
 * Records a loop, answering whether the session had not seen it before. An
 * entry an earlier version kept with no kind gives way to the watcher's.
 */
async function remember($: Engine, loop: SeenLoop): Promise<boolean> {
  let isNew = false
  await update($, seen, list => {
    isNew = !list.some(one => one.tracked === loop.tracked && isKind(one.kind))
    return isNew ? [...list.filter(one => one.tracked !== loop.tracked), loop] : list
  })
  return isNew
}

/**
 * Records the tracked file of each watcher a command starts, and shows the
 * panel when one is new. A loop restarts its watcher with the same command,
 * as a Monitor does each time it expires, so a known one shows nothing and
 * a path it cannot read is said once.
 */
async function adopt($: Engine, command: string): Promise<void> {
  if (!command.includes('watch-')) return
  const env = await envOf($)
  let isAdded = false
  for (const watcher of findWatchers(command, env)) {
    if (watcher.tracked === undefined) {
      if (!unreadable.has(watcher.written)) {
        unreadable.add(watcher.written)
        $.ui.toast(`projector: cannot tell which file ${watcher.written} names, so the panel does not list that loop`)
      }
      continue
    }
    const tracked = (await canonical($, watcher.tracked)) ?? watcher.tracked
    isAdded = (await remember($, { kind: watcher.kind, tracked })) || isAdded
  }
  if (isAdded) show($)
}

async function fetchPrs($: Engine, prs: readonly TrackedPr[]): Promise<Fetched> {
  if (prs.length === 0) return { prs: new Map(), viewer: null, error: null }
  const query = buildQuery(prs)
  try {
    const result = await $.process.run(['gh', 'api', 'graphql', '-f', `query=${query.text}`], {
      timeoutMs: 30_000,
    })
    return parseResponse(query, result)
  } catch (error) {
    return { prs: new Map(), viewer: null, error: messageOf(error) }
  }
}

/** One pull request's unresolved threads, or null when GitHub cannot say. */
async function fetchThreads($: Engine, pr: TrackedPr): Promise<Thread[] | null> {
  const query = buildThreadsQuery(pr)
  try {
    const result = await $.process.run(['gh', 'api', 'graphql', '-f', `query=${query.text}`], {
      timeoutMs: 30_000,
    })
    return parseThreads(query, result)
  } catch {
    return null
  }
}

/**
 * Reads the threads of the pull request whose drawer is open, for the drawer
 * to list: when the drawer opens, and on each poll while it stays open. Each
 * read costs a point of the account's GraphQL quota, so the panel reads only
 * the open drawer's threads, and none when the poll counted none. When GitHub
 * cannot say, the drawer keeps what it listed.
 */
async function loadThreads($: Engine): Promise<void> {
  const opened = await read($, drawer)
  const row = current(await read($, snapshot)).rows.find(one => keyOf(one.pr) === opened)
  if (opened === null || row === undefined) return
  const found = row.pr.openThreads === 0 ? [] : await fetchThreads($, row.pr)
  // Another drawer may have opened while GitHub answered.
  if (found === null || (await read($, drawer)) !== opened) return
  await update($, drawerThreads, () => ({ drawer: opened, threads: found }))
}

/**
 * The panel's background for a Claude Code theme: by the theme's name, or
 * for a custom theme, `custom:<slug>`, by the theme its file builds on,
 * `dark` unless its `base` says otherwise. A custom theme whose file cannot
 * be read takes `OTHER_BACKGROUND`.
 */
async function backgroundOf($: Engine, theme: unknown): Promise<string> {
  if (typeof theme !== 'string') return OTHER_BACKGROUND
  if (!theme.startsWith('custom:')) return THEME_BACKGROUNDS[theme] ?? OTHER_BACKGROUND
  const env = await envOf($)
  const home = env('HOME')
  const config = env('CLAUDE_CONFIG_DIR') ?? (home === undefined ? undefined : `${home}/.claude`)
  if (config === undefined) return OTHER_BACKGROUND
  try {
    const path = `${config}/themes/${theme.slice('custom:'.length)}.json`
    const base = (JSON.parse(await $.fs.read(path)) as { base?: unknown }).base ?? 'dark'
    return typeof base === 'string' ? (THEME_BACKGROUNDS[base] ?? OTHER_BACKGROUND) : OTHER_BACKGROUND
  } catch {
    return OTHER_BACKGROUND
  }
}

/** Colors the panel for a theme ahead of the render, which then waits on no read. */
async function paint($: Engine, theme: unknown): Promise<void> {
  const color = await backgroundOf($, theme)
  await update($, themeBackground, () => color)
}

/** The `panel.background` values the panel said it does not take, so it says so once for each. */
const unpaintable = new Set<string>()

/**
 * Reads `panel.background` through the CLI, which merges the `.projector.toml`
 * files. The panel alone decides which values it takes, so the CLI stores any
 * string. A value it does not take leaves the color to the theme, and the
 * panel says so once. A read that fails keeps the value the panel last read,
 * and the next reload reads it again.
 */
async function readBackground($: Engine): Promise<void> {
  try {
    const ran = await project($, ['config', 'get', BACKGROUND_KEY, '--default', AUTO, '--json'])
    if (ran.exitCode !== 0) return
    const value = (JSON.parse(ran.stdout) as { value?: unknown }).value ?? AUTO
    const color = value === AUTO ? AUTO : typeof value === 'string' ? backgroundFor(value) : undefined
    const written = JSON.stringify(value)
    if (color === undefined && !unpaintable.has(written)) {
      unpaintable.add(written)
      void $.ui.toast(
        `projector: ${BACKGROUND_KEY} is ${written}, which is not rgb(r,g,b), #rrggbb, ansi256(n), sidebar, black or white, so the panel takes its color from the theme`,
      )
    }
    await update($, background, () => color ?? AUTO)
  } catch {
    // The next reload reads it again.
  }
}

/** The session's own loops, the watchers it started, each with the pull requests its tracked file lists. */
async function trackedSets($: Engine): Promise<(SeenLoop & { entries: TrackedPr[] })[]> {
  const loops = (await read($, seen)).filter(loop => isKind(loop.kind))
  // A tracked file that is gone belongs to a loop that ended.
  return Promise.all(
    loops.map(async loop => ({
      ...loop,
      entries: await $.fs.read(loop.tracked).then(parseTracked, () => [] as TrackedPr[]),
    })),
  )
}

async function load($: Engine): Promise<void> {
  const sets = await trackedSets($)

  const kindsOf = new Map<string, LoopKind[]>()
  const tracked = new Map<string, TrackedPr>()
  for (const kind of KINDS) {
    for (const entry of sets.filter(set => set.kind === kind).flatMap(set => set.entries)) {
      const key = keyOf(entry)
      tracked.set(key, tracked.get(key) ?? entry)
      const kinds = kindsOf.get(key) ?? []
      if (!kinds.includes(kind)) kindsOf.set(key, [...kinds, kind])
    }
  }

  // Each reload reads the panel's setting too, so an edit to it shows without a restart.
  const [fetched] = await Promise.all([fetchPrs($, [...tracked.values()]), readBackground($)])

  // When GitHub cannot be reached, keep drawing what it said last time.
  const last = current(await read($, snapshot))
  const previous = new Map(last.rows.map(row => [keyOf(row.pr), row.pr]))
  const isFetched = fetched.error === null

  const rows: Row[] = [...kindsOf].flatMap(([key, kinds]) => {
    const pr = fetched.prs.get(key) ?? (isFetched ? undefined : previous.get(key))
    return pr !== undefined && pr.state === 'OPEN' ? [{ pr, kinds }] : []
  })

  const time = await $.clock.now()
  await update($, snapshot, () => ({
    rows,
    viewer: fetched.viewer ?? last.viewer,
    fetchedAt: isFetched ? time : last.fetchedAt,
    error: fetched.error,
  }))
  await update($, now, () => time)
  await closeLeftDrawer($, time)
  await loadThreads($)
}

/**
 * Reloads the loops and their pull requests, only while the panel is shown
 * unless forced. A request that arrives mid-load runs once more after it,
 * so a change it was told about is never missed.
 */
function refresh($: Engine, isForced = false): Promise<void> {
  if (inFlight !== undefined) {
    isQueued = true
    return inFlight
  }
  inFlight = (async () => {
    try {
      if (isForced || (await read($, isShown))) {
        attemptedAt = await $.clock.now()
        await load($)
      }
    } catch (error) {
      const message = messageOf(error)
      await update($, snapshot, last => ({ ...current(last), error: message })).catch(() => undefined)
    }
  })().finally(() => {
    inFlight = undefined
    if (isQueued) {
      isQueued = false
      void refresh($)
    }
  })
  return inFlight
}

/** Puts an item the CLI just changed in the panel's list, in place of what it showed. */
async function replace($: Engine, item: Item): Promise<void> {
  await update($, items, list => list.map(one => (one.ref === item.ref && one.session === item.session ? item : one)))
  await forgetAsked($, [item])
}

/**
 * Forgets what an item's drawer was asking once the question no longer fits
 * the item, wherever that happened: how long to snooze, once the item is
 * resolved or snoozed. That is the only question an item's drawer asks; only
 * a pull request's drawer asks before it dismisses. The drawer then opens on
 * its actions when the item comes back.
 */
async function forgetAsked($: Engine, changed: readonly Item[]): Promise<void> {
  const was = await read($, asked)
  const item = changed.find(one => `item:${one.ref}` === was?.drawer)
  if (was === null || item === undefined) return
  const isMoot =
    item.answer !== undefined || (was.about === 'snooze' && isSnoozedAt(item, await $.clock.now()))
  if (!isMoot) return
  await update($, asked, now => (now?.drawer === was.drawer && now.about === was.about ? null : now))
}

/** Closes a drawer when it is the one open, and with it anything the drawer was asking. */
async function closeDrawer($: Engine, key: string): Promise<void> {
  await update($, drawer, was => (was === key ? null : was))
  await update($, asked, was => (was?.drawer === key ? null : was))
}

/**
 * Closes the open drawer once its line has left the panel, so it does not
 * open again by itself when the line comes back: a pull request a loop
 * tracks again, or an item restored or woken from its snooze.
 */
async function closeLeftDrawer($: Engine, time: number): Promise<void> {
  const opened = await read($, drawer)
  if (opened === null) return
  const isListed = opened.startsWith('item:')
    ? (await read($, items)).some(item => `item:${item.ref}` === opened && isShownAt(item, time))
    : current(await read($, snapshot)).rows.some(row => keyOf(row.pr) === opened)
  if (!isListed) await closeDrawer($, opened)
}

/**
 * An item as the CLI prints it, without the session the panel adds. Each kind
 * is its own `Omit`, because one over the union would merge their `kind`s.
 */
type Printed = Omit<Question, 'session'> | Omit<Notice, 'session'>

/** The item a CLI command printed with `--json`, in the session it belongs to. */
const itemOf = (stdout: string, session: string): Item => ({
  ...(JSON.parse(stdout) as { item: Printed }).item,
  session,
})

/**
 * Takes the panel's list from the store, and shows the panel for an item that
 * is open and not snoozed when the panel last held it otherwise: a new item,
 * or one restored or unsnoozed since.
 */
async function setItems($: Engine, list: Item[]): Promise<void> {
  const time = await $.clock.now()
  let isNew = false
  await update($, items, before => {
    const isAwake = (item: Item) => item.answer === undefined && !isSnoozedAt(item, time)
    const awake = new Set(before.filter(isAwake).map(one => one.ref))
    isNew = list.some(one => isAwake(one) && !awake.has(one.ref))
    return list
  })
  await forgetAsked($, list)
  if (isNew && !(await read($, isShown))) show($)
}

/**
 * Reads the session's inbox when the store's directory changed since the
 * last read that succeeded. Listing a directory starts no process, so an idle
 * session, panel shown or hidden, starts none. The signature is recorded only
 * once the read succeeds, so a read that fails runs again on the next tick.
 */
async function readStore($: Engine, session: string): Promise<void> {
  const store = storeOf(await envOf($), session)
  if (store === undefined) return
  const entries = await $.fs.list(store).catch(() => [] as FsEntry[])
  const files = signatureOf(entries, new Set(Object.keys(await read($, sending))))
  const signature = `${session}\n${files}`
  if (signature === listed) return
  if (files === '') {
    await setItems($, [])
  } else {
    const ran = await project($, ['inbox', 'list', '--all', '--json', '--session', session])
    if (ran.exitCode !== 0) return
    const { items: list } = JSON.parse(ran.stdout) as { items: Item[] }
    await setItems($, list)
  }
  listed = signature
}

/** Remembers an answer the panel is delivering, in the session's state so a reloaded module finds it. */
const track = ($: Engine, entry: Sending) => update($, sending, all => ({ ...all, [entry.ref]: entry }))

/**
 * Ends a delivery: marks the answer delivered, or reopens its question and
 * says why. Whichever of the submit's own answer and the prompt hook comes
 * first takes the delivery out of the state, and the other finds it gone.
 */
async function settle($: Engine, ref: string, outcome: 'delivered' | 'reopen', reason?: string): Promise<void> {
  let entry: Sending | undefined
  await update($, sending, all => {
    entry = all[ref]
    return Object.fromEntries(Object.entries(all).filter(([key]) => key !== ref))
  })
  if (entry === undefined) return
  const { session, token, text } = entry
  const ran = await project($, ['inbox', outcome, ref, '--claim', token, '--session', session, '--json']).catch(
    () => undefined,
  )
  if (ran?.exitCode === 0) await replace($, itemOf(ran.stdout, session))
  if (outcome === 'reopen') {
    // You never see an item's number, so the toast names the question by its
    // subject. A restore's line is not sent again, and its question stays open.
    const question = (await read($, items)).find(one => one.ref === ref && one.session === session)
    const quoted = question === undefined ? undefined : quoteOf(question)
    const why = reason ? ` (${reason})` : ''
    void $.ui.toast(
      text.startsWith('RESTORED ')
        ? `projector: word that ${quoted ?? 'a question'} is open again did not reach its agent${why}`
        : `projector: ${quoted === undefined ? 'an answer' : `the answer to ${quoted}`} did not reach its agent${why}; answer it again`,
    )
  }
}

/**
 * Claims a recorded answer and delivers it: to the subagent that asked while
 * it can still read a message, otherwise to the main loop as a prompt of its
 * own, which waits for the session to be idle.
 */
async function deliver($: Engine, question: Question): Promise<void> {
  const { ref, session } = question
  delivering.add(ref)
  try {
    const ran = await project($, ['inbox', 'claim', ref, '--session', session, '--json'])
    // Another deliverer holds it, or it was delivered since the panel read the store.
    if (ran.exitCode !== 0) return
    const claimed = JSON.parse(ran.stdout) as { token: string; text: string }
    await replace($, itemOf(ran.stdout, session))

    const agents = question.agentId === undefined ? [] : await $.agent.list().catch(() => [])
    const agentId = liveAgent(question, agents)
    if (agentId !== undefined) {
      await track($, { ref, session, token: claimed.token, route: 'send', text: claimed.text })
      const sent = await $.session.send({ to: { agentId }, text: claimed.text }).catch(() => undefined)
      if (sent?.isDelivered === true) return await settle($, ref, 'delivered')
    }

    const text = forMainLoop(claimed.text, question, agents)
    await track($, { ref, session, token: claimed.token, route: 'submit', text })
    const submitted = await $.prompt.submit({ text }).catch((error: unknown) => ({ drop: messageOf(error) }))
    await settle($, ref, submitted.drop === undefined ? 'delivered' : 'reopen', submitted.drop)
  } catch (error) {
    await settle($, ref, 'reopen', messageOf(error))
  } finally {
    delivering.delete(ref)
  }
}

/** The panel's takeover of `oldSending`, which runs once per load of the module. */
let adoption: Promise<void> | undefined

/** Moves the deliveries under `oldSending` into `sending`, once per load, and again after a failure. */
function adoptOldSending($: Engine): Promise<void> {
  adoption ??= (async () => {
    const old = await read($, oldSending)
    if (Object.keys(old).length === 0) return
    await update($, sending, all => ({ ...old, ...all }))
    await update($, oldSending, () => ({}))
  })().catch(() => {
    adoption = undefined
  })
  return adoption
}

/**
 * Keeps each delivery's claim live while it waits, as a submit behind a long
 * turn does. A submit that an earlier copy of the module made still waits in
 * the session's queue, so its claim is kept until the prompt hook sees it
 * arrive. A send settles at once, so one an earlier copy left is let go, and
 * its claim lapses for the panel to deliver again.
 */
async function renewClaims($: Engine): Promise<void> {
  await adoptOldSending($)
  for (const entry of Object.values(await read($, sending))) {
    if (entry.route === 'send' && !delivering.has(entry.ref)) {
      await update($, sending, all => Object.fromEntries(Object.entries(all).filter(([key]) => key !== entry.ref)))
      continue
    }
    await project($, ['inbox', 'claim', entry.ref, '--renew', entry.token, '--session', entry.session]).catch(
      () => undefined,
    )
  }
}

/**
 * Reads the store when it changed, renews the panel's claims, and delivers
 * each answer nobody is delivering. A store read that fails, as one still
 * running at the CLI's timeout does, skips nothing after it, so a waiting
 * answer's claim never lapses because of it.
 */
async function checkInbox($: Engine): Promise<void> {
  if (isChecking) return
  isChecking = true
  try {
    const session = await $.session.id()
    await readStore($, session).catch(() => undefined)
    await renewClaims($)
    const time = await $.clock.now()
    const pending = await read($, sending)
    for (const item of await read($, items)) {
      if (item.session !== session || delivering.has(item.ref) || item.ref in pending) continue
      if (needsDelivery(item, time)) void deliver($, item)
    }
  } catch {
    // The next tick tries again.
  } finally {
    isChecking = false
  }
}

/** Checks the inbox now rather than at the next tick. */
const checkSoon = ($: Engine) => $.clock.after(1, () => void checkInbox($))

/**
 * Dismisses, snoozes for `minutes`, or restores an item from the panel; a
 * second press finds it changed and does nothing. Dismissing or snoozing
 * closes the item's drawer, and the item stays listed, muted, for 30 seconds,
 * so its line opens the drawer again to restore it. Restoring keeps the
 * drawer open on the item, open again.
 */
async function act(
  $: Engine,
  item: Item,
  command: 'dismiss' | 'snooze' | 'restore',
  minutes?: number,
): Promise<void> {
  const { ref, session } = item
  if (acting.has(ref)) return
  acting.add(ref)
  try {
    const length = minutes === undefined ? [] : ['--for', String(minutes)]
    const ran = await project($, ['inbox', command, ref, ...length, '--session', session, '--json'])
    if (ran.exitCode === 0) {
      await replace($, itemOf(ran.stdout, session))
      if (command !== 'restore') await closeDrawer($, `item:${ref}`)
    } else if (ran.exitCode === 65) {
      // Resolved elsewhere since the panel read the store: read it again.
      listed = undefined
    } else {
      void $.ui.toast(`projector: ${errorOf(ran)}`)
    }
    checkSoon($)
  } catch (error) {
    void $.ui.toast(`projector: ${messageOf(error)}`)
  } finally {
    acting.delete(ref)
  }
}

/**
 * Dismisses a pull request from the session's loops: `project loop drop`
 * keeps it out of each tracked file that lists it, and the panel reloads
 * without it. A failure says why, and the line stays.
 */
async function dismissPr($: Engine, pr: PullRequest): Promise<void> {
  const key = keyOf(pr)
  const named = `${pr.repo}#${pr.number}`
  if (acting.has(key)) return
  acting.add(key)
  const stopped: LoopKind[] = []
  try {
    for (const loop of await trackedSets($)) {
      if (!loop.entries.some(entry => keyOf(entry) === key)) continue
      const ran = await project($, ['loop', 'drop', named, '--tracked', loop.tracked])
      if (ran.exitCode !== 0) {
        void $.ui.toast(`projector: ${errorOf(ran)}`)
        return
      }
      stopped.push(loop.kind)
    }
    await closeDrawer($, key)
  } catch (error) {
    void $.ui.toast(`projector: ${messageOf(error)}`)
  } finally {
    acting.delete(key)
    const kinds = KINDS.filter(kind => stopped.includes(kind))
    if (kinds.length > 0) void $.ui.toast(`projector: stopped ${kinds.join(' and ')} ${named}`)
    await refresh($)
  }
}

/**
 * Records an answer a sent prompt gave and marks it delivered, because the
 * prompt itself carries it to the main loop. The panel's own deliverer leaves
 * the question alone meanwhile, so the agent hears the answer once.
 */
async function answerFromPrompt($: Engine, question: Question, letter: string): Promise<void> {
  const { ref, session } = question
  delivering.add(ref)
  try {
    const answered = await project($, ['inbox', 'answer', `${ref}.${letter}`, '--session', session, '--json'])
    if (answered.exitCode !== 0) return
    const claimed = await project($, ['inbox', 'claim', ref, '--session', session, '--json'])
    if (claimed.exitCode !== 0) return
    const { token } = JSON.parse(claimed.stdout) as { token: string }
    const ran = await project($, ['inbox', 'delivered', ref, '--claim', token, '--session', session, '--json'])
    if (ran.exitCode === 0) await replace($, itemOf(ran.stdout, session))
  } catch {
    // An answer recorded but not marked delivered is the panel's to deliver on a later tick.
  } finally {
    delivering.delete(ref)
  }
}

/**
 * Reads the inbox whether or not the panel is shown, closes the drawer of an
 * item that left the panel, and brings a hidden panel back for an item whose
 * snooze ended since the last tick. A shown panel moves
 * its clock on, which returns that item to the list, and polls GitHub once
 * the last load is old enough.
 */
async function tick($: Engine): Promise<void> {
  // Read before the store, so a snooze that ends while the store is read
  // falls in the next tick's window.
  const time = await $.clock.now()
  await checkInbox($)
  await closeLeftDrawer($, time).catch(() => undefined)
  const since = tickedAt ?? time
  tickedAt = time
  if (!(await read($, isShown))) {
    if (wokeBetween(await read($, items), since, time).length > 0) show($)
    return
  }
  await update($, now, () => time)
  if (time - attemptedAt >= POLL_MS) await refresh($)
}

/**
 * Shows the panel and loads it, from a timer so no hook waits on GitHub. The
 * panel's clock moves on before it shows, so an item whose snooze ended while
 * it was hidden is drawn at once rather than after GitHub answers.
 */
function show($: Engine): void {
  $.clock.after(1, () => {
    void $.clock
      .now()
      .then(time => update($, now, () => time))
      .then(() => update($, isShown, () => true))
      .then(() => refresh($, true))
  })
}

/**
 * Puts the panel's key hint back on the chord into the panel once the prompt
 * has the keyboard again. Only a change of hint redraws the panel.
 */
async function unfocus($: Engine): Promise<void> {
  if (!(await read($, isFocused))) return
  await update($, isFocused, () => false)
  $.ui.invalidate('ui.render')
}

/**
 * Moves the panel's focus ring onto one of its elements, which the engine
 * waits for when the panel has yet to draw it, and writes why the ring stayed
 * to the debug log when the engine refuses the move.
 */
async function focusOn($: Engine, requestId: string, key: string): Promise<void> {
  const moved = await $.ui.focus({ requestId, key }).catch((error: unknown) => ({ deny: messageOf(error) }))
  if (moved.deny !== undefined) $.ui.log(`projector: the focus stayed off ${key}: ${moved.deny}`, { to: 'debug' })
}

/** Refreshes soon, from a timer so the hook that noticed a change returns at once. */
const soon = ($: Engine) => $.clock.after(1, () => void refresh($))

/**
 * Names a pull request for the prompt: `#12` when the panel shows one
 * repository, `owner/repo#12` otherwise, so the reference is never ambiguous.
 */
function referencer(prs: readonly PullRequest[]): (pr: PullRequest) => string {
  const isOneRepo = new Set(prs.map(pr => pr.repo)).size <= 1
  return pr => (isOneRepo ? `#${pr.number}` : `${pr.repo}#${pr.number}`)
}

type Note = { text: string; color: 'error' | 'warning' | 'success' }

/** What a row says after the title: what holds the pull request back, or its approval. */
function notesOf(pr: PullRequest): Note[] {
  const notes: Note[] = []
  if (pr.isConflicting) notes.push({ text: 'conflict', color: 'error' })
  if (pr.reviewDecision === 'CHANGES_REQUESTED') notes.push({ text: 'changes requested', color: 'error' })
  if (pr.reviewDecision === 'APPROVED') notes.push({ text: 'approved', color: 'success' })
  // The open threads' count comes last, beside the age.
  if (pr.openThreads > 0) notes.push({ text: `${pr.openThreads}*`, color: 'warning' })
  return notes
}

/** How much of a thread's first line its drawer line shows. */
const EXCERPT_LENGTH = 60

/**
 * A thread's line in its pull request's drawer: where it is, who opened it,
 * how it starts, and whether a later push left it outdated.
 */
function threadLabel(thread: Thread): string {
  const place = thread.line === null ? thread.path : `${thread.path}:${thread.line}`
  const excerpt =
    thread.excerpt.length > EXCERPT_LENGTH ? `${thread.excerpt.slice(0, EXCERPT_LENGTH - 1).trimEnd()}…` : thread.excerpt
  return [place, thread.author, excerpt, thread.isOutdated ? 'outdated' : null]
    .filter(piece => piece !== null && piece !== '')
    .join(' · ')
}

/** Whether the account the panel's `gh` is signed in as wrote a pull request. */
const isOwn = (pr: PullRequest, viewer: string | null) => viewer !== null && pr.author === viewer

/**
 * The panel's pull requests in the order it lists them: yours first, then
 * everyone else's, each author's together and the authors by login. Within
 * each, they keep the order they came in.
 */
function byAuthor(rows: readonly Row[], viewer: string | null): Row[] {
  const loginOf = (row: Row) => (row.pr.author ?? '').toLowerCase()
  const others = rows.filter(row => !isOwn(row.pr, viewer))
  return [
    ...rows.filter(row => isOwn(row.pr, viewer)),
    ...others.sort((one, other) => loginOf(one).localeCompare(loginOf(other))),
  ]
}

const secondsSince = (time: number, then: number) => Math.max(0, Math.round((time - then) / 1000))

/** `1 question` or `3 questions`, or nothing for none. */
const countOf = (count: number, noun: string) =>
  count === 0 ? undefined : `${count} ${noun}${count === 1 ? '' : 's'}`

/** Labels a pull request `#12` when the panel shows one repository, else by its repository's name. */
function labeler(prs: readonly PullRequest[]): (pr: PullRequest) => string {
  const repos = [...new Set(prs.map(pr => pr.repo))]
  if (repos.length <= 1) return pr => `#${pr.number}`
  const names = repos.map(repo => repo.split('/')[1])
  const isNameEnough = new Set(names).size === names.length
  return pr => `${isNameEnough ? pr.repo.split('/')[1] : pr.repo}#${pr.number}`
}

/**
 * Runs the `project inbox` command one of the tools makes: what it printed,
 * or why the tool call is refused. `instead` says where the agent turns when
 * the command does not finish.
 */
async function runTool(
  $: Engine,
  args: string[] | { error: string },
  instead: string,
): Promise<{ stdout: string } | { deny: string }> {
  if (!Array.isArray(args)) return { deny: args.error }
  let ran: Ran
  try {
    ran = await project($, args)
  } catch (error) {
    return { deny: `project did not finish (${messageOf(error)}); ${instead}` }
  }
  // 65 refuses the call, 66 finds no such item, and 75 finds the store busy;
  // any other failure is a CLI too old to know the command.
  if (ran.exitCode === 65 || ran.exitCode === 66 || ran.exitCode === 75) return { deny: errorOf(ran) }
  if (ran.exitCode !== 0) return { deny: `${errorOf(ran)}; run project upgrade` }
  checkSoon($)
  return { stdout: ran.stdout }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'projector',
      description: 'Show or hide Projector’s panel of your loops’ pull requests and your inbox',
    })
    // A session whose engine cannot serve the tools keeps the panel.
    for (const tool of [
      { name: 'ask', description: ASK_DESCRIPTION, inputSchema: ASK_SCHEMA },
      { name: 'tell', description: TELL_DESCRIPTION, inputSchema: TELL_SCHEMA },
      { name: 'withdraw', description: WITHDRAW_DESCRIPTION, inputSchema: WITHDRAW_SCHEMA },
      { name: 'revise', description: REVISE_DESCRIPTION, inputSchema: REVISE_SCHEMA },
      { name: 'restore', description: RESTORE_DESCRIPTION, inputSchema: RESTORE_SCHEMA },
    ]) {
      await $.tool.register({ ...tool, isDeferred: false }).catch(() => undefined)
    }
    // The panel takes its color from the theme, and from its setting once a
    // timer has read it, so the start waits on no process.
    await $.config
      .list()
      .then(rows => paint($, rows.find(row => row.key === 'theme')?.value))
      .catch(() => undefined)
    $.clock.after(1, () => void readBackground($))
    $.clock.every(TICK_MS, () => void tick($))
    soon($)
    checkSoon($)
    return next(e)
  })

  // A theme picked in `/theme` recolors the panel at once.
  on('config.set', { key: 'theme' }, async ($, e, next) => {
    const set = await next(e)
    if (set.deny === undefined) await paint($, set.value)
    return set
  }).catch(($, e, next) => next(e))

  // The tools run the CLI, so what they post, withdraw, or revise and what
  // the shell does land in the same store.
  on('tool.call', { tool: ASK_TOOL }, async ($, e) => {
    const posted = await runTool($, askArguments(e, await $.session.id(), e.agentId), 'ask in the conversation instead')
    if ('deny' in posted) return posted
    const ref = posted.stdout.trim()
    void $.ui.notify(String(e.question), { title: 'Projector question' }).catch(() => undefined)
    return {
      result: `Asked ${ref}. Keep working on anything the answer does not block; it arrives as a message that starts ANSWER ${ref} or DISMISSED ${ref}. Projector’s panel never shows this number, so name the question to the person by what it asks.`,
    }
  }).catch(() => ({ deny: 'Projector could not record the question; ask it in the conversation instead' }))

  on('tool.call', { tool: TELL_TOOL }, async ($, e) => {
    const args = tellArguments(e, await $.session.id(), e.agentId)
    const posted = await runTool($, args, 'tell the person in the conversation instead')
    if ('deny' in posted) return posted
    const ref = posted.stdout.trim()
    void $.ui.notify(String(e.notice), { title: 'Projector notice' }).catch(() => undefined)
    return {
      result: `Posted ${ref}. It waits in Projector’s panel until the person dismisses it; nothing comes back to you. Projector’s panel never shows this number, so name the notice to the person by what it tells.`,
    }
  }).catch(() => ({ deny: 'Projector could not record the notice; tell the person in the conversation instead' }))

  // The panel takes the withdrawn item from the CLI at once, so it leaves the
  // panel before the next read of the store.
  on('tool.call', { tool: WITHDRAW_TOOL }, async ($, e) => {
    const session = await $.session.id()
    const instead = 'tell the person in the conversation that it no longer needs them'
    const ran = await runTool($, withdrawArguments(e, session), instead)
    if ('deny' in ran) return ran
    const item = itemOf(ran.stdout, session)
    await replace($, item)
    await closeDrawer($, `item:${item.ref}`)
    return { result: `Withdrew ${item.ref}. It has left Projector’s panel, and nothing comes back to you.` }
  }).catch(() => ({
    deny: 'Projector could not withdraw the item; tell the person in the conversation that it no longer needs them',
  }))

  // The panel takes the revised question from the CLI at once, so it redraws
  // before the next read of the store. An answer a drawer put in the prompt
  // was chosen from the old text, so it turns stale: sent, it answers no
  // question, this one or another that shares the old text.
  on('tool.call', { tool: REVISE_TOOL }, async ($, e) => {
    const session = await $.session.id()
    const ran = await runTool($, reviseArguments(e, session), 'ask the revised question in the conversation instead')
    if ('deny' in ran) return ran
    const item = itemOf(ran.stdout, session)
    await replace($, item)
    proposed = proposed.map(proposal => (proposal.ref === item.ref ? { ...proposal, isStale: true } : proposal))
    return {
      result: `Revised ${item.ref}. Projector’s panel shows the new text, and its answer still arrives as a message that starts ANSWER ${item.ref} or DISMISSED ${item.ref}.`,
    }
  }).catch(() => ({
    deny: 'Projector could not revise the question; ask the revised question in the conversation instead',
  }))

  // The panel takes the restored item from the CLI at once, so it is open in
  // the panel before the next read of the store, and comes back as a new item
  // does when the person hid it.
  on('tool.call', { tool: RESTORE_TOOL }, async ($, e) => {
    const session = await $.session.id()
    const instead = 'tell the person in the conversation that it could not be restored'
    const ran = await runTool($, restoreArguments(e, session), instead)
    if ('deny' in ran) return ran
    const item = itemOf(ran.stdout, session)
    await replace($, item)
    if (!(await read($, isShown))) show($)
    const { ref } = item
    return {
      result:
        item.kind === 'notice'
          ? `Restored ${ref}. It is back in Projector’s panel until the person dismisses it; nothing comes back to you.`
          : `Restored ${ref}. It is open in Projector’s panel again, and its answer arrives as a message that starts ANSWER ${ref} or DISMISSED ${ref}. An agent that already heard DISMISSED ${ref} hears RESTORED ${ref} first.`,
    }
  }).catch(() => ({
    deny: 'Projector could not restore the item; tell the person in the conversation that it could not be restored',
  }))

  // These hooks only watch: the catch hands back what the event settled to,
  // so a failure here never runs a tool twice or refuses anything.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny === undefined) await adopt($, e.command)
    return ran
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: 'Monitor' }, async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny === undefined) await adopt($, e.command ?? '')
    return ran
  }).catch(($, e, next) => next(e))

  on('prompt.submit', async ($, e, next) => {
    // A watcher's line reaches the session as a task notification: the moment
    // a pull request it tracks has changed.
    if (e.origin.kind === 'task-notification') soon($)

    // A prompt sent from the terminal means the prompt has the keyboard, even
    // when no keystroke edited it since Esc, as with one a drawer filled.
    if (e.origin.kind === 'composer') void unfocus($).catch(() => undefined)

    // An answer the panel submitted for the main loop has arrived. Seeing it
    // here, rather than only where the panel submitted it, settles it after a
    // reload of the module too.
    if (e.origin.kind === 'plugin' && e.origin.name === 'projector') {
      const entered = await next(e)
      if (entered.drop === undefined) {
        await adoptOldSending($)
        const entry = Object.values(await read($, sending)).find(one => one.route === 'submit' && one.text === e.text)
        if (entry !== undefined) await settle($, entry.ref, 'delivered')
      }
      return entered
    }

    // An option the panel put in the prompt answers its question: the prompt
    // carries the answer to the main loop, and once the prompt has entered,
    // the panel records it. Any other text under a quoted question replies to
    // it, and the prompt carries the reply while the question stays open.
    // Either way the prompt is done with the question's drawer. A subagent's
    // question gets a note naming it, so the main loop can pass the answer or
    // the reply on. Only a prompt you sent, at the terminal or through Remote
    // Control, counts: an agent's message or a notification can quote a
    // question it is still waiting on.
    const session = await $.session.id()
    const isYours = e.origin.kind === 'composer' || e.origin.kind === 'bridge'
    const mine = isYours ? (await read($, items)).filter(item => item.session === session) : []
    const { answers, replies } = readPrompt(e.text, mine, isYours ? proposed : [])
    // An open question is done only when the prompt answers or replies to
    // it, so a quote two questions share closes one drawer, not both.
    const isOpenQuestion = (item: Item) => item.kind === 'question' && item.answer === undefined
    const done = new Set([
      ...mine.filter(item => !isOpenQuestion(item) && e.text.includes(quoteOf(item))).map(item => item.ref),
      ...answers.map(one => one.question.ref),
      ...replies.map(question => question.ref),
    ])
    if (isYours) proposed = proposed.filter(proposal => !done.has(proposal.ref) && !e.text.includes(proposal.quote))
    const answered = answers.map(one => one.question).filter(question => question.agentId !== undefined)
    const replied = replies.filter(question => question.agentId !== undefined)
    const agents = answered.length + replied.length === 0 ? [] : await $.agent.list().catch(() => [])
    const notes = [
      ...answered.map(question => relayNote(question, agents)),
      ...replied.map(question => replyNote(question, agents)),
    ]
    const entered = await next(notes.length === 0 ? e : { ...e, context: [...(e.context ?? []), ...notes] })
    if (entered.drop === undefined) {
      for (const ref of done) void closeDrawer($, `item:${ref}`).catch(() => undefined)
      for (const one of answers) void answerFromPrompt($, one.question, one.letter)
    }
    return entered
  }).catch(($, e, next) => next(e))

  // Enter presses the panel's element under the ring, so where the ring is
  // tells a press from the keyboard from a click. A ring that lands also
  // means the panel holds the keyboard, so its hint names the keys that work
  // there.
  on('ui.focus', { component: 'AbovePrompt' }, async ($, e, next) => {
    const moved = await next(e)
    if (moved.deny === undefined) {
      ring = { requestId: e.requestId, key: e.plugin === 'projector' ? e.element : undefined }
      if (!(await read($, isFocused))) {
        await update($, isFocused, () => true)
        $.ui.invalidate('ui.render')
      }
    }
    return moved
  }).catch(($, e, next) => next(e))

  // Claude Code reports no event when Esc gives the keyboard back to the
  // prompt, so the panel's focused hint stays until the next keystroke in the
  // prompt, or until a prompt is sent from it. Only that first keystroke
  // redraws the panel; it waits on nothing.
  on('prompt.edit', ($, e, next) => {
    void unfocus($).catch(() => undefined)
    return next(e)
  })

  // A turn may have pushed, replied or resolved threads.
  on('turn.complete', ($, e, next) => {
    soon($)
    return next(e)
  })

  // `/projector` shows or hides the panel.
  on('command.run', { command: 'projector' }, async $ => {
    if (await read($, isShown)) {
      await update($, isShown, () => false)
      return { text: 'Hid Projector’s panel.' }
    }
    show($)
    return { text: 'Showing Projector’s panel above the prompt.' }
  })

  // The panel above the prompt spans the terminal and is as tall as its rows,
  // so each pull request and each item takes one line, and a drawer more.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !(await read($, isShown))) return next(e)

    const { Box, Button, Link, Markdown, Text } = $.ui.resolve(e)
    const shown = current(await read($, snapshot))
    const time = await read($, now)
    const opened = await read($, drawer)
    const shownThreads = await read($, drawerThreads)
    const asking = await read($, asked)
    const shownItems = (await read($, items)).filter(item => isShownAt(item, time))
    // Each kind's count in the header is a button that collapses its items to
    // one line at the top of the list, and the line expands them again. A
    // kind stays collapsed until it is expanded, but while it has nothing
    // open the header has no count to expand it from, so its items show.
    const kindCounts = ITEM_KINDS.map(kind => {
      const open = shownItems.filter(
        item => item.kind === kind && item.answer === undefined && !isSnoozedAt(item, time),
      ).length
      return { kind, count: countOf(open, kind) }
    })
    const folded = await read($, collapsed)
    const isCollapsed = (kind: Item['kind']) =>
      Array.isArray(folded) && folded.includes(kind) && kindCounts.some(one => one.kind === kind && one.count !== undefined)
    const fold = (kind: Item['kind']) => {
      update($, collapsed, was => {
        const kinds = Array.isArray(was) ? was.filter(one => ITEM_KINDS.includes(one)) : []
        return isCollapsed(kind) ? kinds.filter(one => one !== kind) : [...kinds, kind]
      }).catch(() => undefined)
    }
    const prCount = countOf(shown.rows.length, 'pull request')
    const counts = [prCount, ...kindCounts.map(one => one.count)]
    const label = labeler(shown.rows.map(row => row.pr))
    const reference = referencer(shown.rows.map(row => row.pr))
    const labelWidth = Math.max(0, ...shown.rows.map(row => label(row.pr).length))
    // Another person's line names its author after `draft`, in a column as
    // wide as the longest of those logins, so their titles start in one
    // column too.
    const authorWidth = Math.max(
      0,
      ...shown.rows.filter(row => !isOwn(row.pr, shown.viewer)).map(row => (row.pr.author ?? '').length),
    )
    const tally = counts.filter(part => part !== undefined).join(' · ')
    const reloaded = shown.fetchedAt === null ? '' : span(secondsSince(time, shown.fetchedAt))
    // A failed poll keeps drawing the pull requests GitHub last gave, and the
    // header's time says how old they are, so the panel says why it failed
    // only when it has none of them to draw.
    const failure = shown.error === null || shown.rows.length > 0 ? undefined : `GitHub: ${shown.error}`
    // Enter with the ring on the Button it presses takes the ring along,
    // `onto` what the press puts in the Button's place; a click leaves the
    // ring where it was.
    const carry = (pressed: string, onto: string | undefined) => {
      if (ring?.key === pressed && onto !== undefined) void focusOn($, ring.requestId, onto)
    }
    // A drawer asks before it snoozes or dismisses, in its actions' place,
    // until the question is answered or another drawer opens.
    const ask = (key: string, about: 'snooze' | 'dismiss' | null) => {
      update($, asked, () => (about === null ? null : { drawer: key, about })).catch(() => undefined)
    }
    const isAsked = (key: string, about: 'snooze' | 'dismiss') => asking?.drawer === key && asking.about === about
    // A line's press opens or closes its drawer, and the ring goes onto the
    // drawer's first control or back onto the line. A drawer always opens on
    // its actions, never on a question it asked before. A pull request's
    // drawer reads its threads as it opens.
    const toggle = (key: string, line: string, onto?: string) => {
      update($, drawer, was => (was === key ? null : key))
        .then(() => loadThreads($))
        .catch(() => undefined)
      ask(key, null)
      carry(line, onto)
    }

    // In a terminal the panel draws on a background of its own, so a line
    // that moves as the panel scrolls is plainly the panel's. It ends at
    // `bodyColumns`, where the engine draws the panel's `[-]` on the first
    // row, so the rows between its header and its hint are `columns` wide.
    const isTerminal = e.surface === 'terminal'
    const columns = e.props.bodyColumns - PANEL_INSET - 2 * (PANEL_PADDING + LINE_PADDING)
    // The header and the key hint start a column before the lines between
    // them but end where those lines do, so their right ends line up with
    // the rows' ages.
    const edgeColumns = columns + LINE_PADDING
    const setting = await read($, background)
    const color = setting === AUTO ? await read($, themeBackground) : setting
    const hint = (await read($, isFocused)) ? FOCUSED_HINT : FOCUS_HINT
    // The panel's first line is its header, all in `subtle`: `HEADER_NAME` at
    // its left, and the counts and how long ago the panel reloaded at its
    // right, so the time ends in the rows' age column. Two spaces set the
    // counts apart from the time, about the gap a row leaves before its age.
    // The rows start on the next line, the background setting the header
    // apart from them. An open item's drawer has the panel to itself, so it
    // draws no header. Only a terminal lays text out in those columns, so no
    // other surface draws a panel.
    const status = [tally, reloaded].filter(part => part !== '').join('  ')
    const gap = ' '.repeat(Math.max(1, edgeColumns - HEADER_NAME.length - status.length))
    // Each count of a kind of item is a Button of its own, which collapses or
    // expands that kind, so a header that counts items is a row of pieces.
    const itemCounts = kindCounts.flatMap(({ kind, count }) => (count === undefined ? [] : [{ kind, count }]))
    const header =
      itemCounts.length === 0 ? (
        <Text key="header" color="subtle">
          {`${HEADER_NAME}${gap}${status}`}
        </Text>
      ) : (
        <Box key="header" flexDirection="row">
          <Text color="subtle">{`${HEADER_NAME}${gap}${prCount === undefined ? '' : `${prCount} · `}`}</Text>
          {itemCounts.flatMap(({ kind, count }, at) => [
            ...(at === 0 ? [] : [<Text key={`between:${kind}`} color="subtle">{' · '}</Text>]),
            <Button key={`count:${kind}`} plain onPress={() => fold(kind)}>
              <Text color="subtle">{count}</Text>
            </Button>,
          ])}
          {reloaded !== '' && <Text color="subtle">{`  ${reloaded}`}</Text>}
        </Box>
      )
    const panel = (lines: RenderChildren, isHeaded = true) =>
      isTerminal ? (
        <Box flexDirection="column" width={e.props.bodyColumns} paddingLeft={PANEL_INSET}>
          <Box
            key="panel"
            flexDirection="column"
            width={e.props.bodyColumns - PANEL_INSET}
            paddingLeft={PANEL_PADDING}
            paddingRight={PANEL_PADDING}
            backgroundColor={color}
          >
            {isHeaded && header}
            <Box flexDirection="column" paddingLeft={LINE_PADDING} paddingRight={LINE_PADDING}>
              {lines}
            </Box>
            <Text key="hint" color="subtle">
              {hint.padStart(edgeColumns)}
            </Text>
          </Box>
        </Box>
      ) : (
        <Box flexDirection="column">{lines}</Box>
      )

    if (shown.rows.length === 0 && shownItems.length === 0) {
      return panel([
        failure !== undefined ? (
          <Text key="failure" color="error">
            {failure}
          </Text>
        ) : shown.fetchedAt === null ? (
          <Text key="looking" color="subtle">
            Looking for pull requests…
          </Text>
        ) : (
          <Text key="tagline" color="subtle">{` ${TAGLINE}`}</Text>
        ),
      ])
    }

    // An item names its pull request as the panel labels it, or in full when
    // the panel does not list it.
    const pullOf = (item: Item) => {
      const row = shown.rows.find(one => keyOf(one.pr) === item.pr?.toLowerCase())
      return row === undefined ? (item.pr ?? '') : label(row.pr)
    }

    // The items are a list below the pull requests, oldest first, each
    // bulleted `-`; an open question's line takes `QUESTION_COLOR`, and a
    // resolved or snoozed item's `subtle`. An open question's drawer numbers
    // its options; choosing one puts the question and that answer in the
    // prompt as text, for you to send or change, and sending it unchanged is
    // what answers the question. Discuss puts the question alone, for a reply
    // in your own words, which answers nothing. A resolved question's drawer
    // lists its options with nothing to press, and an answer typed with
    // `project inbox answer --text` after them.

    // A pull request's line and an item's line each end with an age, in one
    // column as wide as the widest age in the panel, so what comes before the
    // ages ends in one column too.
    const ageOf = (then: number | null) => (then === null ? '' : span(secondsSince(time, then)))
    const ageWidth = Math.max(
      0,
      ...[...shown.rows.map(row => row.pr.updatedAt), ...shownItems.map(item => item.postedAt)].map(
        then => ageOf(then).length,
      ),
    )
    const ageColumn = (key: string, then: number | null) => (
      <Box key={`age:${key}`} flexShrink={0} width={ageWidth} justifyContent="flex-end">
        <Text color="subtle">{ageOf(then)}</Text>
      </Box>
    )
    const fill = (text: string) => {
      $.prompt.fill({ text, mode: 'insert' }).catch(() => undefined)
    }
    // The engine draws a plain Button's key in its fixed accent color and the
    // `: ` after it in the Button's own text color, which a plugin cannot set,
    // and a hover or the focus ring inverts each colored part apart. So a
    // keyed line's label is a plain string, drawn and inverted with the `: `
    // as one block after the key.
    //
    // A drawer's actions follow its options, a line each: a plain Button on
    // its digit, as `7: discuss question`. The digits count down from 9, so
    // the options keep 1 to 6 and the item's line 0: `7` discusses, `8` sets
    // the line aside or brings it back, and `9` puts it off or ends the
    // snooze. A digit presses its line as an option's digit presses it. What
    // a drawer asks in its actions' place draws the same way.
    type Line = { name: string; label: string; hotkey: string; onPress: () => void }
    const drawLines = (id: string, lines: readonly Line[]) =>
      lines.map(({ name, label, hotkey, onPress }) => (
        <Button key={`${name}:${id}`} plain hotkey={hotkey} onPress={onPress}>
          {label}
        </Button>
      ))
    // An open question's options are plain Buttons on the digits 1 to 6,
    // which the engine draws before them, as `1: Decline it`. A digit presses
    // its option from the panel, or typed first into an empty prompt, as a
    // survey's does; either only fills the prompt, so a stray one sends
    // nothing. A resolved question's options are text, its answer marked.
    const drawAnswers = (question: Question) => {
      const { ref, answer } = question
      const chosen = answer !== undefined && 'letter' in answer ? answer.letter : undefined
      const recommended = ' (recommended)'
      const marked = <Text color="success"> ✓</Text>
      const pick = (choice: string) => {
        propose(question, choice)
        fill(`${proposalOf(question, choice)}\n`)
      }

      return [
        <Text key={`before-answers:${ref}`}> </Text>,
        ...question.choices.map((choice, index) => {
          const letter = letterAt(index)
          return answer === undefined ? (
            <Button key={`answer:${ref}.${letter}`} plain hotkey={String(index + 1)} onPress={() => pick(choice)}>
              {index === 0 ? `${choice}${recommended}` : choice}
            </Button>
          ) : (
            <Text key={`option:${ref}.${letter}`} color={letter === chosen ? undefined : 'subtle'}>
              {choice}
              {index === 0 && <Text color="success">{recommended}</Text>}
              {letter === chosen && marked}
            </Text>
          )
        }),
        chosen === typedLetter(question) && answer !== undefined && 'text' in answer && (
          <Text key={`option:${ref}.${chosen}`}>
            {answer.text}
            {marked}
          </Text>
        ),
      ]
    }
    const drawItem = (item: Item) => {
      const { ref, answer } = item
      const key = `item:${ref}`
      const isOpen = answer === undefined
      const isDismissed = answer !== undefined && 'dismissed' in answer
      // A snoozed item stays listed for 30 seconds, muted but not struck
      // through, so it reads as put off rather than dismissed.
      const isSnoozed = isSnoozedAt(item, time)
      const isOpened = opened === key
      const color = !isOpen || isSnoozed ? 'subtle' : item.kind === 'question' ? QUESTION_COLOR : NOTICE_COLOR
      const pull = pullOf(item)
      // Each action names the kind of item it acts on. A dismissed or snoozed
      // item's drawer offers to bring it back where it offered to dismiss or
      // snooze it, on the same digit; a resolved item cannot be snoozed.
      const restore = (name: string, hotkey: string): Line => ({
        name,
        label: `${name} ${item.kind}`,
        hotkey,
        onPress: () => {
          ask(key, null)
          void act($, item, 'restore')
        },
      })
      const actions: Line[] = [
        {
          name: 'discuss',
          label: `discuss ${item.kind}`,
          hotkey: '7',
          onPress: () => {
            if (item.kind === 'question') propose(item, '')
            fill(`${quoteOf(item)}\n\n`)
          },
        },
        ...(isOpen
          ? [
              {
                name: 'dismiss',
                label: `dismiss ${item.kind}`,
                hotkey: '8',
                onPress: () => {
                  void act($, item, 'dismiss')
                },
              },
              isSnoozed
                ? restore('unsnooze', '9')
                : {
                    name: 'snooze',
                    label: `snooze ${item.kind}`,
                    hotkey: '9',
                    onPress: () => {
                      ask(key, 'snooze')
                      carry(`snooze:${ref}`, `snooze-${SNOOZES[0].minutes}:${ref}`)
                    },
                  },
            ]
          : isDismissed
            ? [restore('restore', '8')]
            : []),
      ]
      // Snooze asks how long before it snoozes anything. While it asks, the
      // lengths take the options' place and digits, and `9` cancels in the
      // actions' place, putting both back.
      const lengths: Line[] = [
        ...SNOOZES.map(({ label, minutes }, index) => ({
          name: `snooze-${minutes}`,
          label,
          hotkey: String(index + 1),
          onPress: () => {
            ask(key, null)
            void act($, item, 'snooze', minutes)
          },
        })),
        {
          name: 'cancel',
          label: 'cancel',
          hotkey: '9',
          onPress: () => {
            ask(key, null)
            carry(`cancel:${ref}`, `snooze:${ref}`)
          },
        },
      ]
      const isChoosingLength = isOpen && !isSnoozed && isAsked(key, 'snooze')
      // An open item's line has the panel to itself, so closing its drawer
      // puts the line back lower in the list. The engine keeps the ring by
      // position, and the open line's key is one the list never draws, so a
      // move onto the line in the list waits for the panel to draw the list.
      const line = isOpened ? `close:${ref}` : key
      const first = item.kind === 'question' && isOpen ? `answer:${ref}.${letterAt(0)}` : `discuss:${ref}`
      const isSending = !isOpen && item.deliveredAt === undefined

      return (
        <Box key={`line:${ref}`} flexDirection="column">
          <Box flexDirection="row" columnGap={1}>
            {/* The whole item is one plain Button, so a press anywhere on it
                opens its drawer: a click in fullscreen, Enter on the main
                screen. */}
            <Box flexGrow={1} flexShrink={1}>
              {/* An open item's line closes its drawer on `0`, a digit the
                  options leave free, which the engine draws before it in the
                  bullet's place; the drawer below is indented to its text.
                  Like the options' digits, it works from an empty prompt too. */}
              <Button
                key={line}
                plain
                hotkey={isOpened ? CLOSE_HOTKEY : undefined}
                onPress={() => toggle(key, line, isOpened ? key : first)}
              >
                {!isOpened && <Text color={color}>- </Text>}
                <Text color={color} strikethrough={isDismissed}>
                  {item.subject}
                </Text>
              </Button>
            </Box>
            {isSending && (
              <Box flexShrink={0}>
                <Text color="subtle">sending</Text>
              </Box>
            )}
            {pull !== '' && (
              <Box flexShrink={0}>
                <Text color="subtle">{pull}</Text>
              </Box>
            )}
            {ageColumn(key, item.postedAt)}
          </Box>
          {isOpened && (
            <Box key={`drawer:${key}`} flexDirection="column" marginLeft={DRAWER_INDENT}>
              {/* A blank line sets the item apart from its line, and the detail,
                  when there is one, sits between two. Another sets the actions,
                  or what the drawer asks in their place, apart from what comes
                  above. */}
              {item.detail !== '' && <Text key={`before-detail:${ref}`}> </Text>}
              {item.detail !== '' && <Markdown key={`detail:${ref}`} text={item.detail} />}
              {item.kind === 'question' && !isChoosingLength && drawAnswers(item)}
              <Text key={`before-buttons:${ref}`}> </Text>
              {drawLines(ref, isChoosingLength ? lengths : actions)}
            </Box>
          )}
        </Box>
      )
    }

    const drawRow = ({ pr, kinds }: Row) => {
      const key = keyOf(pr)
      const notes = notesOf(pr)
      return (
        <Box key={key} flexDirection="column">
          {/* Only the title gives way when the line is short; the label column
              is as wide as the longest label, so every title starts in one
              column, and the title fills the line, so its notes and age end
              it as an item's pull request and age do. The label is a plain
              Button rather than a link, so it takes a click in fullscreen and
              Enter on the main screen, and no terminal ever draws a URL
              beside it. */}
          <Box flexDirection="row" columnGap={1}>
            <Box flexShrink={0} width={labelWidth}>
              <Button
                key={`pr:${key}`}
                plain
                label={label(pr)}
                onPress={() => toggle(key, `pr:${key}`, opened === key ? undefined : `discuss:${key}`)}
              />
            </Box>
            <Box flexShrink={0}>
              <Text color={pr.isDraft ? 'warning' : 'success'}>{pr.isDraft ? 'draft' : 'ready'}</Text>
            </Box>
            {!isOwn(pr, shown.viewer) && (
              <Box key={`author:${key}`} flexShrink={0} width={authorWidth}>
                <Text color="subtle">{pr.author ?? ''}</Text>
              </Box>
            )}
            <Box flexGrow={1} flexShrink={1}>
              <Text wrap="truncate-end">{pr.title}</Text>
            </Box>
            {notes.length > 0 && (
              <Box flexShrink={0}>
                <Text>
                  {notes.flatMap((note, at) => [
                    ...(at > 0 ? [' · '] : []),
                    <Text key={note.text} color={note.color}>
                      {note.text}
                    </Text>,
                  ])}
                </Text>
              </Box>
            )}
            {ageColumn(key, pr.updatedAt)}
          </Box>
          {/* The drawer starts where the state does, one column past the label. */}
          {opened === key && (
            <Box key={`drawer:${key}`} flexDirection="column" marginLeft={labelWidth + 1}>
              {/* Each `·` keeps the default color, so a piece's color, such
                  as the checks', stops at its own text. */}
              <Text wrap="truncate-end">
                {kinds.flatMap((kind, at) => [
                  ...(at === 0 ? [] : [' and ']),
                  <Text key={`loop:${kind}`} color={LOOP_COLORS[kind]}>
                    {kind}
                  </Text>,
                ])}
                {pr.branch !== '' && ' · '}
                {pr.branch !== '' && <Text color={BRANCH_COLOR}>{pr.branch}</Text>}
                {' · '}
                <Text color="success">{`+${pr.additions}`}</Text>{' '}
                <Text color="error">{`−${pr.deletions}`}</Text>
                {` in ${pr.changedFiles} file${pr.changedFiles === 1 ? '' : 's'}`}
                {pr.checks !== null && ' · '}
                {pr.checks !== null && <Text color={CHECK_COLORS[pr.checks]}>checks {pr.checks}</Text>}
              </Text>
              {/* Under the facts come the actions, a line each, and then the
                  links. Each link has a line of its own: a terminal without
                  hyperlinks draws a link's URL after its text, and a shared
                  line would cut the URL off. */}
              {drawLines(
                key,
                isAsked(key, 'dismiss')
                  ? [
                      {
                        name: 'confirm',
                        label: 'yes, dismiss PR',
                        hotkey: '8',
                        onPress: () => {
                          ask(key, null)
                          void dismissPr($, pr)
                        },
                      },
                      {
                        name: 'keep',
                        label: 'no, keep it',
                        hotkey: '9',
                        onPress: () => {
                          ask(key, null)
                          carry(`keep:${key}`, `dismiss:${key}`)
                        },
                      },
                    ]
                  : [
                      { name: 'discuss', label: 'discuss PR', hotkey: '7', onPress: () => fill(`${reference(pr)} `) },
                      {
                        name: 'dismiss',
                        label: 'dismiss PR',
                        hotkey: '8',
                        onPress: () => {
                          ask(key, 'dismiss')
                          carry(`dismiss:${key}`, `confirm:${key}`)
                        },
                      },
                    ],
              )}
              <Markdown key={`github:${key}`} text={`[open in github](${pr.url})`} />
              {pr.summaryUrl !== null && (
                <Markdown key={`projector:${key}`} text={`[open in projector](${pr.summaryUrl})`} />
              )}
              {/* Each unresolved thread links to its first comment, since GitHub
                  folds an outdated thread and an old one on a long timeline away.
                  The text goes in as a Link's children, so nothing in it is read
                  as markdown. */}
              {(shownThreads?.drawer === key ? shownThreads.threads : []).map((thread, at) => (
                <Text key={`thread:${key}:${at}`}>
                  <Link href={thread.url ?? pr.url}>{threadLabel(thread)}</Link>
                </Text>
              ))}
            </Box>
          )}
        </Box>
      )
    }

    // An open item's drawer has the panel to itself, until it is closed or the
    // item leaves the panel. The header steps aside too, so the item is the
    // top row and, in a terminal, the drawer has another row before the
    // engine, which caps the panel below half the terminal, starts to scroll.
    const focused = shownItems.find(item => opened === `item:${item.ref}`)
    if (focused !== undefined) return panel(drawItem(focused), false)

    return panel([
      ...byAuthor(shown.rows, shown.viewer).map(drawRow),
      failure !== undefined && (
        <Text key="failure" color="error">
          {failure}
        </Text>
      ),
      shownItems.length > 0 && (shown.rows.length > 0 || failure !== undefined) && (
        <Text key="before-items"> </Text>
      ),
      // A collapsed kind's items give way to one line at the top of the list,
      // its count in the kind's color, which expands them again.
      ...itemCounts
        .filter(({ kind }) => isCollapsed(kind))
        .map(({ kind, count }) => (
          <Button key={`expand:${kind}`} plain onPress={() => fold(kind)}>
            <Text color={KIND_COLORS[kind]}>{`- ${count}`}</Text>
          </Button>
        )),
      ...[...shownItems]
        .filter(item => !isCollapsed(item.kind))
        .sort((a, b) => b.postedAt - a.postedAt)
        .map(drawItem),
    ])
  })
}
