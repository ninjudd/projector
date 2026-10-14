import { describe, expect, test } from 'claude-code/testing'

import { claudeCodeTheme, contrast, resolve, UNKNOWN_THEME } from './theme'

const DARK = claudeCodeTheme('dark')
const LIGHT = claudeCodeTheme('light')

/** Each role's color with no settings, less the background. */
const DEFAULTS = {
  muted: 'subtle',
  question: 'permission',
  notice: 'inactive',
  success: 'success',
  warning: 'warning',
  error: 'error',
  added: 'success',
  removed: 'error',
  watching: 'remember',
  reviewing: 'claude',
  branch: 'planMode',
}

const BACKGROUND_NAMES =
  'ink, umber, stone, graphite, smoke, black, white, rgb(r,g,b), #rrggbb, or ansi256(n)'
const ROLE_VALUES = 'rgb(r,g,b), #rrggbb, ansi256(n), a Claude Code theme color such as subtle, or default'

const rounded = (ratio: number | undefined) => (ratio === undefined ? undefined : Math.round(ratio * 100) / 100)

describe('contrast', () => {
  for (const [one, other, ratio] of [
    ['rgb(0,0,0)', 'rgb(255,255,255)', 21],
    ['#fff', '#000000', 21],
    ['rgb(20,20,20)', 'rgb( 20, 20, 20 )', 1],
    ['rgb(40,40,40)', '#100f0c', 1.3],
    ['rgb(70,70,70)', '#100f0c', 2.03],
    ['ansi256(16)', 'ansi256(231)', 21],
    ['ansi256(232)', 'rgb(8,8,8)', 1],
    ['ansi256(255)', 'rgb(238,238,238)', 1],
    ['ansi256(67)', 'rgb(95,135,175)', 1],
  ] as const) {
    test(`${one} against ${other} is ${ratio}:1`, () => {
      expect(rounded(contrast(one, other))).toBe(ratio)
    })
  }

  for (const [what, color] of [
    ['a palette slot, which the terminal sets', 'ansi256(4)'],
    ['a number past 255', 'rgb(256,0,0)'],
    ['a plain name', 'gray'],
    ['a theme key on a theme whose colors the panel does not hold', 'subtle'],
  ]) {
    test(`is unknown for ${what}`, () => {
      expect(contrast(color ?? '', 'rgb(0,0,0)')).toBeUndefined()
    })
  }

  test('measures a theme key by the theme’s colors', () => {
    expect(rounded(contrast('subtle', 'rgb(80,80,80)', DARK.palette))).toBe(1)
    expect(rounded(contrast('subtle', 'rgb(175,175,175)', LIGHT.palette))).toBe(1)
    expect(rounded(contrast('composerSidebarBackground', 'rgb(38,38,38)', DARK.palette))).toBe(1)
  })
})

describe('claudeCodeTheme', () => {
  test('knows the base of every built-in theme, and the colors of those that are not the terminal’s', () => {
    expect([DARK.base, DARK.palette?.subtle]).toEqual(['dark', 'rgb(80,80,80)'])
    expect([LIGHT.base, LIGHT.palette?.subtle]).toEqual(['light', 'rgb(175,175,175)'])
    expect(claudeCodeTheme('dark-daltonized').palette?.success).toBe('rgb(51,153,255)')
    expect(claudeCodeTheme('light-daltonized').palette?.success).toBe('rgb(0,102,153)')
    expect(claudeCodeTheme('dark-ansi')).toEqual({ base: 'dark', palette: null })
    expect(claudeCodeTheme('light-ansi')).toEqual({ base: 'light', palette: null })
    expect(claudeCodeTheme('auto')).toEqual(UNKNOWN_THEME)
    expect(claudeCodeTheme(undefined)).toEqual(UNKNOWN_THEME)
  })

  test('lays a custom theme’s own colors over its base’s, as Claude Code takes them', () => {
    const theme = claudeCodeTheme('light', {
      subtle: 'rgb(225,225,225)',
      text: '#123',
      error: 'ansi:red',
      warning: 'orange',
      success: 3,
      madeUp: 'rgb(1,2,3)',
    })
    expect(theme.palette).toMatchObject({
      subtle: 'rgb(225,225,225)',
      text: '#123',
      error: 'ansi:red',
      warning: 'rgb(150,108,30)',
      success: 'rgb(44,122,57)',
    })
    expect(theme.palette).not.toHaveProperty('madeUp')
    expect(claudeCodeTheme('dark-ansi', { subtle: 'rgb(1,2,3)' }).palette).toBeNull()
  })
})

