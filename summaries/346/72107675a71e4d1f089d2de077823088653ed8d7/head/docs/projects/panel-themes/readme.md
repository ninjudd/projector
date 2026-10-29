---
status: completed
priority: now
---

# Theme Projector's panel

Every color Projector's panel draws comes from a named role, each role's
default follows your Claude Code theme, and you can override any role, or
pick a built-in theme, without one role vanishing on another on any theme
whose colors the panel knows.

## 1. Problem

Projector's panel is the area Claude Code draws above the prompt. It lists
the pull requests of the session's loops and the inbox's questions and
notices. In a terminal it draws on a background of its own, and it colors
each element by what the element means: muted text in the theme's `subtle`,
an open question in `permission`, an open notice in `inactive`, and a pull
request's state in `success`, `warning`, or `error`.

Each of those colors was written into `hooks/register.tsx` where an element
draws it, as a handful of constants and about twenty inline names. Only the
background was a setting, `panel.background`, and that caused three
problems:

- **A setting can hide what draws on it.** The background's check knew
  nothing of the colors drawn on the background. The list of values that
  #341 merged leaves out a `subtle` alias for `panel.background`
  (`docs/projects/inbox/readme.md` § 23), because the panel's header, key
  hint, ages, and counts are `subtle` and would vanish on it. The same
  conflict remained for values the check accepted: `white` on a dark Claude
  Code theme puts the key labels and links, drawn in the theme's light text
  color, on white.
- **No other color can change.** Someone whose terminal palette made the
  `watching` blue, `ansi256(4)`, hard to read on the panel, or who wants
  questions in another color, had no setting to reach for.
- **Colors have no names.** A new element picked a color where it drew.
  `success` means both a pull request that is ready and a count of inserted
  lines, so nothing said which colors may change together.

It mattered little while the defaults looked right on every built-in Claude
Code theme. It grows with every color setting the panel adds, and the
owner's choice of a new default background, `ink`, is one.

## 2. Solution

The panel draws every color through one of thirteen named roles. A small
module, `hooks/theme.ts`, holds the roles, their defaults, the built-in
themes, the values each role takes, and the contrast check, and resolves
them into one color per role. With no settings, every role draws its
default, a Claude Code theme key, on the background of the default theme,
`ink`, so the panel looks right with no configuration. A person changes the
look with `panel.theme`, which picks a built-in theme, `panel.background`,
which sets the background alone, and `panel.colors.<role>`, which overrides
one role. Every name these settings take describes its color. The panel
reads all of it in one `project config get panel` call. It refuses any value
it cannot draw, or that would vanish on what it draws against when it knows
both colors, falls back to the layer below, and says so once in a toast. The
role list and its checks live only in the panel, and the CLI learns only
that these keys hold strings.

### 2.1 The roles

Every role but the background defaults either to a Claude Code theme key or
to no color. A theme key resolves per theme: dark, light, their daltonized
variants, the ANSI themes, and a custom theme's overrides. No color leaves
the text to the engine. So one default serves every theme, and only the
background's default depends on the theme, by its base.

`watching` and `branch` drew in the terminal palette's blue and cyan,
`ansi256(4)` and `ansi256(6)`, which follow the terminal's palette but not
the Claude Code theme. They draw `remember` and `planMode`, the public theme
keys nearest in hue to that blue and cyan on both `dark` and `light`, so
they adapt to light and dark themes like every other role (§ 9).

