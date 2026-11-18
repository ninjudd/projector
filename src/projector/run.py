"""`project review run`: review one pull request's head with an agent and no session, as a CI job does.

The command checks that the head may be reviewed here, starts the agent through a preset or a command the
repository names, in a permission mode where nobody is asked, and checks once the agent exits that it
published a verdict on the head. When it did not, the command cleans the review up, says why on the pull
request, and fails. docs/projects/review-in-ci/readme.md § 2 describes the flow.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import review
from .review import ReviewError

PRESETS = ("claude", "codex")
MODES = ("auto", "allowlist")
DEFAULT_LOOP = "run"
DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT_MINUTES = 45
REPORT_LIMIT = 4000

# The commands `review-pr` has an agent run, and the reads around them. A preset allows them outright, so the
# permission mode decides only what the skill does not name.
ALLOWED_COMMANDS = (
    "project *", "gh pr view *", "gh pr diff *", "gh api *", "gh repo view *", "gh auth status *",
    "git diff *", "git log *", "git show *", "git status *", "git rev-parse *", "git merge-base *",
    "git ls-files *", "git cat-file *",
)
# What a reviewer never does, refused whatever the permission mode decides, because the agent reads a diff
# someone else wrote. `gh repo view` stays allowed: a repository's instructions can require a visibility
# check before a write, and in Claude Code a deny rule beats every allow rule.
CHANGING_REPO_COMMANDS = ("archive", "autolink", "create", "delete", "deploy-key", "edit", "fork", "rename", "sync",
                          "unarchive")
DENIED_COMMANDS = (
    "git push *", "git commit *", "gh pr merge *", "gh pr ready *", "gh pr close *",
    *(f"gh repo {command} *" for command in CHANGING_REPO_COMMANDS),
    "gh secret *", "curl *", "wget *",
)
READ_TOOLS = ("Read", "Grep", "Glob")
CODEX_RULES_NAME = "projector-review-run.rules"
# Claude Code's modes: `auto` has a classifier decide what no rule covers, and `dontAsk` refuses it.
CLAUDE_MODES = {"auto": "auto", "allowlist": "dontAsk"}


@dataclass
class Invocation:
    """How to start the agent: its command line, what it reads on standard input, and the files the run writes
    for it and removes afterwards."""

    argv: list[str]
    stdin: Optional[str] = None
    written: list[Path] = field(default_factory=list)


def prompt(number: int, repo: str) -> str:
    return (f"Use the review-pr skill to review pull request {number} in {repo}. No session runs this review, "
            "and nobody can answer a question, so follow the skill's \"Review without a session\" section.")


def claude_invocation(number: int, repo: str, model: str, effort: str, mode: str, scratch: Path,
                      plugin_dir: Optional[Path]) -> Invocation:
    """`claude -p` with Projector's plugin, in a mode that never waits for a prompt."""
    writable = f"/{scratch}/**"
    allowed = [*READ_TOOLS, f"Edit({writable})", f"Write({writable})",
               *(f"Bash({command})" for command in ALLOWED_COMMANDS)]
    argv = ["claude", "-p", f"/projector:review-pr {number}\n\n{prompt(number, repo)}",
            "--model", model, "--effort", effort, "--output-format", "stream-json", "--verbose",
            "--permission-mode", CLAUDE_MODES[mode], "--permission-prompts", "none",
            "--add-dir", str(scratch),
            "--allowedTools", *allowed,
            "--disallowedTools", *(f"Bash({command})" for command in DENIED_COMMANDS)]
    if plugin_dir is not None:
        argv[2:2] = ["--plugin-dir", str(plugin_dir)]
    return Invocation(argv)


def codex_rules() -> str:
    """The Codex rules that refuse what a reviewer never does, in the format `permissions.CODEX_RULES` uses."""
    rules = ["# Written by `project review run` for one run, and removed when it ends.\n"]
    for command in DENIED_COMMANDS:
        pattern = command.removesuffix(" *").split(" ")
        rules.append(f'prefix_rule(\n    pattern = {json.dumps(pattern)},\n    decision = "forbidden",\n'
                     f'    justification = "a review never runs `{" ".join(pattern)}`",\n)\n')
    return "".join(rules)


