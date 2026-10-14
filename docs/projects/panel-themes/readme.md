---
status: draft
priority: later
---

# Theme Projector's panel

Every color Projector's panel draws comes from a named role, each role's
default follows your Claude Code theme, and you can override any role, or
pick a built-in theme, without one role vanishing on another.

## 1. Problem

Projector's panel is the area Claude Code draws above the prompt. It lists
the pull requests of the session's loops and the inbox's questions and
notices. In a terminal it draws on a background of its own, and it colors
each element by what the element means: muted text in the theme's `subtle`,
an open question in `permission`, an open notice in `inactive`, and a pull
request's state in `success`, `warning`, or `error`.

Each of those colors is written into `hooks/register.tsx` where an element
draws it, as a handful of constants and about twenty inline names. Only the
background is a setting, `panel.background`, and that causes three
problems:

- **A setting can hide what draws on it.** The background's check knows
  nothing of the colors drawn on the background. Draft #341 leaves out a
  `subtle` alias for `panel.background`, because the panel's header, key
  hint, ages, and counts are `subtle` and would vanish on it. The same
  conflict remains for values the check accepts: `white` on a dark Claude
  Code theme puts the pull request titles, drawn in the theme's light text
  color, on white.
- **No other color can change.** Someone whose terminal palette makes the
  `watching` blue, `ansi256(4)`, hard to read on the panel, or who wants
  questions in another color, has no setting to reach for.
- **Colors have no names.** A new element picks a color where it draws.
  `success` means both a pull request that is ready and a count of inserted
  lines, so nothing says which colors may change together.

It matters little today, because the defaults look right on every built-in
Claude Code theme. It grows with every color setting the panel adds, which
is why the work is recorded now and scheduled later.

## 2. Solution

The panel draws every color through one of thirteen named roles. A small
module, `hooks/theme.ts`, holds the roles, their defaults, the built-in
themes, the values each role takes, and the contrast check, and resolves
them into one color per role. With no settings, every role draws exactly
what it draws today, so the panel looks right with no configuration. A
person changes the look with `panel.theme`, which picks a built-in theme,
and `panel.colors.<role>`, which overrides one role. `panel.background`
stays the background's setting. The panel reads all of it in one
`project config get panel` call, refuses any value it cannot draw or that
would vanish on what it draws against, falls back to the role's default,
and says so once in a toast. The role list and its checks live only in the
panel, and the CLI learns only that these keys hold strings.

### 2.1 The roles

Every role but the background defaults either to a Claude Code theme key
or to no color. A theme key resolves per theme: dark, light, their
daltonized variants, the ANSI themes, and a custom theme's overrides. No
color leaves the text to the engine. So one default serves every theme,
and only the background's default depends on the theme, by its base.

`watching` and `branch` draw in the terminal palette's blue and cyan today,
`ansi256(4)` and `ansi256(6)`, which follow the terminal's palette but not
the Claude Code theme. They move to theme keys, so they adapt to light and
dark themes like every other role. The owner picks the two keys by preview,
as § 7 says, and until then step 1 of § 6 keeps the palette slots.

| Role | What draws in it | Default |
| --- | --- | --- |
| `background` | The panel's `Box` | By base, as § 2.2 says |
| `text` | A pull request's title, each ` · `, ` in 5 files`, a chosen or typed answer | No color: the engine's text color |
| `muted` | The header, the key hint, ages, a pull request's author, an item's pull request label, `sending`, resolved and snoozed items, a resolved question's other options, the tagline, `Looking for pull requests…` | `subtle` |
| `question` | An open question's line | `permission` |
| `notice` | An open notice's line | `inactive` |
| `success` | `ready`, `approved`, `checks passing`, `(recommended)`, `✓` | `success` |
| `warning` | `draft`, an open thread count such as `5*`, `checks pending` | `warning` |
| `error` | `conflict`, `changes requested`, `checks failing`, a GitHub error | `error` |
| `added` | A drawer's insertions, such as `+84` | `success` |
| `removed` | A drawer's deletions, such as `−12` | `error` |
| `watching` | `watching` in a pull request's drawer | A theme key, picked by preview |
| `reviewing` | `reviewing` in a pull request's drawer | `claude` |
| `branch` | A pull request's branch in its drawer | A theme key, picked by preview |

