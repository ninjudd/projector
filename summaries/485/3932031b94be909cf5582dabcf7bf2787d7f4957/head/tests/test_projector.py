from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import importlib.metadata as metadata

from projector import cli, instructions, permissions, review
from projector.cli import distribution_version, main
from projector.core import AmbiguousProject, ProjectorError, ProjectStore, project_from_text
from tests.scratch import copy_of


PLAN = """---
status: {status}
{priority}{extra}---

# {title}

## 1. Outcome

{body}
"""


def priority_line(priority: str | None) -> str:
    return f"priority: {priority}\n" if priority else ""


def granted(*allow: str, ask: tuple[str, ...] = ()) -> dict:
    """Claude Code settings as `init` leaves them: the given rules, then Projector's own."""
    return {"permissions": {"allow": [*allow, *permissions.ALLOW], "ask": [*ask, *permissions.ASK]}}


def empty_repository(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)


class RepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, _ = copy_of(empty_repository)
        self.projects = self.root / "docs" / "projects"
        self.projects.mkdir(parents=True)
        (self.projects / "README.md").write_text("# Projects\n", encoding="utf-8")
        # A fully adopted repository, so `check` is quiet by default and each
        # instruction scenario removes or alters exactly what it tests.
        (self.root / "AGENTS.md").write_text(self.block() + "\n", encoding="utf-8")
        os.symlink("AGENTS.md", self.root / "CLAUDE.md")
        # Every command now reads layered configuration, and the user layer
        # lives at $HOME/.projector.toml; `init` also writes the user's Claude
        # Code settings under $HOME/.claude, and Codex rules under $HOME/.codex
        # when that directory exists. Point HOME at an empty directory,
        # outside the repository so a test that replaces the repository keeps
        # it, and unset CLAUDE_CONFIG_DIR and CODEX_HOME, so real ones on the
        # machine running the tests cannot reach them.
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("CODEX_HOME", None)
        self.settings().parent.mkdir()
        self.settings().write_text(
            json.dumps(granted(), indent=2) + "\n", encoding="utf-8"
        )
        # Commands run as an agent would, without a terminal, whatever runs the
        # tests; a test that stands for a person at a terminal says so.
        self.enterContext(mock.patch("sys.stdin.isatty", return_value=False))
        # The Codex rules `init` writes depend on the installed Codex's version,
        # so no test reads the version of whatever Codex runs the tests.
        self.codex = self.enterContext(mock.patch.object(permissions, "codex_version", return_value=None))

    def at_a_terminal(self) -> None:
        self.enterContext(mock.patch("sys.stdin.isatty", return_value=True))

    def settings(self) -> Path:
        """The user's Claude Code settings, which `init` gives Projector's permission rules."""
        return self.home / ".claude" / "settings.json"

    @staticmethod
    def block(projects_dir: str = "docs/projects") -> str:
        return instructions.render(projects_dir)

    def plan(
        self,
        name: str,
        status: str = "draft",
        title: str | None = None,
        body: str = "A useful result.",
        extra: str = "",
        priority: str | None = "later",
    ) -> Path:
        path = self.projects / name / "readme.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            PLAN.format(
                status=status,
                priority=priority_line(priority),
                title=title or name.rsplit("/", 1)[-1].title(),
                body=body,
                extra=extra,
            ),
            encoding="utf-8",
        )
        return path

    def invoke(self, *arguments: str, cwd: Path | None = None) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        previous = Path.cwd()
        os.chdir(cwd or self.root)
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(list(arguments))
        finally:
            os.chdir(previous)
        return code, stdout.getvalue(), stderr.getvalue()


