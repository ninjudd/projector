import type { AgentInfo, FsEntry, On } from 'claude-code'
import { expect, mock, test, type Engine } from 'claude-code/testing'

import type { Notice, Question } from './types'

const HOME = '/home/ada'
const REVIEW = `${HOME}/loops/review/tracked`
const WATCH = `${HOME}/loops/watch/tracked`
const REVIEW_COMMAND = 'scripts/watch-prs.sh --tracked ~/loops/review/tracked --state ~/loops/review/state'
const WATCH_COMMAND = 'bash /plugin/skills/start-watch-loop/scripts/watch-threads.sh --tracked ~/loops/watch/tracked --state s'

const BAND = {
  plugin: 'projector',
  surface: 'terminal',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 12,
    bodyColumns: 120,
    scroll: { offset: 0, bodyRows: 12 },
    view: {},
  },
} as const

const COMMAND = {
  command: 'projector',
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 120 },
} as const

const NOW = Date.UTC(2026, 9, 12, 12, 0)
/** The account the band's `gh` is signed in as. */
const VIEWER = 'ada'
const SEVEN = 'https://github.com/x/y/pull/7'
const SUMMARY = 'https://example.com/site/reviews/7'

const pr = (number: number, title: string, fields: Record<string, unknown> = {}) => ({
  number,
  title,
  url: `https://github.com/o/r/pull/${number}`,
  state: 'OPEN',
  isDraft: false,
  reviewDecision: null,
  mergeable: 'MERGEABLE',
  reviewThreads: { nodes: [] },
  ...fields,
})

// What GitHub answers for each repository, whatever alias the query gives it.
const BY_REPO: Record<string, Record<string, unknown>> = {
  'o/r': {
    p1: pr(1, 'Signed-off change'),
    p2: pr(2, 'Change under review', {
      isDraft: true,
      reviewThreads: { nodes: [{ isResolved: false }] },
      updatedAt: new Date(NOW - 45_000).toISOString(),
    }),
    p3: pr(3, 'Already merged', { state: 'MERGED' }),
    p4: pr(4, 'First change by grace', { author: { login: 'grace' } }),
    p5: pr(5, 'Change by bob', { author: { login: 'bob' } }),
    p6: pr(6, 'Second change by grace', { author: { login: 'grace' } }),
    p9: pr(9, 'Another change of my own', { author: { login: VIEWER } }),
  },
  'x/y': {
    p7: pr(7, 'Conflicting change', {
      url: SEVEN,
      isDraft: true,
      mergeable: 'CONFLICTING',
      reviewThreads: { nodes: [{ isResolved: false }, { isResolved: false }] },
      author: { login: 'ada' },
      headRefName: 'fix-branch',
      additions: 12,
      deletions: 3,
      changedFiles: 2,
      updatedAt: new Date(NOW - 5 * 60_000).toISOString(),
      commits: { nodes: [{ commit: { statusCheckRollup: { state: 'SUCCESS' } } }] },
      body: `Why.\n\n<!-- projector-summary v=1 sha=${'b'.repeat(40)} -->\n📽️ **Projector summary** of bbbbbbb: ${SUMMARY}\n`,
    }),
  },
}

/** Answers a GraphQL query with the signed-in login and each repository it names, under the alias it gave. */
function answer(query: string): string {
  const data: Record<string, unknown> = query.includes('viewer { login }') ? { viewer: { login: VIEWER } } : {}
  for (const [, alias, owner, name] of query.matchAll(/(r\d+): repository\(owner: "([^"]+)", name: "([^"]+)"\)/g)) {
    data[alias ?? ''] = BY_REPO[`${owner}/${name}`] ?? null
  }
  return JSON.stringify({ data })
}

const ran = (stdout: string) => ({
  exitCode: 0,
  stdout,
  stderr: '',
  isStdoutTruncated: false,
  isStderrTruncated: false,
})

/** What the engine draws in the band when the plugin hands it on. */
const ENGINE_BAND = 'the engine’s own band'

type Ran = ReturnType<typeof ran>

/**
 * What a `project` command comes to: its output, or `{ deny }` for a call that
 * rejects, as one still running at its timeout does.
 */
type Outcome = Ran | { deny: string }

const TAGLINE = 'Our minds are projectors, casting ideas onto the world.'

/** GitHub's answer to a query, where a test needs another, or needs it late. */
type Beneath = { github?: (query: string) => Ran | Promise<Ran> }

/** Answers the files, GitHub, prompt and Bash calls beneath the plugin, and `project` when given. */
function world(
  on: On,
  files: Record<string, string>,
  project?: (argv: string[]) => Outcome | Promise<Outcome>,
  { github = query => ran(answer(query)) }: Beneath = {},
) {
  const commands: string[][] = []
  const filled: string[] = []
  on('fs.stat', ($, e) => ({
    value: { kind: 'file' as const, size: 1, mtimeMs: 0, isLink: false, realPath: e.path },
  }))
  on('fs.read', ($, e) => ({ value: files[e.path] ?? '' }))
  on('process.run', ($, e) => {
    commands.push([...e.argv])
    const answered = (outcome: Outcome) => ('deny' in outcome ? outcome : { value: outcome })
    const outcome =
      e.argv[0] === 'project' && project !== undefined ? project([...e.argv]) : github(e.argv.join(' '))
    return outcome instanceof Promise ? outcome.then(answered) : answered(outcome)
  })
  on('prompt.fill', ($, e) => {
    filled.push(`${e.mode}:${e.text}`)
    return { isFilled: true }
  })
  on('tool.call', { tool: 'Bash' }, () => ({
    result: { stdout: '', stderr: '', interrupted: false },
    text: '',
  }))
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{ENGINE_BAND}</Text>
  })
  return { commands, filled }
}

test('draws one line per open pull request of the session’s own loops', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const { commands } = world(on, { [REVIEW]: 'o/r#1\no/r#2\no/r#3\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  // The watch loop runs in another session, so the band does not list it.
  const gh = commands.filter(argv => argv[0] === 'gh')
  expect(gh.length).toBe(1)
  expect(gh[0]?.join(' ')).not.toContain('repository(owner: "x", name: "y")')

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Change under review' })).toBeDefined()
  // Open threads show as their count after a `⋮`, in yellow.
  expect((await ui.findAll({ type: 'Text', text: '⋮1' })).map(found => found.props.color)).toContain('warning')
  expect(await ui.find({ type: 'Text', text: /open thread/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  const divider = await ui.find({ type: 'Text', text: /^─+ updated (just now|\d+s ago)$/ })
  expect(divider?.text.length).toBe(BAND.props.bodyColumns)
  // `dimColor` does not dim in every terminal, so muted text takes the theme's subtle color.
  expect(divider?.props.color).toBe('subtle')
  // A blank line sets the divider apart from the rows under it.
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts[texts.indexOf(divider?.text ?? '') + 1]).toBe(' ')
  expect(await ui.find({ type: 'Text', text: 'Already merged' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /Watching|Reviewing|tracked|session|Refresh/ })).toBeUndefined()
  expect(await ui.find({ key: 'github:o/r#1' })).toBeUndefined()
  await ui.unmount()
})

test('draws no divider on a surface that does not lay text out in columns', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts.filter(text => text.includes('─'))).toEqual([])
  expect(texts[0]).not.toBe(' ')
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  await ui.unmount()
})

test('a press on a pull request’s number opens its drawer, and discuss puts it in the prompt', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const { filled } = world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  // The number is a plain button, so no terminal draws a URL beside it.
  expect((await ui.find({ type: 'Button', key: 'pr:x/y#7' }))?.props.label).toBe('y#7')
  expect((await ui.find({ type: 'Button', key: 'pr:o/r#1' }))?.props.label).toBe('r#1')
  expect(await ui.find({ type: 'Text', text: 'conflict' })).toBeDefined()
  // The threads' count comes after the other notes, beside the age.
  const notes = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(notes.indexOf(' · ⋮2')).toBeGreaterThan(notes.indexOf('conflict'))

  await ui.press({ key: 'pr:x/y#7' })
  // The drawer's facts name no author, whoever wrote the pull request.
  const facts = await ui.find({ type: 'Text', text: /^watching · fix-branch · \+12 −3 in 2 files$/ })
  expect(facts?.props.color).toBe('subtle')
  expect(await ui.find({ type: 'Text', text: /\bby\b/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: ' · checks passing' })).toBeDefined()
  // The pull request's age ends its line, so the drawer does not repeat it.
  expect(await ui.find({ type: 'Text', text: /updated 5m ago/ })).toBeUndefined()
  // The drawer starts where the state does: past the three-column labels `r#1` and `y#7` and the gap after them.
  const opened = await ui.find({ key: 'drawer:x/y#7' })
  expect(opened).toBeDefined()
  expect(opened?.props.marginLeft).toBe(4)
  // Under the facts, each link has a line of its own, so a terminal without hyperlinks shows the whole URL, and the actions' row comes last.
  const lines = (opened?.children ?? []) as { type: string; props: { key?: string } }[]
  expect(lines.map(line => line.props.key ?? line.type)).toEqual(['Text', 'github:x/y#7', 'projector:x/y#7', 'Box'])
  expect(await ui.find({ key: 'github:x/y#7' })).toBeDefined()
  expect((await ui.find({ key: 'projector:x/y#7' }))?.props.text).toBe(`[projector](${SUMMARY})`)
  expect(await ui.find({ key: 'projector:o/r#1' })).toBeUndefined()

  // discuss is a plain Button, so Enter fills the prompt on the main screen too.
  expect((await ui.find({ type: 'Button', key: 'discuss:x/y#7' }))?.props.plain).toBe(true)
  await ui.press({ key: 'discuss:x/y#7' })
  expect(filled).toEqual(['insert:x/y#7 '])

  await ui.press({ key: 'pr:x/y#7' })
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  await ui.unmount()
})

