---
status: in-progress
priority: now
---

# Review pull requests in GitHub Actions

A repository can have Projector review each ready pull request in a GitHub
Actions job, with no agent session running anywhere, and get the same
labeled verdict, incremental reviews, summary, and sign-off that a
session's review gives.

## 1. Problem

Projector reviews a pull request through the `review-pr` skill. An agent
session runs it, and the `project review` commands do the mechanical steps:
`setup` checks the head out and posts a start comment, `interdiff` decides
whether a small push can get an incremental review, and `publish` posts the
verdict, sets the `clean` and `reviewing` labels, and posts the
`projector/review` status. `review-repo` subscribes a session to review every
pull request in a repository, and the watch daemon announces each new head
to it.

All of that needs a live session on somebody's machine. When no session is
running, nothing reviews, and a repository that requires `projector/review`
cannot merge. A contributor without Projector installed can get no review at
all. And every review posts as the operator's own account, so on the
operator's pull requests GitHub refuses `REQUEST_CHANGES`, and the verdict
lives only in the label and the status.

GitHub Actions already runs on every push, for free on a public repository.
Two things keep a runner from doing the review today:

- **The review commands assume a person's `gh` login.** `authenticated_login`
  in `src/projector/review.py` reads the operator from `gh api user`, which a
  workflow's `GITHUB_TOKEN` and an app installation token are both refused.
  `reviewer_token` asks for `gh auth token --user <login>`, and a runner has
  no stored login.
- **Nothing runs the skill without a session.** The skill expects a host
  that loads Projector's plugin, a session ID to key its record of published
  reviews, a subscription for later heads, and a user to ask when something
  is ambiguous.

## 2. Solution

Add `project review run <number>`, a command that reviews one head without
a session. It starts an agent through the `claude` or `codex` CLI, or
through a command the repository names, with Projector's plugin loaded, a
permission mode that never waits for a prompt, and a prompt that runs
`review-pr` with no subscription and no questions. When the agent exits, the
command checks that a verdict on the head was published, and cleans up and
fails the job when none was. A composite action, `actions/review`, sets up a
runner for that command and keeps the review state between runs in the
Actions cache, so a small push still gets an incremental review. The
agent's summary of the pull request goes to a second job that runs no
agent, and that job publishes it to the site, so the agent's token never
needs to push. `project init --ci-review` sets a repository up in one
command. It writes the workflow that calls the action, records the settings,
and sets the secret that holds the model's credential. The
review posts as `github-actions[bot]` with the workflow's token, or as a
GitHub App the repository's owner creates. A new setting,
`review.reviewers`, lets every other reader of verdicts, such as the
author's sessions and the site, count that account's reviews.

### 2.1 One review, from push to verdict

1. **The event.** A `pull_request` event (`opened`, `reopened`,
   `synchronize`, or `ready_for_review`) starts the workflow, or a person
   starts it by hand with `workflow_dispatch` and a pull request number.
   Only a pull request that is not a draft and whose head is in the same
   repository is reviewed. A fork's pull request is skipped (§ 7). For a
   `pull_request` event, the job's `if` skips the rest before a runner
   starts. A dispatch carries only a number, so `project review run`
   enforces the same rule for either trigger (step 6).
2. **One run per pull request and agent.** The job's concurrency group is
   `projector-review-<number>-<agent>` with `cancel-in-progress: true`, so a
   new push cancels the review of the head it replaces. The group stands in
   for the review lock: no other run of this reviewer and agent can hold the
   pull request.
3. **The checkout.** The action checks the repository out with full history
   and runs `git remote set-head origin --auto`, so `config.load` reads a
   tracked `.projector.toml` from the default branch, as it does on a
   person's machine, and `setup` can find the merge base.
4. **The state.** The action restores the newest cache entry for the pull
   request and agent into `$XDG_STATE_HOME/projector/reviews` (§ 2.5).
5. **The agent.** The action installs the pinned CLI the agent input names.
   It writes the `config` input, if any, to `$HOME/.projector.toml`, the
   untracked user layer, where `[override.<priority>]` tables can replace a
   committed setting for CI, such as a `review.gate` that needs a local
   tool.
6. **The run.** The action runs `project review run <number> --agent <agent>
   --model <model>`. The command first reads the pull request from GitHub.
   When its head is in another repository, or it is a draft, the command
   prints why and exits 1 before it reads the diff, posts anything, or
   starts an agent. This check, not the job's `if`, is the boundary that
   keeps a fork's diff away from the model credential and the token. Then
   the command does these steps:
   1. **Skip a reviewed head.** When the reviewer already has a verdict by
      this model on the head, the command prints that review's URL and exits
      0, unless `--rereview` is given. A re-run of a finished job therefore
      posts nothing.
   2. **Clear a cancelled run's start comment.** A live start comment by
      this reviewer and model can only come from a cancelled run, because
      step 2 holds the group. The command passes its ID to the agent as the
      `--replace` value for `setup`.
   3. **Start the agent** with the runner preset (§ 2.3), the prompt
      (§ 2.2), and `--timeout`, 45 minutes by default.
   4. **Check the verdict.** When the agent exits, the command reads the
      reviewer's reviews. It succeeds when a Projector verdict by this model
      was submitted after the run started, on the head or on one pushed
      while the agent ran (§ 9). Otherwise it
      runs `project review finish --force` for the review the agent set up
      (§ 9), which deletes the start comment and takes `reviewing` off. It
      then posts one
      comment that names the head, the reason the agent gave in its report
      file or "the agent published no verdict", and the run's URL, and it
      exits 1.
7. **Saving the state and the summary.** The action saves the state folder
   under a new cache key, whether the run passed or failed. When the run
   passed and the agent handed a summary off (§ 2.6), the action uploads it
   as the artifact
   `projector-summary-<number>-<agent>`, kept for one day.
8. **Publishing the summary.** A second job, `summary`, starts once every
   agent's review job has ended, unless the run was cancelled. It runs no
   agent and holds no model credential. It downloads each summary artifact
   of the run and publishes each one, as § 2.6 describes.

### 2.2 The prompt and the headless rules

