"""`project review run`: review one pull request's head with an agent and no session, as a CI job does.

The command checks that the head may be reviewed here, starts the agent through a preset or a command the
repository names, in a permission mode where nobody is asked, and checks once the agent exits that it
published a verdict on the head. When it did not, the command cleans the review up, says why on the pull
request, and fails. docs/projects/review-in-ci/readme.md § 2 describes the flow.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import core, review
from .review import TOKEN_LOGIN, ReviewError

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
# The login of the workflow's own token, which reaches only the repository the workflow runs in.
WORKFLOW_LOGIN = "github-actions[bot]"
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+\Z")
VISIBILITIES = ("PUBLIC", "PRIVATE", "INTERNAL")
# Visibilities whose readers the owner chose, so one such repository's code may reach another's review.
SHARED = ("PRIVATE", "INTERNAL")
# Where the action has the run clone the context repositories. Elsewhere the run clones them into a temporary
# folder it removes once the agent exits.
CONTEXT_DIR = "PROJECTOR_CONTEXT_DIR"
# A token that only reads the context repositories, which the action mints apart from the review's. The run
# checks and clones them with it, and removes it from the agent's environment. Without it, `gh`'s own token does.
CONTEXT_TOKEN = "PROJECTOR_CONTEXT_TOKEN"
# The outcome of the action's step that mints that token, `failure` when GitHub refused it, as it does whenever
# the app cannot reach one of the repositories it was asked for.
CONTEXT_MINT = "PROJECTOR_CONTEXT_MINT"


@dataclass
class Invocation:
    """How to start the agent: its command line, what it reads on standard input, and the files the run writes
    for it and removes afterwards."""

    argv: list[str]
    stdin: Optional[str] = None
    written: list[Path] = field(default_factory=list)


def prompt(number: int, repo: str, context: tuple[Path, ...] = ()) -> str:
    text = (f"Use the review-pr skill to review pull request {number} in {repo}. No session runs this review, "
            "and nobody can answer a question, so follow the skill's \"Review without a session\" section.")
    if context:
        text += (" These folders hold other repositories at their default branches, to read as context for the "
                 "change and never to review: " + ", ".join(str(folder) for folder in context) + ".")
    return text


def claude_invocation(number: int, repo: str, model: str, effort: str, mode: str, scratch: Path,
                      plugin_dir: Optional[Path], context: tuple[Path, ...] = ()) -> Invocation:
    """`claude -p` with Projector's plugin, in a mode that never waits for a prompt, reading each `context`
    folder and writing none."""
    writable = f"/{scratch}/**"
    allowed = [*READ_TOOLS, f"Edit({writable})", f"Write({writable})",
               *(f"Bash({command})" for command in ALLOWED_COMMANDS)]
    argv = ["claude", "-p", f"/projector:review-pr {number}\n\n{prompt(number, repo, context)}",
            "--model", model, "--effort", effort, "--output-format", "stream-json", "--verbose",
            "--permission-mode", CLAUDE_MODES[mode], "--permission-prompts", "none",
            "--add-dir", str(scratch), *(part for folder in context for part in ("--add-dir", str(folder))),
            "--allowedTools", *allowed,
            "--disallowedTools", *(f"Bash({command})" for command in DENIED_COMMANDS),
            *(f"{tool}(/{folder}/**)" for folder in context for tool in ("Edit", "Write"))]
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
                     state: Path, checkout: Path, environ: dict[str, str],
                     context: tuple[Path, ...] = ()) -> Invocation:
    """`codex exec` in its workspace-write sandbox, with network on so `gh` reaches GitHub, and the review
    state and the scratch folder writable beside the checkout. The sandbox reads every folder, so each
    `context` folder needs only the prompt's mention. It stays unwritable only where `PROJECTOR_CONTEXT_DIR`
    puts it outside `/tmp` and `$TMPDIR`, which the sandbox writes and where the temporary folder a run uses
    without that variable lies."""
    home = Path(environ.get("CODEX_HOME") or Path(environ.get("HOME") or Path.home()) / ".codex")
    rules = home / "rules" / CODEX_RULES_NAME
    approval = ["--approve-for-me"] if mode == "auto" else ["-c", 'approval_policy="never"']
    argv = ["codex", "exec", "--model", model, "-c", f'model_reasoning_effort="{effort}"', "--json",
            "--sandbox", "workspace-write", *approval, "-c", "sandbox_workspace_write.network_access=true",
            "--cd", str(checkout), "--add-dir", str(state), "--add-dir", str(scratch),
            prompt(number, repo, context)]
    return Invocation(argv, written=[rules])


def command_invocation(command: str, number: int, repo: str, context: tuple[Path, ...] = ()) -> Invocation:
    """A command `review.runner` names, which reads the prompt on standard input."""
    argv = shlex.split(command)
    if not argv:
        raise ReviewError("review.runner names an empty command")
    return Invocation(argv, stdin=prompt(number, repo, context))


def parse_context_repos(listed: object) -> list[str]:
    """The `owner/name` repositories `review.context_repos` lists, in order and once each, warning about an
    entry that names none."""
    names: list[str] = []
    for entry in (part.strip() for part in (listed if isinstance(listed, str) else "").split(",")):
        if not entry:
            continue
        if not REPOSITORY.match(entry):
            print(f"project: left {entry!r} out of the context repositories: it is not owner/name", file=sys.stderr)
        elif entry.lower() not in {name.lower() for name in names}:
            names.append(entry)
    return names


def token_repositories(repo: str, listed: object) -> list[str]:
    """The names, without the owner, of the context repositories a token for reviewing `repo` reads: each one
    `listed` names of `repo`'s owner, other than `repo`."""
    owner = repo.partition("/")[0].lower()
    return [entry.partition("/")[2] for entry in parse_context_repos(listed)
            if entry.partition("/")[0].lower() == owner and entry.lower() != repo.lower()]