Some text stays outside every role, because Claude Code draws it and a
plugin cannot set its color: a plain `Button`'s label and its key, such as
`7: discuss PR` and a pull request's number, and a Markdown link, such as
`open in github`. No role uses `dimColor`, which some terminals draw at full
strength.

### 2.2 The background by Claude Code theme

| Claude Code theme | Base | Background default |
| --- | --- | --- |
| `dark`, `dark-daltonized` | dark | `rgb(20,20,20)` |
| `light`, `light-daltonized` | light | `rgb(240,240,240)` |
| `dark-ansi`, `light-ansi` | dark or light | `inverseText` |
| `custom:<slug>` | its file's `base`, `dark` when it names none | its base's |
| a custom theme whose file the panel cannot read, or any other | unknown | `inverseText` |

That is today's `THEME_BACKGROUNDS` and `OTHER_BACKGROUND`, kept as `auto`'s
background. The base also tells the contrast check in § 2.5 which side of
light the theme's own text sits on.

### 2.3 The settings

| Key | Type | Default | Takes |
| --- | --- | --- | --- |
| `panel.theme` | string | `auto` | `auto`, or a built-in theme's name |
| `panel.background` | string | `auto` | The list #341 sets: `auto`, a literal color, `sidebar`, `black`, or `white`, less a background that § 2.5 refuses |
| `panel.colors.<role>` | string | unset | A literal color, one of Claude Code's 23 public theme keys, or `default` for the engine's text color |

A literal color is `rgb(r,g,b)` or `ansi256(n)` with each number from 0 to
255, or `#rgb` or `#rrggbb`, as #341 parses them. The public keys are the
ones Claude Code's `ThemeKey` type lists, from `text` to `diffRemovedWord`.
A role refuses every name Claude Code would not draw as meant:

- **A plain name**, such as `gray`, which draws in the default color.
- **An `ansi:` name**, which makes Claude Code refuse the panel's whole
  tree.
- **A theme key outside the public 23**, such as
  `composerSidebarBackground`, whose spelling the panel cannot check.

```toml
[panel]
background = "rgb(30,30,40)"

[panel.colors]
muted = "ansi256(244)"
question = "suggestion"
```

Each layer overrides the one before it:

1. The `auto` defaults for the session's base, § 2.1 and § 2.2.
2. The built-in theme `panel.theme` names. A built-in theme is a partial
   role table, so a role it leaves out keeps `auto`'s default and still
   follows the Claude Code theme.
3. `panel.background` for the background, and `panel.colors.<role>` for
   each other role.

`auto` is the only built-in theme until the owner chooses the first other
one, as § 7 says. The key and the layer exist from the start so that a
built-in theme needs no new setting.

### 2.4 The flow

1. When the session starts, the panel reads the Claude Code theme from
   `$.config.list()`, works out its base, reading a custom theme's file as
   it does today, and stores the base in `panelBase`.
2. One timer tick later, and on each reload, the panel runs
   `project config get panel --json` once. The CLI merges every
   `.projector.toml` that applies, table by table. A missing `panel` table,
   which the CLI reports as `null` with exit status 1, means no settings.
3. The panel resolves one color per role, layer by layer as § 2.3 says. A
   value that fails its check gives way to the layer below and adds a
   problem: one outside the role's grammar, a role or a theme name the
   panel does not know, a non-string, or a color that vanishes by § 2.5.
4. It stores the colors in `panelColors`. It toasts every problem it has not
   toasted in this session, all new problems from one read in one toast,
   such as
   `projector: panel.colors.mutd is not a role (the roles are background, text, muted, …); panel.colors.muted rgb(25,25,25) would vanish on the panel's rgb(20,20,20), so it draws subtle`.
5. The render reads `panelColors` and passes `colors.<role>` to the panel's
   `Box` and to every `Text`. It waits on no read.
6. A theme picked in `/theme` recolors at once: the `config.set` hook stores
   the new base and resolves again. A `project config set` or `unset` of a
   `panel.` key that the session runs through its Bash tool re-reads the
   settings as soon as the command finishes, through the `tool.call` hook
   that already reads the session's commands, and so does `/projector`.
   Any other change shows on the next reload, within 30 seconds.

### 2.5 Contrast