class DiscoveryTests(RepositoryTestCase):
    def test_list_discovers_top_level_and_nested_projects_from_a_subdirectory(self) -> None:
        self.plan("payments", "in-progress", "Payments", priority="now")
        self.plan("payments/invoices", "ready", "Invoices", priority="next")
        notes = self.projects / "payments" / "notes"
        notes.mkdir()
        (notes / "design.md").write_text("No project sentinel.\n", encoding="utf-8")

        code, stdout, stderr = self.invoke(
            "list", "--json", cwd=self.projects / "payments" / "notes"
        )

        self.assertEqual(0, code, stderr)
        payload = json.loads(stdout)
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual(
            ["payments", "payments/invoices"],
            [project["name"] for project in payload["projects"]],
        )

    def test_status_and_priority_filters_and_human_groups_are_queries_only(self) -> None:
        self.plan("alpha", "in-progress", "Alpha", priority="now")
        self.plan("beta", "draft", "Beta", priority="later")
        self.plan("gamma", "completed", "Gamma", priority=None)

        code, stdout, _ = self.invoke("list", "--status", "in-progress")

        self.assertEqual(0, code)
        self.assertIn("now:", stdout)
        self.assertIn("alpha", stdout)
        self.assertNotIn("beta", stdout)
        self.assertFalse((self.projects / "now.md").exists())

        code, stdout, _ = self.invoke("list", "--priority", "later")

        self.assertEqual(0, code)
        self.assertIn("later:", stdout)
        self.assertIn("beta", stdout)
        self.assertNotIn("alpha", stdout)
        self.assertNotIn("gamma", stdout)

        code, stdout, _ = self.invoke("list")

        self.assertEqual(0, code)
        self.assertIn("completed:", stdout)
        self.assertLess(stdout.index("now:"), stdout.index("later:"))
        self.assertLess(stdout.index("later:"), stdout.index("completed:"))

    def test_show_returns_frontmatter_and_content(self) -> None:
        path = self.plan("alpha", "ready", "Alpha", priority="next")
        code, stdout, _ = self.invoke("show", "alpha", "--json")
        payload = json.loads(stdout)

        self.assertEqual(0, code)
        self.assertEqual("ready", payload["project"]["status"])
        self.assertEqual("next", payload["project"]["priority"])
        self.assertEqual(path.relative_to(self.root).as_posix(), payload["project"]["path"])
        self.assertIn("status: ready", payload["project"]["content"])

    def test_search_reports_the_nearest_containing_project(self) -> None:
        self.plan("parent", "in-progress", body="Parent only", priority="now")
        self.plan("parent/child", "ready", body="Needle in child", priority="next")
        design = self.projects / "parent" / "child" / "design.md"
        design.write_text("Another needle.\n", encoding="utf-8")

        code, stdout, _ = self.invoke("search", "needle", "--json")
        payload = json.loads(stdout)

        self.assertEqual(0, code)
        self.assertEqual({"parent/child"}, {match["project"] for match in payload["matches"]})
        self.assertEqual(2, len(payload["matches"]))

    def test_root_and_projects_dir_overrides_work(self) -> None:
        alternate = self.root / "plans"
        alternate.mkdir()
        (alternate / "README.md").write_text("# Plans\n", encoding="utf-8")
        path = alternate / "alpha" / "readme.md"
        path.parent.mkdir()
        path.write_text(
            PLAN.format(
                status="draft",
                priority=priority_line("later"),
                extra="",
                title="Alpha",
                body="Done",
            )
        )

        code, stdout, _ = self.invoke(
            "list", "--root", str(self.root), "--projects-dir", "plans", "--json"
        )

        self.assertEqual(0, code)
        self.assertEqual("alpha", json.loads(stdout)["projects"][0]["name"])

    def test_absolute_projects_directory_outside_root_emits_a_usable_path(self) -> None:
        with tempfile.TemporaryDirectory() as plans_directory:
            plans = Path(plans_directory)
            (plans / "README.md").write_text("# Plans\n")

            code, stdout, stderr = self.invoke(
                "create",
                "alpha",
                "--projects-dir",
                str(plans),
                "--no-edit",
                "--json",
            )

            self.assertEqual(0, code, stderr)
            self.assertEqual(
                str((plans / "alpha" / "readme.md").resolve()),
                json.loads(stdout)["path"],
            )

    def test_an_option_before_the_command_is_refused_and_runs_nothing(self) -> None:
        (self.root / "CLAUDE.md").unlink()
        for argv in (["--root", str(self.root), "init"], ["--root=.", "init"], ["--projects-dir", "plans", "list"],
                     ["--", "init"], ["--version"], ["--help"], ["-h"], ["--ver"]):
            with self.subTest(argv=argv):
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
                    main(argv)

                self.assertEqual(2, stop.exception.code)
                self.assertEqual("", stdout.getvalue())
                self.assertIn(f"project: error: {argv[0]}: options go after the command, as in "
                              "`project init --root .`; for the version or the help, run `project version` or "
                              "`project help`", stderr.getvalue())
                self.assertFalse((self.root / "CLAUDE.md").exists(), "init never ran")

    def test_invalid_plan_fails_discovery_with_a_diagnostic(self) -> None:
        self.plan("good", "in-progress", priority="now")
        self.plan("bad", "shipped")

        for arguments in (("list",), ("search", "good")):
            code, stdout, stderr = self.invoke(*arguments)
            self.assertEqual(65, code, arguments)
            self.assertEqual("", stdout)
            self.assertIn("bad/readme.md", stderr)
            self.assertIn("status must be one of", stderr)
            self.assertIn("project check", stderr)

        code, stdout, stderr = self.invoke("show", "good")
        self.assertEqual(0, code, stderr)
        self.assertIn("# Good", stdout)

        code, _, stderr = self.invoke("show", "bad")
        self.assertEqual(65, code)
        self.assertIn("status must be one of", stderr)

    def test_diagnostics_stay_repository_relative(self) -> None:
        uppercase = self.projects / "Payments" / "readme.md"
        uppercase.parent.mkdir(parents=True)
        uppercase.write_text(
            PLAN.format(
                status="draft",
                priority=priority_line("now"),
                extra="",
                title="Pay",
                body="Done",
            ),
            encoding="utf-8",
        )

        code, _, stderr = self.invoke("list")

        self.assertEqual(65, code)
        self.assertIn("docs/projects/Payments/readme.md: invalid project name", stderr)
        self.assertNotIn(str(self.root), stderr)

        code, stdout, _ = self.invoke("check", "--json")

        self.assertEqual(65, code)
        self.assertNotIn(str(self.root), stdout)
        invalid = [
            issue
            for issue in json.loads(stdout)["issues"]
            if issue["code"] == "invalid-project"
        ]
        self.assertEqual(
            ["invalid project name 'Payments'"],
            [issue["message"] for issue in invalid],
        )

    def test_list_accepts_an_adopted_repository_with_no_projects(self) -> None:
        code, stdout, stderr = self.invoke("list", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual([], json.loads(stdout)["projects"])

    def test_commands_require_the_projects_directory(self) -> None:
        (self.projects / "README.md").unlink()
        self.projects.rmdir()

        # Every command that reads projects reaches the check through discovery, and create checks for itself.
        for arguments in (("list",), ("create", "alpha", "--no-edit")):
            code, stdout, stderr = self.invoke(*arguments)
            self.assertEqual(66, code, arguments)
            self.assertEqual("", stdout, arguments)
            self.assertIn("projects directory not found", stderr)
            self.assertIn("project init", stderr)
            self.assertFalse(self.projects.exists(), arguments)


class MutationTests(RepositoryTestCase):
    def test_create_supports_nested_projects_without_moving_the_parent(self) -> None:
        parent = self.plan("payments", "in-progress", priority="now")

        code, stdout, stderr = self.invoke(
            "create",
            "invoices",
            "--parent",
            "payments",
            "--status",
            "ready",
            "--priority",
            "next",
            "--no-edit",
        )

        self.assertEqual(0, code, stderr)
        self.assertEqual("docs/projects/payments/invoices/readme.md\n", stdout)
        self.assertTrue(parent.exists())
        created = (self.projects / "payments" / "invoices" / "readme.md").read_text()
        self.assertIn("status: ready", created)
        self.assertIn("priority: next", created)

    def test_create_scaffolds_the_problem_before_the_solution(self) -> None:
        code, stdout, stderr = self.invoke("create", "card-limit", "--no-edit")

        self.assertEqual(0, code, stderr)
        self.assertEqual("docs/projects/card-limit/readme.md\n", stdout)
        created = (self.projects / "card-limit" / "readme.md").read_text()
        headings = [line for line in created.splitlines() if line.startswith("#")]
        self.assertEqual(
            [
                "# Card Limit",
                "## 1. Problem",
                "## 2. Solution",
                "## 3. Why this design",
                "## 4. Acceptance criteria",
            ],
            headings,
        )
        summary = created.split("# Card Limit\n\n", 1)[1].split("\n", 1)[0]
        self.assertEqual("Say in one sentence what this project makes true.", summary)
        self.assertNotIn("Outcome", created)

    def test_create_refuses_invalid_or_existing_names(self) -> None:
        self.plan("alpha")
        code, _, stderr = self.invoke("create", "alpha", "--no-edit")
        self.assertEqual(65, code)
        self.assertIn("already exists", stderr)

        code, _, stderr = self.invoke("create", "Not Valid", "--no-edit")
        self.assertEqual(2, code)
        self.assertIn("lowercase", stderr)

    def test_create_refuses_an_orphaned_nested_project(self) -> None:
        code, _, stderr = self.invoke("create", "ghost/child", "--no-edit")

        self.assertEqual(66, code)
        self.assertIn("project not found: ghost", stderr)
        self.assertFalse((self.projects / "ghost").exists())

    def test_status_changes_only_the_status_scalar(self) -> None:
        path = self.plan(
            "alpha",
            "draft",
            extra="owner: team\ncustom: keep-me\n",
            body="Uncommitted body edit.\n",
        )
        before = path.read_text()

        code, stdout, stderr = self.invoke("status", "alpha", "ready", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual("updated", json.loads(stdout)["action"])
        self.assertEqual(before.replace("status: draft", "status: ready"), path.read_text())

    def test_priority_changes_only_the_priority_scalar(self) -> None:
        path = self.plan(
            "alpha",
            "draft",
            extra="owner: team\n",
            body="Uncommitted body edit.\n",
        )
        before = path.read_text()

        code, stdout, stderr = self.invoke("priority", "alpha", "next", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual("updated", json.loads(stdout)["action"])
        self.assertEqual(
            before.replace("priority: later", "priority: next"), path.read_text()
        )

    def test_priority_inserts_a_line_when_a_completed_project_has_none(self) -> None:
        path = self.plan("alpha", "completed", priority=None)
        before = path.read_text()

        code, stdout, stderr = self.invoke("priority", "alpha", "next", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual("updated", json.loads(stdout)["action"])
        self.assertEqual(
            before.replace(
                "status: completed\n", "status: completed\npriority: next\n"
            ),
            path.read_text(),
        )

    def test_status_off_completed_requires_a_priority_first(self) -> None:
        path = self.plan("alpha", "completed", priority=None)
        before = path.read_text()

        code, _, stderr = self.invoke("status", "alpha", "in-progress")

        self.assertEqual(65, code)
        self.assertIn("run project priority alpha", stderr)
        self.assertEqual(before, path.read_text())

        code, _, stderr = self.invoke("check")
        self.assertEqual(0, code, stderr)

        code, _, stderr = self.invoke("priority", "alpha", "now")
        self.assertEqual(0, code, stderr)
        code, _, stderr = self.invoke("status", "alpha", "in-progress")
        self.assertEqual(0, code, stderr)
        self.assertIn("status: in-progress", path.read_text())

    def test_status_preserves_comments_and_crlf_line_endings(self) -> None:
        path = self.plan("alpha", "draft")
        before = path.read_bytes().replace(
            b"status: draft\n", b"status: draft # keep for Q3\n"
        ).replace(b"\n", b"\r\n")
        path.write_bytes(before)

        code, _, stderr = self.invoke("status", "alpha", "ready")

        self.assertEqual(0, code, stderr)
        self.assertEqual(
            before.replace(b"status: draft", b"status: ready"), path.read_bytes()
        )

    def test_status_refuses_a_concurrent_body_edit(self) -> None:
        path = self.plan("alpha", "draft")
        original_atomic_write = ProjectStore._atomic_write

        def collide(target: Path, content: str, signature: tuple[int, int, int]) -> None:
            target.write_text(target.read_text() + "Concurrent edit.\n")
            original_atomic_write(target, content, signature)

        with mock.patch.object(ProjectStore, "_atomic_write", side_effect=collide):
            code, _, stderr = self.invoke("status", "alpha", "ready")

        self.assertEqual(65, code)
        self.assertIn("changed before", stderr)
        self.assertIn("Concurrent edit", path.read_text())

    def test_owner_adds_the_field_before_the_closing_marker(self) -> None:
        # An `owner:` line in the body is prose, not the field, and stays put.
        path = self.plan("alpha", body="Uncommitted body edit.\nowner: someone in prose\n")
        before = path.read_text()

        code, stdout, stderr = self.invoke("owner", "alpha", "aparsuri-poly", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual("updated", json.loads(stdout)["action"])
        self.assertEqual(
            before.replace("priority: later\n---", "priority: later\nowner: aparsuri-poly\n---", 1),
            path.read_text(),
        )
        self.assertEqual("aparsuri-poly", ProjectStore(self.root).resolve("alpha").owner)

    def test_owner_changes_only_the_owner_scalar(self) -> None:
        path = self.plan("alpha", extra="owner: team # the lead\ncustom: keep-me\n")
        before = path.read_bytes().replace(b"\n", b"\r\n")
        path.write_bytes(before)

        code, _, stderr = self.invoke("owner", "alpha", "Apar Suri")

        self.assertEqual(0, code, stderr)
        self.assertEqual(before.replace(b"owner: team #", b"owner: Apar Suri #"), path.read_bytes())

    def test_owner_clear_removes_only_the_owner_line(self) -> None:
        path = self.plan("alpha", extra="owner: team\ncustom: keep-me\n", body="owner: prose\n")
        before = path.read_text()

        code, stdout, stderr = self.invoke("owner", "alpha", "--clear", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual("updated", json.loads(stdout)["action"])
        self.assertEqual(before.replace("owner: team\n", "", 1), path.read_text())
        self.assertIsNone(ProjectStore(self.root).resolve("alpha").owner)

    def test_owner_reports_when_no_file_changed(self) -> None:
        path = self.plan("alpha", extra="owner: team\n")
        unowned = self.plan("beta")
        stamps = (path.stat().st_mtime_ns, unowned.stat().st_mtime_ns)

        code, stdout, stderr = self.invoke("owner", "alpha", "team", "--json")
        self.assertEqual(0, code, stderr)
        self.assertEqual("unchanged", json.loads(stdout)["action"])
        code, stdout, stderr = self.invoke("owner", "beta", "--clear", "--json")
        self.assertEqual(0, code, stderr)
        self.assertEqual("unchanged", json.loads(stdout)["action"])

        self.assertEqual(stamps, (path.stat().st_mtime_ns, unowned.stat().st_mtime_ns))

    def test_owner_sets_a_nested_project_named_in_any_case(self) -> None:
        parent = self.plan("alpha")
        child = self.plan("alpha/child")
        before = parent.read_text()

        code, _, stderr = self.invoke("owner", "Alpha/Child", "ben")

        self.assertEqual(0, code, stderr)
        self.assertIn("owner: ben\n---", child.read_text())
        self.assertEqual(before, parent.read_text())

    def test_owner_refuses_a_value_that_would_not_read_back(self) -> None:
        path = self.plan("alpha")
        before = path.read_text()

        for value, reason in (("@ben", "leading @"), ("", "empty"), ("a: b", "plain YAML"),
                              ("ben # lead", "plain YAML"), (" ben", "space"), ("a\nb", "one line")):
            code, _, stderr = self.invoke("owner", "alpha", value)
            self.assertEqual(2, code, value)
            self.assertIn(reason, stderr, value)
        self.assertEqual(before, path.read_text())

    def test_owner_requires_a_value_or_clear_and_not_both(self) -> None:
        self.plan("alpha")
        for arguments in (("owner", "alpha"), ("owner", "alpha", "ben", "--clear")):
            with self.assertRaises(SystemExit) as raised, redirect_stderr(StringIO()):
                main(list(arguments))
            self.assertEqual(2, raised.exception.code, arguments)

    def test_owner_refuses_an_ambiguous_project(self) -> None:
        first = self.plan("alpha").resolve()
        store = ProjectStore(self.root)
        second = store.projects_dir / "Alpha" / "readme.md"
        with mock.patch.object(store, "_entry_points", return_value=[first, second]):
            with self.assertRaises(AmbiguousProject):
                store.set_owner("alpha", "ben")
        self.assertNotIn("owner:", first.read_text())

    def test_list_selects_one_owner_ignoring_case(self) -> None:
        self.plan("alpha", extra="owner: Ben\n")
        self.plan("beta", extra="owner: apar\n")
        self.plan("gamma")

        code, stdout, stderr = self.invoke("list", "--owner", "ben", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual([("alpha", "Ben")], [(p["name"], p["owner"]) for p in json.loads(stdout)["projects"]])

    def test_an_empty_owner_means_no_owner(self) -> None:
        empty = {"alpha": "owner:\n", "beta": "owner: \"\"\n", "gamma": "owner: '  ' # unassigned\n"}
        for name, line in empty.items():
            self.plan(name, extra=line)
        self.plan("delta", extra="owner: Ben\n")

        code, stdout, stderr = self.invoke("check")
        self.assertEqual(0, code, stderr)
        self.assertEqual("Project plans are valid.\n", stdout)
        code, stdout, stderr = self.invoke("list", "--json")
        self.assertEqual(0, code, stderr)
        self.assertEqual(
            [("alpha", None), ("beta", None), ("delta", "Ben"), ("gamma", None)],
            [(p["name"], p["owner"]) for p in json.loads(stdout)["projects"]],
        )
        code, stdout, stderr = self.invoke("show", "alpha", "--json")
        self.assertEqual(0, code, stderr)
        self.assertIsNone(json.loads(stdout)["project"]["owner"])
        for wanted in ("ben", "''", '""'):
            code, stdout, stderr = self.invoke("list", "--owner", wanted, "--json")
            self.assertEqual(0, code, stderr)
            self.assertEqual(["delta"] if wanted == "ben" else [],
                             [p["name"] for p in json.loads(stdout)["projects"]], wanted)

    def test_owner_replaces_an_empty_owner_in_place(self) -> None:
        for line, expected in (("owner:\n", "owner: ben\n"), ("owner:   \n", "owner: ben\n"),
                               ('owner: ""\n', "owner: ben\n"), ("owner: # unassigned\n", "owner: ben # unassigned\n")):
            with self.subTest(line):
                path = self.plan("alpha", extra=line + "custom: keep-me\n")
                before = path.read_text()

                code, stdout, stderr = self.invoke("owner", "alpha", "ben", "--json")

                self.assertEqual(0, code, stderr)
                self.assertEqual("updated", json.loads(stdout)["action"])
                self.assertEqual(before.replace(line, expected, 1), path.read_text())
                self.assertEqual("ben", ProjectStore(self.root).resolve("alpha").owner)

    def test_owner_and_status_find_their_lines_in_a_crlf_plan(self) -> None:
        path = self.plan("alpha", extra="owner:\n")
        before = path.read_bytes().replace(b"\n", b"\r\n")
        path.write_bytes(before)

        for arguments in (("owner", "alpha", "ben"), ("status", "alpha", "ready")):
            code, _, stderr = self.invoke(*arguments)
            self.assertEqual(0, code, stderr)

        self.assertEqual(
            before.replace(b"status: draft\r\n", b"status: ready\r\n").replace(b"owner:\r\n", b"owner: ben\r\n"),
            path.read_bytes(),
        )

    def test_owner_clear_removes_an_empty_owner_line(self) -> None:
        path = self.plan("alpha", extra="owner:\ncustom: keep-me\n")
        before = path.read_text()

        code, stdout, stderr = self.invoke("owner", "alpha", "--clear", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual("updated", json.loads(stdout)["action"])
        self.assertEqual(before.replace("owner:\n", "", 1), path.read_text())

    def test_status_priority_and_done_change_a_plan_with_an_empty_owner(self) -> None:
        path = self.plan("alpha", extra="owner:\n")
        before = path.read_text()

        for arguments in (("status", "alpha", "in-progress"), ("priority", "alpha", "now"), ("done", "alpha")):
            code, _, stderr = self.invoke(*arguments)
            self.assertEqual(0, code, stderr)

        self.assertEqual(
            before.replace("status: draft\npriority: later\n", "status: completed\npriority: now\n", 1),
            path.read_text(),
        )

    def test_done_changes_status_and_reminds_about_the_outcome(self) -> None:
        path = self.plan("alpha", "in-progress", priority="now")
        code, _, stderr = self.invoke("done", "alpha")
        self.assertEqual(0, code)
        self.assertIn("status: completed", path.read_text())
        self.assertIn("shipped", stderr)

    def test_status_reports_when_no_file_changed(self) -> None:
        path = self.plan("alpha", "in-progress", priority="now")
        before = path.stat().st_mtime_ns

        code, stdout, stderr = self.invoke("status", "alpha", "in-progress", "--json")

        self.assertEqual(0, code, stderr)
        self.assertEqual("unchanged", json.loads(stdout)["action"])
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_edit_refuses_a_noninteractive_session(self) -> None:
        self.plan("alpha")
        with mock.patch("sys.stdin.isatty", return_value=False):
            code, _, stderr = self.invoke("edit", "alpha")
        self.assertEqual(69, code)
        self.assertIn("interactive terminal", stderr)

    ADOPTED = ("docs/projects/README.md", "AGENTS.md", "CLAUDE.md")

    def snapshot(self) -> dict[str, bytes]:
        return {name: (self.root / name).read_bytes() for name in self.ADOPTED} | {
            "settings": self.settings().read_bytes()
        }

    def test_init_adopts_an_empty_repository_and_is_idempotent(self) -> None:
        self.at_a_terminal()
        self.root, _ = copy_of(empty_repository)
        self.settings().unlink()

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "created docs/projects/README.md\ncreated AGENTS.md\ncreated CLAUDE.md\ncreated ~/.claude/settings.json\n", stdout
        )
        self.assertEqual("", stderr)
        convention = (self.root / "docs" / "projects" / "README.md").read_text()
        self.assertIn("lowercase `readme.md`", convention)
        self.assertIn("https://github.com/ninjudd/projector", convention)
        self.assertEqual(self.block() + "\n", (self.root / "AGENTS.md").read_text())
        claude = self.root / "CLAUDE.md"
        self.assertTrue(claude.is_symlink())
        self.assertEqual("AGENTS.md", os.readlink(claude))
        # One mode for all three: the README's 0644 under the umask, not the
        # owner-only mode a temporary file is born with.
        modes = {stat.S_IMODE((self.root / name).stat().st_mode) for name in self.ADOPTED}
        self.assertEqual(1, len(modes), modes)
        self.assertTrue(modes.pop() & stat.S_IRGRP)
        self.assertEqual(granted(), json.loads(self.settings().read_text()))
        self.assertFalse((self.root / ".claude").exists(), "the rule is the user's, not the repository's")

        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

        before = self.snapshot()
        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "unchanged docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n",
            stdout,
        )
        self.assertEqual(before, self.snapshot())

    def test_init_adds_a_missing_convention_file_to_an_existing_tree(self) -> None:
        self.plan("alpha")
        (self.projects / "README.md").unlink()

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "created docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout
        )
        self.assertTrue((self.projects / "alpha" / "readme.md").exists())

    def test_distinct_instruction_files_each_get_the_block_and_keep_their_bytes(self) -> None:
        agents = self.root / "AGENTS.md"
        claude = self.root / "CLAUDE.md"
        claude.unlink()
        agents.write_bytes(b"# House rules\r\n\r\nBe kind.  \r\n")
        claude.write_bytes(b"# Claude\n\nUse plan mode.")

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "unchanged docs/projects/README.md\nupdated AGENTS.md\nupdated CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout
        )
        written = agents.read_bytes()
        self.assertTrue(written.startswith(b"# House rules\r\n\r\nBe kind.  \r\n\r\n<!-- projector:begin"))
        self.assertIn(b"## Projector conventions\r\n", written)
        self.assertTrue(written.endswith(b"<!-- projector:end -->\r\n"))
        # Two genuinely distinct files each carry the block; an import would
        # have made Claude Code read all of AGENTS.md, not only Projector's part.
        self.assertEqual(
            b"# Claude\n\nUse plan mode.\n\n" + self.block().encode() + b"\n", claude.read_bytes()
        )
        self.assertFalse(claude.is_symlink())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

        claude.write_text(claude.read_text().replace("second person", "third person"))
        code, _, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertIn("warning: CLAUDE.md: ", stderr)
        self.assertIn("[instructions-edited]", stderr)
        self.assertNotIn("AGENTS.md:", stderr)
        code, stdout, _ = self.invoke("init")
        self.assertEqual(
            "unchanged docs/projects/README.md\nunchanged AGENTS.md\nupdated CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout
        )

    def test_a_claude_first_repository_links_agents_md_to_claude_md(self) -> None:
        agents = self.root / "AGENTS.md"
        claude = self.root / "CLAUDE.md"
        claude.unlink()
        agents.unlink()
        claude.write_text("# Claude rules\n")

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "unchanged docs/projects/README.md\ncreated AGENTS.md\nupdated CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout
        )
        self.assertTrue(agents.is_symlink())
        self.assertEqual("CLAUDE.md", os.readlink(agents))
        self.assertFalse(claude.is_symlink())
        self.assertEqual("# Claude rules\n\n" + self.block() + "\n", claude.read_text())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))
        code, stdout, _ = self.invoke("init")
        self.assertEqual(
            "unchanged docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout
        )

    def test_a_link_checked_out_as_a_plain_file_is_left_alone(self) -> None:
        agents = self.root / "AGENTS.md"
        claude = self.root / "CLAUDE.md"
        for plain, other in ((claude, agents), (agents, claude)):
            agents.unlink(missing_ok=True)
            claude.unlink(missing_ok=True)
            other.write_text("# Shared instructions\n\n" + self.block() + "\n")
            # What Git writes for a committed symlink when core.symlinks is
            # false: a regular file whose whole content is the link text.
            plain.write_bytes(other.name.encode())

            code, _, stderr = self.invoke("check")
            self.assertEqual(0, code)
            self.assertIn(f"warning: {plain.name}: is a symlink checked out as a plain file", stderr)
            self.assertIn("[instructions-unlinked]", stderr)
            self.assertIn("core.symlinks=true", stderr)
            self.assertNotIn("instructions-missing", stderr)

            code, stdout, stderr = self.invoke("init")
            self.assertEqual(0, code)
            self.assertIn(f"kept {plain.name}\n", stdout)
            self.assertIn(f"unchanged {other.name}\n", stdout)
            self.assertIn("core.symlinks=true", stderr)
            self.assertEqual(other.name.encode(), plain.read_bytes())

    def test_init_restores_an_edited_block_that_check_warned_about(self) -> None:
        agents = self.root / "AGENTS.md"
        agents.write_text(agents.read_text().replace("second person", "third person"))

        code, stdout, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertEqual("Project plans are valid.\n", stdout)
        self.assertIn("warning: AGENTS.md: ", stderr)
        self.assertIn("[instructions-edited]", stderr)
        self.assertIn("project init", stderr)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "unchanged docs/projects/README.md\nupdated AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout
        )
        self.assertEqual(self.block() + "\n", agents.read_text())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

    def xcode_project(self, path: str = "App/App.xcodeproj", settings: str = "SDKROOT = iphoneos;") -> Path:
        project = self.root / path
        project.mkdir(parents=True)
        (project / "project.pbxproj").write_text(f"// !$*UTF8*$!\n{{\n\t{settings}\n}}\n", encoding="utf-8")
        return project

    def test_an_xcode_project_brings_the_ios_rules_into_the_block_and_its_removal_takes_them_out(self) -> None:
        agents = self.root / "AGENTS.md"
        project = self.xcode_project()

        # Untracked counts, so a new app is caught before its first commit.
        code, stdout, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertEqual("Project plans are valid.\n", stdout)
        self.assertIn("warning: AGENTS.md: Projector section leaves out the iOS rules this repository needs", stderr)
        self.assertIn("[instructions-ios]", stderr)
        self.assertNotIn("instructions-edited", stderr)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("updated AGENTS.md\n", stdout)
        self.assertEqual(instructions.render("docs/projects", ios=True) + "\n", agents.read_text())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

        subprocess.run(["git", "-C", str(self.root), "add", str(project)], check=True)
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))
        subprocess.run(["git", "-C", str(self.root), "rm", "-rq", "--cached", str(project)], check=True)
        shutil.rmtree(project)

        code, _, stderr = self.invoke("check")
        self.assertIn("Projector section carries iOS rules this repository does not need", stderr)
        self.assertIn("[instructions-ios]", stderr)
        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("updated AGENTS.md\n", stdout)
        self.assertEqual(self.block() + "\n", agents.read_text())

    def test_an_ignored_xcode_project_brings_no_ios_rules(self) -> None:
        (self.root / ".gitignore").write_text("vendor/\n", encoding="utf-8")
        self.xcode_project("vendor/Pods/Pods.xcodeproj")
        self.xcode_project("vendor/App.xcworkspace")
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

    def test_only_a_project_that_builds_for_ios_brings_the_ios_rules(self) -> None:
        # `project ios` builds only with the iOS SDK, so a Mac-only app gets no
        # rules telling its agents to use it, and neither does a workspace,
        # which names no platform of its own.
        self.xcode_project("Mac/Mac.xcodeproj", "SDKROOT = macosx;")
        self.xcode_project("Sim/Sim.xcodeproj", "SDKROOT = iphonesimulator;")
        workspace = self.root / "App.xcworkspace"
        workspace.mkdir()
        (workspace / "contents.xcworkspacedata").write_text("<Workspace/>\n", encoding="utf-8")
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

        # A multiplatform target lists the iOS SDK among its platforms.
        self.xcode_project("Both/Both.xcodeproj",
                           'SDKROOT = auto;\n\tSUPPORTED_PLATFORMS = "iphoneos iphonesimulator macosx";')
        _, _, stderr = self.invoke("check")
        self.assertIn("leaves out the iOS rules", stderr)
        self.assertIn("[instructions-ios]", stderr)

    def test_instructions_ios_overrides_what_the_repository_holds(self) -> None:
        config = self.root / ".projector.toml"
        agents = self.root / "AGENTS.md"
        self.xcode_project()
        config.write_text("[instructions]\nios = false\n", encoding="utf-8")
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

        shutil.rmtree(self.root / "App")
        config.write_text("[instructions]\nios = true\n", encoding="utf-8")
        _, _, stderr = self.invoke("check")
        self.assertIn("leaves out the iOS rules", stderr)
        code, _, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertEqual(instructions.render("docs/projects", ios=True) + "\n", agents.read_text())

        # A configured `ios.project` says the repository builds an app with
        # `project ios`, wherever its Xcode project lives.
        config.write_text('[ios]\nproject = "ios/App.xcodeproj"\n', encoding="utf-8")
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

    def test_init_keeps_a_newer_block_and_check_says_to_upgrade(self) -> None:
        agents = self.root / "AGENTS.md"
        shipped = instructions.template_version()
        agents.write_text(
            agents.read_text().replace(f"projector:begin {shipped}", f"projector:begin {shipped + 98}")
        )
        before = agents.read_bytes()

        code, _, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertIn("[instructions-ahead]", stderr)
        self.assertIn("project upgrade", stderr)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code)
        self.assertEqual(
            "unchanged docs/projects/README.md\nkept AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout
        )
        self.assertIn(f"version {shipped + 98}", stderr)
        self.assertEqual(before, agents.read_bytes())

    def test_init_refreshes_an_outdated_block(self) -> None:
        agents = self.root / "AGENTS.md"
        shipped = instructions.template_version()
        agents.write_text(
            agents.read_text()
            .replace(f"projector:begin {shipped}", "projector:begin 0")
            .replace("second person", "an older register")
        )

        code, _, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertIn("[instructions-outdated]", stderr)
        self.assertIn("project init", stderr)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("updated AGENTS.md\n", stdout)
        self.assertEqual(self.block() + "\n", agents.read_text())

    def test_init_reports_malformed_markers_after_writing_the_other_files(self) -> None:
        agents = self.root / "AGENTS.md"
        broken = agents.read_text().replace("<!-- projector:end -->", "")
        agents.write_text(broken)
        (self.root / "CLAUDE.md").unlink()
        (self.projects / "README.md").unlink()

        code, _, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertIn("[instructions-malformed]", stderr)
        self.assertIn("warning: CLAUDE.md: is absent", stderr)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(65, code)
        self.assertEqual("created docs/projects/README.md\nkept AGENTS.md\ncreated CLAUDE.md\n", stdout)
        self.assertIn("markers are malformed", stderr)
        self.assertEqual(broken, agents.read_text())
        self.assertTrue((self.root / "CLAUDE.md").is_symlink())

    def test_check_recognizes_an_import_the_way_claude_code_does(self) -> None:
        claude = self.root / "CLAUDE.md"
        claude.unlink()

        code, stdout, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertEqual("Project plans are valid.\n", stdout)
        self.assertIn("warning: CLAUDE.md: is absent", stderr)
        self.assertIn("[instructions-missing]", stderr)

        # Which texts are imports is ImportTests' to check; here, that check and init both ask the same question.
        text = "Read @AGENTS.md before making changes.\n"
        claude.write_text(text)
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))
        code, stdout, _ = self.invoke("init")
        self.assertIn("unchanged CLAUDE.md\n", stdout)
        self.assertEqual(text, claude.read_text())

        claude.write_text("```\n@AGENTS.md\n```\n")
        _, _, stderr = self.invoke("check")
        self.assertIn("warning: CLAUDE.md: has no Projector section", stderr)

        code, stdout, _ = self.invoke("init")
        self.assertIn("updated CLAUDE.md\n", stdout)
        self.assertEqual("```\n@AGENTS.md\n```\n\n" + self.block() + "\n", claude.read_text())

    def test_a_symlink_in_either_direction_gets_one_block_and_no_import(self) -> None:
        agents = self.root / "AGENTS.md"
        claude = self.root / "CLAUDE.md"
        for link, target in ((claude, agents), (agents, claude)):
            agents.unlink(missing_ok=True)
            claude.unlink(missing_ok=True)
            target.write_text("# Shared instructions\n")
            link.symlink_to(target.name)

            code, stdout, stderr = self.invoke("init")

            self.assertEqual(0, code, stderr)
            self.assertEqual(
                "unchanged docs/projects/README.md\nupdated AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n",
                stdout,
                link.name,
            )
            self.assertTrue(link.is_symlink(), link.name)
            self.assertTrue(target.is_file() and not target.is_symlink(), target.name)
            text = target.read_text()
            self.assertEqual("# Shared instructions\n\n" + self.block() + "\n", text)
            self.assertNotIn("@AGENTS.md", text)
            self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))
            code, stdout, _ = self.invoke("init")
            self.assertEqual(
                "unchanged docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n",
                stdout,
            )

    def test_a_dangling_claude_md_link_is_written_through(self) -> None:
        claude = self.root / "CLAUDE.md"
        claude.unlink()
        # The target's directory does not exist either; init creates it.
        claude.symlink_to("notes/claude.md")

        code, _, stderr = self.invoke("check")
        self.assertEqual(0, code)
        self.assertIn("warning: CLAUDE.md: is a dangling link to notes/claude.md", stderr)
        self.assertIn("[instructions-missing]", stderr)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("created CLAUDE.md\n", stdout)
        self.assertTrue(claude.is_symlink())
        self.assertEqual(self.block() + "\n", (self.root / "notes" / "claude.md").read_text())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

    def test_a_link_leaving_the_repository_is_never_written(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            shared = Path(elsewhere) / "shared.md"
            shared.write_text("# Personal instructions\n")
            for name, other in (("CLAUDE.md", "AGENTS.md"), ("AGENTS.md", "CLAUDE.md")):
                (self.root / "AGENTS.md").unlink(missing_ok=True)
                (self.root / "CLAUDE.md").unlink(missing_ok=True)
                (self.root / name).symlink_to(shared)

                code, stdout, stderr = self.invoke("init")
                self.assertEqual(0, code)
                self.assertIn(f"kept {name}\n", stdout)
                self.assertIn(f"created {other}\n", stdout)
                self.assertIn("leaves the repository", stderr)
                self.assertEqual("# Personal instructions\n", shared.read_text())

                code, _, stderr = self.invoke("check")
                self.assertEqual(0, code)
                self.assertIn(f"warning: {name}: resolves outside the repository", stderr)
                self.assertIn("[instructions-external]", stderr)
                self.assertNotIn("instructions-missing", stderr)

    def test_the_block_names_a_configured_projects_dir(self) -> None:
        (self.root / ".projector.toml").write_text('[projects]\ndir = "plans"\n')
        (self.root / "AGENTS.md").unlink()

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertEqual("created plans/README.md\ncreated AGENTS.md\nunchanged CLAUDE.md\nunchanged ~/.claude/settings.json\n", stdout)
        self.assertIn("`plans/README.md`", (self.root / "AGENTS.md").read_text())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

    def test_instructions_can_be_disabled_in_configuration(self) -> None:
        (self.root / ".projector.toml").write_text("[instructions]\nenabled = false\n")
        (self.root / "AGENTS.md").unlink()
        (self.root / "CLAUDE.md").unlink()

        self.assertEqual((0, "unchanged docs/projects/README.md\nunchanged ~/.claude/settings.json\n", ""),
                         self.invoke("init"))
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

        (self.root / ".projector.toml").write_text('[instructions]\nenabled = "no"\n')
        code, _, stderr = self.invoke("check")
        self.assertEqual(78, code)
        self.assertIn("instructions.enabled must be true or false", stderr)

    def test_check_json_carries_severity_and_warnings_do_not_fail(self) -> None:
        (self.root / "CLAUDE.md").unlink()

        code, stdout, _ = self.invoke("check", "--json")
        payload = json.loads(stdout)
        self.assertEqual(0, code)
        self.assertTrue(payload["valid"])
        self.assertEqual(
            [("instructions-missing", "CLAUDE.md", "warning")],
            [(issue["code"], issue["path"], issue["severity"]) for issue in payload["issues"]],
        )

        self.plan("bad", "waiting")
        code, stdout, _ = self.invoke("check", "--json")
        payload = json.loads(stdout)
        self.assertEqual(65, code)
        self.assertFalse(payload["valid"])
        self.assertEqual({"error", "warning"}, {issue["severity"] for issue in payload["issues"]})

    def test_init_json_keeps_action_and_path_and_adds_files(self) -> None:
        (self.root / "CLAUDE.md").unlink()

        code, stdout, stderr = self.invoke("init", "--json")
        payload = json.loads(stdout)

        self.assertEqual(0, code, stderr)
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual("unchanged", payload["action"])
        self.assertEqual("docs/projects/README.md", payload["path"])
        self.assertEqual(
            [
                {"path": "docs/projects/README.md", "action": "unchanged"},
                {"path": "AGENTS.md", "action": "unchanged"},
                {"path": "CLAUDE.md", "action": "created"},
                {"path": "~/.claude/settings.json", "action": "unchanged"},
            ],
            payload["files"],
        )

    def test_init_adds_the_rules_to_existing_settings_and_keeps_the_rest(self) -> None:
        self.at_a_terminal()
        existing = {"env": {"A": "1"},
                    "permissions": {"allow": ["Bash(npm test)"], "ask": ["Bash(rm *)"], "deny": ["Read(.env)"]}}
        self.settings().write_text(json.dumps(existing), encoding="utf-8")

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn("updated ~/.claude/settings.json\n", stdout)
        self.assertEqual({"env": {"A": "1"},
                          "permissions": {**granted("Bash(npm test)", ask=("Bash(rm *)",))["permissions"],
                                          "deny": ["Read(.env)"]}},
                         json.loads(self.settings().read_text()))
        before = self.settings().read_bytes()
        self.assertIn("unchanged ~/.claude/settings.json\n", self.invoke("init")[1])
        self.assertEqual(before, self.settings().read_bytes())

    def test_init_replaces_the_narrower_rules_projector_wrote_before(self) -> None:
        self.at_a_terminal()
        self.settings().write_text(json.dumps({"permissions": {
            "allow": ["Bash(npm test)", "Bash(project review publish *)", "Bash(project review hold *)",
                      "Bash(git status)", "Bash(project summary publish *)", "Bash(project review finish *)",
                      "Bash(project review settle *)", "Bash(project review publish *)"],
            "deny": ["Bash(project review hold *)"],
        }}), encoding="utf-8")

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn("updated ~/.claude/settings.json\n", stdout)
        self.assertEqual({"permissions": {**granted("Bash(npm test)", "Bash(git status)")["permissions"],
                                          "deny": ["Bash(project review hold *)"]}},
                         json.loads(self.settings().read_text()), "only allow rules Projector wrote are removed")
        before = self.settings().read_bytes()
        self.assertIn("unchanged ~/.claude/settings.json\n", self.invoke("init")[1])
        self.assertEqual(before, self.settings().read_bytes())

    def test_init_removes_the_ask_rule_for_an_option_before_the_command(self) -> None:
        self.at_a_terminal()
        self.settings().write_text(json.dumps({"permissions": {
            "allow": list(permissions.ALLOW),
            "ask": ["Bash(rm *)", *permissions.ASK, "Bash(project -*)"],
            "deny": ["Bash(project -*)"],
        }}), encoding="utf-8")

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn("updated ~/.claude/settings.json\n", stdout)
        self.assertEqual({"permissions": {**granted(ask=("Bash(rm *)",))["permissions"],
                                          "deny": ["Bash(project -*)"]}},
                         json.loads(self.settings().read_text()), "only the ask rule Projector wrote is removed")
        before = self.settings().read_bytes()
        self.assertIn("unchanged ~/.claude/settings.json\n", self.invoke("init")[1])
        self.assertEqual(before, self.settings().read_bytes())

    def test_init_removes_a_leftover_narrower_rule_only_when_it_writes(self) -> None:
        leftover = granted("Bash(project review hold *)", ask=("Bash(project -*)",))
        self.settings().write_text(json.dumps(leftover), encoding="utf-8")

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("unchanged ~/.claude/settings.json\n", stdout,
                      "a leftover rule allows nothing the broad one does not, so a report has nothing to say")
        self.assertEqual(leftover, json.loads(self.settings().read_text()))

        self.at_a_terminal()
        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("updated ~/.claude/settings.json\n", stdout)
        self.assertEqual(granted(), json.loads(self.settings().read_text()))

    def test_init_adds_the_ask_rules_beside_an_allow_rule_already_there(self) -> None:
        self.settings().write_text(json.dumps({"permissions": {"allow": list(permissions.ALLOW)}}), encoding="utf-8")

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("kept ~/.claude/settings.json\n", stdout)
        self.assertIn(", ".join(permissions.ASK), stderr)
        self.assertIn(f"an agent can run {permissions.ASKED} without asking you", stderr)
        self.assertIn("`project site` with `--prepare` or `--allow-prepare`", permissions.ASKED)

        self.at_a_terminal()
        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("updated ~/.claude/settings.json\n", stdout)
        self.assertEqual(granted(), json.loads(self.settings().read_text()))

    def test_init_keeps_settings_it_cannot_read_and_says_why(self) -> None:
        for text, reason in (
            ("{not json", "is not valid JSON"),
            ("[]", "does not hold a JSON object"),
            ('{"permissions": []}', "`permissions` that is not an object"),
            ('{"permissions": {"allow": "Bash(ls)"}}', "`permissions.allow` that is not a list"),
            ('{"permissions": {"ask": {}}}', "`permissions.ask` that is not a list"),
        ):
            with self.subTest(text=text):
                self.settings().write_text(text, encoding="utf-8")

                code, stdout, stderr = self.invoke("init")

                self.assertEqual(0, code, stderr)
                self.assertIn("kept ~/.claude/settings.json\n", stdout)
                self.assertIn(reason, stderr)
                self.assertIn(f"add {', '.join(permissions.ALLOW)} to permissions.allow and "
                              f"{', '.join(permissions.ASK)} to permissions.ask yourself", stderr)
                self.assertEqual(text, self.settings().read_text())

    def test_init_writes_through_a_settings_link_into_a_dotfiles_repository(self) -> None:
        dotfiles = tempfile.TemporaryDirectory()
        self.addCleanup(dotfiles.cleanup)
        shared = Path(dotfiles.name) / "claude" / "settings.json"
        shared.parent.mkdir()
        shared.write_text("{}\n", encoding="utf-8")
        self.settings().unlink()
        os.symlink(shared, self.settings())

        code, stdout, stderr = self.invoke("init", "--publish-rule")

        self.assertEqual(0, code, stderr)
        self.assertIn("updated ~/.claude/settings.json\n", stdout)
        self.assertTrue(self.settings().is_symlink())
        self.assertEqual(granted(), json.loads(shared.read_text()))

    @unittest.skipIf(getattr(os, "geteuid", lambda: -1)() == 0, "root writes a read-only directory anyway")
    def test_init_keeps_settings_it_cannot_write_and_reports_the_rest(self) -> None:
        # A settings file linked into a read-only directory, as home-manager
        # links it into the Nix store.
        store = tempfile.TemporaryDirectory()
        self.addCleanup(store.cleanup)
        shared = Path(store.name) / "claude" / "settings.json"
        shared.parent.mkdir()
        shared.write_text("{}\n", encoding="utf-8")
        shared.parent.chmod(0o555)
        self.addCleanup(shared.parent.chmod, 0o755)
        self.settings().unlink()
        os.symlink(shared, self.settings())
        (self.root / "CLAUDE.md").unlink()

        code, stdout, stderr = self.invoke("init", "--publish-rule")

        self.assertEqual(0, code, stderr)
        self.assertIn("created CLAUDE.md\n", stdout, "the files init did write are still reported")
        self.assertIn("kept ~/.claude/settings.json\n", stdout)
        self.assertIn("could not be written", stderr)
        self.assertTrue(all(rule in stderr for rule in permissions.ALLOW + permissions.ASK), stderr)
        self.assertEqual("{}\n", shared.read_text())

    def test_init_adds_the_publish_rule_where_claude_config_dir_points(self) -> None:
        self.at_a_terminal()
        configured = tempfile.TemporaryDirectory()
        self.addCleanup(configured.cleanup)
        before = self.settings().read_bytes()

        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": configured.name}):
            code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn(f"created {Path(configured.name) / 'settings.json'}\n", stdout)
        self.assertEqual(granted(), json.loads((Path(configured.name) / "settings.json").read_text()))
        self.assertEqual(before, self.settings().read_bytes())

    def test_init_leaves_the_repositorys_own_claude_settings_alone(self) -> None:
        self.at_a_terminal()
        self.settings().unlink()
        repository = self.root / ".claude" / "settings.json"
        repository.parent.mkdir()
        for text in ('{"permissions": {"allow": ["Bash(npm test)"]}}\n',
                     json.dumps(granted()) + "\n"):
            with self.subTest(text=text):
                repository.write_text(text, encoding="utf-8")

                code, stdout, stderr = self.invoke("init")

                self.assertEqual(0, code, stderr)
                self.assertEqual(text, repository.read_text())
                self.assertNotIn("\nunchanged .claude/settings.json", "\n" + stdout)
                self.assertTrue(self.settings().exists(), "the user's settings get the rule either way")

    def test_init_adds_the_publish_rule_only_at_a_terminal_or_when_asked(self) -> None:
        self.settings().unlink()

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("kept ~/.claude/settings.json\n", stdout)
        self.assertIn("run `project init` yourself in a terminal, or pass --publish-rule", stderr)
        self.assertFalse(self.settings().exists())

        (self.root / ".projector.toml").write_text("[review]\npublish_rule = true\n")
        self.assertIn("kept ~/.claude/settings.json\n", self.invoke("init")[1])
        self.assertFalse(self.settings().exists(), "configuration cannot add the rule without a terminal")

        (self.root / ".projector.toml").write_text("[review]\npublish_rule = false\n")
        code, stdout, stderr = self.invoke("init", "--publish-rule")
        self.assertEqual(0, code, stderr)
        self.assertIn("created ~/.claude/settings.json\n", stdout)
        self.assertEqual(granted(), json.loads(self.settings().read_text()))

        self.settings().unlink()
        self.at_a_terminal()
        self.assertNotIn(".claude/settings.json", self.invoke("init", "--no-publish-rule")[1])
        (self.root / ".projector.toml").unlink()
        self.assertIn("created ~/.claude/settings.json\n", self.invoke("init")[1])

    def test_the_publish_rule_can_be_disabled_in_configuration(self) -> None:
        self.at_a_terminal()
        self.settings().unlink()
        (self.root / ".projector.toml").write_text("[review]\npublish_rule = false\n")

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertNotIn(".claude/settings.json", stdout)
        self.assertFalse(self.settings().exists())

        (self.root / ".projector.toml").write_text('[review]\npublish_rule = "no"\n')
        (self.root / "AGENTS.md").unlink()
        code, _, stderr = self.invoke("init")
        self.assertEqual(78, code)
        self.assertIn("review.publish_rule must be true or false", stderr)
        self.assertFalse((self.root / "AGENTS.md").exists(), "a bad value refuses before any file is written")

    def codex_rules(self) -> Path:
        """Projector's rules file in the user's Codex configuration."""
        return self.home / ".codex" / "rules" / "projector.rules"

    def test_init_writes_the_codex_rules_where_codex_is_installed(self) -> None:
        self.at_a_terminal()
        (self.home / ".codex").mkdir()

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn("created ~/.codex/rules/projector.rules\n", stdout)
        self.assertEqual(permissions.CODEX_RULES, self.codex_rules().read_text())
        self.assertIn("unchanged ~/.codex/rules/projector.rules\n", self.invoke("init")[1])

        self.codex_rules().write_text("# edited\n")
        self.assertIn("updated ~/.codex/rules/projector.rules\n", self.invoke("init")[1])
        self.assertEqual(permissions.CODEX_RULES, self.codex_rules().read_text())

    def test_init_leaves_host_executable_out_for_a_codex_too_old_to_parse_it(self) -> None:
        # A Codex before 0.107 that meets `host_executable` drops its whole policy, the person's own rules
        # included, and before 0.108 it resolves no program path, so the line closes no hole there.
        self.at_a_terminal()
        (self.home / ".codex").mkdir()
        self.codex.return_value = (0, 106, 0)

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn("created ~/.codex/rules/projector.rules\n", stdout)
        written = self.codex_rules().read_text()
        self.assertEqual(permissions.CODEX_RULES_WITHOUT_HOST_EXECUTABLE, written)
        self.assertNotIn("host_executable", written)
        self.assertTrue(permissions.CODEX_RULES.startswith(written), "only the line and its comment are left out")
        self.assertTrue(permissions.codex_allows(written, permissions.PUBLISH))
        self.assertIn("Codex 0.106.0 is older than 0.107", stderr)
        # A newer Codex that reads the same rules, such as the desktop app, resolves a path to its basename.
        self.assertIn("any Codex 0.108 or newer that reads it, such as the desktop app, runs a path to a program "
                      "named `project` outside its sandbox", stderr)
        self.assertIn("update or remove the old `codex` and run `project init` again", stderr)
        self.assertIn("unchanged ~/.codex/rules/projector.rules\n", self.invoke("init")[1])

        for version in ((0, 107, 0), (0, 169, 0), None):
            with self.subTest(version=version):
                self.codex.return_value = version
                code, stdout, stderr = self.invoke("init")
                self.assertEqual(0, code, stderr)
                self.assertEqual(permissions.CODEX_RULES, self.codex_rules().read_text(),
                                 "a current Codex, or one whose version is unknown, gets the line")
                self.assertNotIn("older than", stderr)

    def test_init_leaves_codex_alone_where_codex_is_not_installed(self) -> None:
        self.at_a_terminal()

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertNotIn(".codex", stdout + stderr)
        self.assertFalse((self.home / ".codex").exists(), "init creates no Codex directory")

    def test_init_writes_the_codex_rules_where_codex_home_points(self) -> None:
        self.at_a_terminal()
        configured = tempfile.TemporaryDirectory()
        self.addCleanup(configured.cleanup)

        with mock.patch.dict(os.environ, {"CODEX_HOME": configured.name}):
            code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        rules = Path(configured.name) / "rules" / "projector.rules"
        self.assertIn(f"created {rules}\n", stdout)
        self.assertEqual(permissions.CODEX_RULES, rules.read_text())

    def test_init_only_reports_missing_codex_rules_without_a_terminal(self) -> None:
        (self.home / ".codex").mkdir()

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn("kept ~/.codex/rules/projector.rules\n", stdout)
        self.assertIn("run `project init` yourself in a terminal, or pass --publish-rule", stderr)
        self.assertFalse(self.codex_rules().exists())

    def test_init_outside_a_repository_sets_up_only_the_permission_rules(self) -> None:
        self.at_a_terminal()
        self.settings().unlink()
        (self.home / ".codex").mkdir()

        code, stdout, stderr = self.invoke("init", cwd=self.home)

        self.assertEqual(0, code, stderr)
        self.assertEqual("created ~/.claude/settings.json\ncreated ~/.codex/rules/projector.rules\n", stdout)
        self.assertIn("not inside a Git repository, so init set up only the permission rules", stderr)
        self.assertEqual(granted(), json.loads(self.settings().read_text()))
        self.assertFalse((self.home / "docs").exists(), "init adopts no convention outside a repository")

        code, stdout, _ = self.invoke("init", "--json", cwd=self.home)
        self.assertEqual(0, code)
        payload = json.loads(stdout)
        self.assertNotIn("action", payload, "no projects README to describe outside a repository")
        self.assertEqual([{"path": "~/.claude/settings.json", "action": "unchanged"},
                          {"path": "~/.codex/rules/projector.rules", "action": "unchanged"}], payload["files"])

    def test_init_outside_a_repository_says_it_only_reported_rules_it_did_not_add(self) -> None:
        self.settings().unlink()
        (self.home / ".codex").mkdir()

        code, stdout, stderr = self.invoke("init", cwd=self.home)

        self.assertEqual(0, code, stderr)
        self.assertEqual("kept ~/.claude/settings.json\nkept ~/.codex/rules/projector.rules\n", stdout)
        self.assertIn("so init added no permission rules and only reported what is missing", stderr)
        self.assertNotIn("set up", stderr)
        self.assertFalse(self.settings().exists())
        self.assertFalse(self.codex_rules().exists())

        self.at_a_terminal()
        self.settings().write_text("{not json", encoding="utf-8")
        code, stdout, stderr = self.invoke("init", cwd=self.home)
        self.assertEqual(0, code, stderr)
        self.assertIn("kept ~/.claude/settings.json\ncreated ~/.codex/rules/projector.rules\n", stdout)
        self.assertIn("so init set up some of the permission rules and reported the rest", stderr)

    def test_init_keeps_codex_rules_it_cannot_read_or_write_and_says_how_to_add_them(self) -> None:
        self.at_a_terminal()
        rules = self.codex_rules()
        rules.parent.parent.mkdir()

        def case(reason: str) -> None:
            code, stdout, stderr = self.invoke("init")
            self.assertEqual(0, code, stderr)
            self.assertIn("kept ~/.codex/rules/projector.rules\n", stdout)
            self.assertIn(reason, stderr)
            self.assertTrue(all(rule in stderr for rule in permissions.CODEX_PREFIX_RULES), stderr)

        with self.subTest("rules is a file"):
            rules.parent.write_text("not a directory\n")
            case("is not a directory")
            rules.parent.unlink()
        rules.parent.mkdir()
        with self.subTest("not UTF-8"):
            rules.write_bytes(b"# caf\xe9\n")
            case("is not UTF-8")
            self.assertEqual(b"# caf\xe9\n", rules.read_bytes())
            rules.unlink()
        if getattr(os, "geteuid", lambda: -1)() != 0:
            with self.subTest("unreadable"):
                rules.write_text("# mine\n")
                rules.chmod(0o000)
                self.addCleanup(lambda: rules.exists() and rules.chmod(0o644))
                case("could not be read")
                rules.chmod(0o644)
                rules.unlink()
            with self.subTest("unwritable directory"):
                rules.parent.chmod(0o555)
                self.addCleanup(rules.parent.chmod, 0o755)
                case("could not be written")
                rules.parent.chmod(0o755)
                self.assertFalse(rules.exists())

    def test_init_writes_codex_rules_through_a_link(self) -> None:
        self.at_a_terminal()
        dotfiles = tempfile.TemporaryDirectory()
        self.addCleanup(dotfiles.cleanup)
        shared = Path(dotfiles.name) / "projector.rules"
        shared.write_text("# old\n", encoding="utf-8")
        self.codex_rules().parent.mkdir(parents=True)
        os.symlink(shared, self.codex_rules())

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertIn("updated ~/.codex/rules/projector.rules\n", stdout)
        self.assertTrue(self.codex_rules().is_symlink())
        self.assertEqual(permissions.CODEX_RULES, shared.read_text())

    def test_init_outside_a_repository_refuses_to_set_up_a_site(self) -> None:
        code, _, stderr = self.invoke("init", "--site", cwd=self.home)

        self.assertEqual(2, code)
        self.assertIn("run `project init` inside that repository", stderr)

    def test_init_keeps_a_plain_file_claude_and_undecodable_settings(self) -> None:
        self.settings().write_bytes(b'{"env": {"NAME": "caf\xe9"}}')
        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("kept ~/.claude/settings.json\n", stdout)
        self.assertIn("is not UTF-8", stderr)

        self.settings().unlink()
        self.settings().parent.rmdir()
        self.settings().parent.write_text("not a directory\n", encoding="utf-8")
        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertIn("kept ~/.claude/settings.json\n", stdout)
        self.assertIn("is not a directory", stderr)


class RulesetGitHub:
    """The slice of GitHub `init`'s required check uses, for acme/app: its rules, rulesets, open pull requests,
    their verdicts and threads, and the statuses posted."""

    def __init__(self) -> None:
        self.rules: list[str] = []
        self.rulesets: list[dict] = []
        self.refuse = False
        # Each open pull request's head and the verdict bodies on it, and the pull requests with an open finding.
        self.pulls: dict[int, tuple[str, list[str]]] = {}
        self.open_findings: set[int] = set()
        self.statuses: list[dict] = []
        self.calls: list[list[str]] = []
        # A part of a call's arguments that makes it fail, as gh does offline or signed out.
        self.fail: str | None = None

    def __call__(self, args: list[str], token: str | None = None) -> str:
        self.calls.append(list(args))
        if self.fail and self.fail in " ".join(args):
            raise review.ReviewError(f"gh {' '.join(args)} failed: error connecting to api.github.com")
        if args == ["api", "repos/acme/app", "--jq", ".default_branch"]:
            return "main\n"
        if args[:3] == ["api", "--paginate", "repos/acme/app/rules/branches/main"]:
            return "".join(f"{context}\n" for context in self.rules)
        if args[:4] == ["api", "-X", "POST", "repos/acme/app/rulesets"]:
            if self.refuse:
                raise review.ReviewError("gh api -X POST repos/acme/app/rulesets failed: Not Found (HTTP 404)")
            created = json.loads(Path(args[5]).read_text())
            self.rulesets.append(created)
            for rule in created["rules"]:
                self.rules += [check["context"] for check in rule["parameters"]["required_status_checks"]]
            return "{}"
        if args[:3] == ["api", "--paginate", "repos/acme/app/pulls?state=open&per_page=100"]:
            return "".join(json.dumps({"number": number, "sha": sha, "visibility": "public"}) + "\n"
                           for number, (sha, _) in self.pulls.items())
        if args[:2] == ["api", "--paginate"] and args[2].endswith("/reviews"):
            number = int(args[2].split("/")[-2])
            return "".join(json.dumps({"id": 900 + i, "url": f"https://github.com/acme/app/pull/{number}#r{i}",
                                       "at": f"2026-11-01T00:0{i}:00Z", "login": "operator", "association": "OWNER",
                                       "body": body}) + "\n"
                           for i, body in enumerate(self.pulls[number][1]))
        if args[:2] == ["api", "graphql"]:
            number = int(next(arg for arg in args if arg.startswith("n="))[2:])
            nodes = [{"id": "T1", "isResolved": False, "path": "a.py", "line": 1,
                      "comments": {"nodes": [{"body": "<!-- projector-finding v=1 priority=P2 --> nil", "id": "C1",
                                              "databaseId": 1, "author": {"login": "operator"}}]}}] \
                if number in self.open_findings else []
            return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}})
        if args[:3] == ["api", "-X", "POST"] and args[3].startswith("repos/acme/app/statuses/"):
            fields = dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "-f")
            self.statuses.append(dict(fields, sha=args[3].rsplit("/", 1)[1]))
            return "{}"
        if args == ["api", "user", "--jq", ".login"]:
            return "operator\n"
        raise AssertionError(f"unexpected gh call: {args}")


