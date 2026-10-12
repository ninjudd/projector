import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

const HOME = '/home/ada'
const REVIEW = `${HOME}/loops/review/tracked`
const WATCH = `${HOME}/loops/watch/tracked`
const REVIEW_COMMAND = 'scripts/watch-prs.sh --tracked ~/loops/review/tracked --state ~/loops/review/state'

// The Bash tool's own shell shows the command single-quoted; the watcher it
// started shows its arguments expanded. The watch loop runs in another session.
const PS = [
  `/bin/zsh -c eval 'scripts/watch-prs.sh --tracked "$HOME/loops/review/tracked"'`,
  `bash scripts/watch-prs.sh --tracked ${REVIEW} --state ${HOME}/loops/review/state`,
  `bash /plugin/skills/start-fix-loop/scripts/watch-threads.sh --tracked ${WATCH} --state s`,
  '',
].join('\n')

const PANE = {
  plugin: 'projector',
  surface: 'terminal',
  component: 'Pane',
  requestId: 'loop-prs',
  props: {
    title: 'Loop PRs',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

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

const GITHUB = {
  data: {
    r0: {
      p1: pr(1, 'Signed-off change'),
      p2: pr(2, 'Change under review', { isDraft: true, reviewThreads: { nodes: [{ isResolved: false }] } }),
      p3: pr(3, 'Already merged', { state: 'MERGED' }),
    },
    r1: {
      p7: pr(7, 'Conflicting change', {
        isDraft: true,
        mergeable: 'CONFLICTING',
        reviewThreads: { nodes: [{ isResolved: false }, { isResolved: false }] },
      }),
    },
  },
}

const ran = (stdout: string) => ({
  exitCode: 0,
  stdout,
  stderr: '',
  isStdoutTruncated: false,
  isStderrTruncated: false,
})

/** Answers the files, processes, panes and Bash calls beneath the plugin. */
function world(on: On, files: Record<string, string>, ps = PS) {
  const commands: string[][] = []
  const opened: string[] = []
  on('fs.stat', ($, e) => ({
    value: { kind: 'file' as const, size: 1, mtimeMs: 0, isLink: false, realPath: e.path },
  }))
  on('fs.read', ($, e) => ({ value: files[e.path] ?? '' }))
  on('process.run', ($, e) => {
    commands.push([...e.argv])
    return { value: ran(e.argv[0] === 'ps' ? ps : JSON.stringify(GITHUB)) }
  })
  on('ui.open', ($, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true as const } }
  })
  on('tool.call', { tool: 'Bash' }, () => ({
    result: { stdout: '', stderr: '', interrupted: false },
    text: '',
  }))
  return { commands, opened }
}

test('draws the open pull requests in a Watching and a Reviewing section', async ($, on) => {
  const clock = mock.clock(on, { now: Date.UTC(2026, 9, 12, 12, 0) })
  mock.env(on, { HOME })
  const { commands, opened } = world(on, { [REVIEW]: 'o/r#1\no/r#2\no/r#3\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  expect(opened).toEqual(['loop-prs'])
  const gh = commands.filter(argv => argv[0] === 'gh')
  expect(gh.length).toBe(1)
  expect(gh[0]?.join(' ')).toContain('r1: repository(owner: "x", name: "y") { p7: pullRequest(number: 7)')

  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Watching' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Reviewing' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Change under review' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '1 open thread' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'conflict' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '2 open threads' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^updated / })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Already merged' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /merged or closed/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /tracked|session|Refresh/ })).toBeUndefined()
  await ui.unmount()
})

test('hides a section with no open pull requests', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#3\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Watching' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Reviewing' })).toBeUndefined()
  await ui.unmount()
})

test('opens the pane once for a watcher the session starts again', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  const { opened } = world(on, { [REVIEW]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  expect(opened).toEqual(['loop-prs'])
})

test('draws over a snapshot an earlier version left in the session', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n' })
  const earlier = { loops: [{ kind: 'review', tracked: REVIEW, prs: [] }], fetchedAt: null, error: null }
  on('state.get', { plugin: 'projector', key: 'loopSnapshot' }, () => ({
    value: { value: earlier as never, version: 1 },
  }))

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Looking for pull requests…' })).toBeDefined()
  await ui.unmount()
})

test('says so when no loop tracks an open pull request', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, {}, '')
  on('ui.panes', () => ({ value: [] }))

  await $.command.run({
    command: 'loop-prs',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: true, columns: 120 },
  })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount({ ...PANE, props: { ...PANE.props, placement: 'inline' } })
  expect(await ui.find({ type: 'Text', text: /No open pull requests are being watched or reviewed/ })).toBeDefined()
  await ui.unmount()
})
