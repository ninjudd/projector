import { expect, mock, test } from 'claude-code/testing'

const HOME = '/home/ada'
const REVIEW = `${HOME}/loops/review/tracked`
const FIX = `${HOME}/loops/fix/tracked`

const FILES: Record<string, string> = {
  [REVIEW]: 'o/r#1\no/r#2\no/r#3\n',
  [FIX]: 'x/y#7\n',
}

// The Bash tool's own shell shows the command single-quoted; the watcher it
// started shows its arguments expanded. A fix loop runs in another session.
const PS = [
  `/bin/zsh -c eval 'scripts/watch-prs.sh --tracked "$HOME/loops/review/tracked"'`,
  `bash scripts/watch-prs.sh --tracked ${REVIEW} --state ${HOME}/loops/review/state`,
  `bash /plugin/skills/start-fix-loop/scripts/watch-threads.sh --tracked ${FIX} --state s`,
  '',
].join('\n')

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
      p7: pr(7, 'Conflicting fix', {
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

test('draws the open pull requests of the loops the session can see', async ($, on) => {
  const clock = mock.clock(on, { now: Date.UTC(2026, 9, 12, 12, 0) })
  mock.env(on, { HOME })

  const commands: string[][] = []
  const opened: string[] = []
  on('fs.stat', ($, e) => ({
    value: { kind: 'file' as const, size: 1, mtimeMs: 0, isLink: false, realPath: e.path },
  }))
  on('fs.read', ($, e) => ({ value: FILES[e.path] ?? '' }))
  on('process.run', ($, e) => {
    commands.push([...e.argv])
    return { value: ran(e.argv[0] === 'ps' ? PS : JSON.stringify(GITHUB)) }
  })
  on('ui.open', ($, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true as const } }
  })
  on('tool.call', { tool: 'Bash' }, () => ({
    result: { stdout: '', stderr: '', interrupted: false },
    text: '',
  }))

  await $.tool.call({
    tool: 'Bash',
    command: 'scripts/watch-prs.sh --tracked ~/loops/review/tracked --state ~/loops/review/state --once',
  })
  await clock.advance(1)
  await clock.settle()

  expect(opened).toEqual(['loop-prs'])
  const gh = commands.filter(argv => argv[0] === 'gh')
  expect(gh.length).toBe(1)
  expect(gh[0]?.join(' ')).toContain('r1: repository(owner: "x", name: "y") { p7: pullRequest(number: 7)')

  const ui = await $.ui.mount({
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
  })

  expect(await ui.find({ type: 'Text', text: 'Review loop' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /this session/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Fix loop' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /another session/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Change under review' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting fix' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Already merged' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: '1 merged or closed' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '1 open thread' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'conflict' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '2 open threads' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^Updated \d\d:\d\d/ })).toBeDefined()

  await ui.unmount()
})

test('says how to start when no loop is running', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  on('process.run', () => ({ value: ran('') }))
  on('ui.open', () => ({ value: { isPlaced: true as const } }))
  on('ui.panes', () => ({ value: [] }))

  await $.command.run({
    command: 'loop-prs',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: true, columns: 120 },
  })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount({
    plugin: 'projector',
    surface: 'terminal',
    component: 'Pane',
    requestId: 'loop-prs',
    props: {
      title: 'Loop PRs',
      isFocused: false,
      bodyColumns: 60,
      placement: 'inline',
      scroll: { offset: 0, bodyRows: 10 },
      view: {},
    },
  })
  expect(await ui.find({ type: 'Text', text: /No fix or review loop is running/ })).toBeDefined()
  await ui.unmount()
})