| Role | What draws in it | Default |
| --- | --- | --- |
| `background` | The panel's `Box` | By base, as § 2.2 says |
| `text` | A pull request's title, each ` · `, ` in 5 files`, a chosen or typed answer | No color: the engine's text color |
| `muted` | The header and its counts, the key hint, ages, a pull request's author, an item's pull request label, `sending`, resolved and snoozed items, a resolved question's other options, the tagline, `Looking for pull requests…` | `subtle` |
| `question` | An open question's line, and the line that stands for collapsed questions, such as `- 2 questions` | `permission` |
| `notice` | An open notice's line, and the line that stands for collapsed notices | `inactive` |
| `success` | `ready`, `approved`, `checks passing`, `(recommended)`, `✓` | `success` |
| `warning` | `draft`, an open thread count such as `5*`, `checks pending` | `warning` |
| `error` | `conflict`, `changes requested`, `checks failing`, a GitHub error | `error` |
| `added` | A drawer's insertions, such as `+84` | `success` |
| `removed` | A drawer's deletions, such as `−12` | `error` |
| `watching` | `watching` in a pull request's drawer | `remember` |
| `reviewing` | `reviewing` in a pull request's drawer | `claude` |
| `branch` | A pull request's branch in its drawer | `planMode` |

Some text stays outside every role, because Claude Code draws it and a
plugin cannot set its color: a plain `Button`'s label and its key, such as
`7: discuss PR` and a pull request's number, and a link, such as the
Markdown `open in github` or the `Link` of an unresolved thread's line in a
drawer. No role uses `dimColor`, which some terminals draw at full
strength.

### 2.2 The background by Claude Code theme

Every built-in theme is a background for each base, dark and light. The
default theme, `ink`, draws these:

| Claude Code theme | Base | Background default |
| --- | --- | --- |
| `dark`, `dark-daltonized` | dark | `#100f0c` |
| `light`, `light-daltonized` | light | `#f6f4ee` |
| `dark-ansi`, `light-ansi` | dark or light | `inverseText` |
| `custom:<slug>` | its file's `base`, `dark` when it names none of Claude Code's themes, as Claude Code reads it | its base's |
| a custom theme whose file the panel cannot read, or any other | unknown | `inverseText` |

Every built-in theme falls back to `inverseText` where `ink` does, because
a fixed shade could clash with a terminal palette the panel cannot see. The
base also tells the contrast check in § 2.5 which side of light the theme's
own text sits on.

### 2.3 The settings

| Key | Type | Default | Takes |
| --- | --- | --- | --- |
| `panel.theme` | string | `ink` | A built-in theme's name |
| `panel.background` | string | unset, which draws the theme's background | A built-in theme's name for that theme's background, `smoke`, `black`, `white`, or a literal color, less a background that § 2.5 refuses |
| `panel.colors.<role>` | string | unset | A literal color, one of Claude Code's 23 public theme keys, or `default` for the engine's text color |

The built-in themes come from the palette of the project's site,
projector.bot, in `web/app/globals.css`, plus the gray the panel drew
before `ink`:

| Theme | Dark | Light | Source |
| --- | --- | --- | --- |
| `ink` | `#100f0c` | `#f6f4ee` | The site's `--bg`, its page |
| `umber` | `#171510` | `#fdfcf8` | The site's `--surface`, its code blocks |
| `stone` | `#1e1c16` | `#eeebe2` | The site's `--surface-2`, its inline code |
| `graphite` | `rgb(20,20,20)` | `rgb(240,240,240)` | The panel's earlier gray |

`panel.background` names three more colors: `smoke` draws the theme key
`composerSidebarBackground`, the gray of Claude Code's side panel, and
`black` and `white` draw `rgb(0,0,0)` and `rgb(255,255,255)`. Each name is
kept in one constant in `hooks/theme.ts`, `PANEL_THEMES` or
`BACKGROUND_ALIASES`, so renaming one is a one-line change.

A literal color is `rgb(r,g,b)` or `ansi256(n)` with each number from 0 to
255, or `#rgb` or `#rrggbb`, as #341 parses them. The public keys are the
ones Claude Code's `ThemeKey` type lists, from `text` to `diffRemovedWord`.
A role refuses every name Claude Code would not draw as meant:

- **A plain name**, such as `gray`, which draws in the default color. A live
  preview of the panel in a Claude Code session showed this, though Claude
  Code's `Color` type documents a name such as `"red"` as a raw color.
- **An `ansi:` name**, which makes Claude Code refuse the panel's whole
  tree.
- **A theme key outside the public 23**, such as
  `composerSidebarBackground`, whose spelling the panel cannot check.

