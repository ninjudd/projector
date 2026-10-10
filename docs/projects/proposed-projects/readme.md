---
status: in-progress
priority: now
---

# Show projects proposed in open pull requests

The site's Projects page lists each project whose plan an open pull request
in the repository adds, marked **Proposed** and linked to that pull request,
beside the projects already on the default branch.

## 1. Problem

The Projects page is the site's list of plans. `project site build` makes it
from the `readme.md` files in the checkout it builds, which on a deploy is the
default branch. A new plan reaches that branch only when its pull request
merges, and Projector writes a plan as a pull request so that people review
it first. While the plan is in review, the page does not list it, so the
reader who goes there to find plans misses the ones that need their attention
most.

The rest of the site already has the plan. The review loop publishes a
summary of every pull request it reviews to `refs/projector/summaries`, and
each summary stores the pull request's diff as `diff.patch`. A new file's diff
holds every line of the file. So the Reviews section shows the whole plan as a
diff, but nothing on the site shows it as a project:

- **The Projects page and search leave it out.** Pull request #216 added the
  `stack-header` plan, and the review loop summarized each of its heads. On
  2026-10-10, while #216 was open, its newest summary, at head `a3e26c8`,
  stored `docs/projects/stack-header/readme.md` as a new file in one hunk,
  `@@ -0,0 +1,640 @@`, which is every line of the plan. The Projects page and
  the site's search did not list `stack-header` until #216 merged.
- **A review cannot link the plan it adds.** `project_linker` in
  `src/projector/site/__init__.py` links a changed file to the project whose
  folder holds it, and only to a project the site has a page for. A file in
  the folder of a plan that the pull request adds gets no link.
- **A stack header cannot name a plan in review.** The `stack-header` plan,
  merged in #216, resolves each stack's header against the projects the site
  has a page for. A stack whose plan is still in review falls back to a plain
  title.

## 2. Solution

The site build finds each project that an open pull request's newest summary
adds, and adds it to the site's list of projects as a proposed project. A
proposed project is one whose `readme.md` is a new file in the diff of the
newest summary of an open pull request whose head branch is in this
repository, and is not in the checkout the site is built from. The build
rebuilds the plan's text from that diff, parses it with the same code
`project list` uses, and serves it as Markdown at `projects/<name>/`, the
address it keeps after it merges. The Projects page lists it in its status
group with a **Proposed** badge that links the pull request's review page.
Its own page opens with a banner that names the pull request and says the
plan is not merged. The build reads only what summaries already publish, plus
one field it adds to the status query it already makes, so it puts nothing on
the site that the Reviews section does not already show. The site workflow
also deploys when a pull request in the repository closes, so a proposed
project leaves the site as soon as its pull request merges or closes.

### 2.1 The build finds proposed projects in the summaries

`build_site` in `src/projector/site/__init__.py` collects the checkout's
projects first, as it does now. Then `build_summaries` runs these steps:

1. It reads every summary and keeps each pull request's newest one, as it
   does now. `prepare_page` parses each diff into files with `parse_diff`,
   which marks a new file with `new` and keeps every line of every hunk.
2. It asks `status_lookup` for each pull request's status once, before it
   links any summary to a project. It already asks once for each pull request,
   after linking. This step only moves the call earlier. `PR_STATUS_QUERY` in
   `src/projector/summary.py` gains the pull request's `isCrossRepository`,
   and `pr_status` returns it as `fork`, `true` when the head branch is in
   another repository.
3. It takes each pull request whose state is `open` and whose `fork` is
   `false`, lowest number first. A draft pull request is open. A pull request
   from a fork is never proposed, whoever published its summary. A pull
   request whose status the build cannot ask for proposes nothing. When any
   summary adds a plan and its status is unknown, the build prints one line
   that says how many pull requests it left out and why.
4. In each of those pull requests' newest diffs, it picks every file that is
   `new` and whose path is `<projects directory>/<name>/readme.md`, with that
   exact lowercase file name. The projects directory is the one the checkout
   configures, even when the checkout does not have it yet, so a repository's
   first plan can be proposed. `<name>` may hold slashes, for a nested
   project.
5. It drops a candidate whose name is a project in the checkout, or whose path
   is a file in the checkout. The checkout always wins.
