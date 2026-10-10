---
status: ready
priority: next
---

# Head each stack with one project or a title

Every stack of pull requests on the site, a pull request alone included, shows
a header above its rows for each project it carries out, linked to that
project's page, or one short title in plain text when it carries none (§ 8).

## 1. Problem

The site's Reviews index lists the pull requests that have summaries, and
lists the pull requests of a stack together, bottom layer first. A summary
page's sidebar lists the same stack, one row per pull request. Neither says
what the stack as a whole is for. A reader sees each layer's title and has to
work out what the layers add up to.

The site does link reviews to projects, but one pull request at a time, and
to as many projects as it finds. `project_linker` in
`src/projector/site/__init__.py` gives a pull request every project whose
folder holds a file its diff changes, plus any its summary names in a
`projects` list that no skill writes or documents. The sidebar draws one row
per such project above the stack, and each project's page lists the reviews
linked to it. The index shows no project at all. The rule fails in both
directions:

- **Too many projects.** A change that edits plans in passing links to each
  plan it edits. In this repository, the pull request that renamed the design
  skill to `spec` links to two unrelated projects.
- **Too few projects.** The layers of a stack that implement a plan without
  editing it link to nothing. Only the layer that changes the plan's status
  shows the project.

A pull request with no project, which is most of them, has nothing above its
stack at all. Of the 22 pull requests whose summaries this repository's site
builds, 19 link to no project.

A header for a stack also needs to know which pull requests the stack holds,
and the site works that out from base branches alone. `stack_bases` treats a
pull request as stacked when the base branch its newest summary records is
another pull request's head branch. GitHub records stacks itself, and its
record is more complete. It keeps a stack's pull requests after they merge.
It also keeps a layer whose base has since moved to the default branch, which
base branches cannot show. In this repository, the top layer of one stack
targets `main`, so base branches would list it alone.

## 2. Solution

Each summary records the one project its stack carries out, or none, and a
short title for the stack. The site build takes each stack's pull requests
and their order from GitHub's stacks. For a pull request that GitHub puts in
no stack, and for every pull request when GitHub cannot answer, it falls back
to base branches, as it does today, and never fails. It splits each stack
into segments, one for each run of layers that carry the same project, and
gives each segment a header (§ 8). A stack whose layers name no project has
one title header. The Reviews index draws each header above its segment's
rows, and a summary page's sidebar draws each above its segment's part of the
stack list, in place of the project rows. A project header links to the
project's page. A title header is plain text. A merged stack stays one stack.
A summary published before the change gets its header by a fixed rule from
the fields it already has, so nothing needs to be published again.

### 2.1 A summary records one project and a title

`summary.json` gains a `project` field, and its `name` field becomes the
stack's title:

| Field | Value | Meaning |
| --- | --- | --- |
| `project` | A project's canonical name, such as `"stack-header"` or `"payments/invoices"` | The one project this pull request carries out as a layer of its stack: the plan it adds, specs, implements, or completes. Other layers of the stack can carry other projects (§ 8). |
| `project` | `""` | This pull request names no project. It sits under the header of the member before it in the stack's order (§ 2.2), or under the first header when it is the bottom member (§ 8.1). The stack has a title header when no layer names one. |
| `project` | `null`, or no `project` key | Not decided yet. `init` writes `null`, and `publish` refuses it. The build reads a summary published before the field existed by the rule in § 2.3, step 2. |
| `name` | A short title, at most 60 characters | The title of the work the stack does, or the work of the pull request alone. The site shows it as the stack's header when no layer of the stack names a project. |

`summarize-pr` fills both fields in step 4 of "Build the summary". Each
summary records its own choices, and copies no other layer's:

1. **Choose the project the change is about.** List the candidates with
   `project list --json`, and look at the plan files the diff changes. The
   pull request's body and branch often name the plan. When the change touches
   several plans, choose the plan it adds or the plan whose status it changes.
   A plan touched only in passing, such as by a link fix or a rename across
   every plan, is not the change's project. For a pull request in a stack,
   choose the project this layer carries out, which it may not edit. That is
   usually the project of the layer beneath. A layer that starts the work of
   another plan records that plan, and the site starts a new header there
   (§ 8). `gh api repos/OWNER/NAME/stacks?pull_request=NUMBER` lists the
   stack's pull requests on GitHub, and the summaries ref holds what each
   summarized layer chose. When that request answers 404 or fails, follow the
   summary's `pr.basePr` down the stack instead, as the build falls back to
   base branches. When no single plan is the subject, record `""`.
2. **Name the whole stack.** Write two to six words in sentence case that name
   the work, such as `Stack header plan` or `Summary publish retry`. Do not
   write a sentence, and do not copy the pull request's title. When no layer
   names a project, the site shows the lowest layer's `name`, so on the
   bottom layer of a stack, name the work of the whole stack as far as you
   know it. To change a stack's title, publish the bottom layer's summary
   again.

An update makes the same two choices. `review-pr`, and the skill's "Update
the page when the pull request moves" section, start from the newest summary
on the summaries ref. A summary published before this change has no
`project`, so step 2 of that section sets `project` and checks `name` by the
steps above before the summary is published again. Without that step, the
first republish of every open pull request would meet the refusal below.

`publish` refuses a summary whose `project` is `null`, missing, or not a
string, or whose `name` is empty or longer than `NAME_MAX`, which is 60
characters. The message names the field and says what to set. `publish` does
not check that `project` names a plan the checkout has, because a spec pull
request records the plan it adds, and the default branch the site builds from
lacks that plan until the pull request merges. `project site page` skips
these checks, so you can preview a summary in progress as often as you like.

### 2.2 The build takes each stack from GitHub, and falls back to branches

GitHub records stacked pull requests. The `gh stack` CLI creates them, and the
repository's stacks API lists each stack with a number and its pull requests
from the bottom up. A stack keeps its pull requests, and their order, after
they merge.

Where GitHub has no stack to give, the build reads base branches, as it does
today: a pull request sits on the pull request whose head branch is its base
branch. Stacks never fail the build. The build reads the stacks once, and
works out every stack's members once, for the index, the sidebar, and the
header:

