---
name: start-review-loop
description: Monitor the operator's pull requests, and those that request the operator's review, in the current GitHub repository and run review-pr on every pushed SHA, one subagent per pull request, so each head gets an exact-head review and a pull request stays in draft until its head is clean. Use when the user asks to keep reviewing current and subsequent PR heads.
---

# Start Review Loop

Run a persistent exact-head review loop for the current repository. The loop
watches pull requests and decides when a head needs a review; each review is
the `review-pr` skill, in `../review-pr/SKILL.md`, run once for that
head. That skill defines the reviewer and operator identities, the two review
modes, what a review inspects, and how it publishes, so this file covers only
the loop around it.

## Resolve the operator and scope

Resolve the reviewer and the operator as `../review-pr/SKILL.md`
describes; the watcher needs the operator's login for `--author` and
`--review-requested`, and the reviewer's for `--reviewer`.

Watch only the repository containing the current working directory. The loop
reviews every open pull request the operator authors there, from whichever
session opened it, every one that requests the operator's review, and any the
user explicitly assigns. The tracked set lives in the tracked file the watcher
reads, one `owner/repo#number` per line, and the watcher sees nothing outside
it. Run the watcher with `--repo <owner/repo> --author <operator>
--review-requested <operator>` and it appends each open pull request the
operator wrote or was asked to review to that file, so a new one reports as
`NEW PR` without anyone adopting it. A request counts whoever made it,
whether the author, a CODEOWNERS rule, or a bot, but only when it names the
operator rather than a team the operator belongs to. A requested pull request
is someone else's, so `review-pr` reviews it in the cross-author mode and
never moves its draft state. Adoption only adds lines, so it stays tracked
after the request is answered, and every later push gets a review until it
closes. A line `!owner/repo#number` keeps one pull request out when the user
asks. `project loop drop owner/repo#number --tracked <file>` appends one, as
Projector's band does when the user dismisses a pull request, and `project
loop restore` with the same arguments removes it. Hold validated logins
literally in every query you run yourself; never use `@me`. Under the default
the two roles coincide and `@me` looks harmless, but its value follows
whichever token is live, so under a reviewer override it resolves to the
reviewer — an account that authors nothing here — and a listing by `@me` comes
back empty, indistinguishable from a repository with nothing open.

## Establish the loop

1. Read repository instructions, `../review-pr/SKILL.md`, and applicable
   review or GitHub workflow skills.
2. Run `project review rules` once. It reads the user's own Claude Code
   settings and Codex rules, writes nothing, and exits 1 when either lacks the
   rules that allow `project review publish`, `project review hold`, and
   `project summary publish`.
   On exit 1, tell the user once, before the first review, that the host's
   automatic approval, such as Claude Code's auto mode or Codex's approval
   reviewer, can refuse the loop's publishes, a summary on another author's
   pull request especially. Say that running `project init` themselves in a
   terminal adds the rules. Do not run `init` to add them yourself, because
   `init` adds them only for a person. Then continue: the repository's own
   settings can still allow the commands, so a missing rule means a publish
   can be refused, not that it will be.
3. Resolve the repository, checked-out branch, the operator's open pull
   requests, the open pull requests that request the operator's review, any
   the user assigned, and the total open count:

   ```sh
   gh pr list --author <operator> --state open --limit 200
   gh pr list --search "user-review-requested:<operator>" --state open --limit 200
   ```

4. Fetch each pull request's base SHA, head SHA, state, reviews, and
   thread-aware review threads. Say how many pull requests are tracked, the
   operator's and the requested ones, out of the repository total.
5. Find the current heads this loop has reviewed. For each tracked pull
   request, run `project review status <number> --json`. Its `models` list
   holds each model's newest verdict on the current head, and the head is
   reviewed when an entry's `login` is the reviewer's, in any letter case,
   and its `model` is the model ID the host reports for this session,
   lowercased and without a trailing bracketed variant such as `[1m]`.
   Another model's verdict is that model's review, and a verdict by the
   same model from a teammate's account is a review by the teammate, not
   by this loop. Review any current head without a completed exact-head
   review by this model immediately; do not baseline it away.
6. Write the tracked set to a durable tracked file, one `owner/repo#number`
   per line, kept beside the state file and outside any checkout. That file
   is the watcher's whole scope and the record of the set. The watcher
   appends the operator's pull requests and the requested ones itself;
   append one the user assigns, and delete a line when its pull request
   closes. Start or reuse a persistent recurring goal that records the
   repository, operator, the tracked file's path, the model ID the host
   reports for this session, and this loop's id: a name chosen once for this
   model, such as `<repo>-review-<model-key>`, where the model key is the
   model ID lowercased, without a trailing bracketed variant such as `[1m]`,
   and with every character but a letter, a digit, `.`, `_`, and `-` made
   `-`. The loop passes `--loop <id>` and `--model <model-id>` to each
   `project review` command it runs on a pull request: `setup`, `move`,
   `census`, `interdiff`, `gate`, `publish`, and `release`. The record of
   which reviews this loop published lives under that id in the review state
   directory, where `project review publish` keeps it. Give each model's loop
   an id of its own, because two loops that write one record can lose an
   entry.
