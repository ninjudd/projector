import type { AgentInfo, ConfigRow, FsEntry, On } from 'claude-code'
import { expect, mock, test, type Engine, type Mounted } from 'claude-code/testing'

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

/** How wide the panel's lines are: `bodyColumns` less its one-column inset and its padding at each side. */
const COLUMNS = BAND.props.bodyColumns - 3

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
    p10: pr(10, 'Change held back', {
      mergeable: 'CONFLICTING',
      reviewDecision: 'CHANGES_REQUESTED',
      reviewThreads: { nodes: [{ isResolved: false }] },
    }),
    p11: pr(11, 'Approved change', {
      reviewDecision: 'APPROVED',
      reviewThreads: { nodes: [{ isResolved: false }, { isResolved: false }, { isResolved: false }] },
    }),
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

/**
 * GitHub's answer to a query, where a test needs another, or needs it late,
 * and the settings `.projector.toml` holds, by dotted key, which a test may
 * change between reloads.
 */
type Beneath = { github?: (query: string) => Ran | Promise<Ran>; settings?: Record<string, unknown> }

/** `project config get <key> [--default <value>] --json`, answered from `settings` as the CLI answers it. */
function configGet(argv: readonly string[], settings: Record<string, unknown>): Ran {
  const key = argv[3] ?? ''
  const at = argv.indexOf('--default')
  const value = settings[key] ?? (at === -1 ? null : argv[at + 1])
  const source = key in settings ? `${HOME}/.projector.toml` : null
  return { ...ran(JSON.stringify({ key, schema_version: 2, source, value })), exitCode: value === null ? 1 : 0 }
}

/**
 * Answers the files, GitHub, prompt and Bash calls beneath the plugin,
 * `project config get` from `settings`, and any other `project` command when
 * given.
 */
function world(
  on: On,
  files: Record<string, string>,
  project?: (argv: string[]) => Outcome | Promise<Outcome>,
  { github = query => ran(answer(query)), settings = {} }: Beneath = {},
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
    const [command, group, verb] = e.argv
    const outcome =
      command !== 'project'
        ? github(e.argv.join(' '))
        : group === 'config' && verb === 'get'
          ? configGet(e.argv, settings)
          : project !== undefined
            ? project([...e.argv])
            : github(e.argv.join(' '))
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

/** A Text's pieces as drawn: its strings, and each nested Text's text and color. */
function partsOf(text: { children: unknown[] } | undefined) {
  const parts = (text?.children ?? []) as (string | { props: { color?: string }; children: string[] })[]
  return parts.map(part => (typeof part === 'string' ? part : [part.children.join(''), part.props.color]))
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
  // Open threads show as their count before a `*`, in yellow.
  expect((await ui.findAll({ type: 'Text', text: '1*' })).map(found => found.props.color)).toContain('warning')
  expect(await ui.find({ type: 'Text', text: /open thread/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  // The panel's header starts with `/projector`, the command that shows and hides the band, at its left. At its
  // right, it counts the two open pull requests the band lists, not the merged one, and then says how long ago the
  // band reloaded as the rows say their ages: in the short form, `0s` for a reload just now as for an item posted
  // just now. The two sit two spaces apart, so the time ends in the rows' age column, at the edge of the panel's
  // lines. All of it is `subtle`, and no rule runs through it.
  const header = await ui.find({ type: 'Text', text: /^\/projector +2 pull requests  0s$/ })
  expect(header?.text).toBe(`/projector${'2 pull requests  0s'.padStart(COLUMNS - '/projector'.length)}`)
  expect(header?.props.color).toBe('subtle')
  expect(await ui.find({ type: 'Text', text: /─|updated|ago|just now/ })).toBeUndefined()
  // The rows start on the line under the header, with no blank line between.
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts.slice(0, 3)).toEqual([header?.text, 'ready', ''])
  expect(await ui.find({ type: 'Text', text: 'Already merged' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /Watching|Reviewing|tracked|session|Refresh/ })).toBeUndefined()
  expect(await ui.find({ key: 'github:o/r#1' })).toBeUndefined()
  await ui.unmount()
})

test('draws no panel and no header on a surface that does not lay text out in columns', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  for (const surface of ['desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })
    expect(await ui.find({ key: 'panel' })).toBeUndefined()
    expect((await ui.findAll({ type: 'Box' })).filter(box => box.props.backgroundColor !== undefined)).toEqual([])
    // The band starts with its first row.
    const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
    expect(texts.filter(text => text.includes('─') || text.startsWith('/projector') || /\d+s$/.test(text))).toEqual([])
    expect(texts[0]).toBe('ready')
    expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
    await ui.unmount()
  }
})

/** A terminal's band: one column of inset in the terminal's own background, then the panel, which holds every line. */
const inPanel = (backgroundColor: string) => ({
  type: 'Box',
  props: { width: BAND.props.bodyColumns, paddingLeft: 1 },
  children: [
    {
      type: 'Box',
      props: { width: COLUMNS + 2, paddingLeft: 1, paddingRight: 1, paddingBottom: 1, backgroundColor },
    },
  ],
})

/** The panel's background as drawn, or undefined when the band draws no panel. */
const backgroundOf = async (ui: Pick<Mounted, 'find'>) => (await ui.find({ key: 'panel' }))?.props.backgroundColor

test('in a terminal the band draws as one panel, inset a column and padded, on a background of its own', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  // The panel pads one column at each side and one row at its bottom, none at its top, where its header sits, and
  // ends at bodyColumns, where the engine draws the band's `[-]`. A band that has read no theme takes
  // `inverseText`.
  expect(await ui.drawn()).toMatchObject(inPanel('inverseText'))
  expect((await ui.find({ key: 'panel' }))?.props.paddingTop).toBeUndefined()
  // Only the panel paints a background, so its inset keeps the terminal's.
  expect((await ui.findAll({ type: 'Box' })).filter(box => box.props.backgroundColor !== undefined)).toHaveLength(1)
  await ui.unmount()
})

/** Who owns Claude Code's own `/config` rows. */
const ENGINE = { plugin: 'engine', tier: 'core' } as const

/** The `/config` row of Claude Code's theme, holding `value`. */
const themeRow = (value: string): ConfigRow => ({
  key: 'theme',
  label: 'Theme',
  kind: 'choice',
  value,
  provider: ENGINE,
  isLocked: false,
})

type PanelOptions = {
  theme?: string
  env?: Record<string, string>
  files?: Record<string, string>
  settings?: Record<string, unknown>
}

/**
 * A session on a Claude Code theme whose review loop tracks o/r#1, with the
 * theme's `/config` row, the files and the settings beneath the plugin. A
 * change of theme to `dark-ansi` is refused.
 */
function panelWorld(on: On, { theme = 'dark', env = {}, files = {}, settings = {} }: PanelOptions = {}) {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME, ...env })
  const { commands } = world(on, { [REVIEW]: 'o/r#1\n', ...files }, undefined, { settings })
  const toasts = toastsOf(on)
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'projector' } }))
  on('config.list', () => ({ value: [themeRow(theme)] }))
  on('config.set', ($, e) => (e.value === 'dark-ansi' ? { deny: 'the theme stays' } : { value: e.value }))
  return { clock, commands, toasts }
}

const DARK = 'rgb(20,20,20)'
const LIGHT = 'rgb(240,240,240)'
const THEMES = `${HOME}/.claude/themes`

for (const [theme, color, files] of [
  ['dark', DARK, {}],
  ['dark-daltonized', DARK, {}],
  ['light', LIGHT, {}],
  ['light-daltonized', LIGHT, {}],
  ['dark-ansi', 'inverseText', {}],
  ['light-ansi', 'inverseText', {}],
  ['custom:dusk', DARK, { [`${THEMES}/dusk.json`]: '{"name": "Dusk", "base": "dark", "overrides": {}}' }],
  ['custom:dawn', LIGHT, { [`${THEMES}/dawn.json`]: '{"name": "Dawn", "base": "light-daltonized"}' }],
  ['custom:plain', DARK, { [`${THEMES}/plain.json`]: '{"name": "Plain"}' }],
  ['custom:retro', 'inverseText', { [`${THEMES}/retro.json`]: '{"base": "dark-ansi"}' }],
  ['custom:gone', 'inverseText', {}],
  ['auto', 'inverseText', {}],
] as const) {
  test(`the panel is ${color} on the ${theme} theme`, async ($, on) => {
    const { clock } = panelWorld(on, { theme, files })
    await startSession($, clock)

    const ui = await $.ui.mount(BAND)
    expect(await ui.drawn()).toMatchObject(inPanel(color))
    await ui.unmount()
  })
}

test('a custom theme’s file is read from the Claude config directory CLAUDE_CONFIG_DIR names', async ($, on) => {
  const { clock } = panelWorld(on, {
    theme: 'custom:dawn',
    env: { CLAUDE_CONFIG_DIR: `${HOME}/claude-config` },
    files: {
      [`${HOME}/claude-config/themes/dawn.json`]: '{"base": "light"}',
      [`${THEMES}/dawn.json`]: '{"base": "dark"}',
    },
  })
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  expect(await backgroundOf(ui)).toBe(LIGHT)
  await ui.unmount()
})

test('a theme picked in /theme recolors the panel at once, and one the change refuses leaves it', async ($, on) => {
  const { clock } = panelWorld(on, { theme: 'dark' })
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  expect(await backgroundOf(ui)).toBe(DARK)

  // As the person picks a theme in `/theme`.
  const pick = (value: string, previous: string) =>
    $.config.set({ key: 'theme', value, previous, provider: ENGINE, origin: { kind: 'composer' } })
  expect(await pick('light', 'dark')).toEqual({ value: 'light' })
  expect(await backgroundOf(ui)).toBe(LIGHT)

  expect(await pick('dark-ansi', 'light')).toEqual({ deny: 'the theme stays' })
  expect(await backgroundOf(ui)).toBe(LIGHT)
  await ui.unmount()
})

