import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { LoopKind, LoopView, PullRequest, SeenLoop, Snapshot } from './types'
import { buildQuery, keyOf, parseResponse, type Fetched } from './github'
import { expandPath, findWatchers, parseTracked, type Env, type TrackedPr } from './parse'

type Engine = EngineInterface

const PANE = 'loop-prs'
const TITLE = 'Loop PRs'
const POLL_MS = 60_000
const EMPTY: Snapshot = { loops: [], fetchedAt: null, error: null }

const KIND_LABEL: Record<LoopKind, string> = {
  review: 'Review loop',
  fix: 'Fix loop',
  unknown: 'Tracked set',
}

const seen = atom({ plugin: 'projector', key: 'seenLoops' } as const, [])
const snapshot = atom({ plugin: 'projector', key: 'loopSnapshot' } as const, EMPTY)

let envLoad: Promise<Env> | undefined
let inFlight: Promise<void> | undefined

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

const remember = ($: Engine, loop: SeenLoop) =>
  update($, seen, list =>
    list.some(one => one.tracked === loop.tracked) ? list : [...list, loop],
  )

/**
 * Records the tracked file of each watcher a command starts. Answers whether
 * the command started one.
 */
async function adopt($: Engine, command: string): Promise<boolean> {
  const env = await envOf($)
  const watchers = findWatchers(command, env)
  for (const watcher of watchers) {
    if (watcher.tracked === undefined) {
      $.ui.toast(`loop-prs: cannot tell which file ${watcher.written} names; run /loop-prs <tracked file>`)
      continue
    }
    const tracked = (await canonical($, watcher.tracked)) ?? watcher.tracked
    await remember($, { kind: watcher.kind, tracked })
  }
  return watchers.length > 0
}

/** Watchers running anywhere on this machine, found by their arguments. */
async function runningLoops($: Engine, env: Env): Promise<SeenLoop[]> {
  let stdout: string
  try {
    ;({ stdout } = await $.process.run(['ps', '-A', '-o', 'args='], { timeoutMs: 10_000 }))
  } catch {
    return []
  }

  const loops = new Map<string, SeenLoop>()
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

  const loops = [
    ...own.map(loop => {
      const live = running.find(one => one.tracked === loop.tracked)
      const kind = loop.kind === 'unknown' && live !== undefined ? live.kind : loop.kind
      return { ...loop, kind, isOwn: true, isRunning: live !== undefined }
    }),
    ...running
      .filter(loop => !own.some(one => one.tracked === loop.tracked))
      .map(loop => ({ ...loop, isOwn: false, isRunning: true })),
  ]

  const sets = await Promise.all(
    loops.map(async loop => {
      try {
        return { loop, entries: parseTracked(await $.fs.read(loop.tracked)), error: null }
      } catch {
        return { loop, entries: [] as TrackedPr[], error: 'The tracked file is missing.' }
      }
    }),
  )

  const unique = new Map(sets.flatMap(set => set.entries).map(pr => [keyOf(pr), pr]))
  const fetched = await fetchPrs($, [...unique.values()])

  // When GitHub cannot be reached, keep drawing what it said last time.
  const last = await read($, snapshot)
  const previous = new Map(last.loops.flatMap(loop => loop.prs).map(pr => [keyOf(pr), pr]))
  const isFetched = fetched.error === null

  const views: LoopView[] = sets.map(({ loop, entries, error }) => {
    const prs: PullRequest[] = []
    const missing: string[] = []
    for (const entry of entries) {
      const key = keyOf(entry)
      const pr = fetched.prs.get(key) ?? (isFetched ? undefined : previous.get(key))
      if (pr !== undefined) {
        prs.push(pr)
      } else if (isFetched) {
        missing.push(key)
      }
    }
    return { ...loop, prs, missing, error }
  })

  const now = await $.clock.now()
  await update($, snapshot, () => ({
    loops: views,
    fetchedAt: isFetched ? now : last.fetchedAt,
    error: fetched.error,
  }))
}

/**
 * Reloads the loops and their pull requests. A timer's refresh runs only
 * while the pane is open, so a closed pane costs no GitHub calls.
 */
function refresh($: Engine, isForced = false): Promise<void> {
  inFlight ??= (async () => {
    try {
      if (isForced || (await $.ui.panes()).some(pane => pane.id === PANE)) await load($)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      await update($, snapshot, last => ({ ...last, error: message }))
    }
  })().finally(() => {
    inFlight = undefined
  })
  return inFlight
}

/** Opens the pane and loads it, from a timer so no hook waits on GitHub. */
function show($: Engine): void {
  $.clock.after(1, () => {
    $.ui.open({ id: PANE, title: TITLE }).catch(() => undefined)
    void refresh($, true)
  })
}

