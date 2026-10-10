---
name: summarize-pr
description: Summarize a GitHub pull request's changes as a guided review page, grouped in reading order, with each logical change explained, review notes on each group, file and line, and every hunk syntax-highlighted. Use when the user asks for help walking through, understanding, summarizing, or reviewing a big PR diff.
---

# Summarize a pull request's changes

Summarize a pull request's changes as one guided page a reviewer reads top
to bottom: the diff split into logical groups in reading order, each group
opened by what it does and why, followed by that group's files with every
hunk, and review notes on each group, file and line. This skill writes the
page's data, a summary. The Projector CLI renders it and the repository's
Projector site serves it, so your job is the judgment: the grouping, the
explanations, and the notes. Every command below is the `project` CLI that ships beside
this skill; install it as Projector's README describes when `project` is
missing. The CLI updates separately from this skill, so when `project`
rejects a command below as an invalid choice, run `project upgrade` and
retry.

Reading a pull request does not authorize changing it. Do not comment on,
push to, or approve the pull request unless the user asks.

## Build the summary

1. Resolve the repository (`owner/name`) and pull request number from the
   user's request or the current checkout.
2. Write a skeleton summary. It records the pull request's head and lists every
   changed file with its line counts:

   ```sh
   project summary init \
     --repo OWNER/NAME --pr NUMBER --summary WORKDIR/summary.json
   ```

   Keep `WORKDIR` somewhere you can reach again this session. When you will
   publish to Claude Artifacts, the Artifact tool accepts files only from the
   working directory or its own scratchpad directory, which the host names in
   its instructions. Build `WORKDIR` in that scratchpad, or else under the
   working directory. `$TMPDIR`, `/tmp`, and a background job's temporary
   directory are outside both, and the publish refuses files from them. The
   summary is what you edit when the pull request moves.
3. Read enough to explain the change: the pull request body, the full diff,
   plan or design documents the change touches, and the review threads.
   Take the diff from the summary's `pr.base` to its `pr.head` through the
   compare API, the one `page` and `publish` read, which works from any
   directory:

   ```sh
   gh api -H 'Accept: application/vnd.github.diff' \
     repos/OWNER/NAME/compare/PR_BASE...PR_HEAD > WORKDIR/pr.diff
   ```

   In a checkout whose `origin` is the repository, a local diff holds the
   same changes:

   ```sh
   git fetch origin PR_BASE PR_HEAD
   git diff PR_BASE PR_HEAD > WORKDIR/pr.diff
   ```

   Do not read it with `gh pr diff`. GitHub's pull request diff endpoint
   refuses a pull request that changes more than 300 files with HTTP 406,
   and the compare API does not share that limit. For a large diff, delegate
   the reading to subagents and keep only their conclusions. Ask each one
   for what its files did before the change and do after it, in plain
   words, the terms a newcomer would need defined, and the line numbers of
   anything worth a note, taken from inside the diff's hunks.
4. Fill the summary as `format.md` describes, and write every piece of prose in
   it for the reader that "Write for someone new to the code" below
   describes. The groups decide whether the page helps:
   - **Order groups from the contract outward.** API and schema first, then
     the shared core, then persistence and configuration, then adapters, then
     the callers that drive it all, then tooling and documentation.
   - **Put every changed file in exactly one group.** `build` refuses a summary
     that misses a file, repeats one, or names one that is not in the diff.
   - **Explain why, not only what.** Give the reason itself. A plan
     decision or review thread can back it up, but its number alone, such
     as "decision 4.19", explains nothing to someone who has not read the
     plan.
   - **Write notes a reviewer can act on.** A `context` note is what to hold
     in mind while reading. A `verify` note states an invariant you checked
     and how: a test you ran, a call you traced, a mutation that failed. The
     page labels it verified with no checkbox, so never write one you did
     not check; an invariant you want the reader to trace is a `context`
     note. A `flag` note is your own observation that needs a decision;
     confirm it against the code before you write it, and say plainly what
     is wrong or risky.
   - **Put each note at the smallest scope that holds it.** A reader cannot
     carry a list of notes across twenty files and hundreds of lines. A note
     about one line goes on its file with that `line`, and renders under
     that line in the diff. A note about one file goes on that file. Only a
     note that ties several files together stays on the group.
   - **Collapse noise.** Generated code, tests and documentation start
     collapsed unless a file's `collapsed` says otherwise, and the page's
     **Files changed** list keeps them in a closed list of their own. A file
     the repository marks `linguist-generated` in `.gitattributes` counts as
     generated, as on GitHub; `format.md` says how. Give the files a
     reviewer must read a one-line `note` that says what the file is for and
     what changed in it, not a list of the names it defines.
   - **Open the overview with orientation.** Say first what the part of the
     system this pull request touches is for, what problem the change
     solves, and how, in plain words. Then give the few facts every group
     depends on, such as a decision table, the configuration surface, what
     earlier review rounds settled, and what the pull request leaves for
     later.

