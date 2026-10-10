from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import http.client
import importlib.metadata as metadata
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import url2pathname, urlopen

from . import review, site, summary
from .permissions import allow_publish
from .summary import WORKFLOW_PATH
from .config import ConfigError
from .config import load as load_config
from .core import (
    DISTRIBUTION,
    PRIORITIES,
    STATUSES,
    EnvironmentError,
    FileAction,
    InitError,
    ProjectorError,
    ProjectStore,
    UsageError,
    discover_git_root,
    distribution_version,
    grouped_projects,
    json_scalar,
    json_text,
)


UPSTREAM = "ninjudd/projector"
INSTALLER_URL = "https://projector.bot/install.sh"


def install_record() -> dict:
    """Where pip recorded this command's install came from, as `direct_url.json` holds it."""

    try:
        distribution = metadata.distribution(DISTRIBUTION)
    except metadata.PackageNotFoundError as error:
        raise EnvironmentError(
            "cannot upgrade: project is not an installed distribution"
        ) from error
    text = distribution.read_text("direct_url.json")
    if text is None:
        raise EnvironmentError(
            "cannot upgrade: the installed command does not record where it came from"
        )
    return json.loads(text)


def checkout(record: dict) -> Optional[Path]:
    """The checkout this command was installed from, or None for any other install.

    pip records the source of every install in `direct_url.json`, so the
    command can find its own installer without being told where the checkout
    is. A command installed from a release archive or a Git URL has none.
    """

    if "dir_info" not in record:
        return None
    path = Path(url2pathname(urlparse(record["url"]).path))
    if not path.is_dir():
        raise EnvironmentError(f"cannot upgrade: install source no longer exists: {path}")
    return path


def github_repository(url: str) -> Optional[str]:
    """owner/repo of a GitHub archive or Git URL, the two ways a release installs."""

    match = re.match(r"(?:git\+)?https://github\.com/([^/]+)/([^/@#]+?)(?:\.git)?(?:[/@#]|$)", url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def release_installer(record: dict) -> tuple[str, dict[str, str]]:
    """The released installer to run, and the environment that points it at this install's repository.

    An install from Projector's own repository upgrades through the installer
    projector.bot serves, which is the one from the newest release. One from a
    fork upgrades from that fork, through its own released installer.
    """

    environment = dict(os.environ)
    override = os.environ.get("PROJECTOR_INSTALLER_URL")
    repository = github_repository(record.get("url", "")) or UPSTREAM
    if repository.lower() != UPSTREAM:
        environment.setdefault("PROJECTOR_REPO", repository)
    ref = environment.get("PROJECTOR_REF", "v0")
    if override:
        return override, environment
    if repository.lower() == UPSTREAM:
        return INSTALLER_URL, environment
    return f"https://raw.githubusercontent.com/{repository}/{ref}/install.sh", environment


def fetch_installer(url: str, target: Path) -> None:
    try:
        with urlopen(url, timeout=30) as response:
            target.write_bytes(response.read())
    except OSError as error:
        raise EnvironmentError(f"cannot upgrade: could not download the installer from {url}: {error}") from error


def run_upgrade(targets: list[str]) -> int:
    """Run the installer from anywhere, with the same targets, output, and exit status.

    Installation belongs to the installer, which already knows how to place
    the CLI and each host plugin, so nothing here decides what an upgrade is.
    A checkout install runs the checkout's `install.sh`. Any other install --
    from a release archive or a Git URL -- downloads the released installer
    and runs that, which reinstalls from the newest release without git.
    """

    record = install_record()
    source = checkout(record)
    # `install.sh cli` rewrites this package under the running interpreter.
    # Everything this process still needs is imported already, so nothing
    # below reaches for a file the reinstall is in the middle of replacing.
    if source is not None:
        script = source / "install.sh"
        if not script.is_file():
            raise EnvironmentError(f"cannot upgrade: {script} does not exist")
        command = [str(script), *targets]
        print(f"+ {shlex.join(command)}", file=sys.stderr)
        return subprocess.run(command, check=False).returncode
    url, environment = release_installer(record)
    with tempfile.TemporaryDirectory(prefix="projector-upgrade-") as scratch:
        script = Path(scratch) / "install.sh"
        fetch_installer(url, script)
        print(f"+ curl -fsSL {url} | bash -s -- {shlex.join(targets)}".rstrip(), file=sys.stderr)
        return subprocess.run(["bash", str(script), *targets], env=environment, check=False).returncode


RELEASE_CHECK_SECONDS = 24 * 60 * 60
RELEASE_CHECK_TIMEOUT = 2


def release_manifest_url(record: dict) -> str:
    """The plugin manifest of the release `upgrade` would install, which carries the CLI's version too."""

    repository = github_repository(record.get("url", "")) or UPSTREAM
    ref = os.environ.get("PROJECTOR_REF", "v0")
    return f"https://raw.githubusercontent.com/{repository}/{ref}/.claude-plugin/plugin.json"


def release_cache_file() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "projector" / "release.json"


def fetch_release_version(url: str) -> Optional[str]:
    """The manifest's version, or None when it does not arrive within RELEASE_CHECK_TIMEOUT.

    `urlopen`'s timeout bounds each socket operation rather than the fetch,
    and name resolution has none, so the fetch runs in a daemon thread that
    the command stops waiting for.
    """

    versions: list[str] = []

    def fetch() -> None:
        try:
            with urlopen(url, timeout=RELEASE_CHECK_TIMEOUT) as response:
                manifest = json.loads(response.read())
        except (OSError, ValueError, http.client.HTTPException):
            return
        version = manifest.get("version") if isinstance(manifest, dict) else None
        if isinstance(version, str):
            versions.append(version)

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(RELEASE_CHECK_TIMEOUT)
    return versions[0] if versions else None


def cached_release(path: Path, url: str) -> tuple[float, Optional[str]]:
    """When `url` was last checked and the version it gave, or (0, None) when it never was."""

    try:
        cached = json.loads(path.read_text())
        if cached["url"] == url:
            version = cached["version"]
            return float(cached["checked"]), version if isinstance(version, str) else None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 0.0, None


def record_release(path: Path, url: str, version: Optional[str]) -> bool:
    """Cache `version` as checked now, and say whether the cache could be written."""

    # Parallel commands, such as a review loop's subagents, write this at
    # once, so each writes its own file and renames it into place.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_name(f"{path.name}.{os.getpid()}")
        scratch.write_text(json.dumps({"url": url, "checked": time.time(), "version": version}))
        scratch.replace(path)
    except OSError:
        return False
    return True


def latest_release(url: str) -> Optional[str]:
    """The version of the release at `url`, fetched at most once a day.

    One command a day waits up to RELEASE_CHECK_TIMEOUT for the fetch. The
    check is recorded before the fetch, so a failed fetch waits a day like a
    successful one, and a cache that cannot be written skips the fetch
    rather than repeating it on every command. `PROJECTOR_OFFLINE` skips the
    fetch and answers from the cache.
    """

    path = release_cache_file()
    checked, version = cached_release(path, url)
    if os.environ.get("PROJECTOR_OFFLINE") or 0 <= time.time() - checked < RELEASE_CHECK_SECONDS:
        return version
    if not record_release(path, url, version):
        return version
    fetched = fetch_release_version(url)
    if fetched is not None and fetched != version:
        record_release(path, url, fetched)
        return fetched
    return version


def newer_release(release: str, installed: str) -> bool:
    """Whether `release` is later than `installed`; never when either is not plain dotted numbers, like `unknown`."""

    orders = [tuple(map(int, version.split("."))) for version in (release, installed)
              if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)]
    return len(orders) == 2 and orders[0] > orders[1]


