# Set up the Projector site

Projector can host a site for a repository on GitHub Pages, built from content
Projector keeps in the repository itself. Its menu has three sections:
**Projects**, the home page, lists the projects under `docs/projects`;
**Reviews** lists the pull request walkthroughs the `walkthrough-pr` skill
publishes; and **Docs** renders `README.md` beside a sidebar of every other
document under `docs/`. A document is Markdown, which the site renders, or an
HTML page, which it shows as it is inside the site, with the files beside it,
so an interactive explorer or a WebAssembly playground works there as it does
from disk. Walkthroughs sit
on hidden refs such as `refs/projector/walkthroughs`, which are not branches,
so publishing one adds no branch, no pull request banner, and nothing to
anyone's clone. One workflow on the default branch builds and deploys the
site when a walkthrough is published or `README.md` or `docs/` changes. Set a
repository up once, with admin rights, from a checkout of it whose `origin`
is the GitHub repository:

1. Run `init`:

   ```sh
   project init
   ```

   Besides adopting the convention, it turns on GitHub Pages with GitHub
   Actions as its source, and points the repository's website link at the
   site if the link is empty. A repository that already deploys its own
   Pages site keeps it unless you pass `--site`. If the repository is private, it makes the site private too, and
   skips the workflow if GitHub refuses: a private repository's Pages site
   is public unless the account has private Pages, which needs GitHub
   Enterprise Cloud. Then it writes the workflow. When it cannot set the site
   up, for example because you are not an admin, it says why on stderr and
   adopts the repository anyway; pass `--site` to make that an error. Run it
   again at any time; it changes only what is out of date.

2. Commit the workflow to the default branch through a pull request. GitHub
   runs the dispatched workflow only from the default branch, and deploys
   from the default branch without any change to the `github-pages`
   environment. `init` writes `.github/workflows/projector-site.yml`,
   which `project site workflow --write` also writes on its own:

   ```yaml
   name: Projector site
   on:
     repository_dispatch:
       types: [projector-walkthroughs]
     push:
       branches: [main]
       paths: [README.md, 'docs/**']
     workflow_dispatch:
   permissions:
     contents: read
     pull-requests: read
     pages: write
     id-token: write
   concurrency:
     group: pages
     cancel-in-progress: false
   jobs:
     publish:
       runs-on: ubuntu-latest
       environment:
         name: github-pages
         url: ${{ steps.site.outputs.page_url }}
       steps:
         - id: site
           uses: ninjudd/projector/actions/site@v0
   ```

   `@v0` follows Projector's compatible releases. Pin an exact tag such as
   `@v0.5.0`, or a full commit SHA, to change only when you choose, with
   `--action-ref`.

   A page the repository generates rather than commits, such as an HTML
   explorer or a WebAssembly module a playground loads, needs building before
   the site. Name the command that builds it as `site.prepare` in
   `.projector.toml`, where the deploy and `project site serve` both find it:

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

   The action's `prepare` input runs a different command in its place. On
   your own machine, `project site serve` runs the command only after you
   allow it once with `--allow-prepare`, so previewing a branch or a clone
   you have not read never runs its code unasked.

3. Publish something. Once the workflow is on the default branch, the
   `walkthrough-pr` skill publishes to the site by default: ask your agent
   for a walkthrough of a pull request and it pushes the spec to the hidden
   ref, starts the workflow, and hands you the link. Ask for a Claude
   Artifact instead when you want a private page. The site's Reviews section
   lists every walkthrough, and each pull request's newest version is at
   `reviews/<number>/`. The projects and the docs appear on the first
   deploy, without publishing anything.

## Serve the site without GitHub Pages

You don't need GitHub Pages to read the site. From a checkout, `project site
serve` builds the same site a deploy builds and serves it on your machine,
rebuilding it whenever `README.md`, `docs/`, or the plans change:

```sh
project site serve
```

It fetches the published walkthroughs from `origin` first, so the Reviews menu
matches what the repository has published. Publish a walkthrough without
starting a deploy with `project walkthrough publish --no-dispatch`. To host
the site somewhere other than GitHub Pages, run `project site build --out
DIR --base PATH` and serve `DIR` from any static host that answers a missing
path with `404.html`.