```toml
[panel]
theme = "umber"
background = "rgb(30,30,40)"

[panel.colors]
muted = "ansi256(244)"
question = "suggestion"
```

Each layer overrides the one before it:

1. Each role's default for the session's base, § 2.1, and `ink`'s
   background, § 2.2.
2. The built-in theme `panel.theme` names. A built-in theme sets only the
   background, so every other role keeps its default and still follows the
   Claude Code theme.
3. `panel.background` for the background, and `panel.colors.<role>` for each
   other role.

`panel.colors` takes every role but `background`, which only
`panel.background` sets. The panel treats `panel.colors.background` as a
role it does not know, and its toast names `panel.background` instead.

### 2.4 The flow

1. When the session starts, the panel reads the Claude Code theme from
   `$.config.list()` and works out its base, reading a custom theme's file
   as it does today. It stores the base, and the theme's colors with a
   custom theme's own `overrides` over its base's, in `panelTheme`.
2. One timer tick later, and on each reload, the panel runs
   `project config get panel --json` once. The CLI merges every
   `.projector.toml` that applies, table by table. A missing `panel` table,
   which the CLI reports as `null` with exit status 1, means no settings.
3. The panel resolves one color per role, layer by layer as § 2.3 says. A
   value that fails its check gives way to the layer below and adds a
   problem: one outside the role's grammar, a role or a theme name the panel
   does not know, a non-string, or a color that vanishes by § 2.5.
4. It stores the colors in `panelColors`. It toasts every problem it has not
   toasted in this session, all new problems from one read in one toast,
   such as this one:

   ```text
   projector: panel.colors.mutd is not a role (the roles are text, muted, question, …); panel.colors.muted rgb(25,25,25) would vanish on the panel’s #100f0c, so muted draws subtle
   ```

5. The render reads `panelColors` and passes `colors.<role>` to the panel's
   `Box` and to every `Text`. It waits on no read.
6. A theme picked in `/theme` recolors at once: the `config.set` hook stores
   the new theme's base and colors and resolves again. A
   `project config set` or `unset` of a `panel.` key that the session runs
   through its Bash tool re-reads the settings one timer tick after the
   command finishes, through the `tool.call` hook that already reads the
   session's commands. `/projector` re-reads them when it shows the panel,
   because showing it reloads it. Any other change shows on the next
   reload, within 30 seconds. Each resolve waits for the one before it, so
   a resolve that read a theme or settings since replaced never stores its
   colors over a later one's.

### 2.5 Contrast

The panel compares two colors by their contrast ratio, as the Web Content
Accessibility Guidelines (WCAG) 2 define it, from 1:1 for the same color to
21:1 for black on white. A color under 1.5:1 against the color it draws on
would vanish. The panel computes the ratio only when it knows both colors,
and it knows two kinds:

- **A literal color.** An `rgb()` or hex value, `black`, `white`, or
  `ansi256(n)` for `n` from 16 to 255, which terminals take from the
  standard xterm table.
- **A theme key on a theme whose values the panel holds.** `hooks/theme.ts`
  keeps the value of each public theme key, and of
  `composerSidebarBackground`, which `smoke` draws, on `dark`, `light`, and
  their daltonized themes. They are copied from Claude Code 2.1.300, as the
  panel's built-in backgrounds are fixed values too. On `dark`, for
  example, Claude Code draws `subtle` as `rgb(80,80,80)`. A custom theme
  takes its base's values, with the colors its own file sets over them.

The panel cannot know a color on an ANSI theme, whose keys draw from the
terminal's palette, or on a custom theme whose file it cannot read, or in
the palette's slots 0 to 15. It compares those only for an exact match, so
on such a theme a literal background can still hide a role that keeps its
default, as § 7 records.

The panel settles the background first, then every other role:

1. Each role takes its override when the override would not vanish on the
   background `panel.background` sets, and its default otherwise.
2. When a role's color from step 1 would still vanish on that background, or
   Claude Code's own text would, the background falls back to the layer
   below: the background of the theme `panel.theme` names, then `ink`'s,
   which always stands. Step 1 then runs again against the background that
   stands.