test('ends each pull request’s line with its age, in one column as wide as the widest', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\no/r#2\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  // Ages of different widths sit right-aligned in one column, so the titles and notes before them line up.
  const ages = await Promise.all(['o/r#1', 'o/r#2', 'x/y#7'].map(key => ui.find({ key: `age:${key}` })))
  expect(ages.map(age => [age?.props.width, age?.props.justifyContent])).toEqual([
    [3, 'flex-end'],
    [3, 'flex-end'],
    [3, 'flex-end'],
  ])
  expect((await ui.find({ type: 'Text', text: /^45s$/ }))?.props.color).toBe('subtle')
  expect((await ui.find({ type: 'Text', text: /^5m$/ }))?.props.color).toBe('subtle')
  // A pull request GitHub gave no time for shows no age.
  expect(ages[0]?.text).toBe('')
  await ui.unmount()
})

test('lists your pull requests first, then everyone else’s by author, naming theirs before the title', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const { commands } = world(on, { [REVIEW]: 'o/r#4\no/r#9\no/r#5\no/r#6\n', [WATCH]: 'x/y#7\n' })

  // One command starts both watchers, so the band loads once.
  await $.tool.call({ tool: 'Bash', command: `${REVIEW_COMMAND} & ${WATCH_COMMAND}` })
  await clock.advance(1)
  await clock.settle()

  // The signed-in login comes in the one query that asks for the pull requests.
  const gh = commands.filter(argv => argv[0] === 'gh')
  expect(gh).toHaveLength(1)
  expect(gh[0]?.join(' ')).toContain('viewer { login }')

  const ui = await $.ui.mount(BAND)
  // Yours keep their order, then bob's, then grace's two in theirs, with nothing between the groups.
  const keys = (await ui.findAll({ type: 'Button' })).map(button => button.key ?? '').filter(key => key.startsWith('pr:'))
  expect(keys).toEqual(['pr:x/y#7', 'pr:o/r#9', 'pr:o/r#5', 'pr:o/r#4', 'pr:o/r#6'])
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts.filter(text => text === ' ')).toHaveLength(1)

  // Only someone else's line names its author, in `subtle`, in a column as wide as the longest login.
  expect(await ui.find({ key: 'author:x/y#7' })).toBeUndefined()
  expect(await ui.find({ key: 'author:o/r#9' })).toBeUndefined()
  const authors = await Promise.all(['o/r#5', 'o/r#4', 'o/r#6'].map(key => ui.find({ key: `author:${key}` })))
  expect(authors.map(author => [author?.text, author?.props.width])).toEqual([
    ['bob', 5],
    ['grace', 5],
    ['grace', 5],
  ])
  for (const name of await ui.findAll({ type: 'Text', text: /^(bob|grace)$/ })) expect(name.props.color).toBe('subtle')
  await ui.unmount()
})

/** Records each toast the band shows. */
function toastsOf(on: On): string[] {
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  return toasts
}

test('dismiss drops a pull request from each loop that tracks it, and its line goes', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const files: Record<string, string> = { [REVIEW]: 'o/r#1\nx/y#7\n', [WATCH]: 'x/y#7\n' }
  // `project loop drop <reference> --tracked <file>` appends the exclusion, as the CLI does.
  const { commands } = world(on, files, ([, , , reference = '', , tracked = '']) => {
    files[tracked] = `${files[tracked] ?? ''}!${reference}\n`
    return ran(`dropped ${reference} from ${tracked}\n`)
  })
  const toasts = toastsOf(on)
  await $.tool.call({ tool: 'Bash', command: `${REVIEW_COMMAND} & ${WATCH_COMMAND}` })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'pr:x/y#7' })
  // The drawer's actions share one row, as an item's do.
  expect(await ui.findAll({ type: 'Text', text: '|' })).toHaveLength(1)
  expect((await ui.find({ type: 'Button', key: 'dismiss:x/y#7' }))?.props.plain).toBe(true)

  await ui.press({ key: 'dismiss:x/y#7' })
  await clock.settle()

  expect(commands.filter(argv => argv[0] === 'project')).toEqual([
    ['project', 'loop', 'drop', 'x/y#7', '--tracked', REVIEW],
    ['project', 'loop', 'drop', 'x/y#7', '--tracked', WATCH],
  ])
  expect(toasts).toEqual(['projector: stopped watching and reviewing x/y#7'])
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  await ui.unmount()
})

test('a dismiss the CLI refuses says why and keeps the line', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const refused = { ...ran(''), exitCode: 66, stderr: `project: no tracked file at ${WATCH}\n` }
  world(on, { [WATCH]: 'x/y#7\n' }, () => refused)
  const toasts = toastsOf(on)
  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'pr:x/y#7' })
  await ui.press({ key: 'dismiss:x/y#7' })
  await clock.settle()

  expect(toasts).toEqual([`projector: no tracked file at ${WATCH}`])
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeDefined()
  await ui.unmount()
})

test('opening one drawer closes the one that was open', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'pr:x/y#7' })
  await ui.press({ key: 'pr:o/r#1' })
  expect(await ui.find({ key: 'github:o/r#1' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  await ui.unmount()
})

test('lists a pull request both loops track once, as watched and reviewed', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'pr:o/r#1' })
  expect(await ui.find({ type: 'Text', text: /^watching and reviewing/ })).toBeDefined()
  await ui.unmount()
})

test('`/projector` only shows or hides the band, whatever follows it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const { commands } = world(on, { [WATCH]: 'x/y#7\n' })

  await $.command.run({ ...COMMAND, args: '~/loops/watch/tracked' })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: ` ${TAGLINE}` })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  expect(commands).toEqual([])
  await ui.unmount()
})

test('leaves out a tracked file an earlier version was given by hand, until the session starts its watcher', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'x/y#7\n' })
  let loops: unknown = [{ tracked: WATCH }, { kind: 'reviewing', tracked: REVIEW }]
  let version = 1
  on('state.get', { plugin: 'projector', key: 'seenLoops' }, () => ({ value: { value: loops as never, version } }))
  on('state.set', { plugin: 'projector', key: 'seenLoops' }, ($, e) => {
    loops = e.value
    version += 1
    return { value: { isSet: true as const, version } }
  })

  await $.command.run(COMMAND)
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()

  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(loops).toEqual([
    { kind: 'reviewing', tracked: REVIEW },
    { kind: 'watching', tracked: WATCH },
  ])
  await ui.unmount()
})

test('stays hidden until the session starts a watcher or asks for it', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'x/y#7\n' })

  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: ENGINE_BAND })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeUndefined()
  await ui.unmount()
})

test('keeps a band you hid hidden when a known watcher starts again', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await $.command.run(COMMAND)
  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: ENGINE_BAND })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeUndefined()
  await ui.unmount()
})

test('says once when it cannot tell which tracked file a watcher reads', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, {})
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })

  const command = 'bash scripts/watch-prs.sh --tracked "$LOOP/tracked" --state s'
  await $.tool.call({ tool: 'Bash', command })
  await $.tool.call({ tool: 'Bash', command })
  await clock.settle()

  expect(toasts).toEqual(['projector: cannot tell which file $LOOP/tracked names, so the band does not list that loop'])
})

test('draws over a snapshot an earlier version left in the session', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n' })
  const earlier = { watching: [], reviewing: [], fetchedAt: null, error: null }
  on('state.get', { plugin: 'projector', key: 'bandSnapshot' }, () => ({
    value: { value: earlier as never, version: 1 },
  }))

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Looking for pull requests…' })).toBeDefined()
  await ui.unmount()
})

