import type { ThemeKey } from 'claude-code'

import type { Base, ClaudeCodeTheme, Colors, TextRole } from './types'

/** What a role takes to draw in the engine's own text color, as Claude Code draws text with no color. */
const ENGINE_COLOR = 'default'

/**
 * Each role's color when no setting overrides it. Every default but `text`'s
 * is a theme key, so it follows the Claude Code theme, daltonized and custom
 * themes included.
 */
const ROLE_DEFAULTS = {
  text: ENGINE_COLOR,
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
} as const satisfies Record<TextRole, ThemeKey | typeof ENGINE_COLOR>

const TEXT_ROLES = Object.keys(ROLE_DEFAULTS) as TextRole[]

/**
 * The panel's built-in themes, each named for the color of its background,
 * which it draws by the side of light the Claude Code theme sits on.
 */
const DEFAULT_THEME = 'ink'
const PANEL_THEMES = new Map<string, Record<Base, string>>([
  [DEFAULT_THEME, { dark: '#100f0c', light: '#f6f4ee' }],
  ['umber', { dark: '#171510', light: '#fdfcf8' }],
  ['stone', { dark: '#1e1c16', light: '#eeebe2' }],
  ['graphite', { dark: 'rgb(20,20,20)', light: 'rgb(240,240,240)' }],
])

/**
 * What every theme draws on a Claude Code theme whose colors come from the
 * terminal's palette, or whose colors the panel cannot read: the theme's own
 * `inverseText`, since a fixed shade could clash with a palette it cannot see.
 */
const PALETTE_BACKGROUND = 'inverseText'

/** The theme key of the gray Claude Code draws its side panel on. */
const SIDEBAR_KEY = 'composerSidebarBackground'

/**
 * The names `panel.background` takes besides a theme's name and a literal
 * color. `black` and `white` are literal colors: `inverseText` flips between
 * the two with the theme, and `ansi256(0)` is the terminal palette's black,
 * often the terminal's own background, which would make the panel vanish.
 */
const BACKGROUND_ALIASES = new Map([
  ['smoke', SIDEBAR_KEY],
  ['black', 'rgb(0,0,0)'],
  ['white', 'rgb(255,255,255)'],
])

/**
 * Claude Code's colors for each public theme key, and for the key `smoke`
 * draws, on the themes whose colors are not the terminal's, as Claude Code
 * 2.1.300 holds them. A plugin cannot read a theme key's value at run time,
 * so the contrast check measures a theme key against this copy.
 */
type Palette = Record<ThemeKey | typeof SIDEBAR_KEY, string>

const DARK: Palette = {
  text: 'rgb(255,255,255)',
  inverseText: 'rgb(0,0,0)',
  inactive: 'rgb(153,153,153)',
  subtle: 'rgb(80,80,80)',
  suggestion: 'rgb(177,185,249)',
  remember: 'rgb(177,185,249)',
  success: 'rgb(78,186,101)',
  error: 'rgb(255,107,128)',
  warning: 'rgb(255,193,7)',
  merged: 'rgb(175,135,255)',
  claude: 'rgb(215,119,87)',
  permission: 'rgb(177,185,249)',
  planMode: 'rgb(72,150,140)',
  autoAccept: 'rgb(175,135,255)',
  promptBorder: 'rgb(136,136,136)',
  bashBorder: 'rgb(253,93,177)',
  ide: 'rgb(71,130,200)',
  diffAdded: 'rgb(34,92,43)',
  diffRemoved: 'rgb(122,41,54)',
  diffAddedDimmed: 'rgb(71,88,74)',
  diffRemovedDimmed: 'rgb(105,72,77)',
  diffAddedWord: 'rgb(56,166,96)',
  diffRemovedWord: 'rgb(179,89,107)',
  composerSidebarBackground: 'rgb(38,38,38)',
}