const clock = (ms: number) => {
  const date = new Date(ms)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'loop-prs',
      description: 'Show the open pull requests in a fix or review loop',
      argumentHint: '[tracked file]',
    })
    $.clock.every(POLL_MS, () => void refresh($))
    $.clock.after(1, () => void refresh($))
    return next(e)
  })

  // These hooks only watch: the catch hands back what the tool settled to,
  // so a failure here never runs the tool twice or refuses it.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny === undefined && e.command.includes('watch-') && (await adopt($, e.command))) {
      show($)
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: 'Monitor' }, async ($, e, next) => {
    const ran = await next(e)
    const command = e.command ?? ''
    if (ran.deny === undefined && command.includes('watch-') && (await adopt($, command))) {
      show($)
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('command.run', { command: 'loop-prs' }, async ($, e) => {
    const written = (e.args ?? '').trim()
    if (written !== '') {
      const expanded = expandPath(written, await envOf($))
      const tracked = expanded === undefined ? undefined : await canonical($, expanded)
      if (tracked === undefined) return { text: `loop-prs: no tracked file at ${written}.` }
      await remember($, { kind: 'unknown', tracked })
    }
    show($)
    return { text: 'Opened the Loop PRs pane.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Link, Text } = $.ui.resolve(e)
    const { loops, fetchedAt, error } = await read($, snapshot)
    const home = (await envOf($))('HOME')
    const shorten = (path: string) =>
      home !== undefined && path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path

    const drawPr = (pr: PullRequest, isOneRepo: boolean) => {
      const notes: { text: string; color: 'error' | 'warning' | 'success' }[] = []
      if (pr.isConflicting) notes.push({ text: 'conflict', color: 'error' })
      if (pr.reviewDecision === 'CHANGES_REQUESTED') notes.push({ text: 'changes requested', color: 'error' })
      if (pr.openThreads > 0) {
        notes.push({ text: `${pr.openThreads} open thread${pr.openThreads === 1 ? '' : 's'}`, color: 'warning' })
      }
      if (pr.reviewDecision === 'APPROVED') notes.push({ text: 'approved', color: 'success' })

      return (
        <Box key={keyOf(pr)} flexDirection="column">
          <Box flexDirection="row">
            <Link href={pr.url} label={isOneRepo ? `#${pr.number}` : keyOf(pr)} />
            <Text color={pr.isDraft ? 'warning' : 'success'}>{pr.isDraft ? ' draft ' : ' ready '}</Text>
            <Text wrap="truncate-end">{pr.title}</Text>
          </Box>
          {notes.length > 0 && (
            <Text>
              {'  '}
              {notes.map((note, index) => (
                <Text color={note.color}>
                  {index > 0 ? ' · ' : ''}
                  {note.text}
                </Text>
              ))}
            </Text>
          )}
        </Box>
      )
    }

    const drawLoop = (loop: LoopView) => {
      const open = loop.prs.filter(pr => pr.state === 'OPEN')
      const done = loop.prs.length - open.length
      const repos = new Set([...loop.prs.map(pr => pr.repo), ...loop.missing.map(key => key.split('#')[0])])
      const isOneRepo = repos.size === 1
      const where = loop.isOwn ? 'this session' : 'another session'

      return (
        <Box key={loop.tracked} flexDirection="column" marginBottom={1}>
          <Text wrap="truncate-end">
            <Text bold>{KIND_LABEL[loop.kind]}</Text>
            {isOneRepo ? ` · ${[...repos][0]}` : ''}
            <Text dimColor> · {where} · </Text>
            <Text color={loop.isRunning ? 'success' : 'warning'}>
              {loop.isRunning ? 'watching' : 'stopped'}
            </Text>
          </Text>
          <Text dimColor wrap="truncate-start">
            {shorten(loop.tracked)}
          </Text>
          {loop.error !== null && <Text color="error">{loop.error}</Text>}
          {loop.error === null && open.length === 0 && <Text dimColor>No open pull requests.</Text>}
          {open.map(pr => drawPr(pr, isOneRepo))}
          {loop.missing.length > 0 && (
            <Text color="warning">Not found: {loop.missing.join(', ')}</Text>
          )}
          {done > 0 && <Text dimColor>{done} merged or closed</Text>}
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        {loops.length === 0 && (
          <Text dimColor>
            {fetchedAt === null && error === null
              ? 'Looking for a loop…'
              : 'No fix or review loop is running. Start one, or run /loop-prs <tracked file>.'}
          </Text>
        )}
        {loops.map(drawLoop)}
        {error !== null && <Text color="error">GitHub: {error}</Text>}
        <Box flexDirection="row">
          <Text dimColor>{fetchedAt === null ? 'Not loaded yet ' : `Updated ${clock(fetchedAt)} `}</Text>
          <Button key="refresh" label="Refresh" hotkey="r" plain onPress={() => void refresh($, true)} />
        </Box>
      </Box>
    )
  })
}