def warn_of_stale_release() -> None:
    """Say on stderr when this command is older than the release `upgrade` would install.

    Every Projector skill runs this command, so the line reaches the agent
    using an old install without any skill text of its own. A checkout
    install is left to `install.sh status`, which compares it with the
    checkout rather than a release.
    """

    try:
        record = install_record()
    except (EnvironmentError, ValueError):
        return
    if "dir_info" in record:
        return
    installed = distribution_version()
    release = latest_release(release_manifest_url(record))
    if release is not None and newer_release(release, installed):
        print(
            f"warning: project {installed} is behind the {release} release;"
            " run 'project upgrade' to update the CLI and the Projector plugins",
            file=sys.stderr,
        )


class PackageDir(argparse.Action):
    """Print where this command's package actually lives, and exit.

    `install.sh status` diffs that directory against the checkout. argparse's
    own `version` action reflows its text to the terminal width, which would
    fold a long path across lines and defeat the caller reading it.
    """

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: D102
        print(Path(__file__).resolve().parent)
        parser.exit()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="project")
    result.add_argument(
        "--version",
        action="version",
        version=f"project {distribution_version()}",
    )
    result.add_argument(
        "--package-dir",
        nargs=0,
        action=PackageDir,
        help=argparse.SUPPRESS,
    )
    result.add_argument("--root", type=Path, help="Git repository root")
    result.add_argument(
        "--projects-dir",
        type=Path,
        help="project directory, relative to the Git root unless absolute",
    )
    subcommands = result.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser(
        "init", help="adopt the project convention; outside a repository, set up only the permission rules"
    )
    site_choice = init.add_mutually_exclusive_group()
    site_choice.add_argument(
        "--site",
        action="store_true",
        default=None,
        help="require the Projector site to be set up, failing if it cannot be (default: set it up when possible)",
    )
    site_choice.add_argument(
        "--no-site", action="store_false", dest="site", help="leave the GitHub Pages site and its workflow alone"
    )
    init.add_argument("--action-ref", default="v0", help="the projector tag or commit the site workflow runs")
    init.add_argument(
        "--url",
        help="serve the GitHub Pages site at this custom domain, such as https://projects.example.com, and link the "
        "repository's website to it",
    )
    rule_choice = init.add_mutually_exclusive_group()
    rule_choice.add_argument(
        "--publish-rule",
        action="store_true",
        default=None,
        help="add the rules allowing `project review publish` and `project summary publish` to your Claude Code "
        "settings and Codex rules even without a terminal (default: add them when run at a terminal)",
    )
    rule_choice.add_argument(
        "--no-publish-rule", action="store_false", dest="publish_rule",
        help="leave your Claude Code settings and Codex rules alone",
    )
    add_output(init)

    listing = subcommands.add_parser("list", help="list projects")
    listing.add_argument("--status", choices=STATUSES)
    listing.add_argument("--priority", choices=PRIORITIES)
    listing.add_argument("--owner", help="only the projects this person owns, ignoring case")
    add_output(listing)

    show = subcommands.add_parser("show", help="show one project")
    show.add_argument("project")
    add_output(show)

    search = subcommands.add_parser("search", help="search project content")
    search.add_argument("query")
    search.add_argument("--status", choices=STATUSES)
    search.add_argument("--priority", choices=PRIORITIES)
    add_output(search)

    create = subcommands.add_parser("create", help="create a project")
    create.add_argument("project")
    create.add_argument("--status", choices=STATUSES, default="draft")
    create.add_argument("--priority", choices=PRIORITIES, default="later")
    create.add_argument("--parent")
    create.add_argument("--no-edit", action="store_true")
    add_output(create)

    edit = subcommands.add_parser("edit", help="open a project plan")
    edit.add_argument("project")

    status = subcommands.add_parser("status", help="change project status")
    status.add_argument("project")
    status.add_argument("status", choices=STATUSES)
    add_output(status)

    priority = subcommands.add_parser("priority", help="change project priority")
    priority.add_argument("project")
    priority.add_argument("priority", choices=PRIORITIES)
    add_output(priority)

    owner = subcommands.add_parser("owner", help="set or clear a project's owner")
    owner.add_argument("project")
    owner_value = owner.add_mutually_exclusive_group(required=True)
    owner_value.add_argument("owner", nargs="?", help="who owns the project, preferably a GitHub login")
    owner_value.add_argument("--clear", action="store_true", help="remove the owner field")
    add_output(owner)

    done = subcommands.add_parser("done", help="mark a project completed")
    done.add_argument("project")
    add_output(done)

    add_output(subcommands.add_parser("check", help="validate all projects"))

    config = subcommands.add_parser("config", help="read layered configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)

    config_get = config_commands.add_parser("get", help="print one value")
    config_get.add_argument("key", help="dotted key, for example review.username")
    config_get.add_argument("--default", help="printed when the key is unset")
    add_output(config_get)

    config_list = config_commands.add_parser("list", help="print the merged configuration")
    add_output(config_list)

    config_paths = config_commands.add_parser(
        "paths", help="print the files that contribute, lowest precedence first"
    )
    add_output(config_paths)

    upgrade = subcommands.add_parser("upgrade", help="run the checkout's install.sh from anywhere")
    upgrade.add_argument(
        "target",
        nargs="*",
        help="install.sh target: all (its default), cli, claude, codex, or status",
    )

    summary_parser = subcommands.add_parser("summary", help="write and publish pull request summaries")
    summary_commands = summary_parser.add_subparsers(dest="summary_command", required=True)
    summary_init = summary_commands.add_parser("init", help="write a skeleton summary for a pull request")
    summary_init.add_argument("--repo", required=True, help="OWNER/NAME")
    summary_init.add_argument("--pr", required=True, type=int)
    summary_init.add_argument("--summary", required=True)
    summary_init.add_argument("--diff", help="read the diff from this file instead of GitHub")
    summary_publish = summary_commands.add_parser(
        "publish", help="commit a summary to the hidden summaries ref and start a site build"
    )
    summary_publish.add_argument("--summary", required=True)
    summary_publish.add_argument("--remote", default="origin")
    summary_publish.add_argument("--diff", help="publish the diff from this file instead of fetching it from GitHub")
    summary_publish.add_argument(
        "--no-dispatch", action="store_true", help="push the summary without starting the workflow"
    )
    summary_publish.add_argument(
        "--local", action="store_true",
        help="commit the summary to this checkout's summaries ref for `site serve` to preview, and push nothing "
        "(the default when the repository does not host its site)",
    )

    review_parser = subcommands.add_parser("review", help="the mechanical steps of a pull request review")
    review_commands = review_parser.add_subparsers(dest="review_command", required=True)
    review_setup = review_commands.add_parser(
        "setup", help="fetch a pull request's head into a scratch worktree and post the start comment"
    )
    review_move = review_commands.add_parser(
        "move", help="follow a head that moved mid-review and update the start comment in place"
    )
    review_census = review_commands.add_parser("census", help="count a pull request's Projector finding threads")
    review_release = review_commands.add_parser(
        "release", help="clear a pull request's review lock when no review is running"
    )
    review_publish = review_commands.add_parser(
        "publish", help="check and submit the review, set draft state, and delete the start comment"
    )
    review_gate = review_commands.add_parser(
        "gate", help="run the repository's review.gate command in the review's worktree, on a trusted head only"
    )
    for command in (review_setup, review_move, review_census, review_release, review_publish, review_gate):
        command.add_argument("pr", type=int, help="the pull request number")
        command.add_argument("--repo", help="owner/name (setup: must match the checkout's origin; "
                             "later: default the review set up for this pull request)")
        command.add_argument("--state-dir", type=Path,
                             help="where review state and locks live (default: $XDG_STATE_HOME/projector/reviews)")
        command.add_argument("--loop", help="the review loop's id, which keys its record of published reviews")
        add_output(command)
    review_setup.add_argument("--checkout", type=Path,
                              help="the source checkout to fetch into (default: the repository containing "
                                   "this directory)")
    review_setup.add_argument("--reviewer", help="the GitHub login that posts (default: review.username, then you)")
    review_setup.add_argument("--model", required=True, help="the model id the start comment names")
    review_setup.add_argument("--rereview", action="store_true",
                              help="a re-review of a head this loop already published a verdict on")
    review_publish.add_argument("--verdict", required=True, choices=("clean", "changes-requested"),
                                help="the verdict the signature line and marker carry")
    review_publish.add_argument("--body", type=Path, required=True,
                                help="the review below its signature line, with {census} and optionally "
                                     "{took}, {seconds}, {sha}, {short_sha}")
    review_publish.add_argument("--threads", type=Path,
                                help="a JSON list of findings, each {path, line, priority, body}")
    review_publish.add_argument("--covered", required=True,
                                help="files read over files changed, such as 12/12")
    review_publish.add_argument("--second-verdict", type=int, metavar="REVIEW_ID",
                                help="outside a loop, the earlier verdict a user agreed to publish beside")

    site = subcommands.add_parser("site", help="build, serve, or host the Projector site for a repository")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_build = site_commands.add_parser(
        "build", help="build the site from the projects, the published reviews, and the docs"
    )
    site_build.add_argument("--out", required=True)
    site_build.add_argument("--summaries", help="directory holding <number>/<head>/summary.json files")
    site_build.add_argument(
        "--repo-root", help="checkout whose README.md and docs/ the site serves (default: this repository)"
    )
    site_build.add_argument(
        "--base", default="/", help="the path the site is served under, such as /projector/ (default: /)"
    )
    site_build.add_argument(
        "--check-visibility",
        action="store_true",
        help="refuse to build when a private repository's Pages site is public, as a deploy must",
    )
    site_build.add_argument(
        "--prepare", metavar="COMMAND",
        help="run this shell command in the checkout before building, instead of site.prepare",
    )
    site_build.add_argument("--no-prepare", action="store_true", help="skip site.prepare")
    site_build.add_argument(
        "--allow-prepare", action="store_true",
        help="allow this checkout's site.prepare command, and remember it until the command changes",
    )
    site_page = site_commands.add_parser("page", help="build one summary page from a summary")
    site_page.add_argument("--summary", required=True)
    site_page.add_argument("--out", required=True)
    site_page.add_argument("--diff", help="read the diff from this file instead of GitHub")
    site_page.add_argument(
        "--at-head", action="store_true", help="build the summary's recorded head even if the pull request moved"
    )
    site_status = site_commands.add_parser(
        "status", help="say whether a repository hosts the site, and print its URL if so"
    )
    site_status.add_argument("--repo", required=True, help="OWNER/NAME")
    site_status.add_argument("--pr", type=int, help="print the URL of this pull request's review")
    site_serve = site_commands.add_parser(
        "serve", help="build the site and serve it over HTTP, rebuilding as its sources change"
    )
    site_serve.add_argument("--port", type=int, default=8000, help="the port to listen on (default: 8000; 0 picks one)")
    site_serve.add_argument(
        "--host", default="127.0.0.1", help="the address to listen on (default: 127.0.0.1, this machine only)"
    )
    site_serve.add_argument("--base", default="/", help="the path to serve the site under (default: /)")
    site_serve.add_argument(
        "--repo-root", help="checkout whose README.md and docs/ the site serves (default: this repository)"
    )
    site_serve.add_argument(
        "--summaries", help="directory holding <number>/<head>/summary.json files, instead of the summaries ref"
    )
    site_serve.add_argument("--remote", default="origin", help="the remote to fetch summaries from (default: origin)")
    site_serve.add_argument("--no-fetch", action="store_true", help="serve the summaries already fetched")
    site_serve.add_argument("--no-watch", action="store_true", help="build once instead of rebuilding on changes")
    site_serve.add_argument(
        "--prepare", metavar="COMMAND",
        help="run this shell command in the checkout before building, instead of site.prepare",
    )
    site_serve.add_argument("--no-prepare", action="store_true", help="skip site.prepare")
    site_serve.add_argument(
        "--allow-prepare", action="store_true",
        help="allow this checkout's site.prepare command, and remember it until the command changes",
    )
    site_workflow = site_commands.add_parser(
        "workflow", help="print or write the workflow file for the default branch"
    )
    site_workflow.add_argument("--action-ref", default="v0", help="the projector tag or commit the workflow runs")
    site_workflow.add_argument("--write", action="store_true", help=f"write {WORKFLOW_PATH} in this checkout")
    site_workflow.add_argument("--force", action="store_true",
                               help=f"with --write, replace {WORKFLOW_PATH} even if it was edited by hand")
    site_workflow.add_argument(
        "--branch", help="the default branch whose README and docs changes rebuild the site (default: origin's)"
    )
    return result


def add_output(command: argparse.ArgumentParser) -> None:
    command.add_argument("--json", action="store_true", dest="json_output")


def open_editor(path: Path) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise EnvironmentError("an interactive terminal is required to open an editor")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        raise EnvironmentError("set VISUAL or EDITOR before opening a project")
    try:
        subprocess.run([*shlex.split(editor), str(path)], check=True)
    except FileNotFoundError as error:
        raise EnvironmentError(f"editor not found: {editor}") from error
    except subprocess.CalledProcessError as error:
        raise EnvironmentError(f"editor exited with status {error.returncode}") from error


def emit_path(store: ProjectStore, path: Path, json_output: bool, action: str) -> None:
    relative = store._relative(path)
    if json_output:
        print(json_text({"action": action, "path": relative}))
    else:
        print(relative)


def config_start(arguments: argparse.Namespace) -> Path:
    """Where the walk begins: the repository root, or the working directory.

    Configuration is useful outside a repository -- the user layer alone still
    answers -- so a missing repository is not an error here, unlike everywhere
    else in this CLI.
    """

    if arguments.root:
        return arguments.root.resolve()
    try:
        return discover_git_root(Path.cwd())
    except EnvironmentError:
        return Path.cwd().resolve()


def format_value(value: object) -> str:
    """Render a scalar the way the TOML file spells it.

    Booleans matter: `str(True)` is `True`, which no shell comparison against a
    config file's `true` will match.
    """

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (str, int, float)):
        return str(value)
    # An array or table, which may itself contain a date.
    return json.dumps(value, sort_keys=True, default=json_scalar)


