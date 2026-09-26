from __future__ import annotations

import argparse
import datetime
import importlib.metadata as metadata
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

from . import site, walkthrough
from .walkthrough import WORKFLOW_PATH
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


def checkout() -> Path:
    """The checkout this command was installed from.

    pip records the source of every install in `direct_url.json`, so the
    command can find its own installer without being told where the checkout
    is. A command installed from a Git URL has no checkout, and therefore no
    `install.sh` to hand off to.
    """

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
    record = json.loads(text)
    if "dir_info" not in record:
        raise EnvironmentError(
            f"cannot upgrade: install.sh needs a checkout, and project was installed "
            f"from {record['url']}"
        )
    path = Path(url2pathname(urlparse(record["url"]).path))
    if not path.is_dir():
        raise EnvironmentError(f"cannot upgrade: install source no longer exists: {path}")
    return path


def run_upgrade(targets: list[str]) -> int:
    """Run the checkout's `install.sh` from anywhere.

    Installation belongs to the installer, which already knows how to place
    the CLI and each host plugin from this checkout. `project upgrade all`
    is `./install.sh all` with the same targets, output, and exit status, so
    nothing here decides what an upgrade is.
    """

    script = checkout() / "install.sh"
    if not script.is_file():
        raise EnvironmentError(f"cannot upgrade: {script} does not exist")
    command = [str(script), *targets]
    print(f"+ {shlex.join(command)}", file=sys.stderr)
    # `install.sh cli` rewrites this package under the running interpreter.
    # Everything this process still needs is imported already, so nothing
    # below reaches for a file the reinstall is in the middle of replacing.
    return subprocess.run(command, check=False).returncode


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

    add_output(subcommands.add_parser("init", help="adopt the project convention"))

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

    walkthrough = subcommands.add_parser("walkthrough", help="write and publish pull request walkthrough specs")
    walkthrough_commands = walkthrough.add_subparsers(dest="walkthrough_command", required=True)
    walkthrough_init = walkthrough_commands.add_parser("init", help="write a skeleton spec for a pull request")
    walkthrough_init.add_argument("--repo", required=True, help="OWNER/NAME")
    walkthrough_init.add_argument("--pr", required=True, type=int)
    walkthrough_init.add_argument("--spec", required=True)
    walkthrough_init.add_argument("--diff", help="read the diff from this file instead of GitHub")
    walkthrough_publish = walkthrough_commands.add_parser(
        "publish", help="commit a spec to the hidden walkthroughs ref and start a site build"
    )
    walkthrough_publish.add_argument("--spec", required=True)
    walkthrough_publish.add_argument("--remote", default="origin")
    walkthrough_publish.add_argument("--diff", help="publish the diff from this file instead of fetching it from GitHub")
    walkthrough_publish.add_argument(
        "--no-dispatch", action="store_true", help="push the spec without starting the workflow"
    )

    site = subcommands.add_parser("site", help="build the Projector site a repository serves from GitHub Pages")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_build = site_commands.add_parser(
        "build", help="build the site from the README, docs, project plans, and published walkthroughs"
    )
    site_build.add_argument("--out", required=True)
    site_build.add_argument("--walkthroughs", help="directory holding <number>/<head>/spec.json files")
    site_build.add_argument(
        "--repo-root", help="checkout whose README.md and docs/ the site serves (default: this repository)"
    )
    site_page = site_commands.add_parser("page", help="build one walkthrough page from a spec")
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
    site_status.add_argument("--pr", type=int, help="print the URL of this pull request's walkthrough")
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


def emit_files(files: list[FileAction], json_output: bool) -> None:
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


NOT_HOSTED = 3


def run_walkthrough(arguments: argparse.Namespace) -> int:
    if arguments.walkthrough_command == "init":
        walkthrough.init(arguments.repo, arguments.pr, arguments.spec, arguments.diff)
    else:
        walkthrough.publish(
            Path(arguments.spec),
            arguments.remote,
            send_dispatch=not arguments.no_dispatch,
            diff_path=Path(arguments.diff) if arguments.diff else None,
        )
    return 0


def site_projects(root: Path) -> tuple[list, Optional[Path]]:
    """The repository's projects for the site, or none when its plans do not parse.

    A broken plan should cost the projects view, not the deploy that also
    carries the README, the docs, and the walkthroughs.
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
        return walkthrough.repo_slug(walkthrough.git("-C", str(root), "remote", "get-url", "origin"))
    except walkthrough.SpecError:
        return ""


def site_branch(root: Path) -> str:
    try:
        branch = walkthrough.git("-C", str(root), "rev-parse", "--abbrev-ref", "HEAD")
    except walkthrough.SpecError:
        return "main"
    return branch if branch and branch != "HEAD" else "main"


def run_site_build(arguments: argparse.Namespace) -> int:
    root = Path(arguments.repo_root).resolve() if arguments.repo_root else discover_git_root(Path.cwd())
    projects, projects_dir = site_projects(root)
    entries, failures = site.build_site(
        Path(arguments.out),
        walkthroughs=Path(arguments.walkthroughs) if arguments.walkthroughs else None,
        repo_root=root,
        projects=projects,
        projects_dir=projects_dir,
        repo=site_repo(root),
        branch=site_branch(root),
    )
    print(
        f"wrote {arguments.out}/index.html: {len(projects)} projects, {len(entries)} pull requests, "
        f"{len(failures)} walkthroughs skipped"
    )
    return 0


def run_site(arguments: argparse.Namespace) -> int:
    command = arguments.site_command
    if command == "build":
        return run_site_build(arguments)
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
        url, reason = walkthrough.hosting(arguments.repo)
        if url is None:
            print(f"not hosted: {reason}")
            return NOT_HOSTED
        print(f"{url}{arguments.pr}/" if arguments.pr else url)
    else:
        text = walkthrough.workflow_text(arguments.action_ref, arguments.branch or walkthrough.default_branch())
        if not arguments.write:
            print(text, end="")
            return 0
        path = discover_git_root(Path.cwd()) / WORKFLOW_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}; commit it to the default branch, where GitHub runs dispatched workflows")
    return 0


def run(arguments: argparse.Namespace) -> int:
    if arguments.command == "config":
        return run_config(arguments)
    if arguments.command == "upgrade":
        return run_upgrade(arguments.target)
    if arguments.command == "walkthrough":
        return run_walkthrough(arguments)
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
        try:
            files = store.init(instructions_enabled(root))
        except InitError as error:
            # Say what was written before saying what could not be.
            if not arguments.json_output:
                emit_files(error.files, False)
            raise
        emit_files(files, arguments.json_output)
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
