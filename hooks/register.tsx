import { atom, read, update } from 'claude-code'
import type { EngineInterface, FsEntry, Register } from 'claude-code'

import type { Checks, Item, LoopKind, Notice, PullRequest, Question, Row, SeenLoop, Sending, Snapshot } from './types'
import { buildQuery, parseResponse, type Fetched } from './github'
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
 * something. A notice keeps the default color.
 */
const QUESTION_COLOR = 'permission'

/** The lengths a drawer offers to snooze an item for, within the CLI's cap of a day. */
const SNOOZES = [
  { label: '15 min', minutes: 15 },
  { label: '1 hour', minutes: 60 },
  { label: '4 hours', minutes: 240 },
  { label: '1 day', minutes: 1440 },
] as const

const seen = atom({ plugin: 'projector', key: 'seenLoops' } as const, [])
const snapshot = atom({ plugin: 'projector', key: 'bandSnapshot' } as const, EMPTY)
const now = atom({ plugin: 'projector', key: 'bandNow' } as const, 0)
const isShown = atom({ plugin: 'projector', key: 'isBandShown' } as const, false)
const drawer = atom({ plugin: 'projector', key: 'bandDrawer' } as const, null)
const snoozing = atom({ plugin: 'projector', key: 'bandSnoozing' } as const, null)
const items = atom({ plugin: 'projector', key: 'bandInbox' } as const, [])
const sending = atom({ plugin: 'projector', key: 'bandSending' } as const, {})

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

/** The store's directory as the band last read it, so it reads again only after a change. */
let listed: string | undefined
let isChecking = false
/** The answers this copy of the module is claiming or delivering, by reference. */
const delivering = new Set<string>()
/** The items and pull requests the band is dismissing or snoozing, so a second press does nothing. */
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
 * Where the band's focus ring last landed, as `ui.focus` reported it: the
 * band's site, and the key of the band's element under the ring, absent when
 * the ring is on none of them. The engine says nothing when the band hands
 * the keyboard back, so the ring can be stale, and the engine refuses a move
 * once the band no longer holds the keyboard.
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

