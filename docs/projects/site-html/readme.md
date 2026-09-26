---
status: completed
priority: now
---

# Serve HTML pages in the site

## 1. Outcome

The site shows HTML pages as well as Markdown, in Docs and in projects, so a
repository can publish an interactive page beside its prose: a generated
explorer such as EP3's protocol explorer, or a playground that runs real code
through WebAssembly, such as the name-matcher playground in
us-exchange-monorepo#1758. The page works on the site as it does from disk,
inside the site's menu, and the files it loads come with it.

## 2. Acceptance criteria

- Every `.html` file under `docs/` or the projects directory is a page, listed
  in its section's sidebar, titled by its `<title>`, at its path without
  `.html`. An `index.html` takes its folder's path when no readme does.
- The page shows the HTML in a frame the width of the window, with the section
  sidebar collapsed behind a toggle, and links to the page on its own.
- Every other file under `docs/` and the projects directory is copied beside
  the page, so its relative `fetch()`, script, and WebAssembly loads resolve.
- A deep link's hash reaches the page, and the page's own hash changes show in
  the address, so a hash-routed page can be linked into.
- Search covers an HTML page's title and visible text, not its scripts.
- A repository whose pages are generated rather than committed can build them
  in the site workflow before the site build.

## 3. Design

**The frame shows the copy under `content/`.** The build already copied every
non-Markdown file under `docs/` there, so an HTML page and its siblings were
reachable; the page was only missing from the sidebar, the routes, and search.
Showing that copy in a frame keeps the page's relative URLs pointing at its
siblings, keeps its scripts and styles apart from the site's, and leaves the
page unchanged, so a page that works from disk works here. The build now
copies the projects directory's other files too, for a playground that lives
in a project folder outside `docs/`.

**HTML carries the trust of merged code.** The site sanitizes Markdown, but it
serves HTML as written, since an interactive page is the point. A sandboxed
frame would add nothing, because the same file is reachable at its `content/`
address outside the frame. The page carries the same trust as any code merged
to the default branch, and the visibility check that keeps a private
repository's site private still applies.

**A page that wants the whole window gets it.** An explorer or a playground
needs width more than the section's tree, so an HTML page starts with the
sidebar collapsed behind a toggle and the frame sized to the window. Markdown
pages keep the sidebar open.

**Routes follow the Markdown rules.** `local_route` strips `.html` as it
strips `.md`, and an `index.html` wants its folder's route as a readme does.
The readme claims first. A page whose route is taken keeps its whole name,
except an `index.html`, which moves to `index/`, because `index.html/` would
be a folder beside the shell page the folder's own route already wrote.

**Which files are pages is decided by extension alone.** A template or a
fragment named `.html` would become a broken page, so a repository names such
a file otherwise, as EP3 will its explorer's `template.html`. No exclude
setting was needed for the one case in view.

**The action runs a `prepare` command.** EP3's explorer and the playground's
WebAssembly module are built, not committed. `actions/site` checks the
repository out itself, and that checkout removes untracked files, so a file a
step before the action generated would be gone by the build. The action's
`prepare` input runs a shell command after that checkout and before the build.
The command's toolchain, such as Go or helm, is set up in steps before the
action, which survive the checkout. `project site workflow` still writes the
plain workflow; a repository that needs `prepare` edits its copy.

## 4. Outcome

Shipped. Every criterion in section 2 holds. `tests/test_site.py` covers an
HTML doc's title, route, and copy, an `index.html` taking a free folder route,
a playground in a nested project with its JSON and WebAssembly siblings, the
`index/` fallback beside a readme, the search text, and the `prepare` step's
place between the checkout and the build. In Chrome, against a demo served by
`project site serve`, a hash-routed page opened at a deep link and wrote its
own hash back to the address, the toggle opened the sidebar, and a playground
fetched its `cases.json` and instantiated its `.wasm` module inside the frame.