## Write for someone new to the code

Follow the writing guide in `../writing.md` when you write every intro, note
and card. Write for a reviewer who may be seeing this code and this part of
the system for the first time (§ 2), with structure (§ 4), and to the reader
(§ 5). A summary also needs these:

- **Define terms in each group.** Readers jump to a group from the
  sidebar, so each group defines the terms it uses, and the overview gets a
  card listing the page's terms when there are more than a few.
- **Give a list, table or heading an item of its own.** In an intro or the
  overview, an item that starts with a block, such as
  `"<ol><li>…</li></ol>"`, renders as written rather than inside a
  paragraph. Wrap a table in `<div class="tblwrap"><table class="tbl">`,
  and split a long intro with an `<h4>`.
- **Let the kicker and the intro divide the work.** A group's `kicker` is
  its one line on what the group covers. The intro's opening sentences say
  what the group does and why, adding to the kicker rather than repeating
  it.
- **Let a note on the plan document name its section**, since that section
  is what the reader is looking at.
- **Expect the page to grow.** A summary written this way runs longer than
  one written for insiders, often by half. Cut repetition and detail no
  reviewer would act on, never the context, and keep each group intro to a
  few short paragraphs, with the details in file and line notes.

For example, instead of:

> Pass 1 scores the typed address against the carrier record with
> `addrmatch` (0.92); pass 2 reads the postal check.

write:

> When someone saves a shipping address, the service now checks that the
> address can receive a delivery. The address must pass both steps:
>
> 1. **The typed address matches a known delivery point.** The service
>    compares the address the user typed with the carrier's records, using
>    `addrmatch`, the fuzzy matcher that already checks billing addresses.
> 2. **The postal service delivers there.** The service asks the postal
>    service whether that address accepts mail.

Before you publish, reread each group intro and note as that newcomer, and
check that each has the structure its content calls for. Build as often as
you like, since building is how you learn that a note's line sits outside
the diff, but publish only once the reread is done. Where a sentence makes
sense only to someone who already knows the code, rewrite it.

## Build the page

```sh
project site page --summary WORKDIR/summary.json --out WORKDIR/site
```

`page` fetches the diff from GitHub's compare API for the summary's merge base
(`pr.base`) and head, and refuses to run when the pull request's head is no
longer the summary's `pr.head`, so the page never describes a diff it does not
show. The compare API is not the pull request diff endpoint and does not
share its 300-file limit, so a pull request that `gh pr diff` refuses still
builds without help. Pass `--diff` only when `page` itself reports that
GitHub could not serve the diff. Step 3's compare call reads the same
endpoint and has failed too then, so pass the local `git diff` from step 3,
taken in a checkout of the repository. The head check
still runs; only when `gh` cannot reach GitHub does an offline `--diff` build
go ahead with a warning, because it cannot tell whether the pull request
moved.

The output directory holds `index.html` with the data embedded, plus the
site's renderer files beside it. Opening `index.html` from disk works in any
browser.

## Publish the summary

Publish the summary to the repository's hidden summaries ref, where the
repository's Projector site reads it, whether that site is its GitHub Pages
site or `project site serve` on the user's machine. From a checkout whose
`origin` is the repository, run:

```sh
project summary publish --summary WORKDIR/summary.json
```

`publish` decides where the summary goes, and says which:

- **The repository hosts its site**, meaning the Projector site workflow is
  on its default branch and it has a Pages site. `publish` pushes the summary,
  starts the deploy, and links the summary from the pull request. Setting
  that up was the reviewed decision to host summaries there, so publish
  without asking again. Get the page's URL with
  `project site status --repo OWNER/NAME --pr NUMBER` and hand it over.
- **It does not.** `publish` commits the summary to this checkout's own
  `refs/projector/summaries` and pushes nothing, so nothing on GitHub
  changes. Tell the user to preview it with `project site serve` from that
  checkout, at `reviews/NUMBER/`, which a server already running picks up
  within seconds. Say in one sentence that the repository can host its own
  summaries, pointing at the "Set up the Projector site" section of
  Projector's README.

Pass `--local` to keep the summary in the checkout even where the site is
hosted, when the user wants to preview a summary before it goes live.
Publish a Claude Artifact instead only when the user asks for one, or when
no checkout of the repository is at hand to publish from, and say which.

### Publish to a Claude Artifact

With Claude Artifacts, publish `index.html` and pass the two renderer files
through `files`, so the page loads them by relative path:

- `file_path`: `WORKDIR/site/index.html`
- `files`: `{"summary.js": "WORKDIR/site/summary.js", "summary.css": "WORKDIR/site/summary.css"}`
- `icon`: `code` on the first publish
- `description`: one sentence naming the pull request

The renderer already meets the Artifact page contract: a named `<title>`,
light and dark themes from tokens, scripts only from cdnjs, fonts only from
Google Fonts, and a layout that works at phone width. An artifact starts
private, and only its owner can share it, from the page's Share menu. Tell
the user that when you hand over the link.

Without Artifacts, give the user the path to `WORKDIR/site/index.html`.

### Publish to the repository's GitHub Pages site

A repository can host its own summaries. Summaries live on the hidden ref
`refs/projector/summaries`, which is not a branch: GitHub lists no branch
and offers no pull request for it, and clones do not fetch it. A workflow on
the default branch builds and deploys them with Projector's shared action.
`publish` pushes to that hidden ref only in a repository that hosts its
site, which a repository accepts by setting hosting up; anywhere else the
summary stays in the checkout, as the section above describes.

Always publish with `project summary publish`, run directly. Do not wrap it
in a script of your own, and do not commit to the ref or send the dispatch
yourself. The
command already builds the summary and refuses one that does not build, and a
wrapper only hides what is being written from the user and from the host's
permission checks. You do not need to build the page first; `page` is a
local preview.

A host's automatic approval can refuse `publish` as sending repository content
to another host, unless the user's own settings allow it. `project init`, run
by a person at a terminal, adds that allow rule to their Claude Code settings
and to their Codex rules, inside a repository or outside one. If the host
refuses `publish`, do not retry it in another form. Ask the user to run
`project init` in a terminal, or to run the `publish` command themselves.
`init` adds the rules only for a person at a terminal, so do not run it to
add them yourself.

`publish` fetches the diff once, builds the summary against it, and refuses one
that does not build. It then commits the summary and that diff to
`summaries/<number>/<head>/` on the ref, as `summary.json` and `diff.patch`,
without touching the checkout, and sends a `repository_dispatch` event that
starts the workflow. The deploy checks out the default branch, which lacks
the head, so `publish` also stores the `linguist-generated` attributes the
head's `.gitattributes` gives the changed files, as `attributes.json`, and
fetches the head first when the checkout lacks it. Publishes that run at the
same time, as a review loop's subagents run them, each land. A publish whose
push loses the race for the ref fetches the ref again, rebuilds its commit on
the new tip, and pushes again, up to five pushes in all. `publish` fetches the
diff from the compare API, which serves pull requests past the 300-file limit
of `gh pr diff`, so pass `--diff` only when `publish` reports that GitHub
could not serve the diff. The file is then the local `git diff` from step 3,
since the compare call there fails with `publish`'s. It must be the diff from
the summary's `pr.base` to its `pr.head`, because every later deploy serves
the stored diff as it is. Because the diff is
stored, the site deploy asks GitHub for no diff: it checks each summary against
its stored diff and writes each page's data beside it, and the page loads
that data when it opens. The deploy reports and skips any summary that still
fails, or that names another repository, so one bad summary costs one
summary rather than the deployment. The site's Reviews section lists
every summary at `reviews/`, serves each pull request's newest head at
`reviews/<number>/` and each head at `reviews/<number>/<head>/`, and links the
older heads and every pull request in the same stack from each page. The
summary on the ref is the durable copy: to update a summary later, fetch it
with `git fetch origin refs/projector/summaries` and start from it rather
than from a fresh `init`. A summary that an older release stored there as
`spec.json` does not appear on the site, and the deploy reports it as
skipped. To update one, start from its `spec.json` and publish it as
`summary.json`. A repository that has not published since summaries were
called walkthroughs has its summaries on `refs/projector/walkthroughs`, under
`walkthroughs/`, instead, all stored as `spec.json`. Its next `publish`
carries them over to the new ref, where they stay off the site until each is
published again.

