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
  command: 'loop-prs',
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 120 },
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

/** Answers the files, processes and Bash calls beneath the plugin. */
function world(on: On, files: Record<string, string>, ps = PS) {
  const commands: string[][] = []
  on('fs.stat', ($, e) => ({
    value: { kind: 'file' as const, size: 1, mtimeMs: 0, isLink: false, realPath: e.path },
  }))
  on('fs.read', ($, e) => ({ value: files[e.path] ?? '' }))
  on('process.run', ($, e) => {
    commands.push([...e.argv])
    return { value: ran(e.argv[0] === 'ps' ? ps : JSON.stringify(GITHUB)) }
  })
  on('tool.call', { tool: 'Bash' }, () => ({
    result: { stdout: '', stderr: '', interrupted: false },
    text: '',
  }))
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{ENGINE_BAND}</Text>
  })
  return { commands }
}

/** What the engine draws in the band when the plugin hands it on. */
const ENGINE_BAND = 'the engine’s own band'

test('draws one row per open pull request, under a Watching and a Reviewing label', async ($, on) => {
  const clock = mock.clock(on, { now: Date.UTC(2026, 9, 12, 12, 0) })
  mock.env(on, { HOME })
  const { commands } = world(on, { [REVIEW]: 'o/r#1\no/r#2\no/r#3\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const gh = commands.filter(argv => argv[0] === 'gh')
  expect(gh.length).toBe(1)
  expect(gh[0]?.join(' ')).toContain('r1: repository(owner: "x", name: "y") { p7: pullRequest(number: 7)')

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Watching' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Reviewing' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Change under review' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '1 open thread' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'conflict' })).toBeDefined()
  const divider = await ui.find({ type: 'Text', text: /^─+ updated (just now|\d+s ago)$/ })
  expect(divider?.text.length).toBe(BAND.props.bodyColumns)
  expect(await ui.find({ type: 'Text', text: 'Already merged' })).toBeUndefined()
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

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Watching' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Reviewing' })).toBeUndefined()
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
  expect(await ui.find({ type: 'Text', text: 'Reviewing' })).toBeUndefined()
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
  expect(await ui.find({ type: 'Text', text: 'Reviewing' })).toBeUndefined()
  await ui.unmount()
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

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Looking for pull requests…' })).toBeDefined()
  await ui.unmount()
})

test('says so when no loop tracks an open pull request', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, {}, '')

  await $.command.run(COMMAND)
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /No open pull requests are being watched or reviewed/ })).toBeDefined()
  await ui.unmount()
})
