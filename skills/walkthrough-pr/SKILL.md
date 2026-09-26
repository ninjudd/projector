---
name: walkthrough-pr
description: Build a guided, grouped walkthrough page for reviewing a large GitHub pull request, with each logical change explained, a checklist per group, and every hunk syntax-highlighted in reading order. Use when the user asks for help walking through, understanding, or reviewing a big PR diff.
---

# Pull Request Walkthrough

Turn a pull request into one page a reviewer reads top to bottom: the diff
split into logical groups in reading order, each group opened by what it
does and why, what to hold in mind, and a checklist, followed by that
group's files with every hunk. The page framework is fixed. Your job is the
judgment: the grouping, the explanations, and the checks.

Reading a pull request does not authorize changing it. Do not comment on,
push to, or approve the pull request unless the user asks.

## Build the spec

1. Resolve the repository (`owner/name`) and pull request number from the
   user's request or the current checkout.
2. Write a skeleton spec. It records the pull request's head and lists every
   changed file with its line counts:

   ```sh
   python3 <skill-dir>/scripts/walkthrough.py init \
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
python3 <skill-dir>/scripts/walkthrough.py build \
  --spec WORKDIR/walkthrough.json --out WORKDIR/site
```

`build` fetches the diff from GitHub's compare API for the spec's merge base
(`pr.base`) and head, and refuses to run when the pull request's head is no
longer the spec's `pr.head`, so the page never describes a diff it does not
show. When GitHub cannot serve the diff because the pull request is too
large, produce it locally and pass it with `--diff`:

```sh
git fetch origin BASE HEAD_SHA
git diff "$(git merge-base origin/BASE HEAD_SHA)" HEAD_SHA > WORKDIR/pr.diff
```

The output directory holds `index.html` with the data embedded, plus the
shared renderer, `walkthrough.js` and `walkthrough.css`. Opening
`index.html` from disk works in any browser.

## Publish the page

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

A repository can host its own walkthroughs: specs live on an orphan
`projector-pages` branch, and a workflow on that branch builds and deploys
them with Projector's shared action. Publishing writes to the repository, so
do it only when the user asks or repository instructions say to.

```sh
python3 <skill-dir>/scripts/walkthrough.py publish --spec WORKDIR/walkthrough.json
```

`publish` commits the spec to `walkthroughs/<number>/<head>/spec.json` on the
branch without touching the checkout, and on first use creates the branch
with `.github/workflows/walkthroughs.yml`. The workflow calls
`ninjudd/projector/actions/walkthroughs@v1`; pass `--action-ref` to pin a
different tag or a full commit SHA. The site lists every walkthrough at its
root, serves each pull request's newest head at `/<number>/`, and links the
older heads from each page. The spec on the branch is the durable copy: to
update a walkthrough later, start from it rather than from a fresh `init`.

Setting the repository up needs admin rights, once:

```sh
gh api -X POST repos/OWNER/NAME/pages -f build_type=workflow
gh api -X POST repos/OWNER/NAME/environments/github-pages/deployment-branch-policies \
  -f name=projector-pages -f type=branch
```

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