1. `build_checkout` in `src/projector/cli.py` passes `build_site` a new
   `stacks_lookup`, beside `lookup` and `status_lookup`. It calls a new
   `summary.repo_stacks`, which makes one paginated request,
   `GET repos/{owner}/{repo}/stacks?per_page=100`, with the
   `X-GitHub-Api-Version: 2026-03-10` header that the REST reference names.
   Like `pr_status`, the lookup returns None when GitHub cannot answer. That
   covers a 404, which a host without the endpoint or a token without access
   gets, a network error, an answer that is not a list, any other failure,
   and a build with no `gh` or no repository. The listing is one paginated
   request, so GitHub answers for every pull request in a build or for none.
   A failure on any page leaves the lookup with None.
2. A new `find_stacks` in `src/projector/site/__init__.py` builds one map,
   from each pull request to the one beneath it, and walks it as `stack_rows`
   does today:
   - **Within a GitHub stack, GitHub decides.** Each member sits on the
     member before it in GitHub's order, merged and closed members included.
     The bottom member sits on nothing, because GitHub roots the stack on its
     base branch. The base branch a member's summary records does not count,
     even when it names another pull request.
   - **Everywhere else, base branches decide.** Every pull request in no
     GitHub stack, and every pull request when the lookup returns None, sits
     on the pull request whose head branch is its base branch. `stack_bases`
     finds it as it does today, with the same `lookup` call for a branch that
     no summary names. A pull request chained by hand onto a member of a
     GitHub stack therefore joins that stack. Each pull request sits on one
     other at most, so no base branch joins two GitHub stacks.
   - **The walk keeps GitHub's order.** From each pull request, the walk
     visits its successor in its own GitHub stack first, then its other
     branches, lowest number first. A GitHub stack's members stay together in
     GitHub's order, and a pull request joined by branch comes after them.
   - **A member without a summary stays in the stack**, as a pull request
     known only as a base does today.
3. `stack_groups`, which orders the Reviews index, and `stack_rows`, which
   lists the sidebar's stack, both read the members that `find_stacks`
   returns, instead of working membership out themselves.

The build depends on as little of the API as it can, because the API is new.
GitHub's guide to stacked pull requests, at gh.io/stacks, covers the
`gh stack` CLI. The REST reference, under `pulls/stacks`, documents the
endpoints, and marks neither as a preview. The build reads only each stack's
`number` and each member's `number`, in the order `pull_requests` lists them.
The REST reference says that a stack is created from pull requests listed
bottom to top, but not how a response orders them. Every stack in this
repository comes back bottom first.

- **A missing field.** A stack without an integer `number` or a
  `pull_requests` list is left out, and so is a member without an integer
  `number`. Their pull requests fall back to base branches.
- **A pull request in two stacks.** It belongs to the stack with the higher
  number. The lower stack drops it and closes the gap: the member after it
  sits on the member before it, or on nothing when it was the bottom. The two
  stacks stay separate.
- **A lookup that fails for one branch.** The `lookup` for a base branch can
  fail while others answer, as it can today. The pull request it was asked for
  then sits on nothing, and the rest of the build is unchanged.

**One note per build.** The build prints one plain line about stacks, never
a `::warning::` annotation and never a line per pull request. A pull request
in no GitHub stack is the normal case and adds nothing to the line:

```text
stacks: read 16 GitHub stacks in 1 request
stacks: read 16 GitHub stacks in 1 request; left out 1 stack with no number, whose pull requests sit on their base branches
stacks: GitHub's stacks could not be read (HTTP 404), so every pull request sits on its base branch
```

A GitHub stack's number is its key while the build groups it. The index,
every sidebar, and the header take their members from the same stack, so a
merged stack shows as one group under **Closed**, and in each of its pull
requests' sidebars, in GitHub's order.

### 2.3 The build decides each stack's headers

`build_summaries` in `src/projector/site/__init__.py` decides each stack's
headers before it writes any page:

1. It reads each pull request's newest summary, as it does today, and takes
   each stack's members, bottom first, from § 2.2. The members with a summary
   on the site, in that order, are the stack's group.
2. It resolves each pull request's own project from its newest summary,
   against the projects the site has a page for:
   - A `project` string names that project when the site has it, and no
     project otherwise. `""` names no project.
   - A summary whose `project` is `null` or missing takes the first name in
     its `projects` list that the site has. Without one, it takes the project
     whose folder holds the files its diff changes, when exactly one project
     does. The deepest project owns each file, as `project_linker` already
     assigns it. Otherwise the summary has no project.
3. For each stack, `stack_segments` takes every member that `find_stacks`
   gives, members without a summary included, rather than the group. It
   splits them into segments in that order, as § 8.1 describes, and gives
   each segment its header:
   - A segment of members that carry a project gives
     `{"project": <name>, "title": <the plan's title>, "url": "<base>projects/<name>/"}`.
   - A stack whose members carry no project is one segment. The first member
     with a non-empty `name` gives it `{"title": <name>}`.
   - Without a `name` either, the pull request title of the first member with
     a summary gives `{"title": <pr.title>}`. The group holds at least that
     member, and `validate` requires its title, so every segment has a
     header.
4. Layers that name different projects are normal, and each starts its own
   segment (§ 8), so the build prints nothing about them.
5. It writes into the data of every page of every pull request in the group,
   every head included, the stack's `segments` and the `header` of the
   segment that the page's pull request is in (§ 8.2). Each entry carries
   that `header` too, and `review_row` copies it into the pull request's row
   of `site.json`.
6. `build_site` lists, on each project's page, the reviews whose header is
   that project, newest first.

A pull request that stands alone is a group of one, so its one segment and
its header come from its own summary by the same steps. `build_page`, which
`project site page` uses to build one summary with no site around it, gives
that page one segment with the header of a site with no projects:
`{"title": <name>}`, or the pull request's title when `name` is empty.

