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

from .config import ConfigError
from .config import load as load_config
from .core import (
    PRIORITIES,
    STATUSES,
    EnvironmentError,
    ProjectorError,
    ProjectStore,
    discover_git_root,
    grouped_projects,
    json_scalar,
    json_text,
)


def distribution_version() -> str:
    """The version of the installed distribution, not of this source tree.

    `install.sh status` compares this against `setup.cfg` to tell a stale
    install from a current one. A checkout that was never installed has no
    distribution to report, which is itself the answer.
    """

    try:
        return metadata.version("projector-cli")
    except metadata.PackageNotFoundError:
        return "unknown"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="project")
    result.add_argument(
        "--version",
        action="version",
        version=f"project {distribution_version()}",
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


def configured_projects_dir(root: Path) -> Optional[Path]:
    """`projects.dir` from configuration, when the flag did not supply one."""

    configured = load_config(root).get("projects.dir")
    if configured is None:
        return None
    if not isinstance(configured, str):
        raise ConfigError(f"projects.dir must be a string, not {type(configured).__name__}")
    return Path(configured)


def run(arguments: argparse.Namespace) -> int:
    if arguments.command == "config":
        return run_config(arguments)

    # Resolved once and passed down, so configuration and the store agree on
    # the repository without asking git for it twice.
    root = arguments.root.resolve() if arguments.root else discover_git_root(Path.cwd())
    projects_dir = arguments.projects_dir
    if projects_dir is None:
        projects_dir = configured_projects_dir(root)

    store = ProjectStore(Path.cwd(), root, projects_dir)
    command = arguments.command

    if command == "init":
        emit_path(store, store.init(), arguments.json_output, "created")
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
        issues = store.check()
        if arguments.json_output:
            print(json_text({"valid": not issues, "issues": [issue.__dict__ for issue in issues]}))
        elif issues:
            for issue in issues:
                print(f"{issue.path}: {issue.message} [{issue.code}]", file=sys.stderr)
        else:
            print("Project plans are valid.")
        return 0 if not issues else 65
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