The command gives the agent one prompt. For Claude it is
`/projector:review-pr <number>`, and for Codex it is "Use the review-pr skill
to review pull request <number>". It also sets these environment variables:

| Variable | Value |
| --- | --- |
| `PROJECTOR_HEADLESS` | `1` |
| `PROJECTOR_LOOP` | `actions`, the loop ID that keys the record of published reviews |
| `PROJECTOR_MODEL` | The `--model` value, which the CLI also runs |
| `PROJECTOR_REPLACE` | The start comment from step 6.2, when there is one |
| `PROJECTOR_RUN_REPORT` | A file in the run's scratch folder |
| `PROJECTOR_SUMMARY_HANDOFF` | A folder in the run's scratch space, where `project summary publish` leaves the summary for the summary job (§ 2.6) |
| `PROJECTOR_REREVIEW` | `1` with `--rereview`, so `setup` reviews a head this model already reviewed |
| `PROJECTOR_SCRATCH` | The run's scratch folder, the only place the agent writes files of its own |

A new section of `skills/review-pr/SKILL.md`, "Review without a session",
says what changes when `PROJECTOR_HEADLESS` is set:

- **Pass the loop and the model from the environment.** Use
  `--loop "$PROJECTOR_LOOP" --model "$PROJECTOR_MODEL"`, and
  `--replace "$PROJECTOR_REPLACE"` when it is set.
- **Do not subscribe.** The workflow runs again on the next head.
- **Do not ask.** Wherever the skill says to ask the user, write the
  question and what you found to `$PROJECTOR_RUN_REPORT` and stop without
  publishing. The command posts it on the pull request.
- **Run nothing that the skill does not name.** No push, no commit, no edit
  outside the run's scratch folder, and no `gh pr ready` or `gh pr merge`.

### 2.3 Runner presets never wait for a prompt

`--agent` picks a preset, and `review.runner` sets the default: `claude`,
`codex`, or any other string, which is a shell command that reads the prompt
on standard input with the variables above in its environment. The command
form is where `agent-sdk` or a runner with MCP servers plugs in. Each preset
runs in a permission mode where nobody is asked, backed by rules that hold
whatever the mode decides:

| | `claude` | `codex` |
| --- | --- | --- |
| Command | `claude -p --plugin-dir <projector> --model <model> --effort <effort> --output-format stream-json` | `codex exec --model <model> --json -o <report>`, with `CODEX_HOME` holding Projector's plugin, installed by `install.sh codex` |
| Mode | `--permission-mode auto --permission-prompts none`: a classifier decides each action no rule covers, and anything that would still prompt is denied | `--sandbox workspace-write --approve-for-me -c sandbox_workspace_write.network_access=true`: Codex's automatic review decides approvals, inside its sandbox, with network on so `gh` can reach GitHub |
| Allowed | `--allowedTools` with `Read`, `Grep`, `Glob`, `Edit` and `Write` in the scratch folder, and `Bash` for `project *`, `gh pr view *`, `gh pr diff *`, `gh repo view *`, `gh api *`, and read-only `git` (`diff`, `log`, `show`, `status`, `rev-parse`, `merge-base`, `ls-files`, `cat-file`) | The sandbox's workspace and the scratch folder |
| Denied | `--disallowedTools` with `Bash` for `git push *`, `git commit *`, `gh pr merge *`, `gh pr ready *`, `gh pr close *`, each `gh repo` subcommand that changes a repository (`archive`, `autolink`, `create`, `delete`, `deploy-key`, `edit`, `fork`, `rename`, `sync`, and `unarchive`), `gh secret *`, `curl *`, and `wget *` | The same commands, refused by an `execpolicy` rule file the preset passes |

The deny rules name each `gh repo` subcommand that changes a repository
instead of denying `gh repo *`. A deny rule wins over every allow rule, and
a repository's instructions can require
`gh repo view <owner>/<repo> --json visibility` before the review writes
anything, so a broad deny would stop that review without a verdict.

`--permission-mode allowlist` replaces the mode with one that runs only the
allowed rules and denies the rest: Claude's `dontAsk`, and Codex with
`approval_policy = "never"`. Use it when auto mode is unavailable for the
credentials the runner has (§ 7). No preset ever passes
`--dangerously-skip-permissions` or `--dangerously-bypass-approvals-and-sandbox`.

### 2.4 The token can review and nothing else

The workflow grants its token only what the review commands call:

| Permission | Used by |
| --- | --- |
| `contents: read` | The checkout and `setup`'s fetch of the head |
| `pull-requests: write` | `publish`'s review and thread replies, `settle`, and `dismiss` |
| `issues: write` | The start comment and the labels |
| `statuses: write` | The `projector/review` status |
| `checks: read`, `actions: read` | `project review gate`'s wait for `review.gate_checks` |
| `pages: read` | `project site status`, which the skill runs to decide whether the repository hosts summaries |

The review job has no `contents: write`, so nothing the agent does can push
a branch or a ref. The summary job has its own token with the permissions
§ 2.6 lists, and no agent runs in that job.

The model's credential comes from a repository secret that the action
passes to the CLI. A subscription credential bills the run to the plan's
usage limits instead of to API credits:

| Agent | Subscription | API credits |
| --- | --- | --- |
| `claude` | `CLAUDE_CODE_OAUTH_TOKEN`, the long-lived token `claude setup-token` prints after you sign in with the subscription | `ANTHROPIC_API_KEY` |
| `codex` | `CODEX_AUTH_JSON`, the contents of `auth.json` after `codex login` with a ChatGPT plan, which the action writes to `$CODEX_HOME/auth.json` | `OPENAI_API_KEY` |

The action refuses to start when an agent has both kinds of credential, so a
review never draws on API credits that you meant to keep for something else.
The `claude` preset never passes `--bare`, because bare mode reads only an API
key. A subscription run shares the plan's usage limits with your own
sessions, so a busy repository can slow your interactive work.

A Codex ChatGPT login refreshes its tokens and writes the new ones back to
`auth.json`. A hosted runner throws that file away, so the secret gets older
every run until a refresh token it holds stops working. The action never
keeps `auth.json` in the Actions cache. A cache is not secret: any workflow
in the repository can restore its entries, and a fork's pull request can
restore the default branch's. A Codex subscription is therefore reliable
only where `auth.json` stays on disk: a self-hosted runner or the
self-hosted reviewer (§ 7). § 7 lists the check for how long the secret
lasts on a hosted runner.