const DARK_DALTONIZED: Palette = {
  text: 'rgb(255,255,255)',
  inverseText: 'rgb(0,0,0)',
  inactive: 'rgb(153,153,153)',
  subtle: 'rgb(80,80,80)',
  suggestion: 'rgb(153,204,255)',
  remember: 'rgb(153,204,255)',
  success: 'rgb(51,153,255)',
  error: 'rgb(255,102,102)',
  warning: 'rgb(255,204,0)',
  merged: 'rgb(175,135,255)',
  claude: 'rgb(255,153,51)',
  permission: 'rgb(153,204,255)',
  planMode: 'rgb(102,153,153)',
  autoAccept: 'rgb(175,135,255)',
  promptBorder: 'rgb(136,136,136)',
  bashBorder: 'rgb(51,153,255)',
  ide: 'rgb(71,130,200)',
  diffAdded: 'rgb(0,68,102)',
  diffRemoved: 'rgb(102,0,0)',
  diffAddedDimmed: 'rgb(62,81,91)',
  diffRemovedDimmed: 'rgb(62,44,44)',
  diffAddedWord: 'rgb(0,119,179)',
  diffRemovedWord: 'rgb(179,0,0)',
  composerSidebarBackground: 'rgb(38,38,38)',
}

const LIGHT: Palette = {
  text: 'rgb(0,0,0)',
  inverseText: 'rgb(255,255,255)',
  inactive: 'rgb(102,102,102)',
  subtle: 'rgb(175,175,175)',
  suggestion: 'rgb(87,105,247)',
  remember: 'rgb(0,0,255)',
  success: 'rgb(44,122,57)',
  error: 'rgb(171,43,63)',
  warning: 'rgb(150,108,30)',
  merged: 'rgb(135,0,255)',
  claude: 'rgb(215,119,87)',
  permission: 'rgb(87,105,247)',
  planMode: 'rgb(0,102,102)',
  autoAccept: 'rgb(135,0,255)',
  promptBorder: 'rgb(138,138,138)',
  bashBorder: 'rgb(255,0,135)',
  ide: 'rgb(71,130,200)',
  diffAdded: 'rgb(105,219,124)',
  diffRemoved: 'rgb(255,168,180)',
  diffAddedDimmed: 'rgb(199,225,203)',
  diffRemovedDimmed: 'rgb(253,210,216)',
  diffAddedWord: 'rgb(47,157,68)',
  diffRemovedWord: 'rgb(209,69,75)',
  composerSidebarBackground: 'rgb(245,245,245)',
}

const LIGHT_DALTONIZED: Palette = {
  text: 'rgb(0,0,0)',
  inverseText: 'rgb(255,255,255)',
  inactive: 'rgb(102,102,102)',
  subtle: 'rgb(175,175,175)',
  suggestion: 'rgb(51,102,255)',
  remember: 'rgb(51,102,255)',
  success: 'rgb(0,102,153)',
  error: 'rgb(204,0,0)',
  warning: 'rgb(255,153,0)',
  merged: 'rgb(135,0,255)',
  claude: 'rgb(255,153,51)',
  permission: 'rgb(51,102,255)',
  planMode: 'rgb(51,102,102)',
  autoAccept: 'rgb(135,0,255)',
  promptBorder: 'rgb(138,138,138)',
  bashBorder: 'rgb(0,102,204)',
  ide: 'rgb(71,130,200)',
  diffAdded: 'rgb(153,204,255)',
  diffRemoved: 'rgb(255,204,204)',
  diffAddedDimmed: 'rgb(209,231,253)',
  diffRemovedDimmed: 'rgb(255,233,233)',
  diffAddedWord: 'rgb(51,102,204)',
  diffRemovedWord: 'rgb(153,51,51)',
  composerSidebarBackground: 'rgb(235,235,235)',
}

/**
 * The theme keys a role takes: the public ones, which Claude Code's
 * `ThemeKey` type lists and `Palette` holds every one of. Claude Code draws a
 * key it does not know as no color, so a typo of any other key would draw
 * nothing.
 */
const PUBLIC_THEME_KEYS: ReadonlySet<string> = new Set(Object.keys(DARK).filter(key => key !== SIDEBAR_KEY))