A page's `data.json`, a row of `site.json`, and each of a page's `segments`
carry a header as one of these:

```json
"header": {"project": "stack-header", "title": "Head each stack with one project or a title", "url": "/projects/stack-header/"}
```

```json
"header": {"title": "Fix loop stack subagent name"}
```

| Field | Present when | Value |
| --- | --- | --- |
| `title` | Always | The project's title, from its plan's first heading, or the stack's title |
| `project` | The segment has a project | The project's canonical name |
| `url` | The segment has a project | The project's page, under the site's base path |

### 2.4 The index and the sidebar draw the header

The site's pages load `summary.js` for the helpers at its top, such as `esc`
and `statusHtml`. A new helper there, `stackHeaderHtml`, draws the content of
one segment's header for both pages. For a project it returns an `<a href>`
to `url` around the escaped title. Otherwise it returns a `<span>` around the
escaped title.

The examples in this section show stacks of one segment, which is every
stack whose layers carry one project or none. A stack with several segments
draws a header above each, as § 8.3 shows.

**Reviews index.** `reviewsTable` in `site/src/site.ts` starts each stack's
table body with a header row, for a pull request alone too:

```html
<tbody>
  <tr class="stackhead"><th colspan="3" scope="rowgroup"><a href="/projects/stack-header/">Head each stack with one project or a title</a></th></tr>
  <tr>…#301…</tr>
  …
</tbody>
```

The header cell spans all three columns. It starts at the left edge of the
first column and has no divider beneath it, so it reads as the title of the
rows below. Each row keeps its pull request's number and title, and the
divider below each stack stays where it is:

```text
 Pull request                                            Status              Updated
 ─────────────────────────────────────────────────────────────────────────────────────
 Head each stack with one project or a title             ← link to the project's page
 #301  Plan one header per stack                         Clean               Oct 10, 2:14 PM
 #302  Record each summary's project                     Unreviewed          Oct 11, 9:02 AM
 #303  Draw the stack header                             Changes requested   Oct 11, 4:40 PM
 ─────────────────────────────────────────────────────────────────────────────────────
 Fix loop stack subagent name                            ← plain text
 #298  Name the fix loop's subagent after its stack      Clean               Oct 9, 5:30 PM
 ─────────────────────────────────────────────────────────────────────────────────────
```

With **Closed** checked, a merged stack is one group in GitHub's order. This
is this repository's stack of #196 and #197, whose summaries name no project,
under the `name` of its lowest layer:

```text
 Stack List in Summary Sidebar                                                ← plain text
 #196  List the pull requests in a summary's stack in its sidebar              Merged   …
 #197  Add a Files changed pane to a summary page and simplify its header …    Merged   …
 ─────────────────────────────────────────────────────────────────────────────────────
```

The header row takes the top padding that each stack's first row has today.
`site.css` gives that padding with
`table.tbl.reviews tbody tr:first-child td { padding-top: 6px; }`, and the
header row becomes every body's first row, with a `th` rather than a `td`. So
`table.tbl.reviews tr.stackhead th` gets the 6 pixels of top padding, and the
`tr:first-child td` rule goes. The first pull request's row then sits directly
under the header, as each row sits under the one above it.

When the **Open** and **Closed** boxes hide some of a stack's pull requests,
the header stays above the rest. When they hide all of them, they hide the
header too.

**Summary sidebar.** `renderPage` in `site/src/summary.ts` replaces
`projectsList`, which draws a `<ul class="stack projects">` with one
`<a class="srow">` row per project, with one header above the stack list:

```html
<div class="prblock">
  <div class="stackhead"><a href="/projects/stack-header/">Head each stack with one project or a title</a></div>
  <ul class="stack" aria-label="Pull requests in this stack">…</ul>
</div>
```

The header keeps the look of today's project row: 13.5-pixel text at weight
600, a row's padding, pulled up toward the box's top edge. A project header
has the row's hover background. A title header has the same text with no
hover state. Like a stack row's title, a header shows at most three lines.
The stack list below it is the stack's members from § 2.2, so a merged pull
request's sidebar still lists every layer of its stack.

```text
 ┌──────────────────────────────────┐
 │ Head each stack with one project │  ← link to the project's page
 │ or a title                       │
 │ #301  Plan one header per stack  │
 │ #302  Record each summary's      │  ← this page, highlighted
 │       project                    │
 │ #303  Draw the stack header      │
 │                                  │
 │ Overview  Files                  │
 │ ──────────────────────────────── │
 │ 1  The summary records a project │
 │ …                                │
 └──────────────────────────────────┘

 ┌──────────────────────────────────┐
 │ Fix loop stack subagent name     │  ← plain text
 │ #298  Name the fix loop's        │  ← this page, highlighted
 │       subagent after its stack   │
 │                                  │
 │ Overview  Files                  │
 │ …                                │
 └──────────────────────────────────┘
```

### 2.5 What reads `projects` and `name` after the change

| Reader | Today | After the change |
| --- | --- | --- |
| Summary page sidebar | One `.stack.projects` row per entry in the page data's `projects` | One header per segment, from `segments` (§ 8.2) |
| Reviews index | Each row's pull request number and title, with no project and no `name` | Unchanged rows, under a header row for each segment, from each row's `header` (§ 8.2) |
| Project page's list of reviews | The reviews whose `projects` include the project, each as `#N` and its `name`, or its title when `name` is empty | The reviews whose `header` is the project, which are the members of the segments the project heads, each as `#N` and the pull request's title |
| Page `<title>` and the summary's `h1` | The pull request's title, falling back to `name` | The pull request's title. `validate` requires it, so the fallback never runs, and the change removes it from `pr_title` and `renderPage`. |
| A file card's **View project** link | The deepest project whose folder holds the file | Unchanged. It says where the file lives, not what the stack is for. |
| Search | Indexes the docs and the project files | Unchanged. It reads neither field. |
| `prepare_page` | Sets an empty `name` to `<repo>#<n> summary` | Passes `name` through as written, so § 2.3, step 3 can tell an empty name from a set one |

