---
status: ready
priority: next
---

# Head each stack with one project or a title

Every stack of pull requests on the site, a pull request alone included, shows
one header above its rows: the project it carries out, linked to that
project's page, or a short title in plain text when it has none.

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

## 2. Solution

Each summary records the one project its stack carries out, or none, and a
short title for the stack. The site build decides one header per stack from
its layers' summaries, lowest layer first: the first project the site has a
page for, else the first title. The Reviews index draws that header above each
stack's rows, and a summary page's sidebar draws it above its stack list in
place of the project rows. A project header links to the project's page. A
title header is plain text. A summary published before the change gets its
header by a fixed rule from the fields it already has, so nothing needs to be
published again.

### 2.1 A summary records one project and a title

`summary.json` gains a `project` field, and its `name` field becomes the
stack's title:

| Field | Value | Meaning |
| --- | --- | --- |
| `project` | A project's canonical name, such as `"stack-header"` or `"payments/invoices"` | The one project this pull request's stack carries out: the plan the stack adds, specs, implements, or completes. |
| `project` | `""` | This pull request names no project. When no layer of its stack names one either, the stack has no project. A layer above that names a project gives the stack that project (§ 2.2, step 3). |
| `project` | `null`, or no `project` key | Not decided yet. `init` writes `null`, and `publish` refuses it. The build reads a summary published before the field existed by the rule in § 2.2, step 2. |
| `name` | A short title, at most 60 characters | The title of the work the stack does, or the work of the pull request alone. The site shows it as the stack's header when the stack has no project. |

`summarize-pr` fills both fields in step 4 of "Build the summary", in this
order:

1. **Choose the project the change is about.** List the candidates with
   `project list --json`, and look at the plan files the diff changes. The
   pull request's body and branch often name the plan. When the change touches
   several plans, choose the plan it adds or the plan whose status it changes.
   A plan touched only in passing, such as by a link fix or a rename across
   every plan, is not the change's project. When no single plan is the
   subject, record `""`.
2. **Name the whole stack.** Write two to six words in sentence case that name
   the work, such as `Stack header plan` or `Summary publish retry`. Do not
   write a sentence, and do not copy the pull request's title. When you
   summarize the bottom layer of a stack that you know will grow, name the
   work of the whole stack.
3. **Agree with the layer beneath.** When the pull request is stacked, its
   summary's `pr.basePr` names the pull request beneath it. Read that pull
   request's newest summary from the summaries ref. When its `project` is a
   non-empty string, copy it over your choice from step 1. When its `name` is
   non-empty, copy it over your choice from step 2. Otherwise keep your own
   choice: a layer beneath that names no project, such as a refactor the plan
   never mentions, leaves the project to the layers above it. To change a
   stack's project or title, publish the summary of the lowest layer that
   names one again, because the build takes the header from that layer.

An update makes the same three choices. `review-pr`, and the skill's "Update
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

### 2.2 The build decides one header per stack

`build_summaries` in `src/projector/site/__init__.py` decides each stack's
header before it writes any page:

1. It reads each pull request's newest summary, as it does today. It computes
   `bases` with `stack_bases`, and the stacks with `stack_groups`. Each group
   lists a stack's pull requests that have summaries in the order of
   `stack_rows`: the bottom layer first, then each pull request's branches
   after it, lowest number first.
2. It resolves each pull request's own project from its newest summary,
   against the projects the site has a page for:
   - A `project` string names that project when the site has it, and no
     project otherwise. `""` names no project.
   - A summary whose `project` is `null` or missing takes the first name in
     its `projects` list that the site has. Without one, it takes the project
     whose folder holds the files its diff changes, when exactly one project
     does. The deepest project owns each file, as `project_linker` already
     assigns it. Otherwise the summary has no project.
3. For each group, it takes the header from the members in group order:
   - The first member with a project gives
     `{"project": <name>, "title": <the plan's title>, "url": "<base>projects/<name>/"}`.
   - Without one, the first member with a non-empty `name` gives
     `{"title": <name>}`.
   - Without either, the bottom member's pull request title gives
     `{"title": <pr.title>}`. `validate` requires that title, so every stack
     has a header.
4. When a member after the header's member records a different project, the
   build prints
   `::warning title=Stack project::#<n> records <project>, but its stack takes <header project> from #<m>`
   and continues.
5. It writes `header` into the data of every page of every pull request in the
   group, every head included, and into each entry. `review_row` copies it into
   the pull request's row of `site.json`.
6. `build_site` lists, on each project's page, the reviews whose header is
   that project, newest first.

A pull request that stands alone is a group of one, so its header comes from
its own summary by the same steps. `build_page`, which `project site page`
uses to build one summary with no site around it, gives that page the header
of a site with no projects: `{"title": <name>}`, or the pull request's title
when `name` is empty.