test('panel.background colors the panel, and auto or no value leaves the color to the theme', async ($, on) => {
  const settings: Record<string, unknown> = { 'panel.background': '#202830' }
  const { clock, commands, toasts } = panelWorld(on, { theme: 'light', settings })
  await startSession($, clock)
  const reload = async () => {
    await $.command.run(COMMAND)
    await $.command.run(COMMAND)
    await clock.advance(1)
    await clock.settle()
  }

  // The band reads the setting through the CLI, which merges the `.projector.toml` files.
  expect(commands).toContainEqual(['project', 'config', 'get', 'panel.background', '--default', 'auto', '--json'])
  const ui = await $.ui.mount(BAND)
  expect(await backgroundOf(ui)).toBe('#202830')

  // Each reload reads it again, so an edit to the file shows without a restart.
  for (const value of ['ansi256(236)', 'rgb(30,30,40)', 'composerSidebarBackground']) {
    settings['panel.background'] = value
    await reload()
    expect(await backgroundOf(ui)).toBe(value)
  }
  settings['panel.background'] = 'auto'
  await reload()
  expect(await backgroundOf(ui)).toBe(LIGHT)
  delete settings['panel.background']
  await reload()
  expect(await backgroundOf(ui)).toBe(LIGHT)
  expect(toasts).toEqual([])
  await ui.unmount()
})

test('a panel.background no surface can draw leaves the color to the theme, and the band says so once', async ($, on) => {
  const settings: Record<string, unknown> = { 'panel.background': 'ansi:black' }
  const { clock, toasts } = panelWorld(on, { theme: 'dark', settings })
  await startSession($, clock)
  const reload = async () => {
    await $.command.run(COMMAND)
    await $.command.run(COMMAND)
    await clock.advance(1)
    await clock.settle()
  }

  // The panel draws on the theme's color rather than not at all.
  const ui = await $.ui.mount(BAND)
  expect(await ui.drawn()).toMatchObject(inPanel(DARK))
  await reload()
  expect(await backgroundOf(ui)).toBe(DARK)
  for (const value of ['', '   ', 'a/b', 'x'.repeat(41), 3]) {
    settings['panel.background'] = value
    await reload()
    expect(await backgroundOf(ui)).toBe(DARK)
  }
  const told = (value: string) =>
    `projector: panel.background is ${value}, not a color the panel can draw, so the panel takes its color from the theme`
  expect(toasts).toEqual([
    told('"ansi:black"'),
    told('""'),
    told('"   "'),
    told('"a/b"'),
    told(`"${'x'.repeat(41)}"`),
    told('3'),
  ])
  await ui.unmount()
})

test('a pull request’s notes take their own colors, and each ` · ` between them the default color', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#10\no/r#11\n' })
  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  expect(partsOf(await ui.find({ type: 'Text', text: 'conflict · changes requested · 1*' }))).toEqual([
    ['conflict', 'error'],
    ' · ',
    ['changes requested', 'error'],
    ' · ',
    ['1*', 'warning'],
  ])
  expect(partsOf(await ui.find({ type: 'Text', text: 'approved · 3*' }))).toEqual([
    ['approved', 'success'],
    ' · ',
    ['3*', 'warning'],
  ])
  expect(await ui.find({ type: 'Text', text: /⋮/ })).toBeUndefined()
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
  // The threads' count comes after the other notes, beside the age, and the ` · ` between them keeps the default color.
  expect(partsOf(await ui.find({ type: 'Text', text: 'conflict · 2*' }))).toEqual([
    ['conflict', 'error'],
    ' · ',
    ['2*', 'warning'],
  ])

  await ui.press({ key: 'pr:x/y#7' })
  // The drawer's facts name no author, whoever wrote the pull request.
  const facts = await ui.find({ type: 'Text', text: /^watching · fix-branch · \+12 −3 in 2 files · checks passing$/ })
  expect(await ui.find({ type: 'Text', text: /\bby\b/ })).toBeUndefined()
  // What the loop does takes its loop's color, the branch the palette's cyan, and the size reads as a diff stat:
  // insertions green, deletions red. The checks take theirs, and the files and each `·` keep the default color.
  expect([facts?.props.color, facts?.props.dimColor]).toEqual([undefined, undefined])
  const pieces = (facts?.children ?? []) as (string | { props: { color?: string }; children: string[] })[]
  expect(
    pieces.map(piece => (typeof piece === 'string' ? piece : [piece.children.join(''), piece.props.color])),
  ).toEqual([
    ['watching', 'ansi256(4)'],
    ' · ',
    ['fix-branch', 'ansi256(6)'],
    ' · ',
    ['+12', 'success'],
    ' ',
    ['−3', 'error'],
    ' in 2 files',
    ' · ',
    ['checks passing', 'success'],
  ])
  // The pull request's age ends its line, so the drawer does not repeat it.
  expect(await ui.find({ type: 'Text', text: /updated 5m ago/ })).toBeUndefined()
  // The drawer starts where the state does: past the three-column labels `r#1` and `y#7` and the gap after them.
  const opened = await ui.find({ key: 'drawer:x/y#7' })
  expect(opened).toBeDefined()
  expect(opened?.props.marginLeft).toBe(4)
  // Under the facts come the actions, a line each, then the links, each on a line of its own, so a terminal without hyperlinks shows the whole URL.
  const lines = (opened?.children ?? []) as { type: string; props: { key?: string } }[]
  expect(lines.map(line => line.props.key ?? line.type)).toEqual([
    'Text',
    'discuss:x/y#7',
    'dismiss:x/y#7',
    'github:x/y#7',
    'projector:x/y#7',
  ])
  expect((await ui.find({ key: 'github:x/y#7' }))?.props.text).toBe(`[open in github](${SEVEN})`)
  expect((await ui.find({ key: 'projector:x/y#7' }))?.props.text).toBe(`[open in projector](${SUMMARY})`)
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
  // The header counts everyone's pull requests the band lists, the session's own and others'.
  expect(await ui.find({ type: 'Text', text: /^\/projector +5 pull requests  \d+s$/ })).toBeDefined()
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts.filter(text => text === ' ')).toEqual([])

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
  expect(await ui.find({ type: 'Text', text: /^\/projector +2 pull requests  \d+s$/ })).toBeDefined()
  await ui.press({ key: 'pr:x/y#7' })
  // The drawer's actions are lines of their own, as an item's are.
  expect(await ui.findAll({ type: 'Text', text: '|' })).toEqual([])
  expect((await ui.find({ type: 'Button', key: 'dismiss:x/y#7' }))?.props.plain).toBe(true)

  await ui.press({ key: 'dismiss:x/y#7' })

  await ui.press({ key: 'confirm:x/y#7' })
  await clock.settle()

  expect(commands.filter(argv => argv[0] === 'project' && argv[1] === 'loop')).toEqual([
    ['project', 'loop', 'drop', 'x/y#7', '--tracked', REVIEW],
    ['project', 'loop', 'drop', 'x/y#7', '--tracked', WATCH],
  ])
  expect(toasts).toEqual(['projector: stopped watching and reviewing x/y#7'])
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Signed-off change' })).toBeDefined()
  // The header stops counting the dismissed pull request.
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request  \d+s$/ })).toBeDefined()
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
  await ui.press({ key: 'confirm:x/y#7' })
  await clock.settle()

  expect(toasts).toEqual([`projector: no tracked file at ${WATCH}`])
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeDefined()
  await ui.unmount()
})

test('a pull request’s drawer closes when its line leaves the band, so it does not open again when the line returns', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const files: Record<string, string> = { [WATCH]: 'x/y#7\n' }
  world(on, files, ([, , , reference = '', , tracked = '']) => {
    files[tracked] = `${files[tracked] ?? ''}!${reference}\n`
    return ran(`dropped ${reference} from ${tracked}\n`)
  })
  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()
  // Hiding and showing the band reloads it, as a watcher's report does.
  const reload = async () => {
    await $.command.run(COMMAND)
    await $.command.run(COMMAND)
    await clock.advance(1)
    await clock.settle()
  }

  const ui = await $.ui.mount(BAND)
  // Dropped from a terminal while its drawer is open, the pull request leaves the band.
  await ui.press({ key: 'pr:x/y#7' })
  files[WATCH] = 'x/y#7\n!x/y#7\n'
  await reload()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  // `project loop restore` brings its line back with the drawer closed.
  files[WATCH] = 'x/y#7\n'
  await reload()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()

  // So does dismissing it from its drawer.
  await ui.press({ key: 'pr:x/y#7' })
  await ui.press({ key: 'dismiss:x/y#7' })
  await ui.press({ key: 'confirm:x/y#7' })
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  files[WATCH] = 'x/y#7\n'
  await reload()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  await ui.unmount()
})

/** What the loops do with a pull request, as its open drawer's facts line starts: each word with its color, and the `and` between. */
async function loopsOf(ui: Pick<Mounted, 'find'>, key: string) {
  const drawer = await ui.find({ key: `drawer:${key}` })
  const facts = ((drawer?.children ?? []) as { children?: unknown[] }[])[0]
  type Piece = string | { props: { color?: string }; children: string[] }
  const pieces = (facts?.children ?? []) as Piece[]
  const loops = pieces.slice(0, pieces.findIndex(piece => piece === ' · '))
  return loops.map(piece => (typeof piece === 'string' ? piece : [piece.children.join(''), piece.props.color]))
}

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
  expect(await loopsOf(ui, 'x/y#7')).toEqual([['watching', 'ansi256(4)']])
  await ui.press({ key: 'pr:o/r#1' })
  expect(await ui.find({ key: 'github:o/r#1' })).toBeDefined()
  expect(await ui.find({ key: 'github:x/y#7' })).toBeUndefined()
  // Reviewing takes the theme's `claude` orange, as watching takes the palette's blue.
  expect(await loopsOf(ui, 'o/r#1')).toEqual([['reviewing', 'claude']])
  await ui.unmount()
})