With an `app-id` input and an `app-private-key`
secret, the action mints the token with
`actions/create-github-app-token` and the same permissions, and the review
posts as `<app-slug>[bot]`.

The review commands learn who the token is in two changes to
`src/projector/review.py`:

- **`authenticated_login`.** When `gh api user` is refused with GitHub's
  integration error, it uses `PROJECTOR_TOKEN_LOGIN`, which the action sets
  to `github-actions[bot]` or to `<app-slug>[bot]`. The variable is read
  only after that refusal, so it can never rename a person's token.
- **`reviewer_token`.** When the reviewer is the authenticated login, it
  returns the active token, `gh auth token` without `--user`, which reads
  `GH_TOKEN` on a runner.

The bot is never the pull request's author, so every CI review is a
cross-author review. The skill's existing rules then apply unchanged:
`REQUEST_CHANGES` when findings are open, a `COMMENT` when the head is
clean, and a real `APPROVE` only where `review.allow_approve` is `true`.

### 2.5 The state between runs lives in the Actions cache

The review state folder holds two things a later run needs:
`loops/actions/published.json`, the record of the loop's published reviews
that `interdiff` measures from, and `prs/<owner>/<repo>/<number>/<model>/state.json`,
whose recorded start comment `setup` gives way to. The action caches both,
leaving out locks and worktrees:

| Field | Value |
| --- | --- |
| `path` | `$XDG_STATE_HOME/projector/reviews/loops`, `$XDG_STATE_HOME/projector/reviews/prs`, and `!**/*.lock` |
| `key` | `projector-review-<number>-<agent>-<run_id>-<run_attempt>` |
| `restore-keys` | `projector-review-<number>-<agent>-` |

A cache entry that a `pull_request` run saves is scoped to the pull
request's merge ref, so later runs for the same pull request restore it and
no other pull request can. GitHub evicts an entry after 7 days unused. A
run that finds no entry still reviews correctly: `interdiff` finds no last
full review in an empty record and asks for a full review.

### 2.6 Summaries publish from a job that runs no agent

A summary is the page on the repository's Projector site that explains a
pull request to its human reviewers. `review-pr` writes one after it
publishes the verdict, whenever `review.summarize` is not `false` and
`project site status` says the repository hosts its site. Its
`project summary publish` step does three things:

- **It pushes the summary.** It commits the summary to
  `refs/projector/summaries` and pushes that ref.
- **It starts a site build.** It sends a `repository_dispatch` event, which
  the site workflow listens for.
- **It links the summary.** It posts one comment on the pull request and
  adds one line at the end of the description.

The push and the dispatch need `contents: write`, which the review job does
not have. So in CI the publish happens in two halves:

1. **The agent hands the summary off.** The action sets
   `PROJECTOR_SUMMARY_HANDOFF` to a folder in the run's scratch space. When
   it is set, `project summary publish` reads and checks the summary
   against the diff as it does today, and then writes `summary.json` and the
   diff it checked against into that folder instead of pushing. It prints
   that the workflow publishes the summary, and exits 0. The skill runs
   `project summary publish` as it always does, so `summarize-pr` needs no
   change. When the variable is unset, the command behaves as today.
2. **The summary job publishes it.** The job checks the repository out,
   downloads the run's summary artifacts, and runs
   `project summary publish --summary <folder>/summary.json --diff
   <folder>/diff.patch --pr <number>` for each, in the matrix's order. The new
   `--pr` flag refuses a summary of any other pull request, because an
   agent that read untrusted text wrote the artifact. The publish then
   pushes the ref, sends the dispatch, and links the summary.
3. **An old head links nothing.** A summary job can finish after the job
   for a newer head. So `publish` links a summary only while its head is
   still the pull request's head. It still pushes the summary, because the
   site keeps a page for every head. The guard applies to every publish, in
   CI and in a session.

The summary job has its own token:

| Permission | Used by |
| --- | --- |
| `contents: write` | The push of `refs/projector/summaries`, and the `repository_dispatch` that starts the site workflow |
| `pull-requests: write` | The link at the end of the description |
| `issues: write` | The link comment |
| `pages: read` | The publish's check that the repository hosts its site |

GitHub starts no workflow for most events that the workflow's own token
causes, but `repository_dispatch` is an exception. So the dispatch starts the
site workflow, and the site deploys the summary. The job's concurrency group
is `projector-summary-<number>` without `cancel-in-progress`, so two runs'
summaries publish one after the other, never at once.

### 2.7 Readers count the bot's verdict through `review.reviewers`

On a public repository, `counted_reviews` counts a Projector verdict only
from the reviewer and from an account GitHub calls the repository's
`OWNER`, `MEMBER`, or `COLLABORATOR`, so nobody can forge a verdict.
`github-actions[bot]` is none of those, so the author's sessions, the panel,
`project review status`, and the site would ignore it.

`review.reviewers` is a comma-separated list of logins whose Projector
verdicts count beside those. `counted_reviews` takes the list as a new
argument, and each of its callers passes the list from the configuration:
`verdicts` in `src/projector/review.py`, the three calls in
`src/projector/watch.py`, and `review_status` in `src/projector/cli.py`. A
repository commits it next to the workflow:

```toml
[review]
reviewers = "github-actions[bot]"
```

A tracked `.projector.toml` takes effect from the default branch, so a pull
request cannot add an account to the list that judges it.

### 2.8 The action and the workflow

`actions/review/action.yml` is a composite action, like `actions/site`. It
runs the CLI from its own checkout with `PYTHONPATH` set to the action's
`src` and `python3 -P`, so the caller's checkout cannot shadow the
`projector` module.