So when an override of the background meets an override of a role, the role
gives way, and one element changes rather than the whole panel. The
background gives way only to a default the person did not change, or to
Claude Code's own text. Each refusal adds a problem, which the panel toasts
once, as § 2.4 says.

- **A role that would vanish is refused.** For example, a `muted` override
  of `rgb(40,40,40)` on the default `#100f0c` is 1.30:1, so `muted` draws
  `subtle`. `rgb(70,70,70)` is 2.03:1 and draws.
- **A background that would hide a role's default is refused.** On `dark`,
  `panel.background = "rgb(80,80,80)"` matches `subtle`, so with no `muted`
  override the header, the key hint, and the ages would vanish. The panel
  draws `#100f0c` instead. With `panel.colors.muted` set to
  `rgb(200,200,200)` as well, `muted` no longer vanishes, so both draw as
  set unless another role's default would vanish on `rgb(80,80,80)`.
- **A background that would hide Claude Code's own text is refused.** Claude
  Code draws some of the panel's text itself, and no role can recolor it: a
  `Button`'s label, the accent of its key, and a Markdown link. A `Button`'s
  label draws in the theme's text color, near white on a dark base and near
  black on a light one. So when it settles the background, the panel
  compares a background it knows with white on a dark base and with black on
  a light one. That refuses `white` on a dark theme and `black` on a light
  one. On a base the panel cannot tell, it makes no such check.
- **This partly reverses #341.** #341 accepts `black` and `white` on every
  theme. The panel draws `white` only on a light theme and `black` only on a
  dark one, and a person who set the other gets the theme's background and
  one toast.
- **Low contrast that stays readable draws as set.** A color at 1.5:1 or
  more draws with no warning. The panel's muted text sits at low contrast by
  design, so a warning at a higher ratio would flag looks like the default.

### 2.6 The files

| File | Change |
| --- | --- |
| `hooks/theme.ts` (new) | `ROLE_DEFAULTS`, `PANEL_THEMES`, `BACKGROUND_ALIASES`, the literal color patterns from #341, Claude Code's colors for each public key and `composerSidebarBackground` on `dark`, `light`, and their daltonized themes, typed so the compiler requires every key of Claude Code's `ThemeKey` and no other, `PUBLIC_THEME_KEYS`, the contrast ratio, `claudeCodeTheme(name, overrides)`, which gives a Claude Code theme's base and colors, `customTheme(base, overrides)`, which reads a custom theme's file as Claude Code does, and `resolve(theme, settings)`, which returns `{ colors, problems }` |
| `hooks/register.tsx` | Draws every color through `colors.<role>`. Removes `QUESTION_COLOR`, `NOTICE_COLOR`, `BRANCH_COLOR`, `LOOP_COLORS`, `CHECK_COLORS`, the colors in `Note`, `THEME_BACKGROUNDS`, `OTHER_BACKGROUND`, and the background check, which move into `hooks/theme.ts`. `readBackground` becomes `readSettings`, which reads the `panel` table, `paint` stores the base and the theme's colors, and `recolor` resolves both and toasts. The Bash hook re-reads after a `project config set` or `unset` of a `panel.` key |
| `hooks/types.d.ts` | The state keys `panelTheme`, `panelSettings`, and `panelColors` replace `panelThemeBackground` and `panelBackground`. The types they hold, `ClaudeCodeTheme`, `TextRole`, and `Colors`, live here too, because the plugin's types contract must import nothing |
| `hooks/theme.test.ts` (new) | `resolve`, `claudeCodeTheme`, `customTheme`, and the contrast ratio, table by table |
| `hooks/panel.test.tsx` | The defaults on each Claude Code theme, the built-in themes, overrides, refusals, toasts, and the re-read after a set |
| `src/projector/config.py` | `SETTINGS` gains `panel.theme` as a string, and `TABLES` holds `panel.colors.<role>`, whose keys `set` and `unset` accept under any lowercase name, each value a string |
| `tests/test_config.py` | `set` and `unset` of `panel.theme` and `panel.colors.<name>`, read back as the panel reads them, and the documented keys test reads the `panel.colors.<role>` row |
| `docs/plugins.md` | "Choose the panel's color" describes the themes, the background, the roles, the contrast rule, and the toasts. Keep its anchor, `#choose-the-panels-color`, which other sections link to |
| `docs/cli.md` | The settings table gains `panel.theme` and `panel.colors.<role>` |