def visibility(repo: str, token: Optional[str] = None) -> Optional[str]:
    """`repo`'s visibility as GitHub reports it to `token`, or to `gh`'s own token, `PUBLIC`, `PRIVATE`, or
    `INTERNAL`, or None when the check fails, which proves nothing."""
    try:
        found = review.run_gh(["repo", "view", repo, "--json", "visibility", "--jq", ".visibility"], token).strip()
    except ReviewError:
        return None
    return found if found in VISIBILITIES else None


def context_repos(repo: str, listed: object, environ: dict[str, str]) -> list[str]:
    """The context repositories a review of `repo` may read, as docs/projects/review-in-ci/readme.md § 2.9
    describes, warning about each one it leaves out. A repository must share `repo`'s owner, because an app
    token reaches one account, and be public, or private or internal beside a private or internal `repo`, by a
    check that succeeded."""
    names = parse_context_repos(listed)
    if names and environ.get(TOKEN_LOGIN) == WORKFLOW_LOGIN:
        print(f"project: left out the context repositories {', '.join(names)}: the workflow's own token reaches "
              f"only {repo}, so the review needs a GitHub App whose installation covers them", file=sys.stderr)
        return []
    if names and environ.get(CONTEXT_MINT) == "failure":
        print(f"project: left out the context repositories {', '.join(names)}: GitHub refused a token for them, "
              "because the app cannot reach one of them; check that the app is installed on each, or drop the "
              "missing one from review.context_repos", file=sys.stderr)
        return []
    owner = repo.partition("/")[0].lower()
    candidates = []
    for name in names:
        if name.lower() == repo.lower():
            print(f"project: left {name} out of the context repositories: it is the repository under review",
                  file=sys.stderr)
        elif name.partition("/")[0].lower() != owner:
            print(f"project: left {name} out of the context repositories: an app token reaches one account's "
                  f"repositories, and {name} is not {repo}'s owner's", file=sys.stderr)
        else:
            candidates.append(name)
    own = visibility(repo) if candidates else None
    kept = []
    for name in candidates:
        theirs = visibility(name, environ.get(CONTEXT_TOKEN) or None)
        if theirs == "PUBLIC" or (theirs in SHARED and own in SHARED):
            kept.append(name)
        elif theirs is None:
            print(f"project: left {name} out of the context repositories: its visibility check failed",
                  file=sys.stderr)
        elif own is None:
            print(f"project: left {name} out of the context repositories: it is {theirs.lower()}, and {repo}'s own "
                  "visibility check failed", file=sys.stderr)
        else:
            print(f"project: left {name} out of the context repositories: it is {theirs.lower()}, and {repo} is "
                  f"{own.lower()}, so its code would reach readers who cannot see it", file=sys.stderr)
    return kept


