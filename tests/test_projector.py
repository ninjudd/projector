from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import importlib.metadata as metadata

from projector import cli, instructions
from projector.cli import distribution_version, main
from projector.core import AmbiguousProject, ProjectStore


PLAN = """---
status: {status}
{priority}{extra}---

# {title}

## 1. Outcome

{body}
"""


def priority_line(priority: str | None) -> str:
    return f"priority: {priority}\n" if priority else ""


class RepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.projects = self.root / "docs" / "projects"
        self.projects.mkdir(parents=True)
        (self.projects / "README.md").write_text("# Projects\n", encoding="utf-8")
        # A fully adopted repository, so `check` is quiet by default and each
        # instruction scenario removes or alters exactly what it tests.
        (self.root / "AGENTS.md").write_text(self.block() + "\n", encoding="utf-8")
        os.symlink("AGENTS.md", self.root / "CLAUDE.md")
        # Every command now reads layered configuration, and the user layer
        # lives at $HOME/.projector.toml. Point HOME at an empty directory so
        # a real one on the machine running the tests cannot reach them.
        self.home = self.root / "home"
        self.home.mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

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
            "--root", str(self.root), "--projects-dir", "plans", "list", "--json"
        )

        self.assertEqual(0, code)
        self.assertEqual("alpha", json.loads(stdout)["projects"][0]["name"])

    def test_absolute_projects_directory_outside_root_emits_a_usable_path(self) -> None:
        with tempfile.TemporaryDirectory() as plans_directory:
            plans = Path(plans_directory)
            (plans / "README.md").write_text("# Plans\n")

            code, stdout, stderr = self.invoke(
                "--projects-dir",
                str(plans),
                "create",
                "alpha",
                "--no-edit",
                "--json",
            )

            self.assertEqual(0, code, stderr)
            self.assertEqual(
                str((plans / "alpha" / "readme.md").resolve()),
                json.loads(stdout)["path"],
            )

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

        for arguments in (
            ("list",),
            ("search", "needle"),
            ("show", "alpha"),
            ("create", "alpha", "--no-edit"),
            ("status", "alpha", "ready"),
            ("priority", "alpha", "now"),
            ("done", "alpha"),
        ):
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
        return {name: (self.root / name).read_bytes() for name in self.ADOPTED}

    def test_init_adopts_an_empty_repository_and_is_idempotent(self) -> None:
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "created docs/projects/README.md\ncreated AGENTS.md\ncreated CLAUDE.md\n", stdout
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

        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

        before = self.snapshot()
        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "unchanged docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\n",
            stdout,
        )
        self.assertEqual(before, self.snapshot())

    def test_init_adds_a_missing_convention_file_to_an_existing_tree(self) -> None:
        self.plan("alpha")
        (self.projects / "README.md").unlink()

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertEqual(
            "created docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\n", stdout
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
            "unchanged docs/projects/README.md\nupdated AGENTS.md\nupdated CLAUDE.md\n", stdout
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
            "unchanged docs/projects/README.md\nunchanged AGENTS.md\nupdated CLAUDE.md\n", stdout
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
            "unchanged docs/projects/README.md\ncreated AGENTS.md\nupdated CLAUDE.md\n", stdout
        )
        self.assertTrue(agents.is_symlink())
        self.assertEqual("CLAUDE.md", os.readlink(agents))
        self.assertFalse(claude.is_symlink())
        self.assertEqual("# Claude rules\n\n" + self.block() + "\n", claude.read_text())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))
        code, stdout, _ = self.invoke("init")
        self.assertEqual(
            "unchanged docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\n", stdout
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
            "unchanged docs/projects/README.md\nupdated AGENTS.md\nunchanged CLAUDE.md\n", stdout
        )
        self.assertEqual(self.block() + "\n", agents.read_text())
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
            "unchanged docs/projects/README.md\nkept AGENTS.md\nunchanged CLAUDE.md\n", stdout
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

        for text in ("@AGENTS.md\n", "Read @AGENTS.md before making changes.\n", "See @./AGENTS.md\n"):
            claude.write_text(text)
            self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"), text)
            code, stdout, _ = self.invoke("init")
            self.assertIn("unchanged CLAUDE.md\n", stdout, text)
            self.assertEqual(text, claude.read_text())

        for text in ("Mentions `@AGENTS.md` only in code.\n", "```\n@AGENTS.md\n```\n"):
            claude.write_text(text)
            _, _, stderr = self.invoke("check")
            self.assertIn("warning: CLAUDE.md: has no Projector section", stderr, text)

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
                "unchanged docs/projects/README.md\nupdated AGENTS.md\nunchanged CLAUDE.md\n",
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
                "unchanged docs/projects/README.md\nunchanged AGENTS.md\nunchanged CLAUDE.md\n",
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
        self.assertEqual("created plans/README.md\ncreated AGENTS.md\nunchanged CLAUDE.md\n", stdout)
        self.assertIn("`plans/README.md`", (self.root / "AGENTS.md").read_text())
        self.assertEqual((0, "Project plans are valid.\n", ""), self.invoke("check"))

    def test_instructions_can_be_disabled_in_configuration(self) -> None:
        (self.root / ".projector.toml").write_text("[instructions]\nenabled = false\n")
        (self.root / "AGENTS.md").unlink()
        (self.root / "CLAUDE.md").unlink()

        self.assertEqual((0, "unchanged docs/projects/README.md\n", ""), self.invoke("init"))
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
            ],
            payload["files"],
        )


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


