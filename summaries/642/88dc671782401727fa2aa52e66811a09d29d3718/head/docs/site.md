# Set up the Projector site

Projector can host a site for a repository on GitHub Pages, built from content
Projector keeps in the repository itself. Its menu has four sections:
**Projects**, the home page, lists the projects under `docs/projects` with
each one's priority, and its owner when any plan names one, beside the plans
that open pull requests propose, each marked **Proposed**;
**Reviews** lists the pull request summaries the `summarize-pr` skill
publishes; **Artifacts** lists versioned interactive pages; and **Docs** renders `README.md` beside a sidebar of every other
document under `docs/`. A document is Markdown, which the site renders, or an
HTML page, which it shows as it is inside the site, with the files beside it,
so an interactive explorer or a WebAssembly playground works there as it does
from disk. Summaries sit
on hidden refs such as `refs/projector/summaries`, which are not branches,
so publishing one adds no branch, no pull request banner, and nothing to
anyone's clone. One workflow on the default branch builds and deploys the
site when a summary is published, when `README.md` or `docs/` changes, and
when a pull request closes. Set a
repository up once, with admin rights, from a checkout of it whose `origin`
is the GitHub repository:

1. Run `init`:

   ```sh
   project init
   ```

   Besides adopting the convention, it turns on GitHub Pages with GitHub
   Actions as its source, and points the repository's website link at the
   site if the link is empty. To serve the site at a custom domain, pass it
   as `--url projects.example.com`; the website then links there even if it
   linked somewhere else. A repository that already deploys its own
   Pages site keeps it unless you pass `--site`. If the repository is private, it makes the site private too, and
   skips the workflow if GitHub refuses: a private repository's Pages site
   is public unless the account has private Pages, which needs GitHub
   Enterprise Cloud. Then it writes the workflow. When it cannot set the site
   up, for example because you are not an admin, it says why on stderr and
   adopts the repository anyway; pass `--site` to make that an error.
   Without admin rights, it still writes the workflow, and prints the `gh`
   commands an admin runs to finish the setup. Run it again at any time; it
   changes only what is out of date.

2. Commit the workflow to the default branch through a pull request. GitHub
   runs the dispatched workflow only from the default branch, and deploys
   from the default branch without any change to the `github-pages`
   environment. `init` writes `.github/workflows/projector-site.yml`,
   which `project site workflow --write` also writes on its own:

   ```yaml
   name: Projector site
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

   The push paths include the workflow itself, so the merge that adds it
   deploys the site right away, and `.projector.toml`, so a new
   `site.prepare` takes effect.

   A pull request that closes, merged or not, deploys the site too, so the
   Reviews section shows it as merged or closed at once. GitHub runs a
   `pull_request_target` workflow from the default branch, on the default
   branch, so the deploy needs no change to the `github-pages` environment,
   and the action checks out the default branch, never the pull request's
   code. A pull request from a fork skips the deploy, and its run takes a
   concurrency group of its own, so it never replaces a deploy waiting in
   `pages`. `project check` warns while the workflow is an earlier shape
   that Projector wrote, and `project init` or
   `project site workflow --write` rewrites it.

   `@v0` follows Projector's compatible releases. Pin an exact tag such as
   `@v0.5.0`, or a full commit SHA, to change only when you choose, with
   `--action-ref`.

   A page the repository generates rather than commits, such as an HTML
   explorer or a WebAssembly module a playground loads, needs building before
   the site. Name the command that builds it as `site.prepare` in the
   repository's `.projector.toml`, where the deploy and `project site serve`
   both find it:

   ```sh
   project config set site.prepare "make docs-wasm"
   ```

   That writes this to the file, which you can also edit by hand:

   ```toml
   [site]
   prepare = "make docs-wasm"
   ```

   The build runs it in the action's own checkout just before building the
   site. Set up its toolchain in steps before the action, and add the
   generator's inputs to the push `paths` so a change to them redeploys:

   ```yaml
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-go@v5
           with:
             go-version-file: go.mod
         - id: site
           uses: ninjudd/projector/actions/site@v0
   ```

   Running `init` again keeps a workflow you edited this way, and says so,
   rather than overwriting your steps; it updates only a workflow Projector
   wrote. `project site workflow` prints the current one to compare, and
   `project site workflow --write --force` replaces yours.

   The action's `prepare` input runs a different command in its place. On
   your own machine, `project site serve` runs the command only after you
   allow it once with `--allow-prepare`, so previewing a branch or a clone
   you have not read never runs its code unasked.

3. Publish something. Once the workflow is on the default branch, the
   `summarize-pr` skill publishes to the site by default: ask your agent
   for a summary of a pull request and it pushes the summary to the hidden
   ref, starts the workflow, and hands you the link. Until then, it keeps
   the summary in your checkout's own `refs/projector/summaries` and pushes
   nothing, so you can preview summaries with `project site serve` before
   the site is set up. Ask for a Claude Artifact instead when you want a
   private page. The site's Reviews section lists every summary, and each
   pull request's newest version is at
   `reviews/<number>/`. The projects and the docs appear on the first
   deploy, without publishing anything.

## Serve the site without GitHub Pages

You don't need GitHub Pages to read the site. From a checkout, `project site
serve` builds the same site a deploy builds and serves it on your machine,
rebuilding it whenever `README.md`, `docs/`, or the plans change:

```sh
project site serve
```

It fetches the published summaries from `origin` first, and its Reviews
menu lists them beside the summaries this checkout keeps in its own
`refs/projector/summaries`. `project summary publish` keeps a summary there,
pushing nothing, while the repository does not host its site, and
`project summary publish --local` does the same where it does, so you can
preview a summary before anyone else sees it. To host
the site somewhere other than GitHub Pages, run `project site build --out
DIR --base PATH` and serve `DIR` from any static host that answers a missing
path with `404.html`.

## Republish summaries stored as `spec.json`

Older releases stored each summary as `spec.json`, and the site reads only
`summary.json`. The site leaves out each summary stored only as `spec.json`,
and the deploy reports it as skipped. To show one again, upgrade Projector,
then copy the stored summary and publish it under the new name:

```sh
project upgrade
project summary init --repo <owner>/<repo> --pr <number> --head <head> \
  --summary summary.json --from-published