The panel compares two colors by their contrast ratio, as the Web Content
Accessibility Guidelines (WCAG) 2 define it, from 1:1 for the same color to
21:1 for black on white. It computes the ratio only when it knows both
colors: a literal `rgb()` or hex value, `black`, `white`, or `ansi256(n)`
for `n` from 16 to 255, which terminals take from the standard xterm table.
It cannot know a theme key's value, `sidebar`'s, or that of the terminal
palette's slots 0 to 15, so it compares those only for an exact match.

- **A color that would vanish is refused.** A role whose color matches the
  background's exactly, or whose contrast ratio with it is under 1.5:1,
  takes the layer below. For example, `rgb(40,40,40)` on the default
  `rgb(20,20,20)` is 1.25:1 and is refused, while `rgb(70,70,70)` is 1.95:1
  and draws. When an override of the background meets an override of a
  role, the role gives way, so one element changes rather than the whole
  panel.
- **A background that would hide Claude Code's own text is refused.**
  Claude Code draws some of the panel's text itself, and no role can
  recolor it: a `Button`'s label, the accent of its key, and a Markdown
  link. A `Button`'s label draws in the theme's text color, near white on
  a dark base and near black on a light one. So the panel compares a
  background it knows with white on a dark base and with black on a light
  one. A background under 1.5:1 falls back to the layer below, which is
  `auto`'s background until a built-in theme sets one, and the panel says
  so once in a toast. That refuses `white` on a dark theme and `black` on
  a light one. On a base the panel cannot tell, it makes no such check.
- **This partly reverses #341.** #341 accepts `black` and `white` on every
  theme. From step 2 of § 6, the panel draws `white` only on a light theme
  and `black` only on a dark one, and a person who set the other gets
  `auto` and one toast.
- **Low contrast that stays readable draws as set.** A color at 1.5:1 or
  more draws with no warning. The panel's muted text sits at low contrast
  by design, so a warning at a higher ratio would flag looks like the
  default.

### 2.6 The files

| File | Change |
| --- | --- |
| `hooks/theme.ts` (new) | `ROLES` with their defaults, `BASE_BACKGROUNDS`, `BUILT_IN_THEMES`, the literal color patterns and `BACKGROUND_ALIASES` from #341, `PUBLIC_THEME_KEYS` checked against Claude Code's `ThemeKey` type at compile time, the contrast ratio, and `resolve(base, settings)`, which returns `{ colors, problems }` |
| `hooks/register.tsx` | Draws every color through `colors.<role>`. Removes `QUESTION_COLOR`, `NOTICE_COLOR`, `BRANCH_COLOR`, `LOOP_COLORS`, `CHECK_COLORS`, the colors in `Note`, `THEME_BACKGROUNDS`, `OTHER_BACKGROUND`, and the background check, which move into `hooks/theme.ts`. `readBackground` becomes `readSettings`, which reads the `panel` table, and `paint` stores the base. The Bash hook re-reads after a `project config set` or `unset` of a `panel.` key |
| `hooks/types.d.ts` | The state keys `panelBase`, `panelSettings`, and `panelColors` replace `panelThemeBackground` and `panelBackground` |
| `hooks/theme.test.ts` (new) | `resolve` and the contrast ratio, table by table |
| `hooks/panel.test.tsx` | The defaults on each Claude Code theme, overrides, refusals, toasts, and the re-read after a set |
| `src/projector/config.py` | `SETTINGS` gains `panel.theme` as a string, and a table entry, `panel.colors`, whose keys `set` and `unset` accept under any lowercase role name, each value a string |
| `tests/test_config.py` | `set` and `unset` of `panel.colors.<name>`, and the documented keys test reads the `panel.colors.<role>` row |
| `docs/plugins.md` | "Choose the panel's color" describes the roles, the settings, the contrast rule, and the toasts. Keep its anchor, `#choose-the-panels-color`, which other sections link to |
| `docs/cli.md` | The settings table gains `panel.theme` and `panel.colors.<role>` |

## 3. Why this design

- **Roles, not elements.** A role names what a color means, so a new
  element picks a role instead of a color, and an override changes every
  element that means the same thing. Thirteen roles cover every color the
  panel draws today. `added` and `removed` are apart from `success` and
  `error`, because a diff stat and a verdict can want different colors.
