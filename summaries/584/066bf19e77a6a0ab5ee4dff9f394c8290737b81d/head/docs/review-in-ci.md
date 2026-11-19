# Review pull requests in GitHub Actions

A repository can have Projector review each ready pull request in a GitHub
Actions job, with no agent session running anywhere. Each review gets the
same labeled verdict, incremental review, summary, and sign-off that a
session's review gives. The jobs post as `github-actions[bot]`, so every CI
review is a cross-author review: findings are `REQUEST_CHANGES`, and a clean
head gets a `COMMENT`, the `clean` label, and, where a ruleset requires it,
the `projector/review` status.

## Set a repository up

Run `init` from a checkout of the repository, as an admin of it:

```console
$ project init --ci-review
created .github/workflows/projector-review.yml
project: created review.ci = true in .projector.toml
project: created review.ci_agents = "claude" in .projector.toml
project: created review.reviewers = "github-actions[bot]" in .projector.toml
project: the claude review needs CLAUDE_CODE_OAUTH_TOKEN: run `claude setup-token`, signed in with your subscription, and paste the token it prints
? Paste your secret ********
```

`init` does four things:

1. **Writes the workflow**, `.github/workflows/projector-review.yml`, with
   one review job for each agent. Pass `--ci-agents claude,codex=<model>` to
   have two models review each pull request. An entry `agent=<model>` names
   the model that agent runs. `codex` needs one, because the action has no
   default model for it, so `init` refuses `codex` alone rather than write a
   job that would stop before every review. `claude` runs `claude-opus-5-5`
   unless you name another. A later `init`, such as the one you run after an
   upgrade, replaces the workflow while nobody has edited it, and keeps an
   edited one. `project review ci-workflow` prints the workflow `init` would
   write, so you can compare it with yours.
2. **Records the settings** in the committed `.projector.toml`:
   `review.ci`, `review.ci_agents` with each agent's model, and
   `github-actions[bot]` in `review.reviewers`. That last setting lets your
   own sessions, the panel, and the site count the bot's verdicts on a
   public repository.
3. **Sets the credential secret** for each agent that has none. `gh`
   prompts for the value without showing it, so the value never passes
   through Projector or a command line. Without a terminal, `init` prints
   the `gh secret set` command to run instead.
4. **Says what the review depends on**: a Projector site, for summaries, and
   any `review.gate` step that a hosted runner cannot run.

Commit the workflow and `.projector.toml` through a pull request. A
`pull_request` workflow runs from the pull request's own files, so that pull
request is the first one the workflow reviews. Your sessions count the bot's
verdicts once `review.reviewers` reaches the default branch.

At a terminal, `init` asks whether to set CI review up until you answer.
`--no-ci-review` records that the repository has none, and `init` stops
asking.

## Choose a credential

Each agent reads one secret. A subscription credential bills the review to
your plan's usage limits; an API key bills API credits:

| Agent | Subscription | API key |
| --- | --- | --- |
| `claude` | `CLAUDE_CODE_OAUTH_TOKEN`, the token `claude setup-token` prints | `ANTHROPIC_API_KEY` |
| `codex` | `CODEX_AUTH_JSON`, the `auth.json` that `codex login` writes | `OPENAI_API_KEY` |

`init` asks for the subscription secret for `claude` and the API key for
`codex` unless `--ci-credential` says otherwise. A job that finds both
secrets for its agent refuses to start, so a review never draws on API
credits you meant to keep unused.

Keep these limits in mind:

- **A subscription is for personal use.** Reviewing your own pull requests
  with it fits that. For a shared repository where collaborators open pull
  requests, use an API key.
- **A subscription run shares your plan's limits** with your own sessions,
  so a busy repository can slow your interactive work.