| Input | Default | Effect |
| --- | --- | --- |
| `pr` | The event's pull request | The pull request to review |
| `agent` | `claude` | The preset, or `command` to use `review.runner` |
| `model` | `claude-opus-5-5` for `claude`, none for `codex` | The model the CLI runs and the review records. Codex requires it. |
| `effort` | `high` | The effort the preset runs at |
| `permission-mode` | `auto` | `auto` or `allowlist` (§ 2.3) |
| `rereview` | `false` | Review a head this model already reviewed |
| `config` | Empty | TOML written to `$HOME/.projector.toml` |
| `app-id` | Empty | Post as this GitHub App instead of `github-actions[bot]` |
| `timeout-minutes` | `45` | The agent's time limit. The job's own limit is 15 minutes longer. |
| `step` | `review` | `review` runs the agent. `summary` publishes the run's summary artifacts and runs no agent (§ 2.6). |

Secrets reach the action as `env`. `project init --ci-review` (§ 2.10)
writes `.github/workflows/projector-review.yml`, which calls the action at
`@v0`, and Projector's own copy calls it at `@main`, as the site workflow
does. `project review ci-workflow` prints the workflow `init` would write,
so you can compare it with yours, as `project review workflow` does for the
`clean` label workflow:

```yaml
name: Projector review
on:
  pull_request:
    types: [opened, reopened, synchronize, ready_for_review]
  workflow_dispatch:
    inputs:
      pr: {required: true}
      rereview: {type: boolean, default: false}
permissions: {}
jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
      issues: write
      statuses: write
      checks: read
      actions: read
      pages: read
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (!github.event.pull_request.draft &&
       github.event.pull_request.head.repo.full_name == github.repository)
    strategy:
      matrix:
        include:
          - agent: claude
    concurrency:
      group: projector-review-${{ github.event.pull_request.number || inputs.pr }}-${{ matrix.agent }}
      cancel-in-progress: true
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: ninjudd/projector/actions/review@v0
        with:
          agent: ${{ matrix.agent }}
          model: ${{ matrix.model }}
          pr: ${{ github.event.pull_request.number || inputs.pr }}
          rereview: ${{ inputs.rereview || false }}
        env:
          # A subscription token from `claude setup-token`; pass
          # ANTHROPIC_API_KEY instead to bill API credits.
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
  summary:
    needs: review
    if: ${{ !cancelled() && needs.review.result != 'skipped' }}
    permissions:
      contents: write
      pull-requests: write
      issues: write
      pages: read
    concurrency:
      group: projector-summary-${{ github.event.pull_request.number || inputs.pr }}
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: ninjudd/projector/actions/review@v0
        with:
          step: summary
          pr: ${{ github.event.pull_request.number || inputs.pr }}
```

The `summary` job starts once every agent's review job has ended. It
publishes the artifacts that exist, and does nothing when there are none,
such as when the repository hosts no site or every review failed.

Adding `codex` to the matrix gives each pull request a second model's
review. The rules for several models on one pull request keep the two
verdicts apart, and the newest verdict on the head signs it off.

### 2.9 Context repositories give the review more to read

`review.context_repos` is a comma-separated list of `owner/name`
repositories. The action clones each at its default branch, depth 1, into
`$RUNNER_TEMP/context/<owner>/<name>`. The preset gives Claude each folder
with `--add-dir`, and the prompt names the folders for either agent. The
token must reach those repositories, so this needs an app token whose
installation covers them. `GITHUB_TOKEN` reaches only its own repository.

A review must not carry a private repository's contents into a place its
readers cannot see. Before it clones anything, `project review run` checks
each repository's visibility with `gh repo view --json visibility`, and
leaves it out with a warning unless one of these holds:

- **The context repository is public.**
- **Both repositories are private or internal.** The repository's committed
  list is the owner's explicit choice to share one private repository's
  code with the other's reviewers.

Each condition needs a visibility that a check reported. A failed check
proves nothing, so a context repository whose own check fails is left out,
and so is every private or internal one when the reviewed repository's
check fails.

### 2.10 `project init --ci-review` sets a repository up

`project init --ci-review` adds CI review to the repository it runs in, and
`--no-ci-review` records that the repository has none. The step follows the
pattern `init` already uses for the site and the required review check. A
flag answers the question. At a terminal, with no flag and nothing
recorded, `init` asks:

```text
Review this repository's pull requests in GitHub Actions? [y/N]
```

`init` records the answer as `review.ci` in the committed `.projector.toml`,
so it never asks again. Without a terminal, a flag, or a recorded answer, it
asks nothing and changes nothing, so an agent or a CI job that runs `init`
never adds a workflow by surprise. Once `review.ci` is `true`, every later
`init`, such as the one you run after an upgrade, refreshes the workflow.

| Flag | Default | Effect |
| --- | --- | --- |
| `--ci-review`, `--no-ci-review` | The recorded `review.ci`, then the prompt | Set CI review up, or record that the repository has none |
| `--ci-agents <list>` | The recorded `review.ci_agents`, then `claude` | The agents in the workflow's matrix, each as `agent=<model>` to name its model, such as `claude,codex=<model>`. `codex` needs a model. |
| `--ci-credential <kind>` | `subscription` for `claude`, `api-key` for `codex` | Which secret `init` asks for, as the credentials table in § 2.4 lists them |
| `--ci-app <slug>` | None | Post as this GitHub App. `init` reads the app's ID with `gh api apps/<slug>`, which needs no credential. |
| `--action-ref <ref>` | `v0` | The existing flag, which now sets the ref of the review action too |

A yes runs these steps in order, and reports each one as `init` reports a
file:

1. **Write the workflow.** `init` renders
   `.github/workflows/projector-review.yml` with the agents' matrix, each
   agent's credential in the job's `env`, the app's ID when there is one,
   and the action ref. It replaces a workflow that an earlier release wrote
   and that nobody edited. It keeps an edited one, with a note that says
   how to compare it with `project review ci-workflow`, as
   `write_clean_workflow` does for the `clean` label workflow. The
   template's digest joins `init_digests` as `ci-review-workflow`, so a
   release that changes it tells you to run `init` again.
2. **Record the settings.** `init` writes `review.ci = true` and
   `review.ci_agents` to the committed `.projector.toml`. It adds
   `github-actions[bot]`, or `<slug>[bot]` with an app, to
   `review.reviewers`, and keeps every login the list already holds.