- **Theme keys as defaults.** A theme key already follows every Claude Code
  theme, the daltonized ones and a custom theme's overrides included. The
  defaults keep today's choices, so step 1 of § 6 changes nothing anyone
  sees. Only the background needs a table by base, because no theme key
  means "a step darker than the terminal".
- **`watching` and `branch` follow the theme too.** A palette slot keeps
  the terminal's own blue or cyan on every theme, and those can be faint
  on the light background. The owner chose theme keys for both, so they
  adapt to light and dark as the other roles do.
- **Settings, not theme files.** `.projector.toml` already merges per file,
  and `project config set` already writes it, so a person's own theme is a
  `[panel.colors]` table in `~/.projector.toml`. A theme file format would
  need a reader, a location, and a command of its own. Named user themes
  wait until someone switches between several (§ 7).
- **`panel.background` stays.** It already ships, and a background takes
  different values from a text role: #341's aliases are shades that look
  right behind text. A second key for the same color, such as
  `panel.colors.background`, would make the reader ask which one wins.
- **One owner for each list.** The plugin and the CLI install and upgrade
  apart, as #341 found, so the panel, which draws the colors, owns the
  roles, the theme names, and every check. The CLI knows only that the keys
  hold strings, so it never needs a release to add a role, and
  `project config get` reads the `panel` table under any CLI version.
- **Refuse what vanishes, and nothing else.** Adjusting a color the person
  chose would change something they did not ask for, and the panel cannot
  compute a theme key's value to adjust it anyway. A warning tier would
  flag the panel's own deliberate low-contrast muted text. Refusing only
  what vanishes keeps every role visible and leaves taste to the person.
- **The engine's text sets a limit on the background.** No role can move
  Claude Code's own text off a background that hides it, so a warning
  alone would leave labels and links unreadable until the person noticed.
  The owner chose to refuse such a background and fall back to `auto`,
  the same as for any other value the panel cannot draw.
- **Public theme keys only.** Claude Code draws a theme key it does not
  know as no color at all, and a mod cannot list the theme's keys at run
  time. Only the public 23 can be checked, through the `ThemeKey` type, so
  a typo is refused rather than drawn as nothing.
- **Preview on real rows.** A set re-reads at once (§ 2.4, step 6), so a
  person sees the change on their own pull requests and items. The plugin
  draws no made-up rows. Designing a new built-in theme uses a temporary
  session mod, as the panel's layout work has.

## 4. Acceptance criteria

| Do | Expect | Test |
| --- | --- | --- |
| Draw the panel with no settings on `dark`, `light`, both daltonized themes, both ANSI themes, custom themes on each base, one with no `base`, and one with no file | Every element draws exactly the color it draws before the change | `with no settings every role draws today's color on the <theme> theme` in `hooks/panel.test.tsx`, one test per theme |
| Open a pull request's drawer with no settings on `dark`, then on `light` | `watching` and the branch draw the theme keys the owner picked, not `ansi256(4)` or `ansi256(6)` | `watching and the branch draw theme keys, so they follow the theme` |
| Set `panel.colors.muted` to `ansi256(244)` | The header, hint, ages, author, labels, and resolved items draw `ansi256(244)` | `panel.colors overrides the elements of its role` |
| Set `panel.colors.question` to `suggestion` | An open question's line draws `suggestion` | the same test |
| Set `panel.colors.mutd`, or `panel.theme` to `nope` | Defaults draw, and one toast names the roles or the themes | `an unknown role or theme leaves the defaults and says so once` |
| Set a role to `gray`, `ansi:black`, `composerSidebarBackground`, `rgb(256,0,0)`, an empty string, or a number | The role draws its default, the tree is never refused, and one toast names the value | `a role value the panel does not take leaves its default and says so once` |
| Set `panel.colors.muted` to `rgb(40,40,40)` on the default dark background | `muted` draws `subtle`, and one toast says it would vanish | `a role that would vanish on the background takes its default` |
| Set `panel.colors.muted` to `rgb(70,70,70)` on the same background | It draws `rgb(70,70,70)` with no toast | the same test |
| Set `panel.background` to `rgb(20,20,20)` and `panel.colors.muted` to `rgb(20,20,20)` | The background stays, and `muted` takes its default | the same test |
| Set `panel.background` to `white` on `dark`, then to `black` on `light` | The panel draws `rgb(20,20,20)`, then `rgb(240,240,240)`, with one toast for each value | `a background that would hide Claude Code's own text falls back to auto and says so once` |
| Set `panel.background` to `white` on `light`, then to `black` on `dark` | The panel draws `rgb(255,255,255)`, then `rgb(0,0,0)`, with no toast | the same test |
| Run `project config set panel.colors.muted 'rgb(1,2,3)'` through the Bash tool | The panel redraws with the new color before the next reload | `a set of a panel key through Bash re-reads the settings at once` |
| Run `project config set panel.colors.muted 'rgb(1,2,3)' --user`, then `unset` | The file gains and loses a `[panel.colors]` entry, and the key `panel.colors.Muted` is refused | `test_panel_colors_are_set_and_unset_under_any_role_name` in `tests/test_config.py` |
| Read the settings table in `docs/cli.md` | `panel.theme` and `panel.colors.<role>` are listed with their types | `test_the_documented_keys_are_the_known_keys` |

