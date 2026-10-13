import { atom, read, update } from 'claude-code'
import type { EngineInterface, FsEntry, Register } from 'claude-code'

import type { Checks, Item, LoopKind, Notice, PullRequest, Question, Row, SeenLoop, Sending, Snapshot } from './types'
import { buildQuery, parseResponse, type Fetched } from './github'
import { expandPath, findWatchers, keyOf, parseTracked, type Env, type TrackedPr } from './parse'
import {
  ASK_DESCRIPTION,
  ASK_SCHEMA,
  answersIn,
  askArguments,
  forMainLoop,
  isShownAt,
  letterAt,
  liveAgent,
  mayReference,
  needsDelivery,
  proposalOf,
  quoteOf,
  relayNote,
  signatureOf,
  span,
  storeOf,
  TELL_DESCRIPTION,
  TELL_SCHEMA,
  tellArguments,
  typedLetter,
  wokeBetween,
  type Proposal,
} from './inbox'

type Engine = EngineInterface
type Loop = { kind: LoopKind; tracked: string }
type Ran = { exitCode: number; stdout: string; stderr: string }

const TICK_MS = 5_000
const POLL_MS = 30_000
const EMPTY: Snapshot = { rows: [], fetchedAt: null, error: null }
const ASK_TOOL = 'mcp__projector__ask'
const TELL_TOOL = 'mcp__projector__tell'
const QUOTE = '“Your mind is a projector; it uses everything as a kind of screen, it projects itself on the screen.” — Osho'

// Watching first: a pull request both loops track is listed once, where its
// watch loop puts it.
const KINDS: readonly LoopKind[] = ['watching', 'reviewing']

const CHECK_COLORS: Record<Checks, 'success' | 'error' | 'warning'> = {
  passing: 'success',
  failing: 'error',
  pending: 'warning',
}

const seen = atom({ plugin: 'projector', key: 'seenLoops' } as const, [])
const snapshot = atom({ plugin: 'projector', key: 'bandSnapshot' } as const, EMPTY)
const now = atom({ plugin: 'projector', key: 'bandNow' } as const, 0)
const isShown = atom({ plugin: 'projector', key: 'isBandShown' } as const, false)
const drawer = atom({ plugin: 'projector', key: 'bandDrawer' } as const, null)
const items = atom({ plugin: 'projector', key: 'bandInbox' } as const, [])
const sending = atom({ plugin: 'projector', key: 'bandSending' } as const, {})

// The session keeps its state across a reload or an update of the plugin,
// so a value an earlier version wrote can have another shape.
const isKind = (kind: unknown): kind is LoopKind => KINDS.some(one => one === kind)
const current = (value: Snapshot): Snapshot => (Array.isArray(value.rows) ? value : EMPTY)

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
/** The items the band is dismissing or snoozing, so a second press does nothing. */
const answering = new Set<string>()
/** When the last tick ran, so an item whose snooze ends between two ticks wakes once. */
let tickedAt: number | undefined
/**
 * The answers questions' drawers put in the prompt, oldest first, so a sent
 * quote answers the question you chose when another shares its text.
 */
let proposed: Proposal[] = []

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
const project = ($: Engine, args: readonly string[], stdin?: string): Promise<Ran> =>
  $.process.run(['project', ...args], { timeoutMs: 15_000, ...(stdin === undefined ? {} : { stdin }) })

/** The CLI's error, as its last line of standard error says it. */
const errorOf = (ran: Ran) =>
  (ran.stderr.trim().split('\n').pop() ?? '').replace(/^project: /, '') || `project exited ${ran.exitCode}`

/** Records a loop, answering whether the session had not seen it before. */
async function remember($: Engine, loop: SeenLoop): Promise<boolean> {
  let isNew = false
  await update($, seen, list => {
    isNew = !list.some(one => one.tracked === loop.tracked)
    return isNew ? [...list, loop] : list
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
        $.ui.toast(`projector: cannot tell which file ${watcher.written} names; run /projector <tracked file>`)
      }
      continue
    }
    const tracked = (await canonical($, watcher.tracked)) ?? watcher.tracked
    isAdded = (await remember($, { kind: watcher.kind, tracked })) || isAdded
  }
  if (isAdded) show($)
}