## 3. Why this design

- **Roles, not elements.** A role names what a color means, so a new element
  picks a role instead of a color, and an override changes every element
  that means the same thing. Thirteen roles cover every color the panel
  draws. `added` and `removed` are apart from `success` and `error`, because
  a diff stat and a verdict can want different colors.
- **Theme keys as defaults.** A theme key already follows every Claude Code
  theme, the daltonized ones and a custom theme's overrides included. Only
  the background needs a table by base, because no theme key means "the
  panel's own surface".
- **`watching` and `branch` follow the theme too.** A palette slot keeps the
  terminal's own blue or cyan on every theme, and those can be faint on the
  light background. The owner chose theme keys for both, so they adapt to
  light and dark as the other roles do.
- **Names that describe the color.** `auto` said nothing about what the
  panel draws, and `sidebar` named a place. A person choosing between
  `ink`, `umber`, `stone`, `graphite`, and `smoke` can tell them apart
  before trying any.
- **The project's own palette.** The site's background colors already come
  in dark and light pairs chosen to sit together, so each pair is a
  background for both bases with no new design.
- **Built-in themes set only the background.** Each shipped theme differs
  from the others only in its background, so a theme is a pair of colors.
  A theme that sets other roles can grow `PANEL_THEMES` to hold them, and
  no setting changes.
- **`panel.background` takes a theme's name.** It is the setting people
  already use, so the name that replaces `auto` there is the default
  theme's, and one list of names serves both settings.
- **Settings, not theme files.** `.projector.toml` already merges per file,
  and `project config set` already writes it, so a person's own theme is a
  `[panel.colors]` table in `~/.projector.toml`. A theme file format would
  need a reader, a location, and a command of its own. Named user themes
  wait until someone switches between several (§ 7).
- **`panel.background` stays.** It already ships, and a background takes
  different values from a text role: shades that look right behind text. A
  second key for the same color, such as `panel.colors.background`, would
  make the reader ask which one wins.
- **One owner for each list.** The plugin and the CLI install and upgrade
  apart, as #341 found, so the panel, which draws the colors, owns the
  roles, the theme names, and every check. The CLI knows only that the keys
  hold strings, so it never needs a release to add a role, and
  `project config get` reads the `panel` table under any CLI version.
- **Refuse what vanishes, and nothing else.** Adjusting a color the person
  chose would change something they did not ask for, and on an ANSI theme
  the panel cannot compute a theme key's value to adjust it anyway. A
  warning tier would flag the panel's own deliberate low-contrast muted
  text. Refusing only what vanishes keeps every role the panel can measure
  visible, and leaves taste to the person.
- **A copy of Claude Code's theme values.** A plugin cannot read a theme
  key's value at run time, so the check needs a copy to measure a default
  against a literal background. A value Claude Code changes later leaves
  the copy stale until someone updates it. The check refuses only colors
  within 1.5:1 of each other, so a small change rarely turns its verdict.
  The copy is Claude Code's data, not a list Projector keeps in two places.
- **The engine's text sets a limit on the background.** No role can move
  Claude Code's own text off a background that hides it, so a warning alone
  would leave labels and links unreadable until the person noticed. The
  owner chose to refuse such a background and fall back to the theme's, the
  same as for any other value the panel cannot draw.
- **Public theme keys only.** Claude Code draws a theme key it does not know
  as no color at all, and a mod cannot list the theme's keys at run time.
  Only the public 23 can be checked, through the `ThemeKey` type, so a typo
  is refused rather than drawn as nothing.
