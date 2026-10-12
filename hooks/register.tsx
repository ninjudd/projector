import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Checks, LoopKind, PullRequest, Row, SeenLoop, Snapshot } from './types'
import { buildQuery, parseResponse, type Fetched } from './github'
import { expandPath, findWatchers, keyOf, parseTracked, type Env, type TrackedPr } from './parse'

type Engine = EngineInterface
type Loop = { kind: LoopKind; tracked: string }

const TICK_MS = 5_000
const POLL_MS = 30_000
const EMPTY: Snapshot = { rows: [], fetchedAt: null, error: null }

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

// The session keeps its state across a reload or an update of the plugin,
// so a value an earlier version wrote can have another shape.
const isKind = (kind: unknown): kind is LoopKind => KINDS.some(one => one === kind)
const current = (value: Snapshot): Snapshot => (Array.isArray(value.rows) ? value : EMPTY)

let envLoad: Promise<Env> | undefined
let inFlight: Promise<void> | undefined
let isQueued = false
let attemptedAt = 0
const unreadable = new Set<string>()

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
    return { prs: new Map(), error: error instanceof Error ? error.message : String(error) }
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
      const message = error instanceof Error ? error.message : String(error)
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

/** Moves the band's clock on, and polls GitHub once the last load is old enough. */
async function tick($: Engine): Promise<void> {
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

const ago = (seconds: number) =>
  seconds < 5 ? 'just now' : seconds < 60 ? `${seconds}s ago` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago`
    : seconds < 86_400 ? `${Math.floor(seconds / 3600)}h ago` : `${Math.floor(seconds / 86_400)}d ago`

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
    $.clock.every(TICK_MS, () => void tick($))
    soon($)
    return next(e)
  })

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

  // A watcher's line reaches the session as a task notification: the moment
  // a pull request it tracks has changed.
  on('prompt.submit', ($, e, next) => {
    if (e.origin.kind === 'task-notification') soon($)
    return next(e)
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
  // so each pull request takes one line, and its drawer two more.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !(await read($, isShown))) return next(e)

    const { Box, Markdown, Text } = $.ui.resolve(e)
    const shown = current(await read($, snapshot))
    const time = await read($, now)
    const opened = await read($, drawer)
    const label = labeler(shown.rows.map(row => row.pr))
    const reference = referencer(shown.rows.map(row => row.pr))
    const labelWidth = Math.max(0, ...shown.rows.map(row => label(row.pr).length))
    // A drawer starts where the titles do: past the label, `draft`, and a gap after each.
    const drawerIndent = labelWidth + 1 + 'draft'.length + 1
    const updated = shown.fetchedAt === null ? '' : ` updated ${ago(secondsSince(time, shown.fetchedAt))}`
    const failure = shown.error === null ? undefined : `GitHub: ${shown.error}`

    // The engine draws the band's `[-]` just past `bodyColumns` on the first
    // row, so a divider that fills the body ends at the mark.
    const divider = (
      <Text dimColor>
        {'─'.repeat(Math.max(0, e.props.bodyColumns - updated.length))}
        {updated}
      </Text>
    )

    if (shown.rows.length === 0) {
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
                onLinkPress={() => {
                  update($, drawer, open => (open === key ? null : key)).catch(() => undefined)
                }}
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

    return (
      <Box flexDirection="column">
        {divider}
        {shown.rows.map(drawRow)}
        {failure !== undefined && <Text color="error">{failure}</Text>}
      </Box>
    )
  })
}