The change removes the page data's `projects` list, the `projects` list that
`build_summaries` keeps on each entry for `build_site`, the `name` field of a
`site.json` row, the `projectsList` function, and the `.prblock .stack.projects`
rules in `site/assets/summary.css`. The build still reads a summary's
`projects` list, but only in § 2.3, step 2, for a summary with no `project`.

### 2.6 Files that change

| File | Change |
| --- | --- |
| `src/projector/summary.py` | `repo_stacks` lists the repository's stacks. `init` writes `"project": null`. `publish` checks `project` and `name` against `NAME_MAX` and says what to set. `prepare_page` stops setting a default `name`. |
| `src/projector/cli.py` | `build_checkout` passes `stacks_lookup`, which returns None when `repo_stacks` fails. |
| `src/projector/site/__init__.py` | `build_site` and `build_summaries` take `stacks_lookup`. `find_stacks` works out each stack's members once, and `stack_groups` and `stack_rows` read them. `project_linker` returns a summary's own project. A new `stack_segments` takes every member of a stack from `find_stacks`, members without a summary included, and returns its segments, each with its header (§ 8.2). `build_summaries` computes the groups before it writes pages, writes `segments` and `header` in place of each entry's `projects`, and prints the note about stacks. `review_row` writes `header` and drops `name`. `build_site` lists each project's reviews by header. `pr_title` drops its `name` fallback. `build_page` gives a standalone page its header. |
| `site/src/summary.ts` | `stackHeaderHtml` draws one segment's header, and `stackSegmentsHtml` draws the sidebar's stack, a header above each segment's rows (§ 8.2). They replace `projectsList`. The page title drops its `name` fallback. |
| `site/src/site.ts` | `reviewsTable` draws each segment as a table body that starts with its header row (§ 8.3). A project page lists its reviews by pull request title. |
| `site/src/globals.d.ts` | `StackHeader` and `StackSegment` types. `segments` and `header` replace `projects` in `SummaryData`, and `header` replaces `name` in `SiteReview`. |
| `site/assets/summary.css`, `site/assets/site.css` | `.prblock .stackhead` replaces the `.prblock .stack.projects` rules, with room above every header but the first. A `table.tbl.reviews tr.stackhead th` rule overrides the head row's small capitals, takes the body's top padding in place of the `tbody tr:first-child td` rule, and adds no divider. A segment's body that the stack continues below draws no divider (§ 8.3). |
| `site/assets/summary.js`, `site/assets/site.js` | Rebuilt with `npm run build` in `site/`. |
| `skills/summarize-pr/SKILL.md`, `skills/summarize-pr/format.md` | The `project` field, the title meaning of `name`, and the two choices in § 2.1, in both "Build the summary" and step 2 of "Update the page when the pull request moves". |
| `docs/cli.md` | Stacks from GitHub and the fallback to base branches, the Reviews index, the sidebar, and the project pages, as § 2.2, § 2.4, and § 2.5 describe them. |
| `tests/test_summary.py`, `tests/test_site.py` | The tests in § 4. |

## 3. Why this design

- **The summary records the project, and the build only checks it.** Which
  plan a change carries out is a judgment. The summarizing agent already
  makes it when it reads the plan, the pull request's body, and the diff. The
  diff alone cannot make it, because a stack's upper layers implement a plan
  without editing it, and a rename edits plans it is not about. Rejected:
  deriving every pull request's project from its diff, which is today's rule
  and the source of both failures in § 1.
- **GitHub's stack decides membership, and base branches cover the rest.**
  GitHub holds the stack the author made, keeps it whole after a merge, keeps
  a layer whose base has moved to the default branch, and gives each stack a
  number. Base branches stay for a repository or a host without stacks, and
  for pull requests stacked by hand. Where the two disagree about a GitHub
  stack's member, GitHub wins, because its record is the one the author made.
  A pull request chained by hand onto a GitHub stack joins it, because a
  reader of the pull request on GitHub sees it on that stack's branch.
  Rejected:
  - Base branches alone, which lose a layer once its base moves, and which
    depend on when each layer was last summarized.
  - Keeping a pull request chained by hand out of the GitHub stack it builds
    on. The site would then split what the branches on GitHub join.
  - One request per pull request with `?pull_request=`. A pull request in no
    stack needs its own request to learn that, so this repository would make
    21 requests where the list makes one. A per-pull-request answer can also
    fail for some pull requests and not others, which one listing cannot.
  - Failing the build, or warning per pull request, when GitHub cannot
    answer. The site has to build on every host Projector supports, and a
    repository without stacks is normal, so one plain note says what
    happened.
- **Each layer records its own choice, and the build reads every layer.**
  GitHub's stack keeps every layer, merged ones included, so every build reads
  every layer's summary. No layer has to copy another to keep the headers
  steady through a merge. Each run of layers that carry one project gets its
  own header, so layers that name different projects need no reconciling
  (§ 8). Nothing overwrites a layer's project with another layer's `""`, so a
  refactor at the bottom of a stack never hides the project above it.
  Rejected:
  - Copying the layer beneath when a summary is written. It keeps a stack
    found from base branches steady after its bottom merges, but GitHub's
    stack already does that, and a stack found from base branches can take
    its header from the layers it still has. The copy rule also has to except
    `""`, or a refactor at the bottom erases the project.
  - A record per stack, such as `summaries/stacks/<number>.json`, keyed by the
    GitHub stack's number. The number is stable, but the record would be a
    second file for `publish` to write, and every layer's publish would race
    to write it. A stack found from base branches has no number, so the
    record would cover only some stacks. Reading the members' summaries
    covers both kinds.
  - One project for the whole stack, whichever layer chooses it. § 8.4 says
    why.
- **A layer with no project does not hide one above it.** A stack often opens
  with a refactor that the plan's work needs but the plan never names. A
  member with no project joins a neighboring segment rather than heading one
  (§ 8.1), so that stack opens under its project's header. Treating a project
  the site lacks as no project does the same for a plan that a pull request
  adds before it merges.