- **Preview on real rows.** A set re-reads at once (§ 2.4, step 6), so a
  person sees the change on their own pull requests and items. The plugin
  draws no made-up rows. Designing a new built-in theme uses a temporary
  session mod, as the panel's layout work has.

## 4. Acceptance criteria

| Do | Expect | Test |
| --- | --- | --- |
| Draw the panel with no settings on `dark`, `light`, both daltonized themes, both ANSI themes, custom themes on each base, one with no `base`, one whose `base` is misspelled, and one with no file | The background is `ink`'s for the theme's base, `inverseText` where the panel cannot know the colors, and every element draws its role's default | `with no settings the panel draws <color>, and each role its default, on the <theme> theme` in `hooks/panel.test.tsx`, one test per theme, and `with no settings, the <theme> theme draws ink’s background and every role its default` in `hooks/theme.test.ts` |
| Open a pull request's drawer with no settings on `dark`, then on `light` | `watching` and the branch draw `remember` and `planMode`, not `ansi256(4)` or `ansi256(6)` | `watching and the branch draw theme keys, so they follow the theme` |
| Set `panel.theme` to `umber`, `stone`, `graphite`, and `ink` | Each draws its background for the theme's base, and every other role keeps its default | `panel.theme picks a built-in theme, and panel.background overrides its background`, and `panel.theme <name> draws its background by the theme’s base` in `hooks/theme.test.ts` |
| Set `panel.background` to each theme's name, `smoke`, `black`, `white`, and literal colors | Each draws its color, over the theme's background | `panel.background colors the panel, and a theme’s name draws that theme’s background` |
| Set `panel.background` to `auto`, `sidebar`, or another value it does not take | The theme's background draws, and one toast per value lists the names the panel takes | `a panel.background the panel does not take leaves the theme’s background, and the panel says so once` |
| Set `panel.colors.muted` to `ansi256(244)` | The header, hint, ages, author, labels, and resolved items draw `ansi256(244)` | `panel.colors overrides the elements of its role` |
| Set `panel.colors.question` to `suggestion` | An open question's line draws `suggestion` | the same test |
| Set `panel.colors.mutd`, or `panel.theme` to `nope` | Defaults draw, and one toast names the roles or the themes | `an unknown role or theme leaves the defaults and says so once` |
| Set a role to `gray`, `ansi:black`, `composerSidebarBackground`, `rgb(256,0,0)`, an empty string, or a number | The role draws its default, the tree is never refused, and one toast names the value | `a role value the panel does not take leaves its default and says so once` |
| Set `panel.colors.muted` to `rgb(40,40,40)` on the default dark background, `#100f0c` | `muted` draws `subtle`, and one toast says it would vanish | `a role that would vanish on the background takes its default` |
| Set `panel.colors.muted` to `rgb(70,70,70)` on the same background | It draws `rgb(70,70,70)` with no toast | the same test |
| Set `panel.background` to `rgb(20,20,20)` and `panel.colors.muted` to `rgb(20,20,20)` | The background stays, and `muted` takes its default | the same test |
| Set `panel.background` to `rgb(80,80,80)` on `dark`, where `subtle` is `rgb(80,80,80)` | The panel draws `#100f0c`, and one toast says the background would hide `muted` | `a background that would hide a role’s default falls back to ink and says so once` |
| Set `panel.background` to `rgb(80,80,80)` and `panel.colors.muted` to `rgb(200,200,200)` on `dark` | Both draw as set, with no toast | the same test |
| Set `panel.background` to `rgb(230,230,230)` on a custom theme built on `light` whose file sets `subtle` to `rgb(225,225,225)`, then pick `light` | The panel draws `#f6f4ee`, and one toast says the background would hide `muted`; on `light` itself it draws `rgb(230,230,230)` | the same test |
| Set `panel.colors.background` | Defaults draw, and one toast names `panel.background` | `an unknown role or theme leaves the defaults and says so once` |
| Set `panel.background` to `white` on `dark`, then to `black` on `light` | The panel draws `#100f0c`, then `#f6f4ee`, with one toast for each value | `a background that would hide Claude Code’s own text falls back to ink and says so once` |
| Set `panel.background` to `white` on `light`, then to `black` on `dark` | The panel draws `rgb(255,255,255)`, then `rgb(0,0,0)`, with no toast | the same test |
| Run `project config set panel.colors.muted 'rgb(170,170,170)'` through the Bash tool | The panel redraws with the new color before the next reload | `a set of a panel key through Bash re-reads the settings at once` |
| Run `project config set panel.colors.muted 'rgb(170,170,170)' --user`, then `unset` | The file gains and loses a `[panel.colors]` entry, and the key `panel.colors.Muted` is refused | `test_panel_colors_are_set_and_unset_under_any_role_name` in `tests/test_config.py` |
| Read the settings table in `docs/cli.md` | `panel.theme` and `panel.colors.<role>` are listed with their types | `test_the_documented_keys_are_the_known_keys` |

