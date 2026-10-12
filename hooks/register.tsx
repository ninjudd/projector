import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { LoopKind, PullRequest, SeenLoop, Snapshot } from './types'
import { buildQuery, parseResponse, type Fetched } from './github'
import { expandPath, findWatchers, keyOf, parseTracked, type Env, type TrackedPr } from './parse'

type Engine = EngineInterface
type Loop = { kind: LoopKind; tracked: string }

const TICK_MS = 5_000
const POLL_MS = 30_000
const EMPTY: Snapshot = { watching: [], reviewing: [], fetchedAt: null, error: null }

const SECTIONS: { kind: LoopKind; title: string }[] = [
  { kind: 'watching', title: 'Watching' },
  { kind: 'reviewing', title: 'Reviewing' },
]

const seen = atom({ plugin: 'projector', key: 'seenLoops' } as const, [])
const snapshot = atom({ plugin: 'projector', key: 'loopSnapshot' } as const, EMPTY)
const now = atom({ plugin: 'projector', key: 'loopNow' } as const, 0)
const isShown = atom({ plugin: 'projector', key: 'isLoopBandShown' } as const, false)

// The session keeps its state across a reload or an update of the plugin,
// so a value an earlier version wrote can have another shape.
const isKind = (kind: unknown): kind is LoopKind => SECTIONS.some(section => section.kind === kind)
const current = (value: Snapshot): Snapshot =>
  Array.isArray(value.watching) && Array.isArray(value.reviewing) ? value : EMPTY

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
 * Records the tracked file of each watcher a command starts, and opens the
 * band when one is new. A loop restarts its watcher with the same command,
 * as a Monitor does each time it expires, so a known one opens nothing and
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
        $.ui.toast(`loop-prs: cannot tell which file ${watcher.written} names; run /loop-prs <tracked file>`)
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

  const unique = new Map(sets.flatMap(set => set.entries).map(pr => [keyOf(pr), pr]))
  const fetched = await fetchPrs($, [...unique.values()])

  // When GitHub cannot be reached, keep drawing what it said last time.
  const last = current(await read($, snapshot))
  const previous = new Map([...last.watching, ...last.reviewing].map(pr => [keyOf(pr), pr]))
  const isFetched = fetched.error === null

  const openIn = (kind: LoopKind): PullRequest[] => {
    const keys = new Set(sets.filter(set => set.kind === kind).flatMap(set => set.entries.map(keyOf)))
    return [...keys]
      .map(key => fetched.prs.get(key) ?? (isFetched ? undefined : previous.get(key)))
      .filter((pr): pr is PullRequest => pr !== undefined && pr.state === 'OPEN')
  }

  const time = await $.clock.now()
  await update($, snapshot, () => ({
    watching: openIn('watching'),
    reviewing: openIn('reviewing'),
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

const ago = (seconds: number) =>
  seconds < 5 ? 'just now' : seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ago`

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
      name: 'loop-prs',
      description: 'Show the open pull requests your watch and review loops track',
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

  // `/loop-prs` shows or hides the band; `/loop-prs <tracked file>` adds a
  // loop and shows it.
  on('command.run', { command: 'loop-prs' }, async ($, e) => {
    const written = (e.args ?? '').trim()
    if (written !== '') {
      const expanded = expandPath(written, await envOf($))
      const tracked = expanded === undefined ? undefined : await canonical($, expanded)
      if (tracked === undefined) return { text: `loop-prs: no tracked file at ${written}.` }
      await remember($, { tracked })
    } else if (await read($, isShown)) {
      await update($, isShown, () => false)
      return { text: 'Hid the loop PRs band.' }
    }
    show($)
    return { text: 'Showing the loop PRs band above the prompt.' }
  })

  // The band above the prompt spans the terminal and is as tall as its rows,
  // so each pull request takes one line.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !(await read($, isShown))) return next(e)

    const { Box, Link, Text } = $.ui.resolve(e)
    const shown = current(await read($, snapshot))
    const time = await read($, now)
    const rows = SECTIONS.flatMap(section =>
      shown[section.kind].map((pr, index) => ({ pr, section, isFirst: index === 0, notes: notesOf(pr) })),
    )
    const label = labeler(rows.map(row => row.pr))
    const labelWidth = Math.max(0, ...rows.map(row => label(row.pr).length))
    const sectionWidth = Math.max(...SECTIONS.map(section => section.title.length))
    const updated =
      shown.fetchedAt === null ? '' : ` updated ${ago(Math.max(0, Math.round((time - shown.fetchedAt) / 1000)))}`
    const failure = shown.error === null ? undefined : `GitHub: ${shown.error}`

    // The engine draws the band's `[-]` just past `bodyColumns` on the first
    // row, so a divider that fills the body ends at the mark.
    const divider = (
      <Text dimColor>
        {'─'.repeat(Math.max(0, e.props.bodyColumns - updated.length))}
        {updated}
      </Text>
    )

    if (rows.length === 0) {
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

    // Only the title gives way when the line is short. The section and
    // label columns are fixed, so every title starts in one column.
    return (
      <Box flexDirection="column">
        {divider}
        {rows.map(({ pr, section, isFirst, notes }) => (
          <Box key={`${section.kind}:${keyOf(pr)}`} flexDirection="row" columnGap={1}>
            <Box flexShrink={0} width={sectionWidth}>
              {isFirst && <Text bold>{section.title}</Text>}
            </Box>
            <Box flexShrink={0} width={labelWidth}>
              <Link href={pr.url} label={label(pr)} />
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
        ))}
        {failure !== undefined && <Text color="error">{failure}</Text>}
      </Box>
    )
  })
}