3. **Set the secrets.** For each agent, `init` lists the repository's
   secrets with `gh secret list`.
   - **A credential is present.** `init` reports it unchanged.
   - **Both kinds are present for one agent.** `init` warns that the action
     refuses to start, and names the secret to delete.
   - **The credential is missing, at a terminal.** `init` says how to get
     the value, such as running `claude setup-token`. Then it runs
     `gh secret set <name> --repo <owner>/<repo>` with the terminal as its
     input. `gh` prompts for the value without showing it, so the value
     never passes through Projector or a command line. For
     `CODEX_AUTH_JSON`, `init` warns that the login goes stale on a hosted
     runner (§ 2.4), asks you to confirm, and then passes `gh` the file
     `$CODEX_HOME/auth.json`.
   - **The credential is missing, without a terminal.** `init` prints the
     `gh secret set` command to run, and still exits 0, because the files
     are written.

   With `--ci-app`, `init` does the same for `PROJECTOR_APP_PRIVATE_KEY`.
   Setting a secret takes admin access to the repository. When GitHub
   refuses, `init` prints the commands for an admin to run, and keeps the
   files it wrote.
4. **Check what the review depends on.** `init` reports two things it
   cannot fix, after its other steps:
   - **No site.** When the site step was skipped or failed, CI reviews
     publish no summaries until the site is set up.
   - **A gate.** When `review.gate` is set, CI runs it. A step that a hosted
     runner cannot run needs an override in the workflow's `config` input,
     and the note shows one.
5. **Say what comes next.** Commit the workflow and `.projector.toml`
   through a pull request. A `pull_request` workflow runs from the pull
   request's own files, so that pull request is the first one the workflow
   reviews. Readers count the bot's verdict once `review.reviewers` reaches
   the default branch.

### 2.11 The files

| File | Change |
| --- | --- |
| `src/projector/run.py` | New. The presets, the command each builds, the run, the checks before it, and the verdict check after it |
| `src/projector/cli.py` | `review run` and `review ci-workflow`. `init`'s CI review step and its flags (§ 2.10), beside `init_required_check`, and the new template in `init_digests`. `review_status` passes `review.reviewers`. `summary publish` takes `--pr` and links only the pull request's current head (§ 2.6). |
| `src/projector/summary.py` | `publish` writes the summary and its diff to `PROJECTOR_SUMMARY_HANDOFF` instead of pushing when the variable is set (§ 2.6) |
| `src/projector/review.py` | `authenticated_login` and `reviewer_token` work with a runner's token (§ 2.4). `counted_reviews` and `verdicts` take the extra reviewers (§ 2.7). |
| `src/projector/watch.py` | The three `counted_reviews` calls pass `review.reviewers` |
| `src/projector/config.py` | `review.reviewers`, `review.runner`, `review.ci_agents`, and `review.context_repos`, all strings, and `review.ci`, a boolean |
| `src/projector/templates/review-workflow.yml` | New. The workflow `init --ci-review` writes and `review ci-workflow` prints |
| `actions/review/action.yml` | New. The composite action (§ 2.8) |
| `.github/workflows/projector-review.yml` | New. Projector's own review workflow, at `@main` |
| `.projector.toml` | `reviewers = "github-actions[bot]"`, and an override for the gate in the workflow's `config` input, since `hooks/typecheck.sh` cannot run in CI |
| `skills/review-pr/SKILL.md` | "Review without a session" (§ 2.2) |
| `docs/review-in-ci.md` | New. How to set a repository up with `project init --ci-review`, the inputs, the secrets, and what the review can and cannot do |
| `docs/cli.md` | `review run`, `review ci-workflow`, and the CI review step under "Adopt a repository" |
| `tests/test_run.py`, `tests/test_review.py`, `tests/test_watch.py` | The tests in § 4 |

## 3. Why this design

- **One command is the unit of review.** `project review run` does not
  depend on Actions. It runs the same way on a laptop, in a job, and from a
  daemon that someone hosts. A self-hosted reviewer with MCP servers or
  private network access is then a different caller of the same command
  (§ 7), not a second implementation.
- **The CLIs, not `agent-sdk`, start the agent.** The review's logic lives in
  the skill and in the `project review` commands, so the agent needs only to
  load the plugin, run shell commands under a policy, and exit. Both CLIs
  already do that. Calling them keeps the core free of runtime dependencies.
  `agent-sdk` is a TypeScript layer that, as its README describes it, does
  not yet load plugins or skills or take MCP server configuration. It plugs
  in through the command form of `review.runner` once it is worth it: for
  typed in-process tools, other backends, or one event stream for every
  model.
- **The mode never waits, and the rules hold anyway.** A headless run cannot
  answer a prompt, so each preset denies what would prompt. Auto mode
  decides the rest, because a pure allowlist fails a review that needs one
  command nobody listed. Auto mode is a judgment, so the deny rules and the
  narrow token bound what a wrong judgment can do. That matters because the
  agent reads a diff someone else wrote. The flags that skip every check are
  out, because the runner has network access, a GitHub token, and a model
  key.
- **The verdict check makes a failure loud.** A headless agent fails by
  doing less, not by hanging. Without the check, a run that was refused a
  command and gave up would pass, and the pull request would wait for a
  review that never comes.
- **The cache keeps Projector's state, not the agent's transcript.**
  Incremental review needs only the loop's record and the last full review
  on GitHub. Resuming the agent's transcript instead was rejected for three
  reasons. The skill treats a prior session's summary as no evidence. A
  resumed transcript makes each run cost more than the last. And it anchors
  the reviewer on what it concluded before. The cache is allowed to lose
  entries, because a miss costs one full review.
- **Rebuilding the record from GitHub was deferred, not rejected.** The
  verdict markers already hold most of the record, so `interdiff` could work
  with no cache at all. That change touches every loop, not only CI, and the
  cache is enough to start.
- **The workflow's token comes first, and an app is optional.** The
  workflow's token needs no setup. A single shared "Projector" app was
  rejected, because Projector would have to hold its private key and run a
  service that mints tokens, and the core has no hosted parts. Each owner
  creates their own app when they want a named reviewer or context
  repositories.
- **`review.reviewers` extends the trust rule instead of loosening it.**
  Counting every bot's review would let any workflow that can comment forge
  a verdict. Naming the accounts in the default branch's configuration keeps
  the decision with the people who can merge.