Each test fails on the code before this change, which drew `rgb(20,20,20)`
on `dark`, took no `panel.theme` or `panel.colors`, refused no background by
contrast, and drew `watching` and the branch in palette slots.

## 5. Cost

The panel spawns the same number of processes as before on each reload:
one `project config get panel` in place of the
`project config get panel.background` it ran. A `project config set` or
`unset` of a `panel.` key that the session runs adds one read. Resolving
thirteen roles and a few dozen contrast ratios runs once per read, on each
reload rather than on each render, and the render reads one state key, so
cost does not matter here.

## 6. Rollout

The plan's three steps land in one pull request, because the default
background and the names changed with the themes, so no step alone would
ship the look it promises:

1. Route every color through the roles.
2. Add `panel.colors.<role>`, the checks, the toasts, the re-read after a
   set, the CLI keys, and the documentation, and move `watching` and
   `branch` to `remember` and `planMode`. Under a CLI from before this
   change, `project config set panel.colors.muted` and
   `project config set panel.theme` refuse the key until
   `project upgrade`, while the panel still reads a table written by hand.
   The panel refuses `white` on a dark theme and `black` on a light one,
   which #341 draws, and `docs/plugins.md` says so.
3. Add `panel.theme` and the built-in themes `ink`, `umber`, `stone`, and
   `graphite`, with `ink` as the default, and rename `panel.background`'s
   `sidebar` to `smoke`. A `.projector.toml` that still says `auto` or
   `sidebar` draws the theme's background, and the toast lists the names
   the panel takes.

## 7. Open questions

- **Whether a repository can set the panel's colors.** Deferred. The CLI
  merges a repository's `.projector.toml` over `~/.projector.toml`, so a
  repository can already set `panel.background`. Keep that until a
  repository's colors surprise someone.
- **Measuring the colors of an ANSI theme.** Deferred until a plugin can
  read the terminal's palette. On `dark-ansi`, `light-ansi`, and a custom
  theme whose file the panel cannot read, the panel checks a literal
  background only against Claude Code's own text and exact matches, so it
  can still hide a role that keeps its default there.
- **The engine's own colors.** Deferred until Claude Code lets a plugin set
  a `Button`'s label color, its key's accent, or a link's color.
- **A swatch that shows every role at once.** Deferred. The plugin draws no
  made-up rows, and the real rows show most roles.
- **Named user themes.** Deferred until someone switches between several of
  their own `[panel.colors]` tables.

## 8. Background

The role table in § 2.1 gives each role's default. Before this change,
about twenty of the colors were inline names in `hooks/register.tsx`, and
the rest came from `THEME_BACKGROUNDS`, `OTHER_BACKGROUND`,
`QUESTION_COLOR`, `NOTICE_COLOR`, `CHECK_COLORS`, the `Note` type,
`LOOP_COLORS`, and `BRANCH_COLOR`. `watching` drew `ansi256(4)`, the branch
`ansi256(6)`, and the background `rgb(20,20,20)` on a dark theme and
`rgb(240,240,240)` on a light one.

What Claude Code does with a plugin's color, which the design depends on:

- A theme key resolves by name, the non-public ones such as
  `composerSidebarBackground` included, and a name it does not know draws as
  no color.
