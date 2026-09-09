from __future__ import annotations

import datetime
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote, urlsplit

from . import instructions


STATUSES = ("draft", "ready", "in-progress", "completed")
PRIORITIES = ("now", "next", "later")
NAME_PART = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
FRONTMATTER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)$")


def _field_line(field: str) -> re.Pattern[str]:
    return re.compile(
        rf"^(?P<prefix>{field}:[ \t]*)(?P<value>[^#\r\n]*?)"
        r"(?P<suffix>[ \t]*(?:#.*)?)$",
        re.MULTILINE,
    )


STATUS_LINE = _field_line("status")
PRIORITY_LINE = _field_line("priority")
FIELD_LINES = {"status": STATUS_LINE, "priority": PRIORITY_LINE}


class ProjectorError(Exception):
    exit_code = 65


class UsageError(ProjectorError):
    exit_code = 2


class ProjectNotFound(ProjectorError):
    exit_code = 66


class AmbiguousProject(ProjectorError):
    exit_code = 67


class EnvironmentError(ProjectorError):
    exit_code = 69


@dataclass(frozen=True)
class Project:
    name: str
    title: str
    status: str
    path: Path
    priority: Optional[str] = None
    owner: Optional[str] = None

    def public(self, root: Path) -> dict[str, object]:
        value = asdict(self)
        try:
            value["path"] = self.path.relative_to(root).as_posix()
        except ValueError:
            value["path"] = str(self.path)
        return value


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class FileAction:
    """What `init` did to one file: created, updated, unchanged, or kept.

    `kept` means the file was deliberately left alone, and `note` says why.
    """

    path: str
    action: str
    note: Optional[str] = None


class InitError(ProjectorError):
    """`init` handled every file it could, and one could not be brought up to date."""

    def __init__(self, message: str, files: list[FileAction]) -> None:
        super().__init__(message)
        self.files = files


