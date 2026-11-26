---
name: artifact
description: Create or revise a static interactive page, visual explanation, chart, diagram, or small tool, with saved versions on the repository's Projector site. Use when the user asks to make an artifact or edit a published one. Do not use for backend applications or ordinary source-code changes.
---

# Make an artifact

Build a finished interactive page for the user's question and publish it on
the repository's Projector site. Artifacts live on a hidden Git ref, with a
stable latest link, permanent version links, and a directory on the site.
Use the `project` CLI shipped with Projector. If it rejects an artifact
command as an invalid choice, run `project upgrade` and retry.

## Create or recover the source

Resolve the repository from the request or the checkout, and read its
publication instructions. Use a short, stable lowercase ID such as
`cache-explorer`. A later revision keeps the same ID. List existing artifacts
when the user refers to one without giving its ID:

```sh
project artifact list --json
project artifact list --id cache-explorer --json
```

Keep source in a dedicated working directory you can reach again. Create a
new bundle, or recover the published source before revising an existing one:

```sh
project artifact init cache-explorer --dir WORKDIR --title 'Cache explorer'
project artifact init cache-explorer --dir WORKDIR --from-published
```

Choose one of those commands. The output directory must not exist. To recover
an older version, add `--version FULL_VERSION_ID`. Recovery and listing fetch
first; `--no-fetch` reads cached versions offline. Add `--checkout CHECKOUT`
when your shell is outside the target repository.

## Build and preview

Author `index.html` and its supporting files. Use the representation and
controls that help answer the question: a diagram, a chart with adjustable
inputs, an explorer, or a small tool. Label controls, support keyboard use,
provide readable contrast, and make the layout work on a phone. Explain the
meaning of inputs and results inside the page.

`artifact.json` records `schema: 1`, the `id`, a `title`, a `description`,
`entry: "index.html"`, and `files`, an explicit list of paths relative to the
bundle directory. List every file the page needs, including local data and
binary assets; omit `artifact.json` itself. Use relative URLs. Compile any
framework source before publishing and list the editable source too when
future edits need it. The host runs no build commands or server code.

The bundle accepts at most 1,000 regular files, 25 MiB per file, and 50 MiB
total. Hidden files, symlinks, traversal, and case-colliding paths are refused.
Keep credentials and source material outside the intended audience out of
the bundle. Reading a pull request does not authorize publishing its code.

Preview in the repository's site, without writing a ref:

```sh
project site serve --artifacts WORKDIR --no-fetch
```

Open `artifacts/ID/` under the printed address. Exercise the actual controls,
relative data fetches, keyboard interaction, phone layout, and any deep-link
state. Fix console errors and failed requests before publication. Do not add
or allow a `site.prepare` command merely to preview the bundle. Stop a server
you started when it is no longer needed.

## Save and hand over

An artifact request publishes to an already configured Projector site by
default. A request for local preview stays local. Run publication directly,
as a tool call of its own, with no `cd`, wrapper, or chained command:

```sh
project artifact publish --dir WORKDIR
```

Add `--checkout CHECKOUT` when needed. Use `--local` when the user wants a
local saved version. The CLI validates the bundle before writing anything.
Where hosting is absent, cannot be checked, or would expose private content,
it saves locally and pushes nothing. Tell the user the preview route and
point them to Projector's site setup instructions. Do not enable Pages or
change its audience unless asked.

Give the user the latest link and the saved version's link, or the local
preview route. A hosted publish requests a deploy; the page is available
after that workflow succeeds. Check its result before calling it deployed.
If dispatch fails, the CLI distinguishes saved content from deployment and
prints the workflow retry command. Report that state; do not manufacture a
new version to retry a deploy.

Every changed bundle saves a new content-based version. Republishing existing
content adds no version and does not move latest backwards. The site's
Artifacts directory and version picker expose the saved history. GitHub
repository write permissions control publishing, and GitHub Pages controls
who can read all versions. A hidden ref is not a separate privacy boundary.

Saved artifacts appear in the session panel, with Open and Dismiss actions.
Dismiss hides the row from that session without deleting saved versions or
published links. Use `project artifact restore ID --session SESSION` when
the user asks to bring a dismissed row back. `project artifact open ID`
opens a saved artifact on its site, or in a temporary local preview when
hosting is unavailable.

Follow `../writing.md` for prose in the artifact and its hand-over. For the
file format, commands, and site access details, read the repository's
`docs/site.md` when working in Projector itself, or the installed Projector
site documentation otherwise.