test('shows its tagline, one space in and in the subtle color, when no loop tracks an open pull request', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, {})

  await $.command.run(COMMAND)
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  // `dimColor` does not dim in every terminal, so the tagline takes the theme's subtle color.
  expect((await ui.find({ type: 'Text', text: ` ${TAGLINE}` }))?.props.color).toBe('subtle')
  expect(await ui.find({ type: 'Text', text: /No open pull requests/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeDefined()
  await ui.unmount()

  const desktop = await $.ui.mount({ ...BAND, surface: 'desktop' })
  const texts = (await desktop.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts[0]).toBe(` ${TAGLINE}`)
  expect(texts.filter(text => text.includes('─'))).toEqual([])
  await desktop.unmount()
})

/** What `gh` answers when it cannot reach GitHub. */
const DOWN = { ...ran(''), exitCode: 1, stderr: 'read: connection reset by peer\n' }

test('says why GitHub failed only when the band has no pull request from it to draw', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  let isDown = true
  world(on, { [REVIEW]: 'o/r#1\n' }, undefined, { github: query => (isDown ? DOWN : ran(answer(query))) })
  // Hiding the band and showing it again loads it again.
  const reload = async () => {
    await $.command.run(COMMAND)
    await $.command.run(COMMAND)
    await clock.advance(1)
    await clock.settle()
  }

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'GitHub: read: connection reset by peer' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Looking for pull requests…' })).toBeUndefined()

  isDown = false
  await reload()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()

  // A later failure keeps what GitHub said last, and says nothing of it.
  isDown = true
  await reload()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^GitHub:/ })).toBeUndefined()
  await ui.unmount()
})

test('says why GitHub failed for a loop added after the band loaded with nothing to track', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const files: Record<string, string> = {}
  world(on, files, undefined, { github: () => DOWN })

  // A band shown before any loop tracks a pull request asks GitHub nothing.
  await $.command.run(COMMAND)
  await clock.advance(1)
  await clock.settle()

  files[REVIEW] = 'o/r#1\n'
  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'GitHub: read: connection reset by peer' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: TAGLINE })).toBeUndefined()
  await ui.unmount()
})

const SESSION = 's1'
const STORE = `${HOME}/.local/state/projector/inbox/${SESSION}`
/** What the CLI says, exiting 75, when another command holds the store's lock. */
const BUSY = `another command holds ${STORE}/.lock; try again once it finishes`
const TICK = 5_000

type HeldQuestion = Omit<Question, 'session'>
type HeldNotice = Omit<Notice, 'session'>
type Held = HeldQuestion | HeldNotice
type Clock = ReturnType<typeof mock.clock>

const HOUR = 3_600_000

const held = (fields: Partial<HeldQuestion> = {}): HeldQuestion => ({
  ref: '1',
  kind: 'question',
  number: 1,
  pr: 'x/y#7',
  postedAt: NOW - 120_000,
  subject: 'Decline the finding?',
  detail: 'The loop caps retries at three.',
  choices: ['Decline it', 'Add a budget'],
  ...fields,
})

const notice = (fields: Partial<HeldNotice> = {}): HeldNotice => ({
  ref: '2',
  kind: 'notice',
  number: 2,
  pr: 'x/y#7',
  postedAt: NOW - 60_000,
  subject: 'Force-pushed #7 to drop a stray file',
  detail: 'The file held a debug token.',
  ...fields,
})

/** A question with its answer and claim cleared, as `reopen` leaves it. */
const reopened = (question: HeldQuestion): HeldQuestion =>
  Object.fromEntries(Object.entries(question).filter(([key]) => key !== 'answer' && key !== 'claim')) as HeldQuestion

/** `project inbox`, answered from items held in memory the way the CLI keeps them. */
function questionStore(clock: Clock, seed: readonly Held[]) {
  const store = new Map<string, Held>()
  const times = new Map<string, number>()
  /** How the tools' commands fail: an exit code and its message, or `rejection` when the command never finishes. */
  const refusal: { code?: number; message?: string; rejection?: string } = {}
  /**
   * How many of the next `list` commands fail, how many run past the CLI's
   * timeout, and how many of the next item commands find the lock held.
   */
  const failing = { lists: 0, hangs: 0, locks: 0 }
  /** A write that lands while the next `list` runs, after that list read the store. */
  const during: { list?: () => void } = {}
  let writes = 0
  let posted = 0
  const put = (item: Held) => {
    writes += 1
    store.set(item.ref, item)
    times.set(item.ref, writes)
    return item
  }
  for (const item of seed) put(item)

  const failed = (exitCode: number, message: string): Ran => ({ ...ran(''), exitCode, stderr: `project: ${message}\n` })
  const printed = (item: Held, extra: Record<string, unknown> = {}) =>
    ran(JSON.stringify({ schema_version: 2, ...extra, item }))
  const flag = (args: readonly string[], name: string) => {
    const joined = args.find(arg => arg.startsWith(`--${name}=`))
    if (joined !== undefined) return joined.slice(name.length + 3)
    const at = args.indexOf(`--${name}`)
    return at === -1 ? undefined : args[at + 1]
  }
  const textOf = (question: HeldQuestion) =>
    question.answer !== undefined && 'letter' in question.answer
      ? `ANSWER ${question.ref}.${question.answer.letter} ${question.subject} → ${question.answer.text}`
      : `DISMISSED ${question.ref} ${question.subject}`

  const run = (argv: string[]): Outcome | Promise<Outcome> => {
    const [, group, command = '', ...args] = argv
    const time = clock.now()
    if (group !== 'inbox') return failed(2, `invalid choice: '${group ?? ''}'`)
    if (command === 'list') {
      if (failing.hangs > 0) {
        failing.hangs -= 1
        return { deny: 'project timed out after 15000 ms' }
      }
      if (failing.lists > 0) {
        failing.lists -= 1
        return failed(1, 'the store could not be read')
      }
      const items = [...store.values()].map(one => ({ ...one, session: SESSION }))
      const listed = ran(JSON.stringify({ schema_version: 2, items }))
      const write = during.list
      if (write === undefined) return listed
      delete during.list
      return clock
        .sleep(10)
        .then(() => {
          write()
          return clock.sleep(10)
        })
        .then(() => listed)
    }
    if (command === 'ask' || command === 'tell' || command === 'withdraw' || command === 'revise') {
      if (refusal.rejection !== undefined) return { deny: refusal.rejection }
      if (refusal.code !== undefined) return failed(refusal.code, refusal.message ?? '')
    }
    if (command === 'ask' || command === 'tell') {
      posted += 1
      const ref = String(posted)
      const agentId = flag(args, 'agent')
      const common = {
        ref,
        number: posted,
        pr: flag(args, 'pr') ?? null,
        postedAt: time,
        subject: args[args.indexOf('--') + 1] ?? '',
        detail: flag(args, 'detail') ?? '',
        ...(agentId === undefined ? {} : { agentId }),
      }
      const options = args.filter(arg => arg.startsWith('--option=')).map(arg => arg.slice('--option='.length))
      put(
        command === 'ask'
          ? { ...common, kind: 'question', choices: [flag(args, 'recommend') ?? '', ...options] }
          : { ...common, kind: 'notice' },
      )
      return ran(`${ref}\n`)
    }

    const written = args[0] ?? ''
    const ref = command === 'answer' ? written.slice(0, written.lastIndexOf('.')) : written
    const item = store.get(ref)
    if (item === undefined) return failed(66, `no item ${ref}`)
    if (failing.locks > 0) {
      failing.locks -= 1
      return failed(75, BUSY)
    }
    if (item.answer !== undefined && ['answer', 'dismiss', 'withdraw', 'revise', 'snooze'].includes(command)) {
      return failed(65, `${ref} is already answered`)
    }
    const { snoozedUntil: _, ...awake } = item
    if (command === 'dismiss') {
      const answer = { dismissed: true as const, answeredAt: time }
      return printed(put(awake.kind === 'notice' ? { ...awake, answer, deliveredAt: time } : { ...awake, answer }))
    }
    if (command === 'withdraw') {
      const answer = { dismissed: true as const, withdrawn: true as const, answeredAt: time }
      return printed(put({ ...awake, answer, deliveredAt: time }))
    }
    if (command === 'snooze') return printed(put({ ...item, snoozedUntil: time + HOUR }))
    if (item.kind === 'notice') return failed(65, `${ref} is a notice; dismiss it with \`project inbox dismiss\``)
    if (command === 'revise') {
      const options = args.filter(arg => arg.startsWith('--option=')).map(arg => arg.slice('--option='.length))
      const [recommended = '', ...others] = item.choices
      const kept = args.includes('--no-options') ? [] : options.length > 0 ? options : others
      return printed(
        put({
          ...item,
          subject: flag(args, 'question') ?? item.subject,
          detail: flag(args, 'detail') ?? item.detail,
          choices: [flag(args, 'recommend') ?? recommended, ...kept],
        }),
      )
    }
    if (command === 'answer') {
      const letter = written.slice(written.lastIndexOf('.') + 1)
      const text = flag(args, 'text') ?? item.choices[letter.charCodeAt(0) - 'a'.charCodeAt(0)] ?? ''
      return printed(put({ ...(awake as HeldQuestion), answer: { letter, text, answeredAt: time } }))
    }
    if (command === 'claim') {
      if (item.answer === undefined || item.deliveredAt !== undefined) return failed(65, 'nothing to deliver')
      const renew = flag(args, 'renew')
      if (renew !== undefined) {
        if (item.claim?.token !== renew) return failed(65, 'not claimed with that token')
        put({ ...item, claim: { token: renew, renewedAt: time } })
        return ran('')
      }
      if (item.claim !== undefined && time - item.claim.renewedAt < 60_000) return failed(65, 'already claimed')
      const claim = { token: `token-${writes}`, renewedAt: time }
      return printed(put({ ...item, claim }), { token: claim.token, text: textOf(item) })
    }
    if (command === 'delivered' || command === 'reopen') {
      if (item.claim?.token !== flag(args, 'claim')) return failed(65, 'not claimed with that token')
      return printed(put(command === 'delivered' ? { ...item, deliveredAt: time } : reopened(item)))
    }
    return failed(2, `invalid choice: '${command}'`)
  }

  const files = (): FsEntry[] =>
    [...store.keys()].map(ref => ({
      name: `${encodeURIComponent(ref)}.json`,
      kind: 'file' as const,
      size: 100,
      mtimeMs: times.get(ref) ?? 0,
      isLink: false,
    }))

  return { run, files, store, refusal, failing, during, write: put }
}