describe('resolve', () => {
  for (const [name, theme, background] of [
    ['dark', DARK, '#100f0c'],
    ['dark-daltonized', claudeCodeTheme('dark-daltonized'), '#100f0c'],
    ['light', LIGHT, '#f6f4ee'],
    ['light-daltonized', claudeCodeTheme('light-daltonized'), '#f6f4ee'],
    ['dark-ansi', claudeCodeTheme('dark-ansi'), 'inverseText'],
    ['an unknown', UNKNOWN_THEME, 'inverseText'],
  ] as const) {
    test(`with no settings, the ${name} theme draws ink’s background and every role its default`, () => {
      expect(resolve(theme, null)).toEqual({ colors: { background, text: undefined, ...DEFAULTS }, problems: [] })
      expect(resolve(theme, {})).toEqual(resolve(theme, null))
    })
  }

  for (const [name, dark, light] of [
    ['umber', '#171510', '#fdfcf8'],
    ['stone', '#1e1c16', '#eeebe2'],
    ['graphite', 'rgb(20,20,20)', 'rgb(240,240,240)'],
    ['ink', '#100f0c', '#f6f4ee'],
  ] as const) {
    test(`panel.theme ${name} draws its background by the theme’s base`, () => {
      expect(resolve(DARK, { theme: name }).colors.background).toBe(dark)
      expect(resolve(LIGHT, { theme: name }).colors.background).toBe(light)
      expect(resolve(claudeCodeTheme('light-ansi'), { theme: name }).colors.background).toBe('inverseText')
    })
  }

  for (const [value, color] of [
    ['umber', '#171510'],
    ['smoke', 'composerSidebarBackground'],
    ['black', 'rgb(0,0,0)'],
    ['#20283a', '#20283a'],
    ['ansi256(17)', 'ansi256(17)'],
  ] as const) {
    test(`panel.background ${value} draws ${color}, over the theme’s`, () => {
      expect(resolve(DARK, { theme: 'stone', background: value })).toEqual({
        colors: { background: color, text: undefined, ...DEFAULTS },
        problems: [],
      })
    })
  }

  for (const value of ['ansi256(244)', '#abc', 'rgb(200, 200, 200)', 'suggestion', 'diffRemovedWord']) {
    test(`panel.colors takes ${value}`, () => {
      expect(resolve(DARK, { colors: { muted: value } })).toEqual({
        colors: { background: '#100f0c', text: undefined, ...DEFAULTS, muted: value },
        problems: [],
      })
    })
  }

  test('panel.colors takes default for the engine’s own text color', () => {
    const { colors, problems } = resolve(DARK, { colors: { muted: 'default', text: 'rgb(220,220,220)' } })
    expect([colors.muted, colors.text, problems]).toEqual([undefined, 'rgb(220,220,220)', []])
  })

  for (const [settings, problem] of [
    [{ theme: 'nope' }, 'panel.theme is "nope", which is not ink, umber, stone, or graphite, so the panel draws ink'],
    [{ theme: 3 }, 'panel.theme is 3, which is not ink, umber, stone, or graphite, so the panel draws ink'],
    [{ background: 'auto' }, `panel.background is "auto", which is not ${BACKGROUND_NAMES}, so the panel draws ink`],
    [{ background: 'sidebar' }, `panel.background is "sidebar", which is not ${BACKGROUND_NAMES}, so the panel draws ink`],
    [{ background: 'ansi:black' }, `panel.background is "ansi:black", which is not ${BACKGROUND_NAMES}, so the panel draws ink`],
    [{ background: false }, `panel.background is false, which is not ${BACKGROUND_NAMES}, so the panel draws ink`],
    [{ colors: 'red' }, 'panel.colors is "red", which is not a table of roles'],
    [
      { colors: { mutd: 'red' } },
      'panel.colors.mutd is not a role (the roles are text, muted, question, notice, success, warning, error, added, removed, watching, reviewing, and branch)',
    ],
    [{ colors: { background: '#000' } }, 'panel.colors.background is not a role (panel.background sets the background)'],
    [{ colors: { muted: 'gray' } }, `panel.colors.muted is "gray", which is not ${ROLE_VALUES}, so muted draws subtle`],
    [{ colors: { muted: 'ansi:white' } }, `panel.colors.muted is "ansi:white", which is not ${ROLE_VALUES}, so muted draws subtle`],
    [
      { colors: { muted: 'composerSidebarBackground' } },
      `panel.colors.muted is "composerSidebarBackground", which is not ${ROLE_VALUES}, so muted draws subtle`,
    ],
    [{ colors: { text: 'ansi256(256)' } }, `panel.colors.text is "ansi256(256)", which is not ${ROLE_VALUES}, so text draws default`],
    [{ colors: { error: '' } }, `panel.colors.error is "", which is not ${ROLE_VALUES}, so error draws error`],
    [{ colors: { error: ['red'] } }, `panel.colors.error is ["red"], which is not ${ROLE_VALUES}, so error draws error`],
    ['ink', 'panel is "ink", which is not a table'],
  ] as const) {
    test(`${JSON.stringify(settings)} leaves the defaults and says why`, () => {
      expect(resolve(DARK, settings)).toEqual({
        colors: { background: '#100f0c', text: undefined, ...DEFAULTS },
        problems: [problem],
      })
    })
  }

  const VANISH = 'would vanish on the panel’s'
  const ENGINE = 'would hide Claude Code’s own text, so the panel draws ink'
  const MIST = claudeCodeTheme('light', { subtle: 'rgb(225,225,225)' })
  const DARK_ANSI = claudeCodeTheme('dark-ansi')
  for (const [name, theme, settings, background, muted, problem] of [
    ['a role under 1.5:1', DARK, { colors: { muted: 'rgb(40,40,40)' } }, '#100f0c', 'subtle', `panel.colors.muted rgb(40,40,40) ${VANISH} #100f0c, so muted draws subtle`],
    ['a role over 1.5:1', DARK, { colors: { muted: 'rgb(70,70,70)' } }, '#100f0c', 'rgb(70,70,70)', undefined],
    ['a role on a background set to its own color', DARK, { background: 'rgb(20,20,20)', colors: { muted: 'rgb(20,20,20)' } }, 'rgb(20,20,20)', 'subtle', `panel.colors.muted rgb(20,20,20) ${VANISH} rgb(20,20,20), so muted draws subtle`],
    ['a background that hides a default', DARK, { background: 'rgb(80,80,80)' }, '#100f0c', 'subtle', 'panel.background rgb(80,80,80) would hide muted, so the panel draws ink'],
    ['the same background over a role that shows on it', DARK, { background: 'rgb(80,80,80)', colors: { muted: 'rgb(200,200,200)' } }, 'rgb(80,80,80)', 'rgb(200,200,200)', undefined],
    ['a background that gives way to the theme’s', DARK, { theme: 'umber', background: 'rgb(80,80,80)' }, '#171510', 'subtle', 'panel.background rgb(80,80,80) would hide muted, so the panel draws umber'],
    ['white on dark', DARK, { background: 'white' }, '#100f0c', 'subtle', `panel.background white ${ENGINE}`],
    ['black on light', LIGHT, { background: 'black' }, '#f6f4ee', 'subtle', `panel.background black ${ENGINE}`],
    ['white on light', LIGHT, { background: 'white' }, 'rgb(255,255,255)', 'subtle', undefined],
    ['black on dark', DARK, { background: 'black' }, 'rgb(0,0,0)', 'subtle', undefined],
    ['a light gray on light', LIGHT, { background: 'rgb(230,230,230)' }, 'rgb(230,230,230)', 'subtle', undefined],
    ['a light gray on a custom theme whose subtle matches it', MIST, { background: 'rgb(230,230,230)' }, '#f6f4ee', 'subtle', 'panel.background rgb(230,230,230) would hide muted, so the panel draws ink'],
    ['white on dark-ansi', DARK_ANSI, { background: 'white' }, 'inverseText', 'subtle', `panel.background white ${ENGINE}`],
    ['a gray on dark-ansi, whose colors the panel cannot know', DARK_ANSI, { background: 'rgb(80,80,80)' }, 'rgb(80,80,80)', 'subtle', undefined],
    ['a role named as the background on dark-ansi', DARK_ANSI, { colors: { muted: 'inverseText' } }, 'inverseText', 'subtle', `panel.colors.muted inverseText ${VANISH} inverseText, so muted draws subtle`],
    ['white on a theme of unknown base', UNKNOWN_THEME, { background: 'white' }, 'rgb(255,255,255)', 'subtle', undefined],
  ] as const) {
    test(`contrast: ${name} draws ${background} with muted ${muted}`, () => {
      const { colors, problems } = resolve(theme, settings)
      expect([colors.background, colors.muted, problems]).toEqual([
        background,
        muted,
        problem === undefined ? [] : [problem],
      ])
    })
  }

  test('a theme whose background would hide a role gives way to ink, which stands', () => {
    const dusk = claudeCodeTheme('dark', { subtle: 'rgb(25,23,18)' })
    expect(resolve(dusk, { theme: 'umber' })).toEqual({
      colors: { background: '#100f0c', text: undefined, ...DEFAULTS },
      problems: ['panel.theme umber would hide muted, so the panel draws ink'],
    })
  })

  test('a background that hides several roles names them all', () => {
    const { colors, problems } = resolve(LIGHT, { background: 'rgb(80,80,80)', colors: { muted: 'rgb(200,200,200)' } })
    expect([colors.background, colors.muted]).toEqual(['#f6f4ee', 'rgb(200,200,200)'])
    expect(problems).toEqual([
      'panel.background rgb(80,80,80) would hide notice, error, removed, watching, and branch, so the panel draws ink',
    ])
  })
})