- **`name` becomes the title, rather than a new field.** Every published
  summary already has a short `name`, and the skill already writes one, so a
  summary published before the change has a title without being published
  again. Rejected: a new `title` field, which every older summary lacks and
  which reads like `pr.title`.
- **`null` means undecided, and `publish` refuses it.** If the skeleton wrote
  `""`, an agent that skipped the choice would publish "no project", and
  nothing would show the mistake. Refusing `null` makes every new publish
  state its choice. The build still reads an older summary, which has no
  `project` key, by the fixed rule in § 2.3.
- **The migration rule keeps today's answer where today's answer is one
  project.** A summary with a `projects` list keeps its first known name, and
  one whose diff changes exactly one project's files keeps that project. One
  whose diff changes files in several projects gets its title, because
  today's rule cannot say which project the change is about.
- **A cap at `publish`, not truncation on the page.** Truncation cuts the words
  that tell two stacks apart. Refusing at `publish` makes the agent shorten
  the title while it still has the context. The cap is 60 characters because
  the longest of this repository's 26 distinct names is 49. The three-line
  clamp covers a summary that never passed the check.
- **One helper draws both headers.** `stackHeaderHtml` sits with `esc` and
  `statusHtml`, which the site's pages already load from `summary.js`. The
  index and the sidebar draw the same header from the same data and escape it
  the same way.
- **The index, the sidebar, and the header read one set of stacks.**
  `find_stacks` works out membership once, and `stack_groups`, `stack_rows`,
  and the header all read it. The index's grouping, a page's stack list, and
  the header's members cannot drift apart.

## 4. Acceptance criteria

Build tests in `tests/test_site.py` give the build summaries in a temporary
checkout with the projects `alpha` and `alpha/beta`, as the existing tests do.
A test's `stacks_lookup` returns the stacks it lists, or None.

Stacks, where GitHub answers:

| Do | Expect |
| --- | --- |
| Build with a GitHub stack of #9 and #10, where #10's summary records `baseRef` `main` | #9 and #10 share one group on the index and one stack list in each sidebar, in GitHub's order |
| Build with a GitHub stack of #9 and #10, where #10's summary records #14's head branch as its base | GitHub wins: #10 sits on #9, and #14 joins neither |
| Build with a GitHub stack whose pull requests are all merged | One group in GitHub's order, listed under **Closed**, and each page's sidebar lists every layer |
| Build with a GitHub stack of #9 and #11, where #11 has no summary | #9's sidebar lists #11, linked to GitHub. The index's group holds #9 alone. |
| Build with a GitHub stack of #9 and #10, and #15 in no stack, based on #10's branch | #15 joins the stack after #10: #9, #10, #15 |
| Build with a GitHub stack of #9 and #10, and #15 in no stack, based on #9's branch | #15 joins after the stack's members: #9, #10, #15 |
| Build with GitHub stacks of #9 and #10, and of #20 and #21, where #20's summary is based on #10's branch | Two stacks: the branch between them is ignored |
| Build with no GitHub stack for #12, whose summary is based on #13's branch | #12 and #13 share a group by base branches, as today |
| Build with a stack missing `number`, or a member missing `number` | Those pull requests sit on their base branches, and the note line counts what the build left out |
| Build with #10 listed in stack 30 as #9, #10, #11 and in stack 40 as #10, #12 | Stack 40 holds #10 and #12. Stack 30 holds #9 and #11, with #11 sitting on #9. |

Stacks, where GitHub does not answer:

| Do | Expect |
| --- | --- |
| Build with `stacks_lookup` returning None | Every stack comes from base branches, exactly as today |
| Build where `lookup` answers for #13's branch and fails for #16's | #12 sits on #13, #17 based on #16's branch stands alone, and the rest of the build is unchanged |
| Call `summary.repo_stacks` with a fake `gh` that answers two pages | Every stack from both pages |
| Call it with a fake `gh` that answers 404, fails on the second page, fails with a network error, or answers with an object that is not a list | None, through `build_checkout`'s lookup, in each case |

In every row of both tables, the build returns no failure for stacks, prints
exactly one `stacks:` line, and prints no `::warning::` about stacks.

Headers:

| Do | Expect |
| --- | --- |
| Build a stack whose bottom layer records `alpha` and whose upper layer records `""` | Both pages and both `site.json` rows carry alpha's header, with `url` `/projects/alpha/` |
| Build a stack whose bottom layer records `""` and whose second layer records `alpha` | One segment, whose header is alpha's |
| Build a merged GitHub stack whose bottom layer records `alpha` | Every layer's page and row carry alpha's header |
| Build a stack whose layers record `alpha`, then `alpha/beta` | Two segments, alpha's then alpha/beta's, and no warning (§ 8.5) |
| Build a summary that records `no-such-project` | A title header from its `name` |
| Build a summary that records `""` while its diff changes alpha's plan | A title header, and alpha's `reviews` leaves it out |
| Build a summary with no `project` whose diff changes one project's files | That project's header |
| Build a summary with no `project` whose `projects` list is `["no-such-project", "alpha/beta"]` | alpha/beta's header |
| Build a summary with no `project` whose diff changes files in both projects | A title header |
| Build a stack with no project whose bottom layer's `name` is empty | The next layer's `name`. With every `name` empty, the pull request title of the first layer with a summary. |
| Read `site.json` | Every row has `header`, and none has `name` or `projects` |
| Read a project's `reviews` in `site.json` | Every review in a segment whose header is that project, newest first, and no other |
| Build one summary with `project site page` | The page's header is `{"title": <name>}` |

Publish tests in `tests/test_summary.py` run in a temporary Git repository:

| Do | Expect |
| --- | --- |
| Run `project summary init` | The skeleton has `"project": null` |
| Publish with `project` `null`, missing, or `7` | Refused with a message naming `project`. The ref does not move. |
| Publish with an empty `name`, or a `name` of 61 characters | Refused with a message naming `name` and the 60-character limit |
| Publish with `project` naming a plan the checkout lacks | Published |
| Build with `project site page` with `project` `null` | Built |