type Options = Beneath & { agents?: AgentInfo[]; drop?: string; isTurnRunning?: boolean }

/** A session with a loop on x/y#7 and a store of questions beneath the plugin. */
function questionWorld(on: On, clock: Clock, seed: readonly Held[] = [], options: Options = {}) {
  const store = questionStore(clock, seed)
  const { commands, filled } = world(on, { [REVIEW]: 'x/y#7\n' }, store.run, options)
  const registered: unknown[] = []
  const notified: string[] = []
  const toasts: string[] = []
  const sent: { to: string; text: string }[] = []
  const submitted: { text: string; context: readonly string[] }[] = []
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: SESSION }))
  on('command.register', () => ({ value: { command: 'projector' } }))
  on('tool.register', ($, e) => {
    registered.push(e)
    return { value: { tool: `mcp__projector__${e.name}` } }
  })
  on('fs.list', ($, e) => ({ value: e.path === STORE ? store.files() : [] }))
  on('agent.list', () => ({ value: options.agents ?? [] }))
  on('session.send', ($, e) => {
    sent.push({ to: e.to, text: e.text })
    return { isDelivered: true as const }
  })
  // A submit waits for the running turn; `release` ends that turn.
  let release = () => undefined as void
  const turn = new Promise<void>(resolve => {
    release = resolve
  })
  if (options.isTurnRunning !== true) release()
  on('prompt.submit', async ($, e) => {
    submitted.push({ text: e.text, context: e.context ?? [] })
    await turn
    return options.drop === undefined ? { text: e.text } : { drop: options.drop }
  })
  on('ui.notify', ($, e) => {
    notified.push(e.text)
    return { value: { isSent: true as const, channel: 'terminal_bell' as const } }
  })
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  const project = () => commands.filter(argv => argv[0] === 'project').map(argv => argv.slice(1).join(' '))
  return { store, registered, notified, toasts, sent, submitted, filled, project, release }
}

async function startSession($: Engine, clock: Clock, isShown = true) {
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  // A shown band belongs to a session that started its review loop's watcher.
  if (isShown) await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()
}

const running = (status: AgentInfo['status']): AgentInfo =>
  ({ id: 'agent-1', description: 'Fix #7', type: 'general-purpose', status }) as AgentInfo

test('asks through the tool, and the band lists the question below the pull requests', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)

  const called = await $.tool.call({
    tool: 'mcp__projector__ask',
    question: 'Decline the finding?',
    recommendation: 'Decline it',
    options: ['Add a budget'],
    pr: 'x/y#7',
  })
  expect(String(called.text ?? called.result)).toBe(
    'Asked 1. Keep working on anything the answer does not block; it arrives as a message that starts ANSWER 1 or DISMISSED 1.',
  )
  expect(session.registered).toEqual([
    expect.objectContaining({ name: 'ask', isDeferred: false }),
    expect.objectContaining({ name: 'tell', isDeferred: false }),
    expect.objectContaining({ name: 'withdraw', isDeferred: false }),
    expect.objectContaining({ name: 'revise', isDeferred: false }),
  ])
  expect(session.project()).toContain(
    'inbox ask --session=s1 --recommend=Decline it --option=Add a budget --pr=x/y#7 -- Decline the finding?',
  )
  // You never need the number, so the notification holds the question alone.
  expect(session.notified).toEqual(['Decline the finding?'])

  await clock.advance(1)
  await clock.settle()
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^─+ 1 question · updated (just now|\d+s ago)$/ })).toBeDefined()
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts).toContain('Conflicting change')
  expect(texts.indexOf('Conflicting change')).toBeLessThan(texts.indexOf('Decline the finding?'))
  // The line names the question's pull request as the band labels it.
  expect(await ui.find({ type: 'Text', text: '#7' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'item:1' })).toBeDefined()
  await ui.unmount()
})

test('a blank line sets GitHub’s error apart from the items under it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  questionWorld(on, clock, [notice()], { github: () => DOWN })
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  const failure = texts.indexOf('GitHub: read: connection reset by peer')
  expect(failure).toBeGreaterThan(-1)
  expect(texts.slice(failure + 1, failure + 3)).toEqual([' ', '• '])
  await ui.unmount()
})

test('refuses what the CLI refuses, asks an old CLI to upgrade, and says when it hangs', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)
  const ask = { tool: 'mcp__projector__ask' as const, question: 'Decline the finding?', recommendation: 'Decline it' }

  Object.assign(session.store.refusal, { code: 65, message: 'the question must be one line; put the rest in --detail' })
  const refused = await $.tool.call(ask)
  expect(refused.deny ?? refused.text).toBe('the question must be one line; put the rest in --detail')

  Object.assign(session.store.refusal, { code: 75, message: BUSY })
  const busy = await $.tool.call(ask)
  expect(busy.deny ?? busy.text).toBe(BUSY)

  Object.assign(session.store.refusal, { code: 2, message: "error: argument command: invalid choice: 'inbox'" })
  const old = await $.tool.call(ask)
  expect(old.deny ?? old.text).toBe("error: argument command: invalid choice: 'inbox'; run project upgrade")

  Object.assign(session.store.refusal, { rejection: 'project timed out after 15000 ms' })
  const hung = await $.tool.call(ask)
  expect(hung.deny ?? hung.text).toMatch(
    /^project did not finish \(.*project timed out after 15000 ms\); ask in the conversation instead$/,
  )
})

test('a question asked from the shell shows the hidden band within a tick', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  questionWorld(on, clock, [held({ pr: null, subject: 'Run project init?' })])
  await startSession($, clock, false)
  await clock.advance(TICK)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Run project init?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /1 question/ })).toBeDefined()
  await ui.unmount()
})

test('an idle session with no questions starts no project process', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)
  await clock.advance(3 * TICK)
  await clock.settle()

  expect(session.project()).toEqual([])
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^─+ updated/ })).toBeDefined()
  await ui.unmount()
})