test('lists a pull request both loops track once, as watched and reviewed, and counts it once', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  world(on, { [REVIEW]: 'o/r#1\n', [WATCH]: 'o/r#1\n' })

  await $.tool.call({ tool: 'Bash', command: REVIEW_COMMAND })
  await $.tool.call({ tool: 'Bash', command: WATCH_COMMAND })
  await clock.advance(1)
  await clock.settle()

  const ui = await $.ui.mount(BAND)
  // The header counts its one line once, in the singular.
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request  \d+s$/ })).toBeDefined()
  await ui.press({ key: 'pr:o/r#1' })
  expect(await ui.find({ type: 'Text', text: /^watching and reviewing/ })).toBeDefined()
  // Each loop takes its own color, and the `and` between them the default color.
  expect(await loopsOf(ui, 'o/r#1')).toEqual([['watching', 'ansi256(4)'], ' and ', ['reviewing', 'claude']])
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
  // The band runs nothing but its reload's read of the panel's setting.
  expect(commands.filter(argv => argv.slice(0, 3).join(' ') !== 'project config get')).toEqual([])
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
  // With nothing to count, the header is `/projector` and the reload's age alone, at the right of the panel's line,
  // and the tagline is the line under it, in the panel.
  const header = await ui.find({ type: 'Text', text: /^\/projector +\d+s$/ })
  expect(header?.text.length).toBe(COLUMNS)
  expect(header?.props.color).toBe('subtle')
  expect((await ui.findAll({ type: 'Text' })).map(found => found.text)).toEqual([header?.text, ` ${TAGLINE}`])
  expect(await ui.drawn()).toMatchObject(inPanel('inverseText'))
  await ui.unmount()

  const desktop = await $.ui.mount({ ...BAND, surface: 'desktop' })
  const texts = (await desktop.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts).toEqual([` ${TAGLINE}`])
  expect(await desktop.find({ key: 'panel' })).toBeUndefined()
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

/** An item with the named fields cleared. */
const without = <T extends Held>(item: T, ...keys: string[]): T =>
  Object.fromEntries(Object.entries(item).filter(([key]) => !keys.includes(key))) as T

/** A question with its answer and claim cleared, as `reopen` leaves it. */
const reopened = (question: HeldQuestion): HeldQuestion => without(question, 'answer', 'claim')

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
    question.restoredAt !== undefined
      ? `RESTORED ${question.ref} ${question.subject}`
      : question.answer !== undefined && 'letter' in question.answer
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
    if (['ask', 'tell', 'withdraw', 'revise', 'restore'].includes(command)) {
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
    if (command === 'restore') {
      if (item.answer === undefined) {
        if (item.snoozedUntil === undefined || time >= item.snoozedUntil) {
          return failed(65, `${ref} is open, so there is nothing to restore`)
        }
        return printed(put(without(item, 'snoozedUntil', 'snoozedAt')))
      }
      if ('withdrawn' in item.answer) {
        return failed(65, `${ref} was withdrawn by the agent that posted it, so it is that agent's to post again`)
      }
      if (!('dismissed' in item.answer)) return failed(65, `${ref} is already answered`)
      const restored = without(item, 'answer', 'deliveredAt', 'claim')
      if (restored.kind === 'question' && (item.deliveredAt !== undefined || 'claim' in item)) {
        return printed(put({ ...restored, restoredAt: time }))
      }
      return printed(put(restored))
    }
    // Dismissing or answering an item drops word of a restore not yet claimed, as the CLI does.
    const awake = without(item, 'snoozedUntil', 'snoozedAt', 'restoredAt', 'claim')
    const isUnsaid = 'restoredAt' in item && !('claim' in item)
    if (command === 'dismiss') {
      const answer = { dismissed: true as const, answeredAt: time }
      const isDelivered = awake.kind === 'notice' || isUnsaid
      return printed(put(isDelivered ? { ...awake, answer, deliveredAt: time } : { ...awake, answer }))
    }
    if (command === 'withdraw') {
      const answer = { dismissed: true as const, withdrawn: true as const, answeredAt: time }
      return printed(put({ ...awake, answer, deliveredAt: time }))
    }
    if (command === 'snooze') {
      const minutes = Number(flag(args, 'for') ?? 60)
      return printed(put({ ...item, snoozedAt: time, snoozedUntil: time + minutes * 60_000 }))
    }
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
      if (item.restoredAt === undefined && (item.answer === undefined || item.deliveredAt !== undefined)) {
        return failed(65, 'nothing to deliver')
      }
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
      // A restore's line is done either way.
      if (item.restoredAt !== undefined) return printed(put(without(item, 'claim', 'restoredAt')))
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
    expect.objectContaining({ name: 'restore', isDeferred: false }),
  ])
  expect(session.project()).toContain(
    'inbox ask --session=s1 --recommend=Decline it --option=Add a budget --pr=x/y#7 -- Decline the finding?',
  )
  // You never need the number, so the notification holds the question alone.
  expect(session.notified).toEqual(['Decline the finding?'])

  await clock.advance(1)
  await clock.settle()
  const ui = await $.ui.mount(BAND)
  // The header counts the pull request and then the question before the reload's age, ending at the panel's edge.
  const header = await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question  \d+s$/ })
  expect(header?.text.length).toBe(COLUMNS)
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts).toContain('Conflicting change')
  expect(texts.indexOf('Conflicting change')).toBeLessThan(texts.indexOf('Decline the finding?'))
  // The line names the question's pull request as the band labels it.
  expect(await ui.find({ type: 'Text', text: '#7' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'item:1' })).toBeDefined()
  await ui.unmount()
})

test('every item takes the `-` bullet, an open question the permission color, an open notice gray, and a resolved or snoozed one subtle', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const dismissed = { dismissed: true as const, answeredAt: NOW - 1_000 }
  questionWorld(on, clock, [
    held(),
    notice(),
    held({ ref: '3', number: 3, subject: 'Rebase first?', answer, deliveredAt: NOW - 1_000 }),
    notice({ ref: '4', number: 4, subject: 'Pushed a fix', answer: dismissed, deliveredAt: NOW - 1_000 }),
    notice({ ref: '5', number: 5, subject: 'Merged #6', snoozedAt: NOW - 1_000, snoozedUntil: NOW + HOUR }),
  ])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  type Part = { props?: { color?: string; strikethrough?: boolean; inverse?: boolean }; children: string[] }
  /** Each part of an item's line, the bullet then the subject: its text, color, and strikethrough. */
  const lineOf = async (key: string) => {
    const parts = ((await ui.find({ type: 'Button', key }))?.children ?? []) as Part[]
    return parts.map(part => [part.children.join(''), part.props?.color, part.props?.strikethrough === true])
  }

  expect(await lineOf('item:1')).toEqual([
    ['- ', 'permission', false],
    ['Decline the finding?', 'permission', false],
  ])
  // An open notice's line takes the theme's mid gray, `inactive`, apart from the `subtle` of a resolved item.
  expect(await lineOf('item:2')).toEqual([
    ['- ', 'inactive', false],
    [FORCE_PUSHED, 'inactive', false],
  ])
  expect(await lineOf('item:3')).toEqual([
    ['- ', 'subtle', false],
    ['Rebase first?', 'subtle', false],
  ])
  expect(await lineOf('item:4')).toEqual([
    ['- ', 'subtle', false],
    ['Pushed a fix', 'subtle', true],
  ])
  expect(await lineOf('item:5')).toEqual([
    ['- ', 'subtle', false],
    ['Merged #6', 'subtle', false],
  ])
  expect((await ui.findAll({ type: 'Text' })).filter(text => text.props.dimColor !== undefined)).toEqual([])

  // An open item's line drops its bullet, for the `0` the engine draws in its place, and is not inverted: its subject
  // keeps the item's color, `permission` for an open question.
  await ui.press({ key: 'item:1' })
  const opened = ((await ui.find({ type: 'Button', key: 'close:1' }))?.children ?? []) as Part[]
  expect(opened.map(part => [part.children.join(''), part.props?.color, part.props?.inverse])).toEqual([
    ['Decline the finding?', 'permission', undefined],
  ])
  await ui.press({ key: 'close:1' })
  await ui.press({ key: 'item:2' })
  const openNotice = ((await ui.find({ type: 'Button', key: 'close:2' }))?.children ?? []) as Part[]
  expect(openNotice.map(part => [part.children.join(''), part.props?.color, part.props?.inverse])).toEqual([
    [FORCE_PUSHED, 'inactive', undefined],
  ])
  // The rule after it stays in the default color.
  expect((await ui.find({ type: 'Text', text: /^ ─+$/ }))?.props.color).toBeUndefined()
  await ui.press({ key: 'close:2' })
  // A resolved or snoozed item's open line keeps `subtle`, which shows its state.
  await ui.press({ key: 'item:3' })
  const resolved = ((await ui.find({ type: 'Button', key: 'close:3' }))?.children ?? []) as Part[]
  expect(resolved.map(part => [part.children.join(''), part.props?.color])).toEqual([['Rebase first?', 'subtle']])
  await ui.press({ key: 'close:3' })
  await ui.press({ key: 'item:5' })
  const snoozed = ((await ui.find({ type: 'Button', key: 'close:5' }))?.children ?? []) as Part[]
  expect(snoozed.map(part => [part.children.join(''), part.props?.color])).toEqual([['Merged #6', 'subtle']])
  await ui.unmount()
})