Page tests run under node against the compiled renderer, as the existing
sidebar and index tests do:

| Do | Expect |
| --- | --- |
| Render a page whose header is a project | `<div class="stackhead"><a href="/projects/alpha/">Build alpha</a></div>` comes before the stack list, and no `stack projects` list is drawn |
| Render a page whose header is a title | `<div class="stackhead"><span>Summary 9</span></div>`, with no link |
| Render a header whose title holds `<script>` | The text is escaped, on the sidebar and on the index |
| Draw the Reviews index with a stack and a pull request alone | Each table body starts with `<tr class="stackhead"><th colspan="3" scope="rowgroup">`. The project header links and the title header does not. |
| Draw the index with a stack's bottom pull request filtered out | The header stays above the stack's other rows |

The build tests cover what the site does with the values a stack's layers
record. Which values each layer records is the skill's job, so the
implementing pull request's `skills/summarize-pr/SKILL.md` states § 2.1 as
written: each summary records its own choices and copies no other layer's
value. A stack that opens with a refactor recording `""` therefore keeps the
project that a layer above it records, as the second header test proves on
the site.

The existing tests `test_the_sidebar_lists_each_project_as_a_row_above_the_stack`,
`test_a_review_links_to_the_projects_its_diff_changes_and_back`, and
`test_a_summary_can_name_a_project_its_diff_does_not_touch` assert today's
project lists. The change rewrites them to assert the header. Every new test
above fails before the change, except the publish of a plan the checkout
lacks. The rows that expect today's grouping from base branches fail only
because `build_site` takes no `stacks_lookup` yet. They guard that grouping
against the change.

In a browser, the implementing pull request checks `project site serve` on
this repository's summaries ref, in the light and dark themes, at full width
and at 375 pixels. Each segment of a stack on Reviews has a left-aligned
header above its rows. The merged stack of #196 and #197 is one group under
**Closed**, and each of their sidebars lists both. Each summary's sidebar has
a header above each segment of its stack. A project header opens the
project's page, and clicking a title header does nothing. A 60-character
title fits in three lines of the sidebar. The pull request's Testing section
records these steps.

## 5. Cost

The new cost is the stacks request, and it is small. The build makes one
paginated request for the whole repository, at 100 stacks a page: one request
for this repository's 16 stacks, and ten for a repository with 1,000. It
saves the `base_pr` lookups for every member of a GitHub stack above its
bottom, which the build makes today once per base branch that no summary
resolves. The deploy already makes one `pr_status` query per pull request
with a summary, 22 in this repository, so one more request does not change
its time. The note line says how many stacks the build read, in how many
requests, so the implementing pull request can report both for this
repository. When GitHub cannot answer, the build costs what it costs today.

The header adds one pass over each stack's members and one dictionary lookup
per summary. Each page's data and each `site.json` row gain one object of up
to three short strings. `publish` adds two field checks and no network call.

## 6. Rollout

The skill and the CLI change in the same pull request, so one release carries
both. An agent that runs an older skill against the new CLI meets the refusal
for a `null` `project`, and the message says what to set. An older site build
ignores `project` and keeps today's project lists, and it finds every stack
from base branches, as it does today.

## 7. Background

The evidence comes from this repository's `refs/projector/summaries`, fetched
on 2026-10-10:

- **What summaries hold.** The ref holds 55 `summary.json` files across 22
  pull requests. 13 more pull requests have only a `spec.json` from an older
  release, which the site skips. No summary records `projects`, and every one
  records a non-empty `name`. The 26 distinct names run up to 49 characters,
  in a mix of sentence case and title case.
- **What the migration rule gives them.** Each pull request's newest head's
  diff decides. Three pull requests change a plan the site has. #204 changes
  `summary-diff-context` alone and keeps it as its project. #212 adds the plan
  `incremental-review`, which `main` now has, and keeps that project. #182,
  the `spec` rename, changes two plans and gets its title. The other 19
  change no plan and get their titles.
- **What links today.** `project_linker` adds each project whose folder holds
  a changed file, after the names in a summary's `projects` list. The page data
  carries the result as `projects`, and `build_site` lists each project's
  reviews from the same names. A `site.json` row carries no projects.
  `test_a_summary_can_name_a_project_its_diff_does_not_touch` pins the order:
  named projects first, unknown names dropped, then the ones the diff
  touches.

The GitHub stacks come from `gh api` on the same day:

- **One request lists them all.**
  `GET repos/ninjudd/projector/stacks?per_page=100` returns this repository's
  16 stacks in one page, each closed, with every member merged except the two
  of one stack that closed unmerged. A pull request in no stack, such as #215,
  gets `[]` from `?pull_request=`.
- **A merged stack keeps its members and order.** Stack 199 holds #196, then
  #197, both merged. #197's base is #196's head branch, so GitHub lists the
  stack bottom first. Stacks 26, 109, and 129 list their members bottom first
  too, by the same check of each member's base.
- **GitHub keeps a layer that base branches would drop.** Stack 129's top
  layer, #134, targets `main`. Its summary would record `main` as its base, so
  base branches would list it alone.
- **The endpoint answers 404 for a stack it does not have**, with a
  `documentation_url` at `docs.github.com/rest/pulls/stacks`. It answers with
  or without the `X-GitHub-Api-Version: 2026-03-10` header.

## 8. A stack can carry several projects

A stack can carry more than one project. One layer finishes a plan and the
next starts another, or a plan's work stacks on a layer that belongs to a
different plan. Each pull request still records one project or none
(§ 2.1), and no pull request names several. The build splits each stack into
segments, one for each run of layers that carry the same project, and gives
each segment its own header. The stack stays one group on the Reviews index
and in the sidebar, in its order from § 2.2. This replaces one header per
stack, which had to pick one project and hide the others.

### 8.1 A stack splits into segments

`stack_segments` walks the stack's members bottom first, as `find_stacks`
gives them, each with the project that § 2.3, step 2 resolves for it. A
member without a summary on the site, a member that records `""`, and a
member whose project the site lacks all name no project.