/** Claude Code's built-in themes: the side of light each sits on, and its colors unless they are the terminal's. */
const CLAUDE_CODE_THEMES = new Map<string, { base: Base; palette?: Palette }>([
  ['dark', { base: 'dark', palette: DARK }],
  ['dark-daltonized', { base: 'dark', palette: DARK_DALTONIZED }],
  ['light', { base: 'light', palette: LIGHT }],
  ['light-daltonized', { base: 'light', palette: LIGHT_DALTONIZED }],
  ['dark-ansi', { base: 'dark' }],
  ['light-ansi', { base: 'light' }],
])

export const UNKNOWN_THEME: ClaudeCodeTheme = { base: null, palette: null }

/**
 * Claude Code draws the text of a Button, its key, and a link itself, in the
 * theme's text color, which no role can change: near white on a dark base and
 * near black on a light one.
 */
const ENGINE_TEXT: Record<Base, string> = { dark: 'rgb(255,255,255)', light: 'rgb(0,0,0)' }

/** Under this contrast ratio, a color is lost against the color it draws on. */
const MIN_CONTRAST = 1.5

/**
 * The literal colors the panel takes, written as Claude Code reads them:
 * `#rgb` or `#rrggbb`, and `rgb(r,g,b)` or `ansi256(n)`, with a space allowed
 * after `(`, each comma, and before `)`. Each number is at most 255.
 */
const HEX_COLOR = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i
const RGB_COLOR = /^rgb\( ?(\d{1,3}), ?(\d{1,3}), ?(\d{1,3}) ?\)$/
const ANSI256_COLOR = /^ansi256\( ?(\d{1,3}) ?\)$/

type Rgb = readonly number[]

const isTable = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const numbersOf = (match: RegExpExecArray | null): number[] | undefined => {
  const numbers = match?.slice(1).map(Number)
  return numbers?.every(number => number <= 255) === true ? numbers : undefined
}

const isLiteral = (value: string) =>
  HEX_COLOR.test(value) || [RGB_COLOR, ANSI256_COLOR].some(pattern => numbersOf(pattern.exec(value)) !== undefined)

/**
 * What a Claude Code theme gives the panel: its base, and for a theme whose
 * colors are not the terminal's, each key's color with a custom theme's own
 * `overrides` over its base's, as Claude Code applies them.
 */
export function claudeCodeTheme(name: unknown, overrides?: unknown): ClaudeCodeTheme {
  const theme = typeof name === 'string' ? CLAUDE_CODE_THEMES.get(name) : undefined
  if (theme === undefined) return UNKNOWN_THEME
  const { base, palette } = theme
  if (palette === undefined) return { base, palette: null }
  const own = Object.entries(isTable(overrides) ? overrides : {}).filter(
    (entry): entry is [string, string] =>
      Object.hasOwn(palette, entry[0]) &&
      typeof entry[1] === 'string' &&
      (isLiteral(entry[1]) || entry[1].startsWith('ansi:')),
  )
  return { base, palette: { ...palette, ...Object.fromEntries(own) } }
}

/** The xterm table's color for `ansi256(n)` past the palette's first 16, which a terminal's own palette sets. */
function xterm(n: number): Rgb | undefined {
  if (n < 16) return undefined
  if (n >= 232) return Array<number>(3).fill(8 + 10 * (n - 232))
  const levels = [0, 95, 135, 175, 215, 255]
  return [Math.floor((n - 16) / 36), Math.floor((n - 16) / 6) % 6, (n - 16) % 6].map(level => levels[level] ?? 0)
}

/** A color's red, green and blue, or undefined when the panel cannot know them. */
function rgbOf(color: string, palette: ClaudeCodeTheme['palette']): Rgb | undefined {
  const keyed = palette !== null && Object.hasOwn(palette, color) ? palette[color] : undefined
  if (keyed !== undefined) return rgbOf(keyed, null)
  const hex = HEX_COLOR.exec(color)?.[1]
  if (hex !== undefined) {
    const digits = hex.length === 3 ? [...hex].map(digit => digit + digit) : (hex.match(/../g) ?? [])
    return digits.map(pair => parseInt(pair, 16))
  }
  const ansi256 = numbersOf(ANSI256_COLOR.exec(color))?.[0]
  return ansi256 === undefined ? numbersOf(RGB_COLOR.exec(color)) : xterm(ansi256)
}

