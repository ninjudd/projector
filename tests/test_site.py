from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, walkthrough


def plan(title: str, status: str, priority: str | None) -> str:
    front = f"status: {status}\n" + (f"priority: {priority}\n" if priority else "")
    return f"---\n{front}---\n\n# {title}\n\n## 1. Outcome\n\nText.\n"


class SiteRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        files = {
            "README.md": "# Example\n\nSee [the guide](docs/guide.md).\n",
            "docs/guide.md": "# Use the guide\n\nText.\n",
            "docs/projects/README.md": "# Projects\n\nHow plans work.\n",
            "docs/projects/alpha/readme.md": plan("Build alpha", "in-progress", "now"),
            "docs/projects/alpha/notes.md": "# Alpha notes\n",
            "docs/projects/alpha/research/survey.md": "# Survey\n",
            "docs/projects/alpha/beta/readme.md": plan("Finish beta", "completed", None),
        }
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        self.out = Path(tempfile.mkdtemp()) / "site"


class SiteBuildTests(SiteRepoCase):
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
            [("alpha", "Build alpha", "in-progress", "now",
              [{"path": "docs/projects/alpha/notes.md", "title": "Alpha notes"},
               {"path": "docs/projects/alpha/research/survey.md", "title": "Survey"}]),
             ("alpha/beta", "Finish beta", "completed", None, [])],
            [(p["name"], p["title"], p["status"], p["priority"], p["files"]) for p in manifest["projects"]],
        )
        self.assertEqual([], manifest["reviews"])
        for path in ("README.md", "docs/guide.md", "docs/projects/README.md", "docs/projects/alpha/readme.md",
                     "docs/projects/alpha/notes.md", "docs/projects/alpha/beta/readme.md"):
            self.assertEqual((self.repo / path).read_bytes(), (self.out / "content" / path).read_bytes(), path)

    def test_the_home_page_loads_the_site_script_and_its_libraries(self) -> None:
        self.build()

        home = (self.out / "index.html").read_text()
        self.assertIn("<title>owner/example</title>", home)
        for needed in ('src="/assets/site.js"', "marked", "purify.min.js", 'href="/assets/site.css"', 'rel="icon"',
                       'data-base="/"'):
            self.assertIn(needed, home)
        for asset in ("site.js", "site.css", "walkthrough.js", "walkthrough.css"):
            self.assertTrue((self.out / "assets" / asset).is_file(), asset)

    def test_every_view_has_a_real_path_with_the_same_shell(self) -> None:
        self.build()

        home = (self.out / "index.html").read_text()
        for route in ("projects/", "projects/alpha/", "projects/alpha/beta/", "projects/alpha/notes/",
                      "projects/alpha/research/survey/", "reviews/", "docs/", "docs/guide/", "search/"):
            self.assertEqual(home, (self.out / route / "index.html").read_text(), route)
        self.assertEqual(home, (self.out / "404.html").read_text())
        self.assertFalse((self.out / "docs" / "projects").exists(), "a project's files live under projects/, not docs/")
        self.assertFalse((self.out / "prs").exists())

    def test_a_readme_at_the_top_of_docs_moves_aside_for_the_repository_readme(self) -> None:
        (self.repo / "docs/README.md").write_text("# About these docs\n")

        _, manifest, _ = self.build()

        self.assertIn({"path": "docs/README.md", "title": "About these docs"}, manifest["docs"])
        self.assertTrue((self.out / "docs" / "readme" / "index.html").is_file())

    def test_the_base_path_prefixes_every_asset_and_link(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo), "--base", "projector"])

        home = (self.out / "index.html").read_text()
        self.assertIn('data-base="/projector/"', home)
        self.assertIn('src="/projector/assets/site.js"', home)
        self.assertEqual("/projector/", json.loads((self.out / "site.json").read_text())["base"])

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


