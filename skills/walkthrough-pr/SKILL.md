---
name: walkthrough-pr
description: Build a guided, grouped walkthrough page for reviewing a large GitHub pull request, with each logical change explained, a checklist per group, and every hunk syntax-highlighted in reading order. Use when the user asks for help walking through, understanding, or reviewing a big PR diff.
---

# Pull Request Walkthrough

Turn a pull request into one page a reviewer reads top to bottom: the diff
split into logical groups in reading order, each group opened by what it
does and why, what to hold in mind, and a checklist, followed by that
group's files with every hunk. This skill writes the page's data, a
walkthrough spec. The Projector CLI renders it and the repository's Projector
site serves it, so your job is the judgment: the grouping, the explanations,
and the checks. Every command below is the `project` CLI that ships beside
this skill; install it as Projector's README describes when `project` is
missing. The CLI updates separately from this skill, so when `project`
rejects a command below as an invalid choice, run `project upgrade` and
retry.

Reading a pull request does not authorize changing it. Do not comment on,
push to, or approve the pull request unless the user asks.

## Build the spec

1. Resolve the repository (`owner/name`) and pull request number from the
   user's request or the current checkout.
2. Write a skeleton spec. It records the pull request's head and lists every
   changed file with its line counts:

   ```sh
   project walkthrough init \
     --repo OWNER/NAME --pr NUMBER --spec WORKDIR/walkthrough.json
   ```

   Keep `WORKDIR` somewhere you can reach again this session, and put it in
   the host's scratch directory when you will publish to Claude Artifacts,
   which accepts supporting files only from the working directory or the
   scratch directory. The spec is what you edit when the pull request moves.
3. Read enough to explain the change: the pull request body, the full diff
   (`gh pr diff NUMBER --repo OWNER/NAME`), plan or design documents the
   change touches, and the review threads. For a large diff, delegate the
   reading to subagents and keep only their conclusions.
4. Fill the spec as `spec.md` describes. The groups decide whether the page
   helps:
   - **Order groups from the contract outward.** API and schema first, then
     the shared core, then persistence and configuration, then adapters, then
     the callers that drive it all, then tooling and documentation.
   - **Put every changed file in exactly one group.** `build` refuses a spec
     that misses a file, repeats one, or names one that is not in the diff.
   - **Explain why, not only what.** Cite the plan decisions or review
     threads behind a design choice.
   - **Write checks a reviewer can act on.** A `verify` check names an
     invariant worth tracing. A `flag` check is your own observation that
     needs a decision; confirm it against the code before you write it, and
     say plainly what is wrong or risky.
   - **Collapse noise.** Generated code, tests and documentation start
     collapsed unless a file's `collapsed` says otherwise. Give the files a
     reviewer must read a one-line `note`.
   - **Use the overview for the few facts every group depends on**, such as
     a decision table, the configuration surface, what earlier review rounds
     settled, and what the pull request leaves for later.

## Build the page

```sh
project site page --spec WORKDIR/walkthrough.json --out WORKDIR/site
```

`page` fetches the diff from GitHub's compare API for the spec's merge base
(`pr.base`) and head, and refuses to run when the pull request's head is no
longer the spec's `pr.head`, so the page never describes a diff it does not
show. When GitHub cannot serve the diff because the pull request is too
large, produce it locally and pass it with `--diff`. The head check still
runs; only when `gh` cannot reach GitHub does an offline `--diff` build go
ahead with a warning, because it cannot tell whether the pull request moved:

```sh
git fetch origin BASE HEAD_SHA
git diff "$(git merge-base origin/BASE HEAD_SHA)" HEAD_SHA > WORKDIR/pr.diff
```

The output directory holds `index.html` with the data embedded, plus the
site's renderer files beside it. Opening `index.html` from disk works in any
browser.

## Publish the page

Publish to the repository's own site when it hosts walkthroughs, and to a
Claude Artifact otherwise. Ask which applies:

```sh
project site status --repo OWNER/NAME --pr NUMBER
```