project summary publish --summary summary.json
```

`init --from-published` reads the head's `spec.json` when no `summary.json`
is beside it. `publish` stores `summary.json` beside the old file, and the
next deploy shows the summary. The site dates a republished summary by its
`spec.json`, when its head was first published, so you can republish a pull
request's heads in any order.

## Update a site set up for walkthroughs

Summaries were once called walkthroughs, and a repository set up then has a
workflow that listens for the `projector-walkthroughs` event and summaries on
`refs/projector/walkthroughs`. Its workflow keeps deploying:
`project summary publish` sends both `projector-summaries` and
`projector-walkthroughs`, and the site action reads the old ref until the new
one exists. Every summary on the old ref is stored as `spec.json`, so the site
leaves each one out and the deploy reports it as skipped. The first
`project summary publish` creates `refs/projector/summaries` from the old
ref's summaries and history, and leaves the old ref in place. Then republish
each old summary you want to show, as
[Republish summaries stored as `spec.json`](#republish-summaries-stored-as-specjson)
describes.

To finish moving, regenerate the workflow so it listens for the new event,
and commit it to the default branch through a pull request:

```sh
project site workflow --write
```

Once `refs/projector/summaries` exists, delete the old ref:

```sh
git push origin :refs/projector/walkthroughs
```

## Create and version artifacts

Ask the `artifact` skill to make an interactive explanation, chart, diagram,
or small tool. In Claude Code, use `/projector:artifact`; in Codex, use
`$artifact`. The skill builds a static HTML bundle, previews its controls,
and publishes to the repository's configured site. The Artifacts section
lists each artifact and offers its saved versions.

To author a bundle yourself, create a new working directory:

```sh
project artifact init cache-explorer --dir /tmp/cache-explorer --title 'Cache explorer'
```

Edit `index.html` and add every supporting file to `artifact.json`:

```json
{
  "schema": 1,
  "id": "cache-explorer",
  "title": "Cache explorer",
  "description": "Compare cache policies with adjustable inputs.",
  "entry": "index.html",
  "files": ["index.html", "cases.json"]
}
```

Use relative URLs for scripts, styles, data, images, and WebAssembly. Compile
framework source before publication and list any editable source needed for
later revisions. Pages serves finished files and runs no server or build.
The explicit file list excludes unrelated working files. Each bundle holds
at most 1,000 files, 25 MiB per file, and 50 MiB total. Files must be regular,
with no hidden components, symlinks, traversal, or case collisions.

Preview without saving, then publish:

```sh
project site serve --artifacts /tmp/cache-explorer --no-fetch
project artifact publish --dir /tmp/cache-explorer
```

Open `artifacts/cache-explorer/` under the preview address. Stop the preview
server before running the next command in the same terminal. Publication
saves source and exact asset bytes on `refs/projector/artifacts`, without
changing branches, your index, or checkout files. It prints a latest link at
`artifacts/cache-explorer/` and a fixed link at
`artifacts/cache-explorer/<version>/`. The full content digest identifies a
version. Changes to metadata or bytes create new versions; republishing saved
content adds no version and leaves latest unchanged. Every saved version
remains reachable after later edits.

Publication requests the existing site workflow on its default branch. The
page updates when that workflow succeeds. `--no-dispatch` saves remotely
without requesting a deploy. If dispatch fails, content remains saved and
the command prints a workflow retry command. Repositories pinned to an exact
site action release or commit need an action version that supports artifacts;
update that pin through a pull request first. The compatible `@v0` action
picks up artifact support with its release.

List artifacts and recover source for an edit:

```sh
project artifact list --json
project artifact list --id cache-explorer --json
project artifact init cache-explorer --dir /tmp/cache-revision --from-published
```

Saved artifacts also appear in the [session panel](plugins.md#use-saved-artifacts-in-the-session-panel)
with their saved descriptions. Open a row to view or discuss its artifact,
or dismiss it from that session without deleting any version.
`project artifact open cache-explorer` opens the saved
artifact directly; add `--print-url` to print the address without launching
a browser. Local artifacts use a temporary preview server that expires
after 15 minutes without a request.

The recovery directory must not exist. Add `--version FULL_DIGEST` to recover
a fixed version, or `--no-fetch` to use cached data. Add `--checkout CHECKOUT`
or `--remote REMOTE` to select the repository or remote explicitly.

Without safe configured hosting, publication saves only in the checkout's
local ref. `--local` does this even when hosting exists. Normal
`project site serve` combines local and fetched remote versions and marks
local latest versions as previews. It notices subsequent ref changes without
a restart. The Artifacts directory and site search index the latest version;
older versions stay available through the picker and fixed links.

Artifacts use the site's existing audience. GitHub write access controls
publication; GitHub Pages controls reading, with no per-artifact sharing.
Private and internal repositories require confirmed private Pages. Hidden
refs are visible to repository readers. Published HTML and its scripts have
the same origin and trust as the site's other HTML pages. Credentials do not
belong in a bundle. A deploy repeats the site's visibility check for all
versions and assets.

`project site build` reads artifacts directly from the checkout's local and
fetched refs. The site action fetches the artifact ref separately from review
summaries, without checking out or running the bundle. To build from a saved
directory instead, pass `--artifacts DIR`, where `DIR` contains
`<id>/<version>/artifact.json`, `revision.json`, `files/`, and each ID's
`latest.json`. Invalid versions are reported and skipped; an invalid latest
pointer leaves that artifact out of the directory while keeping valid fixed
links. The build reports total artifact bytes across retained versions, which
grow with history.

## Verify artifact pages

You can check the artifact UI with disposable Git repositories and a fresh
browser profile. From the repository root, with Node.js and Chrome installed:

1. Start the preview under a project-site prefix:

   ```sh
   PYTHONPATH=src python3 -m tests.artifact_preview --port 8765 --base /demo/
   ```

2. In another terminal, start a preview at the site root:

   ```sh
   PYTHONPATH=src python3 -m tests.artifact_preview --port 8766 --base /
   ```

3. In a third terminal, install the browser test dependency outside the
   checkout and run the check:

   ```sh
   npm install --prefix /tmp/projector-artifact-ui --no-package-lock --no-audit --no-fund playwright
   PROJECTOR_PLAYWRIGHT_DIR=/tmp/projector-artifact-ui PROJECTOR_BROWSER_CHANNEL=chrome node tests/artifact_ui.mjs http://127.0.0.1:8765/demo/ http://127.0.0.1:8766/
   ```

The check prints `passed: true` for both addresses and saves desktop and
phone screenshots in the temporary directory it prints. It exercises the
directory filter, both versions, relative JSON, SVG and binary assets,
keyboard controls, live hash changes, navigation, search, and full-screen
controls. An assertion, timeout, page error, or failed local request fails
the command. Stop both servers with Ctrl-C to remove their temporary
repositories and builds. The dependency directory and screenshots remain
available for inspection. To use Playwright's installed Chromium instead,
omit `PROJECTOR_BROWSER_CHANNEL`.