- **A Codex ChatGPT login goes stale on a hosted runner.** Codex refreshes
  its tokens and writes them back to `auth.json`, and a hosted runner
  throws that file away, so the secret gets older with every run. Use an
  API key for `codex` on hosted runners, or keep the login on a runner of
  your own, as
  [Run reviews on your own runners](self-hosted-runners.md#codex) describes.

`--ci-credential runner` is a third kind, for a self-hosted runner that
holds the agent's credential itself: `init` sets no secret, and the workflow
passes none, so the job reads the runner's own environment and, for
`codex`, the runner's own Codex login.

## Run on your own runners

The jobs run on `ubuntu-latest` unless you name your own runners' labels
with `--ci-runs-on`, such as `--ci-runs-on self-hosted,linux,projector`.
`init` records them as `review.ci_runs_on`, and both jobs run there.
[Run reviews on your own runners](self-hosted-runners.md) describes what the
runner needs, how it holds the agent's credential, how a review reaches your
internal tools from it, and how to keep it safe.

## What a review job does

The workflow runs on `opened`, `reopened`, `synchronize`, and
`ready_for_review`, and by hand from the **Actions** tab with a pull request
number. Each job:

1. **Skips forks and drafts.** The job's condition skips them, and
   `project review run` refuses them too, so a manual run cannot review
   either. A fork's head is code an outsider wrote, and the agent would read
   it with your credential in its environment.
2. **Cancels a superseded review.** One job runs for each pull request and
   agent. A new push cancels the job for the head it replaces.
3. **Restores the review state** from the Actions cache, so a small push
   gets an incremental review. GitHub evicts an entry after 7 days unused,
   and a run without one gives a full review.
4. **Runs the agent** with `project review run`, in a permission mode that
   never waits for a prompt, as
   [Review a head with no session](cli.md#review-a-head-with-no-session)
   describes. The action runs Projector from the source at the ref the
   workflow names, not from an installed package, so it sets
   `PROJECTOR_VERSION` from that source's `.claude-plugin/plugin.json`. The
   review names that version in its start comment and its signature.
5. **Fails loudly.** When the agent publishes no verdict, the job deletes
   the start comment, takes `reviewing` off, posts one comment that says
   why, and fails.

A second job, `summary`, starts once every review job has ended. It holds
the only token that can push `refs/projector/summaries`, and it runs no
agent. Each review job's `project summary publish` checks its summary and
hands it to this job, which publishes it, starts the site build, and links
the summary from the pull request.

## Tokens

The workflow gives each job only what it needs:

| Job | Permissions |
| --- | --- |
| `review` | `contents: read`, `pull-requests: write`, `issues: write`, `statuses: write`, `checks: read`, `actions: read`, and `pages: read` |
| `summary` | `contents: write`, `pull-requests: write`, `issues: write`, and `pages: read` |

The review job cannot push a branch or a ref, so a misled agent cannot
change the code it reviews. The agent's own rules refuse `git push`,
`git commit`, `gh pr merge`, `gh pr ready`, and every command that changes
the repository's settings.

## Post as a GitHub App

By default the reviews post as `github-actions[bot]`. To post them under an
app's name, register a GitHub App, make it public so it installs on your
account, and pass its slug to `init`:

```sh
project init --ci-review --ci-app projector-review
```

`init` looks the app's ID up with `gh api apps/<slug>`, which answers only
for a public app, and refuses an app GitHub does not show. It writes the ID
into the workflow as the action's `app-id` input, passes the
`PROJECTOR_APP_PRIVATE_KEY` secret as its `app-private-key` input, records
`review.ci_app` and `review.ci_app_id`, and adds `<slug>[bot]` to
`review.reviewers`. A later `init` rebuilds the workflow from the recorded
ID without asking GitHub, so it works offline. At a terminal
it asks for the path of the private key file you generated in the app's
settings, and has `gh` set it as the `PROJECTOR_APP_PRIVATE_KEY` secret from
that file. Delete the file once the secret holds it.

In each review job and in the summary job, the action's **Mint the app's
token** step creates an installation token with
`actions/create-github-app-token`. The token reaches only the repository the
job runs in, with the review's permissions in the table above, and the
review, the summary's comment, and the link in the description post as
`<slug>[bot]` with it. The app needs those same permissions, and it must be
installed on the repository.

The app has no `contents: write`, so it cannot push code. The summary job
still needs that permission for two things, and it uses the workflow's own
token for both: the push of `refs/projector/summaries`, which goes up with
the checkout's credentials, and the site's rebuild event, which
`project summary publish` sends with the token in
`PROJECTOR_DISPATCH_TOKEN`.

The private key carries the app's full authority over every repository it is
installed on, so the workflow passes it as an input and never as `env`. A
caller's `env` reaches every step of a composite action, the steps that run
the agent and its install scripts included. An input sets no environment
variable in a composite action, so only the step that mints the token, the
one step that reads `app-private-key`, receives the key.

## Override a setting for CI

The review job runs `review.gate` on a trusted head, as a session does. When
a step of the gate needs a tool a hosted runner lacks, give the action a
`config` input. It is written to the runner's `~/.projector.toml`, where an
`[override.<priority>]` table beats the committed setting:

```yaml
      - uses: ninjudd/projector/actions/review@v0
        with:
          agent: ${{ matrix.agent }}
          model: ${{ matrix.model }}
          pr: ${{ github.event.pull_request.number || inputs.pr }}
          config: |
            [override.50.review]
            gate = 'git diff --check "$2" HEAD'
```

An edit like this one makes the workflow one that `init` keeps, rather than
replaces.

## Action inputs

| Input | Default | Effect |
| --- | --- | --- |
| `step` | `review` | `review` runs the agent. `summary` publishes the run's summaries and runs no agent. |
| `pr` | The event's pull request | The pull request to review |
| `agent` | `claude` | `claude`, `codex`, or `command`, which runs the command `review.runner` names |
| `model` | `claude-opus-5-5` for `claude` | The model the agent runs and the review records. `codex` and `command` need one. The workflow `init` writes passes the model `review.ci_agents` names for the job's agent. |
| `effort` | `high` | The agent's reasoning effort |
| `permission-mode` | `auto` | `auto`, or `allowlist`, which refuses whatever no rule allows |
| `rereview` | `false` | Review a head this model already reviewed |
| `config` | Empty | TOML for the runner's `~/.projector.toml` |
| `timeout-minutes` | `45` | The agent's time limit. The job's own limit is 15 minutes longer. |
| `claude-code-version`, `codex-version` | The versions Projector tests | The CLI version the agent runs. The action installs it with `npm` unless the CLI on `PATH` already reports it. |
| `app-id` | Empty | Post as this GitHub App. Empty posts as `github-actions[bot]`. |
| `app-private-key` | Empty | The private key of the app `app-id` names, from the `PROJECTOR_APP_PRIVATE_KEY` secret. Pass it here, never as `env`, so that only the step that mints the token receives it. |