6. It rebuilds each candidate's text with `new_file_text`, a new function in
   `src/projector/summary.py`. The function joins the added lines of a new
   file's hunks with `\n`, and ends the text with `\n` unless the diff marks
   the last line as having no newline. It returns `None` for a file with no
   hunks, which is either empty or binary.
7. It parses the text with `project_from_text`, the function that
   `ProjectStore._project_from_path` in `src/projector/core.py` now calls
   after it reads a file. The name comes from the path, as it does for a
   checkout project. A plan that does not load, because of a bad name,
   frontmatter, status, priority, or owner, is skipped with one line that
   names the pull request, the path, and the reason:

   ```text
   ::warning title=Proposed project skipped::#218 docs/projects/proposed-projects/readme.md: status must be one of draft|ready|in-progress|completed
   ```

   A nested plan loads whether or not its top-level folder holds a plan, as a
   nested plan in the checkout does.
8. When two open pull requests add a plan with the same name, the lower
   number's plan is the proposed project, and the other numbers go in its
   `also` list. A plan that fails step 7 does not take the name.
9. For each proposed project, it takes the other new Markdown files that the
   same diff adds under the project's folder, outside any deeper project's
   folder and outside any directory whose name starts with `.`, as the
   project's `files`. `pages_under` leaves out the same hidden directories for
   a checkout project. It rebuilds each file with `new_file_text` and titles
   it with `title_from_text`, as `page_title` titles a Markdown file in the
   checkout. It does not carry a new HTML page, image, or data file.
10. It writes each proposed file to `content/<path>` in the site, beside the
    checkout's files. It never writes over a path the checkout has.
11. It builds `project_linker` over the checkout's projects and the proposed
    ones, links each summary, and writes the review pages, as it does now.
    It returns the proposed projects beside its entries.

`build_site` then adds the proposed projects to its list, so
`assign_routes`, `site_routes`, `site.json`, `search.json`, and each
project's `reviews` include them. `site.json` names the projects directory as
`projectsDir` whenever it lists a project, so the page routes a proposed
project's files to the Projects section even when the checkout has no
projects directory. `search_index` reads each document from the site's
`content/` copy rather than from the checkout, so it indexes a proposed file
the same way as any other. `build_checkout` in `src/projector/cli.py` counts
the proposed projects in its summary line:

```text
wrote _site/index.html: 15 projects, 1 proposed project, 23 reviews, 0 reviews skipped
```

### 2.2 The data marks a proposed project

A proposed project is an ordinary entry in `site.json`'s `projects` list,
with the fields every project has, `name`, `path`, `title`, `status`,
`priority`, `owner`, `files`, and `reviews`, and one more:

| Field | Value |
| --- | --- |
| `proposed.number` | The open pull request whose newest summary adds the plan, such as `218` |
| `proposed.head` | That summary's head, the full SHA, such as `15cb40d18e911face2cd2393af143b526d6392e9` |
| `proposed.also` | The other open pull requests from the repository whose newest summaries add a plan of the same name that loads, lowest first, or `[]` |

A checkout project has no `proposed` field. The pull request's title and its
review status come from its row in `site.json`'s `reviews`, which every
proposed project has, because the summary that proposes it is on the site.

Each entry in `search.json` for a proposed project's pages gains
`"proposed": <number>`.

### 2.3 The site shows each proposed project as proposed

`site/src/site.ts` holds the word in one constant, `PROPOSED`, which every
label below uses. Wherever the site lists a proposed project, the listing
carries a badge, `<a class="badge proposed" href="<base>reviews/218/">Proposed #218</a>`,
that links the pull request's review page.

- **Projects page.** `showProjects` lists a proposed project in its status
  group, sorted with the others by priority and name, with the badge after
  its title. The note under the heading counts the proposed projects when
  there are any. The filter matches the word, so typing `proposed` lists only
  the proposed projects.
- **Project page.** `showProjectFile` draws a banner above the status badges
  on every page of a proposed project, its plan and each of its files. The
  banner names the pull request with its title and review status, drawn by
  `statusHtml` as the Reviews index draws it. It says that the plan is not on
  the site's branch, `site.branch`, and which head it shows. When `also` is
  not empty, it names those pull requests too.
- **GitHub links.** **View on GitHub**, and a link or image in the plan that
  points at a repository file the site does not carry, go to the file at
  `proposed.head`, not on the site's branch, where the file may not exist.
  `repoUrl` and `renderMarkdown` take the ref to link at.