def codex_invocation(number: int, repo: str, model: str, effort: str, mode: str, scratch: Path,
                     state: Path, checkout: Path, environ: dict[str, str]) -> Invocation:
    """`codex exec` in its workspace-write sandbox, with network on so `gh` reaches GitHub, and the review
    state and the scratch folder writable beside the checkout."""
    home = Path(environ.get("CODEX_HOME") or Path(environ.get("HOME") or Path.home()) / ".codex")
    rules = home / "rules" / CODEX_RULES_NAME
    approval = ["--approve-for-me"] if mode == "auto" else ["-c", 'approval_policy="never"']
    argv = ["codex", "exec", "--model", model, "-c", f'model_reasoning_effort="{effort}"', "--json",
            "--sandbox", "workspace-write", *approval, "-c", "sandbox_workspace_write.network_access=true",
            "--cd", str(checkout), "--add-dir", str(state), "--add-dir", str(scratch), prompt(number, repo)]
    return Invocation(argv, written=[rules])


def command_invocation(command: str, number: int, repo: str) -> Invocation:
    """A command `review.runner` names, which reads the prompt on standard input."""
    argv = shlex.split(command)
    if not argv:
        raise ReviewError("review.runner names an empty command")
    return Invocation(argv, stdin=prompt(number, repo))


def runner(agent: Optional[str], configured: object) -> tuple[str, Optional[str]]:
    """The preset to run, `claude`, `codex`, or `command`, and the command for `command`: `--agent`, then
    `review.runner`, which names a preset or any other command, then `claude`."""
    command = configured if isinstance(configured, str) and configured.strip() else None
    chosen = agent or ("command" if command and command not in PRESETS else command) or "claude"
    if chosen == "command" and (command is None or command in PRESETS):
        raise ReviewError("--agent command runs the command review.runner names, and review.runner names none")
    if chosen not in (*PRESETS, "command"):
        raise ReviewError(f"--agent {chosen!r} is not claude, codex, or command")
    return chosen, command if chosen == "command" else None


def refusal(pr: dict, repo: str) -> Optional[str]:
    """Why a run must not review the pull request, whatever started it, or None. A fork's head is code an
    outsider wrote, and a draft waits on its author."""
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    if (head_repo or "").lower() != repo.lower():
        return f"its head is in {head_repo or 'a deleted fork'}, not {repo}, and a run reviews no fork's head"
    if pr.get("draft"):
        return "it is a draft, and a draft waits on its author"
    return None


def verdicts_on(reviews: list[dict], head: str, model: str) -> list[dict]:
    """The reviewer's Projector verdicts by `model` on `head` among `reviews`, as `reviewer_reviews` lists them,
    newest first."""
    found = [posted for posted in reviews
             if (marker := review.verdict_marker(posted.get("body") or "")) is not None
             and marker.sha == head and review.identity(marker.model) == review.identity(model)]
    return sorted(found, key=lambda posted: posted.get("at") or "", reverse=True)


def stale_start(starts: list[dict], reviews: list[dict], head: str, model: str) -> Optional[int]:
    """The newest live start comment of `model` on `head`. Only one run of a reviewer and an agent holds a pull
    request at a time, so such a comment comes from a run that was cancelled."""
    at = review.now()
    live = [comment for comment in starts if comment["sha"] == head
            and review.identity(comment["model"]) == review.identity(model) and review.is_live(comment, reviews, at)]
    return max(live, key=lambda comment: comment["created_at"])["id"] if live else None


def run_link(environ: dict[str, str]) -> Optional[str]:
    """The GitHub Actions run this command runs in, or None outside one."""
    server, repository, run_id = (environ.get(name) for name in
                                  ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repository}/actions/runs/{run_id}" if server and repository and run_id else None


def failure_body(head: str, model: str, reason: str, link: Optional[str]) -> str:
    where = f"\n\nThe run: {link}" if link else ""
    return (f"<!-- projector-run v=1 sha={head} model={model} result=failed -->\n"
            f"📽️ Projector's review of `{head[:7]}` by {model} did not finish: {reason}{where}")


def report_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text if len(text) <= REPORT_LIMIT else text[:REPORT_LIMIT] + "…"


def start(invocation: Invocation, environ: dict[str, str], cwd: Path, timeout: float) -> tuple[Optional[int], bool]:
    """Run the agent in its own process group, so a run out of time ends with every process it started. Returns
    its exit code, or None when it could not start, and whether it ran out of time."""
    try:
        process = subprocess.Popen(invocation.argv, cwd=cwd, env=environ, start_new_session=True,
                                   stdin=subprocess.PIPE if invocation.stdin is not None else subprocess.DEVNULL)
    except OSError as error:
        print(f"project: could not start {invocation.argv[0]}: {error}", file=sys.stderr)
        return None, False
    try:
        process.communicate(invocation.stdin.encode() if invocation.stdin is not None else None, timeout=timeout)
        return process.returncode, False
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return process.returncode, True