Last, `publish` comments a link to the summary on the pull request, so a
reader on GitHub finds the page. The pull request keeps one such comment
per account, always as its last message: each publish posts the link
again at the end and deletes the older comment. Publishing the same head
again posts nothing while its comment is still the pull request's last
timeline item. A later review, comment, push, or ready-for-review event
makes it post the link again. `publish` also puts the same link at the
very bottom of the pull request's description, replacing the one an
earlier publish left there. Where the account cannot edit the
description, `publish` says so and keeps the comment. Pass
`--no-describe` to post the comment and leave the description alone,
where the repository's instructions forbid agents to edit descriptions.

Setting a repository up is once, with admin rights, and only when the user
asks for it. From a checkout whose `origin` is the repository, run:

```sh
project init --site
```

It enables Pages with the GitHub Actions source, makes a private
repository's site private, links an empty repository website to the site,
and writes `.github/workflows/projector-site.yml`. `--site` makes it fail
rather than skip the site. When it fails because the user is not an admin,
it still writes the workflow and prints the `gh` commands an admin runs;
relay those commands. When GitHub refuses to make a private repository's
site private, it writes no workflow; report why. Either way, offer `project site serve` or an Artifact meanwhile. Then add the workflow to the default branch through a
pull request, because GitHub runs dispatched workflows only from there.

The workflow calls `ninjudd/projector/actions/site@v0`. Pass
`--action-ref` to pin an exact release tag or a full commit SHA instead.

## Update the page when the pull request moves

Start from the newest version of the summary on the summaries ref, whoever
published it. Another session or account can have summarized the pull
request already, at an earlier head or at this one. To get that version,
fetch the ref, find the folder most recently written for the pull request,
and copy its summary. `HEAD_SHA` is the head that folder names:

```sh
git fetch origin refs/projector/summaries
git log -1 --name-only --format= FETCH_HEAD -- summaries/NUMBER/
git show FETCH_HEAD:summaries/NUMBER/HEAD_SHA/summary.json > WORKDIR/summary.json
```

When that folder is already this head, revise its summary in place of
starting a new one, and skip step 1.

1. List what changed since the summary's head:
   `git log --oneline OLD_HEAD..NEW_HEAD` and
   `git diff OLD_HEAD NEW_HEAD`. A rebase makes the old head unreachable;
   compare against the new merge base instead.
2. Read the new commits and any new review threads, then revise the summary:
   set `pr.head`, move new files into groups, drop files that left the diff,
   and rewrite every note, check and overview card the change made untrue.
   Move each note that has a `line` to where its code sits in the new diff,
   using step 1's diff, and remove a note whose code is gone: `build` checks
   only that a line is still in its file's diff, not that it still holds the
   code the note is about. Resolve or remove `flag` checks the new commits
   fixed.
3. `publish` the revised summary, which becomes a new version beside the old
   one, pushed to a hosted site or kept in the checkout as the first time.
   A summary kept in the checkout is found there later with
   `git show refs/projector/summaries:summaries/NUMBER/OLD_HEAD/summary.json`.
   For an artifact the user asked for, rebuild and republish to the same
   one: publish the same file path again in the conversation that created
   it, or pass the artifact's URL as `url` after reading it from any other
   conversation.

Summarize for the user what changed in the pull request and which parts of
the page moved.