- **Sidebar.** `tree` draws the badge beside a proposed project, such as a
  proposed nested project in a checkout project's folder.
- **Search.** A result whose entry has `proposed` shows the badge beside its
  kind.

The Projects page, with this plan's pull request, #218, open:

```text
Projects
16 projects under docs/projects, 1 of them proposed in open pull requests.
Status says how finished a project is; priority says when it is scheduled.

READY 5
 Project                                                          Priority  Name
 Review a minimal push incrementally                              next      incremental-review
 Let several models review the same pull request                  next      multi-model-review
 Show projects proposed in open pull requests  [Proposed #218]    next      proposed-projects
 Head each stack with one project or a title                      next      stack-header
 Expand the hidden lines around a summary's diff hunks            next      summary-diff-context
```

The project's page:

```text
Projects / proposed-projects
┌──────────────────────────────────────────────────────────────────────────┐
│ Proposed in #218 Plan showing projects proposed in open pull requests    │
│ · Changes requested                                                       │
│ This plan is in an open pull request and is not on main yet. It shows    │
│ head 15cb40d, the newest head with a summary.                             │
└──────────────────────────────────────────────────────────────────────────┘
ready  next  View on GitHub
Reviews  #218 Plan showing projects proposed in open pull requests

Show projects proposed in open pull requests
…
```

### 2.4 The site deploys when a pull request closes

The site workflow that `project init` and `project site workflow --write`
write, `WORKFLOW` in `src/projector/summary.py`, gains a trigger on a closed
pull request, and its deploy job skips a pull request from a fork:

```yaml
on:
  repository_dispatch:
    types: [projector-summaries]
  push:
    branches: [main]
    paths: [README.md, 'docs/**', .projector.toml, .github/workflows/projector-site.yml]
  pull_request_target:
    types: [closed]
  workflow_dispatch:
permissions:
  contents: read
  pull-requests: read
  pages: write
  id-token: write
concurrency:
  group: ${{ github.event_name == 'pull_request_target' && github.event.pull_request.head.repo.full_name != github.repository && format('fork-{0}', github.run_id) || 'pages' }}
  cancel-in-progress: false
jobs:
  publish:
    if: github.event_name != 'pull_request_target' || github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.site.outputs.page_url }}
    steps:
      - id: site
        uses: ninjudd/projector/actions/site@v0
```

1. A pull request in the repository closes, merged or not, and whatever
   branch it targets.
2. GitHub runs the workflow from the default branch. On `pull_request_target`,
   `GITHUB_REF` is the default branch and `GITHUB_SHA` is its newest commit, so
   the site action's `actions/checkout` checks out the default branch, as on
   every other trigger. The workflow never checks out the pull request's code.
3. The `publish` job runs only when the pull request's head repository is
   this one. A fork's pull request, or one whose fork was deleted, skips it.
4. The run takes the `pages` concurrency group and deploys to the
   `github-pages` environment. It needs no permission beyond the four the
   workflow already grants: `pages: write` and `id-token: write` to deploy,
   and `contents: read` and `pull-requests: read` for the checkout and the
   build's status queries.
5. The build asks the pull request's status, finds it `merged` or `closed`,
   and leaves its plan out of the proposed projects. The same deploy shows the
   pull request as **Merged** or **Closed** on the Reviews index, which until
   now kept showing it as open until some later deploy.

A run for a fork's closed pull request gets a concurrency group of its own,
`fork-<run id>`. Without it, that run would join `pages`, where a newer
waiting run cancels an older waiting one, so a run that deploys nothing could
cancel a deploy waiting there. Every other run keeps the group as it is now:
one deploy runs at a time, and a newer waiting run replaces an older waiting
one, which loses nothing because every deploy builds from the current state.

A repository gets the new workflow in one of three ways:

- **`project init`** rewrites a workflow that Projector wrote in any earlier
  shape, as it does now, and keeps one edited by hand.
- **`project site workflow --write`** does the same, and `--force` replaces a
  hand edit.
- **`project check`** prints a warning while the workflow is an earlier shape
  Projector wrote, which it can recognize because the earlier shape joins
  `PREVIOUS_WORKFLOWS`. The warning never fails the gate, and a workflow edited
  by hand gets none, since Projector cannot tell what its owner meant:

  ```text
  warning: .github/workflows/projector-site.yml: the site workflow is an earlier shape and does not deploy when a pull request closes (run 'project init' or 'project site workflow --write' to refresh it) [site-workflow-outdated]
  ```