- **A second job publishes the summary.** Giving the review job
  `contents: write` was rejected: a misled agent could then push to any
  branch that no rule protects, including the head of the pull request it
  is reviewing. A second token in the same job was rejected too. GitHub
  gives a job one token with one set of permissions, and `actions/checkout`
  stores that token in the checkout's Git configuration, where the agent
  can read it. A separate job gets a separate token, and no agent runs
  there. The handoff sits inside `project summary publish`, so the skill and
  `summarize-pr` stay as they are. If the agent skips the handoff and
  pushes, its token refuses the push.
- **Setup is a step of `init`, not a command of its own.** `init` is the
  command that adopts a repository, and you already run it again after each
  upgrade. It already writes two workflows, asks one question at a
  terminal, records the answer, and keeps a generated file that you edited.
  A separate setup command would repeat each of those, and the CI workflow
  would then go stale after an upgrade, because nothing would rerun that
  command.
- **`init` never sees a secret's value.** `gh secret set` reads the value
  from the terminal itself, so the value never enters Projector's process,
  its output, or a command line that another process can list.
- **Same-repository pull requests only.** A fork's `pull_request` run gets no
  secrets. `pull_request_target` would give it secrets, but the agent would
  then read an outsider's diff with a model key and a writing token in its
  environment, where one successful prompt injection leaks both. A manual
  dispatch names any pull request number, so `project review run` refuses a
  fork's head itself rather than relying on the workflow's `if`.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| Mark a same-repository pull request ready in Projector's repository | A start comment and then a verdict from `github-actions[bot]`, the `clean` label or open findings, and the `projector/review` status on the head |
| Push a small change to a cleanly reviewed pull request | An incremental review that names the full review it builds on, which shows the cache entry was restored |
| Push twice within a minute | The first run is cancelled, and the second run gives the only verdict on the newest head, with no start comment left behind |
| Re-run a finished job | The job prints the existing verdict's URL, exits 0, and posts nothing |
| Open a pull request from a fork, and push to a draft | No review job runs |
| Start the workflow by hand with a fork's pull request number, and with a draft's | The review job fails before the agent starts, its log says why, and nothing is posted on the pull request |
| Make the agent exit without publishing, such as by giving it a model ID the CLI refuses | The job fails, the start comment and `reviewing` label are gone, and one comment names the head and the run |
| Run `project review status` on the reviewed pull request from a session with `review.reviewers` set, and without it | The bot's verdict signs the head off with the setting, and is ignored without it |
| List a private repository in `review.context_repos` of a public repository | The run warns and leaves it out, and the agent's working folders do not include it |
| List a private repository in `review.context_repos`, and make the reviewed repository's own visibility check fail | The run warns and leaves it out, as it does for a public repository |
| Inside the agent, run `git push`, `gh pr merge`, `gh repo edit`, and `gh repo view --json visibility` | The first three are refused and the visibility check runs, in auto mode and in allowlist mode |
| Review a pull request in Projector's repository, which hosts its site | The `summary` job pushes the summary, the site workflow deploys it, and the pull request gets the link comment and the description line |
| Push a new head while an older run's `summary` job waits | The older summary is pushed to the site, but the comment and the description link only the newer head's summary |
| Run `project init --ci-review` at a terminal in a repository with no CI review, as an admin | The workflow and `.projector.toml` are written, `gh` prompts for the missing secret without showing it, and the report ends by telling you to open a pull request |
| Run `project init` again after a release changes the workflow template | An unedited workflow is updated, and an edited one is kept with a note that names `project review ci-workflow` |
| Run `project init` with no terminal, no flag, and no recorded `review.ci` | No workflow is written, and no secret is read or set |
| Review a pull request in a repository with no Projector site | No summary artifact is uploaded, and the `summary` job publishes nothing and passes |

These tests fail before the change, and each catches the bug it names:

- **`test_run_refuses_a_fork_or_a_draft`.** With a fake `gh`, a pull request
  whose head is in another repository, and one that is a draft, each make
  the command exit 1 without reading the diff, posting a comment, or
  starting the agent. Catches a manual dispatch that sends a fork's diff to
  an agent holding the model credential and the token.
- **`test_run_skips_a_reviewed_head`.** Catches a re-run that posts a second
  verdict, or that fails on the collision check because the cache lost the
  record.
- **`test_run_replaces_a_cancelled_start_comment`.** Catches a cancelled run
  whose live start comment makes every later `setup` refuse.
- **`test_run_fails_without_a_verdict`.** An agent that exits 0 without
  publishing makes the command exit 1, delete the start comment, and post
  the failure comment. Catches a silent pass.
- **`test_claude_preset_allows_every_review_command`.** Reads every
  `project review` command in the `review-pr` skill's code blocks, and
  `gh repo view <owner>/<repo> --json visibility`, and checks that the
  Claude preset's allowed rules match each one and no deny rule does.
  Catches a skill change that adds a command CI then refuses, and a deny
  rule broad enough to block the visibility check.
- **`test_presets_never_skip_permissions`.** Builds every preset in both
  modes and checks that no command carries a flag that skips the permission
  system or the sandbox. Catches a "quick fix" that makes a stuck run pass
  by turning every check off.
- **`test_login_from_an_integration_token`.** With `gh api user` refused with
  the integration error, `authenticated_login` returns
  `PROJECTOR_TOKEN_LOGIN`. With a user token, the variable is ignored.
  Catches both a crash at `setup` in CI and a variable that renames a person.
- **`test_reviewer_token_without_a_stored_login`.** Catches `setup` failing on
  a runner because it asks `gh` for a stored login that does not exist.
- **`test_counted_reviews_with_extra_reviewers`.** On a public repository, a
  verdict from a listed bot counts, and one from an unlisted account with
  association `NONE` does not. Catches both an invisible bot verdict and a
  forged one that counts.
- **`test_summary_handoff_pushes_nothing`.** With
  `PROJECTOR_SUMMARY_HANDOFF` set, in a temporary repository with a bare
  remote, `publish` writes the summary and the diff to the folder, and the
  remote's `refs/projector/summaries` does not move. Catches a handoff that
  still pushes, which fails the review job on its token.