- **Exit 0** prints the walkthrough's URL. The repository is set up: it has
  the walkthroughs workflow on its default branch and a Pages site. Setting
  that up was the reviewed decision to host walkthroughs there, so publish to
  the site as the next section describes without asking again, and hand over
  the printed URL.
- **Exit 3** prints why the repository is not set up. Publish an Artifact,
  and tell the user in one sentence that the repository can host its own
  walkthroughs, pointing at the "Set up Projector hosting" section of
  Projector's README.
- **Any other exit** means the check itself failed, for example because
  `gh` could not reach GitHub. Publish an Artifact and say the hosting check
  did not run.

An explicit request wins either way. Publish an Artifact when the user asks
for one or says not to publish, and publish to a site the user names even
where `status` would have chosen otherwise.

### Publish to a Claude Artifact

With Claude Artifacts, publish `index.html` and pass the two renderer files
through `files`, so the page loads them by relative path:

- `file_path`: `WORKDIR/site/index.html`
- `files`: `{"walkthrough.js": "WORKDIR/site/walkthrough.js", "walkthrough.css": "WORKDIR/site/walkthrough.css"}`
- `icon`: `code` on the first publish
- `description`: one sentence naming the pull request

The renderer already meets the Artifact page contract: a named `<title>`,
light and dark themes from tokens, scripts only from cdnjs, fonts only from
Google Fonts, and a layout that works at phone width. An artifact starts
private, and only its owner can share it, from the page's Share menu. Tell
the user that when you hand over the link.

Without Artifacts, give the user the path to `WORKDIR/site/index.html`.

### Publish to the repository's GitHub Pages site

A repository can host its own walkthroughs. Specs live on the hidden ref
`refs/projector/walkthroughs`, which is not a branch: GitHub lists no branch
and offers no pull request for it, and clones do not fetch it. A workflow on
the default branch builds and deploys them with Projector's shared action.
Publishing writes to that hidden ref, which a repository accepts by setting
hosting up; do it when `status` exits 0 or the user asks.

```sh
project walkthrough publish --spec WORKDIR/walkthrough.json
```

`publish` first builds the spec against its diff and refuses one that does
not build, then commits it to `walkthroughs/<number>/<head>/spec.json` on the
ref without touching the checkout and sends a `repository_dispatch` event that
starts the workflow. The site build reports and skips any spec that still
fails, or that names another repository, so one bad spec costs one
walkthrough rather than the deployment. The site lists every walkthrough at its root,
serves each pull request's newest head at `/<number>/`, and links the older
heads from each page. The spec on the ref is the durable copy: to update a
walkthrough later, fetch it with
`git fetch origin refs/projector/walkthroughs` and start from it rather
than from a fresh `init`.

Setting a repository up is once, with admin rights. Enable Pages with the
GitHub Actions source, then add the workflow to the default branch through a
pull request, because GitHub runs dispatched workflows only from there:

```sh
gh api -X POST repos/OWNER/NAME/pages -f build_type=workflow
project site workflow --write
```

The workflow calls `ninjudd/projector/actions/walkthroughs@v0`. Pass
`--action-ref` to pin an exact release tag or a full commit SHA instead.

A private repository's Pages site is public unless the account has private
Pages (GitHub Enterprise Cloud). For a private repository, make the site
private first and stop if GitHub refuses:

```sh
gh api -X PUT repos/OWNER/NAME/pages -F public=false
```

## Update the page when the pull request moves

1. List what changed since the spec's head:
   `git log --oneline OLD_HEAD..NEW_HEAD` and
   `git diff OLD_HEAD NEW_HEAD`. A rebase makes the old head unreachable;
   compare against the new merge base instead.
2. Read the new commits and any new review threads, then revise the spec:
   set `pr.head`, move new files into groups, drop files that left the diff,
   and rewrite every note, check and overview card the change made untrue.
   Resolve or remove `flag` checks the new commits fixed.
3. Rebuild, then republish to the same artifact: publish the same file path
   again in the conversation that created it, or pass the artifact's URL as
   `url` after reading it from any other conversation. On a Pages site,
   `publish` the revised spec; it becomes a new version beside the old one.

Summarize for the user what changed in the pull request and which parts of
the page moved.