class SelfReportingTests(unittest.TestCase):
    """`install.sh status` asks the installed command about itself.

    A regression here does not look like a break. It looks like every install
    reporting `cli-stale installed version unknown`, which reads as the check
    working, so pin the action rather than trusting it.
    """

    def test_version_exits_zero_and_names_the_distribution(self) -> None:
        stdout = StringIO()

        with self.assertRaises(SystemExit) as raised:
            with redirect_stdout(stdout):
                main(["--version"])

        # argparse's version action short-circuits the required subparser, so
        # this exits 0 rather than failing on the absent command.
        self.assertEqual(0, raised.exception.code)
        self.assertRegex(stdout.getvalue().strip(), r"^project \S+$")

    def test_package_dir_names_where_this_package_runs_from(self) -> None:
        stdout = StringIO()

        # A narrow width, because argparse's own version action reflows to the
        # terminal and would fold a long path. `install.sh` reads this with
        # `$(...)` and tests the result with `-d`, so a folded path reports
        # every install stale forever. Pinning COLUMNS checks that at any
        # width rather than at whatever depth this checkout happens to sit.
        with mock.patch.dict(os.environ, {"COLUMNS": "20"}):
            with self.assertRaises(SystemExit) as raised:
                with redirect_stdout(stdout):
                    main(["--package-dir"])

        self.assertEqual(0, raised.exception.code)
        printed = stdout.getvalue().splitlines()
        self.assertEqual(1, len(printed), printed)
        reported = Path(printed[0])
        self.assertEqual(Path(cli.__file__).resolve().parent, reported)
        self.assertTrue((reported / "cli.py").is_file())

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
        # Every lookup -- `--version` makes one too -- asks for the distribution
        # pyproject.toml installs; a wrong name here would report every command
        # as not installed and exit 69 while the suite stayed green.
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

    def served_installer(self, exit_code: int = 0) -> str:
        """A released installer at a file:// URL, which is where `upgrade` fetches it from."""

        served = self.root / "served" / "install.sh"
        served.parent.mkdir()
        served.write_text(
            f'#!/bin/sh\nprintf "%s\\n" "$#" "$@" "repo=${{PROJECTOR_REPO:-}}" > "{self.log}"\nexit {exit_code}\n'
        )
        self.patch(mock.patch.dict(os.environ, {"PROJECTOR_INSTALLER_URL": served.as_uri()}))
        return served.as_uri()

    def test_a_release_install_runs_the_released_installer(self) -> None:
        self.installed_from({"url": "https://github.com/ninjudd/projector/archive/v0.tar.gz",
                             "archive_info": {"hash": "sha256=0"}})
        url = self.served_installer(exit_code=3)

        code, stdout, stderr = self.invoke("all")

        self.assertEqual(3, code, stderr)
        self.assertEqual(["1", "all", "repo="], self.log.read_text().splitlines())
        self.assertEqual("", stdout)
        self.assertIn(f"+ curl -fsSL {url} | bash -s -- all", stderr)

    def test_a_git_install_upgrades_the_same_way(self) -> None:
        self.installed_from({"url": "https://github.com/ninjudd/projector.git",
                             "vcs_info": {"vcs": "git", "commit_id": "0" * 40}})
        self.served_installer()

        code, _, stderr = self.invoke("status")

        self.assertEqual(0, code, stderr)
        self.assertEqual(["1", "status", "repo="], self.log.read_text().splitlines())

    def test_a_fork_upgrades_from_its_own_repository(self) -> None:
        self.installed_from({"url": "https://github.com/someone/projector/archive/v0.tar.gz", "archive_info": {}})
        self.served_installer()

        self.invoke("cli")

        self.assertEqual(["1", "cli", "repo=someone/projector"], self.log.read_text().splitlines())

    def test_the_installer_comes_from_projector_bot_or_the_fork(self) -> None:
        self.patch(mock.patch.dict(os.environ, {}, clear=False))
        os.environ.pop("PROJECTOR_INSTALLER_URL", None)
        os.environ.pop("PROJECTOR_REF", None)

        upstream, _ = cli.release_installer({"url": "https://github.com/ninjudd/projector/archive/v0.tar.gz"})
        git, _ = cli.release_installer({"url": "git+https://github.com/ninjudd/projector.git"})
        unknown, _ = cli.release_installer({"url": "https://example.com/projector.tar.gz"})
        fork, environment = cli.release_installer({"url": "https://github.com/someone/projector/archive/v0.tar.gz"})

        self.assertEqual({"https://projector.bot/install.sh"}, {upstream, git, unknown})
        self.assertEqual("https://raw.githubusercontent.com/someone/projector/v0/install.sh", fork)
        self.assertEqual("someone/projector", environment["PROJECTOR_REPO"])

    def test_an_installer_that_cannot_be_fetched_is_named(self) -> None:
        self.installed_from({"url": "https://github.com/ninjudd/projector/archive/v0.tar.gz", "archive_info": {}})
        missing = (self.root / "nowhere" / "install.sh").as_uri()
        self.patch(mock.patch.dict(os.environ, {"PROJECTOR_INSTALLER_URL": missing}))

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


if __name__ == "__main__":
    unittest.main()