The new trigger takes effect once the refreshed workflow is on the default
branch, where GitHub reads it. This repository's own workflow, which runs the
action from `main` and watches more paths, is edited by hand, so the
implementing pull request changes it by hand in the same way.

### 2.5 How other plans use proposed projects

- **`stack-header`, merged in #216.** That plan resolves each stack's header
  against the projects the site has a page for. Proposed projects are in that
  set, at the same address, `<base>projects/<name>/`. A stack whose plan is
  still in review gets a header that links the proposed page, rather than one
  that falls back to a title. The header can read `proposed` from the
  project to draw the badge. The build knows the proposed projects before it
  links any summary, so it knows them before it resolves any header. The two
  implementations can land in either order. The one that lands second
  resolves the header over the combined list.
- **`summary-diff-context`.** That plan stores the head's version of each
  changed file under `head/<path>` on the summaries ref, but not a new file,
  whose diff already holds every line. This plan reads new files from the
  diff, so it needs nothing from that one. The deferred view of changes to an
  existing project in § 6 would read the head's plan from `head/<path>`.
- **`incremental-review` and `multi-model-review`.** Neither changes how
  summaries are stored. An incremental review still updates the summary for
  the new head, so a proposed plan follows small pushes too. The banner draws
  the review status from the same `review` field that the Reviews index reads,
  so it shows whatever status those plans define.

### 2.6 Files that change

| File | Change |
| --- | --- |
| `src/projector/core.py` | `project_from_text`, the name check and frontmatter checks of `ProjectStore._project_from_path`, which now reads the file and calls it. |
| `src/projector/summary.py` | `new_file_text`, the text of a new file from its parsed hunks. `PR_STATUS_QUERY` asks for `isCrossRepository`, and `pr_status` returns it as `fork`. `WORKFLOW` gains the closed trigger, the fork condition, and the fork's own concurrency group, and today's shape joins `PREVIOUS_WORKFLOWS`. A helper tells the current shape from an earlier one, for `project check`. |
| `src/projector/site/__init__.py` | `build_summaries` asks for statuses before it links, finds the proposed projects, writes their files, links over both lists, and returns them. `build_site` adds them to its list before `assign_routes`, and names `projectsDir` whenever it lists a project. `search_index` reads from `content/`. |
| `src/projector/cli.py` | `site_projects` returns the configured projects directory even when the checkout lacks it. `build_checkout` counts the proposed projects. `check` adds the `site-workflow-outdated` warning. |
| `.github/workflows/projector-site.yml` | This repository's own workflow gains the same trigger, condition, and concurrency group by hand. |
| `site/src/site.ts` | `PROPOSED`, the badge, the banner, the Projects page count and filter, links at `proposed.head`, the sidebar badge, and the search badge. |
| `site/src/globals.d.ts` | `proposed` on `SiteProject`, and on `SearchEntry`. |
| `site/assets/site.css` | The badge and the banner, in the light and dark themes. |
| `site/assets/site.js` | Rebuilt with `npm run build` in `site/`. |
| `docs/cli.md` | The Projects page in "Build the Projector site": proposed projects, where they come from, and when they leave. The workflow's triggers, and the `project check` warning. |
| `tests/test_projector.py`, `tests/test_summary.py`, `tests/test_site.py` | The tests in § 4. |

## 3. Why this design

- **The name is Proposed.** A proposed plan is one put forward for review and
  not yet accepted, which is what an open pull request is. The word sits on a
  separate axis from `status`, so a row reads as both: Proposed, and Draft or
  Ready. Rejected names:
  - **Draft**, which is already a `status` value.
  - **Unmerged**, which names a Git mechanism rather than the plan's state,
    reads as a defect, and is awkward on a badge.
  - **Pending**, which GitHub already uses for checks and reviews, and which
    says the plan waits without saying for what.
  - **Incoming**, which suggests the plan is accepted and on its way.
  - **New**, which describes age. A plan merged yesterday is new, and a
    proposal can sit open for weeks.
  - **In review**, which the review statuses already cover. A proposed plan
    can be unreviewed or have changes requested.
  - **Open**, which is the pull request's state on the Reviews index's
    **Open** and **Closed** boxes, not the plan's.

  The project is named `proposed-projects` for the same reason.
