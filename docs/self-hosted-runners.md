# Run reviews on your own runners

The CI review workflow that `project init --ci-review` writes runs on a
GitHub-hosted runner by default. You can run it on self-hosted runners
instead: machines you control, with your network, your tools, and your
credentials. This page shows how to set one up, give it the agent's
credential, and keep it safe. It assumes you have read
[Review pull requests in GitHub Actions](review-in-ci.md).

## Decide whether you need one

A hosted runner is simpler, so use your own runner only when it gives you
something a hosted one cannot:

- **Internal tools.** The review can read a service on your network, such
  as a self-hosted Grafana, an internal API, or an MCP server that only your
  network reaches.
- **Your own credentials.** The agent's credential stays on a machine you
  control, in place of a repository or organization secret.
- **A ChatGPT login for Codex.** Codex refreshes its login and writes it
  back to `auth.json`. A hosted runner throws that file away after every
  job, so the login goes stale there. A persistent runner keeps it.
- **Cost and control.** You choose the hardware, the operating system, the
  installed tools, and what the machine can reach.

Use a self-hosted runner only for a private repository. GitHub advises
against self-hosted runners for public repositories, and OpenAI's rule is
that a persistent ChatGPT login never serves a public repository. The review
workflow already skips pull requests from forks, but a public repository's
collaborators and its history are still more than you can vouch for.

## Prepare the runner

The review action runs bash steps on the runner, so the machine needs:

- Linux or macOS, with `bash`, `git`, and `tar`.
- Python 3.11 or newer, as `python3`. The action runs Projector from its own
  source with it.
