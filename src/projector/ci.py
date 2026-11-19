"""The GitHub Actions workflow that reviews pull requests with `project review run`, and the secrets it reads, as
docs/projects/review-in-ci/readme.md § 2.8 and § 2.10 describe them."""

from __future__ import annotations

import hashlib
import re
from importlib import resources
from typing import Optional

from .core import UsageError

WORKFLOW_PATH = ".github/workflows/projector-review.yml"
AGENTS = ("claude", "codex")
# `runner` means a self-hosted runner holds the agent's credential, so the workflow passes no secret for it.
RUNNER = "runner"
KINDS = ("subscription", "api-key", RUNNER)
# Each agent's credential secrets, by kind. The workflow passes both, and the action refuses a job that has
# both or neither.
CREDENTIALS = {
    "claude": {"subscription": "CLAUDE_CODE_OAUTH_TOKEN", "api-key": "ANTHROPIC_API_KEY"},
    "codex": {"subscription": "CODEX_AUTH_JSON", "api-key": "OPENAI_API_KEY"},
}
# A Codex ChatGPT login goes stale on a runner that throws its refreshed tokens away, so an API key is the
# kind that keeps working there.
DEFAULT_KIND = {"claude": "subscription", "codex": "api-key"}
WHERE = {
    "CLAUDE_CODE_OAUTH_TOKEN": "run `claude setup-token`, signed in with your subscription, and paste the token "
                               "it prints",
    "ANTHROPIC_API_KEY": "paste an API key from the Claude Console",
    "CODEX_AUTH_JSON": "Projector passes gh the auth.json that `codex login` wrote",
    "OPENAI_API_KEY": "paste an API key from the OpenAI platform",
}
ON_RUNNER = {
    "claude": "set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in the runner's environment, such as its .env file",
    "codex": "run `codex login` as the runner's user, or set OPENAI_API_KEY in the runner's environment",
}
HOSTED = "ubuntu-latest"
# A runner label as a plain YAML scalar in a flow sequence can carry it.
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
# The secret holding a GitHub App's private key. The workflow passes it as the action's `app-private-key` input,
# never as `env`, which would reach every step of the action, so only the step that mints the app's token gets it.
APP_KEY = "PROJECTOR_APP_PRIVATE_KEY"
HEADER = "# Written by `project init --ci-review`, which replaces this file while nobody has edited it.\n"
DIGEST = re.compile(r"\A" + re.escape(HEADER) + r"# projector-workflow sha256=([0-9a-f]{64})\n")


# The agents the action gives a model when the workflow names none. Every other agent's job would stop before it
# reviews anything, so setup refuses it without a model rather than guessing a model ID.
DEFAULT_MODEL = {"claude": "claude-opus-5-5"}
# A model ID as a plain YAML scalar can carry it: no space, quote, comma, or `=`, and no leading flow indicator.
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*\Z")


def parse_agents(text: str) -> dict[str, str]:
    """The agents a comma-separated list names, in order and once each, each with the model an `agent=model`
    entry gives it, or "" for the action's default. Refuses a name that is not an agent, a model that is not a
    model ID, and an agent the action has no default model for that the list gives none."""
    agents: dict[str, str] = {}
    for entry in (part.strip() for part in text.split(",")):
        if not entry:
            continue
        name, _, model = (piece.strip() for piece in entry.partition("="))
        if name not in AGENTS:
            raise UsageError(f"the CI review agents must be {', '.join(AGENTS)}, separated by commas; got {text!r}")
        if model and not MODEL.match(model):
            raise UsageError(f"{model!r} in {text!r} is not a model ID")
        agents.setdefault(name, model)
    if not agents:
        raise UsageError(f"the CI review agents must be {', '.join(AGENTS)}, separated by commas; got {text!r}")
    for name, model in agents.items():
        if not model and name not in DEFAULT_MODEL:
            raise UsageError(f"the {name} review needs a model, and the action has none for it; name one as "
                             f"{name}=<model> in {text!r}")
    return agents


def format_agents(agents: dict[str, str]) -> str:
    """`agents` as `review.ci_agents` records them, which `parse_agents` reads back."""
    return ", ".join(f"{name}={model}" if model else name for name, model in agents.items())


def parse_runs_on(text: str) -> list[str]:
    """The runner labels a comma-separated list names, in order and once each. Refuses a list with none, and a
    label the workflow could not write unquoted."""
    labels: list[str] = []
    for label in (part.strip() for part in text.split(",")):
        if label and not LABEL.match(label):
            raise UsageError(f"{label!r} in {text!r} is not a runner label")
        if label and label not in labels:
            labels.append(label)
    if not labels:
        raise UsageError(f"the CI review runner needs at least one label, separated by commas; got {text!r}")
    return labels


def format_runs_on(labels: list[str]) -> str:
    """`labels` as `review.ci_runs_on` records them, which `parse_runs_on` reads back."""
    return ", ".join(labels)


def body(action_ref: str, agents: dict[str, str], app_id: Optional[int] = None,
         runs_on: Optional[list[str]] = None, credential: Optional[str] = None) -> str:
    if credential == RUNNER and not runs_on:
        raise UsageError("a GitHub-hosted runner holds no credential of yours, so the runner credential needs "
                         "your own runner's labels, from --ci-runs-on or review.ci_runs_on")
    template = resources.files("projector").joinpath("templates/review-workflow.yml").read_text(encoding="utf-8")
    matrix = "\n".join(f"          - agent: {name}" + (f"\n            model: {model}" if model else "")
                       for name, model in agents.items())
    app = ""
    if app_id is not None:
        app = f"          app-id: '{app_id}'\n          app-private-key: ${{{{ secrets.{APP_KEY} }}}}\n"
    # A secret that is not set reads as empty, which would hide the credential a runner's environment holds.
    env = ""
    if credential != RUNNER:
        env = "        env:\n" + "".join(f"          {name}: ${{{{ secrets.{name} }}}}\n"
                                         for agent in agents for name in CREDENTIALS[agent].values())
    labels = runs_on or [HOSTED]
    runner = labels[0] if len(labels) == 1 else f"[{', '.join(labels)}]"
    return (template.replace("@REF@", action_ref).replace("@MATRIX@", matrix).replace("@APP@", app)
            .replace("@ENV@", env).replace("@RUNS_ON@", runner))


def workflow_text(action_ref: str, agents: dict[str, str], app_id: Optional[int] = None,
                  runs_on: Optional[list[str]] = None, credential: Optional[str] = None) -> str:
    """The review workflow for `agents`, calling the action at `action_ref`, posting as the GitHub App `app_id`
    names when it is given, on the runners `runs_on` labels or a GitHub-hosted one, and passing no credential
    secret when `credential` is `runner`. Its second line records a digest of the rest, so a later `init` can
    tell a file nobody edited, which it replaces, from one somebody did."""
    text = body(action_ref, agents, app_id, runs_on, credential)
    return f"{HEADER}# projector-workflow sha256={hashlib.sha256(text.encode()).hexdigest()}\n{text}"


def unedited(text: str) -> bool:
    """Whether `text` is a review workflow Projector wrote, with any agents and ref, that nobody edited since."""
    found = DIGEST.match(text)
    return bool(found) and hashlib.sha256(text[found.end():].encode()).hexdigest() == found.group(1)