1. The first segment starts at the bottom member. Its project is the first
   project that any member names.
2. Each later member that names a project other than the current segment's
   starts a new segment with that project.
3. A member that names the current segment's project, or no project, joins
   the current segment.
4. A stack where no member names a project is one segment, with the title
   header of § 2.3, step 3: the lowest non-empty `name`, else the pull
   request title of the first member with a summary.

A member with no project therefore joins the segment of the member before it
in the stack's order from § 2.2, or the first segment when it is the bottom
member. In a stack with a branch, the member before it in that order can be
on another branch. A pull request that joins a GitHub stack by its base
branch comes after the stack's members (§ 2.2), so it joins the segment of
the member drawn above it, even when it is based on a lower member's branch.
A project that comes back after a different one starts a new segment.
Segments never merge across another project, so they keep the stack's order,
and every member is in exactly one segment.

| Projects, bottom first | Segments |
| --- | --- |
| A, A, B | A: layers 1 and 2. B: layer 3. |
| A, `""`, B | A: layers 1 and 2. B: layer 3. |
| `""`, A, B | A: layers 1 and 2. B: layer 3. |
| A, `""`, A | A: layers 1 to 3. |
| A, B, A | A: layer 1. B: layer 2. A: layer 3. |
| `""`, `""` | One title segment: layers 1 and 2. |

### 8.2 The data both pages read

`stack_segments` in `src/projector/site/__init__.py` takes every member of a
stack, in the order `find_stacks` gives them, members without a summary
included. It returns the stack's segments, each with its header. No separate
function decides one header for a whole stack. Every page of the stack
carries the segments as `segments`:

```json
"segments": [
  {"header": {"project": "proposed-projects", "title": "Show projects proposed in open pull requests", "url": "/projects/proposed-projects/"}, "members": [301, 302]},
  {"header": {"project": "stack-header", "title": "Head each stack with one project or a title", "url": "/projects/stack-header/"}, "members": [303]}
]
```

| Field | Value |
| --- | --- |
| `segments` | The stack's segments, bottom first. A pull request alone has one. |
| `segments[].header` | The segment's header, in the shape that § 2.3 gives |
| `segments[].members` | The numbers of the segment's pull requests, bottom first, members without a summary included |

Each pull request's `header`, on its pages, on its entry, and in its row of
`site.json`, is the header of the segment it is in. A project's page lists
the reviews whose `header` is that project (§ 2.3, step 6). It therefore
lists every member of every segment the project heads, a member with no
project of its own included.

The site's scripts read the shape through these names:

| Name | Where | What it is |
| --- | --- | --- |
| `StackHeader` | `site/src/globals.d.ts` | `{ title: string; project?: string; url?: string }`, the shape that § 2.3 gives |
| `StackSegment` | `site/src/globals.d.ts` | `{ header: StackHeader; members: number[] }`. `SummaryData.segments` is a `StackSegment[]`. |
| `stackHeaderHtml(header)` | The shared helpers at the top of `site/src/summary.ts` | One segment's header: an `<a href>` around the escaped title for a project, a `<span>` for a title |
| `stackSegmentsHtml(segments, rows)` | The same shared helpers | The sidebar's stack: for each segment, its header from `stackHeaderHtml`, then a `<ul class="stack">` of its members' rows in order. `rows` is the page data's `stack`, or the page's own row for a pull request alone. |

The Reviews index reads the flat rows of `site.json`, which arrive in stack
order. `reviewsTable` starts a new segment wherever a row's `header` differs
from the header of the row it drew above it in the same stack. Two segments
next to each other always name different projects, so with every row shown,
that gives exactly the segments in `segments`.

### 8.3 What the reader sees

**Reviews index.** Each segment is its own `<tbody>`, opening with its header
row, so the header's `scope="rowgroup"` covers its own rows only. Every
segment's body but the last one the index draws for the stack has
`class="continued"` and draws no divider. The stack still reads as one group,
with one divider below it:

```html
<tbody class="continued">
  <tr class="stackhead"><th colspan="3" scope="rowgroup"><a href="/projects/proposed-projects/">Show projects proposed in open pull requests</a></th></tr>
  <tr>…#301…</tr>
  <tr>…#302…</tr>
</tbody>
<tbody>
  <tr class="stackhead"><th colspan="3" scope="rowgroup"><a href="/projects/stack-header/">Head each stack with one project or a title</a></th></tr>
  <tr>…#303…</tr>
</tbody>
```

```text
 Pull request                                            Status              Updated
 ─────────────────────────────────────────────────────────────────────────────────────
 Show projects proposed in open pull requests            ← link to proposed-projects
 #301  Read proposed plans from summaries                Clean               …
 #302  Rebuild the site when a PR closes                 Unreviewed          …
 Head each stack with one project or a title             ← link to stack-header
 #303  Record each summary's project                     Changes requested   …
 ─────────────────────────────────────────────────────────────────────────────────────
 Fix loop stack subagent name                            ← plain text
 #298  Name the fix loop's subagent after its stack      Clean               …
 ─────────────────────────────────────────────────────────────────────────────────────
```

The **Open** and **Closed** boxes can hide every row of a segment. The index
then leaves that segment out. Because the index compares the rows it draws,
the segments on either side of a hidden one share one header when they name
the same project.

**Summary sidebar.** `stackSegmentsHtml` draws a header above each segment's
part of the stack list, and the current pull request's row is highlighted in
its own segment:

```html
<div class="prblock">
  <div class="stackhead"><a href="/projects/proposed-projects/">Show projects proposed in open pull requests</a></div>
  <ul class="stack" aria-label="Pull requests for Show projects proposed in open pull requests">…#301 #302…</ul>
  <div class="stackhead"><a href="/projects/stack-header/">Head each stack with one project or a title</a></div>
  <ul class="stack" aria-label="Pull requests for Head each stack with one project or a title">…#303…</ul>
</div>
```