A page's `data.json` and a row of `site.json` carry the header as one of these:

```json
"header": {"project": "stack-header", "title": "Head each stack with one project or a title", "url": "/projects/stack-header/"}
```

```json
"header": {"title": "Fix loop stack subagent name"}
```

| Field | Present when | Value |
| --- | --- | --- |
| `title` | Always | The project's title, from its plan's first heading, or the stack's title |
| `project` | The stack has a project | The project's canonical name |
| `url` | The stack has a project | The project's page, under the site's base path |

### 2.3 The index and the sidebar draw the header

The site's pages load `summary.js` for the helpers at its top, such as `esc`
and `statusHtml`. A new helper there, `stackHeaderHtml`, draws the header's
content for both pages. For a project it returns an `<a href>` to `url`
around the escaped title. Otherwise it returns a `<span>` around the escaped
title.

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

### 2.4 What reads `projects` and `name` after the change

| Reader | Today | After the change |
| --- | --- | --- |
| Summary page sidebar | One `.stack.projects` row per entry in the page data's `projects` | One header, from `header` |
| Reviews index | Each row's pull request number and title, with no project and no `name` | Unchanged rows, under a header row that carries the stack's project or title |
| Project page's list of reviews | The reviews whose `projects` include the project, each as `#N` and its `name`, or its title when `name` is empty | The reviews whose `header` is the project, each as `#N` and the pull request's title |
| Page `<title>` and the summary's `h1` | The pull request's title, falling back to `name` | The pull request's title. `validate` requires it, so the fallback never runs, and the change removes it from `pr_title` and `renderPage`. |
| A file card's **View project** link | The deepest project whose folder holds the file | Unchanged. It says where the file lives, not what the stack is for. |
| Search | Indexes the docs and the project files | Unchanged. It reads neither field. |
| `prepare_page` | Sets an empty `name` to `<repo>#<n> summary` | Passes `name` through as written, so § 2.2, step 3 can tell an empty name from a set one |

The change removes the page data's `projects` list, the `projects` list that
`build_summaries` keeps on each entry for `build_site`, the `name` field of a
`site.json` row, the `projectsList` function, and the `.prblock .stack.projects`
rules in `site/assets/summary.css`. The build still reads a summary's
`projects` list, but only in § 2.2, step 2, for a summary with no `project`.

### 2.5 Files that change

| File | Change |
| --- | --- |
| `src/projector/summary.py` | `init` writes `"project": null`. `publish` checks `project` and `name` against `NAME_MAX` and says what to set. `prepare_page` stops setting a default `name`. |
| `src/projector/site/__init__.py` | `project_linker` returns a summary's own project. A new `stack_header` takes a group's members and returns its header. `build_summaries` computes the groups before it writes pages, writes `header` in place of each entry's `projects`, and prints the warning. `review_row` writes `header` and drops `name`. `build_site` lists each project's reviews by header. `pr_title` drops its `name` fallback. `build_page` gives a standalone page its header. |
| `site/src/summary.ts` | `stackHeaderHtml`. The sidebar's header replaces `projectsList`. The page title drops its `name` fallback. |
| `site/src/site.ts` | `reviewsTable` starts each body with its header row. A project page lists its reviews by pull request title. |
| `site/src/globals.d.ts` | A `StackHeader` type. `header` replaces `projects` in `SummaryData`, and replaces `name` in `SiteReview`. |
| `site/assets/summary.css`, `site/assets/site.css` | `.prblock .stackhead` replaces the `.prblock .stack.projects` rules. A `table.tbl.reviews tr.stackhead th` rule overrides the head row's small capitals, takes the body's top padding in place of the `tbody tr:first-child td` rule, and adds no divider. |
| `site/assets/summary.js`, `site/assets/site.js` | Rebuilt with `npm run build` in `site/`. |
| `skills/summarize-pr/SKILL.md`, `skills/summarize-pr/format.md` | The `project` field, the title meaning of `name`, and the three choices in § 2.1, in both "Build the summary" and step 2 of "Update the page when the pull request moves". |
| `docs/cli.md` | The Reviews index, the sidebar, and the project pages as § 2.3 and § 2.4 describe them. |
| `tests/test_summary.py`, `tests/test_site.py` | The tests in § 4. |

## 3. Why this design

- **The summary records the project, and the build only checks it.** Which
  plan a change carries out is a judgment. The summarizing agent already
  makes it when it reads the plan, the pull request's body, and the diff. The
  diff alone cannot make it, because a stack's upper layers implement a plan
  without editing it, and a rename edits plans it is not about. Rejected:
  deriving every pull request's project from its diff, which is today's rule
  and the source of both failures in § 1.