def clean_verdict(sha: str, verdict: str = "clean") -> str:
    return (f"Projector review\n\n<!-- projector-review v=1 verdict={verdict} projector=1.2.3 model=claude-opus-5-5 "
            f"sha={sha} findings=0 seconds=60 covered=1/1 -->\n")


class RequiredCheckTests(RepositoryTestCase):
    """`project init`'s setup of the required review check in a repository whose origin is acme/app on GitHub."""

    def setUp(self) -> None:
        super().setUp()
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin", "https://github.com/acme/app.git"],
                       check=True)
        self.github = RulesetGitHub()
        self.enterContext(mock.patch.object(review, "run_gh", self.github))
        self.github.pulls = {7: ("a" * 40, [clean_verdict("a" * 40)]), 8: ("b" * 40, [clean_verdict("c" * 40)]),
                             9: ("d" * 40, [clean_verdict("d" * 40)])}
        self.github.open_findings = {9}

    def init(self, *extra: str, answer: str | None = None) -> tuple[int, str, str]:
        if answer is not None:
            self.at_a_terminal()
            self.enterContext(mock.patch("sys.stdin.readline", return_value=answer))
        return self.invoke("init", "--no-site", "--no-publish-rule", *extra)

    def workflow(self) -> Path:
        return self.root / review.CLEAN_WORKFLOW_PATH

    def recorded(self) -> object:
        path = self.root / ".projector.toml"
        return tomllib.loads(path.read_text()).get("review", {}).get("require_check") if path.exists() else None

    def test_a_yes_requires_the_check_writes_the_workflow_and_posts_heads_already_signed_off(self) -> None:
        code, out, err = self.init(answer="y\n")

        self.assertEqual(0, code, err)
        self.assertIn(cli.REQUIRE_QUESTION, err)
        self.assertEqual([review.ruleset()], self.github.rulesets)
        self.assertEqual([("a" * 40, "success")], [(s["sha"], s["state"]) for s in self.github.statuses],
                         "#8's clean verdict is on an earlier head, and #9 has an open finding")
        self.assertEqual(review.clean_workflow_text(), self.workflow().read_text())
        self.assertIn(f"created {review.CLEAN_WORKFLOW_PATH}", out)
        self.assertTrue(self.recorded())

        code, _, err = self.init(answer="n\n")

        self.assertEqual(0, code, err)
        self.assertNotIn(cli.REQUIRE_QUESTION, err, "the answer is recorded")
        self.assertEqual(1, len(self.github.rulesets))

    def test_the_ruleset_and_the_workflow_agree_with_what_publish_posts_and_labels(self) -> None:
        self.assertEqual(0, self.init("--require-review")[0])

        self.assertTrue(review.requires_check("acme/app", "main"), "publish posts its status where init requires it")
        text = self.workflow().read_text()
        self.assertIn(f"contains(github.event.pull_request.labels.*.name, '{review.CLEAN_LABEL}')", text)
        self.assertIn(f'select(ascii_downcase == "{review.CLEAN_LABEL}")', text)
        self.assertNotIn("{label}", text)
        self.assertNotIn("{context}", text)
        self.assertIn(f'select(.context == "{review.REVIEW_CONTEXT}")', text)

    def test_init_replaces_the_workflow_an_earlier_release_wrote_and_keeps_one_edited_by_hand(self) -> None:
        self.workflow().parent.mkdir(parents=True)
        self.workflow().write_text(EARLIER_CLEAN_WORKFLOW)
        self.assertTrue(review.is_earlier_clean_workflow(EARLIER_CLEAN_WORKFLOW))

        code, out, err = self.init("--require-review")

        self.assertEqual(0, code, err)
        self.assertEqual(review.clean_workflow_text(), self.workflow().read_text())
        self.assertIn(f"updated {review.CLEAN_WORKFLOW_PATH}", out)

        edited = EARLIER_CLEAN_WORKFLOW.replace("ubuntu-latest", "ubuntu-24.04")
        self.workflow().write_text(edited)

        code, out, err = self.init("--require-review")

        self.assertEqual(0, code, err)
        self.assertEqual(edited, self.workflow().read_text(), "a hand edit of an earlier workflow is the repository's")
        self.assertIn(f"kept {review.CLEAN_WORKFLOW_PATH}", out)
        # A kept copy never takes the template's fixes, so init says how to see them and adopt them.
        self.assertIn(f"`project review workflow | diff -u {review.CLEAN_WORKFLOW_PATH} -`", err)

        code, out, err = self.invoke("review", "workflow")

        self.assertEqual((0, ""), (code, err))
        self.assertEqual(review.clean_workflow_text(), out)

    def test_a_no_records_the_answer_and_changes_nothing_else(self) -> None:
        code, _, err = self.init(answer="\n")

        self.assertEqual(0, code, err)
        self.assertIs(False, self.recorded())
        self.assertEqual(([], []), (self.github.rulesets, self.github.statuses))
        self.assertFalse(self.workflow().exists())
        self.github.calls.clear()

        code, _, err = self.init(answer="y\n")

        self.assertEqual(0, code, err)
        self.assertNotIn(cli.REQUIRE_QUESTION, err)
        self.assertEqual([], self.github.calls, "a recorded no asks GitHub nothing")

    def test_without_a_terminal_or_a_flag_it_asks_and_changes_nothing(self) -> None:
        code, _, err = self.init()

        self.assertEqual(0, code, err)
        self.assertNotIn(cli.REQUIRE_QUESTION, err)
        self.assertEqual([], self.github.calls)
        self.assertIsNone(self.recorded())
        self.assertFalse(self.workflow().exists())

    def test_a_refused_ruleset_writes_no_workflow_status_or_setting(self) -> None:
        self.github.refuse = True

        code, _, err = self.init("--require-review")

        self.assertNotEqual(0, code)
        self.assertIn("could not create the Projector review ruleset on acme/app", err)
        self.assertIn("an admin of acme/app can run `project init --require-review`", err)
        self.assertEqual([], self.github.statuses)
        self.assertFalse(self.workflow().exists())
        self.assertIsNone(self.recorded())

    def test_with_the_labels_off_it_writes_no_workflow(self) -> None:
        (self.root / ".projector.toml").write_text("[review]\nlabels = false\n")

        code, _, err = self.init("--require-review")

        self.assertEqual(0, code, err)
        self.assertEqual(1, len(self.github.rulesets))
        self.assertEqual(1, len(self.github.statuses))
        self.assertFalse(self.workflow().exists(), "the workflow would take a label of the repository's own off")
        self.assertTrue(self.recorded())

    def test_a_branch_that_already_requires_the_check_gets_the_workflow_without_a_question(self) -> None:
        self.github.rules = [review.REVIEW_CONTEXT]

        code, _, err = self.init(answer="n\n")

        self.assertEqual(0, code, err)
        self.assertNotIn(cli.REQUIRE_QUESTION, err)
        self.assertEqual(([], []), (self.github.rulesets, self.github.statuses))
        self.assertTrue(self.workflow().exists())
        self.assertIsNone(self.recorded(), "nobody answered")

    def test_a_recorded_yes_puts_back_a_ruleset_that_is_gone(self) -> None:
        (self.root / ".projector.toml").write_text("[review]\nrequire_check = true\n")

        code, _, err = self.init()

        self.assertEqual(0, code, err)
        self.assertEqual(1, len(self.github.rulesets))
        self.assertTrue(self.workflow().exists())

    def test_a_github_it_cannot_read_skips_the_question_unless_the_check_was_asked_for(self) -> None:
        # Refreshing the instructions at a terminal must not fail where gh is missing, signed out, or offline.
        self.github.fail = "default_branch"

        code, _, err = self.init(answer="y\n")

        self.assertEqual(0, code, err)
        self.assertIn("did not ask whether acme/app requires a Projector review before merge", err)
        self.assertNotIn(cli.REQUIRE_QUESTION, err)
        self.assertIsNone(self.recorded())

        code, _, err = self.init("--require-review")

        self.assertNotEqual(0, code)
        self.assertIn("could not read the rules of acme/app's default branch", err)

    def test_a_failed_backfill_keeps_the_ruleset_the_workflow_and_the_setting(self) -> None:
        self.github.fail = "pulls?state=open"

        code, _, err = self.init("--require-review")

        self.assertEqual(0, code, err)
        self.assertIn("could not post projector/review on the open pull requests a review already signed off", err)
        self.assertIn("`project review setup <number> --rereview` and then `project review publish`", err,
                      "publish alone refuses a pull request with no review set up")
        self.assertEqual(1, len(self.github.rulesets))
        self.assertTrue(self.workflow().exists())
        self.assertTrue(self.recorded(), "a rerun finds the check required, so this run finishes the setup")

    def test_the_answer_flags_are_refused_outside_a_repository(self) -> None:
        outside = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for flag in ("--require-review", "--no-require-review"):
            with self.subTest(flag=flag):
                code, _, err = self.invoke("init", flag, cwd=outside)

                self.assertNotEqual(0, code)
                self.assertIn(f"{flag} answers for a repository", err)