- `rgb()`, hex, and `ansi256(n)` draw as written and do not follow the
  theme.
- A plain name such as `gray` or `cyan` draws in the default color.
- An `ansi:` name makes Claude Code refuse the plugin's whole tree.
- A mod cannot list the theme's keys at run time. Claude Code's `ThemeKey`
  type lists the 23 public ones.
- A custom theme's file holds a `base` and `overrides`. Claude Code builds
  on `dark` when the `base` names none of its six themes, and lays each
  override that names a key of the base and a color it reads, `rgb()`, hex,
  `ansi256()`, or an `ansi:` name, over the base's colors.

A live preview of the panel's colors in a Claude Code session showed how
each kind of name draws: theme keys, `rgb()`, hex, and `ansi256()` drew as
named, `gray` and `cyan` drew in the default color, and an `ansi:` name made
Claude Code refuse the tree. The plain names are where the types mislead.
Claude Code's `Color` type documents a raw color as "a name such as `"red"`,
or hex", so the panel's refusal of them rests on what the preview showed.

`project config get` reads any key, a table included. Under Projector
0.7.20, which predates every panel setting,
`project config get panel --json` returns the merged `panel` table,
`[panel.colors]` and all, while `project config set panel.colors.muted`
refuses the key.

## 9. Decisions made during implementation

The owner previewed the palette of projector.bot on the live panel, settled
on its page color, and settled the questions this plan left open:

- **The default background is `ink`, projector.bot's page color.**
  `web/app/globals.css` pairs a dark `--bg` of `#100f0c` with a light one of
  `#f6f4ee`. The default draws `#100f0c` on Claude Code's dark themes,
  `#f6f4ee` on light ones, and a custom theme by its `base`. The other
  fallbacks of § 2.2 stay.
- **Every name describes its color.** The owner: "all themes should be
  named after the color, though we can be creative. auto says nothing about
  the actual color". So `auto` becomes `ink` wherever it appeared,
  `panel.background`'s `sidebar` becomes `smoke`, and the earlier gray ships
  as `graphite`. Neither old name keeps an alias. A `.projector.toml` that
  still uses one draws the theme's background, with the one toast that lists
  the names the panel takes.
- **The built-in themes are projector.bot's palette, plus `graphite`.** They
  are every background pair in `web/app/globals.css` that has a dark and a
  light value: `--bg` as `ink`, `--surface` as `umber`, and `--surface-2` as
  `stone`. `--term-bg` stays out: it colors the site's mock terminal, which
  is dark on both, so it has no light background to give. This answers the
  open question of which built-in themes ship besides the default, whose
  candidates were `none` and `sidebar`. `none` does not ship, and `sidebar`
  ships only as `panel.background`'s `smoke`.
- **`watching` and `branch` take `remember` and `planMode`.** They are the
  public theme keys whose `dark` and `light` values sit nearest in hue to
  the palette's blue, 240°, and cyan, 180°. `remember` is `rgb(177,185,249)`
  on `dark`, 233°, and `rgb(0,0,255)` on `light`, 240°. `planMode` is
  `rgb(72,150,140)` on `dark`, 172°, and `rgb(0,102,102)` on `light`, 180°.
  On `dark`, `remember` matches `permission`, so `watching` draws in the
  color of an open question. This answers the open question of which keys
  the two take; the owner can swap either after a preview, in
  `ROLE_DEFAULTS`.
- **Muted text stays `subtle`.** The owner tried a warm muted tint in the
  preview but did not ask for it.

## 10. Outcome

Shipped. Every color the panel draws goes through a role in
`hooks/theme.ts`, and `panel.theme`, `panel.background`, and
`panel.colors.<role>` change them, with the contrast checks and toasts of
§ 2.5. Each acceptance criterion in § 4 has a passing test. The design
changed in two places, both recorded in § 9: the default background is
`ink` rather than the earlier gray, and a built-in theme is a background
pair rather than a partial role table. The owner can still swap any theme
name, `smoke`, or the keys `watching` and `branch` take after a preview,
each a one-line change.