- **`test_summary_handoff_checks_the_summary`.** A summary that does not
  match its diff fails the handoff, as it fails a push. Catches an
  unchecked summary that only fails later in the summary job, after the
  review has passed.
- **`test_publish_refuses_another_pull_request`.** `--pr 7` refuses a
  summary of pull request 8. Catches an artifact that publishes onto a pull
  request it was not written for.
- **`test_publish_links_only_the_current_head`.** When the pull request's
  head has moved past the summary's head, `publish` pushes the summary but
  posts no comment and leaves the description alone. Catches a late job
  that points readers at an older head's summary.
- **`test_init_ci_review_keeps_reviewers`.** In a temporary repository whose
  `.projector.toml` already sets `review.reviewers = "someone"`,
  `init --ci-review` leaves both `someone` and `github-actions[bot]` in the
  list. Catches a setup that drops a reviewer the repository already counts.
- **`test_init_ci_review_keeps_an_edited_workflow`.** An edited
  `projector-review.yml` survives `init`, and an unedited one from an earlier
  template is replaced. Catches both an `init` that destroys your changes
  and one that never refreshes the workflow.
- **`test_init_ci_review_sets_a_secret_without_reading_it`.** With a fake `gh`
  that records its arguments, the secret step calls `gh secret set <name>`
  with no value among the arguments, and gives `gh` the terminal as its
  input. Catches a value passed with `--body`, where any process on the
  machine can list it.
- **`test_init_without_a_terminal_adds_no_ci_review`.** With standard input
  not a terminal and `review.ci` unset, `init` writes no review workflow and
  calls no `gh secret` command. Catches an agent's or a CI job's `init` that
  adds a workflow nobody asked for.
- **`test_context_repo_visibility`.** A private, internal, or unreadable
  context repository is left out of a public repository's review, and a
  private or internal one is left out when the reviewed repository's own
  check fails. Catches a private repository's code reaching a public
  review, including through a lookup that failed.

## 5. Cost

- **Runner time.** A standard GitHub-hosted runner is free on a public
  repository and draws on the owner's minutes on a private one. A run holds
  the runner while `project review gate` waits for `review.gate_checks`, which
  is about a minute in Projector, and while the agent reads. Cancelling a
  superseded head stops paying for it at once.
- **The summary job.** It starts one more runner for each run, which
  checks out the repository and pushes one commit in under a minute. It
  makes no model calls.
- **Model tokens.** These are the same as a session's review of the same
  head, because the prompt and the method are the same. Each model in the
  matrix pays for its own review.
- **GitHub requests.** A review makes tens of REST calls, well inside a
  workflow token's 1,000 requests an hour for each repository. It draws
  nothing from the operator's own GraphQL budget, which the watch daemon
  shares.
- **Cache.** One entry is a few kilobytes for each pull request and agent,
  far below a repository's 10 GB cache.

## 6. Rollout

1. **Land the command, the action, the setting, the `init` step, and the
   workflow** in one stack, with Projector's own workflow at `@main` in the
   last layer, written by `project init --ci-review --action-ref main`.
2. **Check the runner facts** in § 7 on Projector's own pull requests, and
   record what they show in this plan. If auto mode is unavailable for the
   credentials Projector's repository uses, set the workflow's
   `permission-mode` to `allowlist`.
3. **Tag a release**, so `project init --ci-review` can write `@v0` for
   other repositories.
4. **Add the app inputs and context repositories** in a later pull request.
   Neither works with the workflow's token, so neither blocks the first
   three steps.

## 7. Open questions

- **Fork pull requests.** Decided: the workflow skips them, as § 2.1 step 1
  says. Review them only once the agent's shell can run without the model
  credential and a writing token in its environment. Until then,
  `project review run`'s check of the head's repository stays the boundary
  for every trigger, the job's `if` only saves a runner, and no change
  offers `pull_request_target` for reviews.
- **The app model.** Decided: an app is optional. Reviews post as
  `github-actions[bot]` by default. An owner who wants a named reviewer, or
  reviews that read context repositories, creates their own app once and
  passes `app-id` and `app-private-key`, as § 2.4 says. One shared app that
  Projector hosts is rejected in § 3.
- **Approvals.** Decided: no change. A clean CI review posts a `COMMENT`,
  `review.allow_approve` alone permits a real `APPROVE`, and the `clean`
  label with the `projector/review` status stays the sign-off, as § 2.4
  says. With the workflow's token, an `APPROVE` also needs the repository's
  setting that allows GitHub Actions to approve pull requests.
- **Summaries in CI.** Decided: they ship in the first version, published by
  a second job that runs no agent, as § 2.6 says.
- **A self-hosted reviewer.** Deferred to a nested project. A watch daemon
  deliverer named `run` would start `project review run` for each review
  event instead of prompting a conversation, under a repository
  subscription that no person's session holds. It reuses the daemon's
  polling and ownership, and the runner's own `review.runner` command adds
  MCP servers or other tools.
- **Runner facts to check during the rollout.** The implementer settles each
  of these on a GitHub-hosted runner and records the result in this plan:
  - Claude's `auto` mode with an API key and with an OAuth token from
    `claude setup-token`.
  - Codex's `workspace-write` sandbox on GitHub's Ubuntu image, which limits
    unprivileged user namespaces.
  - The `author_association` GitHub gives `github-actions[bot]` reviews.
  - Whether the collaborator-permission call that `trusted_head` makes, and
    the ruleset read that `requires_check` makes, succeed with the workflow's
    token.
  - Whether either CLI can keep the model credential out of the agent's
    shell environment. This matters more for a subscription token, which
    is tied to a person's account.
  - How many runs a `CODEX_AUTH_JSON` secret lasts on a hosted runner before
    its refresh token stops working.
- **Subscription use for other people's pull requests.** Deferred to each
  repository's setup, because the action supports both kinds of credential
  and nothing in the build depends on the choice. A subscription credential
  is for personal use. Reviewing your own pull requests with it fits that.
  Reviewing every collaborator's pull request in a shared repository may
  not, and an API key fits that case. `docs/review-in-ci.md` says so where
  it describes the secrets.

## 8. Background