test('a pull request’s age and an item’s age end in one column', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  questionWorld(on, clock, [held({ postedAt: NOW - 12 * 60_000 })])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  // The pull request changed 5m ago and the question was posted 12m ago, so both ages take the wider one's width.
  const ages = await Promise.all(['x/y#7', 'item:1'].map(key => ui.find({ key: `age:${key}` })))
  expect(ages.map(age => [age?.text, age?.props.width, age?.props.justifyContent])).toEqual([
    ['5m', 3, 'flex-end'],
    ['12m', 3, 'flex-end'],
  ])
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
  expect(texts.slice(failure + 1, failure + 3)).toEqual([' ', '- '])
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

test('an idle session with no questions runs no inbox command', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock)
  await startSession($, clock)
  await clock.advance(3 * TICK)
  await clock.settle()

  // Listing the store's directory starts no process; only a reload of the shown band reads the panel's setting.
  expect(session.project().filter(command => !command.startsWith('config get'))).toEqual([])
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request  \d+s$/ })).toBeDefined()
  await ui.unmount()
})

/** The Buttons of a question's options, in the order its drawer draws them. */
async function optionsOf(ui: Pick<Mounted, 'findAll'>) {
  return (await ui.findAll({ type: 'Button' })).filter(button => button.key?.startsWith('answer:'))
}

/** The keys of the band's Buttons that a digit presses. */
async function digitsOf(ui: Pick<Mounted, 'findAll'>) {
  const buttons = await ui.findAll({ type: 'Button' })
  return buttons.flatMap(button => (/^\d$/.test(String(button.props.hotkey ?? '')) ? [button.key] : []))
}

test('an option puts the question and its answer in the prompt, and sending it answers the question and closes its drawer', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const later = held({ ref: '2', number: 2, subject: 'Rebase first?', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held({ agentId: 'agent-1' }), later], { agents: [running('running')] })
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:1' })
  // The open question has the panel to itself, its header included, so it is the top row.
  expect(await ui.drawn()).toMatchObject(inPanel('inverseText'))
  expect((await ui.findAll({ type: 'Text' }))[0]?.text).toBe('Decline the finding?')
  expect(await ui.find({ type: 'Text', text: 'Rebase first?' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^\/projector/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Conflicting change' })).toBeUndefined()
  expect((await ui.find({ key: 'detail:1' }))?.props.text).toBe('The loop caps retries at three.')
  // An answer in your own words goes through discuss, so only the options are numbered.
  expect(await ui.find({ type: 'Input' })).toBeUndefined()
  expect(await ui.find({ key: 'answer:1.c' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'discuss:1' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:1' })).toBeDefined()
  // Each option is a plain Button on its digit, which the engine draws before it, so the band draws no number.
  const options = await optionsOf(ui)
  expect(options.map(option => [option.key, option.props.hotkey, option.props.plain, option.text])).toEqual([
    ['answer:1.a', '1', true, 'Decline it (recommended)'],
    ['answer:1.b', '2', true, 'Add a budget'],
  ])
  expect(await ui.find({ type: 'Text', text: /^\d\.$/ })).toBeUndefined()

  // Choosing an option answers nothing yet: it fills the prompt, and the drawer stays open.
  await ui.press({ key: 'answer:1.b' })
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

  // The resolved question still opens, its choice marked, the rest muted, and nothing left to press or number.
  await ui.press({ key: 'item:1' })
  expect(await ui.find({ type: 'Text', text: ' ✓' })).toBeDefined()
  expect((await ui.find({ type: 'Text', text: /^Add a budget/ }))?.props.color).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /^Decline it/ }))?.props.color).toBe('subtle')
  expect((await ui.find({ type: 'Text', text: /^Decline the finding\?$/ }))?.props.color).toBe('subtle')
  expect(await optionsOf(ui)).toEqual([])
  // Only the line itself, on `0`, and discuss, on `7`, take a digit.
  expect(await digitsOf(ui)).toEqual(['close:1', 'discuss:1'])
  expect(await ui.find({ type: 'Button', key: 'dismiss:1' })).toBeUndefined()

  await ui.press({ key: 'close:1' })
  expect(await ui.find({ type: 'Text', text: 'Rebase first?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question  \d+s$/ })).toBeDefined()
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
  await ui.press({ key: 'answer:2.a' })
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
  await ui.press({ key: 'answer:2.a' })
  // You clear that from the prompt, then choose the other answer in the older question's drawer.
  await ui.press({ key: 'close:2' })
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'answer:1.b' })
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
  await ui.press({ key: 'close:1' })

  // An answer typed from a terminal shows after the options, marked.
  await ui.press({ key: 'item:3' })
  const typedAnswer = await ui.find({ type: 'Text', text: /^keep the cap ✓$/ })
  expect(typedAnswer?.props.color).toBeUndefined()
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text)
  expect(texts.indexOf(typedAnswer?.text ?? '')).toBeGreaterThan(texts.findIndex(text => text.startsWith('Add a budget')))

  await ui.press({ key: 'close:3' })
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'dismiss:2' })
  await ui.press({ key: 'confirm:2' })
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
  await ui.press({ key: 'answer:1.b' })
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
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question  \d+s$/ })).toBeDefined()

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
  await ui.press({ key: 'answer:2.b' })
  await ui.press({ key: 'close:2' })
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

test('a quote two questions share goes to the one whose drawer you acted in last', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const merge = { subject: 'Merge it?', choices: ['Yes', 'No'] }
  const second = held({ ...merge, ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000 })
  const session = questionWorld(on, clock, [held(merge), second])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  // You pick Yes in the second question's drawer, think better of it, and discuss the first instead.
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a' })
  await ui.press({ key: 'close:2' })
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'discuss:1' })
  await $.prompt.submit({ text: '"Merge it?"\n\nYes\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([
    'inbox answer 1.a --session s1 --json',
  ])
  expect(session.store.store.get('2')).toEqual(second)
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

test('a dropped RESTORED says so without asking for an answer, and is not sent again', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held({ restoredAt: NOW - 1_000 })], { drop: 'a hook refused it' })
  await startSession($, clock)
  await clock.advance(TICK)
  await clock.settle()

  expect(session.submitted.map(one => one.text)).toEqual(['RESTORED 1 Decline the finding?'])
  expect(session.toasts).toEqual([
    'projector: word that "Decline the finding?" is open again did not reach its agent (a hook refused it)',
  ])
  expect(session.store.store.get('1')).toEqual(held())
  await clock.advance(TICK)
  await clock.settle()
  expect(session.submitted).toHaveLength(1)
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

test('a subagent tells through the tool, and the band lists the notice in gray', async ($, on) => {
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
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 notice  \d+s$/ })).toBeDefined()
  expect((await ui.find({ type: 'Text', text: FORCE_PUSHED }))?.props.color).toBe('inactive')
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
    await ui.press({ key: `close:${ref}` })
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
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question · 1 notice  \d+s$/ })).toBeDefined()
  await ui.press({ key: 'item:2' })
  // An open notice has the panel to itself, its header included, as an open question does.
  expect(await ui.drawn()).toMatchObject(inPanel('inverseText'))
  expect(await ui.find({ type: 'Text', text: /^\/projector/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()
  expect((await ui.find({ key: 'detail:2' }))?.props.text).toBe('The file held a debug token.')
  expect(await ui.find({ type: 'Text', text: ' (recommended)' })).toBeUndefined()
  for (const name of ['discuss', 'dismiss', 'snooze']) {
    expect((await ui.find({ type: 'Button', key: `${name}:2` }))?.props.plain).toBe(true)
  }
  expect(await actionLinesOf(ui)).toEqual([
    'discuss:2 7 discuss notice',
    'dismiss:2 8 dismiss notice',
    'snooze:2 9 snooze notice',
  ])

  await ui.press({ key: 'discuss:2' })
  expect(session.filled).toEqual([`insert:"${FORCE_PUSHED}"\n\n`])

  await ui.press({ key: 'dismiss:2' })

  await ui.press({ key: 'confirm:2' })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox dismiss'))).toEqual([
    'inbox dismiss 2 --session s1 --json',
  ])
  // Dismissing closes the drawer, and the line stays, muted and struck through.
  const line = await ui.find({ type: 'Text', text: FORCE_PUSHED })
  expect([line?.props.color, line?.props.strikethrough]).toEqual(['subtle', true])
  expect(await ui.find({ type: 'Button', key: 'discuss:2' })).toBeUndefined()
  // Its line opens the drawer again, where restore takes dismiss's place.
  await ui.press({ key: 'item:2' })
  expect(await ui.find({ type: 'Button', key: 'discuss:2' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'restore:2' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'dismiss:2' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'snooze:2' })).toBeUndefined()

  await clock.advance(TICK)
  await clock.settle()
  expect(session.sent).toEqual([])
  expect(session.submitted).toEqual([])
  expect(session.project().filter(command => command.startsWith('inbox claim'))).toEqual([])

  await ui.press({ key: 'close:2' })
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question  \d+s$/ })).toBeDefined()
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
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 notice  \d+s$/ })).toBeDefined()
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
  await ui.press({ key: 'answer:1.b' })

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
  expect((await optionsOf(ui)).map(option => [option.props.hotkey, option.text])).toEqual([
    ['1', 'Decline it (recommended)'],
    ['2', 'Add a budget of two per host'],
    ['3', 'Ask the reviewer'],
  ])

  await ui.press({ key: 'close:1' })
  await $.tool.call({ tool: 'mcp__projector__revise', number: 1, question: 'Decline the retry finding?' })
  expect(await ui.find({ type: 'Text', text: 'Decline the retry finding?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'item:1' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question  \d+s$/ })).toBeDefined()

  // The answer you chose from the old text answers nothing.
  await $.prompt.submit({ text: '"Decline the finding?"\n\nAdd a budget\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('1')?.answer).toBeUndefined()

  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'answer:1.c' })
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
  await ui.press({ key: 'answer:2.a' })
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
  await ui.press({ key: 'answer:2.a' })
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
  await ui.press({ key: 'answer:2.a' })
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

const DECLINE = '"Decline the finding?"\n\nDecline it\n'

test('a pick for a question withdrawn before you send answers no other question', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), held({ ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000 })])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a' })
  await $.tool.call({ tool: 'mcp__projector__withdraw', number: 2 })
  await $.prompt.submit({ text: DECLINE, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
  await ui.unmount()
})

test('a pick made before a revise, for a question dismissed before you send, answers no other question', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), held({ ref: '2', number: 2, pr: 'x/y#8', postedAt: NOW - 60_000 })])
  await startSession($, clock)

  const ui = await $.ui.mount(BAND)
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'answer:2.a' })
  await $.tool.call({ tool: 'mcp__projector__revise', number: 2, detail: 'The cap is three.' })
  await ui.press({ key: 'dismiss:2' })
  await ui.press({ key: 'confirm:2' })
  await clock.settle()
  expect(session.store.store.get('2')?.answer).toEqual(expect.objectContaining({ dismissed: true }))
  await $.prompt.submit({ text: DECLINE, wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.project().filter(command => command.startsWith('inbox answer'))).toEqual([])
  expect(session.store.store.get('1')?.answer).toBeUndefined()
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
  await ui.press({ key: 'snooze-60:1' })
  await clock.settle()
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'snooze:2' })
  await ui.press({ key: 'snooze-60:2' })
  await clock.settle()
  expect(session.project()).toContain('inbox snooze 1 --for 60 --session s1 --json')
  expect(session.project()).toContain('inbox snooze 2 --for 60 --session s1 --json')
  // Each stays listed for 30 seconds, out of the header's counts, and then leaves the band.
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request  \d+s$/ })).toBeDefined()
  await clock.advance(30_000 + TICK)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: /Decline the finding|Force-pushed/ })).toBeUndefined()
  await ui.unmount()

  await clock.advance(TICK)
  await clock.settle()
  await $.command.run(COMMAND)
  await clock.advance(HOUR - 2 * TICK - 30_000)
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
  expect(await woken.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question · 1 notice  \d+s$/ })).toBeDefined()
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
  for (const keys of [['dismiss:1', 'confirm:1'], ['snooze:1', 'snooze-60:1']]) {
    for (const key of keys) await ui.press({ key })
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
  await ui.press({ key: 'snooze-60:2' })
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
  expect(await woken.find({ type: 'Text', text: /^\/projector +1 pull request · 1 notice  \d+s$/ })).toBeDefined()

  await clock.advance(10 * TICK)
  await clock.settle()
  expect(await woken.find({ type: 'Text', text: 'Conflicting change' })).toBeDefined()
  await woken.unmount()
})