def run_config(arguments: argparse.Namespace) -> int:
    config = load_config(config_start(arguments))
    command = arguments.config_command

    if command == "get":
        value = config.get(arguments.key)
        if value is None:
            value = arguments.default
            source = None
        else:
            source = config.source(arguments.key)
        if arguments.json_output:
            print(
                json_text(
                    {
                        "key": arguments.key,
                        "value": value,
                        "source": str(source) if source else None,
                    }
                )
            )
        elif value is not None:
            print(format_value(value))
        # Unset and undefaulted is not an error, it is an answer -- report it
        # in the exit status so a caller can branch without parsing output.
        return 0 if value is not None else 1

    if command == "list":
        flat = config.flat()
        if arguments.json_output:
            print(
                json_text(
                    {
                        "config": config.values,
                        "sources": {key: str(path) for key, path in config.sources.items()},
                    }
                )
            )
        else:
            for key in sorted(flat):
                print(f"{key} = {format_value(flat[key])}")
        return 0

    if arguments.json_output:
        print(json_text({"paths": [str(path) for path in config.paths]}))
    else:
        for path in config.paths:
            print(path)
    return 0


def emit_files(files: list[FileAction], json_output: bool, pages: Optional[dict] = None,
               readme: bool = True) -> None:
    """Report what `init` did, one line per file, or one JSON document.

    In a repository, the top-level `action` and `path` still describe the
    projects README, as they did before `init` managed more than that file, so
    a consumer written against the earlier shape keeps working; `files` lists
    everything. Outside one there is no README, and `files` is the whole report.
    """

    for entry in files:
        if entry.note:
            print(f"project: {entry.note}", file=sys.stderr)
    if json_output:
        print(
            json_text(
                {
                    **({"action": files[0].action, "path": files[0].path} if readme else {}),
                    "files": [{"path": entry.path, "action": entry.action} for entry in files],
                    **({"site": pages} if pages is not None else {}),
                }
            )
        )
    else:
        for entry in files:
            print(f"{entry.action} {entry.path}")