- **Read the summaries, and only the summaries.** The summaries ref is the
  site's one source of pull request content. Publishing a summary is the act
  that puts a pull request's diff on the site, and every deploy already reads
  the ref. Reading a new plan from a diff the build has already parsed costs
  no request, no fetch, and no new storage. A summary publish also starts a
  deploy, so a proposed plan appears, and follows each new head, at the moment
  its summary does. Rejected:
  - **Fetching `refs/pull/<n>/head` in the build.** It needs the list of open
    pull requests from GitHub anyway, because `refs/pull/*/head` stays after a
    pull request closes. It costs a fetch per pull request on every deploy.
    It needs each branch's merge base, which the deploy's shallow checkout
    lacks, to tell what the pull request adds from what the default branch
    moved, so a project renamed on the default branch would show its old name
    as proposed. `summary-diff-context` rejects a per-deploy fetch for the
    same reasons.
  - **Asking the GitHub API for open pull requests and their files.** It would
    show plans from pull requests that nobody chose to publish, and add a
    second source of content with its own cache. No deploy starts when a pull
    request opens or moves, so the plan would still lag its branch until
    something else deployed.
  - **Every branch.** A branch without a pull request proposes nothing to
    anyone, and reading every branch would publish work in progress.
- **A pull request without a summary proposes nothing.** In a repository with
  a site, `review-pr` summarizes every pull request it reviews, so a plan in
  review has a summary. A repository that does not review with Projector
  publishes a summary with `project summary publish` to show a plan.
- **A fork's pull request is never proposed, and the status query says which
  ones are forks.** A plan on the Projects page reads as the repository's
  own, and a fork's branch is not the repository's to vouch for, even when a
  maintainer publishes its summary for review. `isCrossRepository` rides on
  the status query the build already makes for each pull request, so the
  check costs no request. Rejected: recording the fork in the summary at
  publish as a fallback. The build would read that field only when the status
  query fails, and then the state is unknown too, so the plan is left out
  either way. Every summary published before the change would also lack the
  field.
- **No new exposure, so the existing visibility check covers it.** A deploy
  runs `project site build --check-visibility`, which refuses to deploy a
  private repository's site while that site is public. Every proposed file is
  rebuilt from a `diff.patch` that the same deploy already serves on a review
  page. `project site serve` serves on the loopback address unless told
  otherwise, as it does now.
- **Serve proposed content as Markdown only.** The site renders Markdown
  through DOMPurify, so a plan's text cannot run a script. It shows an HTML
  page as it is, in a frame on the site's own origin, where the page's script
  can read every other page of the site. A proposed HTML page has not been
  merged, so it does not get that frame. The plan links it at its head on
  GitHub instead.
- **Unknown status proposes nothing.** The Projects page is the record of
  plans, so a plan from a closed pull request or a fork listed there is a false
  claim. The Reviews index counts an unknown state as open, because a list of
  summaries loses less from a wrong state. The build prints the count it left
  out, so a site served without `gh` says why it shows no proposed projects.
- **Deploy on `pull_request_target`, not `pull_request`.** Since
  2025-12-08, GitHub runs a `pull_request_target` workflow from the default
  branch, sets `GITHUB_REF` to the default branch, and checks environment
  rules against it, so the deploy job runs as it does on a push. Rejected:
  - **`pull_request`.** It runs on the merge ref, `refs/pull/<n>/merge`, and
    checks the `github-pages` environment's branch rule against that ref.
    This repository's rule allows only `main`, so the deploy would be refused.
    The site action's checkout would also check out the merge ref and build
    the closed pull request's tree as the site. GitHub also does not run a
    `pull_request` workflow for a pull request with a merge conflict, which an
    abandoned pull request often has. With forks left out, its token would
    have the permissions the workflow grants, but none of that helps a deploy
    that the environment refuses.
  - **A separate job that starts the deploy with `gh workflow run`.** It would
    work for any trigger, because a `workflow_dispatch` that the
    `GITHUB_TOKEN` sends still starts a run, but it needs `actions: write` and
    a second run per close. Since `pull_request_target` runs on the default
    branch, the deploy job can run directly.
- **`pull_request_target` runs no code from the pull request.** The workflow
  checks out the default branch, runs the default branch's `site.prepare`,
  and reads only the head repository's name from the event, in the job's
  condition. GitHub's warning about `pull_request_target` concerns building
  or running code from the pull request, which this workflow never does.