- The GitHub CLI, `gh`, which the review uses for every call to GitHub.
- Node.js and `npm`, unless you install the agent's CLI yourself, as
  [Install the agent's CLI ahead of time](#install-the-agents-cli-ahead-of-time)
  describes.
- Outbound HTTPS to GitHub, including the Actions cache and artifact
  services, to your model provider's API, and to the npm registry when the
  action installs the CLI.

The runner application brings its own Node.js for the JavaScript actions the
review uses, such as `actions/checkout` and `actions/cache`.

Register the runner on the repository or the organization, as
[GitHub's self-hosted runner documentation](https://docs.github.com/en/actions/hosting-your-own-runners)
describes. Give it a label that names its purpose, so that only the review
jobs land on it:

```sh
./config.sh --url https://github.com/<owner>/<repo> --token <token> --labels projector
```

## Point the workflow at the runner

Pass the runner's labels to `init`, separated by commas. A job runs on a
runner that has every label you name:

```sh
project init --ci-review --ci-runs-on self-hosted,linux,projector --ci-credential runner
```

`init` writes both jobs of the workflow with those labels and records them
as `review.ci_runs_on`, so every later `init` keeps the workflow on your
runner:

```yaml
    runs-on: [self-hosted, linux, projector]
```

`--ci-credential runner` says that the runner holds the agent's credential,
which the next section sets up. `init` records it as `review.ci_credential`,
sets no credential secret, and writes no `secrets.*` variable into the
workflow. Pass it whenever the runner holds the credential: a secret that is
not set reads as an empty string, and an empty variable in the review step's
environment would hide the runner's own value. `init` refuses
`--ci-credential runner` without runner labels, because a hosted runner
holds no credential of yours.

To compare the workflow with one you edited, or to see it before you run
`init`, print it:

```sh
project review ci-workflow --runs-on self-hosted,linux,projector --credential runner
```

To move the reviews back to a hosted runner, name it and the kind of secret
you want, `subscription` or `api-key`, and set the secret `init` asks for:

```sh
project init --ci-runs-on ubuntu-latest --ci-credential api-key
```

## Give the runner the agent's credential

The review job checks for exactly one credential for its agent before it
starts, and refuses a job with none or with two.

### Claude

Put a token from `claude setup-token`, or an API key, in the runner's
environment. The runner application reads a `.env` file in its own
directory when it starts, and every job it runs gets those variables:

```sh
# .env in the runner's directory
CLAUDE_CODE_OAUTH_TOKEN=<the token claude setup-token prints>
```

Restart the runner service after you change the file. Set
`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`, never both.

### Codex

To review with a ChatGPT plan, sign Codex in once, as the user the runner
runs as. First have Codex keep its login in a file, by adding this line to
`config.toml` in the Codex home, `~/.codex` unless `CODEX_HOME` names
another:

```toml
cli_auth_credentials_store = "file"
```

Then sign in. On a machine without a browser, `--device-auth` gives you a
code to enter on another device:

```sh
codex login --device-auth
```

When the job finds `auth.json` in the runner's Codex home and no Codex
secret, it uses that home in place: Codex refreshes the login there, and
the next job reads the refreshed tokens. The action links Projector's skills
into the home as `skills/projector`, and never writes `auth.json`.

Follow OpenAI's rules for a login that a runner uses:

- **One login for each runner.** A runner runs one job at a time, so one
  login per runner keeps its refreshes in order. Never share an `auth.json`
  between machines, or between runners that run jobs at the same time.
- **One Codex home for each runner.** When several runners share a machine,
  give each one its own home, with `CODEX_HOME` in its `.env` file, and sign
  each one in.
- **Sign in again when the refresh fails.** When a review fails with `401`,
  run `codex login` on the runner again.

A login needs a runner that keeps its files between jobs. On an ephemeral
runner, put `OPENAI_API_KEY` in the runner's environment instead, and give
the job no login.

## Install the agent's CLI ahead of time

The action installs the agent's CLI with `npm install --global` at the
version it pins, in its `claude-code-version` and `codex-version` inputs.
It skips the install when the CLI on `PATH` reports exactly that version,
so you can install the CLI yourself on a runner whose user cannot write
where `npm` installs:

```sh
npm install --global @anthropic-ai/claude-code@<claude-code-version>
npm install --global @openai/codex@<codex-version>
```

Read the pinned versions from `actions/review/action.yml` at the ref your
workflow uses. A new Projector release can pin a newer version, and then the
action installs it with `npm` until you update the runner.

## Reach internal tools from the review

The agent runs as the runner's user, so it reaches what that user reaches:
the network, the files, and the MCP servers configured for that user. Set
the servers up for the agent on the runner:

- **Claude.** Add a server for the runner's user with
  `claude mcp add --scope user`, and deny the tools a review does not need
  with `permissions.deny` in `~/.claude/settings.json`.
- **Codex.** Add a server with `codex mcp add`, which writes it to
  `config.toml` in the Codex home, and list the tools the review needs in
  the server's `enabled_tools`. The job reads that `config.toml` only when
  it uses the runner's own Codex login. With an API key, it gives Codex a
  home of its own for the job.

Give each tool a token scoped to read only what reviews need, such as a
Grafana viewer token for one folder of dashboards.

The review runs `review.gate` on the runner too, so install every tool the
gate needs there. Put any setting you want only on the runner in the runner
user's `~/.projector.toml`, rather than in the action's `config` input,
which adds to that file on every job.

## Keep the runner safe

The agent reads the pull request's code in a permission mode that never
waits for a prompt. A pull request can carry text written to steer it, so
anything the agent can reach, it could be led to quote into a review
comment. Plan the runner around that:

- **Use a private repository.** Never attach the runner to a public one.
- **Prefer ephemeral or just-in-time runners.** A runner that takes one job
  and is then removed keeps nothing that one job could leave for the next.
  A persistent runner is the price of a Codex ChatGPT login.
- **Restrict the runner to the repositories that need it.** Put it in a
  dedicated runner group that only those repositories can use.
- **Run it as a dedicated user** that owns nothing beyond the runner, the
  agent's credential, and the tools reviews need.
- **Give it read-only tokens.** Scope each internal tool's token to what a
  review reads, and give the runner no credential that can change anything.

## Troubleshoot a review job

These messages come from the action's **Check the agent's credentials**
step, and from what GitHub shows for the job:

- **`neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set for claude`.**
  The runner's environment lacks the credential. Check the `.env` file and
  restart the runner. When the workflow still passes `secrets.*` variables,
  run `project init --ci-credential runner`, because an empty secret hides
  the runner's value.
- **`neither CODEX_AUTH_JSON nor OPENAI_API_KEY is set for codex`.** The
  runner has no API key in its environment and no `auth.json` in its Codex
  home. Run `codex login` as the runner's user, with
  `cli_auth_credentials_store = "file"` set.
- **`both CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are set for claude`.**
  The runner's environment, the repository's secrets, or the two together
  hold both. Remove the one you do not mean to use.
- **`both OPENAI_API_KEY and the Codex login in <path> are on the runner`.**
  Remove the key from the runner's environment to use the login, or sign
  Codex out with `codex logout` to use the key.
- **The job waits with "Waiting for a runner to pick up this job".** No
  online runner has every label in `runs-on`. Compare the labels in
  `review.ci_runs_on` with the runner's labels in the repository's
  **Settings**, under **Actions** and **Runners**.
- **`npm` fails with `EACCES` while it installs the agent.** The runner's user
  cannot write where `npm` installs. Install the CLI at the pinned version
  yourself, as
  [Install the agent's CLI ahead of time](#install-the-agents-cli-ahead-of-time)
  describes.