```text
 ┌──────────────────────────────────┐
 │ Show projects proposed in open   │  ← link to proposed-projects
 │ pull requests                    │
 │ #301  Read proposed plans from   │
 │       summaries                  │
 │ #302  Rebuild the site when a PR │  ← this page, highlighted
 │       closes                     │
 │ Head each stack with one project │  ← link to stack-header
 │ or a title                       │
 │ #303  Record each summary's      │
 │       project                    │
 │                                  │
 │ Overview  Files                  │
 │ ──────────────────────────────── │
 │ 1  …                             │
 └──────────────────────────────────┘
```

`.prblock .stackhead` pulls the first header up toward the box's top edge, as
§ 2.4 says, and `.prblock .stack + .stackhead` puts 6 pixels above every
later one, so a segment's header stands apart from the rows above it.
`table.tbl.reviews tbody.continued tr:last-child td` draws no divider and no
bottom padding.

A stack whose layers carry one project, or none, has one segment and looks as
§ 2.4 shows.

### 8.4 Why segments

- **Each pull request still names one project.** A pull request is one
  change, and the summarizing agent can say which plan it carries out. A list
  of projects on one pull request is what § 1 found going wrong.
- **One header per run of layers, not one per stack.** A stack that finishes
  one plan and starts another is realistic. One header per stack has to pick
  one project and hide the rest, and it needs a warning on every such stack.
  A header above each run shows each project over the layers that carry it.
  Layers that name different projects are what segments are for, so the build
  warns about nothing.
- **A segment for each run, in stack order.** The stack's order is the order
  the work lands in, so segments follow it. Rejected: merging the runs of one
  project into one segment, which would take layers out of the stack's order,
  or put one header over rows that another project's rows separate.
- **A layer with no project joins a neighbor.** A refactor that carries no
  plan belongs with the work around it. Giving it a title segment between two
  project segments would put a title in a stack that has projects, and give
  `name` a second job. A stack that opens with such a refactor opens under
  its first project's header, as § 3 says.
- **`segments` in a page's data, `header` in each row of `site.json`.** The
  sidebar draws one stack, members without a summary included, so it reads
  `segments`. The index draws flat rows that its boxes can hide, so each row
  carries its segment's header, and the index compares neighboring rows. Both
  come from one `stack_segments` call, so they agree.
- **Rejected: one header that lists every project**, such as `A · B`. It hides
  which layer belongs to which project.
- **Rejected: the bottom layer's project as the header, with a badge on each
  layer that names another.** A project that is not the bottom layer's shows
  only as a badge, which is easy to miss.
- **Rejected: one project for the whole stack**, chosen by its lowest layer,
  by a majority of its layers, or by its top layer. Each hides every other
  project the stack carries. A majority also ties between two layers, and a
  stack with two branches on one pull request has two tops.

### 8.5 Acceptance criteria

Build tests in `tests/test_site.py` use the setup of § 4, where `alpha` and
`alpha/beta` are projects the site has. Each builds a GitHub stack unless the
row says otherwise:

| Do | Expect |
| --- | --- |
| Build a stack of #9, #10, #11 that records `alpha`, `alpha`, `alpha/beta` | `segments` holds alpha's segment with #9 and #10, then alpha/beta's with #11. Each page's and row's `header` is its segment's. alpha's `reviews` lists #10 and #9, and alpha/beta's lists #11. |
| Build a stack that records `alpha`, `""`, `alpha/beta` | alpha's segment holds #9 and #10, and alpha/beta's holds #11 |
| Build a stack that records `""`, `alpha`, `alpha/beta` | alpha's segment holds #9 and #10, and alpha/beta's holds #11 |
| Build a stack that records `alpha`, `no-such-project`, `alpha/beta` | alpha's segment holds #9 and #10 |
| Build a stack that records `alpha`, `""`, `alpha` | One segment, alpha's, with all three |
| Build a stack that records `alpha`, `alpha/beta`, `alpha` | Three segments: alpha's with #9, alpha/beta's with #10, and alpha's with #11. alpha's `reviews` lists #11 and #9. |
| Build a stack that records `""` and `""`, whose bottom layer's `name` is empty | One title segment, with the second layer's `name` |
| Build a GitHub stack of #9, #11, #10, where #11 has no summary, #9 records `alpha`, and #10 records `alpha/beta` | #11 is in alpha's segment, and #9's sidebar lists it under alpha's header |
| Build a GitHub stack of #9 and #10 that records `alpha`, `alpha/beta`, and #15 in no stack, based on #9's branch, that records `""` | The order is #9, #10, #15, and #15 is in alpha/beta's segment, the segment of the member before it |
| Build, with `stacks_lookup` returning None, a stack from base branches of #9 and #10 that records `alpha`, `alpha/beta` | The same two segments that a GitHub stack gives |
| Build a merged GitHub stack that records `alpha`, `alpha/beta` | Two segments, under **Closed**, and each merged page's sidebar draws both headers |
| Build a pull request alone | One segment |
| Read the build's output for every row above | No `::warning::` and no line about layers that name different projects |

Page tests run under node against the compiled renderer:

| Do | Expect |
| --- | --- |
| Render a page whose `segments` hold two segments | Two `<div class="stackhead">`, each followed by a `<ul class="stack">` of its members' rows in order, and the current row highlighted in its own segment |
| Draw the Reviews index with a stack of two segments | Two table bodies, the first with `class="continued"`, each starting with its header row, and one divider, below the second |
| Draw the index with rows whose headers are alpha's, alpha/beta's, and alpha's, where the boxes hide the middle row | One body, under alpha's header, holding the first and third rows |
| Draw the index with a stack whose last segment's rows are all hidden | The segment before it is the stack's last body, without `continued`, and draws the divider |
| Render segments whose header title holds `<script>` | The text is escaped, on the sidebar and on the index |

The rows of § 4 for a stack of one segment still hold, with the row for
`alpha` then `alpha/beta` and the row for a project's `reviews` as amended
above. In the browser check of § 4, a stack with two segments shows two
left-aligned headers on Reviews and in each member's sidebar, each linked to
its project.
