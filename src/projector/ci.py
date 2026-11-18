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


def parse_agents(text: str) -> tuple[str, ...]:
    """The agents a comma-separated list names, in order and once each, refusing a name that is not an agent."""
    agents = tuple(dict.fromkeys(name.strip() for name in text.split(",") if name.strip()))
    unknown = [name for name in agents if name not in AGENTS]
    if not agents or unknown:
        raise UsageError(f"the CI review agents must be {', '.join(AGENTS)}, separated by commas; got {text!r}")
    return agents


def body(action_ref: str, agents: tuple[str, ...]) -> str:
    template = resources.files("projector").joinpath("templates/review-workflow.yml").read_text(encoding="utf-8")
    env = "\n".join(f"          {name}: ${{{{ secrets.{name} }}}}"
                    for agent in agents for name in CREDENTIALS[agent].values())
    return template.replace("@REF@", action_ref).replace("@AGENTS@", ", ".join(agents)).replace("@ENV@", env)


def workflow_text(action_ref: str, agents: tuple[str, ...]) -> str:
    """The review workflow for `agents`, calling the action at `action_ref`. Its second line records a digest of
    the rest, so a later `init` can tell a file nobody edited, which it replaces, from one somebody did."""
    text = body(action_ref, agents)
    return f"{HEADER}# projector-workflow sha256={hashlib.sha256(text.encode()).hexdigest()}\n{text}"


def unedited(text: str) -> bool:
    """Whether `text` is a review workflow Projector wrote, with any agents and ref, that nobody edited since."""
    found = DIGEST.match(text)
    return bool(found) and hashlib.sha256(text[found.end():].encode()).hexdigest() == found.group(1)