/** The keys of an item's drawer actions, in the order its row draws them. */
async function actionsOf(ui: Pick<Mounted, 'findAll'>, ref: string): Promise<string[]> {
  const pattern = new RegExp(`^(discuss|dismiss|snooze|unsnooze|restore):${ref}$`)
  return (await ui.findAll({ type: 'Button' })).map(button => button.key ?? '').filter(key => pattern.test(key))
}

test('dismiss closes an item’s drawer and strikes it through, and its drawer offers restore in dismiss’s place', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held()])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  const line = () => ui.find({ type: 'Text', text: 'Decline the finding?' })

  await ui.press({ key: 'item:1' })
  expect(await actionsOf(ui, '1')).toEqual(['discuss:1', 'dismiss:1', 'snooze:1'])
  await ui.press({ key: 'dismiss:1' })
  await ui.press({ key: 'confirm:1' })
  await clock.settle()
  // The drawer closes, and the line stays, muted in the subtle color and struck through.
  expect(await ui.find({ key: 'detail:1' })).toBeUndefined()
  const dismissed = await line()
  expect([dismissed?.props.color, dismissed?.props.dimColor, dismissed?.props.strikethrough]).toEqual([
    'subtle',
    undefined,
    true,
  ])

  // Its line opens the drawer again, where restore takes dismiss's place.
  await ui.press({ key: 'item:1' })
  expect(await actionsOf(ui, '1')).toEqual(['discuss:1', 'restore:1'])
  await ui.press({ key: 'restore:1' })
  await clock.settle()
  expect(session.project()).toContain('inbox restore 1 --session s1 --json')
  // The drawer stays open on the question, open again, with dismiss back in its place.
  expect(await ui.find({ key: 'detail:1' })).toBeDefined()
  expect(await ui.find({ key: 'answer:1.a' })).toBeDefined()
  expect(await actionsOf(ui, '1')).toEqual(['discuss:1', 'dismiss:1', 'snooze:1'])
  const restored = await line()
  expect([restored?.props.color, restored?.props.strikethrough]).toEqual(['permission', false])
  expect(session.store.store.get('1')).toEqual(held())

  // The dismissal had not gone out, so the restore sends nothing.
  await clock.advance(TICK)
  await clock.settle()
  expect(session.submitted).toEqual([])

  // Once the agent has heard DISMISSED, a restore sends RESTORED after it.
  await ui.press({ key: 'dismiss:1' })
  await ui.press({ key: 'confirm:1' })
  await clock.advance(1)
  await clock.settle()
  expect(session.submitted.map(one => one.text)).toEqual(['DISMISSED 1 Decline the finding?'])
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'restore:1' })
  await clock.advance(1)
  await clock.settle()
  expect(session.submitted.map(one => one.text)).toEqual([
    'DISMISSED 1 Decline the finding?',
    'RESTORED 1 Decline the finding?',
  ])
  expect(Object.keys(session.store.store.get('1') ?? {})).not.toContain('restoredAt')

  // Picking an option answers it as if it had never been dismissed.
  await ui.press({ key: 'answer:1.b' })
  await $.prompt.submit({ text: '"Decline the finding?"\n\nAdd a budget\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('1')?.answer).toEqual(expect.objectContaining({ letter: 'b' }))
  await ui.unmount()
})

test('snooze closes an item’s drawer and mutes it for 30 seconds, and its drawer offers unsnooze in snooze’s place', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), notice()])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  const line = () => ui.find({ type: 'Text', text: FORCE_PUSHED })

  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'snooze:2' })
  await ui.press({ key: 'snooze-60:2' })
  await clock.settle()
  expect(session.project()).toContain('inbox snooze 2 --for 60 --session s1 --json')
  // The drawer closes, and the line stays in the subtle color, not struck through, out of the header's count.
  expect(await ui.find({ key: 'detail:2' })).toBeUndefined()
  const snoozed = await line()
  expect([snoozed?.props.color, snoozed?.props.dimColor, snoozed?.props.strikethrough]).toEqual([
    'subtle',
    undefined,
    false,
  ])
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question  \d+s$/ })).toBeDefined()

  // Its line opens the drawer again, where unsnooze takes snooze's place.
  await ui.press({ key: 'item:2' })
  expect(await actionsOf(ui, '2')).toEqual(['discuss:2', 'dismiss:2', 'unsnooze:2'])
  await ui.press({ key: 'unsnooze:2' })
  await clock.settle()
  expect(session.project()).toContain('inbox restore 2 --session s1 --json')
  expect(await ui.find({ key: 'detail:2' })).toBeDefined()
  expect(await actionsOf(ui, '2')).toEqual(['discuss:2', 'dismiss:2', 'snooze:2'])
  expect((await line())?.props.color).toBe('inactive')
  expect(session.store.store.get('2')).toEqual(notice())

  // Snoozed again, it leaves the band 30 seconds later, and nothing reached the agent.
  await ui.press({ key: 'snooze:2' })
  await ui.press({ key: 'snooze-15:2' })
  await clock.advance(30_000 - TICK)
  await clock.settle()
  expect(await line()).toBeDefined()
  await clock.advance(2 * TICK)
  await clock.settle()
  expect(await line()).toBeUndefined()
  expect(session.submitted).toEqual([])
  expect(session.project().filter(command => command.startsWith('inbox claim'))).toEqual([])
  await ui.unmount()
})

test('the restore tool brings a dismissed item back at once, its old drawer closed, and its agent hears RESTORED', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const dismissed = { dismissed: true as const, answeredAt: NOW - 1_000 }
  const heard = held({ answer: dismissed, deliveredAt: NOW - 1_000 })
  const read = notice({ answer: dismissed, deliveredAt: NOW - 1_000 })
  const session = questionWorld(on, clock, [heard, read])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)

  // You open the dismissed question's drawer, and the question leaves the band 30 seconds after its dismissal.
  await ui.press({ key: 'item:1' })
  expect(await actionsOf(ui, '1')).toEqual(['discuss:1', 'restore:1'])
  await clock.advance(30_000)
  await clock.settle()
  expect(await ui.find({ type: 'Text', text: 'Decline the finding?' })).toBeUndefined()

  const called = await $.tool.call({ tool: 'mcp__projector__restore', number: 1 })
  expect(String(called.text ?? called.result)).toBe(
    'Restored 1. It is open in Projector’s band again, and its answer arrives as a message that starts ANSWER 1 or DISMISSED 1. An agent that already heard DISMISSED 1 hears RESTORED 1 first.',
  )
  expect(session.project()).toContain('inbox restore 1 --session s1 --json')
  // Before the band reads the store again, the question is open in the band, and its old drawer stays closed.
  const line = await ui.find({ type: 'Text', text: 'Decline the finding?' })
  expect([line?.props.color, line?.props.strikethrough]).toEqual(['permission', false])
  expect(await ui.find({ key: 'detail:1' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 1 question  \d+s$/ })).toBeDefined()
  await clock.advance(TICK)
  await clock.settle()
  expect(session.submitted.map(one => one.text)).toEqual(['RESTORED 1 Decline the finding?'])

  // A notice comes back too, and nothing goes to the agent.
  const told = await $.tool.call({ tool: 'mcp__projector__restore', number: 2 })
  expect(String(told.text ?? told.result)).toBe(
    'Restored 2. It is back in Projector’s band until the person dismisses it; nothing comes back to you.',
  )
  expect(await ui.find({ type: 'Text', text: FORCE_PUSHED })).toBeDefined()
  await clock.advance(TICK)
  await clock.settle()
  expect(session.submitted).toHaveLength(1)
  await ui.unmount()
})