7. Run one pass, with the model ID from step 6 as `--model` and the
   reviewer's login as `--reviewer`:

   ```sh
   scripts/watch-prs.sh --tracked <file> --state <state-file> \
     --model <model-id> --reviewer <reviewer> \
     --repo <owner/repo> --author <operator> --review-requested <operator> --once
   ```

   Name a state file beside the tracked file and leave it to the watcher,
   which creates it and alone writes it. A pull request with no row there
   reports as `NEW PR`, unless its head already has a verdict by this model
   from the reviewer's account, which the watcher records without a word.
   It refuses to start, naming the line, on an entry that is not
   `owner/repo#number` or a number that names no pull request, and it
   distinguishes a lookup that failed for network or auth reasons from a
   missing pull request. Every head you found unreviewed in step 5 must come
   back as `NEW PR`; one that does not is a pull request missing from the
   file. The pass records every head it saw, so the running watcher does not
   announce them again; review the heads it names from this output. Then run
   the same script without `--once`, with the same state file, `--model`,
   `--reviewer`, and adoption flags, `--worktree <repository>`, and an
   interval of at least 30 seconds, under the host's persistent process or
   monitor facility.

The watcher prints `NEW PR`, `NEW HEAD`, `RESPONDED`, `CLOSED`, `BRANCH`, and
`TRACKED`. Treat `RESPONDED` as a re-review request for a head still waiting on
the reviewer: the author answered without pushing, by resolving every thread
or by replying on an open finding thread, so no head event is coming, and only
a re-review can settle the findings and sign off. It fires once per answer, and
the re-review's own replies carry the verify marker, so they never count as
one. It prevents a response without a push from deadlocking both loops. A `TRACKED` line is
a problem with the tracked file itself, announced once; fix the file. Keep the
loop silent while no event needs action.

The author's hold comment, which `fix-pr` posts with `project review hold`
while it builds a change, as `../fix-pr/SKILL.md` describes under "Hold the
pull request while changes are built", silences `RESPONDED`: the head waits
on the author's push, not on a re-review, and `publish` keeps the pull
request a draft while the hold is open however clean the review. Ending a
hold the watcher saw on the current head is an answer, so it asks for a
re-review once the author ends the hold, in case a review of that head ran
while the hold kept it a draft. A `RESPONDED` that reaches a subagent after
a review has marked the pull request ready, with no change request
standing, needs nothing: that review already signed the head off.

If adopting an existing watcher, fetch unresolved state first and run step 5,
reviewing each head it finds unreviewed: the watcher announces a head once,
possibly to a conversation that never finished its review. Confirm that the
watcher runs with both adoption flags, `--author` and `--review-requested`,
for this repository and operator, reads the tracked file this loop records,
and passes this loop's model ID as `--model` and the reviewer's login as
`--reviewer`. Its state file needs no check. A row there says only that the
watcher saw a head, not that a review of it finished, and a tracked pull
request with no row reports on the watcher's next cycle. Restart it when
the script changed after the process started.
Silence alone proves nothing.

## Give each pull request its own subagent