def instructions_enabled(root: Path) -> bool:
    """`instructions.enabled` from configuration; unset means enabled."""

    value = load_config(root).get("instructions.enabled")
    if value is None:
        return True
    if not isinstance(value, bool):
        raise ConfigError(
            f"instructions.enabled must be true or false, not {type(value).__name__}"
        )
    return value


def configured_projects_dir(root: Path) -> Optional[Path]:
    """`projects.dir` from configuration, when the flag did not supply one."""

    configured = load_config(root).get("projects.dir")
    if configured is None:
        return None
    if not isinstance(configured, str):
        raise ConfigError(f"projects.dir must be a string, not {type(configured).__name__}")
    return Path(configured)


def configured_prepare(root: Path) -> Optional[str]:
    """`site.prepare` from configuration: the command that generates what the site copies."""

    configured = load_config(root).get("site.prepare")
    if configured is None or configured == "":
        return None
    if not isinstance(configured, str):
        raise ConfigError(f"site.prepare must be a string, not {type(configured).__name__}")
    return configured


def allowed_prepare_file() -> Path:
    """Where the commands a user allowed are remembered: user state, not a checkout or its config."""
    state = os.environ.get("XDG_STATE_HOME")
    return (Path(state) if state else Path.home() / ".local" / "state") / "projector" / "allowed-prepare"


def prepare_key(root: Path, command: str) -> str:
    return hashlib.sha256(f"{root.resolve()}\n{command}".encode()).hexdigest()


def prepare_command(root: Path, arguments: argparse.Namespace) -> Optional[str]:
    """The command to run before building, or None.

    site.prepare comes from the checkout, so a branch someone else wrote, or a
    clone of a repository you have never looked at, would otherwise run its
    own shell command as you the moment you preview its docs. Locally it runs
    only once you allow that exact command for that checkout; a changed
    command needs allowing again. A deploy runs it unasked, since the workflow
    builds only what was merged to the default branch, and a --prepare given
    on the command line is already your choice.
    """
    if arguments.no_prepare:
        return None
    if arguments.prepare:
        return arguments.prepare
    command = configured_prepare(root)
    if not command or os.environ.get("GITHUB_ACTIONS") == "true":
        return command
    key = prepare_key(root, command)
    allowed = allowed_prepare_file()
    if allowed.is_file() and key in allowed.read_text(encoding="utf-8").split():
        return command
    if arguments.allow_prepare:
        allowed.parent.mkdir(parents=True, exist_ok=True)
        with allowed.open("a", encoding="utf-8") as remembered:
            remembered.write(key + "\n")
        return command
    print(f"site.prepare is not allowed for this checkout, so the site is built without it:\n  {command}\n"
          f"Rerun with --allow-prepare to allow this exact command here.", file=sys.stderr)
    return None


