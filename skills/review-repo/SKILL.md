---
name: review-repo
description: Review every open pull request in the current GitHub repository that the operator wrote or that requests the operator's review, including those opened later, and every head pushed to them, by running review-pr in one subagent per pull request, so each head gets an exact-head review and a pull request stays in draft until its head is clean. Use when the user asks to keep reviewing a repository's pull requests as they are pushed.
---

# Review a repository

Keep reviewing the current repository's pull requests as they are pushed.
This skill subscribes the session to review every open pull request in the
repository that the operator wrote or that requests the operator's review,
including pull requests opened later. The watch daemon, one process per
machine that polls every pull request a session subscribes to, then tells the
session each time a head needs a review. Each review is the `review-pr`
skill, in `../review-pr/SKILL.md`, run once for that head. That skill defines
the reviewer and operator identities, the two review modes, what a review
inspects, how it publishes, and how a session acts on each event, so this
file covers only the subscription around it.

## Resolve the identities, the model, and the repository

Resolve the reviewer and the operator as `../review-pr/SKILL.md` describes.
The daemon adopts pull requests for the operator, the account `gh` is signed
in as, and each review posts as the reviewer.

The model is the model ID the host reports for this session, never a default
copied from this file. Projector compares it lowercased and without a
trailing bracketed variant such as `[1m]`, so pass it as the host reports it.

Review only the repository that contains the current working directory:

```sh
gh repo view --json nameWithOwner --jq .nameWithOwner
```

Hold validated logins literally in every query you run yourself; never use
`@me`. Under the default the two roles coincide and `@me` looks harmless,
but its value follows whichever token is live. Under a reviewer override it
resolves to the reviewer, an account that authors nothing here, so a listing
by `@me` comes back empty, indistinguishable from a repository with nothing
open.

## Subscribe the session

1. Read repository instructions, `../review-pr/SKILL.md`, and applicable
   review or GitHub workflow skills.
2. Run `project review rules` once. It reads the user's own Claude Code
   settings and Codex rules, writes nothing, and exits 1 when either lacks
   the rules that allow Projector's commands, `project review publish`,
   `project review hold`, `project review finish`, `project review settle`,
   and `project summary publish` among them. On exit 1, tell the user once,
   before the first review, that the host's automatic approval, such as
   Claude Code's auto mode or Codex's approval reviewer, can refuse the
   session's publishes, a summary on another author's pull request
   especially. Say that running `project init` themselves in a terminal
   adds the rules. Do not run `init` to add them yourself, because `init`
   adds them only for a person. Then continue: the repository's own settings
   can still allow the commands, so a missing rule means a publish can be
   refused, not that it will be.
3. Subscribe the session to review the repository with this model:

   ```sh
   project watch add <owner>/<repo> --role review --model <model-id>
   ```

   Add `--reviewer <login>` only when the user named the reviewer. Without
   it, the command resolves the reviewer as `review-pr` does, from
   `review.username` and then the operator. The command starts the daemon
   when it is not running, and its last line says how the session's events
   arrive. Act on that line as `../review-pr/SKILL.md` describes under
   "Subscribe the session to the pull request".
4. Run `project watch list` and confirm that it shows the repository's
   `review` line for this model. Tell the user which repository the session
   reviews, as which model and reviewer.

The daemon's next pass adopts each open pull request the subscription
covers and announces `NEW PR` for each one whose head has no verdict by this
model from the reviewer's account. So the session lists nothing itself: a
head this model already reviewed needs no event, and every other head gets
one.

## Know what the subscription covers

- **The operator's pull requests.** The daemon adopts every open pull
  request in the repository whose author is the operator, from whichever
  session opened it, including pull requests opened after you subscribe.
- **Requests for the operator's review.** It adopts every open pull request
  that requests the operator's review, whoever made the request: the
  author, a CODEOWNERS rule, or a bot. The request must name the operator's
  login. A request to a team the operator belongs to does not count. A
  requested pull request is someone else's, so `review-pr` reviews it in the
  cross-author mode and never moves its draft state.
- **Pull requests you keep out.** To keep one pull request out when the user
  asks, run
  `project watch drop <owner>/<repo>#<number> --role review --model <model-id>`,
  as Projector's panel does when the user dismisses a pull request.
  `project watch restore` with the same arguments lets it back in.
- **Pull requests the daemon does not adopt**, such as one that requests a
  team, or one the user assigns. Run `review-pr` on it, which subscribes the
  session to that pull request.
- **One session for each pull request and model.** The daemon gives each
  pull request's review on one model to one session, the owner. A session
  that subscribes to the pull request itself, as `review-pr` does, outranks
  this repository subscription for that pull request. Between two
  subscriptions of the same kind, the newer one wins, so a second session
  that runs `review-repo` here on the same model takes every pull request
  over. Sessions on two different models each review, as two opinions. When
  another session takes a pull request over, this session gets `MOVED`.

## Act on each event

Each event line ends with what to do. On `NEW PR`, `NEW HEAD`, and
`RESPONDED`, start the pull request's `review-<number>` subagent, or send the
event to the one it has, and have it run `review-pr` for the head the line
names. Pass the session's id as `--loop` and the model ID as `--model`, as
the line says. Tell the subagent that the session's subscription to the
repository already covers the pull request, so it skips the subscription
step in `review-pr`. Follow `MOVED`, a `NEW HEAD` that says its last review
stalled, `MERGED`, `CLOSED`, and `MISSING` as `../review-pr/SKILL.md`
describes under "Act on the session's review events", which also gives the
rules for each subagent.

Every pushed SHA, including a fix-only SHA, starts a review cycle that ends
in a verdict published on that SHA, full or incremental. `review-pr` decides
which: a head that changed little since the session's last full review can
get an incremental review, and every other head gets a full one. Green CI
and resolved threads are evidence about state, not substitutes for review.
Keep acting on events until the user asks you to stop.

## Stop reviewing the repository

When the user asks the session to stop reviewing the repository, remove the
subscription:

```sh
project watch remove <owner>/<repo> --role review --model <model-id>
```

Then stop each `review-<number>` subagent. Once it has stopped, run
`project review finish <number> --repo <owner>/<repo> --force` with the
session's `--loop` and `--model` for its pull request. `finish` deletes the
review's start comments, clears its locks and state, and removes its
scratch worktrees, so another session can take the review over at once.
`--force` discards a review still in progress. A pull request that the
session subscribed to with `review-pr` keeps its own subscription; remove
it the same way, with the pull request as the target, when the user asks.