# The clean workflow 0.8.14 wrote, which removed the label without reading the pull request.
EARLIER_CLEAN_WORKFLOW = """name: Projector clean label

# Written by `project init`. Removes the `clean` label when a commit is
# pushed, because the label says that Projector's newest review of the current
# head is clean, and a new head has no review yet. `pull_request_target` gives
# the token write access on a pull request from a fork; the job never checks
# out the pull request's code. The job-level `if` skips the job, so no runner
# starts, unless the pull request carries the label.
on:
  pull_request_target:
    types: [synchronize]

permissions:
  pull-requests: write

jobs:
  unclean:
    if: contains(github.event.pull_request.labels.*.name, 'clean')
    runs-on: ubuntu-latest
    steps:
      - run: gh pr edit "$PR" --repo "$REPO" --remove-label clean
        env:
          GH_TOKEN: ${{ github.token }}
          PR: ${{ github.event.pull_request.number }}
          REPO: ${{ github.repository }}
"""

# A stand-in for `gh` on the clean workflow's PATH. It answers `gh api <path> --jq <program>` from the JSON
# $ANSWERS holds for the path, through the real `jq`, as `gh` prints a `--jq` result. A path holds a list of
# answers, one for each read in turn, and the last answers every read after it. It fails on a path it has no
# answer for, and records every other command in $CALLS.
STUB_GH = """#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
if args[:1] == ["api"]:
    answers = json.loads(open(os.environ["ANSWERS"]).read())
    if args[1] not in answers:
        sys.exit(f"gh: no answer for {args[1]}")
    reads = os.environ["ANSWERS"] + ".reads"
    counts = json.loads(open(reads).read()) if os.path.exists(reads) else {}
    at = counts.get(args[1], 0)
    counts[args[1]] = at + 1
    open(reads, "w").write(json.dumps(counts))
    answer = answers[args[1]][min(at, len(answers[args[1]]) - 1)]
    sys.exit(subprocess.run(["jq", "-r", args[args.index("--jq") + 1]], input=json.dumps(answer),
                            text=True).returncode)
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps(args) + "\\n")
"""


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "the workflow's step runs in bash and reads with jq")
class CleanWorkflowStepTests(unittest.TestCase):
    """The clean workflow's step, run as GitHub runs it, against a stub `gh`."""

    HEAD = "b" * 40

    def script(self) -> str:
        lines = review.clean_workflow_text().splitlines()
        start, end = lines.index("      - run: |") + 1, lines.index("        env:")
        return "\n".join(line[10:] for line in lines[start:end]) + "\n"

    def run_step(self, labels: list[str], *reads: list[dict], heads: tuple[str, ...] = (HEAD,),
                 signed: tuple[str, ...] = ()) -> tuple[int, str, list[list[str]]]:
        """Run the step on a pull request carrying `labels`, whose head's statuses each read in turn gives as
        `reads`, the last for every read after it; with no `reads`, reading the status fails. Each read of the
        pull request gives the next of `heads` as its head, the last for every read after it, and each head in
        `signed` other than `HEAD` has a `success` status."""
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        gh = folder / "bin" / "gh"
        gh.parent.mkdir()
        gh.write_text(STUB_GH)
        gh.chmod(0o755)
        answers = {"repos/acme/app/pulls/7": [{"head": {"sha": sha}, "labels": [{"name": n} for n in labels]}
                                              for sha in heads]}
        if reads:
            answers[f"repos/acme/app/commits/{self.HEAD}/status?per_page=100"] = [{"statuses": r} for r in reads]
        for sha in heads:
            if sha != self.HEAD:
                statuses = [self.status("success")] if sha in signed else []
                answers[f"repos/acme/app/commits/{sha}/status?per_page=100"] = [{"statuses": statuses}]
        (folder / "answers.json").write_text(json.dumps(answers))
        env = {**os.environ, "PATH": f"{gh.parent}{os.pathsep}{os.environ['PATH']}", "PR": "7", "REPO": "acme/app",
               "ANSWERS": str(folder / "answers.json"), "CALLS": str(folder / "calls")}
        # GitHub runs a step's script with `bash -e`.
        done = subprocess.run(["bash", "-e", "-c", self.script()], env=env, capture_output=True, text=True)
        calls = folder / "calls"
        recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return done.returncode, done.stdout + done.stderr, recorded

    def status(self, state: str, context: str = review.REVIEW_CONTEXT) -> dict:
        return {"context": context, "state": state}

    def edit(self, change: str, name: str = review.CLEAN_LABEL) -> list[str]:
        return ["pr", "edit", "7", "--repo", "acme/app", change, name]

    def test_keeps_the_label_on_a_head_a_review_signed_off_since_the_push(self) -> None:
        code, out, calls = self.run_step(["Clean"], [self.status("success")])

        self.assertEqual((0, []), (code, calls), out)
        self.assertIn(f"kept Clean on #7, because a review signed off its head {self.HEAD[:7]}", out)

    def test_removes_the_label_from_a_head_no_review_signed_off(self) -> None:
        # Only a success signs a head off; a pending status stands for every other state.
        for name, statuses in [("no status", []), ("a pending status", [self.status("pending")]),
                               ("another check's success", [self.status("success", "ci/build")])]:
            with self.subTest(name):
                code, out, calls = self.run_step([review.CLEAN_LABEL, "bug"], statuses)

                self.assertEqual((0, [self.edit("--remove-label")]), (code, calls), out)

    def test_gives_the_label_back_to_a_head_a_review_signed_off_while_the_step_removed_it(self) -> None:
        # The label is spelled "Clean": GitHub matches label names without regard to case, and the step edits
        # the label by the name the pull request spells it rather than relying on `gh` to do the same.
        code, out, calls = self.run_step(["Clean"], [], [self.status("success")])

        self.assertEqual((0, [self.edit("--remove-label", "Clean"), self.edit("--add-label", "Clean")]),
                         (code, calls), out)
        self.assertIn(f"gave Clean back to #7, because a review signed off its head {self.HEAD[:7]}", out)

    def test_reads_the_status_of_the_head_as_it_is_after_the_removal(self) -> None:
        # A review signs the first head off during the step, and a push moved the head before the second read,
        # so the signed-off status belongs to a head that is no longer the pull request's.
        pushed = "c" * 40
        code, out, calls = self.run_step(["Clean"], [], [self.status("success")], heads=(self.HEAD, pushed))

        self.assertEqual((0, [self.edit("--remove-label", "Clean")]), (code, calls), out)

    def test_takes_the_label_off_again_when_a_push_lands_while_the_step_gives_it_back(self) -> None:
        # The push after the second read starts a job whose event lists no label, so that job skips.
        signed, pushed = "c" * 40, "d" * 40
        code, out, calls = self.run_step(["Clean"], [], heads=(self.HEAD, signed, pushed), signed=(signed,))

        self.assertEqual((0, [self.edit("--remove-label", "Clean"), self.edit("--add-label", "Clean"),
                              self.edit("--remove-label", "Clean")]), (code, calls), out)
        self.assertIn(f"took Clean off #7 again, because {pushed[:7]} was pushed while the step gave it back to "
                      f"{signed[:7]}", out)

    def test_does_nothing_once_the_label_is_gone(self) -> None:
        code, out, calls = self.run_step(["bug"])

        self.assertEqual((0, []), (code, calls), out)
        self.assertIn("no longer carries", out)

    def test_fails_without_removing_the_label_when_the_status_cannot_be_read(self) -> None:
        code, out, calls = self.run_step([review.CLEAN_LABEL])

        self.assertNotEqual(0, code, out)
        self.assertEqual([], calls)