Where the host can keep a subagent alive and send it later messages, each
tracked pull request gets one reviewing subagent for as long as it stays open.
The main loop owns the watcher, the tracked file, and the loop's id; the
subagent owns every review of its pull request. Review context that
carries over is the point: on a fix-cycle head the subagent already knows its
earlier findings, the author's replies, and what it verified last round, so it
reads the new head against that history instead of rediscovering it.

- On a pull request's first `NEW PR`, start its subagent with a name that
  carries the number, such as `review-<number>`. Give it the repository, the
  pull request, the head SHA, the reviewer and operator logins, the review
  mode, the loop's id to pass as `--loop`, the model ID to pass as
  `--model`, and the path of
  `../review-pr/SKILL.md`, and have it run that skill for the head.
- Send every later `NEW HEAD` and `RESPONDED` for that pull request to the
  same subagent as a message; never start a second one for it. Each message
  is another run of `review-pr`, for the head it names; a `RESPONDED`
  re-review of an unmoved head runs `project review setup` with
  `--rereview`. A `NEW HEAD`
  that arrives mid-review is the moved-head case that skill's start-comment
  rule covers.
- The subagent reports each published review back: the SHA, the verdict, the
  review id, how many threads it opened, for an incremental review the head
  of the full review it builds on, and a newer verdict by another model that
  set the draft state when `publish` names one; `publish` has already added
  the id to the loop's record. A question for the user comes back to the main
  loop to ask.
- Each subagent summarizes every pull request it reviews, as `review-pr`
  describes, another author's included. The loop never pauses or holds
  summaries on its own judgment. The one off switch is `review.summarize =
  false` in `.projector.toml`, which the user sets. A brief that
  forbids editing the pull request body names the summary's link block as
  an exception, because `project summary publish` writes that block at the
  end of every description on purpose. Where the repository's own
  instructions forbid description edits, a subagent publishes with
  `--no-describe`, so the comment alone links the summary, as `review-pr`
  describes.
- Between heads the subagent keeps its scratch worktree; on `CLOSED`, tell it
  to finish: delete any start comment it still holds and remove its scratch
  worktrees. Then close the subagent and delete the pull request's line from
  the tracked file.
- When a subagent is lost, to a session restart for example, start a
  replacement under the same name. It rebuilds its context from GitHub: the
  pull request's Projector reviews, their finding threads, and the replies on
  them.
- A subagent whose publish the host refused reports the refusal and stops
  that publish; it never retries a refused publish in another form, as
  `../review-pr/SKILL.md` and `../summarize-pr/SKILL.md` say. Tell the user
  once for the loop, not once per pull request, that the host's automatic
  approval refused a publish and that running `project init` in a terminal
  adds the rules. Then keep reviewing.

The loop's record decides the collision check `project review publish` runs
before it submits, over the verdicts this loop's model posted. An id in the
record is this loop's own, and a second one on the same head passes only for
a `RESPONDED` re-review, which `setup --rereview` marks, so it never trips the
check. An id absent from it means another loop of the same model under the
same account is reviewing this pull request: `publish` refuses, and the loop
holds the review and asks the user which loop continues. `setup` catches the
same case earlier, when that loop's start comment shows it reviewing. The
tracked file does not prevent this: every loop that adopts the same author's
pull requests tracks the same ones. A loop of another model is a second
opinion, never a collision, as `../review-pr/SKILL.md` describes under
"Review as one model among several".

The main loop routes events and records outcomes; it does not inspect code, so
its own context stays small however many pull requests it tracks. Where the
host has no subagents, the main loop runs `review-pr` itself for each
head.

## Continue after fixes

Every pushed SHA, including a fix-only SHA, starts a review cycle that ends in
a verdict published on that SHA, full or incremental. `review-pr` decides
which: a head that changed little since this loop's last full review can get
an incremental review, and every other head gets a full one. Green CI and
resolved threads are evidence about state, not substitutes for review. When a
pull request closes or merges, close its subagent and delete its line from the
tracked file. The watcher adopts the operator's new pull requests and new
requests for the operator's review itself; append one the user assigns, and
continue until the user stops the loop.