test('an option puts the question and its answer in the prompt, and sending it answers the question and closes its drawer', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const later = held({ ref: '2', number: 2, subject: 'Rebase first?', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held({ agentId: 'agent-1' }), later], { agents: [running('running')] })
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  // The open question has the band to itself, the divider included, so it is the top row.
  expect(await ui.find({ type: 'Text', text: 'Rebase first?' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  expect((await ui.find({ key: 'detail:1' }))?.props.text).toBe('The loop caps retries at three.')
  expect(await ui.find({ type: 'Text', text: ' (recommended)' })).toBeDefined()
  // An answer in your own words goes through discuss, so only the options are numbered.
  expect(await ui.find({ type: 'Input' })).toBeUndefined()
  expect(await ui.find({ key: 'answer:1.c' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'discuss:1' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:1' })).toBeDefined()
  // A digit typed first in an empty prompt presses a band Button whose hotkey it is.
  for (const button of await ui.findAll({ type: 'Button' })) expect(button.props.hotkey).toBeUndefined()

  // Choosing an option answers nothing yet: it fills the prompt, and the drawer stays open.
  await ui.press({ key: 'answer:1.b', link: { href: SEVEN } })
  expect(session.filled).toEqual(['insert:"Decline the finding?"\n\nAdd a budget\n'])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  expect(await ui.find({ key: 'detail:1' })).toBeDefined()

  const text = 'Thanks.\n"Decline the finding?"\n\nAdd a budget\n'
  await $.prompt.submit({ text, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.submitted).toEqual([
    {
      text,
      context: ['The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the answer this prompt gives.'],
    },
  ])
  expect(session.store.store.get('1')?.answer).toEqual(expect.objectContaining({ letter: 'b' }))
  expect(session.store.store.get('1')?.deliveredAt).toBeDefined()
  // The prompt delivered it, so the band sends nothing more.
  expect(session.sent).toEqual([])

  // Sending the answer closed the drawer, so the band shows its list again.
  expect(await ui.find({ key: 'detail:1' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Rebase first?' })).toBeDefined()

  // The resolved question still opens, its choice marked, the rest muted, and nothing left to press.
  await ui.press({ key: 'item:1' })
  expect(await ui.find({ type: 'Text', text: ' ✓' })).toBeDefined()
  expect((await ui.find({ type: 'Text', text: /^2\.$/ }))?.props.color).toBe('success')
  expect((await ui.find({ type: 'Text', text: /^1\.$/ }))?.props.color).toBe('subtle')
  expect((await ui.find({ type: 'Text', text: /^Decline it/ }))?.props.color).toBe('subtle')
  expect((await ui.find({ type: 'Text', text: /^Decline the finding\?$/ }))?.props.color).toBe('subtle')
  expect(await ui.find({ key: 'answer:1.a' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:1' })).toBeUndefined()

  await ui.press({ key: 'item:1' })
  expect(await ui.find({ type: 'Text', text: 'Rebase first?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeDefined()
  await clock.advance(30_000 + TICK)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: /Decline the finding/ })).toBeUndefined()
  await ui.unmount()
})

test('a question another shares the text of takes only the answer its own drawer put in the prompt', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const newer = held({ ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held(), newer])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a', link: { href: 'https://github.com/x/y/pull/8' } })
  expect(session.filled).toEqual(['insert:"Decline the finding?"\n\nDecline it\n'])
  await $.prompt.submit({ text: '"Decline the finding?"\n\nDecline it\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('2')?.answer).toEqual(expect.objectContaining({ letter: 'a' }))
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  await ui.unmount()
})

test('a proposal you cleared from the prompt does not take the answer you sent for another question', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const newer = held({ ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held({ postedAt: NOW - 30_000 }), newer])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a', link: { href: 'https://github.com/x/y/pull/8' } })
  // You clear that from the prompt, then choose the other answer in the older question's drawer.
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'answer:1.b', link: { href: SEVEN } })
  await $.prompt.submit({ text: '"Decline the finding?"\n\nAdd a budget\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('1')?.answer).toEqual(expect.objectContaining({ letter: 'b' }))
  expect(session.store.store.get('2')?.answer).toBeUndefined()
  await ui.unmount()
})

test('only a prompt you sent answers a question', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held()])
  await startSession($, clock)

  const text = 'Waiting on the person for:\n"Decline the finding?"\nI will keep fixing the other threads meanwhile.'
  const others = [
    { kind: 'peer-send-message' },
    { kind: 'peer' },
    { kind: 'task-notification' },
    { kind: 'scheduled-trigger' },
    { kind: 'channel', server: 'chat' },
    { kind: 'unclassified' },
  ] as const
  for (const origin of others) await $.prompt.submit({ text, wait: false, origin })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([])
  expect(session.store.store.get('1')?.answer).toBeUndefined()

  await $.prompt.submit({ text: '"Decline the finding?"\n\nAdd a budget', wait: false, origin: { kind: 'bridge' } })
  await clock.settle()
  expect(session.store.store.get('1')?.answer).toEqual(expect.objectContaining({ letter: 'b' }))
})

test('discuss keeps the drawer open with the question above a blank line, a reply under it answers nothing, and dismiss resolves it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const typed = { letter: 'c', text: 'keep the cap', answeredAt: NOW - 1_000 }
  const squashed = held({ ref: '3', number: 3, subject: 'Squash the fixups?', answer: typed, deliveredAt: NOW - 1_000 })
  const session = questionWorld(on, clock, [held(), held({ ref: '2', number: 2, subject: 'Rebase first?' }), squashed])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'discuss:1' })
  expect(session.filled).toEqual(['insert:"Decline the finding?"\n\n'])
  // The drawer stays open, so its options and detail stay in view while you type.
  expect(await ui.find({ key: 'detail:1' })).toBeDefined()

  // What you type under the quote, or on its line, replies: the prompt carries it, and the question stays open.
  for (const reply of ['"Decline the finding?"\n\nkeep the cap', '"Decline the finding?" what does the cap cover?']) {
    await $.prompt.submit({ text: reply, wait: false, origin: { kind: 'composer' } })
    await clock.settle()
    expect(session.submitted.at(-1)).toEqual({ text: reply, context: [] })
    expect(session.store.store.get('1')).toEqual(held())
    // Sending the reply closed the drawer, and the question is still listed.
    expect(await ui.find({ key: 'detail:1' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeDefined()
    await ui.press({ key: 'item:1' })
  }
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([])
  await ui.press({ key: 'item:1' })

  // An answer typed from a terminal shows after the options, marked.
  await ui.press({ key: 'item:3' })
  expect((await ui.find({ type: 'Text', text: /^3\.$/ }))?.props.color).toBe('success')
  expect(await ui.find({ type: 'Text', text: /^keep the cap/ })).toBeDefined()

  await ui.press({ key: 'item:3' })
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'dismiss:2' })
  expect(session.project()).toContain('inbox dismiss 2 --session s1 --json')
  await clock.advance(1)
  await clock.settle()
  expect(session.submitted.map(one => one.text)).toContain('DISMISSED 2 Rebase first?')
  await ui.unmount()
})

test('a sent reply leaves the question listed, closes its drawer, and records no answer, naming the subagent that asked', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const asked = held({ agentId: 'agent-1' })
  const session = questionWorld(on, clock, [asked], { agents: [running('running')] })
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  // You choose an option, then add to it before you send.
  await ui.press({ key: 'answer:1.b', link: { href: SEVEN } })
  const text = '"Decline the finding?"\n\nAdd a budget\n\nBut what does the cap cover?\n'
  await $.prompt.submit({ text, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.submitted).toEqual([
    {
      text,
      context: [
        'The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the reply this prompt gives, which leaves the question open.',
      ],
    },
  ])
  expect(session.project().filter(command => /^inbox (answer|dismiss|claim|delivered)/.test(command))).toEqual([])
  expect(session.store.store.get('1')).toEqual(asked)
  expect(await ui.find({ key: 'detail:1' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^─+ 1 question · updated/ })).toBeDefined()

  // The question waits for its agent to settle it, and the band sends nothing.
  await clock.advance(TICK)
  await clock.settle()
  expect(session.sent).toEqual([])
  expect(session.submitted).toHaveLength(1)
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeDefined()
  await ui.unmount()
})

test('a prompt about one of two questions that share a subject leaves the other alone', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const fixer = (id: string, description: string) =>
    ({ id, description, type: 'general-purpose', status: 'running' }) as AgentInfo
  const merge = { subject: 'Merge it?', choices: ['Yes', 'No'] }
  const first = held({ ...merge, agentId: 'agent-a' })
  const second = held({ ...merge, ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000, agentId: 'agent-b' })
  const agents = [fixer('agent-a', 'Fix #7'), fixer('agent-b', 'Fix #8')]
  const session = questionWorld(on, clock, [first, second], { agents })
  await startSession($, clock)
  const pull8 = { href: 'https://github.com/x/y/pull/8' }

  const ui = await $.ui.mount(BAND)
  // You discuss the second question: your reply goes to its subagent alone.
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'discuss:2' })
  const reply = '"Merge it?"\n\nwhy now?'
  await $.prompt.submit({ text: reply, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.submitted.at(-1)).toEqual({
    text: reply,
    context: ['The subagent Fix #8 (agent-b) asked "Merge it?"; send it the reply this prompt gives, which leaves the question open.'],
  })
  expect(await ui.find({ key: 'detail:2' })).toBeUndefined()

  // Then you pick No in its drawer and open the first question's drawer before you send. The
  // prompt answers the second question, and the first hears nothing and keeps its drawer open.
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.b', link: pull8 })
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'item:1' })
  const answer = '"Merge it?"\n\nNo\n'
  await $.prompt.submit({ text: answer, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.submitted.at(-1)).toEqual({
    text: answer,
    context: ['The subagent Fix #8 (agent-b) asked "Merge it?"; send it the answer this prompt gives.'],
  })
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([
    'inbox answer 2.b --session s1 --json',
  ])
  expect(session.store.store.get('1')).toEqual(first)
  expect(await ui.find({ key: 'detail:1' })).toBeDefined()
  await ui.unmount()
})

test('an answer from a terminal reaches the live subagent that asked', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ agentId: 'agent-1', answer })], { agents: [running('running')] })
  await startSession($, clock, false)
  await clock.advance(TICK)
  await clock.settle()

  expect(session.sent).toEqual([
    { to: expect.stringContaining('agent-1'), text: 'ANSWER 1.a Decline the finding? → Decline it' },
  ])
  expect(session.submitted).toEqual([])
  expect(session.store.store.get('1')?.deliveredAt).toBeDefined()
})

