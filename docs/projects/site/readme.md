---
status: completed
priority: now
---

# Serve a repository's Projector site

## 1. Outcome

A repository that sets up Projector hosting gets one site on GitHub Pages
for everything Projector keeps in it. Its home page renders the repository's
`README.md`. A menu reaches the project plans under `docs/projects`, the
pull request walkthroughs, and every other Markdown document under `docs/`.
A reader browses the plans by status and priority, opens one to read it with
its nested projects and supplemental files beside it, and follows links
between documents without leaving the site.

The site began as the walkthroughs publisher (`pr-walkthrough`, sections 4,
6 and 7). This project owns the site as a whole; walkthroughs are one of its
content kinds.

## 2. Acceptance criteria

- `project site build` builds the home page, the projects view, the docs, and
  every published walkthrough from one checkout and the walkthroughs ref, and
  builds a site for a repository with no walkthroughs or no plans.
- The home page renders `README.md`, and its menu lists Projects, PRs, then
  each Markdown file under `docs/` outside the plans, titled by its first
  heading.
- The projects view groups plans by status, orders them by priority, shows
  nesting, filters by text, and opens each plan with its status, priority,
  parents, nested projects, and supplemental files.
- A link between two documents the site serves stays in the site; any other
  relative link goes to the file on GitHub.
- A plan that does not parse costs the projects view, not the deploy.
- A push to the default branch that changes `README.md`, `docs/`, or a
  configured projects directory rebuilds the site, as publishing a
  walkthrough does.
- A deploy refuses to run when a private repository's Pages site is public,
  or when GitHub cannot say whether it is.

## 3. How the site is built

`project site build --out DIR --repo-root CHECKOUT --base PATH
[--walkthroughs DIR]` writes a static site. It reads plans through the CLI's
own project model, honoring a configured `projects.dir`, so the site and
`project list` never disagree about what a project is. It copies every
Markdown file it serves, unchanged, under `content/`, and writes `site.json`:
the base path, the repository and branch, the README and docs with their
titles, each project's frontmatter fields and supplemental files, and each
walkthrough's summary.

Every view has a real path under the base, which is `/projector/` for this
repository's site:

| Path | View |
|---|---|
| `/` | The README |
| `/projects/` | The projects browser |
| `/projects/<name>/` | One plan; nested names nest |
| `/prs/` | The walkthrough list |
| `/prs/<number>/` | A pull request's newest walkthrough, rendered in place |
| `/prs/<number>/<head>/` | One walkthrough version, beside its `data.json` |
| `/docs/<path>/` | A document at its repository path without `.md` |
| `/assets/` | The one copy of the scripts and styles every page loads |

GitHub Pages serves only files that exist, so the build writes a small shell
page at every path. The home page's shell loads `site.js`, which fetches
`site.json`, renders the view its path names, and renders links between
views in place through the History API. A walkthrough page loads the
walkthrough renderer and carries the same site menu, which scrolls away so
the walkthrough's own headers can stick. `404.html` is the home shell, so a
mistyped path shows the site's not-found view.

The composite action passes its checkout as `--repo-root` and the base path
`actions/configure-pages` reports as `--base`. A repository that
has published no walkthrough has no ref to fetch, and the action skips that
step instead of failing. The workflow `project site workflow` writes gains a
`push` trigger on the default branch for `README.md` and `docs/**`.

## 4. Decisions

- **Render Markdown in the browser.** The CLI has no runtime dependencies and
  the standard library has no Markdown renderer. The page loads marked and
  DOMPurify from cdnjs, the host the walkthrough pages already load
  highlight.js from, and sanitizes every rendered document before it touches
  the page. Content comes from the repository's default branch, but a
  README can still carry raw HTML, so sanitizing is not optional.
- **Real paths, one shell per path.** GitHub Pages serves static files and
  has no rewrites. The first version routed on the URL's hash from a single
  shell, and `/projector/#/prs` read as an odd address for a site whose
  walkthroughs already had real paths. Each path now gets a copy of a shell
  under a kilobyte, which costs one file per document and per project and
  nothing at view time. Serving every path from `404.html` would have
  avoided the files but answered every page with HTTP status 404. Old hash
  and `/<number>/` walkthrough addresses were not redirected: they were
  public for a day.
- **Generate each walkthrough's data at deploy, not in the browser.** The
  page could parse the stored diff and sanitize the spec itself, making the
  deploy a pure copy, but only by porting the Python parser and checks to
  JavaScript while `publish` and `site page` keep the Python ones. Two
  implementations of one set of rules drift, and a bad spec would fail in a
  reader's browser instead of at publish or deploy.
- **Copy Markdown, do not pre-render it.** The deploy stays a copy plus one
  manifest, in line with walkthroughs (`pr-walkthrough` section 8), and a
  reader always sees the committed text.
- **Check visibility at every deploy.** The site copies a repository's
  README, docs, plans and diffs into its Pages site, and on a plan without
  private Pages a private repository's site is public. `pr-walkthrough`
  section 4 already required refusing that case, but only the skill's
  default publish checked it, and a docs push deploys without the skill. The
  action now builds with `--check-visibility`, which reuses the same check
  and fails closed when GitHub cannot answer.
