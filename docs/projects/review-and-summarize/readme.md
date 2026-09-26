---
status: completed
priority: now
---

# Review and summarize changes

## 1. Outcome

Two one-shot skills, `review-changes` and `summarize-changes`, do the work
the review loop repeats: `review-changes` reviews a pull request's current head
and publishes its review, and `summarize-changes` publishes a guided summary of
a pull request for its human reviewers. `start-review-loop` runs
`review-changes` on every new head, and `review-changes` runs
`summarize-changes` for a large pull request in a repository with a Projector
site, so every large pull request under review carries a current summary.

## 2. Acceptance criteria

- The `walkthrough-pr` skill is `summarize-changes`, and every name built on
  "walkthrough" is renamed: the `project summary` commands, the
  `refs/projector/summaries` ref and its `summaries/` folder, the
  `projector-summaries` event, and the site build's `--summaries` flag.
- A repository that published under the old names keeps its published
  summaries and keeps deploying: the site reads the old ref until the first
  publish after the rename seeds the new ref from it, and publishing sends
  both events while old workflows listen for the old one.
- `review-changes` is a skill of its own that reviews one head by
  `method.md`, and `start-review-loop` keeps only the loop: the reviewer
  identity, the watcher, the tracked set, and one subagent per pull request
  that runs `review-changes`.
- After publishing a review, `review-changes` runs `summarize-changes` when
  the pull request passes a configurable size and the repository has a
  Projector site, updating the existing summary on a new head and carrying the
  review's open findings into it.

## 3. Design

**Names.** The skills are named for what a reader asks for: review the
changes, summarize the changes. `review` alone is taken by both hosts'
built-in commands. The page a summary publishes is still a guided walkthrough
in prose, and the site's section for them is still Reviews.

**Renaming storage names safely.** The ref, its folder, and the event carry
published data and trigger deploys in every repository that adopted the site,
so the rename migrates rather than breaks. The site action and `site serve`
read `refs/projector/summaries` and fall back to `refs/projector/walkthroughs`
and its `walkthroughs/` folder only when the new ref does not exist. The first
`project summary publish` after the rename seeds the new ref by replaying
each old commit with the old folder at `summaries/`, keeping its author,
dates, and message. The site dates a spec by the last commit that touched its
path, and a path-limited log does not follow a move, so a single commit
moving every spec would date them all at the move and let an older head of a
pull request outrank its newest; the replay keeps each spec's date. Publishing sends both
events: a workflow generated before the rename listens only for the old one,
and one generated since listens only for the new one, so each repository
builds once. A later release stops sending the old event.

**The split.** `review-changes` holds what one review does, and
`start-review-loop` keeps what the loop does, so a person can ask for one
review without a loop, and the loop's per-pull-request subagent runs exactly
the skill a person would.

**The automatic summary.** A summary reads the whole diff and writes prose,
so running it on every head of every pull request would roughly double a
review's cost, and a small change needs none. It runs only past a size set in
configuration and only where the repository serves a Projector site, since an
unattended loop has nowhere else to publish. A new head updates the existing
summary rather than starting over.

## 4. Implementation sequence

1. Rename `walkthrough-pr` to `summarize-changes`, with the storage names and
   the migration in section 3.
2. Split `review-changes` out of `start-review-loop`.
3. Run `summarize-changes` from `review-changes`.

## 5. Outcome

Shipped in three stacked pull requests: the rename with its migration (#106),
the `review-changes` split (#107), and the automatic summary. Every criterion
in section 2 holds.

- The migration tests seed the new ref from the old one, keeping each spec's
  publish date so a pull request's newest head stays newest. They also
  exercise the site action's own fallback script against temporary remotes.
- `review-changes` summarizes when `review.summarize` is not `false`, the
  pull request's added and deleted lines reach `review.summarize_min_lines`
  (default 400), and `project site status` reports a site. It updates the
  existing summary on a new head, carries the review's open findings into it
  as `flag` checks, and appends the summary's URL to the review it posted. A
  summary that fails never changes the review's verdict.