The first row's tests pass before the change and after step 1 of § 6,
because they pin today's look. Step 2 changes the colors they expect for
`watching` and the branch to the picked theme keys. Every other test fails
before the change.

## 5. Cost

The panel spawns the same number of processes as today: one
`project config get panel` per reload in place of the
`project config get panel.background` it runs now. Resolving thirteen roles
and at most thirteen contrast ratios runs once per read, at most every 30
seconds, and the render reads one state key, so cost does not matter here.

## 6. Rollout

1. Route every color through the roles, with today's colors as `auto`'s
   defaults. Nothing visible changes, so this step ships alone.
2. Add `panel.colors.<role>`, the checks, the toasts, the re-read after a
   set, the CLI keys, and the documentation. It needs step 1. Under a CLI
   from before this step, `project config set panel.colors.muted` refuses
   the key until `project upgrade`, while the panel still reads a table
   written by hand. From this step, the panel refuses `white` on a dark
   theme and `black` on a light one, which #341 draws, so `docs/plugins.md`
   says so in the same change. The step also moves `watching` and `branch`
   to the theme keys the owner picks by preview.
3. Add `panel.theme` and the first built-in theme the owner chooses. It
   needs step 2.

## 7. Open questions

- **Which built-in themes ship besides `auto`.** Owner, before step 3. The
  owner decides by previewing both candidates in a session mod: `none`,
  which draws no background so the panel sits on the terminal's own, and
  `sidebar`, which draws on Claude Code's side-panel grey and looks raised.
- **Which theme keys `watching` and `branch` take.** Owner, before step 2.
  Both move to theme keys, as § 2.1 says, and the owner picks the two keys
  by preview.
- **Whether a repository can set the panel's colors.** Deferred. The CLI
  merges a repository's `.projector.toml` over `~/.projector.toml`, so a
  repository can already set `panel.background`. Keep that until a
  repository's colors surprise someone.
- **The engine's own colors.** Deferred until Claude Code lets a plugin set
  a `Button`'s label color, its key's accent, or a link's color.
- **A swatch that shows every role at once.** Deferred. The plugin draws no
  made-up rows, and the real rows show most roles.

## 8. Background

The role table in § 2.1 gives each element's color at the time of writing,
as the role's default. In `hooks/register.tsx`, about twenty of them are
inline names, and the rest come from `THEME_BACKGROUNDS`,
`OTHER_BACKGROUND`, `QUESTION_COLOR`, `NOTICE_COLOR`, `CHECK_COLORS`, the
`Note` type, `LOOP_COLORS`, and `BRANCH_COLOR`.

What Claude Code does with a plugin's color, which the design depends on:

- A theme key resolves by name, the non-public ones such as
  `composerSidebarBackground` included, and a name it does not know draws
  as no color.
- `rgb()`, hex, and `ansi256(n)` draw as written and do not follow the
  theme.
- A plain name such as `gray` or `cyan` draws in the default color.
- An `ansi:` name makes Claude Code refuse the plugin's whole tree.
- A mod cannot list the theme's keys at run time. Claude Code's `ThemeKey`
  type lists the 23 public ones.

`project config get` reads any key, a table included. Under Projector
0.7.20, which predates every panel setting, `project config get panel --json`
returns the merged `panel` table, `[panel.colors]` and all, while
`project config set panel.colors.muted` refuses the key.
