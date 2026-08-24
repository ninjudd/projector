from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from projector.cli import main
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

    def tearDown(self) -> None:
        self.temporary.cleanup()

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
            PLAN.format(status="now", extra="", title="Pay", body="Done"),
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
            ("status", "alpha", "now"),
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
        self.assertIn("projector priority alpha", stderr)
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

    def test_init_adopts_an_empty_repository_and_refuses_existing_content(self) -> None:
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

        code, stdout, stderr = self.invoke("init")
        self.assertEqual(0, code, stderr)
        self.assertEqual("docs/projects/README.md\n", stdout)
        self.assertIn("lowercase `readme.md`", (self.root / stdout.strip()).read_text())

        code, _, stderr = self.invoke("init")
        self.assertEqual(65, code)
        self.assertIn("already exists", stderr)

    def test_init_adds_a_missing_convention_file_to_an_existing_tree(self) -> None:
        self.plan("alpha")
        (self.projects / "README.md").unlink()

        code, stdout, stderr = self.invoke("init")

        self.assertEqual(0, code, stderr)
        self.assertEqual("docs/projects/README.md\n", stdout)
        self.assertTrue((self.projects / "alpha" / "readme.md").exists())


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
        path.write_bytes(b"---\nstatus: now\n---\n\n# Bad \xff\n")

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


if __name__ == "__main__":
    unittest.main()
