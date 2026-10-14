# Install Projector

Projector is two things to install: the `project` command, and the plugin
that gives Claude Code and Codex its skills. The framework is Projector; the
command it installs is `project`. The repository, the `projector-cli`
distribution, the Python package, and the `projector@projector` host plugin
all keep the longer name.

Projector keeps one canonical skill tree under `skills/`. The Claude Code
manifest at `.claude-plugin/plugin.json` and the Codex manifest at
`.codex-plugin/plugin.json` both point to that directory. A host loads the same
instructions and supporting scripts without a generated copy or host-specific
fork.

The plugin provides `spec`, `implement`, `finish`, `review-pr`, `fix-pr`,
`start-review-loop`, `start-watch-loop`, `summarize-pr`, and `write`.
`summarize-pr` summarizes a pull request's changes as a guided review page.
Claude invokes a plugin skill as `/projector:<skill>`; Codex invokes it as
`$<skill>`.
`spec` writes a project's technical spec under `docs/projects/`.
`implement` builds from that plan, or from a description of a change small
enough to need no spec, such as `/projector:implement fix the typo in the
install docs`.
`review-pr` reviews a pull request's current head once, by the method in
`skills/review-pr/method.md`, and publishes one labeled review;
`start-review-loop` runs it on every new head of your pull requests and of
those that request your review. `fix-pr` answers one pull request's
findings, verifying each by the same protocol, and merges the base into a
branch that conflicts with it; `start-watch-loop` runs it, one subagent per
pull request, whenever its watcher reports work on one this conversation
owns. `implement` starts that loop on every pull request it opens, or adds
the pull request to the loop the conversation already runs,
and a loop it starts ends once every pull request it watches has merged or
closed. Only the conversation you talk to runs the loop. An `implement`
running as a subagent hands its pull request up, through every subagent
above it, to that conversation, which starts or joins the loop on it, and a
subagent asked to start the loop hands that request up the same way. Where
the host cannot keep the loop running, `implement` says so when it hands the
pull request over. To start the loop yourself instead, run
`project config set watch.auto_start false --user`, or leave out `--user` to
change only the current repository. `fix-pr` also implements or defers each
suggestion a review lists. While it builds a change that no review has read,
such as a
suggestion it implements or a change you ask for, it holds the pull request
in draft with a comment that lists the changes, so the pull request never
looks ready to merge while the changes are coming. It posts and removes that
comment with `project review hold`, and ends the hold once the changes are
pushed, so a clean review of that push marks the pull request ready. The
reviewer settles its own
findings on every head: it resolves one it verifies fixed, reopens one resolved
too early, and keeps one whose decline does not hold, so its verdict rests on
what it checked rather than on who clicked resolve. In a repository with a
Projector site, `review-pr` then runs `summarize-pr` on every pull request it
reviews and links the summary from its review;
`project config set review.summarize false` turns that off. All three of
`implement`, `review-pr`, and `fix-pr` share the code guidelines in
`skills/guidelines.md`, so the rules one follows are the rules the others
review and fix against. Every skill that writes prose a person reads, such as
a plan, a review, a summary, or a pull request body, follows the writing
guide in `skills/writing.md`. `write` applies the same guide to any other
prose you ask for, such as `/projector:write tighten the install section of
the README`. The core workflows use the local CLI and do not require MCP.

When dependent work needs more than one pull request to stay reviewable,
`implement` opens it as a stack that GitHub records, using the `gh stack`
GitHub CLI extension, and `fix-pr` carries a fix on a lower layer up through
the layers above it right away. Every layer stays on the current head of the
layer below it, so you can merge the whole stack, or any number of layers
from the bottom up, in one step from GitHub, and `start-watch-loop` reports
which of them are ready to merge together. The extension is optional.
Without it, or in a repository that does not have stacks enabled,
`implement` chains the pull requests by base branch and tells you once, and
`fix-pr` and `start-watch-loop` find a stack's layers from those base
branches. Install it with `gh extension install github/gh-stack` to get
real stacks.