- **Every closed pull request in the repository deploys.** A merged pull
  request that changes no watched path starts no push deploy, so without this
  trigger the Reviews index would show it as open. A merge that does change
  one starts two deploys, and the concurrency group runs them one after the
  other, or drops the waiting one when a newer run arrives.
- **A fork's run gets its own concurrency group, rather than relying on the
  skipped job.** GitHub's workflow syntax reference does not say whether a job
  that its `if` skips still takes its concurrency group. A group of its own
  for the fork's run makes the answer not matter, and keeps the workflow-level
  group every earlier shape has.
- **The checkout always wins.** A plan of the same name in the checkout is the
  accepted one, and a file the checkout has is never replaced. That keeps a
  proposal from changing any page that the default branch already serves.
- **The lowest pull request number wins a name.** It is the first proposal and
  stays stable from build to build, where the newest summary would flip the
  page each time either pull request is summarized. The banner names the
  others, so their versions are one click away.
- **Proposed plans follow the checkout's rules for what loads.** A proposed
  plan is skipped for exactly the reasons a checkout plan is: step 4 reads
  only a lowercase `readme.md`, step 7 skips a plan that does not load, and
  step 9 leaves out hidden directories as `pages_under` does. A rule that
  only proposed plans follow would hide a plan while it is open that the site
  then lists once it merges.
- **In its status group.** Proposed is a separate axis from status. A section
  of its own would read as a fifth status, the confusion the name was chosen
  to avoid. In its group, a proposed `ready` plan sits beside the other
  `ready` plans, where a reader looking for ready work finds it, and a
  proposed nested project keeps its place under its parent. The badge, the
  count, and the filter keep proposed projects easy to find. A separate
  section remains a possible later change, as § 6 says.
- **The same address before and after the merge.** A proposed project lives at
  `projects/<name>/`, so a link to it, in a pull request or a stack header,
  opens the accepted plan once it merges. Rejected: an address under the pull
  request, such as `reviews/218/projects/proposed-projects/`, which would
  break every such link at the merge.
- **Reuse the parser, the diff, and the status query.** `project_from_text` is
  the code `project list` and `project check` run, moved out of
  `_project_from_path` so it can take text instead of a path. A second parser
  could accept a plan that `project check` rejects. `parse_diff` already
  parses every summary's diff, and `new_file_text` reads its output rather
  than parsing the patch again. The fork check is one field in `pr_status`'s
  query. `project_linker`, `assign_routes`, `search_index`, `statusHtml`,
  `tree`, and the `badge` styles all serve proposed projects unchanged, or with
  one new argument, because a proposed project is an entry in the same list.
- **One constant for the word.** Every label on the site reads `PROPOSED`, so
  the word changes in one place.

## 4. Acceptance criteria

Build tests in `tests/test_site.py` run in a temporary checkout with the
project `alpha`, publish summaries as the existing tests do, and replace
`summary.pr_status` with a fake that returns each pull request's state and
`fork`, `false` unless a row says otherwise. After the first row, each path is
under `docs/projects/`:

| Do | Expect |
| --- | --- |
| Build with open pull request #9, whose diff adds `docs/projects/gamma/readme.md` with `status: ready` and `priority: next` | `site.json` lists `gamma` with that status, priority, and title, and `proposed` `{"number": 9, "head": <head>, "also": []}`. `content/docs/projects/gamma/readme.md` equals the plan byte for byte. `projects/gamma/index.html` exists. `search.json` has `projects/gamma/` with `"proposed": 9`. |
| Build the same with #9 merged, or closed | No `gamma` |
| Build the same with #9 open and `fork` `true` | No `gamma`, and #9's row on the Reviews index stays |
| Build the same while the fake raises | No `gamma`, and one line saying one pull request's status is unknown |
| Build with #9's newest summary no longer adding `gamma`, and an older one adding it | No `gamma` |
| Build with #9 adding a plan whose last line has no newline | The content has no final newline |
| Build with #9 adding a plan with `status: maybe` | No `gamma`, and one `::warning title=Proposed project skipped::#9 docs/projects/gamma/readme.md: status must be one of …` line |
| Build with #9 adding `alpha/readme.md`, which the checkout has | `alpha` is the checkout's, with no `proposed`, and its content is unchanged |
| Build in a checkout with no projects directory, with #9 adding `gamma/readme.md` | `gamma` is proposed, and `site.json`'s `projectsDir` is `docs/projects` |
| Build with #9 and #12 both adding `gamma/readme.md`, with different titles | `gamma` has #9's title and `also` `[12]` |
| Build the same with #9's plan failing to load | `gamma` has #12's title and `also` `[]`, and the warning names #9 |
| Build the same with #12 from a fork | `gamma` has #9's title and `also` `[]` |
| Build with #9 adding `alpha/delta/readme.md` | `alpha/delta` is proposed |
| Build with #9 adding `omega/x/readme.md` and no `omega` anywhere | `omega/x` is proposed, as a checkout with that plan lists it |
| Build with #9 adding `gamma/readme.md`, `gamma/design.md`, `gamma/.notes/x.md`, `gamma/page.html`, `gamma/data.json`, and a binary `gamma/logo.png` | `gamma`'s `files` is `design.md` alone, and `content/` holds no other file of `gamma` |
| Read #9's review page data | Its file `docs/projects/gamma/readme.md` has the project `gamma` at `/projects/gamma/` |
| Count the fake's calls | One per pull request, as the existing `statuses` test asserts |