class ReviewRulesTests(RepositoryTestCase):
    """`project review rules` reports Projector's permission rules and writes nothing, inside a repository or
    outside one."""

    def home_tree(self) -> dict[str, bytes | None]:
        """Every path under the temporary home, with each file's bytes, so a test sees any write at all."""
        return {path.relative_to(self.home).as_posix(): None if path.is_dir() else path.read_bytes()
                for path in sorted(self.home.rglob("*"))}

    def test_rules_present_exits_0_and_writes_nothing(self) -> None:
        before = self.home_tree()
        for cwd in (self.root, self.home):
            with self.subTest(cwd=cwd):
                code, stdout, stderr = self.invoke("review", "rules", cwd=cwd)

                self.assertEqual(0, code, stderr)
                self.assertEqual("allows ~/.claude/settings.json\n", stdout)
                self.assertEqual("", stderr)
                self.assertEqual(before, self.home_tree())
                self.assertFalse((self.home / ".codex").exists(), "the check creates no Codex directory")

        code, stdout, stderr = self.invoke("review", "rules", "--json")
        self.assertEqual(0, code, stderr)
        self.assertEqual({"allowed": True, "files": [{"path": "~/.claude/settings.json", "allowed": True,
                                                      "summary_publish": True}]},
                         json.loads(stdout))

        self.settings().write_text(json.dumps(granted("Bash(project review hold *)")))
        before = self.home_tree()
        self.assertEqual((0, "allows ~/.claude/settings.json\n", ""), self.invoke("review", "rules"),
                         "a narrower rule left beside the broad one takes nothing away")
        self.assertEqual(before, self.home_tree())

        self.settings().write_text(json.dumps(granted(ask=("Bash(project -*)",))))
        before = self.home_tree()
        self.assertEqual((0, "allows ~/.claude/settings.json\n", ""), self.invoke("review", "rules"),
                         "the ask rule for an option before the command asks only before what the CLI refuses")
        self.assertEqual(before, self.home_tree())

    def test_missing_rules_exit_1_and_ask_for_init_without_writing(self) -> None:
        def write(permissions: dict) -> None:
            self.settings().write_text(json.dumps({"permissions": permissions}))

        narrower = ["Bash(project review publish *)", "Bash(project review hold *)", "Bash(project summary publish *)"]
        cases = {
            "only the narrower rules": (lambda: write({"allow": narrower}), "Bash(project *)"),
            "ask rules missing": (lambda: write({"allow": list(permissions.ALLOW)}), "Bash(project init *)"),
            "one ask rule missing": (lambda: write(granted()["permissions"] | {"ask": list(permissions.ASK[:-1])}),
                                     permissions.ASK[-1]),
            "settings absent": (self.settings().unlink, "Bash(project *)"),
        }
        for name, (arrange, missing) in cases.items():
            with self.subTest(name):
                arrange()
                before = self.home_tree()

                code, stdout, stderr = self.invoke("review", "rules")

                self.assertEqual(1, code, stderr)
                self.assertEqual("lacks ~/.claude/settings.json\n", stdout)
                self.assertIn(missing, stderr)
                self.assertIn("ask the user to run `project init` in a terminal", stderr)
                self.assertIn("do not run `project init --publish-rule` to add them yourself", stderr)
                self.assertIn("unless the repository's own or managed settings allow them", stderr)
                self.assertNotIn("or pass --publish-rule", stderr, "the check names no command an agent could run")
                self.assertEqual(before, self.home_tree())
        self.assertFalse(self.settings().exists(), "a missing settings file stays missing")

        code, stdout, _ = self.invoke("review", "rules", "--json")
        self.assertEqual(1, code)
        self.assertEqual({"allowed": False, "files": [{"path": "~/.claude/settings.json", "allowed": False,
                                                       "summary_publish": False}]},
                         json.loads(stdout))
        self.assertFalse(self.settings().exists())

    def test_json_says_whether_a_file_lacking_newer_rules_still_allows_the_summary_publish(self) -> None:
        # A session whose publish was refused reads this to tell a missing rule, which `init` adds, from a
        # matching one, which running `init` again would not change.
        def publish(settings: object) -> bool:
            self.settings().write_text(json.dumps(settings))
            code, stdout, stderr = self.invoke("review", "rules", "--json")
            self.assertIn(code, (0, 1), stderr)
            return json.loads(stdout)["files"][0]["summary_publish"]

        self.assertTrue(publish({"permissions": {"allow": list(permissions.ALLOW)}}), "only ask rules missing")
        self.assertTrue(publish({"permissions": {"allow": ["Bash(project summary publish *)"]}}),
                        "the narrower rule Projector wrote before")
        self.assertFalse(publish({"permissions": {"allow": ["Bash(project review publish *)"]}}))
        self.assertFalse(publish({"permissions": {"allow": "Bash(project *)"}}), "an allow that is not a list")
        self.assertFalse(publish([]))
        self.settings().write_text("{not json")
        self.assertFalse(json.loads(self.invoke("review", "rules", "--json")[1])["files"][0]["summary_publish"])

    def test_a_codex_file_lacking_newer_rules_says_it_still_allows_the_summary_publish(self) -> None:
        rules = self.home / ".codex" / "rules" / "projector.rules"
        rules.parent.mkdir(parents=True)
        # The file an earlier release wrote, before `project review ready` and `workflow` were allowed.
        stale = permissions.CODEX_RULES.replace('"publish", "ready", ', '"publish", ').replace(', "workflow"]', ']')
        self.assertNotEqual(permissions.CODEX_RULES, stale)
        cases = {
            "stale": (stale, True),
            "one-line layout": ("\n".join(permissions.CODEX_PREFIX_RULES), True),
            "no summary rule": (stale.replace('"status", "summary",', '"status",'), False),
            "summary only prompts": (stale.replace('"status", "summary",', '"status",')
                                     + 'prefix_rule(pattern = ["project", "summary"], decision = "prompt")\n', False),
            "not Projector's layout": ('prefix_rule(pattern = ["project", "summary"]  ,decision="allow")\n', False),
            "empty": ("", False),
        }
        for name, (text, allowed) in cases.items():
            with self.subTest(name):
                rules.write_text(text)
                before = self.home_tree()

                code, stdout, stderr = self.invoke("review", "rules")
                json_code, json_stdout, _ = self.invoke("review", "rules", "--json")

                self.assertEqual((1, 1), (code, json_code), stderr)
                self.assertEqual("allows ~/.claude/settings.json\nlacks ~/.codex/rules/projector.rules\n", stdout)
                self.assertEqual(allowed, "it still allows `project summary publish`" in stderr, stderr)
                self.assertEqual(not allowed, "such as `project summary publish`" in stderr, stderr)
                self.assertEqual([True, allowed],
                                 [entry["summary_publish"] for entry in json.loads(json_stdout)["files"]])
                self.assertEqual(before, self.home_tree())

        rules.unlink()
        self.assertEqual([True, False], [entry["summary_publish"] for entry in
                                         json.loads(self.invoke("review", "rules", "--json")[1])["files"]],
                         "a missing file allows nothing")

    def test_a_codex_directory_without_projectors_rules_exits_1(self) -> None:
        (self.home / ".codex").mkdir()
        before = self.home_tree()

        code, stdout, stderr = self.invoke("review", "rules")

        self.assertEqual(1, code, stderr)
        self.assertEqual("allows ~/.claude/settings.json\nlacks ~/.codex/rules/projector.rules\n", stdout)
        self.assertIn("its approval reviewer refuse, a Projector command an agent runs", stderr)
        self.assertNotIn("or pass --publish-rule", stderr)
        self.assertEqual(before, self.home_tree())

        rules = self.home / ".codex" / "rules" / "projector.rules"
        rules.parent.mkdir()
        rules.write_text(permissions.CODEX_RULES)
        code, stdout, stderr = self.invoke("review", "rules")
        self.assertEqual(0, code, stderr)
        self.assertEqual("allows ~/.claude/settings.json\nallows ~/.codex/rules/projector.rules\n", stdout)

    def test_a_codex_too_old_for_the_rules_file_is_told_apart_from_a_missing_rule(self) -> None:
        rules = self.home / ".codex" / "rules" / "projector.rules"
        rules.parent.mkdir(parents=True)
        rules.write_text(permissions.CODEX_RULES)
        self.codex.return_value = (0, 100, 0)
        before = self.home_tree()

        code, stdout, stderr = self.invoke("review", "rules")

        self.assertEqual(1, code, stderr)
        self.assertEqual("allows ~/.claude/settings.json\nlacks ~/.codex/rules/projector.rules\n", stdout)
        self.assertIn("which Codex 0.100.0 cannot parse, so Codex loads no rules at all", stderr)
        self.assertNotIn("or pass --publish-rule", stderr, "the check names no command an agent could run")
        # Codex loads no rules, so none allows the publish, and `summarize-pr` must not say `init` changes nothing.
        self.assertEqual([True, False], [entry["summary_publish"] for entry in
                                         json.loads(self.invoke("review", "rules", "--json")[1])["files"]])
        self.assertEqual(before, self.home_tree())

        rules.write_text(permissions.CODEX_RULES_WITHOUT_HOST_EXECUTABLE)
        code, stdout, stderr = self.invoke("review", "rules")
        self.assertEqual(0, code, stderr)
        self.assertEqual("allows ~/.claude/settings.json\nallows ~/.codex/rules/projector.rules\n", stdout)
        self.assertIn("Codex 0.100.0 is older than 0.107", stderr)
        self.assertEqual([True, True], [entry["summary_publish"] for entry in
                                        json.loads(self.invoke("review", "rules", "--json")[1])["files"]])

    def test_unreadable_settings_exit_1_and_stay_untouched(self) -> None:
        for content, reason in (
            (b"{not json", "is not valid JSON"),
            (b'{"permissions": []}', "`permissions` that is not an object"),
            (b'{"env": {"NAME": "caf\xe9"}}', "is not UTF-8"),
        ):
            with self.subTest(reason=reason):
                self.settings().write_bytes(content)
                before = self.home_tree()

                code, stdout, stderr = self.invoke("review", "rules")

                self.assertEqual(1, code, stderr)
                self.assertEqual("lacks ~/.claude/settings.json\n", stdout)
                self.assertIn(reason, stderr)
                self.assertIn("do not run `project init --publish-rule`", stderr)
                self.assertEqual(content, self.settings().read_bytes())
                self.assertEqual(before, self.home_tree())

    def test_rules_takes_no_pull_request(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as stop:
            main(["review", "rules", "66"])
        self.assertEqual(2, stop.exception.code)


class ValidationTests(RepositoryTestCase):
    def test_check_accepts_a_valid_tree_and_local_links(self) -> None:
        self.plan("alpha", body="See [design](design.md) and [child](child/).")
        (self.projects / "alpha" / "design.md").write_text("# Design\n")
        self.plan("alpha/child")
        (self.root / "docs" / "architecture.md").write_text("# Architecture\n")
        with (self.projects / "alpha" / "readme.md").open("a") as plan:
            plan.write("\nSee [architecture](../../architecture.md).\n")

        code, stdout, stderr = self.invoke("check")

        self.assertEqual(0, code, stderr)
        self.assertEqual("Project plans are valid.\n", stdout)

    def test_check_rejects_a_multiline_owner_but_not_an_empty_one(self) -> None:
        self.plan("alpha", extra="owner:\n")
        self.plan("beta", extra="owner: |\n")
        self.plan("gamma", extra="owner: aparsuri-poly\n")

        code, stdout, _ = self.invoke("check", "--json")

        self.assertEqual(65, code)
        invalid = {issue["path"]: issue["message"] for issue in json.loads(stdout)["issues"]
                   if issue["code"] == "invalid-project"}
        self.assertEqual({"docs/projects/beta/readme.md"}, set(invalid))
        self.assertIn("owner must be a single line", invalid["docs/projects/beta/readme.md"])

    def test_check_accepts_markdown_images(self) -> None:
        self.plan("alpha", body="![Diagram](diagram.png)")
        (self.projects / "alpha" / "diagram.png").write_bytes(b"image")

        code, stdout, stderr = self.invoke("check")

        self.assertEqual(0, code, stderr)
        self.assertEqual("Project plans are valid.\n", stdout)

    def test_check_accepts_a_link_label_split_across_lines(self) -> None:
        self.plan("alpha", body="Read [the design\nnotes](design.md).")
        (self.projects / "alpha" / "design.md").write_text("# Design\n")

        code, stdout, stderr = self.invoke("check")

        self.assertEqual(0, code, stderr)
        self.assertEqual("Project plans are valid.\n", stdout)

    def test_check_reports_every_invalid_plan(self) -> None:
        self.plan("bad-status", "waiting")
        self.plan("bad-priority", priority="someday")
        self.plan("missing-priority", "ready", priority=None)
        malformed = self.projects / "malformed" / "readme.md"
        malformed.parent.mkdir()
        malformed.write_text("# Missing frontmatter\n")

        code, stdout, _ = self.invoke("check", "--json")
        payload = json.loads(stdout)

        self.assertEqual(65, code)
        self.assertFalse(payload["valid"])
        invalid = [issue for issue in payload["issues"] if issue["code"] == "invalid-project"]
        self.assertEqual(4, len(invalid))
        encoded = json.dumps(payload)
        self.assertNotIn(str(self.root), encoded)

    def test_completed_projects_do_not_require_priority(self) -> None:
        self.plan("finished", "completed", priority=None)

        code, stdout, stderr = self.invoke("check")

        self.assertEqual(0, code, stderr)
        self.assertEqual("Project plans are valid.\n", stdout)

    def test_check_reports_wrong_case_missing_plans_and_broken_links(self) -> None:
        uppercase = self.projects / "uppercase" / "README.md"
        uppercase.parent.mkdir()
        uppercase.write_text(
            PLAN.format(
                status="draft",
                priority=priority_line("later"),
                extra="",
                title="Upper",
                body="Done",
            )
        )
        self.plan("linked", body="See [missing](missing.md).")

        code, stdout, _ = self.invoke("check", "--json")
        issues = json.loads(stdout)["issues"]
        codes = {issue["code"] for issue in issues}

        self.assertEqual(65, code)
        self.assertIn("wrong-entry-case", codes)
        self.assertIn("missing-plan", codes)
        self.assertIn("broken-project-link", codes)

    def test_check_uses_the_casing_recorded_by_git(self) -> None:
        lowercase = self.plan("uppercase")
        blob = subprocess.run(
            ["git", "-C", str(self.root), "hash-object", "-w", str(lowercase)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "update-index",
                "--add",
                "--cacheinfo",
                "100644",
                blob,
                "docs/projects/Uppercase/readme.md",
            ],
            check=True,
        )

        code, stdout, _ = self.invoke("check", "--json")
        issues = json.loads(stdout)["issues"]

        self.assertEqual(65, code)
        self.assertTrue(
            any(
                issue["code"] == "wrong-project-case"
                and issue["path"] == "docs/projects/Uppercase/readme.md"
                for issue in issues
            )
        )

    def test_check_reports_case_collisions_recorded_only_by_git(self) -> None:
        lowercase = self.plan("alpha")
        blob = subprocess.run(
            ["git", "-C", str(self.root), "hash-object", "-w", str(lowercase)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        for path in (
            "docs/projects/alpha/readme.md",
            "docs/projects/Alpha/readme.md",
        ):
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    blob,
                    path,
                ],
                check=True,
            )

        code, stdout, _ = self.invoke("check", "--json")
        codes = {issue["code"] for issue in json.loads(stdout)["issues"]}

        self.assertEqual(65, code)
        self.assertIn("case-collision", codes)

    def test_check_reports_missing_directory_symlink_and_exact_link_case(self) -> None:
        (self.projects / "README.md").unlink()
        self.projects.rmdir()
        code, stdout, _ = self.invoke("check", "--json")
        self.assertEqual(65, code)
        self.assertEqual("missing-projects-dir", json.loads(stdout)["issues"][0]["code"])

        self.projects.mkdir()
        (self.projects / "README.md").write_text("# Projects\n")
        self.plan("alpha", body="See [design](DESIGN.md).")
        (self.projects / "alpha" / "design.md").write_text("# Design\n")
        (self.projects / "alpha" / "linked").symlink_to("design.md")

        code, stdout, _ = self.invoke("check", "--json")
        codes = {issue["code"] for issue in json.loads(stdout)["issues"]}
        self.assertEqual(65, code)
        self.assertIn("symlink", codes)
        self.assertIn("broken-project-link", codes)

    def test_invalid_encoding_has_a_diagnostic_without_a_traceback(self) -> None:
        path = self.projects / "encoded" / "readme.md"
        path.parent.mkdir()
        path.write_bytes(b"---\nstatus: draft\npriority: later\n---\n\n# Bad \xff\n")

        code, _, stderr = self.invoke("show", "encoded")

        self.assertEqual(65, code)
        self.assertIn("codec can't decode byte", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_missing_project_uses_the_documented_exit_code(self) -> None:
        code, _, stderr = self.invoke("show", "missing")
        self.assertEqual(66, code)
        self.assertIn("project not found", stderr)

    def test_ambiguous_project_uses_the_documented_exit_code(self) -> None:
        first = self.plan("alpha").resolve()
        store = ProjectStore(self.root)
        second = store.projects_dir / "Alpha" / "readme.md"
        with mock.patch.object(store, "_entry_points", return_value=[first, second]):
            with self.assertRaisesRegex(AmbiguousProject, "ambiguous project") as raised:
                store.resolve("alpha")
        self.assertEqual(67, raised.exception.exit_code)

    def test_check_reports_malformed_markdown_links(self) -> None:
        self.plan("alpha", body="Broken [link](design.md")
        code, stdout, _ = self.invoke("check", "--json")
        codes = {issue["code"] for issue in json.loads(stdout)["issues"]}
        self.assertEqual(65, code)
        self.assertIn("malformed-project-link", codes)


class StoreTests(RepositoryTestCase):
    def test_store_uses_git_root_from_deep_subdirectory(self) -> None:
        self.plan("alpha")
        deep = self.projects / "alpha" / "notes" / "deep"
        deep.mkdir(parents=True)
        store = ProjectStore(deep)
        self.assertEqual(self.root.resolve(), store.root)
        self.assertEqual("alpha", store.resolve("alpha").name)


    def test_project_from_text_loads_a_plan_exactly_as_the_store_does(self) -> None:
        plans = {
            "alpha": PLAN.format(status="ready", priority=priority_line("next"), title="Alpha", body="Text.",
                                 extra="owner: someone\n"),
            "empty-owner": "---\nstatus: ready\npriority: next\nowner:\n---\n",
            "no-frontmatter": "# No frontmatter\n",
            "malformed": "---\nstatus ready\n---\n",
            "unclosed": "---\nstatus: ready\n",
            "bad-status": "---\nstatus: maybe\npriority: next\n---\n",
            "no-priority": "---\nstatus: ready\n---\n",
            "bad-priority": "---\nstatus: ready\npriority: soon\n---\n",
            "block-owner": "---\nstatus: ready\npriority: next\nowner: |\n---\n",
            "Bad_Name": PLAN.format(status="draft", priority=priority_line("later"), title="Bad", body="Text.", extra=""),
        }
        store = ProjectStore(self.root, self.root)
        failures = 0
        for name, text in plans.items():
            with self.subTest(name):
                path = self.projects / name / "readme.md"
                path.parent.mkdir(parents=True)
                path.write_text(text, encoding="utf-8")
                display = Path(f"docs/projects/{name}/readme.md")
                try:
                    expected = store.resolve(name)
                except ProjectorError as error:
                    failures += 1
                    with self.assertRaises(ProjectorError) as raised:
                        project_from_text(text, name, store.projects_dir / name / "readme.md", display)
                    self.assertEqual(str(error), str(raised.exception))
                else:
                    self.assertEqual(expected, project_from_text(text, name, expected.path, display))
        self.assertEqual(len(plans) - 2, failures, "every plan but alpha and empty-owner is a kind of bad plan")


class SelfReportingTests(unittest.TestCase):
    """`install.sh status` asks the installed command about itself.

    A regression here does not look like a break. It looks like every install
    reporting `cli-stale installed version unknown`, which reads as the check
    working, so pin the action rather than trusting it.
    """

    def test_version_exits_zero_and_names_the_distribution(self) -> None:
        stdout = StringIO()

        with redirect_stdout(stdout):
            code = main(["version"])

        # What `--version` printed before the command came first, which
        # `install.sh` reads the version's last word from.
        self.assertEqual(0, code)
        self.assertEqual(f"project {distribution_version()}\n", stdout.getvalue())
        self.assertRegex(stdout.getvalue().strip(), r"^project \S+$")

    def test_package_dir_names_where_this_package_runs_from(self) -> None:
        stdout = StringIO()

        # A narrow width, so a path folded to the terminal's width would show.
        # `install.sh` reads this with `$(...)` and tests the result with
        # `-d`, so a folded path reports every install stale forever. Pinning
        # COLUMNS checks that at any width rather than at whatever depth this
        # checkout happens to sit.
        with mock.patch.dict(os.environ, {"COLUMNS": "20"}):
            with redirect_stdout(stdout):
                code = main(["version", "--package-dir"])

        self.assertEqual(0, code)
        printed = stdout.getvalue().splitlines()
        self.assertEqual(1, len(printed), printed)
        reported = Path(printed[0])
        self.assertEqual(Path(cli.__file__).resolve().parent, reported)
        self.assertTrue((reported / "cli.py").is_file())

    def test_help_prints_the_help_of_project_or_of_the_command_it_names(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main(["help"])

        self.assertEqual(0, code)
        self.assertEqual(cli.parser().format_help(), stdout.getvalue())
        self.assertRegex(stdout.getvalue(), r"\n +version +print the version of this command\n")
        self.assertNotIn("--", stdout.getvalue(), "project itself takes no option")

        for command in (["init"], ["review", "publish"]):
            with self.subTest(command=command):
                printed = []
                for argv in (["help", *command], [*command, "--help"]):
                    stdout = StringIO()
                    with redirect_stdout(stdout), self.assertRaises(SystemExit) as stop:
                        main(argv)
                    self.assertEqual(0, stop.exception.code)
                    printed.append(stdout.getvalue())
                self.assertEqual(printed[1], printed[0])
                self.assertTrue(printed[0].startswith(f"usage: project {' '.join(command)} "), printed[0])

        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            main(["help", "nothing"])
        self.assertEqual(2, stop.exception.code)
        self.assertIn("invalid choice: 'nothing'", stderr.getvalue())

    def test_the_queried_distribution_is_the_one_pyproject_installs(self) -> None:
        # A wrong name here is invisible: `version()` raises, the unknown
        # branch answers, and every install reports `cli-stale installed
        # version unknown` while the suite stays green.
        project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]

        with mock.patch.object(metadata, "version", return_value="9.9.9") as version:
            self.assertEqual("9.9.9", distribution_version())

        version.assert_called_once_with(project["name"])

    def test_an_uninstalled_distribution_reports_unknown(self) -> None:
        with mock.patch.object(
            metadata, "version", side_effect=metadata.PackageNotFoundError
        ):
            self.assertEqual("unknown", distribution_version())


class UpgradeTests(unittest.TestCase):
    """`upgrade` runs `install.sh` from the checkout the command came from.

    Nothing here touches a real environment: the distribution record points
    at a temporary checkout whose `install.sh` is a shell script that logs
    its arguments, and the command runs from a directory that is not a
    repository.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # A space, so the path round-trips through the file:// URL pip writes.
        self.checkout = self.root / "my checkout"
        self.checkout.mkdir()
        self.log = self.root / "install.log"
        # Not a repository: upgrading the command must not need one.
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()
        # The command the fake installer "installs" writes what this one does.
        self.answer = mock.patch.object(cli, "installed_init_digests", return_value=cli.init_digests())
        self.installed = self.patch(self.answer)
        # The newest release is read from a fake gh and a GitHub API on disk, never
        # from GitHub, and no override from the environment running the tests leaks in.
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        self.gh_log = self.root / "gh.log"
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("PROJECTOR_REF", "PROJECTOR_REPO", "PROJECTOR_INSTALLER_URL")}
        environment["PROJECTOR_GH_COMMAND"] = str(self.bin / "gh")
        self.patch(mock.patch.dict(os.environ, environment, clear=True))
        self.api = self.root / "api"
        self.patch(mock.patch.object(cli, "GITHUB_API", self.api.as_uri()))
        self.newest_release(gh="v9.9.9")

    def patch(self, patcher: object) -> object:
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def installed_from(self, record: dict[str, object] | None) -> mock.Mock:
        distribution = mock.Mock()
        distribution.read_text.return_value = None if record is None else json.dumps(record)
        return self.patch(mock.patch.object(metadata, "distribution", return_value=distribution))

    def checkout_record(self, *, editable: bool = False) -> dict[str, object]:
        return {
            "url": self.checkout.as_uri(),
            "dir_info": {"editable": True} if editable else {},
        }

    def fake_installer(self, exit_code: int = 0) -> None:
        """An `install.sh` that records its argument count and arguments."""

        script = self.checkout / "install.sh"
        script.write_text(
            f'#!/bin/sh\nprintf "%s\\n" "$#" "$@" > "{self.log}"\nexit {exit_code}\n'
        )
        script.chmod(0o755)

    def invoke(self, *targets: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        previous = Path.cwd()
        os.chdir(self.elsewhere)
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["upgrade", *targets])
        finally:
            os.chdir(previous)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_target_is_forwarded_to_the_checkout_installer(self) -> None:
        distribution = self.installed_from(self.checkout_record())
        self.fake_installer(exit_code=3)

        code, stdout, stderr = self.invoke("all")

        self.assertEqual(["1", "all"], self.log.read_text().splitlines())
        # The installer's status is the answer, and the command it ran is shown.
        self.assertEqual(3, code)
        self.assertEqual("", stdout)
        self.assertIn(shlex.join([str(self.checkout / "install.sh"), "all"]), stderr)
        # Every lookup asks for the distribution pyproject.toml installs; a
        # wrong name here would report every command as not installed and exit
        # 69 while the suite stayed green.
        project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
        self.assertEqual(
            {project["name"]},
            {call.args[0] for call in distribution.call_args_list},
        )

    def test_no_target_leaves_the_default_to_the_installer(self) -> None:
        self.installed_from(self.checkout_record())
        self.fake_installer()

        code, _, _ = self.invoke()

        self.assertEqual(0, code)
        self.assertEqual(["0"], self.log.read_text().splitlines())

    def test_targets_are_not_validated_here(self) -> None:
        # install.sh owns its target list and its usage error, so a target
        # added there works without a CLI change.
        self.installed_from(self.checkout_record())
        self.fake_installer()

        self.invoke("status")

        self.assertEqual(["1", "status"], self.log.read_text().splitlines())

    def test_an_editable_install_is_a_checkout_like_any_other(self) -> None:
        self.installed_from(self.checkout_record(editable=True))
        self.fake_installer()

        code, _, _ = self.invoke("cli")

        self.assertEqual(0, code)
        self.assertEqual(["1", "cli"], self.log.read_text().splitlines())

    def newest_release(self, *, gh: str | None = None, api: str | None = None) -> None:
        """Make the fake gh answer `gh`, or fail as a gh that is not signed in, and the API on disk answer `api`."""

        if gh:
            answer = f'printf "%s\\n" {shlex.quote(gh)}'
        else:
            answer = 'echo "To get started with GitHub CLI, please run:  gh auth login" >&2; exit 4'
        (self.bin / "gh").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.gh_log}"\n{answer}\n')
        (self.bin / "gh").chmod(0o755)
        shutil.rmtree(self.api, ignore_errors=True)
        if api:
            for repository in ("ninjudd/projector", "someone/projector"):
                latest = self.api / "repos" / repository / "releases" / "latest"
                latest.parent.mkdir(parents=True)
                latest.write_text(json.dumps({"tag_name": api, "name": api}))

    def served_installer(self, exit_code: int = 0) -> list[str]:
        """Serve an installer that logs each run, and return the URLs `upgrade` fetches it from."""

        script = self.root / "served-install.sh"
        script.write_text(
            f'#!/bin/sh\nprintf "%s\\n" "$*|ref=${{PROJECTOR_REF:-}}|repo=${{PROJECTOR_REPO:-}}" >> "{self.log}"\n'
            f'[ "$1" = status ] || exit {exit_code}\n'
        )
        fetched: list[str] = []

        def fetch(url: str, target: Path) -> None:
            fetched.append(url)
            shutil.copy(script, target)

        self.patch(mock.patch.object(cli, "fetch_installer", side_effect=fetch))
        return fetched

    def runs(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    RELEASE = {"url": "https://github.com/ninjudd/projector/archive/v0.tar.gz", "archive_info": {"hash": "sha256=0"}}

    def test_a_release_install_runs_the_installer_from_the_newest_release_tag(self) -> None:
        # The bug: the installer comes through projector.bot's cached v0, which
        # can serve the previous release's installer after v0 moves.
        self.installed_from(self.RELEASE)
        fetched = self.served_installer(exit_code=3)

        code, stdout, stderr = self.invoke("all")

        self.assertEqual((3, ""), (code, stdout), stderr)
        self.assertEqual(["https://raw.githubusercontent.com/ninjudd/projector/v9.9.9/install.sh"], fetched)
        self.assertEqual(["all|ref=v9.9.9|repo="], self.runs())
        self.assertIn(f"+ curl -fsSL {fetched[0]} | PROJECTOR_REF=v9.9.9 bash -s -- all", stderr)
        self.assertNotIn("warning", stderr)
        self.assertEqual(["api --hostname github.com repos/ninjudd/projector/releases/latest --jq .tag_name"],
                         self.gh_log.read_text().splitlines())

    def test_without_a_signed_in_gh_the_anonymous_api_names_the_release(self) -> None:
        self.installed_from(self.RELEASE)
        for case, gh_command in (("gh fails", self.bin / "gh"), ("no gh", self.root / "no-gh")):
            with self.subTest(case):
                self.log.unlink(missing_ok=True)
                os.environ["PROJECTOR_GH_COMMAND"] = str(gh_command)
                self.newest_release(api="v9.9.8")
                fetched = self.served_installer(exit_code=3)

                _, _, stderr = self.invoke("cli")

                self.assertEqual(["https://raw.githubusercontent.com/ninjudd/projector/v9.9.8/install.sh"], fetched)
                self.assertEqual(["cli|ref=v9.9.8|repo="], self.runs())
                self.assertNotIn("warning", stderr)

    def test_an_unreadable_release_falls_back_to_v0_with_one_warning(self) -> None:
        # No network, a rate limit, or no release yet must not stop the
        # upgrade: it runs the installer v0 serves, and says why.
        self.newest_release()
        for record, url, repository in (
            (self.RELEASE, "https://projector.bot/install.sh", ""),
            ({"url": "git+https://github.com/ninjudd/projector.git", "vcs_info": {}},
             "https://projector.bot/install.sh", ""),
            ({"url": "https://example.com/projector.tar.gz", "archive_info": {}}, "https://projector.bot/install.sh", ""),
            ({"url": "https://github.com/someone/projector/archive/v0.tar.gz", "archive_info": {}},
             "https://raw.githubusercontent.com/someone/projector/v0/install.sh", "someone/projector"),
        ):
            with self.subTest(record["url"]):
                self.log.unlink(missing_ok=True)
                self.installed_from(record)
                fetched = self.served_installer(exit_code=3)

                code, _, stderr = self.invoke("all")

                self.assertEqual(3, code, stderr)
                self.assertEqual([url], fetched)
                self.assertEqual([f"all|ref=|repo={repository}"], self.runs())
                warnings = [line for line in stderr.splitlines() if line.startswith("warning:")]
                self.assertEqual(1, len(warnings), stderr)
                self.assertIn(f"could not read the newest release of {repository or 'ninjudd/projector'} (gh: ",
                              warnings[0])
                self.assertIn("gh auth login; GitHub API: ", warnings[0])
                self.assertIn(f"upgrading with {url} instead", warnings[0])

    def test_an_explicit_ref_is_installed_without_a_lookup(self) -> None:
        self.installed_from(self.RELEASE)
        os.environ["PROJECTOR_REF"] = "v0.5.7"
        fetched = self.served_installer(exit_code=3)

        self.invoke("cli")

        self.assertEqual(["https://raw.githubusercontent.com/ninjudd/projector/v0.5.7/install.sh"], fetched)
        self.assertEqual(["cli|ref=v0.5.7|repo="], self.runs())
        self.assertFalse(self.gh_log.exists(), "PROJECTOR_REF names the release, so nothing asks GitHub")

    def test_projector_repo_and_a_fork_name_the_repository_to_upgrade_from(self) -> None:
        for record, override in (
            ({"url": "https://github.com/someone/projector/archive/v0.tar.gz", "archive_info": {}}, None),
            (self.RELEASE, "someone/projector"),
        ):
            with self.subTest(override=override):
                self.log.unlink(missing_ok=True)
                self.gh_log.unlink(missing_ok=True)
                os.environ.pop("PROJECTOR_REPO", None)
                if override:
                    os.environ["PROJECTOR_REPO"] = override
                self.installed_from(record)
                fetched = self.served_installer(exit_code=3)

                self.invoke("cli")

                self.assertEqual(["https://raw.githubusercontent.com/someone/projector/v9.9.9/install.sh"], fetched)
                self.assertEqual(["cli|ref=v9.9.9|repo=someone/projector"], self.runs())
                self.assertIn("repos/someone/projector/releases/latest", self.gh_log.read_text())

    def test_an_installer_url_override_is_the_one_run(self) -> None:
        self.installed_from(self.RELEASE)
        served = self.root / "elsewhere-install.sh"
        served.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*|ref=$PROJECTOR_REF" >> "{self.log}"\nexit 3\n')
        os.environ["PROJECTOR_INSTALLER_URL"] = served.as_uri()

        code, _, stderr = self.invoke("cli")

        self.assertEqual(3, code, stderr)
        self.assertEqual(["cli|ref=v9.9.9"], self.runs())

    def test_an_installer_that_cannot_be_fetched_is_named(self) -> None:
        self.installed_from(self.RELEASE)
        missing = (self.root / "nowhere" / "install.sh").as_uri()
        os.environ["PROJECTOR_INSTALLER_URL"] = missing

        code, _, stderr = self.invoke()

        self.assertEqual(69, code)
        self.assertIn(f"could not download the installer from {missing}", stderr)

    def test_an_uninstalled_command_has_nothing_to_upgrade(self) -> None:
        self.patch(mock.patch.object(metadata, "distribution", side_effect=metadata.PackageNotFoundError))

        code, _, stderr = self.invoke()

        self.assertEqual(69, code)
        self.assertIn("not an installed distribution", stderr)

    def test_a_command_with_no_recorded_source_says_so(self) -> None:
        self.installed_from(None)

        code, _, stderr = self.invoke()

        self.assertEqual(69, code)
        self.assertIn("does not record where it came from", stderr)

    def test_a_vanished_checkout_is_named(self) -> None:
        gone = self.root / "gone"
        self.installed_from({"url": gone.as_uri(), "dir_info": {}})

        code, _, stderr = self.invoke()

        self.assertEqual(69, code)
        self.assertIn(str(gone), stderr)

    def test_a_checkout_without_the_installer_is_named(self) -> None:
        self.installed_from(self.checkout_record())

        code, _, stderr = self.invoke()

        self.assertEqual(69, code)
        self.assertIn(str(self.checkout / "install.sh"), stderr)

    def changed_install(self) -> None:
        """The installed command writes a new Projector section, a new kind of file, and no site workflow."""

        digests = cli.init_digests()
        digests["instructions"]["sha256"] = "0" * 64
        digests["hooks"] = {"label": "the hooks in your settings", "sha256": "1" * 64}
        del digests["site-workflow"]
        self.installed.return_value = digests

    def test_an_install_that_changes_nothing_init_writes_says_so(self) -> None:
        self.installed_from(self.checkout_record())
        self.fake_installer()

        code, stdout, _ = self.invoke("cli")

        self.assertEqual(0, code)
        self.assertEqual("init-current   nothing project init writes changed\n", stdout)

    def test_an_install_lists_each_thing_init_writes_that_changed(self) -> None:
        self.installed_from(self.checkout_record())
        self.fake_installer()
        self.changed_install()

        code, stdout, _ = self.invoke()

        self.assertEqual(0, code)
        # The new kind is named by the label the installed command gave it,
        # and what that command no longer writes is not something to run init for.
        self.assertEqual(
            [
                "init-changed   the Projector section in AGENTS.md and CLAUDE.md",
                "init-changed   the hooks in your settings",
                "init-needed    ⚠️  run project init in each repository that uses Projector to write the changes above",
            ],
            stdout.splitlines(),
        )

    def test_a_failed_install_lists_only_what_it_changed(self) -> None:
        # `all` installs the CLI before it fails for want of a host, so a
        # failure can still have changed what init writes.
        self.installed_from(self.checkout_record())
        self.fake_installer(exit_code=69)

        code, stdout, _ = self.invoke()
        self.assertEqual((69, ""), (code, stdout))

        self.changed_install()
        code, stdout, _ = self.invoke()
        self.assertEqual(69, code)
        self.assertIn("init-needed", stdout)

    def test_an_installed_command_that_cannot_say_is_named(self) -> None:
        self.installed_from(self.checkout_record())
        self.fake_installer()
        self.installed.return_value = None

        code, stdout, _ = self.invoke()

        self.assertEqual(0, code)
        self.assertEqual(
            "init-unknown   ⚠️  the project on PATH cannot say what project init writes -- run project check in "
            "each repository that uses Projector\n",
            stdout,
        )

        self.fake_installer(exit_code=3)
        self.assertEqual((3, ""), self.invoke()[:2])

    def test_what_init_writes_is_read_before_the_installer_runs(self) -> None:
        # The reinstall replaces the templates this command reads them from.
        self.installed_from(self.checkout_record())
        self.fake_installer()
        installer_had_run = []
        real = cli.init_digests
        self.patch(mock.patch.object(
            cli, "init_digests", side_effect=lambda: installer_had_run.append(self.log.exists()) or real()
        ))

        self.invoke("cli")

        self.assertEqual([False], installer_had_run)

    def test_each_thing_init_writes_has_a_digest_of_its_own(self) -> None:
        before = cli.init_digests()
        edits = {
            "instructions": mock.patch.object(instructions, "template", return_value=instructions.template() + "x"),
            "site-workflow": mock.patch.object(cli.summary, "WORKFLOW", cli.summary.WORKFLOW + "# x\n"),
            "clean-workflow": mock.patch.object(review, "clean_workflow_text",
                                                return_value=review.clean_workflow_text() + "# x\n"),
            "permission-rules": mock.patch.object(permissions, "ASK", (*permissions.ASK, "Bash(project x *)")),
        }
        self.assertEqual(set(edits), set(before))
        for name, edit in edits.items():
            with self.subTest(name):
                with edit:
                    after = cli.init_digests()
                self.assertEqual({name}, {key for key in before if before[key] != after[key]})

    def test_the_project_on_path_answers(self) -> None:
        self.answer.stop()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        project = bin_dir / "project"
        source = Path(cli.__file__).resolve().parents[1]
        project.write_text(f'#!/bin/sh\nPYTHONPATH={shlex.quote(str(source))} exec '
                           f'{shlex.quote(sys.executable)} -m projector "$@"\n')
        project.chmod(0o755)

        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}):
            self.assertEqual(cli.init_digests(), cli.installed_init_digests())
            # A release older than this check refuses the question.
            project.write_text('#!/bin/sh\necho "unrecognized arguments: --init-digests" >&2\nexit 2\n')
            self.assertIsNone(cli.installed_init_digests())
        with mock.patch.dict(os.environ, {"PATH": str(self.elsewhere)}):
            self.assertIsNone(cli.installed_init_digests())


class UpgradeCheckTests(unittest.TestCase):
    """`upgrade` checks what a release install left against that release, with the real installer's `status`.

    This checkout's `install.sh` is served as the release's installer. pipx,
    curl, gh, `project`, and both hosts are fakes on PATH that answer from
    files a test writes, and HOME is a temporary directory, so nothing here
    reaches the network or the machine's own install.
    """

    setUp_upgrade = UpgradeTests.setUp
    patch = UpgradeTests.patch
    installed_from = UpgradeTests.installed_from
    invoke = UpgradeTests.invoke
    newest_release = UpgradeTests.newest_release

    def setUp(self) -> None:
        self.setUp_upgrade()
        self.installed_from(UpgradeTests.RELEASE)
        self.state = self.root / "state"
        self.state.mkdir()
        self.curl_log = self.root / "curl.log"
        installer = Path(__file__).parents[1] / "install.sh"
        self.patch(mock.patch.object(cli, "fetch_installer",
                                     side_effect=lambda url, target: shutil.copy(installer, target)))
        fakes = {
            "pipx": "exit 0",
            # The release's manifest, which the installer reads its version from.
            "curl": f'printf "%s\\n" "$*" >> "{self.curl_log}"\n'
                    f'[ -f "{self.state}/release" ] || exit 22\n'
                    f'printf \'{{\\n  "version": "%s"\\n}}\\n\' "$(cat "{self.state}/release")"',
            "project": f'[ "$*" = version ] || exit 2\nprintf "project %s\\n" "$(cat "{self.state}/cli")"',
            "host-claude": f'[ "$*" = "plugin list --json" ] && cat "{self.state}/claude.json"\nexit 0',
            "host-codex": f'[ "$*" = "plugin list --json" ] && cat "{self.state}/codex.json"\nexit 0',
        }
        for name, body in fakes.items():
            (self.bin / name).write_text(f"#!/bin/sh\n{body}\n")
            (self.bin / name).chmod(0o755)
        (self.bin / "python3").symlink_to(sys.executable)
        home = self.root / "home"
        home.mkdir()
        for name in ("PROJECTOR_OFFLINE", "PROJECTOR_CLAUDE_DIR", "PROJECTOR_CODEX_DIR", "CLAUDE_CONFIG_DIR",
                     "PROJECTOR_VENV", "PROJECTOR_BIN_DIR", "NO_COLOR"):
            os.environ.pop(name, None)
        os.environ.update({
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "PROJECTOR_USER_ROOT": str(home),
            "PROJECTOR_CLAUDE_COMMAND": str(self.bin / "host-claude"),
            "PROJECTOR_CODEX_COMMAND": str(self.bin / "host-codex"),
        })

    def left(self, *, release: str | None, cli_version: str, plugins: str, hooks: bool = True) -> None:
        """The release's version, and what the install leaves: the CLI's version and each host's plugins."""

        if release:
            (self.state / "release").write_text(release)
        (self.state / "cli").write_text(cli_version)
        (self.state / "claude.json").write_text(json.dumps([{"id": "projector@projector", "version": plugins}]))
        codex = [{"pluginId": "projector@projector", "version": plugins}]
        if hooks:
            codex.append({"pluginId": "projector-hooks@projector", "version": "1.0.0"})
        (self.state / "codex.json").write_text(json.dumps({"installed": codex}))

    def upgrade(self, *targets: str) -> tuple[int, list[str]]:
        """Run `upgrade`, and return its status and the rows it printed after the installer's own."""

        with tempfile.TemporaryFile() as installer_output:
            saved = os.dup(1)
            os.dup2(installer_output.fileno(), 1)
            try:
                code, stdout, stderr = self.invoke(*targets)
            finally:
                os.dup2(saved, 1)
                os.close(saved)
        rows = [line for line in stdout.splitlines() if not line.startswith("init-")]
        return code, rows

    def test_an_install_that_matches_its_release_says_so(self) -> None:
        self.newest_release(gh="v0.9.0")
        self.left(release="0.9.0", cli_version="0.9.0", plugins="0.9.0")

        code, rows = self.upgrade("all")

        self.assertEqual((0, ["verified       what this upgrade installed matches the v0.9.0 release"]), (code, rows))
        # The version compared with is the tag's, which no cache can hold back the way it can v0's.
        self.assertIn("https://raw.githubusercontent.com/ninjudd/projector/v0.9.0/.claude-plugin/plugin.json",
                      self.curl_log.read_text())

    def test_a_cli_behind_the_release_fails_the_upgrade_and_names_the_fix(self) -> None:
        # The bug: an install that left the previous release's CLI printed success.
        self.newest_release(gh="v0.9.0")
        self.left(release="0.9.0", cli_version="0.8.18", plugins="0.9.0")

        code, rows = self.upgrade()

        self.assertEqual((1, [
            "cli-stale      ⚠️  installed 0.8.18, release 0.9.0 -- run project upgrade cli",
            "mismatch       ⚠️  what this upgrade installed does not match the v0.9.0 release -- run the command "
            "each row above names",
        ]), (code, rows))

    def test_plugins_ahead_of_the_release_fail_the_upgrade(self) -> None:
        # The default branch carries a release's version before its GitHub
        # release is published, and both hosts install plugins from it.
        self.newest_release(gh="v0.8.18")
        self.left(release="0.8.18", cli_version="0.8.18", plugins="0.9.0")

        code, rows = self.upgrade("all")

        self.assertEqual(1, code)
        self.assertEqual(["plugin-ahead", "plugin-ahead", "mismatch"], [row.split()[0] for row in rows])
        self.assertIn("claude installed 0.9.0, release 0.8.18", rows[0])
        self.assertIn("run project upgrade all again in a few minutes", rows[0])

    def test_codex_without_its_hooks_plugin_fails_the_upgrade(self) -> None:
        # The bug: an installer that never added projector-hooks left Codex
        # without Projector's hooks, and the upgrade still printed success.
        self.newest_release(gh="v0.9.0")
        self.left(release="0.9.0", cli_version="0.9.0", plugins="0.9.0", hooks=False)

        code, rows = self.upgrade("codex")

        self.assertEqual((1, "hooks-absent   ⚠️  codex projector-hooks@projector -- run project upgrade codex"),
                         (code, rows[0]))

        # A target that leaves Codex alone does not fail over it.
        code, rows = self.upgrade("claude")
        self.assertEqual((0, "verified"), (code, rows[0].split()[0]))

    def test_a_host_that_is_not_installed_does_not_fail_the_check(self) -> None:
        self.newest_release(gh="v0.9.0")
        self.left(release="0.9.0", cli_version="0.9.0", plugins="0.9.0", hooks=False)
        os.environ["PROJECTOR_CODEX_COMMAND"] = str(self.root / "no-codex")

        code, rows = self.upgrade("all")

        self.assertEqual((0, ["verified       what this upgrade installed matches the v0.9.0 release"]), (code, rows))

    def test_an_install_with_no_release_version_to_compare_is_not_called_verified(self) -> None:
        self.newest_release(gh="v0.9.0")
        self.left(release=None, cli_version="0.9.0", plugins="0.9.0")

        code, rows = self.upgrade("all")

        self.assertEqual((0, ["unverified     ⚠️  could not compare what this upgrade installed with the "
                              "v0.9.0 release -- run project upgrade status to compare"]), (code, rows))


class StaleReleaseWarningTests(unittest.TestCase):
    """Every command says on stderr when it is older than the release `upgrade` installs.

    The release's manifest is a file:// URL and the cache lives in a
    temporary XDG_CACHE_HOME, so nothing here reaches the network or the
    real cache.
    """

    RELEASE = {"url": "https://github.com/ninjudd/projector/archive/v0.tar.gz", "archive_info": {}}
    # setUp points the check at a local manifest; this is the real address.
    unpatched_manifest_url = staticmethod(cli.release_manifest_url)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()
        self.manifest = self.root / "served" / "plugin.json"
        self.manifest.parent.mkdir()
        self.cache = self.root / "cache" / "projector" / "release.json"
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("PROJECTOR_OFFLINE", "PROJECTOR_REF")}
        environment["XDG_CACHE_HOME"] = str(self.root / "cache")
        self.patch(mock.patch.dict(os.environ, environment, clear=True))
        self.patch(mock.patch.object(cli, "release_manifest_url", return_value=self.manifest.as_uri()))

    def patch(self, patcher: object) -> object:
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def installed(self, version: str, record: dict[str, object] | None = None) -> None:
        distribution = mock.Mock()
        distribution.read_text.return_value = json.dumps(record or self.RELEASE)
        self.patch(mock.patch.object(metadata, "distribution", return_value=distribution))
        self.patch(mock.patch.object(metadata, "version", return_value=version))

    def release(self, version: str) -> None:
        self.manifest.write_text(json.dumps({"name": "projector", "version": version}))

    def invoke(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        previous = Path.cwd()
        os.chdir(self.elsewhere)
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    code = main(list(argv or ["config", "paths"]))
                except SystemExit as stop:
                    code = stop.code
        finally:
            os.chdir(previous)
        return code, stdout.getvalue(), stderr.getvalue()

    def age_cache(self, seconds: float) -> None:
        cached = json.loads(self.cache.read_text())
        cached["checked"] -= seconds
        self.cache.write_text(json.dumps(cached))

    def test_a_release_install_behind_the_release_warns_on_stderr(self) -> None:
        self.installed("0.6.4")
        self.release("0.6.6")

        code, stdout, stderr = self.invoke()

        self.assertEqual(0, code, stderr)
        self.assertIn(
            "warning: project 0.6.4 is behind the 0.6.6 release;"
            " run 'project upgrade' to update the CLI and the Projector plugins\n",
            stderr,
        )
        # stdout stays the command's own, so --json output still parses.
        self.assertNotIn("warning", stdout)

    def test_versions_compare_as_numbers(self) -> None:
        for installed, release, warned in [
            ("0.6.6", "0.6.6", False),
            ("0.6.10", "0.6.9", False),
            ("0.6.9", "0.6.10", True),
            ("unknown", "0.6.6", False),
        ]:
            with self.subTest(installed=installed, release=release):
                self.cache.unlink(missing_ok=True)
                self.installed(installed)
                self.release(release)

                _, _, stderr = self.invoke()

                self.assertEqual(warned, "warning: project" in stderr, stderr)

    def test_a_checkout_install_is_left_to_install_sh_status(self) -> None:
        self.installed("0.6.4", {"url": self.root.as_uri(), "dir_info": {}})
        self.release("0.6.6")

        code, _, stderr = self.invoke()

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertFalse(self.cache.exists())

    def test_the_release_is_fetched_at_most_once_a_day(self) -> None:
        self.installed("0.6.4")
        self.release("0.6.6")
        self.invoke()
        self.release("0.6.7")

        _, _, within_a_day = self.invoke()
        self.age_cache(2 * 24 * 60 * 60)
        _, _, a_day_later = self.invoke()

        self.assertIn("behind the 0.6.6 release", within_a_day)
        self.assertIn("behind the 0.6.7 release", a_day_later)

    def test_an_unreachable_release_waits_a_day_before_trying_again(self) -> None:
        self.installed("0.6.4")

        code, _, unreachable = self.invoke()
        self.release("0.6.6")
        _, _, within_a_day = self.invoke()

        self.assertEqual(0, code)
        self.assertEqual("", unreachable)
        self.assertEqual("", within_a_day)
        self.assertIsNone(json.loads(self.cache.read_text())["version"])

    def test_a_failed_refresh_keeps_the_release_it_knew(self) -> None:
        self.installed("0.6.4")
        self.release("0.6.6")
        self.invoke()
        self.manifest.unlink()
        self.age_cache(2 * 24 * 60 * 60)

        _, _, stderr = self.invoke()

        self.assertIn("behind the 0.6.6 release", stderr)

    def test_a_stalled_fetch_gives_up_after_the_timeout(self) -> None:
        # urlopen's timeout bounds each socket operation, not the fetch, and
        # name resolution has none, so only the command's own wait is bounded.
        self.installed("0.6.4")
        released = threading.Event()
        self.addCleanup(released.set)

        def stall(*_: object, **__: object) -> None:
            released.wait(30)
            raise OSError("released at cleanup")

        self.patch(mock.patch.object(cli, "RELEASE_CHECK_TIMEOUT", 0.2))
        self.patch(mock.patch.object(cli, "urlopen", side_effect=stall))

        started = time.monotonic()
        code, _, stderr = self.invoke()
        waited = time.monotonic() - started

        self.assertEqual(0, code, stderr)
        self.assertLess(waited, 2)
        self.assertNotIn("warning", stderr)

    def test_an_unwritable_cache_is_not_fetched_on_every_command(self) -> None:
        self.installed("0.6.4")
        self.release("0.6.6")
        (self.root / "cache").write_text("a file where the cache directory belongs")
        fetch = self.patch(mock.patch.object(cli, "fetch_release_version", wraps=cli.fetch_release_version))

        for _ in range(3):
            code, _, stderr = self.invoke()
            self.assertEqual(0, code, stderr)

        self.assertEqual(0, fetch.call_count)

    def test_an_unreadable_cache_is_fetched_afresh(self) -> None:
        self.installed("0.6.4")
        self.release("0.6.6")
        self.cache.parent.mkdir(parents=True)
        for text in ["not json", "[]", json.dumps({"url": self.manifest.as_uri()}),
                     json.dumps({"url": "https://example.com/other.json", "checked": 9e99, "version": "9.9.9"})]:
            with self.subTest(cache=text):
                self.cache.write_text(text)

                _, _, stderr = self.invoke()

                self.assertIn("behind the 0.6.6 release", stderr)

    def test_offline_answers_from_the_cache_without_fetching(self) -> None:
        self.installed("0.6.4")
        self.release("0.6.6")
        os.environ["PROJECTOR_OFFLINE"] = "1"

        _, _, uncached = self.invoke()
        del os.environ["PROJECTOR_OFFLINE"]
        self.invoke()
        self.age_cache(2 * 24 * 60 * 60)
        self.release("0.6.7")
        os.environ["PROJECTOR_OFFLINE"] = "1"
        _, _, cached = self.invoke()

        self.assertEqual("", uncached)
        self.assertIn("behind the 0.6.6 release", cached)

    def test_a_usage_error_ends_with_the_warning(self) -> None:
        # The error a newer skill meets on an old command is argparse's, so
        # the warning has to follow it rather than wait for a command to run.
        self.installed("0.6.4")
        self.release("0.6.6")

        code, _, stderr = self.invoke("summary-from-the-future")

        self.assertEqual(2, code)
        self.assertLess(stderr.index("invalid choice"), stderr.index("behind the 0.6.6 release"))

    def test_help_version_and_upgrade_do_not_warn(self) -> None:
        self.installed("0.6.4")
        self.release("0.6.6")
        self.patch(mock.patch.object(cli, "run_upgrade", return_value=0))

        for argv in (["help"], ["help", "init"], ["version"], ["upgrade"]):
            with self.subTest(argv=argv):
                code, _, stderr = self.invoke(*argv)

                self.assertEqual(0, code)
                self.assertNotIn("warning", stderr)

    def test_the_compared_release_is_the_one_upgrade_installs(self) -> None:
        manifest_url = self.unpatched_manifest_url

        upstream = manifest_url({"url": "https://github.com/ninjudd/projector/archive/v0.tar.gz"})
        fork = manifest_url({"url": "git+https://github.com/someone/projector.git"})
        os.environ["PROJECTOR_REF"] = "v0.6.2"
        pinned = manifest_url({"url": "https://github.com/ninjudd/projector/archive/v0.6.2.tar.gz"})

        self.assertEqual("https://raw.githubusercontent.com/ninjudd/projector/v0/.claude-plugin/plugin.json", upstream)
        self.assertEqual("https://raw.githubusercontent.com/someone/projector/v0/.claude-plugin/plugin.json", fork)
        self.assertEqual("https://raw.githubusercontent.com/ninjudd/projector/v0.6.2/.claude-plugin/plugin.json", pinned)


class ClosedReaderTests(RepositoryTestCase):
    """A reader that goes away early, as `head` does, ends the command quietly with status 141.

    Python reports a failed flush at exit after every handler has run, so only
    a real process shows it. Each test runs the command both ways it is
    installed: as `python -m projector` and as the console script, which calls
    `projector.cli:main` directly.
    """

    ENTRY_POINTS = {
        "module": ["-m", "projector"],
        "console script": ["-c", "import sys; from projector.cli import main; sys.exit(main())"],
    }

    def environment(self) -> dict[str, str]:
        return dict(os.environ, PYTHONPATH=str(Path(cli.__file__).resolve().parents[1]),
                    PROJECTOR_OFFLINE="1", XDG_CACHE_HOME=str(self.home / ".cache"))

    def project(self, entry: str, *arguments: str, stdout: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen([sys.executable, *self.ENTRY_POINTS[entry], *arguments], cwd=self.root,
                                env=self.environment(), stdout=stdout, stderr=subprocess.PIPE)

    def big_plan(self) -> Path:
        # Several times what a pipe holds, so the reader leaves output unwritten.
        return self.plan("big", body="\n".join(f"Line {n} of a plan no pipe holds at once." for n in range(20_000)))

    def test_a_reader_that_stops_after_the_first_line_ends_the_command_quietly(self) -> None:
        self.big_plan()
        for entry in self.ENTRY_POINTS:
            with self.subTest(entry=entry), self.project(entry, "show", "big", stdout=subprocess.PIPE) as process:
                self.assertEqual(b"---\n", process.stdout.readline())
                process.stdout.close()
                self.assertEqual(b"", process.stderr.read())
                self.assertEqual(141, process.wait(timeout=60))

    def test_output_still_buffered_at_exit_ends_the_command_quietly(self) -> None:
        # Short output waits in Python's buffer until exit, so a reader gone
        # before then fails only the flush at exit.
        self.plan("small")
        # `show` returns from main, and `help show` exits from inside argparse.
        for arguments in (["show", "small"], ["help", "show"]):
            for entry in self.ENTRY_POINTS:
                read, write = os.pipe()
                os.close(read)
                with self.subTest(entry=entry, arguments=arguments), self.project(entry, *arguments,
                                                                                  stdout=write) as process:
                    os.close(write)
                    stderr = process.stderr.read()
                    self.assertEqual((141, b""), (process.wait(timeout=60), stderr))

    def test_a_reader_that_reads_to_the_end_gets_the_whole_plan(self) -> None:
        path = self.big_plan()
        for entry in self.ENTRY_POINTS:
            with self.subTest(entry=entry), self.project(entry, "show", "big", stdout=subprocess.PIPE) as process:
                stdout, stderr = process.communicate(timeout=60)
                self.assertEqual((0, path.read_bytes(), b""), (process.returncode, stdout, stderr))

    def test_a_command_started_with_stdout_closed_still_succeeds(self) -> None:
        # Python leaves sys.stdout None then, so there is nothing to flush.
        self.plan("small")
        result = subprocess.run(["sh", "-c", 'exec "$@" >&-', "sh", sys.executable, "-m", "projector", "show", "small"],
                                cwd=self.root, env=self.environment(), capture_output=True)
        self.assertEqual((0, b""), (result.returncode, result.stderr))

    def test_another_os_error_is_still_reported(self) -> None:
        self.plan("small")
        with mock.patch.object(cli, "run", side_effect=PermissionError(13, "Permission denied", "readme.md")):
            self.assertEqual((65, "", "project: [Errno 13] Permission denied: 'readme.md'\n"),
                             self.invoke("show", "small"))


if __name__ == "__main__":
    unittest.main()