def discover_git_root(start: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise EnvironmentError(f"not inside a Git repository: {start}") from error
    return Path(result.stdout.strip()).resolve()


def valid_name(name: str) -> bool:
    return bool(name) and all(NAME_PART.fullmatch(part) for part in name.split("/"))


def _yaml_scalar(raw: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in ("'", '"'):
            quote = None if quote == character else character if quote is None else quote
            continue
        if character == "#" and quote is None and (index == 0 or raw[index - 1].isspace()):
            raw = raw[:index]
            break
    return raw.strip().strip("\"'")


def parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ProjectorError(f"{path}: missing YAML frontmatter")

    metadata: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        value = line.rstrip("\r\n")
        if value == "---":
            return metadata, index + 1
        if not value or value.lstrip().startswith("#"):
            continue
        match = FRONTMATTER_LINE.fullmatch(value)
        if not match:
            raise ProjectorError(f"{path}:{index + 1}: malformed frontmatter")
        key, raw = match.groups()
        if key in metadata:
            raise ProjectorError(f"{path}:{index + 1}: duplicate {key!r} field")
        metadata[key] = _yaml_scalar(raw)
    raise ProjectorError(f"{path}: unclosed YAML frontmatter")


def title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback.rsplit("/", 1)[-1].replace("-", " ").title()


class ProjectStore:
    def __init__(
        self,
        cwd: Path,
        root: Optional[Path] = None,
        projects_dir: Optional[Path] = None,
    ) -> None:
        self.root = (root or discover_git_root(cwd)).resolve()
        if projects_dir is None:
            self.projects_dir = self.root / "docs" / "projects"
        elif projects_dir.is_absolute():
            self.projects_dir = projects_dir.resolve()
        else:
            self.projects_dir = (self.root / projects_dir).resolve()

    def _project_from_path(self, path: Path) -> Project:
        relative = path.parent.relative_to(self.projects_dir).as_posix()
        display_path = Path(self._relative(path))
        if relative == "." or not valid_name(relative):
            raise ProjectorError(f"{display_path}: invalid project name {relative!r}")
        text = self._read_text(path)
        metadata, _ = parse_frontmatter(text, display_path)
        status = metadata.get("status")
        if status not in STATUSES:
            choices = "|".join(STATUSES)
            raise ProjectorError(f"{display_path}: status must be one of {choices}")
        priority = metadata.get("priority")
        choices = "|".join(PRIORITIES)
        if priority is None:
            if status != "completed":
                raise ProjectorError(
                    f"{display_path}: priority must be one of {choices}"
                    " unless status is completed"
                )
        elif priority not in PRIORITIES:
            raise ProjectorError(f"{display_path}: priority must be one of {choices}")
        return Project(
            name=relative,
            title=title_from_text(text, relative),
            status=status,
            priority=priority,
            owner=metadata.get("owner"),
            path=path,
        )

    def _require_projects_dir(self) -> None:
        if not self.projects_dir.is_dir():
            raise ProjectNotFound(
                f"projects directory not found: {self._relative(self.projects_dir)}"
                " (run 'project init' to adopt the convention)"
            )

    def _entry_points(self) -> list[Path]:
        self._require_projects_dir()
        paths: list[Path] = []
        for directory, _, filenames in os.walk(self.projects_dir):
            if "readme.md" in filenames:
                paths.append(Path(directory) / "readme.md")
        return sorted(paths)

    def projects(self) -> list[Project]:
        found: dict[str, Project] = {}
        for path in self._entry_points():
            try:
                project = self._project_from_path(path)
            except (ProjectorError, OSError, UnicodeDecodeError) as error:
                message = str(error)
                prefix = f"{self._relative(path)}:"
                if not message.startswith(prefix):
                    message = f"{prefix} {message}"
                raise ProjectorError(
                    f"{message} (run 'project check' for the full report)"
                ) from error
            folded = project.name.casefold()
            if folded in found:
                raise AmbiguousProject(
                    f"ambiguous project names: {found[folded].name}, {project.name}"
                )
            found[folded] = project
        return sorted(found.values(), key=lambda project: project.name)

    def resolve(self, name: str) -> Project:
        candidates = []
        for path in self._entry_points():
            relative = path.parent.relative_to(self.projects_dir).as_posix()
            if relative.casefold() == name.casefold():
                candidates.append(path)
        if len(candidates) > 1:
            raise AmbiguousProject(f"ambiguous project: {name}")
        if candidates:
            return self._project_from_path(candidates[0])
        raise ProjectNotFound(f"project not found: {name}")

    def init(self, instructions_enabled: bool = True) -> list[FileAction]:
        """Adopt the convention, or bring an adopted repository up to date.

        Safe to run again. The projects README is authored content: created
        once and never rewritten. The Projector section in `AGENTS.md` and the
        import in `CLAUDE.md` are generated content: created when absent and
        refreshed when they fall behind the template this package ships.
        """

        target = self.projects_dir / "README.md"
        if target.exists():
            files = [FileAction(self._relative(target), "unchanged")]
        else:
            self.projects_dir.mkdir(parents=True, exist_ok=True)
            template = resources.files("projector").joinpath(
                "templates/project-readme.md"
            ).read_text(encoding="utf-8")
            self._create_exclusive(target, template)
            files = [FileAction(self._relative(target), "created")]
        if instructions_enabled:
            more, failure = self._init_instructions()
            files.extend(more)
            if failure is not None:
                raise InitError(failure, files)
        return files

    def create(
        self,
        name: str,
        status: str = "draft",
        priority: str = "later",
        parent: str | None = None,
    ) -> Project:
        self._require_projects_dir()
        if parent:
            if "/" in name or not valid_name(name):
                raise UsageError("--parent requires one valid project name segment")
            self.resolve(parent)
            name = f"{parent}/{name}"
        if not valid_name(name):
            raise UsageError(
                "project names use lowercase letters, digits, hyphens, and slashes"
            )
        if status not in STATUSES:
            raise ProjectorError(f"invalid status: {status}")
        if priority not in PRIORITIES:
            raise ProjectorError(f"invalid priority: {priority}")
        if "/" in name:
            self.resolve(name.rsplit("/", 1)[0])
        path = self.projects_dir / name / "readme.md"
        if path.exists() or any(
            candidate.name.lower() == "readme.md" for candidate in path.parent.glob("*")
        ):
            raise ProjectorError(f"project already exists: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        title = name.rsplit("/", 1)[-1].replace("-", " ").title()
        body = (
            f"---\nstatus: {status}\npriority: {priority}\n---\n\n# {title}\n\n"
            "## 1. Outcome\n\nDescribe the result this project produces.\n\n"
            "## 2. Acceptance criteria\n\n"
            "- Define the evidence that proves the project is complete.\n"
        )
        self._create_exclusive(path, body)
        return self._project_from_path(path)

    @staticmethod
    def _create_exclusive(path: Path, content: str) -> None:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as error:
            raise ProjectorError(f"refusing to overwrite: {path}") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)

    # Agent instructions: the Projector section in AGENTS.md and the CLAUDE.md
    # import. Both hosts read the repository root, whatever the projects
    # directory is, so these paths hang off `self.root`.

    def _rendered_block(self) -> str:
        return instructions.render(self._relative(self.projects_dir))

    def _outside(self, path: Path) -> bool:
        """Whether `path` exists as a link that leads out of the repository."""

        if not (path.exists() or path.is_symlink()):
            return False
        return not path.resolve().is_relative_to(self.root)

    @staticmethod
    def _same_file(first: Path, second: Path) -> bool:
        return first.exists() and second.exists() and first.resolve() == second.resolve()

    @staticmethod
    def _write_through(path: Path, content: str) -> None:
        """Write `content` to the file `path` names, following any symlink.

        Swapping a temporary file in at `path` itself would turn a link into a
        regular file, so the swap happens at the resolved target: the link stays
        a link and the file it points at gets the content.
        """

        target = path.resolve()
        if not target.exists():
            # A new file gets the mode the README gets, 0644 under the umask.
            # `mkstemp` would leave it at the temporary's owner-only 0600, which
            # Git never records and another uid cannot read.
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
            return
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _leaves_repository(path: Path) -> str:
        return f"{path.name}: link leaves the repository ({path.resolve()}); not written"

    @staticmethod
    def _external_message(path: Path) -> str:
        return (
            f"resolves outside the repository to {path.resolve()} (replace the link"
            " with a file in the repository, or set instructions.enabled = false)"
        )

    def _init_instructions(self) -> tuple[list[FileAction], Optional[str]]:
        """Bring `AGENTS.md` and `CLAUDE.md` up to date, one block wherever possible.

        Codex reads `AGENTS.md` and Claude Code reads `CLAUDE.md`. When one of
        the two is absent it becomes a symlink to the other, so one file serves
        both hosts and the block is written once. A `CLAUDE.md` that links to
        or imports `AGENTS.md` already reads the block through it. Only when the
        two are genuinely distinct files, each with its own content and no
        import between them, does each get the block; appending an import would
        change everything Claude Code reads, not only Projector's part.
        """

        agents = self.root / "AGENTS.md"
        claude = self.root / "CLAUDE.md"
        files: list[FileAction] = []
        failures: list[str] = []

        def write(path: Path) -> FileAction:
            try:
                return self._write_block(path)
            except instructions.MalformedBlock as error:
                failures.append(
                    f"{path.name}: Projector section markers are malformed: {error}"
                    " (repair the markers by hand)"
                )
                return FileAction(path.name, "kept")

        agents_outside = self._outside(agents)
        claude_outside = self._outside(claude)
        claude_first = (
            not agents.exists()
            and not agents.is_symlink()
            and claude.exists()
            and not claude_outside
            and not instructions.imports_agents(self._read_text(claude))
        )

        if agents_outside:
            files.append(FileAction("AGENTS.md", "kept", self._leaves_repository(agents)))
        elif claude_first and self._link(agents, claude):
            # A repository that adopted CLAUDE.md first: AGENTS.md becomes a
            # link to it, and the block lands once in the shared file below.
            files.append(FileAction("AGENTS.md", "created"))
            files.append(write(claude))
            return files, "\n".join(failures) or None
        else:
            files.append(write(agents))

        if claude_outside:
            files.append(FileAction("CLAUDE.md", "kept", self._leaves_repository(claude)))
        elif self._same_file(agents, claude):
            files.append(FileAction("CLAUDE.md", "unchanged"))
        elif claude.is_symlink() and not claude.exists():
            files.append(
                FileAction("CLAUDE.md", "kept", "CLAUDE.md: dangling link; not written")
            )
        elif not claude.exists():
            if self._link(claude, agents):
                files.append(FileAction("CLAUDE.md", "created"))
            else:
                # No symlinks here (Windows without Developer Mode): the import
                # is the documented alternative and reads the same file.
                self._write_through(claude, instructions.IMPORT_LINE + "\n")
                files.append(FileAction("CLAUDE.md", "created"))
        elif instructions.imports_agents(self._read_text(claude)):
            files.append(FileAction("CLAUDE.md", "unchanged"))
        else:
            files.append(write(claude))
        return files, "\n".join(failures) or None

    @staticmethod
    def _link(link: Path, target: Path) -> bool:
        """Make `link` a relative symlink to `target`; False where symlinks fail."""

        try:
            os.symlink(target.name, link)
        except OSError:
            return False
        return True

    def _write_block(self, path: Path) -> FileAction:
        rendered = self._rendered_block()
        if not path.exists():
            self._write_through(path, rendered + "\n")
            return FileAction(path.name, "created")
        text = self._read_text(path)
        block = instructions.find_block(text)
        shipped = instructions.template_version()
        if block is not None:
            if block.version > shipped:
                return FileAction(
                    path.name,
                    "kept",
                    f"{path.name}: Projector section is version {block.version}; this command"
                    f" ships version {shipped} (run 'project upgrade', or reinstall the CLI)",
                )
            if block.version == shipped and instructions.block_matches(block, rendered):
                return FileAction(path.name, "unchanged")
        self._write_through(path, instructions.with_block(text, rendered))
        return FileAction(path.name, "updated")

    def instruction_issues(self) -> list[Issue]:
        """Warnings about the Projector section and the `CLAUDE.md` import.

        Every issue here is a warning: a collaborator on an older CLI is told
        what to do without a failing gate, and `init` never downgrades a block.
        """

        agents = self.root / "AGENTS.md"
        claude = self.root / "CLAUDE.md"
        issues: list[Issue] = []

        if self._outside(agents):
            issues.append(self._external_issue(agents))
        else:
            issues.extend(self._block_issues(agents))

        if self._outside(claude):
            issues.append(self._external_issue(claude))
        elif self._same_file(agents, claude):
            pass
        elif not claude.exists():
            issues.append(
                Issue(
                    "instructions-missing",
                    "CLAUDE.md",
                    "is absent, and Claude Code reads CLAUDE.md rather than AGENTS.md (run"
                    " 'project init' to link it)",
                    "warning",
                )
            )
        elif not instructions.imports_agents(self._read_text(claude)):
            issues.extend(self._block_issues(claude))
        return issues

    def _external_issue(self, path: Path) -> Issue:
        return Issue("instructions-external", path.name, self._external_message(path), "warning")

    def _block_issues(self, path: Path) -> list[Issue]:
        """Warnings about the Projector section in one instruction file."""

        def warn(code: str, message: str) -> list[Issue]:
            return [Issue(code, path.name, message, "warning")]

        shipped = instructions.template_version()
        missing = "has no Projector section (run 'project init' to add it)"
        if not path.exists():
            return warn("instructions-missing", missing)
        try:
            block = instructions.find_block(self._read_text(path))
        except instructions.MalformedBlock as error:
            return warn(
                "instructions-malformed",
                f"Projector section markers are malformed: {error} (repair the markers by hand)",
            )
        if block is None:
            return warn("instructions-missing", missing)
        if block.version < shipped:
            return warn(
                "instructions-outdated",
                f"Projector section is version {block.version}; this command ships version"
                f" {shipped} (run 'project init' to refresh it; your own content is not changed)",
            )
        if block.version > shipped:
            return warn(
                "instructions-ahead",
                f"Projector section is version {block.version}; this command ships version"
                f" {shipped} (run 'project upgrade', or reinstall the CLI)",
            )
        if not instructions.block_matches(block, self._rendered_block()):
            return warn(
                "instructions-edited",
                f"Projector section differs from the version {shipped} template (run 'project"
                " init' to restore it, or change the template in Projector)",
            )
        return []

    def set_status(self, name: str, status: str) -> tuple[Project, bool]:
        if status not in STATUSES:
            raise ProjectorError(f"invalid status: {status}")
        if status != "completed":
            project = self.resolve(name)
            if project.priority is None:
                choices = ", ".join(PRIORITIES[:-1]) + f", or {PRIORITIES[-1]}"
                raise ProjectorError(
                    f"{self._relative(project.path)}: leaving completed needs a"
                    f" priority first; run project priority {name} with"
                    f" {choices}"
                )
        return self._set_field(name, "status", status)

    def set_priority(self, name: str, priority: str) -> tuple[Project, bool]:
        if priority not in PRIORITIES:
            raise ProjectorError(f"invalid priority: {priority}")
        return self._set_field(name, "priority", priority)

    def _set_field(self, name: str, field: str, value: str) -> tuple[Project, bool]:
        project = self.resolve(name)
        before = self._read_text(project.path)
        original_stat = project.path.stat()
        metadata, _ = parse_frontmatter(before, project.path)
        if metadata.get(field) == value:
            return project, False
        match = FIELD_LINES[field].search(before)
        if match is None and field == "priority" and "priority" not in metadata:
            after = self._insert_priority(before, project.path, value)
        elif not match or (
            match.group("value").strip().strip("\"'") != getattr(project, field)
        ):
            raise ProjectorError(f"{project.path}: cannot update {field} safely")
        else:
            after = before[: match.start("value")] + value + before[match.end("value") :]
        current_stat = project.path.stat()
        signature = (original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns)
        current = (current_stat.st_ino, current_stat.st_size, current_stat.st_mtime_ns)
        if signature != current:
            raise ProjectorError(f"{project.path}: changed while it was being read")
        self._atomic_write(project.path, after, signature)
        return self.resolve(name), True

    @staticmethod
    def _insert_priority(before: str, path: Path, value: str) -> str:
        match = STATUS_LINE.search(before)
        if not match:
            raise ProjectorError(f"{path}: cannot update priority safely")
        line_end = before.find("\n", match.end())
        insert_at = len(before) if line_end < 0 else line_end + 1
        newline = "\r\n" if "\r\n" in before else "\n"
        return before[:insert_at] + f"priority: {value}{newline}" + before[insert_at:]

    @staticmethod
    def _atomic_write(
        path: Path, content: str, expected_signature: tuple[int, int, int]
    ) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            current_stat = path.stat()
            current = (current_stat.st_ino, current_stat.st_size, current_stat.st_mtime_ns)
            if current != expected_signature:
                raise ProjectorError(f"{path}: changed before the update was written")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def search(
        self,
        query: str,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[dict[str, object]]:
        projects = self.projects()
        roots = sorted(projects, key=lambda project: len(project.path.parts), reverse=True)
        matches: list[dict[str, object]] = []
        needle = query.casefold()

        def selected(project: Project) -> bool:
            return (not status or project.status == status) and (
                not priority or project.priority == priority
            )

        for project in projects:
            metadata = " ".join(
                value
                for value in (
                    project.name,
                    project.title,
                    project.status,
                    project.priority,
                    project.owner,
                )
                if value
            )
            if selected(project) and needle in metadata.casefold():
                matches.append(
                    {
                        "project": project.name,
                        "path": self._relative(project.path),
                        "line": 0,
                        "text": project.title,
                    }
                )
        for path in sorted(self.projects_dir.rglob("*.md")):
            if path == self.projects_dir / "README.md":
                continue
            owner = next(
                (project for project in roots if path == project.path or project.path.parent in path.parents),
                None,
            )
            if owner is None or not selected(owner):
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if needle in line.casefold():
                    matches.append(
                        {
                            "project": owner.name,
                            "path": self._relative(path),
                            "line": line_number,
                            "text": line.strip(),
                        }
                    )
        return matches

    def check(self, instructions_enabled: bool = True) -> list[Issue]:
        issues = self._plan_issues()
        if instructions_enabled:
            issues.extend(self.instruction_issues())
        return issues

    def _plan_issues(self) -> list[Issue]:
        issues: list[Issue] = []
        if not self.projects_dir.exists():
            return [Issue("missing-projects-dir", self._relative(self.projects_dir), "directory does not exist")]

        top_level = [path for path in self.projects_dir.iterdir() if path.is_dir()]
        for directory in sorted(top_level):
            if not any(child.name == "readme.md" and child.is_file() for child in directory.iterdir()):
                issues.append(
                    Issue(
                        "missing-plan",
                        self._relative(directory),
                        "top-level project directory has no lowercase readme.md",
                    )
                )

        seen_case: dict[str, str] = {}
        wrong_case_paths: set[str] = set()
        for path in sorted(self.projects_dir.rglob("*")):
            if path.is_symlink():
                issues.append(Issue("symlink", self._relative(path), "project trees cannot contain symlinks"))
            if (
                path.parent != self.projects_dir
                and path.is_file()
                and path.name.lower() == "readme.md"
                and path.name != "readme.md"
            ):
                wrong_case_paths.add(self._relative(path))
                issues.append(
                    Issue("wrong-entry-case", self._relative(path), "project entry point must be lowercase readme.md")
                )
            relative = path.relative_to(self.projects_dir).as_posix()
            folded = relative.casefold()
            if folded in seen_case and seen_case[folded] != relative:
                issues.append(
                    Issue("case-collision", relative, f"collides with {seen_case[folded]}")
                )
            seen_case[folded] = relative

        tracked_case: dict[str, str] = {}
        for relative in self._tracked_paths():
            name = Path(relative).name
            full_path = (Path(self._relative(self.projects_dir)) / relative).as_posix()
            folded = relative.casefold()
            if folded in tracked_case and tracked_case[folded] != relative:
                issues.append(
                    Issue(
                        "case-collision",
                        full_path,
                        f"Git path collides with {tracked_case[folded]}",
                    )
                )
            tracked_case[folded] = relative
            project_parts = Path(relative).parts[:-1]
            if name.lower() == "readme.md" and any(
                not NAME_PART.fullmatch(part) for part in project_parts
            ):
                issues.append(
                    Issue(
                        "wrong-project-case",
                        full_path,
                        "Git records a project directory with invalid or uppercase casing",
                    )
                )
            if (
                name.lower() == "readme.md"
                and name != "readme.md"
                and Path(relative).parent != Path(".")
                and full_path not in wrong_case_paths
            ):
                issues.append(
                    Issue(
                        "wrong-entry-case",
                        full_path,
                        "Git records the project entry point with casing other than readme.md",
                    )
                )

        projects: list[Project] = []
        names: dict[str, str] = {}
        for path in self._entry_points():
            try:
                project = self._project_from_path(path)
                folded = project.name.casefold()
                if folded in names:
                    raise AmbiguousProject(
                        f"ambiguous project names: {names[folded]}, {project.name}"
                    )
                names[folded] = project.name
                projects.append(project)
            except (ProjectorError, OSError, UnicodeDecodeError) as error:
                message = str(error)
                prefix = f"{self._relative(path)}:"
                if message.startswith(prefix):
                    message = message[len(prefix) :].lstrip()
                issues.append(Issue("invalid-project", self._relative(path), message))

        for path in sorted(self.projects_dir.rglob("*.md")):
            if any(path == project.path or project.path.parent in path.parents for project in projects):
                issues.extend(self._check_links(path))
        return issues

    def _check_links(self, path: Path) -> list[Issue]:
        issues: list[Issue] = []
        text = self._read_text(path)
        for match in LINK.finditer(text):
            raw_target = match.group(1)
            line_number = text.count("\n", 0, match.start()) + 1
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>\"'")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            candidate = (path.parent / unquote(parsed.path)).resolve()
            if not self._exact_path_exists(candidate):
                issues.append(
                    Issue(
                        "broken-project-link",
                        f"{self._relative(path)}:{line_number}",
                        f"target does not exist: {target}",
                    )
                )
        unmatched = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        unmatched = LINK.sub("", unmatched)
        for match in re.finditer(r"\]\(", unmatched):
            line_number = unmatched.count("\n", 0, match.start()) + 1
            issues.append(
                Issue(
                    "malformed-project-link",
                    f"{self._relative(path)}:{line_number}",
                    "malformed Markdown link",
                )
            )
        return issues

    def _tracked_paths(self) -> list[str]:
        try:
            prefix = self.projects_dir.relative_to(self.root).as_posix()
        except ValueError:
            return []
        result = subprocess.run(
            ["git", "-C", str(self.root), "ls-files", "-z", "--", prefix],
            check=False,
            capture_output=True,
        )
        if result.returncode:
            return []
        paths = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        prefix_with_slash = f"{prefix}/"
        return [path[len(prefix_with_slash) :] for path in paths if path.startswith(prefix_with_slash)]

    @staticmethod
    def _exact_path_exists(path: Path) -> bool:
        if not path.exists():
            return False
        current = Path(path.anchor)
        for part in path.parts[1:]:
            try:
                names = {entry.name for entry in current.iterdir()}
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                return False
            if part not in names:
                return False
            current /= part
        return True

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _read_text(path: Path) -> str:
        with path.open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

def json_scalar(value: object) -> str:
    """Render a value `json` cannot, or refuse loudly.

    TOML has first-class dates, times, and datetimes, so `tomllib` hands back
    `datetime` objects for an unquoted `deadline = 2026-10-01` -- which is what
    a person writes without thinking. ISO 8601 is the spelling the file used.
    Anything else still raises, so an genuinely unserializable value fails
    where it is introduced rather than becoming a silent `str()`.
    """

    # `datetime.datetime` subclasses `datetime.date`, so these two cover all
    # three TOML temporal types.
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_text(payload: dict[str, object]) -> str:
    return json.dumps(
        {"schema_version": 2, **payload}, indent=2, sort_keys=True, default=json_scalar
    )


def grouped_projects(projects: Iterable[Project]) -> str:
    remaining = list(projects)
    groups: list[tuple[str, list[Project]]] = [
        (
            priority,
            [
                project
                for project in remaining
                if project.status != "completed" and project.priority == priority
            ],
        )
        for priority in PRIORITIES
    ]
    groups.append(
        ("completed", [project for project in remaining if project.status == "completed"])
    )
    rows: list[str] = []
    for label, group in groups:
        if not group:
            continue
        rows.append(f"{label}:")
        rows.extend(
            f"  {project.name:<28} {project.status:<12} {project.title}"
            + (f" [{project.owner}]" if project.owner else "")
            for project in group
        )
    return "\n".join(rows)