def clone_context(names: list[str], folder: Path, token: Optional[str] = None) -> tuple[Path, ...]:
    """Clone each repository in `names` at its default branch, depth 1, into `folder`/<owner>/<name>, with
    `token`, or `gh`'s own token, and return the folders that cloned, warning about each that did not."""
    cloned = []
    for name in names:
        target = folder / name
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            review.run_gh(["repo", "clone", name, str(target), "--", "--depth", "1", "--quiet"], token)
        except ReviewError as error:
            print(f"project: left {name} out of the context repositories: {error}", file=sys.stderr)
            continue
        cloned.append(target)
    return tuple(cloned)


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


def verdicts_on(reviews: list[dict], head: Optional[str], model: str) -> list[dict]:
    """The reviewer's Projector verdicts by `model` on `head`, or on any head when `head` is None, among
    `reviews`, as `reviewer_reviews` lists them, newest first."""
    found = [posted for posted in reviews
             if (marker := review.verdict_marker(posted.get("body") or "")) is not None
             and head in (None, marker.sha) and review.identity(marker.model) == review.identity(model)]
    return sorted(found, key=lambda posted: posted.get("at") or "", reverse=True)


def stored(paths: review.Paths) -> Optional[dict]:
    """The model's review state for the pull request, or None when there is none or it cannot be read."""
    try:
        return review.stored_state(paths)
    except (ReviewError, OSError, ValueError):
        return None


def set_up_here(before: Optional[dict], after: Optional[dict], loop: str) -> bool:
    """Whether the review state `after` the agent ran is the one its `setup` wrote: it names the run's loop and
    a start comment the state `before` the run did not. Otherwise the state, its lock, and its worktrees are
    another review's, such as one another loop holds, whose lock refused the agent's `setup`."""
    if after is None or after.get("loop") != loop:
        return False
    started = (after.get("start_comment") or {}).get("id")
    return started is not None and started != ((before or {}).get("start_comment") or {}).get("id")


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
    """Run the agent in its own process group, so a run out of time ends with every process it started, on
    Windows too. Returns its exit code, or None when it could not start, and whether it ran out of time."""
    try:
        process = subprocess.Popen(invocation.argv, cwd=cwd, env=environ, **core.process_group(),
                                   stdin=subprocess.PIPE if invocation.stdin is not None else subprocess.DEVNULL)
    except OSError as error:
        print(f"project: could not start {invocation.argv[0]}: {error}", file=sys.stderr)
        return None, False
    try:
        process.communicate(invocation.stdin.encode() if invocation.stdin is not None else None, timeout=timeout)
        return process.returncode, False
    except subprocess.TimeoutExpired:
        core.kill_group(process)
        process.wait()
        return process.returncode, True