Unit tests:

| Do | Expect |
| --- | --- |
| Call `project_from_text` and load the same text with `ProjectStore` (`tests/test_projector.py`) | The same `Project`, and the same message for each kind of bad plan |
| Call `new_file_text` on parsed diffs (`tests/test_summary.py`) | A new file's exact text, with and without a final newline. `None` for a modified file, an empty file, and a binary file. |
| Call `pr_status` with GraphQL answers whose `isCrossRepository` is `true` and `false` (`tests/test_summary.py`) | `fork` is `True` and `False`, and the query asks for `isCrossRepository` |

Workflow tests in `tests/test_site.py`, beside the existing `WorkflowTests`
and `init` tests:

| Do | Expect |
| --- | --- |
| Read `workflow_text("v0", "main")` | It has `pull_request_target` with `types: [closed]`, the `publish` job's fork condition, and a concurrency group that is `pages` except for a fork's closed pull request |
| Pass today's generated workflow, and every earlier shape, to `generated_workflow` | Each is Projector's own |
| Run `project init` on a checkout whose workflow is today's shape | The workflow is rewritten to the new shape and reported `updated` |
| Run `project init` on a checkout whose workflow is edited by hand | The workflow is kept, as the existing test asserts |
| Run `project check` with today's shape, the new shape, a hand-edited workflow, and no workflow | Only today's shape prints the `site-workflow-outdated` warning, and every case exits 0 |

Page tests run under node against the compiled `site.js`, as the existing
Projects view tests do:

| Do | Expect |
| --- | --- |
| Draw the Projects page with one proposed `ready` project | Its row is in the **ready** group with `<a class="badge proposed" href="/reviews/9/">Proposed #9</a>`, and the note says `1 of them proposed in open pull requests` |
| Type `proposed` in the filter | Only the proposed rows stay |
| Draw the proposed project's page | The banner names #9 with its title and status, **View on GitHub** links `blob/<head>/docs/projects/gamma/readme.md`, and a relative link to a file the site lacks links at `<head>` |
| Draw a search result for a proposed page | It shows the badge |
| Give the pull request a title holding `<script>` | The badge and the banner show it as text |

Every test above fails before the change, except two that pin behavior the
change keeps: the unit test of `project_from_text`'s messages, and the build
of `omega/x`, which pins the checkout's rule.

In a browser, the implementing pull request runs `project site serve` on this
repository while a pull request that adds a plan is open, in the light and dark
themes, at full width and at 375 pixels. The plan is in its status group with
the badge, its page has the banner, the badge opens the review page, and search
finds the plan.

After the implementing pull request merges, its author opens a draft pull
request in this repository that adds a throwaway plan, publishes its summary,
and confirms that the deployed Projects page lists the plan as proposed. They
then close the pull request without merging and confirm that the Projector
site workflow runs on `pull_request_target`, its `publish` job deploys, and
the Projects page no longer lists the plan, while the Reviews index shows the
pull request as **Closed**. The pull request's Testing section records these
steps, and the merge of the implementing pull request itself shows the same
run on a merged pull request.

## 5. Cost

The site build runs on every deploy and on every rebuild of
`project site serve`. The deploy now also runs once for each closed pull
request in the repository.

- **GitHub.** No new request in the build. It asks for each pull request's
  status once, as it does now, only earlier, and the query returns one more
  field. The call-count test in § 4 holds it there.