/** Watchers running anywhere on this machine, found by their arguments, which give a hand-named file its kind. */
async function runningLoops($: Engine, env: Env): Promise<Loop[]> {
  let stdout: string
  try {
    ;({ stdout } = await $.process.run(['ps', '-A', '-o', 'args='], { timeoutMs: 10_000 }))
  } catch {
    return []
  }

  const loops = new Map<string, Loop>()
  for (const line of stdout.split('\n')) {
    if (!line.includes('watch-')) continue
    for (const watcher of findWatchers(line, env)) {
      // A process's arguments arrive expanded, and a relative path there is
      // relative to that process, so only an absolute one names its file.
      if (watcher.tracked === undefined || !watcher.tracked.startsWith('/')) continue
      const tracked = await canonical($, watcher.tracked)
      if (tracked !== undefined && !loops.has(tracked)) {
        loops.set(tracked, { kind: watcher.kind, tracked })
      }
    }
  }
  return [...loops.values()]
}

async function fetchPrs($: Engine, prs: readonly TrackedPr[]): Promise<Fetched> {
  if (prs.length === 0) return { prs: new Map(), error: null }
  const query = buildQuery(prs)
  try {
    const result = await $.process.run(['gh', 'api', 'graphql', '-f', `query=${query.text}`], {
      timeoutMs: 30_000,
    })
    return parseResponse(query, result)
  } catch (error) {
    return { prs: new Map(), error: messageOf(error) }
  }
}

async function load($: Engine): Promise<void> {
  // The band lists the session's own loops: the watchers it started, and the
  // tracked files named with `/projector <file>`. A file named by hand takes
  // its kind from the watcher reading it, so only then does the band look at
  // the machine's processes.
  const own = await read($, seen)
  const running = own.every(loop => isKind(loop.kind)) ? [] : await runningLoops($, await envOf($))
  const loops: Loop[] = own.map(loop => ({
    tracked: loop.tracked,
    kind: isKind(loop.kind) ? loop.kind : (running.find(one => one.tracked === loop.tracked)?.kind ?? 'watching'),
  }))

  // A tracked file that is gone belongs to a loop that ended.
  const sets = await Promise.all(
    loops.map(async loop => ({
      kind: loop.kind,
      entries: await $.fs.read(loop.tracked).then(parseTracked, () => [] as TrackedPr[]),
    })),
  )

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
    fetchedAt: isFetched ? time : last.fetchedAt,
    error: fetched.error,
  }))
  await update($, now, () => time)
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
const replace = ($: Engine, question: Item) =>
  update($, items, list =>
    list.map(one => (one.ref === question.ref && one.session === question.session ? question : one)),
  )

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
 * Takes the band's list from the store, and shows the band for an open item
 * it has not shown before, unless that item is still snoozed.
 */
async function setItems($: Engine, list: Item[]): Promise<void> {
  const time = await $.clock.now()
  let isNew = false
  await update($, items, before => {
    const known = new Set(before.map(one => one.ref))
    isNew = list.some(one => one.answer === undefined && !known.has(one.ref) && isShownAt(one, time))
    return list
  })
  if (isNew && !(await read($, isShown))) show($)
}

