from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
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
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import url2pathname, urlopen

from . import site, summary
from .summary import WORKFLOW_PATH
from .config import ConfigError
from .config import load as load_config
from .core import (
    PRIORITIES,
    STATUSES,
    EnvironmentError,
    FileAction,
    InitError,
    ProjectorError,
    ProjectStore,
    discover_git_root,
    grouped_projects,
    json_scalar,
    json_text,
)


DISTRIBUTION = "projector-cli"


def distribution_version() -> str:
    """The version of the installed distribution, not of this source tree.

    `install.sh status` compares this against `setup.cfg` to tell a stale
    install from a current one. A checkout that was never installed has no
    distribution to report, which is itself the answer.
    """

    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return "unknown"


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

    init = subcommands.add_parser("init", help="adopt the project convention")
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
    add_output(init)

    listing = subcommands.add_parser("list", help="list projects")
    listing.add_argument("--status", choices=STATUSES)
    listing.add_argument("--priority", choices=PRIORITIES)
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

    done = subcommands.add_parser("done", help="mark a project completed")
    done.add_argument("project")
    add_output(done)

    add_output(subcommands.add_parser("check", help="validate all projects"))

    config = subcommands.add_parser("config", help="read layered configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)

    config_get = config_commands.add_parser("get", help="print one value")
    config_get.add_argument("key", help="dotted key, for example review.effort")
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

    summary_parser = subcommands.add_parser("summary", help="write and publish pull request summary specs")
    summary_commands = summary_parser.add_subparsers(dest="summary_command", required=True)
    summary_init = summary_commands.add_parser("init", help="write a skeleton spec for a pull request")
    summary_init.add_argument("--repo", required=True, help="OWNER/NAME")
    summary_init.add_argument("--pr", required=True, type=int)
    summary_init.add_argument("--spec", required=True)
    summary_init.add_argument("--diff", help="read the diff from this file instead of GitHub")
    summary_publish = summary_commands.add_parser(
        "publish", help="commit a spec to the hidden summaries ref and start a site build"
    )
    summary_publish.add_argument("--spec", required=True)
    summary_publish.add_argument("--remote", default="origin")
    summary_publish.add_argument("--diff", help="publish the diff from this file instead of fetching it from GitHub")
    summary_publish.add_argument(
        "--no-dispatch", action="store_true", help="push the spec without starting the workflow"
    )

    site = subcommands.add_parser("site", help="build, serve, or host the Projector site for a repository")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_build = site_commands.add_parser(
        "build", help="build the site from the projects, the published reviews, and the docs"
    )
    site_build.add_argument("--out", required=True)
    site_build.add_argument("--summaries", help="directory holding <number>/<head>/spec.json files")
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
    site_page = site_commands.add_parser("page", help="build one summary page from a spec")
    site_page.add_argument("--spec", required=True)
    site_page.add_argument("--out", required=True)
    site_page.add_argument("--diff", help="read the diff from this file instead of GitHub")
    site_page.add_argument(
        "--at-head", action="store_true", help="build the spec's recorded head even if the pull request moved"
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
        "--summaries", help="directory holding <number>/<head>/spec.json files, instead of the summaries ref"
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


def emit_files(files: list[FileAction], json_output: bool, pages: Optional[dict] = None) -> None:
    """Report what `init` did, one line per file, or one JSON document.

    The top-level `action` and `path` still describe the projects README, as
    they did before `init` managed more than that file, so a consumer written
    against the earlier shape keeps working; `files` lists everything.
    """

    for entry in files:
        if entry.note:
            print(f"project: {entry.note}", file=sys.stderr)
    if json_output:
        readme = files[0]
        print(
            json_text(
                {
                    "action": readme.action,
                    "path": readme.path,
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
        raise summary.SpecError(f"the prepare command exited with status {result.returncode}: {command}")


NOT_HOSTED = 3


def run_summary(arguments: argparse.Namespace) -> int:
    if arguments.summary_command == "init":
        summary.init(arguments.repo, arguments.pr, arguments.spec, arguments.diff)
    else:
        summary.publish(
            Path(arguments.spec),
            arguments.remote,
            send_dispatch=not arguments.no_dispatch,
            diff_path=Path(arguments.diff) if arguments.diff else None,
        )
    return 0


def site_projects(root: Path) -> tuple[list, Optional[Path]]:
    """The repository's projects for the site, or none when its plans do not parse.

    A broken plan should cost the projects view, not the deploy that also
    carries the README, the docs, and the summaries.
    """
    store = ProjectStore(root, root, configured_projects_dir(root))
    if not store.projects_dir.is_dir():
        return [], None
    try:
        return store.projects(), store.projects_dir
    except ProjectorError as error:
        print(f"::warning title=Projects skipped::{error}", file=sys.stderr)
        return [], store.projects_dir


def site_repo(root: Path) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return repo
    try:
        return summary.repo_slug(summary.git("-C", str(root), "remote", "get-url", "origin"))
    except summary.SpecError:
        return ""


def site_branch(root: Path) -> str:
    try:
        branch = summary.git("-C", str(root), "rev-parse", "--abbrev-ref", "HEAD")
    except summary.SpecError:
        return "main"
    return branch if branch and branch != "HEAD" else "main"


def build_checkout(root: Path, out: Path, summaries: Optional[Path], base: str, repo: str) -> str:
    """Build the site from a checkout, and summarize what it holds."""
    projects, projects_dir = site_projects(root)
    entries, failures = site.build_site(
        out,
        summaries=summaries,
        repo_root=root,
        projects=projects,
        projects_dir=projects_dir,
        repo=repo,
        branch=site_branch(root),
        base=base,
    )
    return f"{len(projects)} projects, {len(entries)} reviews, {len(failures)} reviews skipped"


def run_site_build(arguments: argparse.Namespace) -> int:
    root = Path(arguments.repo_root).resolve() if arguments.repo_root else discover_git_root(Path.cwd())
    repo = site_repo(root)
    if arguments.check_visibility:
        if not repo:
            raise summary.SpecError("cannot check the site's visibility without knowing the repository")
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
    ref, folder = None, summary.PAGES_ROOT
    if given is None:
        if not arguments.no_fetch:
            reason = serve.fetch_summaries(root, arguments.remote)
            if reason:
                print(f"serving the summaries already fetched; {arguments.remote} has none to fetch: {reason}",
                      file=sys.stderr)
        # The folder comes with the ref, because the old walkthroughs ref a
        # repository may still have keeps its specs under another name.
        ref, folder = serve.summaries_ref(root, arguments.remote) or (None, folder)
    projects_dir = configured_projects_dir(root)
    watched = [root / "README.md", root / "docs"]
    if projects_dir is not None:
        watched.append(projects_dir if projects_dir.is_absolute() else root / projects_dir)
    if given is not None:
        watched.append(given)

    def sources() -> tuple:
        return serve.fingerprint([path for path in watched if path.exists()]), serve.ref_commit(root, ref)

    def build(out: Path) -> str:
        # The build copies what it needs from the specs, so they outlive it only as long as it runs.
        with tempfile.TemporaryDirectory(prefix="projector-specs-") as specs:
            summaries = serve.extract_summaries(root, ref, Path(specs), folder) if ref is not None else given
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
        spec = json.loads(Path(arguments.spec).read_text(encoding="utf-8"))
        diff = Path(arguments.diff).read_text(encoding="utf-8") if arguments.diff else None
        payload = site.build_page(spec, Path(arguments.out), diff=diff, at_head=arguments.at_head)
        counts = payload["stats"]
        print(
            f"wrote {arguments.out}/index.html: {len(spec['groups'])} groups, "
            f"{counts['files']} files, +{counts['adds']} -{counts['dels']}"
        )
    elif command == "status":
        url, reason = summary.hosting(arguments.repo)
        if url is None:
            print(f"not hosted: {reason}")
            return NOT_HOSTED
        print(f"{url}reviews/{arguments.pr}/" if arguments.pr else url)
    else:
        root = discover_git_root(Path.cwd())
        text = site_workflow_text(root, arguments.action_ref, arguments.branch)
        if not arguments.write:
            print(text, end="")
            return 0
        write_site_workflow(root, text)
        print(f"wrote {root / WORKFLOW_PATH}; commit it to the default branch, where GitHub runs dispatched workflows")
    return 0


def site_workflow_text(root: Path, action_ref: str, branch: Optional[str] = None) -> str:
    projects_dir = configured_projects_dir(root)
    return summary.workflow_text(
        action_ref,
        branch or summary.default_branch(root=root),
        projects_dir.as_posix() if projects_dir is not None and not projects_dir.is_absolute() else None,
    )


def write_site_workflow(root: Path, text: str) -> FileAction:
    path = root / WORKFLOW_PATH
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return FileAction(WORKFLOW_PATH, "unchanged")
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


def init_site(root: Path, action_ref: str, takeover: bool = False) -> tuple[dict, FileAction]:
    """Set up the Projector site: its Pages site first, then the workflow that deploys to it.

    The workflow is written only once the Pages site is safe to deploy to, so
    a private repository whose site cannot be made private gets no workflow.
    Without `takeover`, a repository that already deploys its own Pages site,
    from a branch or from another workflow, keeps it.
    """
    try:
        repo = summary.repo_slug(summary.git("-C", str(root), "remote", "get-url", "origin"))
    except summary.SpecError:
        repo = ""
    if not repo:
        raise NotOnGitHub("origin is not a GitHub repository to set up Pages on")
    deployers = other_pages_deployers(root)
    if deployers and not takeover:
        raise summary.SpecError(f"{', '.join(deployers)} already deploys a GitHub Pages site; pass --site to "
                                    "add the Projector site's workflow beside it")
    if shutil.which("gh") is None:
        raise summary.SpecError("the gh CLI is not installed, and setting up Pages needs it")
    details = json.loads(summary.gh("api", f"repos/{repo}"))
    # Only an admin can change Pages or the website link. Without admin, a site
    # already set up still gets its workflow, which needs no admin to propose.
    admin = bool((details.get("permissions") or {}).get("admin"))
    pages = summary.enable_pages(repo, admin, takeover)
    # The website link is a convenience; failing to set it must not cost the workflow.
    try:
        pages["website"], note = summary.set_homepage(
            repo, pages["url"], details.get("homepage") or "", admin
        ) if pages["url"] else ("unchanged", "")
    except summary.SpecError as error:
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


def emit_site(pages: dict) -> None:
    print(f"{pages['pages']} GitHub Pages site {pages['url']}")
    if pages["visibility"] == "updated":
        print("updated GitHub Pages visibility: private")
    if pages["website"] == "updated":
        print(f"updated repository website {pages['url']}")


def run(arguments: argparse.Namespace) -> int:
    if arguments.command == "config":
        return run_config(arguments)
    if arguments.command == "upgrade":
        return run_upgrade(arguments.target)
    if arguments.command == "summary":
        return run_summary(arguments)
    if arguments.command == "site":
        return run_site(arguments)

    # Resolved once and passed down, so configuration and the store agree on
    # the repository without asking git for it twice.
    root = arguments.root.resolve() if arguments.root else discover_git_root(Path.cwd())
    projects_dir = arguments.projects_dir
    if projects_dir is None:
        projects_dir = configured_projects_dir(root)

    store = ProjectStore(Path.cwd(), root, projects_dir)
    command = arguments.command

    if command == "init":
        # An explicit --site or --no-site wins over configuration; unset, the
        # site is set up when it can be and skipped with a note when it cannot.
        wanted = site_enabled(root) if arguments.site is None else arguments.site
        try:
            files = store.init(instructions_enabled(root))
            pages = None
            if wanted:
                try:
                    pages, workflow = init_site(root, arguments.action_ref, takeover=bool(arguments.site))
                    files.append(workflow)
                except ProjectorError as error:
                    if arguments.site:
                        raise InitError(str(error), files) from error
                    pages = {"skipped": str(error)}
                    # A repository with no GitHub origin has no site to miss.
                    if not isinstance(error, NotOnGitHub):
                        print(f"project: site not set up: {error}; pass --no-site, or set site.enabled = false, "
                              "to stop setting it up", file=sys.stderr)
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


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parser().parse_args(argv))
    except ProjectorError as error:
        print(f"project: {error}", file=sys.stderr)
        return error.exit_code
    except (OSError, UnicodeDecodeError) as error:
        print(f"project: {error}", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