test('a restore brings a hidden band back, from the tool or from a terminal, as a new item does', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const dismissed = { dismissed: true as const, answeredAt: NOW - 60_000 }
  const session = questionWorld(on, clock, [
    held({ answer: dismissed, deliveredAt: NOW - 60_000 }),
    notice({ snoozedAt: NOW - 60_000, snoozedUntil: NOW + HOUR }),
    held({ ref: '3', number: 3, subject: 'Rebase first?', answer: dismissed, deliveredAt: NOW - 60_000 }),
  ])
  await startSession($, clock)
  const isHidden = async () => {
    const ui = await $.ui.mount(BAND)
    const hidden = (await ui.find({ type: 'Text', text: ENGINE_BAND })) !== undefined
    await ui.unmount()
    return hidden
  }
  const hide = async () => {
    await $.command.run(COMMAND)
    await clock.advance(TICK)
    await clock.settle()
    expect(await isHidden()).toBe(true)
  }

  await hide()
  await $.tool.call({ tool: 'mcp__projector__restore', number: 1 })
  await clock.advance(1)
  await clock.settle()
  expect(await isHidden()).toBe(false)

  // Unsnoozing or restoring from a terminal brings it back on the next tick.
  for (const ref of ['2', '3']) {
    await hide()
    session.store.run(['project', 'inbox', 'restore', ref, '--session', SESSION])
    await clock.advance(TICK)
    await clock.settle()
    expect(await isHidden()).toBe(false)
  }
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: /^\/projector +1 pull request · 2 questions · 1 notice  \d+s$/ })).toBeDefined()
  await ui.unmount()
})

test('the restore tool refuses what the CLI refuses, asks an old CLI to upgrade, and says when it hangs', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const answered = held({ answer, deliveredAt: NOW - 1_000 })
  const session = questionWorld(on, clock, [answered, notice()])
  await startSession($, clock)
  const restore = { tool: 'mcp__projector__restore' as const, number: 1 }

  const resolved = await $.tool.call(restore)
  expect(resolved.deny ?? resolved.text).toBe('1 is already answered')

  const open = await $.tool.call({ ...restore, number: 2 })
  expect(open.deny ?? open.text).toBe('2 is open, so there is nothing to restore')

  const unknown = await $.tool.call({ ...restore, number: 9 })
  expect(unknown.deny ?? unknown.text).toBe('no item 9')

  session.store.failing.locks = 1
  const busy = await $.tool.call(restore)
  expect(busy.deny ?? busy.text).toBe(BUSY)

  Object.assign(session.store.refusal, { code: 2, message: "error: argument inbox_command: invalid choice: 'restore'" })
  const old = await $.tool.call(restore)
  expect(old.deny ?? old.text).toBe("error: argument inbox_command: invalid choice: 'restore'; run project upgrade")

  Object.assign(session.store.refusal, { code: undefined, rejection: 'project timed out after 15000 ms' })
  const hung = await $.tool.call(restore)
  expect(hung.deny ?? hung.text).toMatch(
    /^project did not finish \(.*\); tell the person in the conversation that it could not be restored$/,
  )
  expect(session.store.store.get('1')).toEqual(answered)
  expect(session.store.store.get('2')).toEqual(notice())
})

test('an open question’s options are Buttons on the digits 1 to 6, only while its drawer is open', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  // The recommendation and five more options, the most the ask tool takes.
  const choices = ['Decline it', 'Add a budget', 'Cap per host', 'Ask the reviewer', 'Wait a day', 'Split the change']
  const session = questionWorld(on, clock, [held({ choices }), notice()])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  const digits = () => digitsOf(ui)

  // A closed drawer leaves the digits to the prompt, and a notice's drawer takes only `0`, on its line, and its actions'.
  expect(await digits()).toEqual([])
  await ui.press({ key: 'item:2' })
  expect(await digits()).toEqual(['close:2', 'discuss:2', 'dismiss:2', 'snooze:2'])
  await ui.press({ key: 'close:2' })

  await ui.press({ key: 'item:1' })
  expect((await optionsOf(ui)).map(option => [option.key, option.props.hotkey])).toEqual([
    ['answer:1.a', '1'],
    ['answer:1.b', '2'],
    ['answer:1.c', '3'],
    ['answer:1.d', '4'],
    ['answer:1.e', '5'],
    ['answer:1.f', '6'],
  ])
  // Pressing the sixth, by its digit or Enter, fills the prompt as clicking its number did, and sending it answers.
  await ui.press({ key: 'answer:1.f' })
  expect(session.filled).toEqual(['insert:"Decline the finding?"\n\nSplit the change\n'])
  await $.prompt.submit({ text: '"Decline the finding?"\n\nSplit the change\n', wait: false, origin: { kind: 'composer' } })
  await clock.settle()
  expect(session.store.store.get('1')?.answer).toEqual(expect.objectContaining({ letter: 'f', text: 'Split the change' }))

  // The answered question's drawer lists its options with no digits; only its line and discuss take one.
  await ui.press({ key: 'item:1' })
  expect(await ui.find({ type: 'Text', text: /^Split the change ✓$/ })).toBeDefined()
  expect(await digits()).toEqual(['close:1', 'discuss:1'])
  await ui.unmount()
})

test('an open item’s line closes its drawer on 0, and no other line takes 0', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const dismissed = { dismissed: true as const, answeredAt: NOW - 1_000 }
  const snoozed = { snoozedAt: NOW - 1_000, snoozedUntil: NOW + HOUR }
  questionWorld(on, clock, [
    held(),
    notice(),
    held({ ref: '3', number: 3, subject: 'Rebase first?', answer, deliveredAt: NOW - 1_000 }),
    notice({ ref: '4', number: 4, subject: 'Pushed a fix', answer: dismissed, deliveredAt: NOW - 1_000 }),
    notice({ ref: '5', number: 5, subject: 'Merged #6', ...snoozed }),
  ])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  const zeros = async () =>
    (await ui.findAll({ type: 'Button' })).flatMap(button => (button.props.hotkey === '0' ? [button.key] : []))

  // No line of the list takes `0`, nor a pull request's drawer.
  expect(await zeros()).toEqual([])
  await ui.press({ key: 'pr:x/y#7' })
  expect(await zeros()).toEqual([])
  await ui.press({ key: 'pr:x/y#7' })

  // Each open item's line takes `0`, whatever state the item is in, and pressing it closes the drawer.
  for (const ref of ['1', '2', '3', '4', '5']) {
    await ui.press({ key: `item:${ref}` })
    const line = await ui.find({ type: 'Button', key: `close:${ref}` })
    expect([line?.props.hotkey, line?.props.plain]).toEqual(['0', true])
    expect(await zeros()).toEqual([`close:${ref}`])
    await ui.press({ key: `close:${ref}` })
    expect(await ui.find({ type: 'Button', key: `close:${ref}` })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: `item:${ref}` })).toBeDefined()
  }
  await ui.unmount()
})

/** The open drawer's action lines, in order: each one's key, the letter that presses it, and its label. */
async function actionLinesOf(ui: Pick<Mounted, 'findAll'>): Promise<string[]> {
  const actions = (await ui.findAll({ type: 'Button' })).filter(button =>
    /^(discuss|dismiss|snooze|unsnooze|restore):/.test(button.key ?? ''),
  )
  return actions.map(action => `${action.key ?? ''} ${String(action.props.hotkey ?? '')} ${action.text}`)
}

/**
 * How far an item's open drawer is indented, beside the width of the key prefix
 * the engine draws before the item's line, as `0: `: equal when the drawer's
 * lines start under the item's text.
 */
async function drawerIndentOf(ui: Pick<Mounted, 'find'>, ref: string): Promise<[unknown, number]> {
  const drawer = await ui.find({ key: `drawer:item:${ref}` })
  const line = await ui.find({ type: 'Button', key: `close:${ref}` })
  return [drawer?.props.marginLeft, `${String(line?.props.hotkey ?? '')}: `.length]
}

/** An item's open drawer, row by row: a Text by its text, any other element by its key or type. */
async function drawerRowsOf(ui: Pick<Mounted, 'findAll'>): Promise<string[]> {
  const drawer = (await ui.findAll({ type: 'Box' })).find(box => box.key?.startsWith('drawer:item:'))
  type Row = { type: string; props: { key?: string }; children?: unknown[] }
  return ((drawer?.children ?? []) as Row[]).map(row =>
    row.type === 'Text'
      ? `text:${(row.children ?? []).filter(part => typeof part === 'string').join('')}`
      : (row.props.key ?? row.type),
  )
}