def run_prepare(root: Path, command: str) -> None:
    """Run the prepare command in the checkout, through the system shell, as a Makefile target would run."""
    # The command comes from the repository, so say what runs before it runs.
    print(f"preparing the site: {command}", file=sys.stderr, flush=True)
    result = subprocess.run(command, shell=True, cwd=root)
    if result.returncode:
        raise summary.SummaryError(f"the prepare command exited with status {result.returncode}: {command}")


NOT_HOSTED = 3


def run_review(arguments: argparse.Namespace) -> int:
    root = review.state_dir(arguments.state_dir)
    loop = review.valid_loop(arguments.loop)
    command = arguments.review_command
    if command == "setup":
        checkout = arguments.checkout or Path.cwd()
        try:
            configured = load_config(review.checkout_root(checkout)).get("review.username")
        except review.ReviewError:
            configured = None
        state = review.setup(root, arguments.pr, checkout, arguments.repo, arguments.reviewer,
                             configured if isinstance(configured, str) else None,
                             arguments.model, loop, arguments.rereview)
    else:
        repo = review.find_repo(root, arguments.pr, arguments.repo, Path.cwd())
        if command == "census":
            result = review.census(repo, arguments.pr)
            if arguments.json_output:
                print(json.dumps(result, indent=2))
            else:
                print("\n".join(review.census_lines(result)))
            return 0
        if command == "publish":
            state = review.read_state(review.Paths(root, repo, arguments.pr))
            try:
                allow = load_config(Path(state["checkout"])).get("review.allow_approve") is True
            except ProjectorError:
                allow = False
            result = review.publish(root, arguments.pr, repo, loop, arguments.verdict, arguments.body,
                                    arguments.threads, arguments.covered, arguments.second_verdict, allow)
            if arguments.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(f"published review {result['review_id']} ({result['verdict']}, {result['event']}) on "
                      f"{result['repo']}#{result['number']} at {result['sha']}")
                if result["draft"] is not None:
                    print("marked draft" if result["draft"] else "marked ready")
            return 0
        if command == "gate":
            state = review.read_state(review.Paths(root, repo, arguments.pr))
            try:
                configured = load_config(Path(state["checkout"])).get("review.gate")
            except ProjectorError:
                configured = None
            result = review.gate(root, arguments.pr, repo, configured if isinstance(configured, str) else None,
                                 output_to_stderr=arguments.json_output)
            if arguments.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(f"review.gate exited {result['exit_code']} at {result['sha'][:7]}", file=sys.stderr)
            return result["exit_code"]
        if command == "release":
            released = review.release(review.Paths(root, repo, arguments.pr))
            if arguments.json_output:
                print(json.dumps({"released": [str(p) for p in released]}, indent=2))
            else:
                print(f"released {len(released)} lock{'' if len(released) == 1 else 's'} for {repo}#{arguments.pr}")
            return 0
        state = review.move(root, arguments.pr, repo, loop)
    if arguments.json_output:
        print(json.dumps(state, indent=2))
    else:
        print(f"reviewing {state['repo']}#{state['number']} at {state['sha']} (head {state['heads']})")
        print(f"merge base {state['base']}")
        print(f"worktree {state['worktree']}")
        print("trusted" if state["trusted"] else f"untrusted: {state['untrusted_because']}; review it by reading")
        print(f"start comment {state['start_comment']['id']} posted {state['start_comment']['created_at']}")
    return 0


def run_summary(arguments: argparse.Namespace) -> int:
    if arguments.summary_command == "init":
        summary.init(arguments.repo, arguments.pr, arguments.summary, arguments.diff)
    else:
        pr = summary.summary_pr(json.loads(Path(arguments.summary).read_text(encoding="utf-8")))
        site, reason = (None, "--local asked for a local preview") if arguments.local \
            else summary.push_decision(pr["repo"])
        summary.publish(
            Path(arguments.summary),
            arguments.remote,
            send_dispatch=not arguments.no_dispatch,
            diff_path=Path(arguments.diff) if arguments.diff else None,
            push=site is not None,
            reason=reason,
        )
        # A summary kept in this checkout has no page on GitHub to link.
        if site is not None:
            print(summary.comment_summary(pr["repo"], pr["number"], pr["head"], site=site))
            print(summary.describe_summary(pr["repo"], pr["number"], pr["head"], site))
    return 0


# The `project check` problems that keep a plan off the site: a plan that does
# not load, a top-level directory with no lowercase readme.md, and an entry
# point spelled README.md, which is read as a page rather than a project.
SKIPPED_PLAN_ISSUES = {"invalid-project", "missing-plan", "wrong-entry-case"}


def site_projects(root: Path) -> tuple[list, Optional[Path]]:
    """The repository's projects for the site, without the plans that are broken.

    A broken plan should cost only its own place on the site, not the other
    projects or the deploy that also carries the README, the docs, and the
    summaries, so each one is skipped with a warning that names it.
    """
    store = ProjectStore(root, root, configured_projects_dir(root))
    if not store.projects_dir.is_dir():
        return [], None
    projects, _ = store.readable_projects()
    for issue in store.check(instructions_enabled=False):
        if issue.code in SKIPPED_PLAN_ISSUES:
            print(f"::warning title=Project skipped::{issue.path}: {issue.message}", file=sys.stderr)
    return projects, store.projects_dir


def site_repo(root: Path) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return repo
    try:
        return summary.repo_slug(summary.git("-C", str(root), "remote", "get-url", "origin"))
    except summary.SummaryError:
        return ""


def site_branch(root: Path) -> str:
    try:
        branch = summary.git("-C", str(root), "rev-parse", "--abbrev-ref", "HEAD")
    except summary.SummaryError:
        return "main"
    return branch if branch and branch != "HEAD" else "main"