- **Build work.** The build rebuilds text from hunks it has already parsed,
  and parses the frontmatter of the plans that open pull requests add. That is
  linear in the size of those plans, and small next to `parse_diff`, which
  already reads every summary's diff.
- **Site size.** Each proposed project adds its plan and its Markdown files to
  `content/` and `search.json`, and one small object to `site.json`. Only open
  pull requests contribute, so the size tracks the plans in review, not the
  history of the ref.
- **Deploys.** Each closed pull request in the repository starts one deploy,
  the same job a summary publish starts. A fork's closed pull request starts a
  run whose job is skipped. In this repository, where the review loop already
  publishes a summary for every head, a close adds one deploy to the several
  that each pull request already causes.

The implementing pull request reports the build's summary line and its time
with `time project site build` on this repository's summaries ref, before and
after the change.

## 6. Deferred

- **Changes to an existing project.** A pull request that changes a plan the
  checkout has, such as an implementation pull request that moves it to
  `in-progress`, shows nothing new. The project's page already lists that pull
  request among its reviews, and its review page shows the change. Showing
  the proposed status or text on the project's page needs the head's version
  of the plan, which `summary-diff-context` stores under `head/<path>`. A
  pull request that renames or moves a project falls here too.
- **A separate Proposed section.** The owner chose in-group placement for now.
  A section above the status groups remains a possible later change, for
  example if proposed projects crowd the groups.
- **`project list`.** The CLI reads the checkout and stays unchanged. A
  command that lists proposed projects can follow if someone needs one in a
  terminal.

## 7. Background

- **Where the summaries come from.** `review-pr` summarizes every pull request
  it reviews while the repository hosts a site, and publishes with
  `project summary publish`. Publishing stores `summary.json`, `diff.patch`,
  and `attributes.json` under `summaries/<number>/<head>/` and sends the
  `repository_dispatch` event that deploys the site. The deploy's other
  triggers today are a push to the default branch that changes the README,
  `docs/`, or the site's configuration, and a manual run. No trigger fires
  when a pull request opens, moves, or closes.
- **What a summary's diff holds.** `parse_diff` sets `new` on a file whose
  diff has `new file mode`, records each added line with its text, and records
  the `\ No newline at end of file` marker as a meta line. A binary file has a
  `Binary files … differ` line and no hunks.
- **What the build asks GitHub.** `build_checkout` gives the build `base_pr`,
  for a summary that did not record the pull request beneath it, and
  `pr_status`, one GraphQL query per pull request for its state, base branch,
  and reviews. The test that drives `statuses` in `tests/test_site.py`
  asserts one `pr_status` call per pull request.
- **How the site shows HTML.** `showHtml` in `site/src/site.ts` loads an HTML
  page in an `iframe` from `content/`, on the site's own origin and without a
  `sandbox` attribute. `renderMarkdown` passes Markdown through
  `DOMPurify.sanitize`.
- **What the checkout's projects skip.** `site_projects` in
  `src/projector/cli.py` prints a `::warning title=Project skipped::` line for
  each `invalid-project`, `missing-plan`, and `wrong-entry-case` issue that
  `project check` reports, but the list it returns comes from
  `readable_projects`. That list leaves out only plans that do not load, and
  a `README.md` entry point is never loaded. A nested plan under a top-level
  folder with no plan, such as `omega/x` with no `omega/readme.md`, loads and
  is listed, with the warning naming `omega`.
  `test_a_plan_that_does_not_parse_costs_only_its_own_place` keeps a nested
  plan whose parent's plan fails to load.
- **How GitHub runs the two pull request events.** GitHub's
  [events reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
  says a `pull_request` workflow runs on `refs/pull/<n>/merge` and not at all
  while the pull request has a merge conflict, and that a
  `pull_request_target` workflow runs on the default branch even then. The
  [2025-11-07 changelog](https://github.blog/changelog/2025-11-07-actions-pull_request_target-and-environment-branch-protections-changes/)
  says that from 2025-12-08 a `pull_request_target` workflow and its checkout
  always come from the default branch, and that environment branch rules are
  checked against `refs/pull/<n>/merge` for `pull_request` and the default
  branch for `pull_request_target`. This repository's `github-pages`
  environment allows deploys only from `main`. A `GITHUB_TOKEN` event starts no
  new run, except `workflow_dispatch` and `repository_dispatch`.