GitHub's own `gh-stack` skill teaches an agent the extension's commands.
Projector does not copy it. Both hosts' Projector marketplaces serve a
`gh-stack` plugin that holds upstream's skill from
[`github/gh-stack`](https://github.com/github/gh-stack) unmodified. In
Claude Code, the `projector` plugin depends on it, so installing `projector`
installs it too, and you invoke it as `/gh-stack:gh-stack`. Codex has no
plugin dependencies, so there you add it as a plugin of its own, as
[Install for Codex](#install-for-codex) shows; Codex lists it as
`gh-stack:gh-stack`. Where upstream's skill and Projector's workflow
differ, `implement` says which rules win, such as drafting every layer and
never merging. Projector's skills work without upstream's skill, because
they name the `gh stack` commands they run.

Both marketplace entries, in `.claude-plugin/marketplace.json` and
`.agents/plugins/marketplace.json`, pin upstream to one tag, its commit, and
the matching `version`, so a newer upstream skill reaches you only when a
Projector release moves all three in both files. A packaging test fails when
the two entries disagree.

## Install with one command

The installer at projector.bot installs the CLI from the newest release and
the plugin for each host you have, without git:

```sh
curl -fsSL https://projector.bot/install.sh | bash
curl -fsSL https://projector.bot/install.sh | bash -s -- status
```

It takes the same targets as `./install.sh` below. Without a checkout it
installs the CLI with `pipx` from the source archive of `PROJECTOR_REF`
(default `v0`, which every release moves), or, without `pipx`, into a virtual
environment under `~/.local/share/projector` with `project` linked into
`~/.local/bin`. It adds the `PROJECTOR_REPO` marketplace (default
`ninjudd/projector`) to each host. `status` compares the installed versions
with the release's. `project upgrade` downloads and runs the same installer,
and every `project` command warns on stderr when a newer release is out (see
[the CLI guide](cli.md#know-when-a-release-is-out)). projector.bot serves
`install.sh` from the `v0` tag, so it is always the newest release's
installer. Set `PROJECTOR_REF` to install a particular release, such as
`v0.5.7`, and `PROJECTOR_REPO` to install from a fork.

The installer also installs or moves the `gh-stack` plugin on each host,
since `claude plugin update` neither installs a dependency a plugin newly
declares nor moves one already installed. A marketplace that does not serve
`gh-stack` yet leaves Projector installed and prints a `gh-stack` warning.
The `all` target, the default, also installs the `gh stack` extension with
`gh extension install github/gh-stack` when `gh` is installed and the
extension is not, and runs `gh extension upgrade stack` when the installed
extension is older than the release the marketplace pins. It leaves an
extension that provides `gh stack` from another owner alone, prints a
`skipped` row when `gh` is missing, and never fails the install over the
extension. `status` adds a `stack-` row comparing the extension with the
pin.

## Install only the CLI

`pipx` installs an isolated `project` from the newest release's source
archive. It needs Python 3.11 or newer:

```sh
pipx install https://github.com/ninjudd/projector/archive/v0.tar.gz
project help
```

## Install for Claude Code

Add the Projector marketplace and install its plugin at user scope:

```sh
claude plugin marketplace add ninjudd/projector --scope user
claude plugin install projector@projector --scope user
```

The install also installs the `gh-stack` plugin that `projector` depends on.
Invoke a skill with the plugin namespace, for example:

```text
/projector:spec a safer deploy workflow
```

To update by hand, refresh the marketplace and update both plugins.
`claude plugin update projector@projector` moves only Projector, and
Projector fails to load while `gh-stack` is missing, so install `gh-stack`
when you do not have it yet:

```sh
claude plugin marketplace update projector
claude plugin update projector@projector
claude plugin install gh-stack@projector --scope user   # when it is missing
claude plugin update gh-stack@projector
```

Validate a checkout before publishing it:

```sh
claude plugin validate .
claude plugin validate skills
claude plugin test .
hooks/typecheck.sh
```

`hooks/typecheck.sh` type-checks the hooks module against the declarations
Claude Code writes to `.claude-plugin/types/` when an interactive session
loads the checkout with `claude --plugin-dir .`. Start that session once, and
run `npm ci` in `site/`, before the first check, as
[Validate a change](develop.md#validate-a-change) describes. A linked
worktree that has no declarations or no `tsc` of its own uses the main
checkout's.

### See your loops' pull requests in Projector's panel

In Claude Code, the plugin also ships a hooks module in `hooks/` that lists
the open pull requests your loops track in the panel above the prompt, one
line each. Run `/projector` to show or hide the panel. It also appears when the
session starts a loop's watcher for the first time.

```text
  /projector                                              2 pull requests  12s   [-]
   #267 draft List a loop's open pull requests above the Claude Code prompt 2h
   #258 draft grace Name the reviewed head in a review's sig… conflict · 5* 5m
        reviewing · review-signature · +84 −12 in 5 files · checks passing
        7: discuss PR
        8: dismiss PR
        open in github
        open in projector
                                                           ctrl+x tab to focus
```

Each line is the pull request's number, `draft` or `ready`, its author when
someone else wrote it, and the title. Notes follow when the pull request
has them: `conflict` or `changes requested` in red, `approved` in green,
and last the number of unresolved review threads before a `*` in yellow,
such as `5*`. The ` · ` between two notes keeps the default text color. The
line ends with how long ago the pull request last changed, in the column
where the inbox's items end with their ages. Only the title shortens on a
narrow terminal.

Your own pull requests come first, then everyone else's, each author's
together and the authors in order of login, with no line between the groups.
The panel tells which are yours by the account `gh` is signed in as, which it
asks for in the same query as the pull requests. A pull request that both a
`start-watch-loop` watcher, `watch-threads.sh`, and a `start-review-loop`
watcher, `watch-prs.sh`, track is listed once.

In a terminal, the panel draws on a background of its own, so a line that
moves as the panel scrolls is plainly the panel's. It is inset one column
from the terminal's edge and ends where Claude Code's `[-]`, which collapses
the panel, begins. Its header and its key hint sit one column in from the
panel's left edge, and the pull requests and items between them two columns
in from each side, so every line ends two columns short of the panel's right
edge, where the ages line up. The panel's background is `ink`, the page
color of Projector's site, [projector.bot](https://projector.bot): near
black on a dark Claude Code theme and a warm off-white on a light one.

- **`dark` and `dark-daltonized`** draw on `#100f0c`.
- **`light` and `light-daltonized`** draw on `#f6f4ee`.
- **A custom theme** takes the color of the theme it builds on, the `base`
  in its file under `~/.claude/themes/`, or under `$CLAUDE_CONFIG_DIR/themes/`
  when that variable is set. A file with no `base` builds on `dark`, and a
  file the panel cannot read gives `inverseText`.
- **Any other theme**, such as `dark-ansi` or `light-ansi`, draws on its own
  `inverseText` color, because its colors come from the terminal's palette,
  which the panel cannot see.

A theme you pick with `/theme` recolors the panel at once. To draw another
of the panel's themes, or a shade or a text color of your own, set
`panel.theme`, `panel.background`, or `panel.colors`, as
[Choose the panel's color](#choose-the-panels-color) describes.

The panel's first line is its header, drawn in the `subtle` color. It starts
with `/projector`, the command that shows and hides the panel, at its left.
At its right, it counts the pull requests the panel lists, a line each, such
as `2 pull requests`, and two spaces on, it says how long ago the panel
reloaded, drawn as the rows draw their ages: in the short form, such as
`12s`, and ending in their column. A reload just now reads `0s`, as an item
posted just now does. A count of zero is left out, so a panel with nothing
to count shows `/projector` and the time alone. The rows start on the line
under the header.

The panel's last line is a key hint, also in `subtle`, at its right, so it
ends in the ages' column. While the prompt has the keyboard, it reads
`ctrl+x tab to focus`. Once the panel has the keyboard, it reads
`esc to return · enter to open`. Claude Code says nothing when Esc gives the
keyboard back, so that hint stays until you next type in the prompt or send
a prompt from it. The hint leaves the arrow keys out, because which of them
moves between lines depends on whether the panel scrolls, as
[Use the panel from the keyboard](#use-the-panel-from-the-keyboard)
describes.

Claude Desktop, VS Code, and the mobile app do not set the panel's text in a
terminal's columns, so they draw no background, no header, and no hint, and
there the panel starts with its first row.

When a poll cannot reach GitHub, the panel keeps showing the pull requests
GitHub gave last, and the header's time shows how old they are. It shows
GitHub's error only when it has none of them to show.

Click a pull request's number to open a drawer under it, and click it again
to close the drawer; opening one drawer closes any other. The drawer starts
one column past the number, under `draft` or `ready`. It says whether your
loops are watching the pull request, in the theme's `remember` blue, or
reviewing it, in Claude's orange, then its branch, in the theme's
`planMode` teal, its size, and its checks. The size reads as a diff stat: the insertions,
such as `+84`, in green and the deletions, such as `−12`, in red. The
checks' state takes a color of its own, and the rest, ` in 5 files` and
each `·` included, keeps the default text color. Below that come two
actions, a line each, on the digits `7` and `8`, as
[Use the panel from the keyboard](#use-the-panel-from-the-keyboard)
describes, and then two links:

- **discuss PR** puts the pull request's number in the prompt at the
  cursor, as `#258`, or as `owner/repo#258` when the panel lists more than
  one repository.
- **dismiss PR** asks to be sure first, as
  [dismissing an item](#read-your-inbox-in-projectors-panel) does, then
  keeps the pull request out of every loop of this session that tracks
  it, and the panel drops its line and says which loops stopped, such as
  `stopped watching and reviewing acme/app#258`.
- **open in github** opens the pull request on GitHub.
- **open in projector** opens its Projector summary, when the pull
  request's description links one.

Each link has a line of its own, because a terminal that does not draw
hyperlinks shows a link's URL after its text, and a line shared with
another link would cut the URL off.

**dismiss** runs `project loop drop` once for each tracked file that lists
the pull request, which appends a `!owner/repo#number` line that both
watchers honor, so the loops stop acting on it. When the command fails, the
panel shows its error and keeps the line. To bring a dismissed pull request
back, run `project loop restore` with the same reference and tracked file,
as [Change a loop's tracked file](cli.md#change-a-loops-tracked-file)
describes. A drawer closes whenever its pull request leaves the panel,
dismissed here, dropped from a terminal, or merged, so the line comes back
with its drawer closed.

On the main screen, which Claude Code uses under tmux and when
`CLAUDE_CODE_NO_FLICKER=0` is set, use the panel from the keyboard, as
[Use the panel from the keyboard](#use-the-panel-from-the-keyboard)
describes. The **open in github** and **open in projector** links take no
press there; the terminal opens them the way it opens any other link.

The panel lists only this session's loops. It finds a loop by the
`--tracked` file its watcher reads, and learns that file when the session
runs the watcher. A loop another session runs stays out of this session's
panel. When the panel cannot tell which file a watcher's `--tracked` argument
names, such as a path built from a variable the session does not set, it
says so once and leaves that loop out.

The panel reloads when one of the session's watchers reports a change and
when a turn ends, and every 30 seconds otherwise, only while it is shown.
Each reload asks GitHub for every pull request in one `gh api graphql` call
and reads the `panel` settings with `project config get panel`. The skills
never depend on the panel, and Codex has no equivalent.

#### Choose the panel's color

The panel draws every color through a role, named for what the color means,
and each role's default follows your Claude Code theme. Three settings
change them, and each takes a name that describes its color:

- **`panel.theme`** picks one of the panel's built-in themes.
- **`panel.background`** sets the background alone, over the theme's.
- **`panel.colors.<role>`** sets the color of one role.

Set them in `~/.projector.toml` to use them in every repository:

```sh
project config set panel.theme umber --user
project config set panel.colors.muted 'ansi256(244)' --user
```

```toml
[panel]
theme = "umber"

[panel.colors]
muted = "ansi256(244)"
```

`project config unset` returns a setting to its default. A change the
session makes with `project config set` or `unset` shows as soon as the
command finishes. The panel also reads the settings when the session
starts, when `/projector` shows the panel, and each time the panel reloads,
so a file you edit by hand shows within 30 seconds, without a restart.

##### The themes

Each built-in theme is a background for a dark and for a light Claude Code
theme, drawn from the palette of [projector.bot](https://projector.bot).
Every other role keeps its default.

| Theme | On a dark theme | On a light theme | What it looks like |
| --- | --- | --- | --- |
| `ink`, the default | `#100f0c` | `#f6f4ee` | The site's page: near black, or a warm off-white |
| `umber` | `#171510` | `#fdfcf8` | The site's code blocks: a warm brown-black, or near white |
| `stone` | `#1e1c16` | `#eeebe2` | The site's inline code: a step lighter than `ink` on dark, a step darker on light |
| `graphite` | `rgb(20,20,20)` | `rgb(240,240,240)` | A neutral gray, a step darker than most terminals |

On a Claude Code theme whose colors come from the terminal's palette, such
as `dark-ansi` or `light-ansi`, and on a custom theme whose file the panel
cannot read, every theme draws the Claude Code theme's `inverseText`. The
panel draws `ink` for any other value of `panel.theme`.

##### The background

`panel.background` takes these values, and with no value the panel draws
the theme's background:

- **A theme's name**, such as `stone`, draws that theme's background.
- **`smoke`** draws the gray of Claude Code's side panel, its theme's
  `composerSidebarBackground`.
- **`black`** draws `rgb(0,0,0)`, and **`white`** draws `rgb(255,255,255)`,
  whatever the theme.
- **A literal color** draws as written: `rgb(r,g,b)` with each number from
  0 to 255, `#rgb` or `#rrggbb`, or `ansi256(n)` with `n` from 0 to 255.

The panel refuses any other value, such as a theme color's name like
`inverseText` or `subtle`, a misspelling like `smok`, or `ansi:black`, and
draws the theme's background instead.

##### The roles

| Role | What it colors | Default |
| --- | --- | --- |
| `text` | A pull request's title, each ` · `, ` in 5 files`, and a chosen or typed answer | Claude Code's text color |
| `muted` | The header, the key hint, ages, another person's login, an item's pull request, `sending`, a resolved or snoozed item, the options a question's answer passed over, the tagline, and `Looking for pull requests…` | `subtle` |
| `question` | An open question's line | `permission` |
| `notice` | An open notice's line | `inactive` |
| `success` | `ready`, `approved`, `checks passing`, `(recommended)`, and `✓` | `success` |
| `warning` | `draft`, an open thread count such as `5*`, and `checks pending` | `warning` |
| `error` | `conflict`, `changes requested`, `checks failing`, and GitHub's error | `error` |
| `added` | A drawer's insertions, such as `+84` | `success` |
| `removed` | A drawer's deletions, such as `−12` | `error` |
| `watching` | `watching` in a pull request's drawer | `remember` |
| `reviewing` | `reviewing` in a pull request's drawer | `claude` |
| `branch` | A pull request's branch in its drawer | `planMode` |

Each default is a Claude Code theme color, so it follows your theme, the
daltonized and custom themes included. `panel.colors.<role>` takes a
literal color as `panel.background` takes one, one of Claude Code's public
theme colors, from `text` to `diffRemovedWord`, or `default` for Claude
Code's own text color. The panel refuses any other value, such as a color's
plain name like `gray`, which Claude Code draws in the default color, an
`ansi:` name, which makes Claude Code refuse the whole panel, or a theme
color outside the public ones, such as `composerSidebarBackground`. A
`panel.colors.background` is refused too, because `panel.background` sets
the background.

Claude Code draws some of the panel's text itself, and no role changes it:
a key's label, such as `7: discuss PR`, a pull request's number, and a link,
such as `open in github`.

##### What the panel refuses

The panel refuses a color that would vanish: one under a contrast ratio of
1.5:1 against the color it draws on, as the Web Content Accessibility
Guidelines (WCAG) 2 measure contrast. It measures a color it knows: a
literal color, `ansi256(n)` from 16 to 255, which terminals take from the
standard xterm table, and a theme color on `dark`, `light`, their
daltonized themes, or a custom theme built on one of them, whose own colors
count. It settles the background first, then each role:

- **A role that would vanish draws its default.** For example, `muted` set
  to `rgb(40,40,40)` is 1.30:1 on `ink`'s `#100f0c`, so `muted` draws
  `subtle`. `rgb(70,70,70)` is 2.03:1 and draws as set. Low contrast that
  stays readable draws with no warning, because the panel's muted text is
  quiet by design.
- **A background that would hide a role's default gives way.** On `dark`,
  `subtle` is `rgb(80,80,80)`, so `panel.background = "rgb(80,80,80)"`
  would hide the header, the hint, and the ages, and the panel draws the
  theme's background. Set `panel.colors.muted` to a color that shows on it,
  such as `rgb(200,200,200)`, and both draw as set.
- **A background that would hide Claude Code's own text gives way.** A
  key's label and a link draw in the theme's text color, near white on a
  dark theme and near black on a light one. So the panel draws `white` only
  on a light theme and `black` only on a dark one.

When the background that `panel.theme` names gives way too, the panel draws
`ink`. On a Claude Code theme whose colors come from the terminal's palette,
or whose file the panel cannot read, the panel knows only literal colors,
so a background can still hide a role that keeps its default there.

Each time the panel refuses a value, it says why once a session, in one
toast for everything one read finds:

```text
projector: panel.colors.mutd is not a role (the roles are text, muted, question, notice, success, warning, error, added, removed, watching, reviewing, and branch); panel.colors.muted rgb(40,40,40) would vanish on the panel’s #100f0c, so muted draws subtle
```

### Read your inbox in Projector's panel

An agent in Claude Code asks you a question with Projector's `ask` tool,
which the model sees as `mcp__projector__ask`, and tells you something that
needs no answer with the `tell` tool, `mcp__projector__tell`. Either way it
keeps working. When an item it posted no longer needs you, such as a question
your reply settled, the agent takes it back with the `withdraw` tool,
`mcp__projector__withdraw`, by the number `ask` or `tell` returned. The panel
never shows that number, so the agent names an item to you by what it asks or
tells. When your reply shows that a question was unclear, the agent rewrites
the question in place with the `revise` tool, `mcp__projector__revise`. When
you tell it you dismissed or snoozed an item by mistake, the agent brings the
item back under its number with the `restore` tool, `mcp__projector__restore`,
rather than asking again. An agent that runs `project inbox ask`, `tell`,
`withdraw`, `revise`, or `restore` from the shell reaches the same inbox, as
[Use the inbox](cli.md#use-the-inbox) describes.

The items are a list below the pull requests, oldest first, each line
starting with `-`. An open question's line is drawn in the theme's
`permission` color, the one Claude Code gives its own permission prompts,
because an agent's question usually asks leave to do something. An open
notice's line is drawn in the theme's mid gray, `inactive`, apart from the
`subtle` of a resolved or snoozed item, so you can tell which items want an
answer and which want a read. Each line ends with the pull request the item
is about, as the panel labels it, and how long ago it was posted. The panel
appears when an item arrives or is restored, even after you hid it, and in
a terminal the header counts the open questions and the open notices after
the pull requests, before its time.

```text
  /projector                1 pull request · 2 questions · 1 notice  12s   [-]
   #337 draft Retry failed webhook deliveries with backoff            1h

   - Run project init to add the publish rule?                        2m
   - Decline the finding about the retry budget?                #337  5m
   - Force-pushed #337 to drop a stray debug file               #337  1m
                                                     ctrl+x tab to focus
```

Click anywhere on an item to open its drawer, and click it again to close
it. On the main screen, press Enter on it from the keyboard instead. While
its drawer is open, the item has the panel to itself, so its line is the
top row, in its usual colors, with `0` in its bullet's place to close the
drawer. In a terminal, the header steps aside too, which gives the drawer
another row before the panel scrolls, and the item's line takes the
header's place as the panel's top row: its subject, then its pull request
and age at the right, with nothing drawn between them, since the panel's
background already sets the drawer apart. The drawer is indented to start
under the item's text, so its detail and the digits of its lines sit under
the item's first letter:

```text
   0: Decline the finding about the retry budget?               #337  5m   [-]

      The review asks for a retry budget per host. The loop caps retries at
      three in total, which covers the case the finding names.

      1: Decline it: the total cap already bounds retries to one host
      (recommended)
      2: Add a budget of two retries per host, and keep the total cap

      7: discuss question
      8: dismiss question
      9: snooze question
                                                     ctrl+x tab to focus
```

The first option is always the agent's recommendation. Press an option by
clicking it, by Enter, or by its digit, and the panel puts the question and
that answer in your prompt, for you to send as it stands or change first:

```text
"Decline the finding about the retry budget?"

Add a budget of two retries per host, and keep the total cap
```

The drawer stays open until you send. Sending the prompt as the option
filled it in answers the question and closes its drawer, so the panel shows
its list again. The panel finds each quoted question whose quote ends its
line, and reads the text after the quote, up to the next quoted item or the
end of the prompt. When that text is one of the question's options, whatever
blank lines and spaces surround it, that option answers the question.

Anything else you send under a quoted question is a reply, not an answer:
your own words, an option with more text after it, or a question back. The
panel records nothing and closes the question's drawer, and the question
stays in the list. Your prompt carries the reply to the agent, which answers
what you asked and then settles the question. It withdraws the question if
your reply settles it, revises it if your reply shows it was unclear or the
choice has changed, or leaves it open while it still needs you. A revised
question keeps its place in the list, and the panel shows its new text as
soon as the agent revises it. An option that a drawer put in your prompt
before the revision answers no question, and neither does an option you
type under a quote that **discuss** put there before it: either one replies
to the revised question. To answer, choose again in the revised question's
drawer.

Each quote speaks to one question. When two open questions ask the same
thing, a quote that one's drawer put in your prompt, with an option or
alone through **discuss**, speaks to that question: one of its options
answers it, and anything else replies to it. When both drawers put the
quote there, the one you acted in last wins, unless it put a different
option there than the one you send. When that question is
answered, dismissed, or withdrawn before you send, the quote says nothing
at all, so it never answers the other question. A quote that no drawer put
there, such as one you type, goes to the older question that offers the
option, or else replies to the older one. A prompt that answers or replies
to one of them says nothing to the other and leaves its drawer alone.

Only a prompt you send, at the terminal or through Remote Control, answers
or replies to a question. Another session's message or a notification that
quotes one does neither. Your prompt is what carries the answer or the reply
to the main loop, and when a subagent asked, the main loop also reads a note
naming it, so it can pass the answer or the reply on.

The options and the actions form one list, the actions a line each under
the options, after a blank line. Each line's key is drawn in Claude Code's
accent color and the rest of the line in the default text color, which
Claude Code's own `: ` after the key always takes, so under the pointer or
the focus ring the line inverts as the key and one block after it. A
question's drawer has three actions:

- **discuss question** puts the quoted question in the prompt, followed
  by a blank line, and leaves the drawer open, so the options and detail
  stay in view while you type. Type your reply under the quote, or on the
  quote's own line, and send the prompt. The reply reaches the agent and
  answers nothing.
- **dismiss question** asks to be sure, as the paragraph after the next
  describes, then sets the question aside without an answer and closes the
  drawer.
- **snooze question** asks how long to put the question off, as the next
  paragraph but one describes.

A notice's drawer shows its detail, then the same three actions:
**discuss notice**, **dismiss notice**, and **snooze notice**. **discuss
notice** puts the quoted notice in the prompt. A prompt you send that quotes
a notice answers nothing, but it closes the notice's drawer; the notice
stays in the panel until you dismiss it. **dismiss notice** clears the
notice once you have read it and closes its drawer, and nothing goes back
to the agent.

**dismiss** dismisses nothing at once. It replaces the action lines with
a confirmation, so a stray `8` only asks:

```text
   8: yes, dismiss question
   9: no, keep it
```

Press `8` again, or click the first line, to dismiss; a notice's reads
`yes, dismiss notice`. `9`, closing the drawer, or opening another keeps
the item and puts the actions back. **restore** and **unsnooze** act at
once.

**snooze** snoozes nothing at once either. A question's options give way
to the lengths it offers, and the action lines to a way back:

```text
   1: 15 min
   2: 1 hour
   3: 4 hours
   4: 1 day
   9: cancel
```

Pick a length, and the panel runs
`project inbox snooze <number> --for <minutes>` with 15, 60, 240, or 1440
minutes, a day being the longest the CLI takes, and closes the drawer. A
notice, which has no options, shows the same lines. **cancel** puts the
options and the actions back, and so does closing the drawer or opening
another, so a drawer always opens on its actions.

A snoozed item leaves the header's counts at once, and stays in the list
for 30 seconds in the `subtle` color, not struck through, so it reads as put
off rather than dismissed. Then it leaves the panel. When its time is up, it
returns to the list, and the panel appears if you hid it. To wake an item
now, or to pick a length the panel does not offer, find the item's number
with `project inbox list` in a terminal, then run
`project inbox snooze <number> --for <minutes>`. An item you answer or
dismiss from a terminal while it is snoozed does not return.

A resolved item stays in the list, muted, for 30 seconds after it is
delivered, and a dismissed one is struck through. A dismissed notice is
delivered at once, since nothing goes back to the agent. An item the agent
withdrew leaves the panel at once, its drawer with it, and sends nothing back,
not even `DISMISSED` for a question. A question's drawer still opens and
lists its options, without their digits and with nothing to press, and
marks the answer given with `✓`, listing an answer typed in a terminal with
`project inbox answer --text` after the options.

While a dismissed or snoozed item is still listed, click its line to open
its drawer again. There, **restore question** or **restore notice** takes
the place of **dismiss** for a dismissed item, which has no **snooze**
line, since a resolved item cannot be snoozed. **unsnooze question** or
**unsnooze notice** takes the place of **snooze** for a snoozed one. Either
runs `project inbox restore`, and the item is open again at once, in its
place in the list, with its drawer still open and **dismiss** and
**snooze** back in their places. A dismissed question whose `DISMISSED`
has not gone out sends its agent nothing; one whose agent already heard
`DISMISSED` sends `RESTORED`. Once the item has left the panel, ask the
agent to restore it, and the panel appears with the item open, even if you
hid it. A drawer closes when its line leaves the panel, so a pull request
or an item that comes back returns with its drawer closed.

An answer you give from a terminal with `project inbox answer` reaches the
agent through the panel of the session that asked. A subagent that is still
running receives it directly, as a message that starts `ANSWER`,
`DISMISSED` for a dismissal, or `RESTORED` when you restore a question
whose agent heard `DISMISSED`. Otherwise the main loop receives it as a prompt
of its own once its turn ends, with the name of the subagent that asked, and
`sending` follows the question until it has gone.

Shown or hidden, the panel lists the session's inbox directory every 5
seconds and runs `project inbox` only when the directory changed since
its last successful read or an answer is on its way, so a session with an
empty inbox starts no process. A read that fails runs again 5 seconds later.

### Use the panel from the keyboard

Claude Code moves the keyboard between the prompt and the panel, and draws
a focus ring, an inverted highlight, on the panel's line or action that has
it:

- **ctrl+x tab** moves the keyboard into the panel, onto its first line.
  Press it again to move to the next.
- **Tab** and **→** move the ring to the next line or action, and
  **shift+Tab** and **←** to the one before. **↑** and **↓** scroll a panel
  taller than the room it has.
- **Enter** presses what the ring is on, as a click does.
- **Esc** gives the keyboard back to the prompt.
- **ctrl+x ctrl+a** collapses the panel to one line, and shows it again.

In a terminal, the panel's last line names the chord that takes the
keyboard in, and once the panel has it, Esc and Enter.

Enter on a line opens its drawer and moves the ring onto the drawer's first
control: a question's first option, otherwise **discuss**. Tab then walks
the options and the actions. Enter on the open line closes the drawer and
leaves the ring on the line. A click opens and closes a drawer without
moving the ring. In the same way, Enter on the **snooze** line moves the
ring onto the first length, and Enter on **cancel** moves it back onto the
**snooze** line. Enter on **dismiss** moves it onto the confirmation, and
Enter on `no, keep it` back onto **dismiss**.

An open question's options read `1:` to `6:`, the digit in the accent
color. Press a digit while the panel has the keyboard, or type it first into
an empty prompt while the option is wholly in view in the panel, as you
answer Claude Code's own surveys, and the panel puts that option in your
prompt as a click does. A digit only fills the prompt,
so nothing reaches the agent until you send. A resolved question's options
have no digits.

An open item's own line, at the top of its drawer, reads `0:` before it,
for every item, open or resolved, question or notice. Press `0` the same
two ways, while the panel has the keyboard or first into an empty prompt
while the line is wholly in view in the panel, and the drawer closes, as a
click or Enter on that line closes it. A pull
request's drawer has no `0`, since its number closes it.

A drawer's actions follow its options, a line each, with the digit that
presses it before the label in the accent color, such as
`7: discuss question`. The digits count down from 9, so the options keep
`1` to `6` and the item's line `0`. Each label ends with what the action
acts on: `question`, `notice`, or `PR`.

- **7** presses **discuss**.
- **8** presses **dismiss**, or **restore** in its place. Dismiss asks to
  be sure, on `8` again to dismiss and `9` to keep the item, so dismissing
  from the keyboard is `8` `8`.
- **9** presses **snooze**, or **unsnooze** in its place, which a pull
  request's drawer does not offer. While snooze asks how long, the lengths
  take `1` to `4` in the options' place and `9` cancels.

An action's digit works the two ways an option's does: while the panel has
the keyboard, or first into an empty prompt while its line is wholly in
view in the panel.

## Install for Codex

Add the same repository as a Codex marketplace and install the plugin:

```sh
codex plugin marketplace add ninjudd/projector
codex plugin add projector@projector
codex plugin add gh-stack@projector
```

Invoke a skill directly, for example:

```text
$spec a safer deploy workflow
```

The Codex manifest exposes the same `skills/` path as Claude Code. It adds only
install-surface metadata; it does not wrap or rewrite skill instructions.

Codex has no plugin dependencies, so `gh-stack@projector` is a plugin of its
own there, and the last command above installs it. Projector's skills work
without it. To update both, run `codex plugin marketplace upgrade projector`
and then both `codex plugin add` commands again, since `add` installs a
plugin again from the marketplace when it is already there.

## Reach every session through the repository

A skill's instructions load only while the skill is invoked, and neither host
loads a plugin's own `CLAUDE.md` or `AGENTS.md` into other repositories. So the
plugin is not how Projector's conventions reach a session that never invokes a
skill. `project init` is: it writes a marked section into the repository's
`AGENTS.md`, which Codex reads directly, and links it as `CLAUDE.md`, which
Claude Code reads. The text lives in the repository, so every
collaborator gets it whether or not they installed the plugin, and `project
check` warns when it drifts from the template the CLI ships. See
[the CLI guide](cli.md#adopt-a-repository).

## Install from a checkout

Run the installer to add the repository this checkout was cloned from as both
hosts' marketplace, install its plugin, and install the CLI with `pipx` from
the checkout:

```sh
./install.sh all
```

Select one component with `cli`, `claude`, or `codex`. The host-only commands
do not install the CLI, so install it separately before running a project
workflow. `all` installs each host CLI it finds, skips a missing host, and exits
69 only when neither Claude Code nor Codex is installed.

The marketplace source is the checkout's `origin` remote, so a fork installs
from the fork. A GitHub remote becomes `owner/repo` for Claude Code and the
HTTPS URL for Codex; any other remote URL is handed to both hosts as it is;
a checkout with no `origin` installs from itself. The plugin reads the
repository rather than the checkout because a host refreshes a marketplace
from its source: one that reads a checkout installs whatever commit is
checked out and then reports itself current until someone pulls, while one
that reads the repository moves with every release.

The same command upgrades what it installed. A marketplace that reads a local
path is moved to the repository, removed and added again from that source,
and a `moved` row names the old path and the new source. One that already
reads a remote is refreshed from wherever it points rather than added again.
Codex's Git clone of a marketplace can time out on a slow connection, so the
installer runs a failed Codex `marketplace add` or `marketplace upgrade` of a
remote source once more after five seconds, with a `retrying` row, and stops
with Codex's exit status only when the second try fails too. A source that is
a local directory clones nothing, so its failure stops the installer at once.
An installed plugin is updated rather than installed again, because a host
asked to install a plugin it already has leaves it as it is; the update is
what moves it. Claude Code applies the update on its next start. A plugin
moves only when its manifest version does, because a host caches a plugin by
that version.

`pipx` installs a copy of the source rather than a link to your checkout, so
pulling new commits does not update the command. Ask which one you have:

```sh
./install.sh status
```

`cli-current` reports a command whose installed source matches the checkout.
`cli-stale` reports one that differs, or one too old to say where its source
lives, and tells you to run `./install.sh cli`. The comparison is of the files
themselves, so it holds whether or not anyone remembered to bump a version.

`marketplace` names each host's source for the `projector` marketplace, which
is where an upgrade refreshes from. `from-checkout` follows it, with a ⚠️
marker, when that source is a local path and the checkout names a repository
to move it to: the plugin goes stale with the checkout until `./install.sh
<host>` moves it. `plugin-current` reports an installed plugin at the
checkout's manifest version, `plugin-stale` one at another version, and
`plugin-absent` a host with none.

The first row is about the checkout itself. The installer fetches the current
branch's upstream and prints `repo-current` when the checkout matches it,
`repo-behind` with the commit count and a `git pull` hint when it does not,
and `repo-untracked` when a detached `HEAD` or a branch without an upstream
leaves nothing to compare against. `repo-behind` carries a ⚠️ marker and
prints in bold yellow on a terminal, because the command is built from the
checkout while a GitHub marketplace serves the plugin, so a checkout nobody
pulled installs an old command beside a current plugin and the two version
numbers look like a bug. Every install target prints the same row last. Set
`PROJECTOR_OFFLINE=1` to skip the fetch and compare against the last one; a
fetch that fails does the same and says so in the row. Set `NO_COLOR` to keep
the row plain.

`project upgrade <target>` runs this installer from any directory, for a
command installed from this checkout: `project upgrade cli` is
`./install.sh cli`, and `project upgrade status` is `./install.sh status`.

A reinstall keeps the Python the existing venv was created with. Left to
itself, `pipx` builds under its default interpreter, which on some machines is
older than Projector's 3.11 floor and fails the reinstall even though the venv
already holds a Python that works.

## Upgrade from agent-config

The old installer linked whole configuration and skill directories into each
host. Projector installs a named plugin instead. During an upgrade,
`install.sh` removes a legacy link only when its target exactly matches this
checkout's old source path. It leaves regular files, other symlinks, settings,
credentials, personal instructions, and unrelated plugins untouched.

Inspect the upgrade before applying it:

```sh
./install.sh status
./install.sh claude
./install.sh codex
```

The host plugin managers own installed copies and future updates after the
migration.

## Release an update

Projector has one version. `pyproject.toml` names it for the CLI, and
`.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` name it for the
plugin, and a packaging test fails when the three disagree. One number means
one answer to "what am I running," and it lets a skill rely on the CLI that
shipped beside it.

The plugin version is what a release delivers: a host caches an installed
plugin in a directory named by that string, so an update that finds an
unchanged version resolves to the copy already on disk and installs nothing.
Claude Code records the path it used, which you can read back:

```sh
jq '.plugins["projector@projector"]' ~/.claude/plugins/installed_plugins.json
```

A release is a deliberate decision to ship what has merged, and only a
release changes the version. Other pull requests leave it alone, even when
they change skills or the CLI, so everything merged since the last tag ships
together in the next release rather than each change spending a number that
is never tagged.

Release from GitHub with the Release workflow. Start it by hand and choose
which part of the version to raise:

```sh
gh workflow run release.yml --repo ninjudd/projector -f bump=patch
```

`bump` is `patch`, `minor`, or `major`, and `patch` is the default in the
Actions tab. The workflow writes the next version to all three files on a
`release/vX.Y.Z` branch and opens a pull request for it. Merging that pull
request is the decision to ship: the same workflow runs on the merge, tags
the merged version, moves the major tag, and creates the GitHub release. The
workflow opens the pull request with its own token, which needs **Allow
GitHub Actions to create and approve pull requests** turned on under the
repository's **Settings > Actions > General**.

GitHub starts no workflow for a pull request that a workflow's own token
opened, so the Check workflow would never run on the release pull request by
itself. The Release workflow dispatches Check on the release branch instead,
both when it opens the pull request and when a rerun finds one already open,
because `workflow_dispatch` is the one event that token may trigger. The
dispatched run's checks land on the branch's commit, which is the pull
request's head, so they show on the release pull request like any other
checks. That is why `.github/workflows/check.yml` lists `workflow_dispatch`
among its triggers and the Release workflow's propose job has `actions: write`.

The workflow runs `.github/scripts/release.py`, which also works by hand
from any checkout. `bump` raises one part of the version and `set` writes an
exact one, both refusing a version that does not go up:

```sh
.github/scripts/release.py bump minor
.github/scripts/release.py set 0.6.0
```

Once the bump merges, `tag` releases it. A version counts as released when
its `v0.6.0` tag and the major tag, `v0`, name the same commit and its GitHub
release exists. `released` exits 0 when all three are in place and 3 when any
is missing, which is how the workflow decides, on every `pyproject.toml`
change, whether there is anything to release:

```sh
.github/scripts/release.py released
.github/scripts/release.py tag
```

`tag` reads the version from `origin/main`, not from the checkout, so an
unmerged bump cannot name a release. It does only the steps the remote still
lacks: it pushes an immutable `v0.6.0` tag, force-moves `v0` to the same
commit, and creates the GitHub release for `v0.6.0` with generated notes. It
prints what it did and what it found already done. `released` needs an
authenticated `gh`, because it looks for the GitHub release, and so does `tag`
unless you pass `--no-release`, which pushes only the tags and never calls
`gh`.

When a release stops partway, for example because GitHub rejects the push of
`v0`, re-run the failed Release workflow job. The job's `tag` finds the steps
that already happened and does the rest, and then the job asks the site
workflow to rebuild with the released action. You can instead run `tag` again
from a checkout. That finishes the tags and the GitHub release, but only the
job asks for the rebuild, so the site stays as the previous action built it
until you send the request yourself:

```sh
gh api repos/ninjudd/projector/dispatches -f event_type=projector-summaries
```

Once `v0.6.0` exists, its commit is the release's commit, so `tag` moves `v0`
there even after `main` has moved past it. Both commands exit 1 rather than
move a tag when `v0.6.0` cannot belong to this release: when `main` does not
contain the tagged commit, or when `main` named a different version at any
point between that commit and its head. Set a new version instead.

The immutable tag names an exact release; the major tag
is the channel that follows compatible releases, the convention GitHub's own
actions use. A breaking change starts the next major tag. Under `0.x` a minor
release may still break, so the `v0` channel promises less than a `v1` channel
will.

Update an installed copy with each host's own command, or run `./install.sh
all` from a checkout, which runs these for every host it finds. Claude Code
needs a restart to apply the update, and Codex refreshes a Git marketplace
snapshot before it can see the new version:

```sh
claude plugin update projector@projector
claude plugin update gh-stack@projector
codex plugin marketplace upgrade
codex plugin add projector@projector
codex plugin add gh-stack@projector
```

A release that first ships the `gh-stack` dependency leaves Projector unable
to load in Claude Code for anyone who updates only `projector@projector`,
because `update` does not install the new dependency. Run
`claude plugin install gh-stack@projector --scope user` once, or
`project upgrade`, which does it for you.

Taking a newer upstream `gh-stack` is a deliberate change of its own: set
the same `ref`, `sha`, and `version` on the `gh-stack` entry in both
marketplace files, and read upstream's new skill against the rules in
`implement`'s "Stack dependent work" section.

## Work without MCP

Projector currently ships no MCP server. The `ask`, `tell`, `withdraw`,
`revise`, and `restore` tools carry the `mcp__projector__` prefix, but
Projector's hooks module in Claude Code serves them: no MCP server runs, and
no skill depends on them. Every core skill uses the public `project` CLI and
ordinary Git or GitHub commands. A future MCP adapter can expose the same
versioned project operations without becoming the package that contains the
skills.