def trunk_branch(root: Path) -> str:
    """The repository's default branch: origin's recorded HEAD, else the checked-out branch.

    A deploy checks out the default branch, which may not record origin's HEAD,
    while a local checkout records it but may sit on any branch.
    """
    try:
        head = summary.git("-C", str(root), "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    except summary.SummaryError:
        return site_branch(root)
    return head.split("/", 1)[1] if "/" in head else head


def build_checkout(root: Path, out: Path, summaries: Optional[Path], base: str, repo: str) -> str:
    """Build the site from a checkout, and summarize what it holds."""
    projects, projects_dir = site_projects(root)

    # A stack is a fact on GitHub; when it cannot be asked, the review shows none.
    def base_pr(base_ref: str) -> Optional[int]:
        try:
            return summary.base_pr(repo, base_ref)
        except summary.SummaryError:
            return None

    # A review that cannot be asked about shows no status, rather than unreviewed.
    def pr_reviews(number: int) -> Optional[list[dict]]:
        try:
            return summary.pr_reviews(repo, number)
        except summary.SummaryError:
            return None

    entries, failures = site.build_site(
        out,
        summaries=summaries,
        repo_root=root,
        projects=projects,
        projects_dir=projects_dir,
        repo=repo,
        branch=site_branch(root),
        trunk=trunk_branch(root),
        base=base,
        lookup=base_pr if repo else None,
        review_lookup=pr_reviews if repo else None,
    )
    return f"{len(projects)} projects, {len(entries)} reviews, {len(failures)} reviews skipped"


def run_site_build(arguments: argparse.Namespace) -> int:
    root = Path(arguments.repo_root).resolve() if arguments.repo_root else discover_git_root(Path.cwd())
    repo = site_repo(root)
    if arguments.check_visibility:
        if not repo:
            raise summary.SummaryError("cannot check the site's visibility without knowing the repository")
        summary.require_private_site(repo)
    command = prepare_command(root, arguments)
    if command:
        run_prepare(root, command)
    summaries = Path(arguments.summaries) if arguments.summaries else None
    built = build_checkout(root, Path(arguments.out), summaries, arguments.base, repo)
    print(f"wrote {arguments.out}/index.html: {built}")
    return 0


def run_site_serve(arguments: argparse.Namespace) -> int:
    from .site import serve

    root = Path(arguments.repo_root).resolve() if arguments.repo_root else discover_git_root(Path.cwd())
    repo = site_repo(root)
    base = "/" + arguments.base.strip("/") + "/" if arguments.base.strip("/") else "/"
    given = Path(arguments.summaries).resolve() if arguments.summaries else None
    published = serve.Summaries(root, arguments.remote) if given is None else None
    fetched = ""
    if published is not None and not arguments.no_fetch:
        fetched = published.fetch()
        if fetched:
            print(f"serving the summaries already fetched; {arguments.remote} has none to fetch: {fetched}",
                  file=sys.stderr)
    projects_dir = configured_projects_dir(root)
    watched = [root / "README.md", root / "docs"]
    if projects_dir is not None:
        watched.append(projects_dir if projects_dir.is_absolute() else root / projects_dir)
    if given is not None:
        watched.append(given)

    def sources() -> tuple:
        files = serve.fingerprint([path for path in watched if path.exists()])
        return files, published.version() if published is not None else ()

    def build(out: Path) -> str:
        # The build copies what it needs from the summaries, so they outlive it only as long as it runs.
        with tempfile.TemporaryDirectory(prefix="projector-summaries-") as extracted:
            summaries = published.extract(Path(extracted)) if published is not None else given
            # The build reports each page as a deploy log would; a server keeps only its one-line result.
            with contextlib.redirect_stdout(io.StringIO()):
                return build_checkout(root, out, summaries, base, repo)

    command = prepare_command(root, arguments)
    site_state = serve.Site(build, sources, (lambda: run_prepare(root, command)) if command else None)
    try:
        print(f"built the site: {site_state.refresh(force=True)}")
        server = serve.ThreadingHTTPServer(
            (arguments.host, arguments.port), serve.handler(site_state, base, serve.allowed_hosts(arguments.host))
        )
    except BaseException:
        site_state.close()
        raise
    host, port = server.server_address[:2]
    shown = f"[{host}]" if ":" in host else host
    if arguments.host not in serve.LOOPBACK:
        print(f"listening on {arguments.host}: anyone who can reach it can read this repository's site",
              file=sys.stderr)
    # A terminal closing or a process manager stopping the server must clean up
    # as Ctrl-C does, since the build holds the repository's docs and diffs.
    def interrupt(signum, frame) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupt)
    stop = threading.Event()
    # Everything after the handlers runs inside the cleanup, and the address is
    # announced last, so a signal sent as soon as it appears is always caught.
    try:
        if not arguments.no_watch:
            report = lambda message: print(message, flush=True)
            threading.Thread(target=serve.watch, args=(site_state, stop, 1.0, report), daemon=True).start()
            # A publish from another checkout reaches a Pages site through its
            # deploy; here it arrives with the next fetch, which the watch sees.
            if published is not None and not arguments.no_fetch:
                threading.Thread(target=serve.keep_fetching, daemon=True,
                                 args=(published.fetch_quietly, stop, serve.FETCH_INTERVAL, report, fetched)).start()
        print(f"serving http://{shown}:{port}{base}; press Ctrl-C to stop", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        site_state.close()
    return 0


def run_site(arguments: argparse.Namespace) -> int:
    command = arguments.site_command
    if command == "build":
        return run_site_build(arguments)
    elif command == "serve":
        return run_site_serve(arguments)
    elif command == "page":
        data = json.loads(Path(arguments.summary).read_text(encoding="utf-8"))
        diff = Path(arguments.diff).read_text(encoding="utf-8") if arguments.diff else None
        payload = site.build_page(data, Path(arguments.out), diff=diff, at_head=arguments.at_head)
        counts = payload["stats"]
        print(
            f"wrote {arguments.out}/index.html: {len(data['groups'])} groups, "
            f"{counts['files']} files, +{counts['adds']} -{counts['dels']}"
        )
    elif command == "status":
        url, reason = summary.hosting(arguments.repo)
        if url is None:
            print(f"not hosted: {reason}")
            return NOT_HOSTED
        print(summary.review_page(url, arguments.pr) if arguments.pr else url)
    else:
        root = discover_git_root(Path.cwd())
        text = site_workflow_text(root, arguments.action_ref, arguments.branch)
        if not arguments.write:
            print(text, end="")
            return 0
        result = write_site_workflow(root, text, force=arguments.force)
        if result.action == "kept":
            print(f"project: {result.note}", file=sys.stderr)
            return 1
        print(f"wrote {root / WORKFLOW_PATH}; commit it to the default branch, where GitHub runs dispatched workflows")
    return 0


def site_projects_dir(root: Path) -> Optional[str]:
    """The configured projects directory as the site workflow names it, if it is inside the checkout."""
    projects_dir = configured_projects_dir(root)
    return projects_dir.as_posix() if projects_dir is not None and not projects_dir.is_absolute() else None


def site_workflow_text(root: Path, action_ref: str, branch: Optional[str] = None) -> str:
    return summary.workflow_text(action_ref, branch or summary.default_branch(root=root), site_projects_dir(root))


def write_site_workflow(root: Path, text: str, force: bool = False) -> FileAction:
    """Write the site workflow, updating one Projector wrote but keeping one edited by hand,
    such as a workflow that sets up a toolchain for site.prepare, unless `force` replaces it."""
    path = root / WORKFLOW_PATH
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        if current == text:
            return FileAction(WORKFLOW_PATH, "unchanged")
        if not force and not summary.generated_workflow(current, site_projects_dir(root)):
            return FileAction(
                WORKFLOW_PATH,
                "kept",
                f"{WORKFLOW_PATH} has changes Projector did not write, so it was left alone; compare it with "
                "`project site workflow`, or replace it with `project site workflow --write --force`",
            )
    action = "updated" if path.exists() else "created"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return FileAction(
        WORKFLOW_PATH,
        action,
        f"commit {WORKFLOW_PATH} to the default branch through a pull request; GitHub runs the site "
        "workflow only from there",
    )


class NotOnGitHub(ProjectorError):
    """The checkout's origin is not a GitHub repository, so it has no Pages site to set up."""


def other_pages_deployers(root: Path) -> list[str]:
    """Workflows in the checkout, other than Projector's, that deploy a GitHub Pages site."""
    folder = root / ".github" / "workflows"
    ours = {Path(WORKFLOW_PATH).name, *(Path(path).name for path in summary.LEGACY_WORKFLOW_PATHS)}
    found = []
    for path in sorted(folder.glob("*.y*ml")) if folder.is_dir() else []:
        if path.name not in ours and "actions/deploy-pages" in path.read_text(encoding="utf-8", errors="replace"):
            found.append(path.relative_to(root).as_posix())
    return found


def site_address(value: str) -> str:
    """The address `init --url` names, as scheme://host/, assuming https:// when it names no scheme.

    It is a custom domain for the Pages site, so it has no path, port, or credentials,
    and its host is dot-separated labels of letters, digits, and hyphens, which
    also keeps it safe to print unquoted in a command for an admin.
    """
    parsed = urlparse(value.strip() if "://" in value else f"https://{value.strip()}")
    try:
        port = parsed.port
    except ValueError:
        port = -1
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if (parsed.scheme not in ("http", "https") or not re.fullmatch(rf"(?:{label}\.)+{label}", parsed.hostname or "")
            or port is not None or parsed.username or parsed.password or parsed.path.strip("/") or parsed.params
            or parsed.query or parsed.fragment):
        raise UsageError(f"--url must be a custom domain's address, such as https://projects.example.com, not {value!r}")
    return f"{parsed.scheme}://{parsed.hostname}/"


def init_site(root: Path, action_ref: str, takeover: bool = False, url: str = "") -> tuple[dict, FileAction]:
    """Set up the Projector site: its Pages site first, then the workflow that deploys to it.

    A private repository whose site `init` cannot make private gets no
    workflow. When the Pages changes need an admin, the workflow is written
    beside the commands the admin runs and rides on the `NeedsAdmin` raised,
    because its deploy step refuses a private repository's public site, so
    it cannot publish before the admin is done.
    Without `takeover`, a repository that already deploys its own Pages site,
    from a branch or from another workflow, keeps it. With `url`, the site is
    served at that custom domain, and the repository's website links to it
    even when it linked somewhere else.
    """
    try:
        repo = summary.repo_slug(summary.git("-C", str(root), "remote", "get-url", "origin"))
    except summary.SummaryError:
        repo = ""
    if not repo:
        raise NotOnGitHub("origin is not a GitHub repository to set up Pages on")
    deployers = other_pages_deployers(root)
    if deployers and not takeover:
        raise summary.SummaryError(f"{', '.join(deployers)} already deploys a GitHub Pages site; pass --site to "
                                    "add the Projector site's workflow beside it")
    if shutil.which("gh") is None:
        raise summary.SummaryError("the gh CLI is not installed, and setting up Pages needs it")
    details = json.loads(summary.gh("api", f"repos/{repo}"))
    # Only an admin can change Pages or the website link. The workflow needs no
    # admin to propose, so a non-admin gets it either way.
    admin = bool((details.get("permissions") or {}).get("admin"))
    current = (details.get("homepage") or "").strip()
    try:
        pages = summary.enable_pages(repo, admin, takeover, url)
    except summary.NeedsAdmin as error:
        # The admin links the website in the same visit, as init would. Only
        # --url names the address up front: making a site private moves it to
        # a subdomain of its own, so otherwise the command reads it back.
        if not current or (url and not summary.same_link(current, url)):
            error.commands.append(summary.homepage_command(repo, url))
        error.workflow = write_site_workflow(root, site_workflow_text(root, action_ref))
        raise
    # The website link is a convenience; failing to set it must not cost the workflow.
    try:
        pages["website"], note = summary.set_homepage(
            repo, pages["url"], current, admin, replace=bool(url)
        ) if pages["url"] else ("unchanged", "")
    except summary.SummaryError as error:
        pages["website"], note = "kept", f"could not link the repository's website to the site: {error}"
    if note:
        print(f"project: {note}", file=sys.stderr)
    return pages, write_site_workflow(root, site_workflow_text(root, action_ref))


def site_enabled(root: Path) -> bool:
    """`site.enabled` from configuration; unset means `init` sets the site up."""

    value = load_config(root).get("site.enabled")
    if value is None:
        return True
    if not isinstance(value, bool):
        raise ConfigError(f"site.enabled must be true or false, not {type(value).__name__}")
    return value


def publish_rule_enabled(root: Path) -> bool:
    """`review.publish_rule` from configuration; unset means `init` adds the rules."""

    value = load_config(root).get("review.publish_rule")
    if value is None:
        return True
    if not isinstance(value, bool):
        raise ConfigError(f"review.publish_rule must be true or false, not {type(value).__name__}")
    return value


def publish_rule_mode(arguments: argparse.Namespace, start: Path) -> Optional[str]:
    """Whether `init` adds the publish rules ("add"), only reports them ("report"), or leaves them (None).

    The rules exempt two commands from each host's automatic approval in the
    person's own settings, so `init` adds them only when a person runs it at a
    terminal or names --publish-rule; an agent refreshing the instructions gets
    a note instead. Configuration can only turn them off.
    """

    if arguments.publish_rule is not None:
        return "add" if arguments.publish_rule else None
    if publish_rule_enabled(start):
        return "add" if sys.stdin.isatty() else "report"
    return None


def init_permissions_only(arguments: argparse.Namespace) -> int:
    """`init` outside a Git repository: set up the user's publish rules, which belong to no repository."""

    if arguments.site or arguments.url:
        raise UsageError("--site and --url set up a repository's site; run `project init` inside that repository")
    publish_rule = publish_rule_mode(arguments, Path.cwd())
    files = allow_publish(apply=publish_rule == "add") if publish_rule else []
    emit_files(files, arguments.json_output, readme=False)
    # Said from what was written, because an agent relays this line: a rule
    # only reported is a rule the next publish still lacks.
    kept = sum(entry.action == "kept" for entry in files)
    if not files:
        done = "did nothing, because the permission rules are off"
    elif kept == len(files):
        done = "added no permission rules and only reported what is missing, above"
    elif kept:
        done = "set up some of the permission rules and reported the rest, above"
    else:
        done = "set up only the permission rules"
    print(f"project: not inside a Git repository, so init {done}; run `project init` inside a repository to "
          "adopt the project convention there", file=sys.stderr)
    return 0


def emit_site(pages: dict) -> None:
    print(f"{pages['pages']} GitHub Pages site {pages['url']}")
    if pages["visibility"] == "updated":
        print("updated GitHub Pages visibility: private")
    if pages["website"] == "updated":
        print(f"updated repository website {summary.homepage_url(pages['url'])}")


def run(arguments: argparse.Namespace) -> int:
    if arguments.command == "config":
        return run_config(arguments)
    if arguments.command == "upgrade":
        return run_upgrade(arguments.target)
    if arguments.command == "summary":
        return run_summary(arguments)
    if arguments.command == "site":
        return run_site(arguments)
    if arguments.command == "review":
        return run_review(arguments)

    # Resolved once and passed down, so configuration and the store agree on
    # the repository without asking git for it twice.
    try:
        root = arguments.root.resolve() if arguments.root else discover_git_root(Path.cwd())
    except EnvironmentError:
        if arguments.command != "init":
            raise
        return init_permissions_only(arguments)
    projects_dir = arguments.projects_dir
    if projects_dir is None:
        projects_dir = configured_projects_dir(root)

    store = ProjectStore(Path.cwd(), root, projects_dir)
    command = arguments.command

    if command == "init":
        # An explicit --site or --no-site wins over configuration; unset, the
        # site is set up when it can be and skipped with a note when it cannot.
        wanted = site_enabled(root) if arguments.site is None else arguments.site
        url = site_address(arguments.url) if arguments.url else ""
        if url and not wanted:
            raise UsageError("--url sets the site's address, but --no-site or site.enabled = false leaves the site "
                             "alone")
        publish_rule = publish_rule_mode(arguments, root)
        try:
            files = store.init(instructions_enabled(root))
            if publish_rule:
                files.extend(allow_publish(apply=publish_rule == "add"))
            pages = None
            if wanted:
                try:
                    pages, workflow = init_site(root, arguments.action_ref, takeover=bool(arguments.site), url=url)
                    files.append(workflow)
                except ProjectorError as error:
                    needs_admin = isinstance(error, summary.NeedsAdmin)
                    if needs_admin and error.workflow is not None:
                        files.append(error.workflow)
                    if arguments.site:
                        raise InitError(str(error), files) from error
                    reason = error.reason if needs_admin else str(error)
                    pages = {"skipped": reason, **({"admin_commands": error.commands} if needs_admin else {})}
                    # A repository with no GitHub origin has no site to miss.
                    if not isinstance(error, NotOnGitHub):
                        print(f"project: site not set up: {reason}; pass --no-site, or set site.enabled = false, "
                              "to stop setting it up", file=sys.stderr)
                    if needs_admin:
                        print(f"project: to finish setting it up, {error.steps()}", file=sys.stderr)
        except InitError as error:
            # Say what was written before saying what could not be.
            if not arguments.json_output:
                emit_files(error.files, False)
            raise
        emit_files(files, arguments.json_output, pages)
        if pages is not None and "skipped" not in pages and not arguments.json_output:
            emit_site(pages)
    elif command == "list":
        projects = store.projects()
        if arguments.status:
            projects = [project for project in projects if project.status == arguments.status]
        if arguments.priority:
            projects = [project for project in projects if project.priority == arguments.priority]
        if arguments.owner:
            wanted = arguments.owner.casefold()
            projects = [project for project in projects if (project.owner or "").casefold() == wanted]
        if arguments.json_output:
            print(json_text({"projects": [project.public(store.root) for project in projects]}))
        else:
            print(grouped_projects(projects))
    elif command == "show":
        project = store.resolve(arguments.project)
        content = store._read_text(project.path)
        if arguments.json_output:
            print(json_text({"project": {**project.public(store.root), "content": content}}))
        else:
            print(content, end="" if content.endswith("\n") else "\n")
    elif command == "search":
        matches = store.search(arguments.query, arguments.status, arguments.priority)
        if arguments.json_output:
            print(json_text({"matches": matches}))
        else:
            for match in matches:
                print(f"{match['project']}\t{match['path']}:{match['line']}\t{match['text']}")
    elif command == "create":
        project = store.create(
            arguments.project, arguments.status, arguments.priority, arguments.parent
        )
        emit_path(store, project.path, arguments.json_output, "created")
        if not arguments.no_edit and sys.stdin.isatty() and sys.stdout.isatty():
            open_editor(project.path)
    elif command == "edit":
        open_editor(store.resolve(arguments.project).path)
    elif command == "owner":
        project, changed = store.set_owner(arguments.project, None if arguments.clear else arguments.owner)
        emit_path(store, project.path, arguments.json_output, "updated" if changed else "unchanged")
    elif command == "priority":
        project, changed = store.set_priority(arguments.project, arguments.priority)
        emit_path(store, project.path, arguments.json_output, "updated" if changed else "unchanged")
    elif command in ("status", "done"):
        new_status = arguments.status if command == "status" else "completed"
        project, changed = store.set_status(arguments.project, new_status)
        emit_path(store, project.path, arguments.json_output, "updated" if changed else "unchanged")
        if command == "done":
            print("Record whether the project shipped, was abandoned, or was superseded.", file=sys.stderr)
    elif command == "check":
        issues = store.check(instructions_enabled(root))
        # Warnings print but never fail the gate; `valid` and the exit code
        # follow errors alone.
        errors = [issue for issue in issues if issue.severity == "error"]
        if arguments.json_output:
            print(json_text({"valid": not errors, "issues": [issue.__dict__ for issue in issues]}))
        else:
            for issue in issues:
                prefix = "warning: " if issue.severity == "warning" else ""
                print(f"{prefix}{issue.path}: {issue.message} [{issue.code}]", file=sys.stderr)
            if not errors:
                print("Project plans are valid.")
        return 0 if not errors else 65
    return 0


def parse(argv: list[str] | None) -> argparse.Namespace:
    try:
        return parser().parse_args(argv)
    except SystemExit as stop:
        # A usage error from an old command is often a newer skill calling a
        # subcommand or flag it lacks, so the warning follows argparse's.
        if stop.code:
            warn_of_stale_release()
        raise


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parse(argv)
        if arguments.command != "upgrade":
            warn_of_stale_release()
        return run(arguments)
    except ProjectorError as error:
        print(f"project: {error}", file=sys.stderr)
        return error.exit_code
    except (OSError, UnicodeDecodeError) as error:
        print(f"project: {error}", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