test('an answer from a terminal for a subagent that ended goes to the main loop, naming it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'b', text: 'Add a budget', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ agentId: 'agent-1', answer })], { agents: [running('completed')] })
  await startSession($, clock, false)
  await clock.advance(TICK)
  await clock.settle()

  expect(session.sent).toEqual([])
  expect(session.submitted[0]?.text).toContain('ANSWER 1.b Decline the finding? → Add a budget')
  expect(session.submitted[0]?.text).toContain('The subagent Fix #7 (agent-1) asked this')
  expect(session.store.store.get('1')?.deliveredAt).toBeDefined()
})

test('a dropped submit reopens the question and says why', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ answer })], { drop: 'a hook refused it' })
  await startSession($, clock)
  await clock.advance(TICK)
  await clock.settle()

  expect(session.toasts).toEqual([
    'projector: the answer to "Decline the finding?" did not reach its agent (a hook refused it); answer it again',
  ])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Button', key: 'item:1' })).toBeDefined()
  await ui.unmount()
})

test('an answer from a terminal waits behind a long turn on one claim, renewed without reading the store', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ answer })], { isTurnRunning: true })
  await startSession($, clock, false)
  await clock.advance(TICK)
  await clock.settle()
  const lists = () => session.project().filter(command => command.startsWith('inbox list')).length
  const before = lists()

  for (let at = 0; at < 6; at += 1) await clock.advance(TICK)
  await clock.settle()
  const claims = session.project().filter(command => command.startsWith('inbox claim'))
  expect(claims.filter(command => !command.includes('--renew'))).toHaveLength(1)
  expect(claims.filter(command => command.includes('--renew')).length).toBeGreaterThanOrEqual(6)
  expect(lists()).toBe(before)
  expect(session.submitted.map(one => one.text)).toEqual(['ANSWER 1.a Decline the finding? → Decline it'])

  session.release()
  await clock.advance(TICK)
  await clock.settle()
  expect(session.store.store.get('1')?.deliveredAt).toBeDefined()
  expect(session.submitted).toHaveLength(1)
})

test('a store read that fails runs again on the next tick', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ answer })])
  session.store.failing.lists = 1
  await startSession($, clock, false)
  for (let at = 0; at < 3; at += 1) await clock.advance(TICK)
  await clock.settle()

  expect(session.project().filter(command => command.startsWith('inbox list')).length).toBeGreaterThanOrEqual(2)
  expect(session.submitted.map(one => one.text)).toEqual(['ANSWER 1.a Decline the finding? → Decline it'])
  expect(session.store.store.get('1')?.deliveredAt).toBeDefined()
})

test('a store read that runs past the CLI’s timeout still renews the claim of an answer waiting to be sent', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ answer })], { isTurnRunning: true })
  await startSession($, clock, false)
  await clock.advance(TICK)
  await clock.settle()
  const renewals = () => session.project().filter(command => command.includes('--renew')).length
  const before = renewals()

  // A notice arrives, so each tick reads the store, and each read hangs.
  session.store.write(notice())
  session.store.failing.hangs = 6
  for (let at = 0; at < 6; at += 1) await clock.advance(TICK)
  await clock.settle()
  expect(session.store.failing.hangs).toBe(0)
  expect(renewals() - before).toBeGreaterThanOrEqual(6)

  session.release()
  await clock.advance(TICK)
  await clock.settle()
  const claims = session.project().filter(command => command.startsWith('inbox claim'))
  expect(claims.filter(command => !command.includes('--renew'))).toHaveLength(1)
  expect(session.submitted.map(one => one.text)).toEqual(['ANSWER 1.a Decline the finding? → Decline it'])
  expect(session.store.store.get('1')?.deliveredAt).toBeDefined()
})

test('a submit an earlier copy of the module left keeps its claim and settles when its prompt arrives', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const text = 'ANSWER 1.a Decline the finding? → Decline it'
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ answer, claim: { token: 'old', renewedAt: NOW } })])
  let pending: Record<string, unknown> = { '1': { ref: '1', session: SESSION, token: 'old', route: 'submit', text } }
  let version = 1
  on('state.get', { plugin: 'projector', key: 'bandSending' }, () => ({ value: { value: pending as never, version } }))
  on('state.set', { plugin: 'projector', key: 'bandSending' }, ($, e) => {
    pending = e.value as Record<string, unknown>
    version += 1
    return { value: { isSet: true as const, version } }
  })
  await startSession($, clock, false)
  await clock.advance(2 * TICK)
  await clock.settle()

  const claims = session.project().filter(command => command.startsWith('inbox claim'))
  expect(claims.length).toBeGreaterThanOrEqual(2)
  for (const claim of claims) expect(claim).toBe('inbox claim 1 --renew old --session s1')
  await $.prompt.submit({ text, wait: false, origin: { kind: 'plugin', name: 'projector' } })
  expect(session.project()).toContain('inbox delivered 1 --claim old --session s1 --json')
  expect(session.store.store.get('1')?.deliveredAt).toBeDefined()
  expect(pending).toEqual({})
})

test('a number or an old reference typed in the prompt reaches the agent as plain text', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held()])
  await startSession($, clock)
  const before = session.project().length

  await $.prompt.submit({ text: 'go with 1.b', wait: false, origin: { kind: 'composer' } })
  await $.prompt.submit({ text: 'go with #7.1.b', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.submitted).toEqual([
    { text: 'go with 1.b', context: [] },
    { text: 'go with #7.1.b', context: [] },
  ])
  expect(session.project().slice(before)).toEqual([])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
})

const FORCE_PUSHED = 'Force-pushed #7 to drop a stray file'

test('a subagent tells through the tool, and the band lists the notice with its own bullet', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)

  const called = await $.tool.call({ tool: 'mcp__projector__tell', notice: FORCE_PUSHED, pr: 'x/y#7', agentId: 'agent-1' })
  expect(String(called.text ?? called.result)).toBe(
    'Posted 1. It waits in Projector’s band until the person dismisses it; nothing comes back to you.',
  )
  expect(session.project()).toContain(`inbox tell --session=s1 --pr=x/y#7 --agent=agent-1 -- ${FORCE_PUSHED}`)
  expect(session.notified).toEqual([FORCE_PUSHED])

  await clock.advance(1)
  await clock.settle()
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^─+ 1 notice · updated (just now|\d+s ago)$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '• ' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '#7' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'item:1' })).toBeDefined()
  await ui.unmount()
})

test('the tools number a session’s items in one count, and notify with the subject alone', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)

  const asked = await $.tool.call({
    tool: 'mcp__projector__ask',
    question: 'Decline the finding?',
    recommendation: 'Decline it',
    agentId: 'agent-1',
  })
  expect(String(asked.text ?? asked.result)).toMatch(/^Asked 1\. .*ANSWER 1 or DISMISSED 1\.$/)
  const told = await $.tool.call({ tool: 'mcp__projector__tell', notice: FORCE_PUSHED, agentId: 'agent-1' })
  expect(String(told.text ?? told.result)).toMatch(/^Posted 2\. /)
  expect(session.notified).toEqual(['Decline the finding?', FORCE_PUSHED])
})

test('no line or drawer shows an item’s number', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  questionWorld(on, clock, [held({ ref: '42', number: 42 }), notice({ ref: '43', number: 43 })])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  const drawn = async () => (await ui.findAll({ type: 'Text' })).map(found => found.text).join('\n')
  expect(await drawn()).toContain('Decline the finding?')
  expect(await drawn()).not.toMatch(/42|43/)
  for (const ref of ['42', '43']) {
    await ui.press({ key: `item:${ref}` })
    expect(await ui.find({ key: `detail:${ref}` })).toBeDefined()
    expect(await drawn()).not.toMatch(/42|43/)
    await ui.press({ key: `item:${ref}` })
  }
  await ui.unmount()
})