const luminance = (rgb: Rgb) => {
  const [r = 0, g = 0, b = 0] = rgb.map(channel => {
    const value = channel / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/**
 * The contrast ratio of two colors as the Web Content Accessibility
 * Guidelines (WCAG) 2 define it, from 1 for one color to 21 for black on
 * white, or undefined when the panel cannot know either color.
 */
export function contrast(one: string, other: string, palette: ClaudeCodeTheme['palette'] = null): number | undefined {
  const [first, second] = [rgbOf(one, palette), rgbOf(other, palette)]
  if (first === undefined || second === undefined) return undefined
  const [lighter = 0, darker = 0] = [luminance(first), luminance(second)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

/** Whether a color would vanish on a background: the same name, or too little contrast where the panel knows both. */
function vanishes(color: string | undefined, background: string, palette: ClaudeCodeTheme['palette']): boolean {
  if (color === undefined) return false
  const ratio = contrast(color, background, palette)
  return color === background || (ratio !== undefined && ratio < MIN_CONTRAST)
}

/** `a`, `a and b`, or `a, b, and c`. */
function listed(items: readonly string[], conjunction: string): string {
  const head = items.slice(0, -1)
  const last = items.at(-1) ?? ''
  if (head.length === 0) return last
  return head.length === 1 ? `${head.join('')} ${conjunction} ${last}` : `${head.join(', ')}, ${conjunction} ${last}`
}

const colorOf = (value: string) => (value === ENGINE_COLOR ? undefined : value)

/** A theme's background on a Claude Code theme: by its base, or `inverseText` where the colors are unknown. */
function themeBackground(name: string, theme: ClaudeCodeTheme): string {
  const pair = PANEL_THEMES.get(name)
  return pair === undefined || theme.base === null || theme.palette === null ? PALETTE_BACKGROUND : pair[theme.base]
}

/** What the panel draws for a `panel.background` value, or undefined when it takes none. */
function backgroundFor(value: string, theme: ClaudeCodeTheme): string | undefined {
  if (PANEL_THEMES.has(value)) return themeBackground(value, theme)
  return BACKGROUND_ALIASES.get(value) ?? (isLiteral(value) ? value : undefined)
}

const LITERAL_FORMS = ['rgb(r,g,b)', '#rrggbb', 'ansi256(n)']
const BACKGROUND_NAMES = listed([...PANEL_THEMES.keys(), ...BACKGROUND_ALIASES.keys(), ...LITERAL_FORMS], 'or')
const ROLE_VALUES = listed([...LITERAL_FORMS, 'a Claude Code theme color such as subtle', ENGINE_COLOR], 'or')

const written = (value: unknown) => JSON.stringify(value) ?? String(value)

/** The theme `panel.theme` names, or the default when it names none the panel has. */
function themeNameOf(chosen: unknown, problems: string[]): string {
  if (typeof chosen === 'string' && PANEL_THEMES.has(chosen)) return chosen
  if (chosen !== undefined) {
    const themes = listed([...PANEL_THEMES.keys()], 'or')
    problems.push(`panel.theme is ${written(chosen)}, which is not ${themes}, so the panel draws ${DEFAULT_THEME}`)
  }
  return DEFAULT_THEME
}

const isRoleValue = (value: string) => isLiteral(value) || PUBLIC_THEME_KEYS.has(value) || value === ENGINE_COLOR

/** The `panel.colors` overrides the panel takes, by role. */
function overridesOf(colors: unknown, problems: string[]): Map<TextRole, string> {
  const overrides = new Map<TextRole, string>()
  if (colors !== undefined && !isTable(colors)) {
    problems.push(`panel.colors is ${written(colors)}, which is not a table of roles`)
  }
  for (const [name, value] of Object.entries(isTable(colors) ? colors : {})) {
    const role = TEXT_ROLES.find(one => one === name)
    if (role === undefined) {
      problems.push(
        name === 'background'
          ? 'panel.colors.background is not a role (panel.background sets the background)'
          : `panel.colors.${name} is not a role (the roles are ${listed(TEXT_ROLES, 'and')})`,
      )
    } else if (typeof value === 'string' && isRoleValue(value)) {
      overrides.set(role, value)
    } else {
      const fallback = ROLE_DEFAULTS[role]
      problems.push(
        `panel.colors.${role} is ${written(value)}, which is not ${ROLE_VALUES}, so ${role} draws ${fallback}`,
      )
    }
  }
  return overrides
}

/** A background the panel may draw: the setting that names it, its name, and its color. */
type Layer = { setting: string; name: string; color: string }

/**
 * Resolves the `panel` settings, as `project config get panel` prints them,
 * into one color per role for a Claude Code theme. Each layer overrides the
 * one before it: each role's default and the default theme's background,
 * the theme `panel.theme` picks, then `panel.background` and
 * `panel.colors.<role>`. A value the panel does not take gives way to the
 * layer below. So does a color that would vanish on the background: a role's
 * override gives way to the role's default, and the background gives way
 * only when a default, or Claude Code's own text, would still vanish on it.
 * Each refusal adds a problem that says why.
 */
export function resolve(theme: ClaudeCodeTheme, settings: unknown): { colors: Colors; problems: string[] } {
  const problems: string[] = []
  if (settings !== null && !isTable(settings)) problems.push(`panel is ${written(settings)}, which is not a table`)
  const { theme: chosen, background, colors } = isTable(settings) ? settings : {}
  const themeName = themeNameOf(chosen, problems)
  const themeLayer = (name: string): Layer => ({
    setting: `panel.theme ${name}`,
    name,
    color: themeBackground(name, theme),
  })

  const layers: Layer[] = []
  if (background !== undefined) {
    const color = typeof background === 'string' ? backgroundFor(background, theme) : undefined
    if (typeof background === 'string' && color !== undefined) {
      layers.push({ setting: `panel.background ${background}`, name: background, color })
    } else {
      problems.push(
        `panel.background is ${written(background)}, which is not ${BACKGROUND_NAMES}, so the panel draws ${themeName}`,
      )
    }
  }
  if (themeName !== DEFAULT_THEME) layers.push(themeLayer(themeName))
  const overrides = overridesOf(colors, problems)

  const { base, palette } = theme
  const drawOn = (layer: Layer) => {
    const drawn: Colors = { background: layer.color }
    const vanished: string[] = []
    for (const role of TEXT_ROLES) {
      const fallback = ROLE_DEFAULTS[role]
      const wanted = overrides.get(role)
      const isKept = wanted !== undefined && !vanishes(colorOf(wanted), layer.color, palette)
      if (wanted !== undefined && !isKept) {
        vanished.push(
          `panel.colors.${role} ${wanted} would vanish on the panel’s ${layer.color}, so ${role} draws ${fallback}`,
        )
      }
      drawn[role] = colorOf(isKept ? wanted : fallback)
    }
    const hidden: string[] = TEXT_ROLES.filter(role => vanishes(drawn[role], layer.color, palette))
    if (base !== null && vanishes(ENGINE_TEXT[base], layer.color, palette)) hidden.push('Claude Code’s own text')
    return { drawn, vanished, hidden }
  }

  const last = themeLayer(DEFAULT_THEME)
  for (const [index, layer] of layers.entries()) {
    const { drawn, vanished, hidden } = drawOn(layer)
    if (hidden.length === 0) return { colors: drawn, problems: [...problems, ...vanished] }
    const next = layers[index + 1] ?? last
    problems.push(`${layer.setting} would hide ${listed(hidden, 'and')}, so the panel draws ${next.name}`)
  }
  const { drawn, vanished } = drawOn(last)
  return { colors: drawn, problems: [...problems, ...vanished] }
}
