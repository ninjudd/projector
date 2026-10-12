import { atom, read, update } from 'claude-code'
import type { EngineInterface, FsEntry, Register } from 'claude-code'

import type { Checks, LoopKind, PullRequest, Question, Row, SeenLoop, Sending, Snapshot } from './types'
import { buildQuery, parseResponse, type Fetched } from './github'
import { expandPath, findWatchers, keyOf, parseTracked, type Env, type TrackedPr } from './parse'
import {
  ASK_DESCRIPTION,
  ASK_SCHEMA,
  arrange,
  askArguments,
  chosenRef,
  forMainLoop,
  isShownAt,
  letterAt,
  liveAgent,
  mayReference,
  needsDelivery,
  signatureOf,
  span,
  storeOf,
  typedLetter,
} from './questions'

type Engine = EngineInterface
type Loop = { kind: LoopKind; tracked: string }
type Ran = { exitCode: number; stdout: string; stderr: string }

const TICK_MS = 5_000
const POLL_MS = 30_000
const EMPTY: Snapshot = { rows: [], fetchedAt: null, error: null }
const ASK_TOOL = 'mcp__projector__ask'

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
const questions = atom({ plugin: 'projector', key: 'bandQuestions' } as const, [])
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
/** The questions an answer from the band is being recorded for, so a second press does nothing. */
const answering = new Set<string>()

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

/** Watchers running anywhere on this machine, found by their arguments. */
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
  const env = await envOf($)
  const own = await read($, seen)
  const running = await runningLoops($, env)

  // A file named by hand takes its kind from the watcher reading it, if one runs.
  const loops: Loop[] = [
    ...own.map(loop => ({
      tracked: loop.tracked,
      kind: isKind(loop.kind)
        ? loop.kind
        : (running.find(one => one.tracked === loop.tracked)?.kind ?? 'watching'),
    })),
    ...running.filter(loop => !own.some(one => one.tracked === loop.tracked)),
  ]

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

/** Puts a question the CLI just answered for in the band's list, in place of what it showed. */
const replace = ($: Engine, question: Question) =>
  update($, questions, list =>
    list.map(one => (one.ref === question.ref && one.session === question.session ? question : one)),
  )

/** The question a CLI command printed with `--json`, in the session it belongs to. */
const questionOf = (stdout: string, session: string): Question => ({
  ...(JSON.parse(stdout) as { question: Omit<Question, 'session'> }).question,
  session,
})