test('the tell tool refuses what the CLI refuses, and says when it hangs', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)
  const tell = { tool: 'mcp__projector__tell' as const, notice: FORCE_PUSHED }

  Object.assign(session.store.refusal, { code: 65, message: 'the notice must be one line; put the rest in --detail' })
  const refused = await $.tool.call(tell)
  expect(refused.deny ?? refused.text).toBe('the notice must be one line; put the rest in --detail')

  Object.assign(session.store.refusal, { code: 75, message: BUSY })
  const busy = await $.tool.call(tell)
  expect(busy.deny ?? busy.text).toBe(BUSY)

  Object.assign(session.store.refusal, { code: undefined, rejection: 'project timed out after 15000 ms' })
  const hung = await $.tool.call(tell)
  expect(hung.deny ?? hung.text).toMatch(/^project did not finish \(.*\); tell the person in the conversation instead$/)
})

test('a notice’s drawer quotes it, and dismiss clears it without sending anything', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), notice()])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^─+ 1 question · 1 notice · updated/ })).toBeDefined()
  await ui.press({ key: 'item:2' })
  // An open notice has the band to itself, the divider included, as an open question does.
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()
  expect((await ui.find({ key: 'detail:2' }))?.props.text).toBe('The file held a debug token.')
  expect(await ui.find({ type: 'Text', text: ' (recommended)' })).toBeUndefined()
  for (const name of ['discuss', 'dismiss', 'snooze']) {
    expect((await ui.find({ type: 'Button', key: `${name}:2` }))?.props.plain).toBe(true)
  }
  const bars = await ui.findAll({ type: 'Text', text: '|' })
  expect(bars.map(bar => bar.props.color)).toEqual(['subtle', 'subtle'])

  await ui.press({ key: 'discuss:2' })
  expect(session.filled).toEqual([`insert:"${FORCE_PUSHED}"\n\n`])

  await ui.press({ key: 'dismiss:2' })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox dismiss'))).toEqual([
    'inbox dismiss 2 --session s1 --json',
  ])
  const line = await ui.find({ type: 'Text', text: FORCE_PUSHED })
  expect([line?.props.color, line?.props.strikethrough]).toEqual(['subtle', true])
  expect(await ui.find({ type: 'Button', key: 'discuss:2' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:2' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'snooze:2' })).toBeUndefined()

  await clock.advance(TICK)
  await clock.settle()
  expect(session.sent).toEqual([])
  expect(session.submitted).toEqual([])
  expect(session.project().filter(command => command.startsWith('inbox claim'))).toEqual([])

  await ui.press({ key: 'item:2' })
  expect(await ui.find({ type: 'Text', text: /^─+ 1 question · updated/ })).toBeDefined()
  await clock.advance(30_000 - 2 * TICK)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: FORCE_PUSHED })).toBeDefined()
  await clock.advance(2 * TICK)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: FORCE_PUSHED })).toBeUndefined()
  await ui.unmount()
})

test('the withdraw tool takes an item out of the band at once, and nothing goes back to the agent', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), notice()])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  expect(await ui.find({ key: 'detail:1' })).toBeDefined()

  const called = await $.tool.call({ tool: 'mcp__projector__withdraw', number: 1 })
  expect(String(called.text ?? called.result)).toBe(
    'Withdrew 1. It has left Projector’s band, and nothing comes back to you.',
  )
  expect(session.project()).toContain('inbox withdraw 1 --session s1 --json')
  // Before the band reads the store again, the item has gone and its drawer with it.
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()
  expect(await ui.find({ key: 'detail:1' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^─+ 1 notice · updated/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: FORCE_PUSHED })).toBeDefined()

  await clock.advance(TICK)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()
  expect(session.sent).toEqual([])
  expect(session.submitted).toEqual([])
  expect(session.project().filter(command => command.startsWith('inbox claim'))).toEqual([])
  await ui.unmount()
})

test('the withdraw tool refuses what the CLI refuses, asks an old CLI to upgrade, and says when it hangs', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const answered = held({ answer, deliveredAt: NOW - 1_000 })
  const session = questionWorld(on, clock, [answered])
  await startSession($, clock)
  const withdraw = { tool: 'mcp__projector__withdraw' as const, number: 1 }

  const resolved = await $.tool.call(withdraw)
  expect(resolved.deny ?? resolved.text).toBe('1 is already answered')

  const unknown = await $.tool.call({ ...withdraw, number: 9 })
  expect(unknown.deny ?? unknown.text).toBe('no item 9')

  session.store.failing.locks = 1
  const busy = await $.tool.call(withdraw)
  expect(busy.deny ?? busy.text).toBe(BUSY)

  Object.assign(session.store.refusal, { code: 2, message: "error: argument inbox_command: invalid choice: 'withdraw'" })
  const old = await $.tool.call(withdraw)
  expect(old.deny ?? old.text).toBe("error: argument inbox_command: invalid choice: 'withdraw'; run project upgrade")

  Object.assign(session.store.refusal, { code: undefined, rejection: 'project timed out after 15000 ms' })
  const hung = await $.tool.call(withdraw)
  expect(hung.deny ?? hung.text).toMatch(
    /^project did not finish \(.*\); tell the person in the conversation that it no longer needs them$/,
  )
  expect(session.store.store.get('1')).toEqual(answered)
})