def run(root: Path, number: int, checkout: Path, *, model: str, agent: Optional[str] = None,
        configured_runner: object = None, configured_reviewer: Optional[str] = None, effort: str = DEFAULT_EFFORT,
        mode: str = "auto", timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES, rereview: bool = False,
        loop: str = DEFAULT_LOOP, plugin_dir: Optional[Path] = None, labels: bool = True,
        environ: Optional[dict[str, str]] = None) -> int:
    """Review the head of pull request `number` in the repository of `checkout` with an agent, as
    docs/projects/review-in-ci/readme.md § 2.1 step 6 describes. Returns the exit code: 0 once a verdict on the
    head stands, and 1 when the agent published none."""
    review.model_key(model)
    review.valid_loop(loop)
    if mode not in MODES:
        raise ReviewError(f"--permission-mode {mode!r} is not auto or allowlist")
    chosen, command = runner(agent, configured_runner)
    checkout = review.checkout_root(checkout)
    repo = review.origin_repo(checkout)
    pr = review.pull_request(repo, number)
    why = refusal(pr, repo)
    if why:
        raise ReviewError(f"{repo}#{number} is not reviewed here: {why}")
    head = pr["head"]["sha"]
    reviewer = review.reviewer_login(None, configured_reviewer, review.authenticated_login())
    reviews = review.reviewer_reviews(repo, number, reviewer)
    earlier = verdicts_on(reviews, head, model)
    if earlier and not rereview:
        print(f"{reviewer} already reviewed {repo}#{number} at {head[:7]} with {model}: {earlier[0].get('url')}; "
              "pass --rereview to review it again")
        return 0
    replace = stale_start(review.start_comments(repo, number, reviewer), reviews, head, model)

    base = dict(os.environ if environ is None else environ)
    scratch = Path(tempfile.mkdtemp(prefix="projector-review-run-"))
    report = scratch / "report.md"
    environment = dict(base, PROJECTOR_HEADLESS="1", PROJECTOR_LOOP=loop, PROJECTOR_MODEL=model,
                       PROJECTOR_SCRATCH=str(scratch), PROJECTOR_RUN_REPORT=str(report),
                       PROJECTOR_REREVIEW="1" if rereview else "")
    environment.pop("PROJECTOR_REPLACE", None)
    if replace is not None:
        environment["PROJECTOR_REPLACE"] = str(replace)
    if chosen == "claude":
        invocation = claude_invocation(number, repo, model, effort, mode, scratch, plugin_dir)
    elif chosen == "codex":
        invocation = codex_invocation(number, repo, model, effort, mode, scratch, root, checkout, environment)
    else:
        invocation = command_invocation(command or "", number, repo)
    for path in invocation.written:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(codex_rules(), encoding="utf-8")

    print(f"reviewing {repo}#{number} at {head[:7]} as {reviewer} with {model} through {chosen}"
          + (f", in place of the cancelled run's start comment {replace}" if replace is not None else ""))
    try:
        code, timed_out = start(invocation, environment, checkout, timeout_minutes * 60)
    finally:
        for path in invocation.written:
            path.unlink(missing_ok=True)

    # A verdict counts only when the run posted it, which no clock can tell as surely as a review id can.
    seen = {posted["id"] for posted in earlier}
    published = [posted for posted in verdicts_on(review.reviewer_reviews(repo, number, reviewer), head, model)
                 if posted["id"] not in seen]
    if published:
        print(f"published {published[0].get('url')}")
        return 0
    if timed_out:
        reason = f"the agent ran out of its {timeout_minutes:g} minutes"
    elif code is None:
        reason = f"`{invocation.argv[0]}` could not start"
    else:
        reason = report_text(report) or f"the agent exited {code} and published no verdict"
    try:
        review.finish(root, number, repo, model, True, checkout, labels)
    except ReviewError as error:
        print(f"project: could not clean the review up: {error}", file=sys.stderr)
    review.run_gh(["api", f"repos/{repo}/issues/{number}/comments",
                   "-f", f"body={failure_body(head, model, reason, run_link(base))}"],
                  review.reviewer_token(reviewer))
    print(f"project: {repo}#{number} at {head[:7]} has no verdict by {model}: {reason}", file=sys.stderr)
    return 1