test('a drawer’s actions follow its options a line each, on 7, 8 and 9, named for what they act on', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  questionWorld(on, clock, [held(), notice()])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)

  // No line of the list takes a key; only an open drawer's lines do.
  expect((await ui.findAll({ type: 'Button' })).filter(button => button.props.hotkey !== undefined)).toEqual([])

  // A question's options and actions are one list: the options on 1 to 6, then the actions on 7, 8 and 9.
  await ui.press({ key: 'item:1' })
  expect((await ui.findAll({ type: 'Button' })).map(button => button.key)).toEqual([
    'close:1',
    'answer:1.a',
    'answer:1.b',
    'discuss:1',
    'dismiss:1',
    'snooze:1',
  ])
  expect(await actionLinesOf(ui)).toEqual([
    'discuss:1 7 discuss question',
    'dismiss:1 8 dismiss question',
    'snooze:1 9 snooze question',
  ])
  // The drawer starts under the item's text, past the `0: ` the engine draws before it, so `1:` and the detail sit
  // under the question's first letter.
  expect(await drawerIndentOf(ui, '1')).toEqual([3, 3])
  // A blank line sets the actions apart from the options, as one sets the detail apart, and no `|` joins them.
  expect(await drawerRowsOf(ui)).toEqual([
    'text: ',
    'detail:1',
    'text: ',
    'answer:1.a',
    'answer:1.b',
    'text: ',
    'discuss:1',
    'dismiss:1',
    'snooze:1',
  ])
  expect(await ui.findAll({ type: 'Text', text: '|' })).toEqual([])

  // The lengths snooze offers take the options' place, under the detail, with the blank line above them.
  await ui.press({ key: 'snooze:1' })
  expect(await drawerRowsOf(ui)).toEqual([
    'text: ',
    'detail:1',
    'text: ',
    'snooze-15:1',
    'snooze-60:1',
    'snooze-240:1',
    'snooze-1440:1',
    'cancel:1',
  ])

  // Snoozed, unsnooze takes snooze's place.
  await ui.press({ key: 'snooze-60:1' })
  await clock.settle()
  await ui.press({ key: 'item:1' })
  expect(await actionLinesOf(ui)).toEqual([
    'discuss:1 7 discuss question',
    'dismiss:1 8 dismiss question',
    'unsnooze:1 9 unsnooze question',
  ])

  // Dismissed, restore takes dismiss's place, and a resolved item has nothing to snooze.
  await ui.press({ key: 'dismiss:1' })
  await ui.press({ key: 'confirm:1' })
  await clock.settle()
  await ui.press({ key: 'item:1' })
  expect(await actionLinesOf(ui)).toEqual(['discuss:1 7 discuss question', 'restore:1 8 restore question'])
  // A resolved question still lists its options, with the blank line under them, under the item's text.
  expect((await drawerRowsOf(ui)).slice(2)).toEqual([
    'text: ',
    'text:Decline it',
    'text:Add a budget',
    'text: ',
    'discuss:1',
    'restore:1',
  ])
  expect(await drawerIndentOf(ui, '1')).toEqual([3, 3])
  await ui.press({ key: 'close:1' })

  // A notice's actions name a notice.
  await ui.press({ key: 'item:2' })
  expect(await actionLinesOf(ui)).toEqual([
    'discuss:2 7 discuss notice',
    'dismiss:2 8 dismiss notice',
    'snooze:2 9 snooze notice',
  ])
  expect(await drawerRowsOf(ui)).toEqual(['text: ', 'detail:2', 'text: ', 'discuss:2', 'dismiss:2', 'snooze:2'])
  expect(await drawerIndentOf(ui, '2')).toEqual([3, 3])
  await ui.press({ key: 'close:2' })

  // A pull request's drawer has its actions above its links, the links with no letters.
  await ui.press({ key: 'pr:x/y#7' })
  expect(await actionLinesOf(ui)).toEqual(['discuss:x/y#7 7 discuss PR', 'dismiss:x/y#7 8 dismiss PR'])
  await ui.unmount()
})

test('a keyed line’s label takes the color of the `: ` before it, so it inverts as one block', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  questionWorld(on, clock, [held()])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)

  // The engine draws a plain Button's key in its accent color and the `: ` in
  // the Button's own color, which takes no color, `hover`, or `dimColor` from
  // the band. A label of plain text, with no colored child, takes that same
  // color, at rest and inverted.
  const partsOf = async (key: string) => {
    const line = await ui.find({ type: 'Button', key })
    return {
      key: line?.props.hotkey,
      plain: line?.props.plain,
      line: [line?.props.color, line?.props.hover, line?.props.dimColor],
      label: [line?.props.label, line?.children],
    }
  }
  await ui.press({ key: 'item:1' })
  expect(await partsOf('answer:1.a')).toEqual({
    key: '1',
    plain: true,
    line: [undefined, undefined, undefined],
    label: ['Decline it (recommended)', []],
  })
  expect(await partsOf('answer:1.b')).toEqual({
    key: '2',
    plain: true,
    line: [undefined, undefined, undefined],
    label: ['Add a budget', []],
  })
  expect(await partsOf('dismiss:1')).toEqual({
    key: '8',
    plain: true,
    line: [undefined, undefined, undefined],
    label: ['dismiss question', []],
  })
  await ui.press({ key: 'close:1' })

  await ui.press({ key: 'pr:x/y#7' })
  expect(await partsOf('discuss:x/y#7')).toEqual({
    key: '7',
    plain: true,
    line: [undefined, undefined, undefined],
    label: ['discuss PR', []],
  })
  await ui.unmount()
})

/**
 * Moves the band's focus ring as the person's Tab or ctrl+x tab does, and
 * records each element the band then tried to move the ring onto. The kit
 * serves no `$.ui.focus`, so it refuses every move the band tries, and the
 * debug line the band writes for a refused move names the element.
 */
function keyboardOf(on: On) {
  const tried: string[] = []
  on('ui.focus', () => ({}))
  on('ui.log', ($, e) => {
    const element = /^projector: the focus stayed off (\S+): /.exec(e.text)?.[1]
    if (element !== undefined) tried.push(element)
    return { value: undefined }
  })
  const tab = ($: Engine, element: string) =>
    $.ui.focus({
      component: 'AbovePrompt',
      requestId: 'above-prompt',
      plugin: 'projector',
      element,
      origin: { kind: 'person' },
    })
  return { tried, tab }
}

test('Enter on a line moves the ring into the drawer it opens and back onto the line, and a click moves nothing', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const answer = { letter: 'a', text: 'Decline it', answeredAt: NOW - 1_000 }
  const answered = held({ ref: '3', number: 3, answer, deliveredAt: NOW - 1_000 })
  questionWorld(on, clock, [held(), notice(), answered])
  const { tried, tab } = keyboardOf(on)
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)

  // A click, with the ring on nothing or on another line, leaves the ring alone.
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'close:1' })
  await tab($, 'pr:x/y#7')
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'close:1' })
  expect(tried).toEqual([])

  // Enter on a question's line moves the ring onto its first option, and Enter
  // on the open line moves it back onto the line in the list.
  await tab($, 'item:1')
  await ui.press({ key: 'item:1' })
  expect(tried).toEqual(['answer:1.a'])
  await tab($, 'close:1')
  await ui.press({ key: 'close:1' })
  expect(tried).toEqual(['answer:1.a', 'item:1'])

  // A notice's drawer and an answered question's have no options to press, so the ring goes onto discuss.
  await tab($, 'item:2')
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'close:2' })
  await tab($, 'item:3')
  await ui.press({ key: 'item:3' })
  await ui.press({ key: 'close:3' })
  expect(tried).toEqual(['answer:1.a', 'item:1', 'discuss:2', 'discuss:3'])

  // A pull request's drawer opens under its line, so closing it leaves the ring where it is.
  await tab($, 'pr:x/y#7')
  await ui.press({ key: 'pr:x/y#7' })
  await ui.press({ key: 'pr:x/y#7' })
  expect(tried).toEqual(['answer:1.a', 'item:1', 'discuss:2', 'discuss:3', 'discuss:x/y#7'])
  await ui.unmount()
})

test('snooze asks how long on 1 to 4 in the options’ place, and only a length it offers snoozes the item', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held(), notice()])
  const { tried, tab } = keyboardOf(on)
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  /** The open drawer's keyed lines under its own line, each by its key, its digit, and what it reads. */
  const lines = async (ref: string) =>
    (await ui.findAll({ type: 'Button' }))
      .filter(button => button.key?.endsWith(`:${ref}`) && button.key !== `close:${ref}`)
      .filter(button => !button.key?.startsWith('answer:') || button.key.startsWith(`answer:${ref}.`))
      .map(button => `${button.key ?? ''} ${String(button.props.hotkey ?? '')} ${button.text}`)
  const lengths = (ref: string) => [
    `snooze-15:${ref} 1 15 min`,
    `snooze-60:${ref} 2 1 hour`,
    `snooze-240:${ref} 3 4 hours`,
    `snooze-1440:${ref} 4 1 day`,
    `cancel:${ref} 9 cancel`,
  ]
  const snoozes = () => session.project().filter(command => command.startsWith('inbox snooze'))

  // A question's options give way to the lengths, on 1 to 4, and its actions to cancel, on 9, so no digit is shared.
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'snooze:1' })
  expect(await optionsOf(ui)).toEqual([])
  expect(await lines('1')).toEqual(lengths('1'))
  expect(await digitsOf(ui)).toEqual([
    'close:1',
    'snooze-15:1',
    'snooze-60:1',
    'snooze-240:1',
    'snooze-1440:1',
    'cancel:1',
  ])
  expect(snoozes()).toEqual([])
  // 9 cancels: the options and actions come back.
  await ui.press({ key: 'cancel:1' })
  expect((await optionsOf(ui)).map(option => option.key)).toEqual(['answer:1.a', 'answer:1.b'])
  expect(await actionLinesOf(ui)).toEqual([
    'discuss:1 7 discuss question',
    'dismiss:1 8 dismiss question',
    'snooze:1 9 snooze question',
  ])
  await ui.press({ key: 'close:1' })

  // A notice shows the same lines, and snoozes nothing yet.
  const actions = ['discuss:2 7 discuss notice', 'dismiss:2 8 dismiss notice', 'snooze:2 9 snooze notice']
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'snooze:2' })
  expect(await lines('2')).toEqual(lengths('2'))
  expect(await ui.findAll({ type: 'Text', text: '|' })).toEqual([])
  expect(snoozes()).toEqual([])
  await ui.press({ key: 'cancel:2' })
  expect(await lines('2')).toEqual(actions)

  // Closing the drawer puts the actions back too, so it opens again on its actions.
  await ui.press({ key: 'snooze:2' })
  await ui.press({ key: 'close:2' })
  await ui.press({ key: 'item:2' })
  expect(await lines('2')).toEqual(actions)
  expect(snoozes()).toEqual([])

  // From the keyboard, snooze moves the ring onto the first length, and cancel moves it back onto snooze.
  await tab($, 'snooze:2')
  await ui.press({ key: 'snooze:2' })
  await tab($, 'cancel:2')
  await ui.press({ key: 'cancel:2' })
  expect(tried).toEqual(['snooze-15:2', 'snooze:2'])

  // Each length snoozes the item for its minutes.
  for (const minutes of [15, 60, 240, 1440]) {
    await ui.press({ key: 'snooze:2' })
    await ui.press({ key: `snooze-${minutes}:2` })
    await clock.settle()
    expect(session.store.store.get('2')?.snoozedUntil).toBe(clock.now() + minutes * 60_000)
    await ui.press({ key: 'item:2' })
    await ui.press({ key: 'unsnooze:2' })
    await clock.settle()
  }
  expect(snoozes()).toEqual([15, 60, 240, 1440].map(minutes => `inbox snooze 2 --for ${minutes} --session s1 --json`))
  await ui.unmount()
})