- **The lowest layer decides, and every layer copies it.** A stack has no file
  of its own on the summaries ref. Each layer has its own summary. Copying the
  layer beneath when a summary is written makes the layers agree, and taking
  the lowest layer when the site builds makes a disagreement harmless and
  visible in the build log. When the bottom layer merges and the next layer is
  rebased onto the default branch, that layer holds the copied values, so the
  stack keeps its header. Rejected:
  - A record per stack, such as `summaries/stacks/<bottom>.json`. A stack has
    no stable key, because its bottom changes as layers merge and new layers
    stack on.
  - A majority vote among the layers. A reader cannot predict it, and two
    layers that disagree tie.
  - The top layer's choice. Two branches stacked on one pull request give a
    stack two tops.
- **A layer with no project does not hide one above it.** A stack often opens
  with a refactor that the plan's work needs but the plan never names.
  Skipping members with no project gives that stack its project. Skipping a
  project the site lacks does the same for a plan that a pull request adds
  before it merges.
- **`name` becomes the title, rather than a new field.** Every published
  summary already has a short `name`, and the skill already writes one, so a
  summary published before the change has a title without being published
  again. Rejected: a new `title` field, which every older summary lacks and
  which reads like `pr.title`.
- **`null` means undecided, and `publish` refuses it.** If the skeleton wrote
  `""`, an agent that skipped the choice would publish "no project", and
  nothing would show the mistake. Refusing `null` makes every new publish
  state its choice. The build still reads an older summary, which has no
  `project` key, by the fixed rule in § 2.2.
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
- **The header reuses the index's grouping.** `stack_groups` already lists
  each stack's members in order for the Reviews index. The header uses those
  groups instead of walking `stack_bases` again, so the index's grouping and
  the header's members cannot drift apart. The sidebar's stack list comes from
  `stack_rows`, which walks the same tree, so a page's header and its stack
  list cover the same pull requests.

## 4. Acceptance criteria

Build tests in `tests/test_site.py` give the build summaries in a temporary
checkout with the projects `alpha` and `alpha/beta`, as the existing tests do:

| Do | Expect |
| --- | --- |
| Build a stack whose bottom layer records `alpha` and whose upper layer records `""` | Both pages and both `site.json` rows carry alpha's header, with `url` `/projects/alpha/` |
| Build a stack whose bottom layer records `""` and whose second layer records `alpha` | The header is alpha's |
| Build a stack whose layers record `alpha`, then `alpha/beta` | The header is alpha's, and the build prints one `::warning title=Stack project::` line naming the upper layer |
| Build a summary that records `no-such-project` | A title header from its `name` |
| Build a summary that records `""` while its diff changes alpha's plan | A title header, and alpha's `reviews` leaves it out |
| Build a summary with no `project` whose diff changes one project's files | That project's header |
| Build a summary with no `project` whose `projects` list is `["no-such-project", "alpha/beta"]` | alpha/beta's header |
| Build a summary with no `project` whose diff changes files in both projects | A title header |
| Build a stack with no project whose bottom layer's `name` is empty | The next layer's `name`. With every `name` empty, the bottom pull request's title. |
| Read `site.json` | Every row has `header`, and none has `name` or `projects` |
| Read a project's `reviews` in `site.json` | Every review in a stack whose header is that project, newest first, and no other |
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
implementing pull request's `skills/summarize-pr/SKILL.md` states § 2.1,
step 3 as written. It copies the layer beneath's `project` only when that
value is a non-empty string, and its `name` only when that value is not
empty. A stack that opens with a refactor recording `""` therefore keeps the
project that a layer above it records.

The existing tests `test_the_sidebar_lists_each_project_as_a_row_above_the_stack`,
`test_a_review_links_to_the_projects_its_diff_changes_and_back`, and
`test_a_summary_can_name_a_project_its_diff_does_not_touch` assert today's
project lists. The change rewrites them to assert the header. Every new test
above fails before the change, except the publish of a plan the checkout
lacks.

In a browser, the implementing pull request checks `project site serve` on
this repository's summaries ref, in the light and dark themes, at full width
and at 375 pixels. Each stack on Reviews has a left-aligned header above its
rows. Each summary's sidebar has one header above its stack. A project header
opens the project's page, and clicking a title header does nothing. A
60-character title fits in three lines of the sidebar. The pull request's
Testing section records these steps.

## 5. Cost

Cost does not matter here. The build already reads every summary and computes
`stack_groups`. The header adds one pass over each group's members and one
dictionary lookup per summary. Each page's data and each `site.json` row gain
one object of up to three short strings. `publish` adds two field checks. No
path gains a network call, a file read, or a Git command.

## 6. Rollout

The skill and the CLI change in the same pull request, so one release carries
both. An agent that runs an older skill against the new CLI meets the refusal
for a `null` `project`, and the message says what to set. An older site build
ignores `project` and keeps today's project lists.

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