/**
 * Reads the session's questions when the store's directory changed since the
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
  const { session, token } = entry
  const ran = await project($, ['inbox', outcome, ref, '--claim', token, '--session', session, '--json']).catch(
    () => undefined,
  )
  if (ran?.exitCode === 0) await replace($, itemOf(ran.stdout, session))
  if (outcome === 'reopen') {
    void $.ui.toast(`projector: ${ref} did not reach its agent${reason ? ` (${reason})` : ''}; answer it again`)
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

/** Reads the store when it changed, renews the band's claims, and delivers each answer nobody is delivering. */
async function checkInbox($: Engine): Promise<void> {
  if (isChecking) return
  isChecking = true
  try {
    const session = await $.session.id()
    await readStore($, session)
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

/** Checks the questions now rather than at the next tick. */
const checkSoon = ($: Engine) => $.clock.after(1, () => void checkInbox($))

/**
 * Dismisses or snoozes an item from the band; a second press finds it changed
 * and does nothing. Snoozing closes the item's drawer, since the item leaves
 * the band.
 */
async function act($: Engine, item: Item, command: 'dismiss' | 'snooze'): Promise<void> {
  const { ref, session } = item
  if (answering.has(ref)) return
  answering.add(ref)
  try {
    const ran = await project($, ['inbox', command, ref, '--session', session, '--json'])
    if (ran.exitCode === 0) {
      await replace($, itemOf(ran.stdout, session))
      if (command === 'snooze') await update($, drawer, was => (was === `item:${ref}` ? null : was))
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
    answering.delete(ref)
  }
}

/**
 * Records an answer a sent prompt gave and marks it delivered, because the
 * prompt itself carries it to the main loop. The band's own deliverer leaves
 * the question alone meanwhile, so the agent hears the answer once.
 */
async function answerFromPrompt($: Engine, question: Question, letter: string, text?: string): Promise<void> {
  const { ref, session } = question
  delivering.add(ref)
  try {
    const typed = text === undefined ? [] : [`--text=${text}`]
    const answered = await project($, ['inbox', 'answer', `${ref}.${letter}`, ...typed, '--session', session, '--json'])
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

/** What the CLI says about each item a prompt refers to, or undefined when it names none. */
async function explain($: Engine, text: string): Promise<string | undefined> {
  try {
    const ran = await project($, ['inbox', 'context', '--session', await $.session.id()], text)
    const said = ran.exitCode === 0 ? ran.stdout.trim() : ''
    return said === '' ? undefined : said
  } catch {
    return undefined
  }
}

/**
 * Reads the inbox whether or not the band is shown, and brings a hidden band
 * back for an item whose snooze ended since the last tick. A shown band moves
 * its clock on, which returns that item to the list, and polls GitHub once
 * the last load is old enough.
 */
async function tick($: Engine): Promise<void> {
  // Read before the store, so a snooze that ends while the store is read
  // falls in the next tick's window.
  const time = await $.clock.now()
  await checkInbox($)
  const since = tickedAt ?? time
  tickedAt = time
  if (!(await read($, isShown))) {
    if (wokeBetween(await read($, items), since, time).length > 0) show($)
    return
  }
  await update($, now, () => time)
  if (time - attemptedAt >= POLL_MS) await refresh($)
}

/** Shows the band and loads it, from a timer so no hook waits on GitHub. */
function show($: Engine): void {
  $.clock.after(1, () => {
    void update($, isShown, () => true).then(() => refresh($, true))
  })
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
  if (pr.openThreads > 0) {
    notes.push({ text: `${pr.openThreads} open thread${pr.openThreads === 1 ? '' : 's'}`, color: 'warning' })
  }
  if (pr.reviewDecision === 'APPROVED') notes.push({ text: 'approved', color: 'success' })
  return notes
}

/** The drawer's facts, before its checks: what the loops do, who, where, and how big. */
function factsOf(pr: PullRequest, kinds: readonly LoopKind[]): string {
  const files = `${pr.changedFiles} file${pr.changedFiles === 1 ? '' : 's'}`
  return [
    kinds.join(' and '),
    pr.author === null ? undefined : `by ${pr.author}`,
    pr.branch || undefined,
    `+${pr.additions} −${pr.deletions} in ${files}`,
  ]
    .filter(part => part !== undefined)
    .join(' · ')
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
 * Runs the `project inbox` command one of the tools makes: the reference it
 * prints, or why the tool call is refused. `instead` says where the agent
 * turns when the command does not finish.
 */
async function post(
  $: Engine,
  args: string[] | { error: string },
  instead: string,
): Promise<{ ref: string } | { deny: string }> {
  if (!Array.isArray(args)) return { deny: args.error }
  let ran: Ran
  try {
    ran = await project($, args)
  } catch (error) {
    return { deny: `project did not finish (${messageOf(error)}); ${instead}` }
  }
  if (ran.exitCode === 65) return { deny: errorOf(ran) }
  if (ran.exitCode !== 0) return { deny: `${errorOf(ran)}; run project upgrade` }
  checkSoon($)
  return { ref: ran.stdout.trim() }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'projector',
      description: 'Show or hide Projector’s band of the pull requests your loops track',
      argumentHint: '[tracked file]',
    })
    // A session whose engine cannot serve the tools keeps the band.
    await $.tool
      .register({ name: 'ask', description: ASK_DESCRIPTION, inputSchema: ASK_SCHEMA, isDeferred: false })
      .catch(() => undefined)
    await $.tool
      .register({ name: 'tell', description: TELL_DESCRIPTION, inputSchema: TELL_SCHEMA, isDeferred: false })
      .catch(() => undefined)
    $.clock.every(TICK_MS, () => void tick($))
    soon($)
    checkSoon($)
    return next(e)
  })

  // The tools post through the CLI, so an item posted here and one posted
  // from the shell land in the same store.
  on('tool.call', { tool: ASK_TOOL }, async ($, e) => {
    const posted = await post($, askArguments(e, await $.session.id(), e.agentId), 'ask in the conversation instead')
    if ('deny' in posted) return posted
    const { ref } = posted
    void $.ui.notify(`${ref} ${String(e.question)}`, { title: 'Projector question' }).catch(() => undefined)
    return {
      result: `Asked ${ref}. Keep working on anything the answer does not block; it arrives as a message that starts ANSWER ${ref} or DISMISSED ${ref}.`,
    }
  }).catch(() => ({ deny: 'Projector could not record the question; ask it in the conversation instead' }))

  on('tool.call', { tool: TELL_TOOL }, async ($, e) => {
    const args = tellArguments(e, await $.session.id(), e.agentId)
    const posted = await post($, args, 'tell the person in the conversation instead')
    if ('deny' in posted) return posted
    const { ref } = posted
    void $.ui.notify(`${ref} ${String(e.notice)}`, { title: 'Projector notice' }).catch(() => undefined)
    return {
      result: `Posted ${ref}. It waits in Projector’s band until the person dismisses it; nothing comes back to you.`,
    }
  }).catch(() => ({ deny: 'Projector could not record the notice; tell the person in the conversation instead' }))

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

    // An answer the band put in the prompt, now sent: the prompt carries it to
    // the main loop, with a note naming the subagent that asked when one did,
    // and the band records it once the prompt has entered. Only a prompt you
    // sent, at the terminal or through Remote Control, answers: an agent's
    // message or a notification can quote a question it is still waiting on.
    const session = await $.session.id()
    const isYours = e.origin.kind === 'composer' || e.origin.kind === 'bridge'
    const given = isYours
      ? answersIn(e.text, (await read($, items)).filter(question => question.session === session), proposed)
      : []
    proposed = proposed.filter(proposal => !given.some(one => one.question.ref === proposal.ref))
    const asked = given.filter(one => one.question.agentId !== undefined)
    const agents = asked.length === 0 ? [] : await $.agent.list().catch(() => [])
    const notes = asked.map(one => relayNote(one.question, agents))

    // A reference typed in the prompt reaches the agent with its question.
    const said = mayReference(e.text) ? await explain($, e.text) : undefined
    const added = [...(said === undefined ? [] : [said]), ...notes]
    const entered = await next(added.length === 0 ? e : { ...e, context: [...(e.context ?? []), ...added] })
    if (entered.drop === undefined) {
      for (const one of given) void answerFromPrompt($, one.question, one.letter, one.text)
    }
    return entered
  }).catch(($, e, next) => next(e))

  // A turn may have pushed, replied or resolved threads.
  on('turn.complete', ($, e, next) => {
    soon($)
    return next(e)
  })

  // `/projector` shows or hides the band; `/projector <tracked file>` adds a
  // loop and shows it.
  on('command.run', { command: 'projector' }, async ($, e) => {
    const written = (e.args ?? '').trim()
    if (written !== '') {
      const expanded = expandPath(written, await envOf($))
      const tracked = expanded === undefined ? undefined : await canonical($, expanded)
      if (tracked === undefined) return { text: `projector: no tracked file at ${written}.` }
      await remember($, { tracked })
    } else if (await read($, isShown)) {
      await update($, isShown, () => false)
      return { text: 'Hid Projector’s band.' }
    }
    show($)
    return { text: 'Showing Projector’s band above the prompt.' }
  })

  // The band above the prompt spans the terminal and is as tall as its rows,
  // so each pull request and each question takes one line, and a drawer more.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !(await read($, isShown))) return next(e)

    const elements = $.ui.resolve(e)
    const { Box, Button, Markdown, Text } = elements
    const env = await envOf($)
    // The mobile app draws no field, so there a question takes a letter or a dismissal.
    const Input = 'Input' in elements ? elements.Input : undefined
    const shown = current(await read($, snapshot))
    const time = await read($, now)
    const opened = await read($, drawer)
    const shownItems = (await read($, items)).filter(item => isShownAt(item, time))
    const counts = (['question', 'notice'] as const).map(kind => {
      const count = shownItems.filter(item => item.kind === kind && item.answer === undefined).length
      return count === 0 ? undefined : `${count} ${kind}${count === 1 ? '' : 's'}`
    })
    const label = labeler(shown.rows.map(row => row.pr))
    const reference = referencer(shown.rows.map(row => row.pr))
    const labelWidth = Math.max(0, ...shown.rows.map(row => label(row.pr).length))
    // A drawer starts where the titles do: past the label, `draft`, and a gap after each.
    const drawerIndent = labelWidth + 1 + 'draft'.length + 1
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
    const toggle = (key: string) => {
      update($, drawer, was => (was === key ? null : key)).catch(() => undefined)
    }

    // The engine draws the band's `[-]` just past `bodyColumns` on the first
    // row, so a divider that fills the body ends at the mark, and a blank line
    // sets it apart from the rows under it. Only a terminal lays text out in
    // those columns; any other surface would wrap the rule, so it draws none.
    const divider =
      e.surface === 'terminal'
        ? [
            <Text key="divider" dimColor>
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
          ) : (
            <Text dimColor>{shown.fetchedAt === null ? 'Looking for pull requests…' : QUOTE}</Text>
          )}
        </Box>
      )
    }

    // A question's numbered options are Markdown links: a plain click runs
    // `onPress`, and a ctrl, alt or cmd click opens `href`. Markdown draws
    // only an `https:`, `http:` or `file:` link as one, so an option links its
    // question's pull request, or the question's own file in the store when it
    // has none.
    const store = (session: string) => storeOf(env, session) ?? ''
    const hrefOf = (question: Question) => {
      const pull = question.pr?.match(/^([^#]+)#(\d+)$/)
      return pull === null || pull === undefined
        ? `file://${encodeURI(`${store(question.session)}/${encodeURIComponent(question.ref)}.json`)}`
        : `https://github.com/${pull[1] ?? ''}/pull/${pull[2] ?? ''}`
    }
    const escaped = (text: string) => text.replace(/[\\[\]*_`~]/g, '\\$&')
    const link = (key: string, text: string, href: string, onPress: () => void) => (
      <Markdown key={key} text={`[${escaped(text)}](${href})`} onLinkPress={onPress} />
    )
    // An item names its pull request as the band labels it, or in full when
    // the band does not list it.
    const pullOf = (item: Item) => {
      const row = shown.rows.find(one => keyOf(one.pr) === item.pr?.toLowerCase())
      return row === undefined ? (item.pr ?? '') : label(row.pr)
    }

    // The items are a list below the pull requests, oldest first: a question
    // bulleted `-`, a notice `•`. A question's drawer numbers its options;
    // choosing one puts the question and that answer in the prompt as text,
    // for you to send or change, and sending it is what answers the question.
    // Each age sits in one column as wide as the widest, so the pull requests
    // before it end in one column too.
    const ageWidth = Math.max(0, ...shownItems.map(item => span(secondsSince(time, item.postedAt)).length))
    const fill = (text: string) => {
      $.prompt.fill({ text, mode: 'insert' }).catch(() => undefined)
    }
    const drawAnswers = (question: Question) => {
      const { ref, answer } = question
      const isOpen = answer === undefined
      const chosen = answer !== undefined && 'letter' in answer ? answer.letter : undefined
      const typed = typedLetter(question)
      const href = hrefOf(question)
      const propose = (answer: string) => {
        proposed = [...proposed.filter(proposal => proposal.ref !== ref), { ref, answer }]
        fill(`${proposalOf(question, answer)}\n`)
      }
      const option = (letter: string, content: JSX.Element) => {
        const number = `${letter.charCodeAt(0) - 'a'.charCodeAt(0) + 1}.`
        return (
          <Box key={`option:${ref}.${letter}`} flexDirection="row" columnGap={1}>
            <Box flexShrink={0}>
              {isOpen ? (
                link(`answer:${ref}.${letter}`, number, href, () =>
                  propose(question.choices[letter.charCodeAt(0) - 'a'.charCodeAt(0)] ?? ''),
                )
              ) : (
                <Text color={letter === chosen ? 'success' : undefined} dimColor={letter !== chosen}>
                  {number}
                </Text>
              )}
            </Box>
            <Box flexShrink={1}>{content}</Box>
          </Box>
        )
      }

      return [
        <Text key={`before-answers:${ref}`}> </Text>,
        ...question.choices.map((choice, index) => {
          const letter = letterAt(index)
          return option(
            letter,
            <Text dimColor={!isOpen && letter !== chosen}>
              {choice}
              {index === 0 && <Text color="success"> (recommended)</Text>}
              {letter === chosen && <Text color="success"> ✓</Text>}
            </Text>,
          )
        }),
        chosen === typed && answer !== undefined && 'text' in answer
          ? option(
              typed,
              <Text>
                {answer.text}
                <Text color="success"> ✓</Text>
              </Text>,
            )
          : isOpen &&
            Input !== undefined &&
            option(
              typed,
              <Input
                key={`answer:${ref}.${typed}`}
                placeholder="Type something"
                submitLabel="answer"
                onSubmit={(value: string) => {
                  if (value.trim() !== '') propose(value.trim())
                }}
              />,
            ),
      ]
    }
    const drawItem = (item: Item) => {
      const { ref, answer } = item
      const key = `item:${ref}`
      const isOpen = answer === undefined
      const isDismissed = answer !== undefined && 'dismissed' in answer
      const pull = pullOf(item)
      const actions = [
        { name: 'discuss', onPress: () => fill(`${quoteOf(item)} `) },
        ...(isOpen
          ? [
              { name: 'dismiss', onPress: () => void act($, item, 'dismiss') },
              { name: 'snooze', onPress: () => void act($, item, 'snooze') },
            ]
          : []),
      ]

      return (
        <Box key={`line:${ref}`} flexDirection="column">
          <Box flexDirection="row" columnGap={1}>
            {/* The whole item is one plain Button, so a press anywhere on it
                opens its drawer: a click in fullscreen, Enter on the main
                screen. */}
            <Box flexGrow={1} flexShrink={1}>
              {/* An open drawer keeps its item inverted, as the pointer draws it. */}
              <Button key={key} plain onPress={() => toggle(key)}>
                <Text dimColor={!isOpen} inverse={opened === key}>
                  {item.kind === 'notice' ? '•' : '-'}{' '}
                </Text>
                <Text dimColor={!isOpen} strikethrough={isDismissed} inverse={opened === key}>
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
            <Box flexShrink={0} width={ageWidth} justifyContent="flex-end">
              <Text color="subtle">{span(secondsSince(time, item.postedAt))}</Text>
            </Box>
          </Box>
          {opened === key && (
            <Box flexDirection="column" marginLeft={2}>
              {/* A blank line sets the item apart from its line, and the detail,
                  when there is one, sits between two. */}
              {item.detail !== '' && <Text key={`before-detail:${ref}`}> </Text>}
              {item.detail !== '' && <Markdown key={`detail:${ref}`} text={item.detail} />}
              {item.kind === 'question' && drawAnswers(item)}
              <Text key={`before-buttons:${ref}`}> </Text>
              <Box flexDirection="row" columnGap={1}>
                {actions.flatMap(({ name, onPress }, at) => [
                  ...(at === 0 ? [] : [<Text key={`between:${ref}.${at}`} dimColor>|</Text>]),
                  <Button key={`${name}:${ref}`} plain onPress={onPress}>
                    <Text>{name}</Text>
                  </Button>,
                ])}
              </Box>
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
              column. The label is a plain Button rather than a link, so it
              takes a click in fullscreen and Enter on the main screen, and no
              terminal ever draws a URL beside it. */}
          <Box flexDirection="row" columnGap={1}>
            <Box flexShrink={0} width={labelWidth}>
              <Button key={`pr:${key}`} plain label={label(pr)} onPress={() => toggle(key)} />
            </Box>
            <Box flexShrink={0}>
              <Text color={pr.isDraft ? 'warning' : 'success'}>{pr.isDraft ? 'draft' : 'ready'}</Text>
            </Box>
            <Box flexShrink={1}>
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
          </Box>
          {opened === key && (
            <Box flexDirection="column" marginLeft={drawerIndent}>
              <Text wrap="truncate-end">
                <Text dimColor>{factsOf(pr, kinds)}</Text>
                {pr.checks !== null && <Text color={CHECK_COLORS[pr.checks]}> · checks {pr.checks}</Text>}
                {pr.updatedAt !== null && <Text dimColor> · updated {ago(secondsSince(time, pr.updatedAt))}</Text>}
              </Text>
              <Box flexDirection="row" columnGap={2}>
                <Markdown
                  key={`discuss:${key}`}
                  text={`[discuss](${pr.url})`}
                  onLinkPress={() => {
                    $.prompt.fill({ text: `${reference(pr)} `, mode: 'insert' }).catch(() => undefined)
                  }}
                />
                <Markdown key={`github:${key}`} text={`[github](${pr.url})`} />
                {pr.summaryUrl !== null && <Markdown key={`projector:${key}`} text={`[projector](${pr.summaryUrl})`} />}
              </Box>
            </Box>
          )}
        </Box>
      )
    }

    // An open item's drawer has the band to itself, until it is closed or the
    // item leaves the band. The divider and the blank line under it step
    // aside too, so the item is the top row and the drawer has two more rows
    // before the engine, which caps the band below half the terminal, starts
    // to scroll.
    const focused = shownItems.find(item => opened === `item:${item.ref}`)
    if (focused !== undefined) {
      return <Box flexDirection="column">{drawItem(focused)}</Box>
    }

    return (
      <Box flexDirection="column">
        {divider}
        {shown.rows.map(drawRow)}
        {failure !== undefined && <Text color="error">{failure}</Text>}
        {shownItems.length > 0 && shown.rows.length > 0 && <Text key="before-items"> </Text>}
        {[...shownItems].sort((a, b) => a.postedAt - b.postedAt).map(drawItem)}
      </Box>
    )
  })
}
