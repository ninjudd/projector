from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, walkthrough


def plan(title: str, status: str, priority: str | None) -> str:
    front = f"status: {status}\n" + (f"priority: {priority}\n" if priority else "")
    return f"---\n{front}---\n\n# {title}\n\n## 1. Outcome\n\nText.\n"


class SiteBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        files = {
            "README.md": "# Example\n\nSee [the guide](docs/guide.md).\n",
            "docs/guide.md": "# Use the guide\n\nText.\n",
            "docs/projects/README.md": "# Projects\n\nHow plans work.\n",
            "docs/projects/alpha/readme.md": plan("Build alpha", "in-progress", "now"),
            "docs/projects/alpha/notes.md": "# Alpha notes\n",
            "docs/projects/alpha/beta/readme.md": plan("Finish beta", "completed", None),
        }
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        self.out = Path(tempfile.mkdtemp()) / "site"

    def build(self) -> tuple[int, dict, str]:
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo)])
        manifest = json.loads((self.out / "site.json").read_text()) if (self.out / "site.json").exists() else {}
        return code, manifest, err.getvalue()

    def test_describes_the_readme_docs_and_projects_and_copies_their_markdown(self) -> None:
        code, manifest, _ = self.build()

        self.assertEqual(0, code)
        self.assertEqual("owner/example", manifest["repo"])
        self.assertEqual("README.md", manifest["readme"])
        self.assertEqual([{"path": "docs/guide.md", "title": "Use the guide"}], manifest["docs"])
        self.assertEqual("docs/projects", manifest["projectsDir"])
        self.assertEqual("docs/projects/README.md", manifest["projectsReadme"])
        self.assertEqual(
            [("alpha", "Build alpha", "in-progress", "now", ["docs/projects/alpha/notes.md"]),
             ("alpha/beta", "Finish beta", "completed", None, [])],
            [(p["name"], p["title"], p["status"], p["priority"], p["files"]) for p in manifest["projects"]],
        )
        self.assertEqual([], manifest["walkthroughs"])
        for path in ("README.md", "docs/guide.md", "docs/projects/README.md", "docs/projects/alpha/readme.md",
                     "docs/projects/alpha/notes.md", "docs/projects/alpha/beta/readme.md"):
            self.assertEqual((self.repo / path).read_bytes(), (self.out / "content" / path).read_bytes(), path)

    def test_the_home_page_loads_the_site_script_and_its_libraries(self) -> None:
        self.build()

        home = (self.out / "index.html").read_text()
        self.assertIn("<title>owner/example</title>", home)
        for needed in ('src="site.js"', "marked", "purify.min.js", 'href="site.css"', 'rel="icon"'):
            self.assertIn(needed, home)
        for asset in ("site.js", "site.css", "walkthrough.js", "walkthrough.css"):
            self.assertTrue((self.out / asset).is_file(), asset)

    def test_a_plan_that_does_not_parse_costs_the_projects_view_not_the_deploy(self) -> None:
        (self.repo / "docs/projects/alpha/readme.md").write_text("---\nstatus: shipped\n---\n\n# Bad\n")

        code, manifest, err = self.build()

        self.assertEqual(0, code)
        self.assertEqual([], manifest["projects"])
        self.assertEqual("README.md", manifest["readme"])
        self.assertIn("Projects skipped", err)

    def test_a_repository_without_docs_still_gets_a_home_page(self) -> None:
        for path in sorted(self.repo.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (self.repo / "README.md").write_text("# Only a readme\n")

        code, manifest, _ = self.build()

        self.assertEqual(0, code)
        self.assertEqual(("README.md", [], [], None), (manifest["readme"], manifest["docs"], manifest["projects"],
                                                       manifest["projectsDir"]))


class WorkflowTests(unittest.TestCase):
    def test_docs_changes_on_the_default_branch_rebuild_the_site(self) -> None:
        text = walkthrough.workflow_text("v0", "trunk")
        self.assertIn("  push:\n    branches: [trunk]\n    paths: [README.md, 'docs/**']\n", text)
        self.assertIn("uses: ninjudd/projector/actions/walkthroughs@v0", text)


if __name__ == "__main__":
    unittest.main()
