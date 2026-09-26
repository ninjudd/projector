---
status: in-progress
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
- A push to the default branch that changes `README.md` or `docs/` rebuilds
  the site, as publishing a walkthrough does.

## 3. How the site is built

`project site build --out DIR --repo-root CHECKOUT [--walkthroughs DIR]`
writes a static site. It reads plans through the CLI's own project model,
honoring a configured `projects.dir`, so the site and `project list` never
disagree about what a project is. It copies every Markdown file it serves,
unchanged, under `content/`, and writes `site.json`: the repository and
branch, the README and docs with their titles, each project's frontmatter
fields and supplemental files, and each walkthrough's summary. The home page
is one shell, `index.html`, whose `site.js` fetches `site.json` and renders
each view on the client, routing on the URL's hash: `#/`, `#/projects`,
`#/projects/<name>`, `#/prs`, and `#/<path>` for a document.

Walkthrough pages keep their URLs, `/<number>/<head>/`, and their link back
now opens `#/prs`.

The composite action passes its checkout as `--repo-root`. A repository that
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
- **One page, hash routes.** GitHub Pages serves static files and has no
  rewrites, so a path per view would need a generated HTML file per document
  and per project. A hash route needs one shell and keeps every view a plain
  link.
- **Copy Markdown, do not pre-render it.** The deploy stays a copy plus one
  manifest, in line with walkthroughs (`pr-walkthrough` section 8), and a
  reader always sees the committed text.
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
