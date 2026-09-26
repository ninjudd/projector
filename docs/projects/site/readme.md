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
items in section 5 remain deferred; none was part of the promised outcome.

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

## 8. Three sections: projects, reviews, and docs

The menu has three items, and each names one kind of content the same way
everywhere on the site: in the menu, the headings, the paths, and the keys of
`site.json`.

- **Projects** is the home page. The root shows the projects list, and so does
  `projects/`. A project's page renders beside a sidebar of its top-level
  project's folder, listing every supplemental file, subdirectory, and nested
  project, because a project can span several files and directories. Each
  file has a path under the project, `projects/<name>/<file>/`, rather than
  under `docs/`.
- **Reviews** lists the pull request walkthroughs at `reviews/`, with each
  head at `reviews/<number>/<head>/`. Reviews replace the earlier `prs/`
  paths, and no redirect keeps the old ones, as with the move to real paths
  in section 4.
- **Docs** renders the repository `README.md` at `docs/` beside a sidebar of
  every Markdown file under `docs/` except the projects directory, which has
  its own section. A `README.md` at the top of `docs/` moves to `docs/readme/`
  so the repository README keeps `docs/`.

A repository with no projects opens on Docs, and the menu leaves out any
section with nothing in it. Search still covers every document and labels
each result as a project or a doc.