test('the revise tool redraws the question with its new text at once, keeping its number', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held()])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  // You choose an option from the question as it reads now, and have not sent it yet.
  await ui.press({ key: 'answer:1.b', link: { href: SEVEN } })

  const options = ['Add a budget of two per host', 'Ask the reviewer']
  const called = await $.tool.call({ tool: 'mcp__projector__revise', number: 1, options, detail: 'The cap is three.' })
  expect(String(called.text ?? called.result)).toBe(
    'Revised 1. Projector’s band shows the new text, and its answer still arrives as a message that starts ANSWER 1 or DISMISSED 1.',
  )
  expect(session.project()).toContain(
    'inbox revise 1 --session s1 --option=Add a budget of two per host --option=Ask the reviewer --detail=The cap is three. --json',
  )
  // Before the band reads the store again, the open drawer shows the new text.
  expect((await ui.find({ key: 'detail:1' }))?.props.text).toBe('The cap is three.')
  expect(await ui.find({ type: 'Text', text: /^Add a budget of two per host/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^Ask the reviewer/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^Add a budget$/ })).toBeUndefined()
  expect(await ui.find({ key: 'answer:1.c' })).toBeDefined()

  await ui.press({ key: 'item:1' })
  await $.tool.call({ tool: 'mcp__projector__revise', number: 1, question: 'Decline the retry finding?' })
  expect(await ui.find({ type: 'Text', text: 'Decline the retry finding?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'item:1' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^─+ 1 question · updated/ })).toBeDefined()

  // The answer you chose from the old text answers nothing.
  await $.prompt.submit({ text: '"Decline the finding?"\n\nAdd a budget\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('1')?.answer).toBeUndefined()

  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'answer:1.c', link: { href: SEVEN } })
  expect(session.filled.at(-1)).toBe('insert:"Decline the retry finding?"\n\nAsk the reviewer\n')
  await $.prompt.submit({ text: '"Decline the retry finding?"\n\nAsk the reviewer\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('1')?.answer).toEqual(expect.objectContaining({ letter: 'c', text: 'Ask the reviewer' }))
  expect(session.store.store.get('1')?.ref).toBe('1')
  await ui.unmount()
})

test('an answer chosen before a revise answers no question, and a pick from the new drawer answers the revised one', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const newer = held({ ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000, agentId: 'agent-1' })
  const session = questionWorld(on, clock, [held(), newer], { agents: [running('running')] })
  await startSession($, clock)
  const decline = '"Decline the finding?"\n\nDecline it\n'

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a', link: { href: 'https://github.com/x/y/pull/8' } })
  await $.tool.call({ tool: 'mcp__projector__revise', number: 2, detail: 'The cap is three.' })
  expect((await ui.find({ key: 'detail:2' }))?.props.text).toBe('The cap is three.')

  // You chose that answer from the text the question had: sent, it answers neither question, though
  // the older one shares the text and offers the option. It replies to the revised question.
  await $.prompt.submit({ text: decline, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  expect(session.store.store.get('2')?.answer).toBeUndefined()
  expect(session.submitted.at(-1)).toEqual({
    text: decline,
    context: [
      'The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the reply this prompt gives, which leaves the question open.',
    ],
  })
  expect(await ui.find({ key: 'detail:2' })).toBeUndefined()
  expect(await ui.findAll({ type: 'Text', text: /^Decline the finding\?$/ })).toHaveLength(2)

  // The same option, picked again in the revised question's drawer, answers it.
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a', link: { href: 'https://github.com/x/y/pull/8' } })
  await $.prompt.submit({ text: decline, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('2')?.answer).toEqual(expect.objectContaining({ letter: 'a' }))
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  await ui.unmount()
})

test('an answer chosen before a revise rewords its question does not answer another question with the old words', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const newer = held({ ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held(), newer])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a', link: { href: 'https://github.com/x/y/pull/8' } })
  await $.tool.call({ tool: 'mcp__projector__revise', number: 2, question: 'Decline the retry finding?' })
  expect(await ui.find({ key: 'detail:2' })).toBeDefined()

  await $.prompt.submit({ text: '"Decline the finding?"\n\nDecline it\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  // The reply was to the revised question, so its drawer closes though the prompt no longer quotes it.
  expect(await ui.find({ key: 'detail:2' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Decline the retry finding?' })).toBeDefined()
  await ui.unmount()
})

test('an option typed under a quote discussed before a revise answers no question, and replies to the revised one', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const newer = held({ ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000, agentId: 'agent-1' })
  const session = questionWorld(on, clock, [held(), newer], { agents: [running('running')] })
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'discuss:2' })
  await $.tool.call({ tool: 'mcp__projector__revise', number: 2, detail: 'The cap is three.' })
  // You type one of the options under the quote that discuss put there before the revision.
  const typed = '"Decline the finding?"\n\nDecline it\n'
  await $.prompt.submit({ text: typed, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  expect(session.store.store.get('2')?.answer).toBeUndefined()
  expect(session.submitted.at(-1)).toEqual({
    text: typed,
    context: [
      'The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the reply this prompt gives, which leaves the question open.',
    ],
  })
  expect(await ui.find({ key: 'detail:2' })).toBeUndefined()
  await ui.unmount()
})

test('the revise tool refuses what the CLI refuses, asks an old CLI to upgrade, and says when it hangs', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const answered = held({ answer, deliveredAt: NOW - 1_000 })
  const session = questionWorld(on, clock, [answered])
  await startSession($, clock)
  const revise = { tool: 'mcp__projector__revise' as const, number: 1, question: 'Decline the retry finding?' }

  const resolved = await $.tool.call(revise)
  expect(resolved.deny ?? resolved.text).toBe('1 is already answered')

  const unknown = await $.tool.call({ ...revise, number: 9 })
  expect(unknown.deny ?? unknown.text).toBe('no item 9')

  const empty = await $.tool.call({ tool: 'mcp__projector__revise', number: 1 })
  expect(empty.deny ?? empty.text).toBe('give what changes: `question`, `detail`, `recommendation`, or `options`')

  session.store.failing.locks = 1
  const busy = await $.tool.call(revise)
  expect(busy.deny ?? busy.text).toBe(BUSY)

  Object.assign(session.store.refusal, { code: 2, message: "error: argument inbox_command: invalid choice: 'revise'" })
  const old = await $.tool.call(revise)
  expect(old.deny ?? old.text).toBe("error: argument inbox_command: invalid choice: 'revise'; run project upgrade")

  Object.assign(session.store.refusal, { code: undefined, rejection: 'project timed out after 15000 ms' })
  const hung = await $.tool.call(revise)
  expect(hung.deny ?? hung.text).toMatch(/^project did not finish \(.*\); ask the revised question in the conversation instead$/)
  expect(session.store.store.get('1')).toEqual(answered)
})

test('snooze hides either kind for an hour, then brings a hidden band back', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), notice()])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'snooze:1' })
  await clock.settle()
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'snooze:2' })
  await clock.settle()
  expect(session.project()).toContain('inbox snooze 1 --session s1 --json')
  expect(session.project()).toContain('inbox snooze 2 --session s1 --json')
  expect(await ui.find({ type: 'Text', text: /Decline the finding|Force-pushed/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^─+ updated/ })).toBeDefined()
  await ui.unmount()

  await clock.advance(TICK)
  await clock.settle()
  await $.command.run(COMMAND)
  await clock.advance(HOUR - TICK)
  await clock.settle()
  const hidden = await $.ui.mount(BAND)
  expect(await hidden.find({ type: 'Text', text: ENGINE_BAND })).toBeDefined()
  await hidden.unmount()

  await clock.advance(2 * TICK)
  await clock.settle()
  const woken = await $.ui.mount(BAND)
  expect(await woken.find({ type: 'Text', text: ENGINE_BAND })).toBeUndefined()
  expect(await woken.find({ type: 'Text', text: 'Decline the finding?' })).toBeDefined()
  expect(await woken.find({ type: 'Text', text: FORCE_PUSHED })).toBeDefined()
  expect(await woken.find({ type: 'Text', text: /^─+ 1 question · 1 notice · updated/ })).toBeDefined()
  await woken.unmount()
})

test('a dismiss or snooze that finds the store locked says so and leaves the item open', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held()])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  session.store.failing.locks = 2
  for (const name of ['dismiss', 'snooze']) {
    await ui.press({ key: `${name}:1` })
    await clock.settle()
  }
  expect(session.toasts).toEqual([`projector: ${BUSY}`, `projector: ${BUSY}`])
  expect(session.store.store.get('1')).toEqual(held())
  expect(await ui.find({ type: 'Button', key: 'dismiss:1' })).toBeDefined()
  await ui.unmount()
})

test('a snooze ended while a tick reads the store brings a hidden band back', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held({ snoozedUntil: NOW + HOUR })])
  await startSession($, clock)
  await $.command.run(COMMAND)
  await clock.advance(TICK)
  await clock.settle()

  // `snooze --for 0` lands after the next tick's `list` read the store, before that list returns.
  session.store.during.list = () => session.store.write({ ...held(), snoozedUntil: clock.now() })
  session.store.write(held({ snoozedUntil: NOW + HOUR }))
  await clock.advance(TICK)
  await clock.settle()
  const hidden = await $.ui.mount(BAND)
  expect(await hidden.find({ type: 'Text', text: ENGINE_BAND })).toBeDefined()
  await hidden.unmount()

  await clock.advance(2 * TICK)
  await clock.settle()
  const woken = await $.ui.mount(BAND)
  expect(await woken.find({ type: 'Text', text: ENGINE_BAND })).toBeUndefined()
  expect(await woken.find({ type: 'Text', text: 'Decline the finding?' })).toBeDefined()
  await woken.unmount()
})

test('an item dismissed from a terminal while snoozed does not return', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [notice()])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'snooze:2' })
  await clock.advance(TICK)
  await clock.settle()
  await ui.unmount()
  await $.command.run(COMMAND)
  session.store.run(['project', 'inbox', 'dismiss', '2', '--session', SESSION])

  await clock.advance(HOUR + 2 * TICK)
  await clock.settle()
  const hidden = await $.ui.mount(BAND)
  expect(await hidden.find({ type: 'Text', text: ENGINE_BAND })).toBeDefined()
  await hidden.unmount()
})

test('a sent prompt that quotes a notice answers nothing, and closes its drawer', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [notice()])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  expect(await ui.find({ type: 'Button', key: 'discuss:2' })).toBeDefined()

  await $.prompt.submit({ text: `"${FORCE_PUSHED}"\n\nThanks`, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => /^inbox (answer|dismiss|claim)/.test(command))).toEqual([])
  expect(session.store.store.get('2')?.answer).toBeUndefined()
  // The notice stays in the band, but the reply is done with its drawer.
  expect(await ui.find({ type: 'Button', key: 'discuss:2' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: FORCE_PUSHED })).toBeDefined()
  await ui.unmount()
})

test('a snoozed item read at the start leaves a hidden band hidden until its time', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  questionWorld(on, clock, [notice({ snoozedUntil: NOW + 3 * TICK })])
  await startSession($, clock, false)
  await clock.advance(TICK)
  await clock.settle()
  const hidden = await $.ui.mount(BAND)
  expect(await hidden.find({ type: 'Text', text: ENGINE_BAND })).toBeDefined()
  await hidden.unmount()

  await clock.advance(3 * TICK)
  await clock.settle()
  const woken = await $.ui.mount(BAND)
  expect(await woken.find({ type: 'Text', text: FORCE_PUSHED })).toBeDefined()
  await woken.unmount()
})

test('an item whose snooze ends while the band is hidden is drawn the moment the band returns', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  let isSlow = false
  const github = (query: string) =>
    isSlow ? clock.sleep(10 * TICK).then(() => ran(answer(query))) : ran(answer(query))
  questionWorld(on, clock, [notice({ snoozedUntil: NOW + 3 * TICK })], { github })
  await startSession($, clock)
  await $.command.run(COMMAND)
  isSlow = true

  // The tick that sees the snooze end brings the band back, and GitHub has not answered yet.
  await clock.advance(3 * TICK)
  await clock.settle()
  const woken = await $.ui.mount(BAND)
  expect(await woken.find({ type: 'Text', text: FORCE_PUSHED })).toBeDefined()
  expect(await woken.find({ type: 'Text', text: /^─+ 1 notice · updated/ })).toBeDefined()

  await clock.advance(10 * TICK)
  await clock.settle()
  expect(await woken.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  await woken.unmount()
})