/** Takes the band's list from the store, and shows the band for a question it has not shown before. */
async function setQuestions($: Engine, list: Question[]): Promise<void> {
  let isNew = false
  await update($, questions, before => {
    const known = new Set(before.map(one => one.ref))
    isNew = list.some(one => one.answer === undefined && !known.has(one.ref))
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
    await setQuestions($, [])
  } else {
    const ran = await project($, ['question', 'list', '--all', '--json', '--session', session])
    if (ran.exitCode !== 0) return
    const { questions: list } = JSON.parse(ran.stdout) as { questions: Question[] }
    await setQuestions($, list)
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
  const ran = await project($, ['question', outcome, ref, '--claim', token, '--session', session, '--json']).catch(
    () => undefined,
  )
  if (ran?.exitCode === 0) await replace($, questionOf(ran.stdout, session))
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
    const ran = await project($, ['question', 'claim', ref, '--session', session, '--json'])
    // Another deliverer holds it, or it was delivered since the band read the store.
    if (ran.exitCode !== 0) return
    const claimed = JSON.parse(ran.stdout) as { token: string; text: string }
    await replace($, questionOf(ran.stdout, session))

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
    await project($, ['question', 'claim', entry.ref, '--renew', entry.token, '--session', entry.session]).catch(
      () => undefined,
    )
  }
}

/** Reads the store when it changed, renews the band's claims, and delivers each answer nobody is delivering. */
async function checkQuestions($: Engine): Promise<void> {
  if (isChecking) return
  isChecking = true
  try {
    const session = await $.session.id()
    await readStore($, session)
    await renewClaims($)
    const time = await $.clock.now()
    const pending = await read($, sending)
    for (const question of await read($, questions)) {
      if (question.session !== session || delivering.has(question.ref) || question.ref in pending) continue
      if (needsDelivery(question, time)) void deliver($, question)
    }
  } catch {
    // The next tick tries again.
  } finally {
    isChecking = false
  }
}

/** Checks the questions now rather than at the next tick. */
const checkSoon = ($: Engine) => $.clock.after(1, () => void checkQuestions($))

/** Records an answer from the band, a letter or typed text, or dismisses the question when no letter is given. */
async function record($: Engine, question: Question, letter?: string, text?: string): Promise<void> {
  const { ref, session } = question
  if (answering.has(ref)) return
  answering.add(ref)
  try {
    const args =
      letter === undefined
        ? ['question', 'dismiss', ref]
        : ['question', 'answer', `${ref}.${letter}`, ...(text === undefined ? [] : [`--text=${text}`])]
    const ran = await project($, [...args, '--session', session, '--json'])
    if (ran.exitCode === 0) {
      await replace($, questionOf(ran.stdout, session))
    } else if (ran.exitCode === 65) {
      // Answered elsewhere since the band read the store: read it again.
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

/** What the CLI says about each question a prompt refers to, or undefined when it names none. */
async function explain($: Engine, text: string): Promise<string | undefined> {
  try {
    const ran = await project($, ['question', 'context', '--session', await $.session.id()], text)
    const said = ran.exitCode === 0 ? ran.stdout.trim() : ''
    return said === '' ? undefined : said
  } catch {
    return undefined
  }
}

/**
 * Reads the questions whether or not the band is shown, then moves the band's
 * clock on and polls GitHub once the last load is old enough.
 */
async function tick($: Engine): Promise<void> {
  await checkQuestions($)
  if (!(await read($, isShown))) return
  const time = await $.clock.now()
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

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'projector',
      description: 'Show or hide Projector’s band of the pull requests your loops track',
      argumentHint: '[tracked file]',
    })
    // A session whose engine cannot serve the tool keeps the band.
    await $.tool
      .register({ name: 'ask', description: ASK_DESCRIPTION, inputSchema: ASK_SCHEMA, isDeferred: false })
      .catch(() => undefined)
    $.clock.every(TICK_MS, () => void tick($))
    soon($)
    checkSoon($)
    return next(e)
  })

  // The tool asks through the CLI, so a question asked here and one asked
  // from the shell land in the same store.
  on('tool.call', { tool: ASK_TOOL }, async ($, e) => {
    const args = askArguments(e, await $.session.id(), e.agentId)
    if (!Array.isArray(args)) return { deny: args.error }
    let ran: Ran
    try {
      ran = await project($, args)
    } catch (error) {
      return { deny: `project did not finish (${messageOf(error)}); ask in the conversation instead` }
    }
    if (ran.exitCode === 65) return { deny: errorOf(ran) }
    if (ran.exitCode !== 0) return { deny: `${errorOf(ran)}; run project upgrade` }
    const ref = ran.stdout.trim()
    checkSoon($)
    void $.ui.notify(`${ref} ${String(e.question)}`, { title: 'Projector question' }).catch(() => undefined)
    return {
      result: `Asked ${ref}. Keep working on anything the answer does not block; it arrives as a message that starts ANSWER ${ref} or DISMISSED ${ref}.`,
    }
  }).catch(() => ({ deny: 'Projector could not record the question; ask it in the conversation instead' }))

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

    // A reference typed in the prompt reaches the agent with its question.
    const said = mayReference(e.text) ? await explain($, e.text) : undefined
    return next(said === undefined ? e : { ...e, context: [...(e.context ?? []), said] })
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
    // The mobile app draws no field, so there a question takes a letter or a dismissal.
    const Input = 'Input' in elements ? elements.Input : undefined
    const shown = current(await read($, snapshot))
    const time = await read($, now)
    const opened = await read($, drawer)
    const asked = (await read($, questions)).filter(question => isShownAt(question, time))
    const open = asked.filter(question => question.answer === undefined).length
    const label = labeler(shown.rows.map(row => row.pr))
    const reference = referencer(shown.rows.map(row => row.pr))
    const labelWidth = Math.max(0, ...shown.rows.map(row => label(row.pr).length))
    // A drawer starts where the titles do: past the label, `draft`, and a gap after each.
    const drawerIndent = labelWidth + 1 + 'draft'.length + 1
    const heading = [
      open === 0 ? undefined : `${open} question${open === 1 ? '' : 's'}`,
      shown.fetchedAt === null ? undefined : `updated ${ago(secondsSince(time, shown.fetchedAt))}`,
    ]
      .filter(part => part !== undefined)
      .join(' · ')
    const updated = heading === '' ? '' : ` ${heading}`
    const failure = shown.error === null ? undefined : `GitHub: ${shown.error}`
    const toggle = (key: string) => {
      update($, drawer, was => (was === key ? null : key)).catch(() => undefined)
    }

    // The engine draws the band's `[-]` just past `bodyColumns` on the first
    // row, so a divider that fills the body ends at the mark.
    const divider = (
      <Text dimColor>
        {'─'.repeat(Math.max(0, e.props.bodyColumns - updated.length))}
        {updated}
      </Text>
    )

    if (shown.rows.length === 0 && asked.length === 0) {
      return (
        <Box flexDirection="column">
          {divider}
          <Box flexDirection="row" columnGap={1}>
            <Text dimColor wrap="truncate-end">
              {shown.fetchedAt === null && failure === undefined
                ? 'Looking for pull requests…'
                : 'No open pull requests are being watched or reviewed.'}
            </Text>
            {failure !== undefined && <Text color="error">{failure}</Text>}
          </Box>
        </Box>
      )
    }

    // A letter or a reference is a Button, which takes a click in fullscreen
    // and Enter on the main screen, drawn plain and underlined as a link. It
    // has no hotkey: a digit typed first in an empty prompt would press it.
    const link = (key: string, text: string, onPress: () => void) => (
      <Button key={key} plain onPress={onPress}>
        <Text color="suggestion" underline>
          {text}
        </Text>
      </Button>
    )

    const drawQuestion = (question: Question) => {
      const { ref } = question
      const key = `question:${ref}`
      const age = (
        <Box flexShrink={0}>
          <Text dimColor>{span(secondsSince(time, question.askedAt))}</Text>
        </Box>
      )

      if (question.answer !== undefined) {
        const isDismissed = 'dismissed' in question.answer
        return (
          <Box key={`line:${ref}`} flexDirection="row" columnGap={1}>
            <Box flexGrow={1} flexShrink={1}>
              <Text dimColor wrap="truncate-end">
                <Text strikethrough={isDismissed}>{chosenRef(question)}</Text>
                {question.deliveredAt === undefined ? ' sending ' : ' '}
                <Text strikethrough={isDismissed}>{question.question}</Text>
              </Text>
            </Box>
            {age}
          </Box>
        )
      }

      const typed = typedLetter(question)
      return (
        <Box key={`line:${ref}`} flexDirection="column">
          <Box flexDirection="row" columnGap={1}>
            <Box flexShrink={0}>{link(key, ref, () => toggle(key))}</Box>
            <Box flexGrow={1} flexShrink={1}>
              <Text wrap="truncate-end">{question.question}</Text>
            </Box>
            {age}
          </Box>
          {opened === key && (
            <Box flexDirection="column" marginLeft={ref.length + 1}>
              {question.detail !== '' && <Markdown key={`detail:${ref}`} text={question.detail} />}
              {question.choices.map((choice, index) => {
                const letter = letterAt(index)
                return (
                  <Box key={`choice:${ref}.${letter}`} flexDirection="row" columnGap={1}>
                    <Box flexShrink={0}>
                      {link(`answer:${ref}.${letter}`, `${letter}.`, () => void record($, question, letter))}
                    </Box>
                    <Box flexShrink={1}>
                      <Text>
                        {choice}
                        {index === 0 && <Text color="success"> (recommended)</Text>}
                      </Text>
                    </Box>
                  </Box>
                )
              })}
              {Input !== undefined && (
                <Box flexDirection="row" columnGap={1}>
                  <Box flexShrink={0}>
                    <Text>{typed}.</Text>
                  </Box>
                  <Box flexGrow={1}>
                    <Input
                      key={`answer:${ref}.${typed}`}
                      placeholder="Type something"
                      submitLabel="answer"
                      onSubmit={(value: string) => {
                        if (value.trim() !== '') void record($, question, typed, value.trim())
                      }}
                    />
                  </Box>
                </Box>
              )}
              <Box>
                <Button key={`dismiss:${ref}`} label="dismiss" onPress={() => void record($, question)} />
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
              column. A plain click on the label opens the drawer; a ctrl, alt
              or terminal-kept cmd click opens its Projector summary, or the
              pull request on GitHub when it has none. */}
          <Box flexDirection="row" columnGap={1}>
            <Box flexShrink={0} width={labelWidth}>
              <Markdown
                key={`pr:${key}`}
                text={`[${label(pr)}](${pr.summaryUrl ?? pr.url})`}
                onLinkPress={() => toggle(key)}
              />
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

    // A question about a pull request the band lists follows it; any other
    // question comes before the pull requests.
    const { loose, byPr } = arrange(shown.rows, asked)
    return (
      <Box flexDirection="column">
        {divider}
        {loose.map(drawQuestion)}
        {shown.rows.flatMap(row => [drawRow(row), ...(byPr.get(keyOf(row.pr)) ?? []).map(drawQuestion)])}
        {failure !== undefined && <Text color="error">{failure}</Text>}
      </Box>
    )
  })
}
