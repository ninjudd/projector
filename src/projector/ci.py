"""The GitHub Actions workflow that reviews pull requests with `project review run`, and the secrets it reads, as
docs/projects/review-in-ci/readme.md § 2.8 and § 2.10 describe them."""

from __future__ import annotations

import hashlib
import re
from importlib import resources

from .core import UsageError

WORKFLOW_PATH = ".github/workflows/projector-review.yml"
AGENTS = ("claude", "codex")
KINDS = ("subscription", "api-key")
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


def body(action_ref: str, agents: dict[str, str]) -> str:
    template = resources.files("projector").joinpath("templates/review-workflow.yml").read_text(encoding="utf-8")
    matrix = "\n".join(f"          - agent: {name}" + (f"\n            model: {model}" if model else "")
                       for name, model in agents.items())
    env = "\n".join(f"          {name}: ${{{{ secrets.{name} }}}}"
                    for agent in agents for name in CREDENTIALS[agent].values())
    return template.replace("@REF@", action_ref).replace("@MATRIX@", matrix).replace("@ENV@", env)


def workflow_text(action_ref: str, agents: dict[str, str]) -> str:
    """The review workflow for `agents`, calling the action at `action_ref`. Its second line records a digest of
    the rest, so a later `init` can tell a file nobody edited, which it replaces, from one somebody did."""
    text = body(action_ref, agents)
    return f"{HEADER}# projector-workflow sha256={hashlib.sha256(text.encode()).hexdigest()}\n{text}"


def unedited(text: str) -> bool:
    """Whether `text` is a review workflow Projector wrote, with any agents and ref, that nobody edited since."""
    found = DIGEST.match(text)
    return bool(found) and hashlib.sha256(text[found.end():].encode()).hexdigest() == found.group(1)