test('dismiss asks to be sure on 8 and 9, and restore and unsnooze act at once', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const dismissed = { dismissed: true as const, answeredAt: NOW - 1_000 }
  const session = questionWorld(on, clock, [
    held(),
    notice(),
    held({ ref: '3', number: 3, subject: 'Rebase first?', answer: dismissed, deliveredAt: NOW - 1_000 }),
    notice({ ref: '4', number: 4, subject: 'Merged #6', snoozedAt: NOW - 1_000, snoozedUntil: NOW + HOUR }),
  ])
  const { tried, tab } = keyboardOf(on)
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  /** The open drawer's keyed lines under its options, each by its key, its digit, and what it reads. */
  const lines = async (id: string) =>
    (await ui.findAll({ type: 'Button' }))
      .filter(button => /^(discuss|dismiss|snooze|unsnooze|restore|confirm|keep):/.test(button.key ?? ''))
      .filter(button => button.key?.endsWith(`:${id}`))
      .map(button => `${button.key ?? ''} ${String(button.props.hotkey ?? '')} ${button.text}`)
  const ran = (command: string) => session.project().filter(line => line.startsWith(`inbox ${command}`))

  // No line of an open drawer takes a letter any more.
  await ui.press({ key: 'item:1' })
  const keys = (await ui.findAll({ type: 'Button' })).map(button => String(button.props.hotkey ?? ''))
  expect(keys.filter(key => key !== '' && !/^\d$/.test(key))).toEqual([])

  // Dismiss runs nothing: it asks, on 8 to dismiss and 9 to keep the item.
  await ui.press({ key: 'dismiss:1' })
  expect(await lines('1')).toEqual(['confirm:1 8 yes, dismiss question', 'keep:1 9 no, keep it'])
  expect(ran('dismiss')).toEqual([])
  // 9 keeps it and puts the actions back.
  await ui.press({ key: 'keep:1' })
  expect(await lines('1')).toEqual([
    'discuss:1 7 discuss question',
    'dismiss:1 8 dismiss question',
    'snooze:1 9 snooze question',
  ])
  // Closing the drawer puts them back too.
  await ui.press({ key: 'dismiss:1' })
  await ui.press({ key: 'close:1' })
  await ui.press({ key: 'item:1' })
  expect((await lines('1'))[1]).toBe('dismiss:1 8 dismiss question')
  expect(ran('dismiss')).toEqual([])

  // From the keyboard, the ring goes onto the confirmation, and from 9 back onto dismiss.
  await tab($, 'dismiss:1')
  await ui.press({ key: 'dismiss:1' })
  await tab($, 'keep:1')
  await ui.press({ key: 'keep:1' })
  expect(tried).toEqual(['confirm:1', 'dismiss:1'])

  // 8 then 8 dismisses.
  await ui.press({ key: 'dismiss:1' })
  await ui.press({ key: 'confirm:1' })
  await clock.settle()
  expect(ran('dismiss')).toEqual(['inbox dismiss 1 --session s1 --json'])
  expect(await ui.find({ key: 'detail:1' })).toBeUndefined()

  // A notice's confirmation names a notice.
  await ui.press({ key: 'item:2' })
  await ui.press({ key: 'dismiss:2' })
  expect(await lines('2')).toEqual(['confirm:2 8 yes, dismiss notice', 'keep:2 9 no, keep it'])
  await ui.press({ key: 'close:2' })

  // Restore and unsnooze need no confirmation.
  await ui.press({ key: 'item:3' })
  await ui.press({ key: 'restore:3' })
  await clock.settle()
  expect(ran('restore')).toEqual(['inbox restore 3 --session s1 --json'])
  await ui.press({ key: 'close:3' })
  await ui.press({ key: 'item:4' })
  await ui.press({ key: 'unsnooze:4' })
  await clock.settle()
  expect(ran('restore')).toEqual(['inbox restore 3 --session s1 --json', 'inbox restore 4 --session s1 --json'])
  await ui.press({ key: 'close:4' })

  // A pull request's dismiss asks the same, and 9 keeps it in its loops.
  await ui.press({ key: 'pr:x/y#7' })
  expect(await lines('x/y#7')).toEqual(['discuss:x/y#7 7 discuss PR', 'dismiss:x/y#7 8 dismiss PR'])
  await ui.press({ key: 'dismiss:x/y#7' })
  expect(await lines('x/y#7')).toEqual(['confirm:x/y#7 8 yes, dismiss PR', 'keep:x/y#7 9 no, keep it'])
  await ui.press({ key: 'keep:x/y#7' })
  expect(await lines('x/y#7')).toEqual(['discuss:x/y#7 7 discuss PR', 'dismiss:x/y#7 8 dismiss PR'])
  expect(session.project().filter(line => line.startsWith('loop drop'))).toEqual([])
  await ui.unmount()
})

test('a question a drawer asked is forgotten once its item is resolved or snoozed, so a restored item opens on its actions', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held()])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  /** The open drawer's keyed lines under its options. */
  const lines = async () =>
    (await ui.findAll({ type: 'Button' }))
      .map(button => button.key ?? '')
      .filter(key => key.endsWith(':1') && !key.startsWith('close:') && !key.startsWith('answer:'))
  const actions = ['discuss:1', 'dismiss:1', 'snooze:1']

  // Dismiss asks, and a terminal dismisses the question meanwhile; restoring it opens its drawer on its actions.
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'dismiss:1' })
  session.store.run(['project', 'inbox', 'dismiss', '1', '--session', SESSION])
  await clock.advance(TICK)
  await clock.settle()
  expect(await lines()).toEqual(['discuss:1', 'restore:1'])
  await ui.press({ key: 'restore:1' })
  await clock.settle()
  expect(await lines()).toEqual(actions)
  expect(session.project().filter(command => command.startsWith('inbox dismiss'))).toEqual([])

  // Snooze asks how long, and a terminal snoozes it meanwhile; unsnoozing it brings back its options and actions.
  await ui.press({ key: 'snooze:1' })
  session.store.run(['project', 'inbox', 'snooze', '1', '--session', SESSION])
  await clock.advance(TICK)
  await clock.settle()
  expect(await lines()).toEqual(['discuss:1', 'dismiss:1', 'unsnooze:1'])
  await ui.press({ key: 'unsnooze:1' })
  await clock.settle()
  expect(await lines()).toEqual(actions)
  expect((await optionsOf(ui)).map(option => option.key)).toEqual(['answer:1.a', 'answer:1.b'])
  await ui.unmount()
})

test('an open item’s line ends in a rule to the panel’s edge on a terminal, beside its Button', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const long = 'Should the band '.padEnd(112, 'x')
  questionWorld(on, clock, [held(), held({ ref: '2', number: 2, pr: null, subject: long })])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  const rule = async () => (await ui.find({ type: 'Text', text: /^ ─+$/ }))?.text

  // The rule fills the panel's line: `0: `, the subject, a space and the rule, then ` #7` and ` 2m`, end at the
  // panel's edge, three columns short of bodyColumns.
  await ui.press({ key: 'item:1' })
  const drawn = await rule()
  expect(drawn).toBe(` ${'─'.repeat(87)}`)
  expect('0: '.length + 'Decline the finding?'.length + (drawn ?? '').length + ' #7'.length + ' 2m'.length).toBe(
    COLUMNS,
  )
  // It sits beside the line's Button, so a hover or the focus ring inverts the title alone, and it draws in the
  // default color while an open question's title takes `permission`.
  expect((await ui.find({ type: 'Button', key: 'close:1' }))?.text).toBe('Decline the finding?')
  expect((await ui.find({ type: 'Text', text: /^ ─+$/ }))?.props.color).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: 'Decline the finding?' }))?.props.color).toBe('permission')
  await ui.press({ key: 'close:1' })
  // No line in the list draws one.
  expect(await rule()).toBeUndefined()

  // A title that leaves too little room draws none.
  await ui.press({ key: 'item:2' })
  expect(await rule()).toBeUndefined()
  await ui.unmount()

  // Another surface draws the title alone, as it draws no header.
  const desktop = await $.ui.mount({ ...BAND, surface: 'desktop' })
  await desktop.press({ key: 'close:2' })
  await desktop.press({ key: 'item:1' })
  expect(await desktop.find({ type: 'Text', text: /─/ })).toBeUndefined()
  await desktop.unmount()
})

test('a dismiss confirmation on a snoozed item survives an unrelated change to the store', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  const session = questionWorld(on, clock, [held({ snoozedAt: NOW - 1_000, snoozedUntil: NOW + HOUR })])
  await startSession($, clock)
  const ui = await $.ui.mount(BAND)
  const lines = async () =>
    (await ui.findAll({ type: 'Button' }))
      .map(button => button.key ?? '')
      .filter(key => /^(discuss|dismiss|unsnooze|confirm|keep):1$/.test(key))

  // The item is snoozed already when dismiss asks to be sure.
  await ui.press({ key: 'item:1' })
  await ui.press({ key: 'dismiss:1' })
  expect(await lines()).toEqual(['confirm:1', 'keep:1'])

  // Another item arriving makes the band read the store, and the question stands.
  session.store.write(notice())
  await clock.advance(TICK)
  await clock.settle()
  expect(await lines()).toEqual(['confirm:1', 'keep:1'])
  await ui.unmount()
})