/** The few variables a tracked path is written with, read once per load. */
function envOf($: Engine): Promise<Env> {
  envLoad ??= (async () => {
    const values: Record<string, string | undefined> = {
      HOME: await $.env.get('HOME'),
      TMPDIR: await $.env.get('TMPDIR'),
      XDG_CONFIG_HOME: await $.env.get('XDG_CONFIG_HOME'),
      XDG_DATA_HOME: await $.env.get('XDG_DATA_HOME'),
      XDG_STATE_HOME: await $.env.get('XDG_STATE_HOME'),
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
 * band when one is new. A loop restarts its watcher with the same command,
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
        $.ui.toast(`projector: cannot tell which file ${watcher.written} names, so the band does not list that loop`)
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

  const fetched = await fetchPrs($, [...tracked.values()])

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
}

/**
 * Reloads the loops and their pull requests, only while the band is shown
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

/** Puts an item the CLI just changed in the band's list, in place of what it showed. */
const replace = ($: Engine, item: Item) =>
  update($, items, list => list.map(one => (one.ref === item.ref && one.session === item.session ? item : one)))

/** Closes a drawer when it is the one open, and with it any question of how long to snooze. */
async function closeDrawer($: Engine, key: string): Promise<void> {
  await update($, drawer, was => (was === key ? null : was))
  await update($, snoozing, was => (was === key ? null : was))
}

/**
 * Closes the open drawer once its line has left the band, so it does not
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
 * An item as the CLI prints it, without the session the band adds. Each kind
 * is its own `Omit`, because one over the union would merge their `kind`s.
 */
type Printed = Omit<Question, 'session'> | Omit<Notice, 'session'>

/** The item a CLI command printed with `--json`, in the session it belongs to. */
const itemOf = (stdout: string, session: string): Item => ({
  ...(JSON.parse(stdout) as { item: Printed }).item,
  session,
})

/**
 * Takes the band's list from the store, and shows the band for an item that
 * is open and not snoozed when the band last held it otherwise: a new item,
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
  if (isNew && !(await read($, isShown))) show($)
}

/**
 * Reads the session's inbox when the store's directory changed since the
 * last read that succeeded. Listing a directory starts no process, so an idle
 * session, band shown or hidden, starts none. The signature is recorded only
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

/** Remembers an answer the band is delivering, in the session's state so a reloaded module finds it. */
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
    // Another deliverer holds it, or it was delivered since the band read the store.
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

/**
 * Keeps each delivery's claim live while it waits, as a submit behind a long
 * turn does. A submit that an earlier copy of the module made still waits in
 * the session's queue, so its claim is kept until the prompt hook sees it
 * arrive. A send settles at once, so one an earlier copy left is let go, and
 * its claim lapses for the band to deliver again.
 */
async function renewClaims($: Engine): Promise<void> {
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
 * Reads the store when it changed, renews the band's claims, and delivers
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
 * Dismisses, snoozes for `minutes`, or restores an item from the band; a
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
      // Resolved elsewhere since the band read the store: read it again.
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
 * keeps it out of each tracked file that lists it, and the band reloads
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
 * prompt itself carries it to the main loop. The band's own deliverer leaves
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
    // An answer recorded but not marked delivered is the band's to deliver on a later tick.
  } finally {
    delivering.delete(ref)
  }
}

/**
 * Reads the inbox whether or not the band is shown, closes the drawer of an
 * item that left the band, and brings a hidden band back for an item whose
 * snooze ended since the last tick. A shown band moves
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
 * Shows the band and loads it, from a timer so no hook waits on GitHub. The
 * band's clock moves on before it shows, so an item whose snooze ended while
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
 * Moves the band's focus ring onto one of its elements, which the engine
 * waits for when the band has yet to draw it, and writes why the ring stayed
 * to the debug log when the engine refuses the move.
 */
async function focusOn($: Engine, requestId: string, key: string): Promise<void> {
  const moved = await $.ui.focus({ requestId, key }).catch((error: unknown) => ({ deny: messageOf(error) }))
  if (moved.deny !== undefined) $.ui.log(`projector: the focus stayed off ${key}: ${moved.deny}`, { to: 'debug' })
}

/** Refreshes soon, from a timer so the hook that noticed a change returns at once. */
const soon = ($: Engine) => $.clock.after(1, () => void refresh($))

/**
 * Names a pull request for the prompt: `#12` when the band shows one
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
  if (pr.openThreads > 0) notes.push({ text: `⋮${pr.openThreads}`, color: 'warning' })
  return notes
}

/** The drawer's facts, before its checks: what the loops do, where, and how big. */
function factsOf(pr: PullRequest, kinds: readonly LoopKind[]): string {
  const files = `${pr.changedFiles} file${pr.changedFiles === 1 ? '' : 's'}`
  return [
    kinds.join(' and '),
    pr.branch || undefined,
    `+${pr.additions} −${pr.deletions} in ${files}`,
  ]
    .filter(part => part !== undefined)
    .join(' · ')
}

/** Whether the account the band's `gh` is signed in as wrote a pull request. */
const isOwn = (pr: PullRequest, viewer: string | null) => viewer !== null && pr.author === viewer

/**
 * The band's pull requests in the order it lists them: yours first, then
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

const ago = (seconds: number) => (seconds < 5 ? 'just now' : `${span(seconds)} ago`)

const secondsSince = (time: number, then: number) => Math.max(0, Math.round((time - then) / 1000))

/** Labels a pull request `#12` when the band shows one repository, else by its repository's name. */
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
      description: 'Show or hide Projector’s band of your loops’ pull requests and your inbox',
    })
    // A session whose engine cannot serve the tools keeps the band.
    for (const tool of [
      { name: 'ask', description: ASK_DESCRIPTION, inputSchema: ASK_SCHEMA },
      { name: 'tell', description: TELL_DESCRIPTION, inputSchema: TELL_SCHEMA },
      { name: 'withdraw', description: WITHDRAW_DESCRIPTION, inputSchema: WITHDRAW_SCHEMA },
      { name: 'revise', description: REVISE_DESCRIPTION, inputSchema: REVISE_SCHEMA },
      { name: 'restore', description: RESTORE_DESCRIPTION, inputSchema: RESTORE_SCHEMA },
    ]) {
      await $.tool.register({ ...tool, isDeferred: false }).catch(() => undefined)
    }
    $.clock.every(TICK_MS, () => void tick($))
    soon($)
    checkSoon($)
    return next(e)
  })

  // The tools run the CLI, so what they post, withdraw, or revise and what
  // the shell does land in the same store.
  on('tool.call', { tool: ASK_TOOL }, async ($, e) => {
    const posted = await runTool($, askArguments(e, await $.session.id(), e.agentId), 'ask in the conversation instead')
    if ('deny' in posted) return posted
    const ref = posted.stdout.trim()
    void $.ui.notify(String(e.question), { title: 'Projector question' }).catch(() => undefined)
    return {
      result: `Asked ${ref}. Keep working on anything the answer does not block; it arrives as a message that starts ANSWER ${ref} or DISMISSED ${ref}.`,
    }
  }).catch(() => ({ deny: 'Projector could not record the question; ask it in the conversation instead' }))

  on('tool.call', { tool: TELL_TOOL }, async ($, e) => {
    const args = tellArguments(e, await $.session.id(), e.agentId)
    const posted = await runTool($, args, 'tell the person in the conversation instead')
    if ('deny' in posted) return posted
    const ref = posted.stdout.trim()
    void $.ui.notify(String(e.notice), { title: 'Projector notice' }).catch(() => undefined)
    return {
      result: `Posted ${ref}. It waits in Projector’s band until the person dismisses it; nothing comes back to you.`,
    }
  }).catch(() => ({ deny: 'Projector could not record the notice; tell the person in the conversation instead' }))

  // The band takes the withdrawn item from the CLI at once, so it leaves the
  // band before the next read of the store.
  on('tool.call', { tool: WITHDRAW_TOOL }, async ($, e) => {
    const session = await $.session.id()
    const instead = 'tell the person in the conversation that it no longer needs them'
    const ran = await runTool($, withdrawArguments(e, session), instead)
    if ('deny' in ran) return ran
    const item = itemOf(ran.stdout, session)
    await replace($, item)
    await closeDrawer($, `item:${item.ref}`)
    return { result: `Withdrew ${item.ref}. It has left Projector’s band, and nothing comes back to you.` }
  }).catch(() => ({
    deny: 'Projector could not withdraw the item; tell the person in the conversation that it no longer needs them',
  }))

  // The band takes the revised question from the CLI at once, so it redraws
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
      result: `Revised ${item.ref}. Projector’s band shows the new text, and its answer still arrives as a message that starts ANSWER ${item.ref} or DISMISSED ${item.ref}.`,
    }
  }).catch(() => ({
    deny: 'Projector could not revise the question; ask the revised question in the conversation instead',
  }))

  // The band takes the restored item from the CLI at once, so it is open in
  // the band before the next read of the store, and comes back as a new item
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
          ? `Restored ${ref}. It is back in Projector’s band until the person dismisses it; nothing comes back to you.`
          : `Restored ${ref}. It is open in Projector’s band again, and its answer arrives as a message that starts ANSWER ${ref} or DISMISSED ${ref}. An agent that already heard DISMISSED ${ref} hears RESTORED ${ref} first.`,
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

    // An answer the band submitted for the main loop has arrived. Seeing it
    // here, rather than only where the band submitted it, settles it after a
    // reload of the module too.
    if (e.origin.kind === 'plugin' && e.origin.name === 'projector') {
      const entered = await next(e)
      if (entered.drop === undefined) {
        const entry = Object.values(await read($, sending)).find(one => one.route === 'submit' && one.text === e.text)
        if (entry !== undefined) await settle($, entry.ref, 'delivered')
      }
      return entered
    }

    // An option the band put in the prompt answers its question: the prompt
    // carries the answer to the main loop, and once the prompt has entered,
    // the band records it. Any other text under a quoted question replies to
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

  // Enter presses the band's element under the ring, so where the ring is
  // tells a press from the keyboard from a click.
  on('ui.focus', { component: 'AbovePrompt' }, async ($, e, next) => {
    const moved = await next(e)
    if (moved.deny === undefined) {
      ring = { requestId: e.requestId, key: e.plugin === 'projector' ? e.element : undefined }
    }
    return moved
  }).catch(($, e, next) => next(e))

  // A turn may have pushed, replied or resolved threads.
  on('turn.complete', ($, e, next) => {
    soon($)
    return next(e)
  })

  // `/projector` shows or hides the band.
  on('command.run', { command: 'projector' }, async $ => {
    if (await read($, isShown)) {
      await update($, isShown, () => false)
      return { text: 'Hid Projector’s band.' }
    }
    show($)
    return { text: 'Showing Projector’s band above the prompt.' }
  })

  // The band above the prompt spans the terminal and is as tall as its rows,
  // so each pull request and each item takes one line, and a drawer more.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !(await read($, isShown))) return next(e)

    const { Box, Button, Markdown, Text } = $.ui.resolve(e)
    const shown = current(await read($, snapshot))
    const time = await read($, now)
    const opened = await read($, drawer)
    const asking = await read($, snoozing)
    const shownItems = (await read($, items)).filter(item => isShownAt(item, time))
    const counts = (['question', 'notice'] as const).map(kind => {
      const count = shownItems.filter(
        item => item.kind === kind && item.answer === undefined && !isSnoozedAt(item, time),
      ).length
      return count === 0 ? undefined : `${count} ${kind}${count === 1 ? '' : 's'}`
    })
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
    const heading = [
      ...counts,
      shown.fetchedAt === null ? undefined : `updated ${ago(secondsSince(time, shown.fetchedAt))}`,
    ]
      .filter(part => part !== undefined)
      .join(' · ')
    const updated = heading === '' ? '' : ` ${heading}`
    // A failed poll keeps drawing the pull requests GitHub last gave, and the
    // divider's time says how old they are, so the band says why it failed
    // only when it has none of them to draw.
    const failure = shown.error === null || shown.rows.length > 0 ? undefined : `GitHub: ${shown.error}`
    // Enter with the ring on the Button it presses takes the ring along,
    // `onto` what the press puts in the Button's place; a click leaves the
    // ring where it was.
    const carry = (pressed: string, onto: string | undefined) => {
      if (ring?.key === pressed && onto !== undefined) void focusOn($, ring.requestId, onto)
    }
    // A line's press opens or closes its drawer, and the ring goes onto the
    // drawer's first control or back onto the line. A drawer always opens on
    // its actions, never on a question of how long to snooze.
    const toggle = (key: string, line: string, onto?: string) => {
      update($, drawer, was => (was === key ? null : key)).catch(() => undefined)
      update($, snoozing, () => null).catch(() => undefined)
      carry(line, onto)
    }

    // The engine draws the band's `[-]` just past `bodyColumns` on the first
    // row, so a divider that fills the body ends at the mark, and a blank line
    // sets it apart from the rows under it. Only a terminal lays text out in
    // those columns; any other surface would wrap the rule, so it draws none.
    const divider =
      e.surface === 'terminal'
        ? [
            <Text key="divider">
              {'─'.repeat(Math.max(0, e.props.bodyColumns - updated.length))}
              {updated}
            </Text>,
            <Text key="under-divider"> </Text>,
          ]
        : []

    if (shown.rows.length === 0 && shownItems.length === 0) {
      return (
        <Box flexDirection="column">
          {divider}
          {failure !== undefined ? (
            <Text color="error">{failure}</Text>
          ) : shown.fetchedAt === null ? (
            <Text color="subtle">Looking for pull requests…</Text>
          ) : (
            <Text key="tagline" color="subtle">{` ${TAGLINE}`}</Text>
          )}
        </Box>
      )
    }

    // An item names its pull request as the band labels it, or in full when
    // the band does not list it.
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
    // column as wide as the widest age in the band, so what comes before the
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
    // its letter, as `d: discuss this`. A letter presses its action while the
    // band holds the keyboard: `d` discusses, `x` sets the line aside or
    // brings it back, and `s` puts it off or ends the snooze.
    type Action = { name: string; label: string; hotkey: 'd' | 'x' | 's'; onPress: () => void }
    const drawActions = (id: string, actions: readonly Action[]) =>
      actions.map(({ name, label, hotkey, onPress }) => (
        <Button key={`${name}:${id}`} plain hotkey={hotkey} onPress={onPress}>
          {label}
        </Button>
      ))
    // The lengths snooze offers share one row, `|` between them, with no
    // keys, since the digits belong to a question's options.
    type Length = { name: string; label: string; onPress: () => void }
    const drawLengths = (id: string, lengths: readonly Length[]) => (
      <Box flexDirection="row" columnGap={1}>
        {lengths.flatMap(({ name, label, onPress }, at) => [
          ...(at === 0 ? [] : [<Text key={`between:${id}.${at}`} color="subtle">|</Text>]),
          <Button key={`${name}:${id}`} plain onPress={onPress}>
            <Text>{label}</Text>
          </Button>,
        ])}
      </Box>
    )
    // An open question's options are plain Buttons on the digits 1 to 6,
    // which the engine draws before them, as `1: Decline it`. A digit presses
    // its option from the band, or typed first into an empty prompt, as a
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
      const color = !isOpen || isSnoozed ? 'subtle' : item.kind === 'question' ? QUESTION_COLOR : undefined
      const pull = pullOf(item)
      // Each action names the kind of item it acts on. A dismissed or snoozed
      // item's drawer offers to bring it back where it offered to dismiss or
      // snooze it, on the same letter; a resolved item cannot be snoozed.
      const restore = (name: string, hotkey: Action['hotkey']): Action => ({
        name,
        label: `${name} ${item.kind}`,
        hotkey,
        onPress: () => void act($, item, 'restore'),
      })
      const actions: Action[] = [
        {
          name: 'discuss',
          label: 'discuss this',
          hotkey: 'd',
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
                hotkey: 'x' as const,
                onPress: () => void act($, item, 'dismiss'),
              },
              isSnoozed
                ? restore('unsnooze', 's')
                : {
                    name: 'snooze',
                    label: `snooze ${item.kind}`,
                    hotkey: 's' as const,
                    onPress: () => {
                      update($, snoozing, () => key).catch(() => undefined)
                      carry(`snooze:${ref}`, `snooze-${SNOOZES[0].minutes}:${ref}`)
                    },
                  },
            ]
          : isDismissed
            ? [restore('restore', 'x')]
            : []),
      ]
      // Snooze asks how long before it snoozes anything: the action lines
      // give way to the lengths and cancel, which puts the actions back.
      const isAsking = asking === key && isOpen && !isSnoozed
      const lengths: Length[] = [
        ...SNOOZES.map(({ label, minutes }) => ({
          name: `snooze-${minutes}`,
          label,
          onPress: () => {
            update($, snoozing, () => null).catch(() => undefined)
            void act($, item, 'snooze', minutes)
          },
        })),
        {
          name: 'cancel',
          label: 'cancel',
          onPress: () => {
            update($, snoozing, () => null).catch(() => undefined)
            carry(`cancel:${ref}`, `snooze:${ref}`)
          },
        },
      ]
      // An open item's line has the band to itself, so closing its drawer
      // puts the line back lower in the list. The engine keeps the ring by
      // position, and the open line's key is one the list never draws, so a
      // move onto the line in the list waits for the band to draw the list.
      const isOpened = opened === key
      const line = isOpened ? `close:${ref}` : key
      const first = item.kind === 'question' && isOpen ? `answer:${ref}.${letterAt(0)}` : `discuss:${ref}`

      return (
        <Box key={`line:${ref}`} flexDirection="column">
          <Box flexDirection="row" columnGap={1}>
            {/* The whole item is one plain Button, so a press anywhere on it
                opens its drawer: a click in fullscreen, Enter on the main
                screen. */}
            <Box flexGrow={1} flexShrink={1}>
              {/* An open drawer keeps its item inverted, as the pointer draws it. */}
              <Button key={line} plain onPress={() => toggle(key, line, isOpened ? key : first)}>
                <Text color={color} inverse={isOpened}>
                  -{' '}
                </Text>
                <Text color={color} strikethrough={isDismissed} inverse={isOpened}>
                  {item.subject}
                </Text>
              </Button>
            </Box>
            {!isOpen && item.deliveredAt === undefined && (
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
            <Box flexDirection="column" marginLeft={2}>
              {/* A blank line sets the item apart from its line, and the detail,
                  when there is one, sits between two. Another sets the actions,
                  or the lengths snooze offers, apart from what comes above. */}
              {item.detail !== '' && <Text key={`before-detail:${ref}`}> </Text>}
              {item.detail !== '' && <Markdown key={`detail:${ref}`} text={item.detail} />}
              {item.kind === 'question' && drawAnswers(item)}
              <Text key={`before-buttons:${ref}`}> </Text>
              {isAsking ? drawLengths(ref, lengths) : drawActions(ref, actions)}
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
                  {notes.map((note, at) => (
                    <Text color={note.color}>
                      {at > 0 ? ' · ' : ''}
                      {note.text}
                    </Text>
                  ))}
                </Text>
              </Box>
            )}
            {ageColumn(key, pr.updatedAt)}
          </Box>
          {/* The drawer starts where the state does, one column past the label. */}
          {opened === key && (
            <Box key={`drawer:${key}`} flexDirection="column" marginLeft={labelWidth + 1}>
              {/* Only the checks take a color, not the `·` before them. */}
              <Text wrap="truncate-end">
                {factsOf(pr, kinds)}
                {pr.checks !== null && ' · '}
                {pr.checks !== null && <Text color={CHECK_COLORS[pr.checks]}>checks {pr.checks}</Text>}
              </Text>
              {/* Under the facts come the actions, a line each, and then the
                  links. Each link has a line of its own: a terminal without
                  hyperlinks draws a link's URL after its text, and a shared
                  line would cut the URL off. */}
              {drawActions(key, [
                { name: 'discuss', label: 'discuss this', hotkey: 'd', onPress: () => fill(`${reference(pr)} `) },
                { name: 'dismiss', label: 'dismiss pull request', hotkey: 'x', onPress: () => void dismissPr($, pr) },
              ])}
              <Markdown key={`github:${key}`} text={`[open in github](${pr.url})`} />
              {pr.summaryUrl !== null && (
                <Markdown key={`projector:${key}`} text={`[open in projector](${pr.summaryUrl})`} />
              )}
            </Box>
          )}
        </Box>
      )
    }

    // An open item's drawer has the band to itself, until it is closed or the
    // item leaves the band. The divider and the blank line under it step
    // aside too, so the item is the top row and, in a terminal, the drawer
    // has two more rows before the engine, which caps the band below half the
    // terminal, starts to scroll.
    const focused = shownItems.find(item => opened === `item:${item.ref}`)
    if (focused !== undefined) {
      return <Box flexDirection="column">{drawItem(focused)}</Box>
    }

    return (
      <Box flexDirection="column">
        {divider}
        {byAuthor(shown.rows, shown.viewer).map(drawRow)}
        {failure !== undefined && <Text color="error">{failure}</Text>}
        {shownItems.length > 0 && (shown.rows.length > 0 || failure !== undefined) && (
          <Text key="before-items"> </Text>
        )}
        {[...shownItems].sort((a, b) => a.postedAt - b.postedAt).map(drawItem)}
      </Box>
    )
  })
}