- **Relative links resolve against the document.** A target the site serves
  becomes its route; anything else goes to `github.com/<repo>/blob/<branch>/`
  for files and `raw/` for images, which only a reader with access to the
  repository can open.

## 5. Deferred

- Linking a plan to the walkthroughs of the pull requests that implement it,
  which `pr-walkthrough` section 6 describes. Nothing in a spec names its
  project yet.
- Search across documents, rather than filtering the projects view by name.
- Non-Markdown files under `docs/`, such as images, which the site does not
  copy; a relative image resolves to GitHub instead.

## 6. Outcome

Shipped in releases 0.5.4 and 0.5.5. The home page, the menu, the projects
view, the docs, and the walkthroughs are live at
`https://ninjudd.com/projector/`, each at its own path, and a push to `main`
that changes `README.md` or `docs/` rebuilds the site, as does a release. Every acceptance criterion in
section 2 holds: the builds without walkthroughs or plans, the broken-plan
fallback, the link rewriting, and the visibility refusal are tested in
`tests/test_site.py`, and the views, in-place navigation, and direct loads of
each path were checked in a browser against a build of this repository.

Two deviations from the first design are recorded elsewhere in the plans:
routing on real paths rather than the URL's hash, a decision in section 4,
and the composite action's rename from `actions/walkthroughs` to
`actions/site`, which `pr-walkthrough` section 7 records (#82). The three
items in section 5 were deferred, since none was part of the promised
outcome, and `site-content` later shipped all three.

## 7. Projector's own site runs the action from `main`

Projector's `.github/workflows/projector-site.yml` uses
`ninjudd/projector/actions/site@main`, so a merged change to the action or
the site code reaches `https://ninjudd.com/projector/` before a release moves
`v0`. The workflow's push trigger watches `actions/site/**` and
`src/projector/**` as well as `README.md` and `docs/**`, so merging such a
change rebuilds the site without waiting for a docs edit.

Other repositories keep `@v0`, which `project site workflow` still generates
by default, so a release remains the only change that reaches them.

The cost is that a bad merge to the action or the site code breaks
Projector's own site until someone fixes or reverts it. Finding that break
here, before a release carries it to other repositories, is the purpose of
the preview.

## 8. Serve the site without GitHub Pages

A repository that cannot turn on GitHub Pages yet, or does not want to, can
still read its site. `project site serve` builds the site a deploy builds
into a temporary directory and serves it over HTTP from the standard
library's `http.server`, so it adds no dependency. It fetches the
walkthroughs ref into the copy `walkthrough publish` keeps, extracts it with
`git archive`, and dates each spec by the commit that last changed it,
because the build orders a pull request's walkthroughs by that date and an
archive stamps every file with the ref's newest commit.

- **Rebuild on change by polling.** The server checks the README, `docs/`,
  the projects directory, and the walkthroughs ref every second and builds a
  fresh directory when any changed. The standard library has no
  cross-platform file watcher, and a second of latency costs a reader
  nothing. A request in flight keeps its directory, which the next rebuild
  removes.
- **Answer like GitHub Pages.** A missing path gets `404.html` with status
  404, and `--base` serves the site under a path, so what works locally
  works on Pages.
- **Listen on loopback by default.** The site copies the README, docs,
  plans and diffs. A wider `--host` is the user's decision, and the server
  warns when it is made.
- **Host elsewhere with `site build`.** Any static host that answers a
  missing path with `404.html` can serve `project site build --out DIR`;
  no host-specific packaging was added.

## 9. `project init` sets the site up

Setting a repository up took three steps a person or agent ran by hand: a
`gh api` call to turn on Pages with the Actions source, a second one to make
a private repository's site private, and `project site workflow --write`.
The second was easy to miss, and missing it publishes a private repository's
README, plans and diffs. `project init` now runs all three, and points the
repository's website link at the site.

- **On by default, best effort.** Adopting Projector is when a repository
  most wants its site, so `init` sets it up without a flag. It must still
  adopt a repository that is not on GitHub, lacks `gh`, or whose user is not
  an admin, so any of those skips the site with a note and exits 0. `--site`
  makes them an error, and `--no-site` or `site.enabled = false` skips the
  site; the key keeps a rerun of `init`, which `check` asks for, from turning
  Pages back on after a repository declined it.
- **Make the site safe before writing the workflow.** When GitHub refuses to
  make a private repository's site private, `init` writes no workflow, so
  nothing can be merged that would deploy the repository's content publicly.
  The action's `--check-visibility` stays as the deploy's own guard.
- **Check admin rights before changing anything.** A non-admin changes
  nothing on GitHub and hears what an admin must do. A site an admin already
  set up still gets its workflow, since proposing a file needs no admin.
- **Never overwrite the website link.** `init` fills an empty link, treats a
  link that differs only in scheme or trailing slash as the site, as GitHub's
  "Use your GitHub Pages website" box writes it, and keeps any other.
- **Read `origin`, not `GITHUB_REPOSITORY`.** The site build reads the
  environment variable for the action; `init` reads only the checkout, so
  tests and scripts that run `init` inside GitHub Actions do not reach the
  repository they run in.
- **Stay idempotent.** An existing Pages site is switched to the Actions
  source rather than refused, and a rerun reports the site, the link, and
  the workflow as `unchanged`, as `init` reports its other files.