class VisibilityTests(SiteRepoCase):
    def build_checked(self, answers: dict[str, str | None]) -> tuple[int, str]:
        def lookup(*gh_args: str) -> str | None:
            endpoint = gh_args[1]
            if endpoint not in answers:
                raise walkthrough.SpecError(f"gh api {endpoint} failed: connection refused")
            return answers[endpoint]

        err = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             mock.patch.object(walkthrough, "gh_lookup", side_effect=lookup), \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo),
                             "--check-visibility"])
        return code, err.getvalue()

    def test_a_private_repository_with_a_public_site_deploys_nothing(self) -> None:
        code, err = self.build_checked({"repos/owner/example/pages": json.dumps({"public": True}),
                                        "repos/owner/example": "true\n"})

        self.assertEqual(65, code)
        self.assertIn("owner/example is private but its GitHub Pages site is public", err)
        self.assertFalse(self.out.exists(), "nothing is written before the check passes")

    def test_a_public_repository_or_a_private_site_deploys(self) -> None:
        for answers in ({"repos/owner/example/pages": json.dumps({"public": True}), "repos/owner/example": "false\n"},
                        {"repos/owner/example/pages": json.dumps({"public": False})}):
            with self.subTest(answers=answers):
                code, _ = self.build_checked(answers)
                self.assertEqual(0, code)
                self.assertTrue((self.out / "site.json").is_file())

    def test_a_failed_lookup_stops_the_deploy_rather_than_assuming_it_is_safe(self) -> None:
        code, err = self.build_checked({})

        self.assertEqual(65, code)
        self.assertIn("connection refused", err)
        self.assertFalse(self.out.exists())


PLAN_DIFF = """diff --git a/docs/projects/alpha/readme.md b/docs/projects/alpha/readme.md
index 1111111..2222222 100644
--- a/docs/projects/alpha/readme.md
+++ b/docs/projects/alpha/readme.md
@@ -1,3 +1,4 @@
 ---
 status: in-progress
+owner: someone
 ---
diff --git a/src/app.py b/src/app.py
index 3333333..4444444 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-old = 1
+new = 2
"""


class SiteContentTests(SiteRepoCase):
    def publish(self, number: int, head: str, projects: list[str] | None = None) -> None:
        spec = {
            "version": 1,
            "name": f"Walkthrough {number}",
            "pr": {"repo": "owner/example", "number": number, "title": f"Change {number}", "head": head,
                   "base": "b" * 40, "baseRef": "main"},
            "overview": {"summary": [], "cards": []},
            "groups": [{"id": "all", "title": "All",
                        "files": [{"path": "docs/projects/alpha/readme.md"}, {"path": "src/app.py"}]}],
        }
        if projects is not None:
            spec["projects"] = projects
        folder = self.specs / str(number) / head
        folder.mkdir(parents=True)
        (folder / "spec.json").write_text(json.dumps(spec))
        (folder / "diff.patch").write_text(PLAN_DIFF)

    def setUp(self) -> None:
        super().setUp()
        # A checkout inside a hidden directory, as a worktree under .claude/ is,
        # must still serve its files; only hidden paths inside the repository are skipped.
        hidden = Path(tempfile.mkdtemp()) / ".worktrees" / "repo"
        hidden.parent.mkdir()
        self.repo = Path(shutil.move(str(self.repo), str(hidden)))
        self.specs = Path(tempfile.mkdtemp()) / "walkthroughs"
        (self.repo / "docs/images").mkdir(parents=True)
        (self.repo / "docs/images/diagram.png").write_bytes(b"\x89PNG fake")
        (self.repo / "docs/.hidden").write_text("not served")

    def build(self) -> dict:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo),
                             "--walkthroughs", str(self.specs)])
        self.assertEqual(0, code)
        return json.loads((self.out / "site.json").read_text())

    def test_a_review_links_to_the_projects_its_diff_changes_and_back(self) -> None:
        self.publish(9, "c" * 40)

        manifest = self.build()

        self.assertEqual([["alpha"]], [r["projects"] for r in manifest["reviews"]])
        reviews = {p["name"]: p["reviews"] for p in manifest["projects"]}
        self.assertEqual({"alpha": [9], "alpha/beta": []}, reviews, "the deepest project owns the file")
        data = json.loads((self.out / "reviews" / "9" / ("c" * 40) / "data.json").read_text())
        self.assertEqual([{"name": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}], data["projects"])
        self.assertEqual("/reviews/", data["indexUrl"])
        self.assertTrue((self.out / "reviews" / "9" / "index.html").is_file())

    def test_a_spec_can_name_a_project_its_diff_does_not_touch(self) -> None:
        self.publish(9, "c" * 40, projects=["alpha/beta", "no-such-project"])

        manifest = self.build()

        self.assertEqual(["alpha/beta", "alpha"], manifest["reviews"][0]["projects"],
                         "named projects first, unknown names dropped, then the ones the diff touches")

    def test_files_under_docs_are_served_beside_the_markdown(self) -> None:
        manifest = self.build()

        self.assertEqual(["docs/images/diagram.png"], manifest["files"])
        self.assertEqual(b"\x89PNG fake", (self.out / "content/docs/images/diagram.png").read_bytes())
        self.assertFalse((self.out / "content/docs/.hidden").exists())

    def test_the_search_index_covers_every_served_document(self) -> None:
        self.build()

        index = json.loads((self.out / "search.json").read_text())
        self.assertEqual(
            [("docs/", "README", "doc"), ("docs/guide/", "Use the guide", "doc"),
             ("projects/", "How projects work", "project"), ("projects/alpha/", "Build alpha", "project"),
             ("projects/alpha/notes/", "Alpha notes", "project"),
             ("projects/alpha/research/survey/", "Survey", "project"),
             ("projects/alpha/beta/", "Finish beta", "project")],
            [(e["route"], e["title"], e["kind"]) for e in index],
        )
        alpha = next(e for e in index if e["route"] == "projects/alpha/")
        self.assertNotIn("status:", alpha["text"], "frontmatter is not searchable text")
        self.assertIn("Build alpha", alpha["text"])
        self.assertTrue((self.out / "search" / "index.html").is_file())