- **The identity calls.** `authenticated_login` runs
  `gh api user --jq .login`. `reviewer_token` runs
  `gh auth token --user <login>`. `setup` calls both before it posts the
  start comment.
- **The trust rule.** `review_counts` counts any review on a private or
  internal repository, and on a public one only an `OWNER`, `MEMBER`, or
  `COLLABORATOR`. Its comment already notes that a workflow's
  `GITHUB_TOKEN` sees a private membership as a lower association.
- **The trusted head.** `trusted_head` refuses to run a head from another
  repository and accepts an author or a commit author who is the operator or
  has write access. In CI the operator is the bot, so every author is
  checked through the collaborator-permission API.
- **The state.** `Paths` keeps each model's review of each pull request
  under `prs/<owner>/<repo>/<number>/<model>/`, with its `state.json`, its
  locks, and its worktrees under `worktrees/`. `loop_record` keeps
  `loops/<loop>/published.json`. `check_collision` refuses a verdict by the
  same model on the head that the loop's record does not hold, which is why
  the command skips a reviewed head before `setup` (§ 2.1, step 6.1).
- **Start comments.** `check_duplicate` refuses while a live start comment
  by the same model exists that this machine's state does not record.
  `is_live` keeps a comment live until its model has a verdict on the head,
  or for a day. `--replace` already deletes a stopped session's comment.
- **The user layer.** `config.user_config_path` is `$HOME/.projector.toml`,
  untracked and read from disk, so the action can write overrides there.
- **The precedent.** `actions/site/action.yml` runs the CLI from the action's
  checkout with `PYTHONPATH` and `python3 -P`, and `project site workflow`
  writes a caller workflow at `@v0`.
- **The CLIs.** Claude Code 2.1.327 takes `--permission-mode` with `auto`
  and `dontAsk` among its choices, `--permission-prompts none`, which denies
  whatever would prompt under `--print`, and `--plugin-dir`, `--allowedTools`,
  `--disallowedTools`, `--mcp-config`, `--model`, and `--effort`. Codex
  0.169.0's `codex exec` takes `--sandbox`, `--approve-for-me`, which sends
  approvals to automatic review inside the `workspace-write` sandbox, and
  `--json` and `-o`. It has no flag that loads a plugin.

## 9. What implementation settled

- **The loop.** `project review run --loop` defaults to `run`, and the
  action passes `actions`, so a laptop's run and a CI run keep separate
  records of the reviews they published.
- **The effort marker.** The run sets no `CLAUDE_EFFORT`. Claude Code sets
  it for its own Bash tool, and Codex reports no effort, so a value the run
  set could only disagree with the host.
- **A run's own verdict.** The run notes the ids of the reviewer's
  Projector reviews before the agent starts, and counts only a verdict by
  the model with a new review id afterwards. A time comparison would depend
  on the runner's clock agreeing with GitHub's, and a re-review of the same
  head makes an earlier verdict look like this run's. The verdict may name
  a newer head than the one the run started on, because the skill moves an
  unpublished review to a head pushed while it runs.
- **A failed run cleans up only its own review.** The run reads the
  model's review state before the agent starts. Afterwards it runs
  `project review finish --force` only when the state names the run's loop
  and a start comment the earlier state did not, which only the agent's
  `setup` writes. When another loop holds the lock, `setup` is refused, and
  that loop's state, lock, and worktrees stay as they are. A lock that
  names the run's loop and appeared during the run, when the agent wrote
  no state, is a `setup` stopped partway, and the run removes it so the
  cached state does not refuse the next run.
- **Asking before a run.** `project review run` starts another agent with
  its own credential, or the command `review.runner` names. Claude Code
  asks before it through `Bash(project review run *)`, and Codex's rules
  leave it to Codex's usual approval.
- **`gh auth status` is allowed.** The skill runs it, so allowlist mode
  would otherwise refuse a step the skill names.
- **The site counts `review.reviewers` too.** The site build reads the
  setting from the checkout it builds from, and the status query returns
  each review's author, so a CI review's verdict sets a page's status on a
  public repository.
- **The handoff runs only where the site is hosted.** Elsewhere
  `project summary publish` keeps the summary in the checkout, as it does
  in a session, and the action uploads nothing.
- **The workflow passes both credentials of each agent.** A secret that is
  not set reads as empty, and the action's check accepts exactly one, so the
  workflow does not depend on the kind of credential you chose.
  `--ci-credential` only picks the secret `init` asks for.
- **A digest line marks a workflow nobody edited.** The workflow's second
  line records the SHA-256 of the rest, and `init` replaces the file only
  while the two agree. The site workflow's way, matching each earlier
  template with its variable parts left free, would need a pattern for every
  combination of agents, and the clean label workflow's list of earlier
  digests would need one for every agent list and ref.
- **Codex reads Projector's skills from a link.** The action links
  `$CODEX_HOME/skills` to the skills in its own checkout, because a
  downloaded action is not a Git checkout that `install.sh codex` can install
  a marketplace from.
- **`project` is a shim on `PATH`.** The agent runs `project` by name, so the
  action writes a script that runs the action's own `src` with `python3 -P`.
- **The app comes later.** `--ci-app` and the `app-id` input ship with
  context repositories in the later pull request § 6 step 4 names.
- **A runner fact to check:** that `actions/download-artifact` succeeds when
  no artifact matches its pattern, as in a repository that hosts no site.
- **Setup names each agent's model.** `--ci-agents` and `review.ci_agents`
  take `agent=<model>` entries, and the workflow's matrix lists each agent
  with its model, which the job passes as the action's `model` input. The
  action has a default model only for `claude`, so `init` refuses `codex`
  without one rather than write a job that stops before every review.
  Projector names no default Codex model, because a model ID it chose would
  go stale as models change, and a wrong one fails every run.
- **The summary job names its token too.** `project summary publish` reads
  the account it posts as before it links the summary, to find and replace
  that account's earlier link, and GitHub refuses `gh api user` to the
  summary job's token just as it does to the review job's. So the lookup
  `authenticated_login` makes, with its `PROJECTOR_TOKEN_LOGIN` fallback,
  lives in `src/projector/summary.py`, which `review.py` already imports,
  and both commands use it. The action sets the variable on every step that
  runs `project` with the workflow's token.