def run(root: Path, number: int, checkout: Path, *, model: str, agent: Optional[str] = None,
        configured_runner: object = None, configured_reviewer: Optional[str] = None, effort: str = DEFAULT_EFFORT,
        mode: str = "auto", timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES, rereview: bool = False,
        loop: str = DEFAULT_LOOP, plugin_dir: Optional[Path] = None, labels: bool = True,
        context_listed: object = None, environ: Optional[dict[str, str]] = None) -> int:
    """Review the head of pull request `number` in the repository of `checkout` with an agent, as
    docs/projects/review-in-ci/readme.md § 2.1 step 6 describes. Returns the exit code: 0 once a verdict on the
    head stands, or the run published one on a head pushed while it ran, and 1 when the agent published none."""
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
    names = context_repos(repo, context_listed, base)
    folder = Path(base.get(CONTEXT_DIR) or tempfile.mkdtemp(prefix="projector-review-context-")) if names else None
    context = clone_context(names, folder, base.get(CONTEXT_TOKEN) or None) if folder is not None else ()
    scratch = Path(tempfile.mkdtemp(prefix="projector-review-run-"))
    report = scratch / "report.md"
    environment = dict(base, PROJECTOR_HEADLESS="1", PROJECTOR_LOOP=loop, PROJECTOR_MODEL=model,
                       PROJECTOR_SCRATCH=str(scratch), PROJECTOR_RUN_REPORT=str(report),
                       PROJECTOR_REREVIEW="1" if rereview else "")
    environment.pop("PROJECTOR_REPLACE", None)
    # The agent reads the clones; a token for their repositories would let it reach them some other way.
    environment.pop(CONTEXT_TOKEN, None)
    if replace is not None:
        environment["PROJECTOR_REPLACE"] = str(replace)
    if chosen == "claude":
        invocation = claude_invocation(number, repo, model, effort, mode, scratch, plugin_dir, context)
    elif chosen == "codex":
        invocation = codex_invocation(number, repo, model, effort, mode, scratch, root, checkout, environment,
                                      context)
    else:
        invocation = command_invocation(command or "", number, repo, context)
    for path in invocation.written:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(codex_rules(), encoding="utf-8")

    print(f"reviewing {repo}#{number} at {head[:7]} as {reviewer} with {model} through {chosen}"
          + (f", in place of the cancelled run's start comment {replace}" if replace is not None else "")
          + (f", with {', '.join(str(path.relative_to(folder)) for path in context)} to read as context"
             if folder is not None and context else ""))
    paths = review.Paths(root, repo, number, model)
    before, held = stored(paths), set(paths.locks())
    try:
        code, timed_out = start(invocation, environment, checkout, timeout_minutes * 60)
    finally:
        for path in invocation.written:
            path.unlink(missing_ok=True)
        if folder is not None and not base.get(CONTEXT_DIR):
            shutil.rmtree(folder, ignore_errors=True)

    # A verdict counts only when the run posted it, which no clock can tell as surely as a review id can. It
    # may name a newer head than `head`, because the agent moves its review to a head pushed while it runs.
    seen = {posted["id"] for posted in reviews}
    published = [posted for posted in verdicts_on(review.reviewer_reviews(repo, number, reviewer), None, model)
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
        if set_up_here(before, stored(paths), loop):
            # The watch daemon can hand the review over while the run reads the state, so `finish` checks that
            # the lock still names the run's loop as it deletes anything.
            review.finish(root, number, repo, model, True, checkout, labels, holder=loop)
        else:
            # A setup stopped between taking its lock and writing the state, as a timeout can stop it, leaves
            # only the lock, which would refuse the next run's setup for a day.
            with review.review_mutex(paths.folder):
                for path in paths.locks():
                    if path not in held and (lock := review.read_lock(path)) is not None and lock.loop == loop:
                        path.unlink(missing_ok=True)
            if before is not None:
                print(f"project: left the review state of {repo}#{number} by {model} alone, because the agent did "
                      "not set it up", file=sys.stderr)
    except ReviewError as error:
        print(f"project: could not clean the review up: {error}", file=sys.stderr)
    review.run_gh(["api", f"repos/{repo}/issues/{number}/comments",
                   "-f", f"body={failure_body(head, model, reason, run_link(base))}"],
                  review.reviewer_token(reviewer))
    print(f"project: {repo}#{number} at {head[:7]} has no verdict by {model}: {reason}", file=sys.stderr)
    return 1