SITE_JS = Path(__file__).parents[1] / "src" / "projector" / "site" / "assets" / "site.js"


def js_function(name: str) -> str:
    """One top-level function of site.js, from its signature to the brace that closes it."""
    lines = SITE_JS.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  function {name}("))
    end = next(i for i in range(start, len(lines)) if lines[i] == "  }")
    return "\n".join(lines[start:end + 1])


@unittest.skipUnless(shutil.which("node"), "the highlight test needs node")
class HighlightTests(unittest.TestCase):
    def highlight(self, text: str, words: list[str]) -> str:
        program = js_function("esc") + "\n" + js_function("highlight") + \
            f"\nprocess.stdout.write(highlight({json.dumps(text)}, {json.dumps(words)}));"
        return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout

    def test_a_later_word_never_matches_inside_an_earlier_mark(self) -> None:
        self.assertEqual("Write <mark>a</mark> <mark>plan</mark> before you st<mark>a</mark>rt.",
                         self.highlight("Write a plan before you start.", ["plan", "a"]))

    def test_a_word_never_matches_inside_an_escaped_entity(self) -> None:
        self.assertEqual("Tom &amp; Jerry <mark>map</mark>", self.highlight("Tom & Jerry map", ["map", "amp"]))

    def test_the_text_is_escaped_and_matching_ignores_case(self) -> None:
        self.assertEqual("&lt;b&gt;<mark>Plan</mark>&lt;/b&gt;", self.highlight("<b>Plan</b>", ["plan"]))


@unittest.skipUnless(shutil.which("node"), "the route test needs node")
class RouteTests(SiteRepoCase):
    def test_every_link_the_page_makes_to_a_file_has_a_shell(self) -> None:
        (self.repo / "docs/README.md").write_text("# About these docs\n")
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo), "--base", "projector"])
        manifest = json.loads((self.out / "site.json").read_text())
        paths = [manifest["readme"], manifest["projectsReadme"], *(d["path"] for d in manifest["docs"])]
        for project in manifest["projects"]:
            paths += [project["path"], *(f["path"] for f in project["files"])]
        names = ("esc", "localRoute", "docRoute", "inProjects", "routeFor", "folderOf", "projectNamed", "ownerOf")
        program = f"var site = {json.dumps(manifest)}, base = '/projector/';\n" + \
            "\n".join(js_function(n) for n in names) + \
            f"\nprocess.stdout.write(JSON.stringify({json.dumps(paths)}.map(routeFor)));"
        routes = json.loads(subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout)

        self.assertEqual(
            ["/projector/docs/", "/projector/projects/", "/projector/docs/readme/", "/projector/docs/guide/",
             "/projector/projects/alpha/", "/projector/projects/alpha/notes/",
             "/projector/projects/alpha/research/survey/", "/projector/projects/alpha/beta/"],
            routes,
        )
        for route in routes:
            self.assertTrue((self.out / route.removeprefix("/projector/") / "index.html").is_file(), route)


class WorkflowTests(unittest.TestCase):
    def test_docs_changes_on_the_default_branch_rebuild_the_site(self) -> None:
        text = walkthrough.workflow_text("v0", "trunk")
        self.assertIn("  push:\n    branches: [trunk]\n    paths: [README.md, 'docs/**']\n", text)
        self.assertIn("uses: ninjudd/projector/actions/site@v0", text)

    def test_a_projects_directory_outside_docs_is_watched_too(self) -> None:
        self.assertIn("paths: [README.md, 'docs/**', 'plans/**']", walkthrough.workflow_text("v0", "main", "plans"))
        self.assertIn("paths: [README.md, 'docs/**']\n", walkthrough.workflow_text("v0", "main", "docs/projects"))


if __name__ == "__main__":
    unittest.main()
