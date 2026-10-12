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

const NOW = Date.UTC(2026, 9, 12, 12, 0)
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
    p2: pr(2, 'Change under review', { isDraft: true, reviewThreads: { nodes: [{ isResolved: false }] } }),
    p3: pr(3, 'Already merged', { state: 'MERGED' }),
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

/** Answers a GraphQL query with each repository it names, under the alias it gave. */
function answer(query: string): string {
  const data: Record<string, unknown> = {}
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

/** Answers the files, processes, prompt and Bash calls beneath the plugin. */
function world(on: On, files: Record<string, string>, ps = PS) {
  const commands: string[][] = []
  const filled: string[] = []
  on('fs.stat', ($, e) => ({
    value: { kind: 'file' as const, size: 1, mtimeMs: 0, isLink: false, realPath: e.path },
  }))
  on('fs.read', ($, e) => ({ value: files[e.path] ?? '' }))
  on('process.run', ($, e) => {
    commands.push([...e.argv])
    return { value: ran(e.argv[0] === 'ps' ? ps : answer(e.argv.join(' '))) }
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

test('draws one line per open pull request, with no section labels', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const { commands } = world(on, { [REVIEW]: 'o/r#1\no/r#2\no/r#3\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const gh = commands.filter(argv => argv[0] === 'gh')
  expect(gh.length).toBe(1)
  expect(gh[0]?.join(' ')).toContain('repository(owner: "x", name: "y") { p7: pullRequest(number: 7)')

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Change under review' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '1 open thread' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'conflict' })).toBeDefined()
  const divider = await ui.find({ type: 'Text', text: /^─+ updated (just now|\d+s ago)$/ })
  expect(divider?.text.length).toBe(BAND.props.bodyColumns)
  expect(await ui.find({ type: 'Text', text: 'Already merged' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /Watching|Reviewing|tracked|session|Refresh/ })).toBeUndefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  await ui.unmount()
})

test('a click on a pull request opens its drawer, and discuss puts it in the prompt', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const { filled } = world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  // A ctrl, alt or cmd click opens what the label links: the summary when
  // there is one, the pull request otherwise.
  expect((await ui.find({ key: 'pr:x/y#7' }))?.props.text).toBe(`[y#7](${SUMMARY})`)
  expect((await ui.find({ key: 'pr:o/r#1' }))?.props.text).toBe('[r#1](https://github.com/o/r/pull/1)')

  await ui.press({ key: 'pr:x/y#7', link: { href: SUMMARY } })
  expect(
    await ui.find({ type: 'Text', text: 'watching · by ada · fix-branch · +12 −3 in 2 files' }),
  ).toBeDefined()
  expect(await ui.find({ type: 'Text', text: ' · checks passing' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: ' · updated 5m ago' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeDefined()
  expect(await ui.find({ key: 'projector:x/y#7' })).toBeDefined()
  expect(await ui.find({ key: 'projector:o/r#1' })).toBeUndefined()

  await ui.press({ key: 'discuss:x/y#7', link: { href: SEVEN } })
  expect(filled).toEqual(['insert:x/y#7 '])

  await ui.press({ key: 'pr:x/y#7', link: { href: SUMMARY } })
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  await ui.unmount()
})

test('opening one drawer closes the one that was open', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'x/y#7\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'pr:x/y#7', link: { href: SUMMARY } })
  await ui.press({ key: 'pr:o/r#1', link: { href: 'https://github.com/o/r/pull/1' } })
  expect(await ui.find({ key: 'github:o/r#1' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  await ui.unmount()
})

test('lists a pull request both loops track once, as watched and reviewed', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'pr:o/r#1', link: { href: 'https://github.com/o/r/pull/1' } })
  expect(await ui.find({ type: 'Text', text: /^watching and reviewing/ })).toBeDefined()
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

test('draws over a snapshot an earlier version left in the session', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n' })
  const earlier = { watching: [], reviewing: [], fetchedAt: null, error: null }
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
