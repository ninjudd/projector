import type { AgentInfo, FsEntry, On } from 'claude-code'
import { expect, mock, test, type Engine } from 'claude-code/testing'

import type { Notice, Question } from './types'

const HOME = '/home/ada'
const REVIEW = `${HOME}/loops/review/tracked`
const WATCH = `${HOME}/loops/watch/tracked`
const REVIEW_COMMAND = 'scripts/watch-prs.sh --tracked ~/loops/review/tracked --state ~/loops/review/state'
const WATCH_COMMAND = 'bash /plugin/skills/start-fix-loop/scripts/watch-threads.sh --tracked ~/loops/watch/tracked --state s'

// The Bash tool's own shell shows the command single-quoted; the watcher it
// started shows its arguments expanded. The watch loop runs in another session.
const PS = [
  `/bin/zsh -c eval 'scripts/watch-prs.sh --tracked "$HOME/loops/review/tracked"'`,
  `bash scripts/watch-prs.sh --tracked ${REVIEW} --state ${HOME}/loops/review/state`,
  `bash /plugin/skills/start-watch-loop/scripts/watch-threads.sh --tracked ${WATCH} --state s`,
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
  command: 'projector',
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

type Ran = ReturnType<typeof ran>

/** Answers the files, processes, prompt and Bash calls beneath the plugin, and `project` when given. */
/**
 * What a `project` command comes to: its output, or `{ deny }` for a call that
 * rejects, as one still running at its timeout does.
 */
type Outcome = Ran | { deny: string }

const QUOTE = '“Your mind is a projector; it uses everything as a kind of screen, it projects itself on the screen.” — Osho'

/** GitHub's answer to a query, where a test needs another. */
type Beneath = { github?: (query: string) => Ran }

function world(
  on: On,
  files: Record<string, string>,
  ps = PS,
  project?: (argv: string[], stdin: string) => Outcome | Promise<Outcome>,
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
    if (e.argv[0] === 'project' && project !== undefined) {
      const answered = (outcome: Outcome) => ('deny' in outcome ? outcome : { value: outcome })
      const outcome = project([...e.argv], e.init?.stdin ?? '')
      return outcome instanceof Promise ? outcome.then(answered) : answered(outcome)
    }
    return { value: e.argv[0] === 'ps' ? ran(ps) : github(e.argv.join(' ')) }
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

  // The watch loop runs in another session, so the band neither lists it nor looks for it.
  const gh = commands.filter(argv => argv[0] === 'gh')
  expect(gh.length).toBe(1)
  expect(gh[0]?.join(' ')).not.toContain('repository(owner: "x", name: "y")')
  expect(commands.filter(argv => argv[0] === 'ps')).toEqual([])

  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Change under review' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '1 open thread' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  const divider = await ui.find({ type: 'Text', text: /^─+ updated (just now|\d+s ago)$/ })
  expect(divider?.text.length).toBe(BAND.props.bodyColumns)
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

  await ui.press({ key: 'pr:x/y#7' })
  expect(
    await ui.find({ type: 'Text', text: 'watching · by ada · fix-branch · +12 −3 in 2 files' }),
  ).toBeDefined()
  expect(await ui.find({ type: 'Text', text: ' · checks passing' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: ' · updated 5m ago' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeDefined()
  expect((await ui.find({ key: 'projector:x/y#7' }))?.props.text).toBe(`[projector](${SUMMARY})`)
  expect(await ui.find({ key: 'projector:o/r#1' })).toBeUndefined()

  await ui.press({ key: 'discuss:x/y#7', link: { href: SEVEN } })
  expect(filled).toEqual(['insert:x/y#7 '])

  await ui.press({ key: 'pr:x/y#7' })
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
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

test('a tracked file named by hand takes its kind from the watcher reading it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const { commands } = world(on, { [WATCH]: 'x/y#7\n' })

  await $.command.run({ ...COMMAND, args: '~/loops/watch/tracked' })
  await clock.advance(1)
  await clock.settle()

  expect(commands.filter(argv => argv[0] === 'ps').length).toBeGreaterThanOrEqual(1)
  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'pr:x/y#7' })
  expect(await ui.find({ type: 'Text', text: /^watching · by ada/ })).toBeDefined()
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

test('shows Osho’s quote, dimmed, when no loop tracks an open pull request', async ($, on) => {
  const clock = mock.clock(on)
  mock.env(on, { HOME })
  world(on, {}, '')

  await $.command.run(COMMAND)
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect((await ui.find({ type: 'Text', text: QUOTE }))?.props.dimColor).toBe(true)
  expect(await ui.find({ type: 'Text', text: /No open pull requests/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeDefined()
  await ui.unmount()

  const desktop = await $.ui.mount({ ...BAND, surface: 'desktop' })
  const texts = (await desktop.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts[0]).toBe(QUOTE)
  expect(texts.filter(text => text.includes('─'))).toEqual([])
  await desktop.unmount()
})

test('says why GitHub failed only until the band first loads', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  let isDown = true
  const down = { ...ran(''), exitCode: 1, stderr: 'read: connection reset by peer\n' }
  world(on, { [REVIEW]: 'o/r#1\n' }, PS, undefined, { github: query => (isDown ? down : ran(answer(query))) })
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

const SESSION = 's1'
const STORE = `${HOME}/.local/state/projector/inbox/${SESSION}`
const TICK = 5_000

type HeldQuestion = Omit<Question, 'session'>
type HeldNotice = Omit<Notice, 'session'>
type Held = HeldQuestion | HeldNotice
type Clock = ReturnType<typeof mock.clock>

const HOUR = 3_600_000

const held = (fields: Partial<HeldQuestion> = {}): HeldQuestion => ({
  ref: '#7.1',
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
  ref: '#7.2',
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
  /** How `ask` fails: an exit code and its message, or `rejection` when the command never finishes. */
  const refusal: { code?: number; message?: string; rejection?: string } = {}
  /** How many of the next `list` commands fail. */
  const failing = { lists: 0 }
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

  const run = (argv: string[], stdin: string): Outcome | Promise<Outcome> => {
    const [, group, command = '', ...args] = argv
    const time = clock.now()
    if (group !== 'inbox') return failed(2, `invalid choice: '${group ?? ''}'`)
    if (command === 'list') {
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
    if (command === 'ask' || command === 'tell') {
      if (refusal.rejection !== undefined) return { deny: refusal.rejection }
      if (refusal.code !== undefined) return failed(refusal.code, refusal.message ?? '')
      const pr = flag(args, 'pr') ?? null
      posted += 1
      const ref = pr === null ? `q${posted}` : `#${pr.split('#')[1] ?? ''}.${posted}`
      const agentId = flag(args, 'agent')
      const common = {
        ref,
        number: posted,
        pr,
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
    if (command === 'context') {
      return ran(stdin.includes('#7.1.b') ? '#7.1.b answers Decline the finding? with Add a budget\n' : '')
    }

    const written = args[0] ?? ''
    const ref = command === 'answer' ? written.slice(0, written.lastIndexOf('.')) : written
    const item = store.get(ref)
    if (item === undefined) return failed(66, `no item ${ref}`)
    if (item.answer !== undefined && (command === 'answer' || command === 'dismiss' || command === 'snooze')) {
      return failed(65, `${ref} is already answered`)
    }
    const { snoozedUntil: _, ...awake } = item
    if (command === 'dismiss') {
      const answer = { dismissed: true as const, answeredAt: time }
      return printed(put(awake.kind === 'notice' ? { ...awake, answer, deliveredAt: time } : { ...awake, answer }))
    }
    if (command === 'snooze') return printed(put({ ...item, snoozedUntil: time + HOUR }))
    if (item.kind === 'notice') return failed(65, `${ref} is a notice; dismiss it with \`project inbox dismiss\``)
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

type Options = { agents?: AgentInfo[]; drop?: string; isTurnRunning?: boolean }

/** A session with a loop on x/y#7 and a store of questions beneath the plugin. */
function questionWorld(on: On, clock: Clock, seed: readonly Held[] = [], options: Options = {}) {
  const store = questionStore(clock, seed)
  const { commands, filled } = world(on, { [REVIEW]: 'x/y#7\n' }, PS, store.run)
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
  expect(String(called.text ?? called.result)).toContain('Asked #7.1.')
  expect(session.registered).toEqual([
    expect.objectContaining({ name: 'ask', isDeferred: false }),
    expect.objectContaining({ name: 'tell', isDeferred: false }),
  ])
  expect(session.project()).toContain(
    'inbox ask --session=s1 --recommend=Decline it --option=Add a budget --pr=x/y#7 -- Decline the finding?',
  )
  expect(session.notified).toEqual(['#7.1 Decline the finding?'])

  await clock.advance(1)
  await clock.settle()
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^─+ 1 question · updated (just now|\d+s ago)$/ })).toBeDefined()
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts).toContain('Conflicting change')
  expect(texts.indexOf('Conflicting change')).toBeLessThan(texts.indexOf('Decline the finding?'))
  // The line names the question's pull request as the band labels it.
  expect(await ui.find({ type: 'Text', text: '#7' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'item:#7.1' })).toBeDefined()
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
  questionWorld(on, clock, [held({ ref: 'q1', pr: null, subject: 'Run project init?' })])
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

test('an option puts the question and its answer in the prompt, and sending it answers the question', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const later = held({ ref: '#7.2', number: 2, subject: 'Rebase first?', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held({ agentId: 'agent-1' }), later], { agents: [running('running')] })
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:#7.1' })
  // The open question has the band to itself, the divider included, so it is the top row.
  expect(await ui.find({ type: 'Text', text: 'Rebase first?' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  expect((await ui.find({ key: 'detail:#7.1' }))?.props.text).toBe('The loop caps retries at three.')
  expect(await ui.find({ type: 'Text', text: ' (recommended)' })).toBeDefined()
  expect((await ui.find({ type: 'Input', key: 'answer:#7.1.c' }))?.props.placeholder).toBe('Type something')
  expect(await ui.find({ type: 'Button', key: 'discuss:#7.1' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:#7.1' })).toBeDefined()
  // A digit typed first in an empty prompt presses a band Button whose hotkey it is.
  for (const button of await ui.findAll({ type: 'Button' })) expect(button.props.hotkey).toBeUndefined()

  // Choosing an option answers nothing yet: it fills the prompt.
  await ui.press({ key: 'answer:#7.1.b', link: { href: SEVEN } })
  expect(session.filled).toEqual(['insert:"Decline the finding?"\n\nAdd a budget\n'])
  expect(session.store.store.get('#7.1')?.answer).toBeUndefined()

  const text = 'Thanks.\n"Decline the finding?"\n\nAdd a budget\n'
  await $.prompt.submit({ text, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.submitted).toEqual([
    {
      text,
      context: ['The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the answer this prompt gives.'],
    },
  ])
  expect(session.store.store.get('#7.1')?.answer).toEqual(expect.objectContaining({ letter: 'b' }))
  expect(session.store.store.get('#7.1')?.deliveredAt).toBeDefined()
  // The prompt delivered it, so the band sends nothing more.
  expect(session.sent).toEqual([])

  // The resolved question still opens, its choice marked and nothing left to press.
  expect(await ui.find({ type: 'Text', text: ' ✓' })).toBeDefined()
  expect(await ui.find({ key: 'answer:#7.1.a' })).toBeUndefined()
  expect(await ui.find({ type: 'Input', key: 'answer:#7.1.c' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:#7.1' })).toBeUndefined()

  await ui.press({ key: 'item:#7.1' })
  expect(await ui.find({ type: 'Text', text: 'Rebase first?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeDefined()
  await clock.advance(60_000 + TICK)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: /Decline the finding/ })).toBeUndefined()
  await ui.unmount()
})

test('a question another shares the text of takes only the answer its own drawer put in the prompt', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const newer = held({ ref: '#8.1', pr: 'x/y#8', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held(), newer])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:#8.1' })
  await ui.press({ key: 'answer:#8.1.a', link: { href: 'https://github.com/x/y/pull/8' } })
  expect(session.filled).toEqual(['insert:"Decline the finding?"\n\nDecline it\n'])
  await $.prompt.submit({ text: '"Decline the finding?"\n\nDecline it\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('#8.1')?.answer).toEqual(expect.objectContaining({ letter: 'a' }))
  expect(session.store.store.get('#7.1')?.answer).toBeUndefined()
  await ui.unmount()
})

test('a proposal you cleared from the prompt does not take the answer you sent for another question', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const newer = held({ ref: '#8.1', pr: 'x/y#8', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held({ postedAt: NOW - 30_000 }), newer])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:#8.1' })
  await ui.press({ key: 'answer:#8.1.a', link: { href: 'https://github.com/x/y/pull/8' } })
  // You clear that from the prompt, then choose the other answer in #7.1's drawer.
  await ui.press({ key: 'item:#8.1' })
  await ui.press({ key: 'item:#7.1' })
  await ui.press({ key: 'answer:#7.1.b', link: { href: SEVEN } })
  await $.prompt.submit({ text: '"Decline the finding?"\n\nAdd a budget\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('#7.1')?.answer).toEqual(expect.objectContaining({ letter: 'b' }))
  expect(session.store.store.get('#8.1')?.answer).toBeUndefined()
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
  expect(session.store.store.get('#7.1')?.answer).toBeUndefined()

  await $.prompt.submit({ text: '"Decline the finding?"\n\nAdd a budget', wait: false, origin: { kind: 'bridge' } })
  await clock.settle()
  expect(session.store.store.get('#7.1')?.answer).toEqual(expect.objectContaining({ letter: 'b' }))
})

test('a typed answer fills the prompt, discuss quotes the question, and dismiss resolves it', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), held({ ref: '#7.2', number: 2, subject: 'Rebase first?' })])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:#7.1' })
  await ui.input({ key: 'answer:#7.1.c', text: 'keep the cap' })
  await ui.press({ key: 'discuss:#7.1' })
  expect(session.filled).toEqual(['insert:"Decline the finding?"\n\nkeep the cap\n', 'insert:"Decline the finding?" '])

  // Text after the quoted question on its own line discusses it and answers nothing.
  const discussed = '"Decline the finding?" what does the cap cover?'
  await $.prompt.submit({ text: discussed, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('#7.1')?.answer).toBeUndefined()

  await $.prompt.submit({ text: '"Decline the finding?"\n\nkeep the cap', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('#7.1')?.answer).toEqual(expect.objectContaining({ letter: 'c', text: 'keep the cap' }))
  expect(session.project()).toContain('inbox answer #7.1.c --text=keep the cap --session s1 --json')

  await ui.press({ key: 'item:#7.1' })
  await ui.press({ key: 'item:#7.2' })
  await ui.press({ key: 'dismiss:#7.2' })
  expect(session.project()).toContain('inbox dismiss #7.2 --session s1 --json')
  await clock.advance(1)
  await clock.settle()
  expect(session.submitted.map(one => one.text)).toContain('DISMISSED #7.2 Rebase first?')
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
    { to: expect.stringContaining('agent-1'), text: 'ANSWER #7.1.a Decline the finding? → Decline it' },
  ])
  expect(session.submitted).toEqual([])
  expect(session.store.store.get('#7.1')?.deliveredAt).toBeDefined()
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
  expect(session.submitted[0]?.text).toContain('ANSWER #7.1.b Decline the finding? → Add a budget')
  expect(session.submitted[0]?.text).toContain('The subagent Fix #7 (agent-1) asked this')
  expect(session.store.store.get('#7.1')?.deliveredAt).toBeDefined()
})

test('a dropped submit reopens the question and says why', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ answer })], { drop: 'a hook refused it' })
  await startSession($, clock)
  await clock.advance(TICK)
  await clock.settle()

  expect(session.toasts).toEqual(['projector: #7.1 did not reach its agent (a hook refused it); answer it again'])
  expect(session.store.store.get('#7.1')?.answer).toBeUndefined()
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Button', key: 'item:#7.1' })).toBeDefined()
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
  expect(session.submitted.map(one => one.text)).toEqual(['ANSWER #7.1.a Decline the finding? → Decline it'])

  session.release()
  await clock.advance(TICK)
  await clock.settle()
  expect(session.store.store.get('#7.1')?.deliveredAt).toBeDefined()
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
  expect(session.submitted.map(one => one.text)).toEqual(['ANSWER #7.1.a Decline the finding? → Decline it'])
  expect(session.store.store.get('#7.1')?.deliveredAt).toBeDefined()
})

test('a submit an earlier copy of the module left keeps its claim and settles when its prompt arrives', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const text = 'ANSWER #7.1.a Decline the finding? → Decline it'
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [held({ answer, claim: { token: 'old', renewedAt: NOW } })])
  let pending: Record<string, unknown> = { '#7.1': { ref: '#7.1', session: SESSION, token: 'old', route: 'submit', text } }
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
  for (const claim of claims) expect(claim).toBe('inbox claim #7.1 --renew old --session s1')
  await $.prompt.submit({ text, wait: false, origin: { kind: 'plugin', name: 'projector' } })
  expect(session.project()).toContain('inbox delivered #7.1 --claim old --session s1 --json')
  expect(session.store.store.get('#7.1')?.deliveredAt).toBeDefined()
  expect(pending).toEqual({})
})

test('a reference typed in the prompt reaches the agent with its question', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held()])
  await startSession($, clock)

  await $.prompt.submit({ text: 'go with #7.1.b', wait: false, origin: { kind: 'composer' } })
  await $.prompt.submit({ text: 'rerun the 2 failing tests', wait: false, origin: { kind: 'composer' } })
  expect(session.submitted).toEqual([
    { text: 'go with #7.1.b', context: ['#7.1.b answers Decline the finding? with Add a budget'] },
    { text: 'rerun the 2 failing tests', context: [] },
  ])
  expect(session.project().filter(command => command.startsWith('inbox context'))).toHaveLength(1)
})

const FORCE_PUSHED = 'Force-pushed #7 to drop a stray file'

test('a subagent tells through the tool, and the band lists the notice with its own bullet', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)

  const called = await $.tool.call({ tool: 'mcp__projector__tell', notice: FORCE_PUSHED, pr: 'x/y#7', agentId: 'agent-1' })
  expect(String(called.text ?? called.result)).toBe(
    'Posted #7.1. It waits in Projector’s band until the person dismisses it; nothing comes back to you.',
  )
  expect(session.project()).toContain(`inbox tell --session=s1 --pr=x/y#7 --agent=agent-1 -- ${FORCE_PUSHED}`)
  expect(session.notified).toEqual([`#7.1 ${FORCE_PUSHED}`])

  await clock.advance(1)
  await clock.settle()
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^─+ 1 notice · updated (just now|\d+s ago)$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '• ' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '#7' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'item:#7.1' })).toBeDefined()
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
  await ui.press({ key: 'item:#7.2' })
  // An open notice has the band to itself, the divider included, as an open question does.
  expect(await ui.find({ type: 'Text', text: /^─+/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()
  expect((await ui.find({ key: 'detail:#7.2' }))?.props.text).toBe('The file held a debug token.')
  expect(await ui.find({ type: 'Text', text: ' (recommended)' })).toBeUndefined()
  for (const name of ['discuss', 'dismiss', 'snooze']) {
    expect((await ui.find({ type: 'Button', key: `${name}:#7.2` }))?.props.plain).toBe(true)
  }
  expect(await ui.findAll({ type: 'Text', text: '|' })).toHaveLength(2)

  await ui.press({ key: 'discuss:#7.2' })
  expect(session.filled).toEqual([`insert:"${FORCE_PUSHED}" `])

  await ui.press({ key: 'dismiss:#7.2' })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox dismiss'))).toEqual([
    'inbox dismiss #7.2 --session s1 --json',
  ])
  const line = await ui.find({ type: 'Text', text: FORCE_PUSHED })
  expect([line?.props.dimColor, line?.props.strikethrough]).toEqual([true, true])
  expect(await ui.find({ type: 'Button', key: 'discuss:#7.2' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:#7.2' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'snooze:#7.2' })).toBeUndefined()

  await clock.advance(TICK)
  await clock.settle()
  expect(session.sent).toEqual([])
  expect(session.submitted).toEqual([])
  expect(session.project().filter(command => command.startsWith('inbox claim'))).toEqual([])

  await ui.press({ key: 'item:#7.2' })
  expect(await ui.find({ type: 'Text', text: /^─+ 1 question · updated/ })).toBeDefined()
  await clock.advance(60_000)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: FORCE_PUSHED })).toBeUndefined()
  await ui.unmount()
})

test('snooze hides either kind for an hour, then brings a hidden band back', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), notice()])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:#7.1' })
  await ui.press({ key: 'snooze:#7.1' })
  await clock.settle()
  await ui.press({ key: 'item:#7.2' })
  await ui.press({ key: 'snooze:#7.2' })
  await clock.settle()
  expect(session.project()).toContain('inbox snooze #7.1 --session s1 --json')
  expect(session.project()).toContain('inbox snooze #7.2 --session s1 --json')
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
  await ui.press({ key: 'item:#7.2' })
  await ui.press({ key: 'snooze:#7.2' })
  await clock.advance(TICK)
  await clock.settle()
  await ui.unmount()
  await $.command.run(COMMAND)
  session.store.run(['project', 'inbox', 'dismiss', '#7.2', '--session', SESSION], '')

  await clock.advance(HOUR + 2 * TICK)
  await clock.settle()
  const hidden = await $.ui.mount(BAND)
  expect(await hidden.find({ type: 'Text', text: ENGINE_BAND })).toBeDefined()
  await hidden.unmount()
})

test('a sent prompt that quotes a notice answers nothing', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [notice()])
  await startSession($, clock)

  await $.prompt.submit({ text: `"${FORCE_PUSHED}"\n\nThanks`, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => /^inbox (answer|dismiss|claim)/.test(command))).toEqual([])
  expect(session.store.store.get('#7.2')?.answer).toBeUndefined()
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
