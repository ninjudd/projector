from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, summary
from projector.site import serve


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
        # A build that knows its repository asks GitHub for each pull request's
        # state and reviews. No test reaches GitHub; one that needs them supplies them.
        offline = mock.patch.object(summary, "pr_status", side_effect=summary.SummaryError("gh api failed: offline"))
        offline.start()
        self.addCleanup(offline.stop)


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
        self.assertEqual([{"path": "docs/guide.md", "title": "Use the guide", "route": "docs/guide/"}], manifest["docs"])
        self.assertEqual("docs/projects", manifest["projectsDir"])
        self.assertEqual("docs/projects/README.md", manifest["projectsReadme"])
        self.assertEqual(
            [("alpha", "Build alpha", "in-progress", "now",
              [{"path": "docs/projects/alpha/notes.md", "title": "Alpha notes", "route": "projects/alpha/notes/"},
               {"path": "docs/projects/alpha/research/survey.md", "title": "Survey",
                "route": "projects/alpha/research/survey/"}]),
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
        for asset in ("site.js", "site.css", "summary.js", "summary.css"):
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

        self.assertIn({"path": "docs/README.md", "title": "About these docs", "route": "docs/readme/"}, manifest["docs"])
        self.assertTrue((self.out / "docs" / "readme" / "index.html").is_file())

    def test_the_base_path_prefixes_every_asset_and_link(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo), "--base", "projector"])

        home = (self.out / "index.html").read_text()
        self.assertIn('data-base="/projector/"', home)
        self.assertIn('src="/projector/assets/site.js"', home)
        self.assertEqual("/projector/", json.loads((self.out / "site.json").read_text())["base"])

    def test_a_plan_that_does_not_parse_costs_only_its_own_place(self) -> None:
        # The nested project under the broken one loads on its own, so it stays.
        (self.repo / "docs/projects/alpha/readme.md").write_text("---\nstatus: shipped\n---\n\n# Bad\n")

        code, manifest, err = self.build()

        self.assertEqual(0, code)
        self.assertEqual(["alpha/beta"], [p["name"] for p in manifest["projects"]])
        self.assertEqual("README.md", manifest["readme"])
        self.assertIn("::warning title=Project skipped::docs/projects/alpha/readme.md: status must be one of", err)

    def test_each_broken_plan_is_skipped_by_name_and_the_rest_are_listed(self) -> None:
        # The three ways a real repository's plans broke at once: a top-level
        # directory holding notes but no readme.md, an entry point spelled
        # README.md inside a project, and a readme with no frontmatter.
        broken = {
            "docs/projects/backlog/tests.md": "# Tests\n",
            "docs/projects/alpha/evidence/README.md": "# Evidence\n",
            "docs/projects/charts/readme.md": "# Split the charts\n\n## 1. Goal\n\nText.\n",
        }
        (self.repo / "docs/projects/gamma").mkdir(parents=True)
        (self.repo / "docs/projects/gamma/readme.md").write_text(plan("Ship gamma", "ready", "next"))
        for path, text in broken.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)

        code, manifest, err = self.build()

        self.assertEqual(0, code)
        self.assertEqual(["alpha", "alpha/beta", "gamma"], [p["name"] for p in manifest["projects"]])
        warnings = [line for line in err.splitlines() if line.startswith("::warning title=Project skipped::")]
        self.assertEqual(
            ["docs/projects/alpha/evidence/README.md", "docs/projects/backlog", "docs/projects/charts/readme.md"],
            sorted(line.split("::")[2].split(":")[0] for line in warnings),
        )
        self.assertIn("docs/projects/charts/readme.md: missing YAML frontmatter", err)

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
                raise summary.SummaryError(f"gh api {endpoint} failed: connection refused")
            return answers[endpoint]

        err = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             mock.patch.object(summary, "gh_lookup", side_effect=lookup), \
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

# Without a doctype a browser lays the page out in quirks mode, and without the
# viewport a phone lays it out at desktop width and ignores the narrow-screen rules.
STANDARDS_HEAD = ('<!doctype html>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n')


class SiteContentTests(SiteRepoCase):
    def publish(self, number: int, head: str, projects: list[str] | None = None, **pr: str) -> None:
        data = {
            "version": 1,
            "name": f"Summary {number}",
            "pr": {"repo": "owner/example", "number": number, "title": f"Change {number}", "head": head,
                   "base": "b" * 40, "baseRef": "main", **pr},
            "overview": {"summary": [], "cards": []},
            "groups": [{"id": "all", "title": "All",
                        "files": [{"path": "docs/projects/alpha/readme.md"}, {"path": "src/app.py"}]}],
        }
        if projects is not None:
            data["projects"] = projects
        folder = self.summaries / str(number) / head
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps(data))
        (folder / "diff.patch").write_text(PLAN_DIFF)

    def setUp(self) -> None:
        super().setUp()
        # A checkout inside a hidden directory, as a worktree under .claude/ is,
        # must still serve its files; only hidden paths inside the repository are skipped.
        hidden = Path(tempfile.mkdtemp()) / ".worktrees" / "repo"
        hidden.parent.mkdir()
        self.repo = Path(shutil.move(str(self.repo), str(hidden)))
        self.summaries = Path(tempfile.mkdtemp()) / "summaries"
        (self.repo / "docs/images").mkdir(parents=True)
        (self.repo / "docs/images/diagram.png").write_bytes(b"\x89PNG fake")
        (self.repo / "docs/.hidden").write_text("not served")

    def build(self) -> dict:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo),
                             "--summaries", str(self.summaries)])
        self.assertEqual(0, code)
        return json.loads((self.out / "site.json").read_text())

    def stacks(self, pulls: dict[str, str] | None = None) -> tuple[dict[int, int | None], list[str]]:
        """Each review's stackedOn, and the head branches the build asked GitHub about."""
        asked: list[str] = []

        def lookup(*args: str) -> str | None:
            head = next(a for a in args if a.startswith("head=")).split(":", 1)[1]
            asked.append(head)
            if pulls is None:
                raise summary.SummaryError("gh api failed: offline")
            return pulls.get(head, "") + "\n"

        with mock.patch.object(summary, "gh_lookup", side_effect=lookup):
            reviews = self.build()["reviews"]
        self.assertTrue(all("baseRef" not in r for r in reviews), "the page shows pull requests, not branch names")
        return {r["number"]: r["stackedOn"] for r in reviews}, asked

    def test_a_review_names_the_review_its_pull_request_is_stacked_on(self) -> None:
        self.publish(9, "c" * 40, headRef="feature-a")
        self.publish(10, "d" * 40, headRef="feature-b", baseRef="feature-a")

        self.assertEqual(({9: None, 10: 9}, []), self.stacks(), "a review's head branch answers without GitHub")

    def test_a_summary_that_recorded_the_pull_request_beneath_it_needs_no_lookup(self) -> None:
        self.publish(10, "d" * 40, baseRef="feature-a", basePr=7)

        self.assertEqual(({10: 7}, []), self.stacks())

    def test_an_older_summary_asks_github_once_per_base_branch(self) -> None:
        self.publish(10, "d" * 40, baseRef="feature-a")
        self.publish(11, "e" * 40, baseRef="feature-a")
        self.publish(12, "f" * 40, baseRef="release")

        stacked, asked = self.stacks({"feature-a": "7"})

        self.assertEqual({10: 7, 11: 7, 12: None}, stacked, "a base branch no pull request uses marks nothing")
        self.assertEqual(["feature-a", "release"], sorted(asked))

    def test_a_pull_request_on_the_default_branch_is_never_stacked_or_looked_up(self) -> None:
        self.publish(9, "c" * 40, headRef="main")
        self.publish(10, "d" * 40)

        self.assertEqual(({9: None, 10: None}, []), self.stacks({"main": "9"}))

    def test_a_default_branch_misjudged_is_still_not_a_stack(self) -> None:
        # A checkout on a feature branch that never recorded origin's HEAD takes feature for the default.
        subprocess.run(["git", "init", "-q", "-b", "feature", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "start"], check=True)
        self.publish(10, "d" * 40)

        self.assertEqual(({10: None}, ["main"]), self.stacks({}),
                         "on a checkout that does not know its default branch, main is asked about and is no pull request's head")

    def test_the_default_branch_comes_from_origin_not_the_branch_checked_out(self) -> None:
        subprocess.run(["git", "init", "-q", "-b", "feature", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "symbolic-ref", "refs/remotes/origin/HEAD",
                        "refs/remotes/origin/trunk"], check=True)
        self.publish(9, "c" * 40, baseRef="trunk")

        self.assertEqual(({9: None}, []), self.stacks({}), "trunk is known to be the default, so it is not asked about")

    def statuses(self, reviews: list[dict] | None) -> dict[str, dict | None]:
        """Each head of pull request 9's review status, built with `reviews` on GitHub, or offline when None."""
        asked: list[tuple[str, int]] = []

        def pr_status(repo: str, number: int) -> dict:
            asked.append((repo, number))
            if reviews is None:
                raise summary.SummaryError("gh api failed: offline")
            return {"state": "open", "reviews": reviews}

        self.publish(9, "c" * 40)
        self.publish(9, "d" * 40)
        with mock.patch.object(summary, "pr_status", side_effect=pr_status):
            self.build()
        self.assertEqual([("owner/example", 9)], asked, "the build asks once for each pull request")
        return {head[:1]: json.loads((self.out / "reviews" / "9" / head / "data.json").read_text()).get("review")
                for head in ("c" * 40, "d" * 40)}

    def test_each_head_shows_the_verdict_of_the_projector_review_that_names_it(self) -> None:
        statuses = self.statuses([projector_review("clean", "c" * 40, "2026-10-01T10:00:00Z", 1),
                                  projector_review("changes-requested", "d" * 40, "2026-10-02T10:00:00Z", 2)])

        self.assertEqual({"c": {"status": "clean", "url": review_url(1)},
                          "d": {"status": "changes-requested", "url": review_url(2)}}, statuses)

    def test_a_head_no_projector_review_names_is_unreviewed(self) -> None:
        marker = projector_review("clean", "c" * 40, "2026-10-01T10:00:00Z", 2)["body"].splitlines()[2]
        statuses = self.statuses([
            projector_review("clean", "e" * 40, "2026-10-01T10:00:00Z", 1),
            {"body": "Looks good to me.", "url": review_url(2), "at": "2026-10-02T10:00:00Z", "association": "OWNER"},
            {"body": f"The marker {marker} only counts on a line of its own.", "url": review_url(3),
             "at": "2026-10-03T10:00:00Z", "association": "OWNER"},
            {"body": None, "url": review_url(4), "at": "2026-10-04T10:00:00Z", "association": "OWNER"},
        ])

        self.assertEqual({"c": {"status": "unreviewed"}, "d": {"status": "unreviewed"}}, statuses)

    def test_a_marker_from_outside_the_repository_sets_no_status(self) -> None:
        # Anyone can review a public repository's pull request, and GitHub
        # shows a marker line as nothing, so a forged one must not count.
        statuses = self.statuses([
            projector_review("changes-requested", "c" * 40, "2026-10-01T10:00:00Z", 1),
            projector_review("clean", "c" * 40, "2026-10-02T10:00:00Z", 2, association="NONE"),
            projector_review("clean", "d" * 40, "2026-10-02T10:00:00Z", 3, association="CONTRIBUTOR"),
            projector_review("clean", "d" * 40, "2026-10-03T10:00:00Z", 4, association="FIRST_TIME_CONTRIBUTOR"),
        ])

        self.assertEqual({"c": {"status": "changes-requested", "url": review_url(1)}, "d": {"status": "unreviewed"}},
                         statuses)

    def test_a_member_or_collaborator_review_sets_the_status(self) -> None:
        statuses = self.statuses([
            projector_review("clean", "c" * 40, "2026-10-01T10:00:00Z", 1, association="MEMBER"),
            projector_review("changes-requested", "d" * 40, "2026-10-01T10:00:00Z", 2, association="COLLABORATOR"),
        ])

        self.assertEqual({"c": {"status": "clean", "url": review_url(1)},
                          "d": {"status": "changes-requested", "url": review_url(2)}}, statuses)

    def test_the_newest_projector_review_of_a_head_decides_its_status(self) -> None:
        statuses = self.statuses([projector_review("clean", "c" * 40, "2026-10-02T10:00:00Z", 2),
                                  projector_review("changes-requested", "c" * 40, "2026-10-01T10:00:00Z", 1),
                                  projector_review("changes-requested", "d" * 40, "2026-10-01T10:00:00Z", 3),
                                  projector_review("clean", "d" * 40, "2026-10-03T10:00:00Z", 4)])

        self.assertEqual({"c": {"status": "clean", "url": review_url(2)},
                          "d": {"status": "clean", "url": review_url(4)}}, statuses)

    def test_a_status_github_cannot_tell_is_left_out_rather_than_unreviewed(self) -> None:
        self.assertEqual({"c": None, "d": None}, self.statuses(None))

    def test_each_page_lists_the_pull_requests_beneath_and_above_it(self) -> None:
        self.publish(9, "c" * 40, headRef="feature-a")
        self.publish(10, "d" * 40, headRef="feature-b", baseRef="feature-a")
        self.publish(11, "e" * 40, headRef="feature-c", baseRef="feature-b")
        self.publish(12, "f" * 40, headRef="feature-d", baseRef="feature-b")
        self.publish(13, "a" * 40, headRef="feature-e", baseRef="feature-x", basePr=7)
        self.publish(20, "b" * 40, headRef="alone")
        self.stacks()

        def stack(number: int, head: str) -> list[dict]:
            return json.loads((self.out / "reviews" / str(number) / head / "data.json").read_text())["stack"]

        rows = stack(10, "d" * 40)
        self.assertEqual([9, 10, 11, 12], [r["number"] for r in rows], "beneath first, then each branch above")
        self.assertEqual([10], [r["number"] for r in rows if r["current"]])
        self.assertEqual({"number": 9, "title": "Change 9", "url": "/reviews/9/", "current": False}, rows[0])
        self.assertEqual([9, 10, 11, 12], [r["number"] for r in stack(11, "e" * 40)], "the top sees the whole stack")
        self.assertEqual([9, 10, 11, 12], [r["number"] for r in stack(9, "c" * 40)], "the bottom sees the whole stack")
        self.assertEqual([{"number": 7, "title": "", "url": "", "current": False},
                          {"number": 13, "title": "Change 13", "url": "/reviews/13/", "current": True}],
                         stack(13, "a" * 40), "a pull request with no summary has no title or link")
        self.assertEqual([], stack(20, "b" * 40), "a pull request alone has no stack")

    def test_a_stack_github_cannot_answer_for_shows_nothing(self) -> None:
        self.publish(10, "d" * 40, baseRef="feature-a")

        self.assertEqual({10: None}, self.stacks(None)[0])

    def test_a_review_links_to_the_projects_its_diff_changes_and_back(self) -> None:
        self.publish(9, "c" * 40)

        manifest = self.build()

        self.assertEqual([["alpha"]], [r["projects"] for r in manifest["reviews"]])
        reviews = {p["name"]: p["reviews"] for p in manifest["projects"]}
        self.assertEqual({"alpha": [9], "alpha/beta": []}, reviews, "the deepest project owns the file")
        data = json.loads((self.out / "reviews" / "9" / ("c" * 40) / "data.json").read_text())
        self.assertEqual([{"name": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}], data["projects"])
        self.assertTrue((self.out / "reviews" / "9" / "index.html").is_file())

    def test_every_page_the_build_writes_opens_in_standards_mode_at_the_device_width(self) -> None:
        self.publish(9, "c" * 40)

        self.build()

        # The pages under content/ are the repository's own, copied as they are.
        pages = sorted(p for p in self.out.rglob("*.html") if not p.is_relative_to(self.out / "content"))
        for path in ("reviews/9/index.html", f"reviews/9/{'c' * 40}/index.html", "index.html", "404.html"):
            self.assertIn(self.out / path, pages)
        for page in pages:
            self.assertTrue(page.read_text().startswith(STANDARDS_HEAD), page.relative_to(self.out))

    def test_a_summary_can_name_a_project_its_diff_does_not_touch(self) -> None:
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


SITE_JS = Path(__file__).parents[1] / "site" / "assets" / "site.js"


def js_function(name: str) -> str:
    """One function of the compiled site.js, from its signature to the brace that closes it."""
    lines = SITE_JS.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"    function {name}("))
    if lines[start].endswith("}"):
        return lines[start]
    end = next(i for i in range(start, len(lines)) if lines[i] == "    }")
    return "\n".join(lines[start:end + 1])


TS_PROJECT = Path(__file__).parents[1] / "site"
TSC = TS_PROJECT / "node_modules" / ".bin" / "tsc"


@unittest.skipUnless(TSC.exists(), "the compiled-script check needs `npm ci` in site/")
class CompiledScriptTests(unittest.TestCase):
    def test_the_committed_javascript_is_what_the_typescript_compiles_to(self) -> None:
        assets = SITE_JS.parent
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([str(TSC), "-p", str(TS_PROJECT), "--outDir", out],
                           check=True, capture_output=True, text=True)
            built = sorted(p.name for p in Path(out).glob("*.js"))
            self.assertEqual(["site.js", "summary.js"], built)
            for name in built:
                self.assertEqual((Path(out) / name).read_text(), (assets / name).read_text(),
                                 f"{name} is stale: run `npm run build` and commit it")


class StateClassTests(unittest.TestCase):
    def test_no_rule_styles_a_state_class_on_its_own(self) -> None:
        # A class the script toggles as state lands on elements that other rules
        # also style, so a bare `.reviewed` rule written for one label restyles
        # every reviewed file card. State is styled only beside another class.
        bare_state_rules = []
        for script, sheet in (("summary.ts", "summary.css"), ("site.ts", "site.css")):
            source = (TS_PROJECT / "src" / script).read_text()
            states = set(re.findall(r"classList\.(?:toggle|add|remove)\('([a-z-]+)'", source))
            self.assertIn("collapsed", states, f"no state classes found in {script}")
            css = re.sub(r"/\*.*?\*/", "", (SITE_JS.parent / sheet).read_text(), flags=re.S)
            for rule in re.findall(r"([^{}]+)\{", css):
                for selector in rule.split(","):
                    for compound in re.split(r"[\s>+~]+", selector.strip()):
                        bare = re.fullmatch(r"\.([a-z-]+)(?::[a-z-]+(?:\([^)]*\))?)*", compound)
                        if bare and bare.group(1) in states:
                            bare_state_rules.append(f"{sheet}: {selector.strip()}")
        self.assertEqual([], bare_state_rules)


@unittest.skipUnless(shutil.which("node"), "the projects view test needs node")
class ProjectsViewTests(unittest.TestCase):
    def projects_html(self, projects: list[dict]) -> str:
        """The projects view's markup for `projects`, drawn by the compiled showProjects."""
        stub = (
            "const STATUS_ORDER = ['in-progress', 'ready', 'draft', 'completed'];\n"
            "const PRIORITY_ORDER = ['now', 'next', 'later'];\n"
            "const base = '/';\n"
            f"const site = {{projects: {json.dumps(projects)}, projectsDir: 'docs/projects', projectsReadme: null}};\n"
            "let drawn = '';\n"
            "function frame(active, title, body) { drawn = body; return {querySelector: function () {"
            " return {addEventListener: function () {}}; }}; }\n"
        )
        program = stub + "\n".join(js_function(n) for n in ("esc", "badge", "depth", "ownerName", "showProjects")) + \
            "\nshowProjects(); process.stdout.write(drawn);"
        return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout

    @staticmethod
    def project(name: str, owner: str | None) -> dict:
        return {"name": name, "title": name.title(), "status": "in-progress", "priority": "now", "owner": owner,
                "path": f"docs/projects/{name}/readme.md", "files": [], "reviews": []}

    def test_an_owner_column_lists_each_owner_and_the_filter_matches_it(self) -> None:
        html = self.projects_html([self.project("alpha", "aparsuri-poly"), self.project("beta", None)])

        self.assertIn("<th>Priority</th><th>Owner</th><th>Name</th>", html)
        self.assertIn('<td class="owner">aparsuri-poly</td>', html)
        self.assertIn('<td class="owner"></td>', html)
        self.assertIn('data-search="alpha alpha aparsuri-poly"', html)

    def test_no_owner_column_when_no_plan_names_an_owner(self) -> None:
        html = self.projects_html([self.project("alpha", None), self.project("beta", None)])

        self.assertIn("<th>Priority</th><th>Name</th>", html)
        self.assertNotIn("Owner", html)

    def test_the_manifest_carries_each_owner(self) -> None:
        repo = Path(tempfile.mkdtemp())
        path = repo / "docs/projects/alpha/readme.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nstatus: draft\npriority: now\nowner: aparsuri-poly\n---\n\n# Alpha\n")
        out = Path(tempfile.mkdtemp()) / "site"
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(["site", "build", "--out", str(out), "--repo-root", str(repo)]))

        self.assertEqual("aparsuri-poly", json.loads((out / "site.json").read_text())["projects"][0]["owner"])


@unittest.skipUnless(shutil.which("node"), "the header test needs node")
class HeaderTests(unittest.TestCase):
    def header_html(self, args: str) -> str:
        """The site header's markup for `header(args)`, drawn by the compiled script."""
        stub = (
            "const base = '/', site = {repo: 'owner/example'};\n"
            "const MARK = '<svg class=\"sitemark\"></svg>', MENU_ICON = '<svg menu></svg>',"
            " FULL_ICON = '<svg full></svg>';\n"
            "function nav() { return ''; }\n"
        )
        program = stub + js_function("esc") + "\n" + js_function("header") + \
            f"\nprocess.stdout.write(header({args}));"
        return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout

    def test_a_page_without_a_sidebar_keeps_the_menu_buttons_place_empty(self) -> None:
        html = self.header_html("'docs'")

        self.assertTrue(html.startswith('<header class="sitebar"><span class="sitebtn menuspace" aria-hidden="true"></span>'
                                        '<a class="sitename"'), html)
        self.assertNotIn("sidetoggle", html)
        self.assertNotIn("barhide", html)

    def test_the_section_menu_button_leads_the_header_and_reports_its_state(self) -> None:
        html = self.header_html("'docs', undefined, 'open'")

        self.assertTrue(html.startswith('<header class="sitebar"><button type="button" class="sitebtn sidetoggle"'), html)
        self.assertIn('aria-controls="siteside" aria-expanded="true"', html)
        self.assertNotIn("barhide", html)

    def test_an_html_page_can_go_full_screen_and_starts_with_its_sidebar_closed(self) -> None:
        html = self.header_html("'docs', undefined, 'closed', true")

        self.assertIn('aria-expanded="false"', html)
        self.assertTrue(html.endswith('aria-label="Full screen"><svg full></svg></button></header>'), html)


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


def run_git(cwd: Path, *args: str, when: int | None = None) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f"{when} +0000", GIT_COMMITTER_DATE=f"{when} +0000")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


HEADER_DIFF = """diff --git a/docs/projects/alpha/readme.md b/docs/projects/alpha/readme.md
index 1111111..2222222 100644
--- a/docs/projects/alpha/readme.md
+++ b/docs/projects/alpha/readme.md
@@ -1,1 +1,1 @@
-old
+new
diff --git a/docs/projects/alpha/research/survey.md b/docs/projects/alpha/research/survey.md
index 1111111..2222222 100644
--- a/docs/projects/alpha/research/survey.md
+++ b/docs/projects/alpha/research/survey.md
@@ -1,1 +1,1 @@
-old
+new
diff --git a/docs/projects/alpha/beta/readme.md b/docs/projects/alpha/beta/readme.md
index 1111111..2222222 100644
--- a/docs/projects/alpha/beta/readme.md
+++ b/docs/projects/alpha/beta/readme.md
@@ -1,1 +1,1 @@
-old
+new
diff --git a/docs/projects/gamma/readme.md b/docs/projects/gamma/readme.md
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/docs/projects/gamma/readme.md
@@ -0,0 +1,1 @@
+new
diff --git a/src/app#1.py b/src/app#1.py
index 1111111..2222222 100644
--- a/src/app#1.py
+++ b/src/app#1.py
@@ -1,1 +1,1 @@
-old = 1
+new = 2
"""
HEADER_FILES = ["docs/projects/alpha/readme.md", "docs/projects/alpha/research/survey.md",
                "docs/projects/alpha/beta/readme.md", "docs/projects/gamma/readme.md", "src/app#1.py"]
SUMMARY_JS = SITE_JS.with_name("summary.js")


def rendered_summary(data: dict) -> str:
    """The markup the compiled renderSummary draws for `data`, run under node."""
    lines = SUMMARY_JS.read_text().splitlines()
    start = lines.index("    function renderSummary(data) {")
    end = next(i for i in range(start, len(lines)) if lines[i] == "    }")
    program = ("const root = {innerHTML: ''};\n"
               "const document = {getElementById: function () { return root; }, body: root};\n"
               "const window = {};\n" + "\n".join(lines[start:end + 1]) +
               f"\nrenderSummary({json.dumps(data)});\nprocess.stdout.write(root.innerHTML);")
    return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout


class FileHeaderTests(SiteRepoCase):
    """Each file card's header links to its diff, to the file on the head branch, and to its project."""

    def build(self, state: str | None = None, **pr: str) -> dict:
        """The data the site build writes for pull request 9, which changes HEADER_FILES.

        GitHub reports the pull request as `state`, or cannot be asked when it is None.
        """
        summaries = Path(tempfile.mkdtemp()) / "summaries"
        folder = summaries / "9" / ("c" * 40)
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps({
            "version": 1, "name": "Summary 9",
            "pr": {"repo": "owner/example", "number": 9, "title": "Change 9", "head": "c" * 40, "base": "b" * 40,
                   "baseRef": "main", **pr},
            "overview": {"summary": [], "cards": []},
            "groups": [{"id": "all", "title": "All", "checks": [{"kind": "context", "text": "About the section."}], "files": [
                {"path": p, "collapsed": False,
                 **({"checks": [{"kind": "context", "text": "The new value.", "line": 1},
                                {"kind": "verify", "text": "The whole file."}]} if p == "src/app#1.py" else
                    {"checks": [{"kind": "flag", "text": "A new plan.", "line": 1}]} if p == "docs/projects/gamma/readme.md"
                    else {})}
                for p in HEADER_FILES]}],
        }))
        (folder / "diff.patch").write_text(HEADER_DIFF)
        status = mock.patch.object(summary, "pr_status", return_value={"state": state, "reviews": []}) \
            if state is not None else nullcontext()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), status, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo),
                             "--summaries", str(summaries)])
        self.assertEqual(0, code)
        return json.loads((self.out / "reviews" / "9" / ("c" * 40) / "data.json").read_text())

    def test_a_file_belongs_to_the_deepest_project_the_site_has(self) -> None:
        files = {f["path"]: f.get("project") for f in self.build()["files"]}

        alpha = {"name": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}
        self.assertEqual({
            "docs/projects/alpha/readme.md": alpha,
            "docs/projects/alpha/research/survey.md": alpha,
            "docs/projects/alpha/beta/readme.md": {"name": "alpha/beta", "title": "Finish beta",
                                                   "url": "/projects/alpha/beta/"},
            "docs/projects/gamma/readme.md": None,
            "src/app#1.py": None,
        }, files, "a supplemental file belongs to its project, a nested project owns its files, and a project "
                  "the site has no page for, such as one the pull request adds, owns nothing")

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_the_header_links_the_diff_the_file_on_its_branch_and_the_project(self) -> None:
        data = self.build(headRef="feature/header-links")
        html = rendered_summary(data)

        def header(path: str) -> str:
            fid = next(f["id"] for f in data["files"] if f["path"] == path)
            start = re.search(f'<article class="file[^"]*" id="{fid}"', html).start()
            return html[start:html.index("</header>", start)]

        survey = header("docs/projects/alpha/research/survey.md")
        anchor = next(f["anchor"] for f in data["files"] if f["path"] == "docs/projects/alpha/research/survey.md")
        self.assertIn(f'href="https://github.com/owner/example/pull/9/files#{anchor}" target="_blank" rel="noopener">Diff</a>',
                      survey)
        self.assertIn('href="https://github.com/owner/example/blob/feature/header-links/docs/projects/alpha/research/'
                      'survey.md" target="_blank" rel="noopener">File</a>', survey)
        self.assertIn('<a class="flink" href="/projects/alpha/" title="Build alpha">Project</a>', survey)
        self.assertIn('<a class="flink" href="/projects/alpha/beta/" title="Finish beta">Project</a>',
                      header("docs/projects/alpha/beta/readme.md"))
        for outside in ("docs/projects/gamma/readme.md", "src/app#1.py"):
            self.assertNotIn(">Project</a>", header(outside))
        self.assertIn('href="https://github.com/owner/example/blob/feature/header-links/src/app%231.py"',
                      header("src/app#1.py"), "each path segment is encoded and the branch keeps its slash")
        self.assertNotIn(">PR</a>", html)

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_the_file_link_follows_the_branch_until_the_pull_request_merges_or_closes(self) -> None:
        # GitHub usually deletes a merged or closed pull request's branch, and
        # its head commit stays reachable, so those link the head.
        branch = "https://github.com/owner/example/blob/feature/header-links/src/app%231.py"
        at_head = f'https://github.com/owner/example/blob/{"c" * 40}/src/app%231.py'
        for state, expected in (("open", branch), ("merged", at_head), ("closed", at_head), (None, branch)):
            with self.subTest(state=state):
                data = self.build(state=state, headRef="feature/header-links")
                self.assertEqual(state, data["pr"].get("state"), "the page records the state the build found")
                self.assertIn(f'href="{expected}"', rendered_summary(data))
                shutil.rmtree(self.out)

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_a_summary_without_its_branch_links_the_file_at_its_head(self) -> None:
        data = self.build()
        self.assertNotIn("headRef", data["pr"])

        self.assertIn(f'href="https://github.com/owner/example/blob/{"c" * 40}/src/app%231.py"', rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_a_pull_request_alone_is_a_stack_of_one_in_the_sidebar(self) -> None:
        data = self.build()
        self.assertEqual([], data["stack"])

        self.assertIn('<ul class="stack" aria-label="Pull requests in this stack"><li class="current" aria-current="page">'
                      '<span class="snum">#9</span><span class="stitle">Change 9</span></li></ul>',
                      rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_the_versions_toggle_is_an_icon_named_for_how_many_there_are(self) -> None:
        data = self.build()
        data["heads"] = [{"head": "c" * 40, "url": f"/reviews/9/{'c' * 40}/", "current": True, "at": 1_700_000_500},
                         {"head": "d" * 40, "url": f"/reviews/9/{'d' * 40}/", "current": False, "at": 1_700_000_000}]
        html = rendered_summary(data)

        self.assertIn('<details class="versions"><summary aria-label="Versions (2)" title="Versions (2)"><svg', html)
        self.assertNotIn("Versions ·", html)

    @unittest.skipUnless(shutil.which("node"), "the overview test needs node")
    def test_the_overview_card_holds_only_what_the_summary_wrote(self) -> None:
        self.assertIn('<div class="card prose"><h3>What this PR does</h3></div>', rendered_summary(self.build()),
                      "the page adds no reading guide of its own")

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_the_sidebar_lists_each_project_as_a_row_above_the_stack(self) -> None:
        html = rendered_summary(self.build())

        projects = re.search(r'<ul class="stack projects" aria-label="Projects this pull request changes">(.*?)</ul>', html)
        self.assertIsNotNone(projects, "the projects list is missing")
        self.assertEqual('<li><a href="/projects/alpha/"><span class="stitle">Build alpha</span></a></li>'
                         '<li><a href="/projects/alpha/beta/"><span class="stitle">Finish beta</span></a></li>',
                         projects.group(1), "each project's title fills its row, with nothing before it")
        self.assertLess(projects.start(), html.index('<ul class="stack" aria-label="Pull requests in this stack">'),
                        "the projects come first in the sidebar")
        self.assertNotIn("Projects:", html)

    @unittest.skipUnless(shutil.which("node"), "the inline note test needs node")
    def test_a_note_under_a_line_puts_its_tag_in_the_gutter_and_its_text_where_the_code_starts(self) -> None:
        html = rendered_summary(self.build())

        rows = dict(re.findall(r'<tr class="noterow (\w+)">(.*?)</tr>', html))
        self.assertEqual('<td class="ngut" colspan="2"><span class="chip context">context</span></td>'
                         '<td class="nte"><span class="ntext">The new value.</span></td>', rows["context"],
                         "a file with both sides has two line-number columns for the tag")
        self.assertTrue(rows["flag"].startswith('<td class="ngut" colspan="1"><span class="chip flag">concern</span></td>'
                                                '<td class="nte"><input type="checkbox" class="nbox note-box"'),
                        "a new file has one line-number column, and a concern's checkbox sits with its text")
        self.assertIn('<table class="diff oneside">', html, "a one-sided diff is marked, so its gutter keeps its width")
        self.assertIn('<table class="diff fnotes"><colgroup><col class="lncol"><col class="lncol"><col></colgroup>'
                      '<tr class="noterow verify"><td class="ngut" colspan="2"><span class="chip verify">verified</span></td>'
                      '<td class="nte"><span class="ntext">The whole file.</span></td></tr></table>', html,
                      "a note on the whole file sits above the diff in the diff's own columns")
        self.assertIn('<div class="gnotes"><h4>Notes</h4><ul class="notes"><li class="context"><span class="chip context">'
                      'context</span><span class="ntext">About the section.</span></li></ul></div>', html,
                      "a section's note leads with its tag, like a note in a diff")


class SummarySidebarTests(unittest.TestCase):
    def test_the_pinned_section_list_scrolls_on_its_own(self) -> None:
        # The sidebar is sticky on a wide screen, so a list taller than the
        # window has no other way to reach its last sections. On a narrow
        # screen it sits above the content and scrolls with the page.
        # The sidebar holds the list's box and the credit under it, so the
        # sidebar is what fits the window, and the box takes what the credit
        # leaves.
        css = (SITE_JS.parent / "summary.css").read_text()
        side = re.search(r"^\.layout > \.side \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(side, "summary.css has no .layout > .side rule")
        self.assertRegex(side.group(1), r"max-height: calc\(100vh\b")
        self.assertRegex(side.group(1), r"display: flex; flex-direction: column;")
        wide = re.search(r"^\.side \.nav \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(wide, "summary.css has no .side .nav rule")
        self.assertRegex(wide.group(1), r"overflow-y: auto")
        # A scroll the list cannot take passes to the page, which is what pins
        # the sidebar and brings a long list's last sections into the window.
        self.assertNotIn("overscroll-behavior", wide.group(1))
        narrow = re.search(r"@media \(max-width: 980px\) \{ \.layout > \.side \{ position: static; max-height: none; \} (.*) \}$",
                           css, re.M)
        self.assertIsNotNone(narrow, "summary.css has no narrow-screen .side rule")
        self.assertIn(".side .nav { overflow: visible; }", narrow.group(1))

    def test_a_long_section_title_wraps_instead_of_scrolling_the_list_sideways(self) -> None:
        css = (SITE_JS.parent / "summary.css").read_text()
        title = re.search(r"^\.nav \.ntitle \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(title, "summary.css has no .nav .ntitle rule")
        self.assertIn("overflow-wrap: anywhere;", title.group(1))

    def test_the_stack_list_rules_leave_the_reviews_index_alone(self) -> None:
        # The home page loads summary.css as well, and its Reviews table draws
        # each "Stacked on #N" note as a `div.stack`, so a rule written for the
        # sidebar's stack list names the sidebar block the list sits in.
        css = re.sub(r"/\*.*?\*/", "", (SITE_JS.parent / "summary.css").read_text(), flags=re.S)
        stack = [selector.strip() for rule in re.findall(r"([^{}]+)\{", css)
                 for selector in rule.split(",") if re.search(r"\.stack\b", selector)]
        self.assertTrue(stack, "summary.css has no .stack rule")
        self.assertEqual([], [selector for selector in stack if not selector.startswith(".prblock .stack")])

    def test_a_stack_row_shows_up_to_three_lines_of_its_title_current_or_not(self) -> None:
        css = (SITE_JS.parent / "summary.css").read_text()
        title = re.search(r"^\.prblock \.stack \.stitle \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(title, "summary.css has no .prblock .stack .stitle rule")
        self.assertIn("overflow-wrap: anywhere;", title.group(1))
        self.assertIn("-webkit-line-clamp: 3;", title.group(1))
        # Every title has the same weight, because bold text is wider, and a
        # current row in a different weight could wrap onto more lines than
        # the same title in another row.
        self.assertIn("font-weight: 600;", title.group(1))
        current = re.search(r"^\.prblock \.stack li\.current \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(current, "summary.css has no .prblock .stack li.current rule")
        self.assertNotIn("font-weight", current.group(1))
        current_title = re.search(r"^\.prblock \.stack li\.current \.stitle \{", css, re.M)
        self.assertIsNone(current_title, "the current row's title is styled like every other title")


def review_url(review_id: int) -> str:
    return f"https://github.com/owner/example/pull/9#pullrequestreview-{review_id}"


def projector_review(verdict: str, head: str, at: str, review_id: int, association: str = "OWNER") -> dict:
    """A review of pull request 9 as `summary.pr_status` lists it, carrying a Projector review's marker for `head`.

    `association` is the author's relation to the repository, as GitHub's
    `author_association` gives it.
    """
    marker = (f"<!-- projector-review v=1 verdict={verdict} projector=0.6.13 model=claude-opus effort=high "
              f"sha={head} findings=0 seconds=60 covered=2/2 -->")
    return {"body": f"**Projector review** · `0.6.13`\n\n{marker}\n\nWhat the review found.",
            "url": review_url(review_id), "at": at, "association": association}


def served_summary(number: int, head: str) -> dict:
    """A summary for pull request `number` at `head` that builds against PLAN_DIFF."""
    return {"version": 1, "name": f"Summary {number}", "overview": {"summary": [], "cards": []},
            "pr": {"repo": "owner/example", "number": number, "title": f"Change {number}", "head": head,
                   "base": "b" * 40, "baseRef": "main"},
            "groups": [{"id": "all", "title": "All",
                        "files": [{"path": "docs/projects/alpha/readme.md"}, {"path": "src/app.py"}]}]}


class ServeTests(SiteRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.sites: list[serve.Site] = []

    def tearDown(self) -> None:
        for site in self.sites:
            site.close()

    def site(self, summaries: Path | None = None, base: str = "/") -> serve.Site:
        def build(out: Path) -> str:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return cli.build_checkout(self.repo.resolve(), out, summaries, base, "owner/example")

        site = serve.Site(build, lambda: serve.fingerprint([self.repo]))
        self.sites.append(site)
        site.refresh(force=True)
        return site

    def fetch(self, site: serve.Site, base: str, path: str, host: str | None = None,
              listen: str = "127.0.0.1") -> tuple[int, str]:
        server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.handler(site, base, serve.allowed_hosts(listen)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}{path}"
            request = urllib.request.Request(url, headers={"Host": host} if host else {})
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.read().decode()
        finally:
            server.shutdown()
            server.server_close()

    def test_a_request_naming_another_host_gets_no_content(self) -> None:
        site = self.site()
        manifest = (site.current / "site.json").read_text()

        for host in ("evil.example:8000", "attacker.example", "127.0.0.1.evil.example"):
            with self.subTest(host=host):
                code, body = self.fetch(site, "/", "/site.json", host=host)
                self.assertEqual(403, code)
                self.assertNotIn('"projects"', body)
        for host in ("localhost:8000", "127.0.0.1", "[::1]:8000"):
            with self.subTest(host=host):
                self.assertEqual((200, manifest), self.fetch(site, "/", "/site.json", host=host))
        self.assertEqual(200, self.fetch(site, "/", "/", host="box.lan:8000", listen="box.lan")[0])
        self.assertEqual(200, self.fetch(site, "/", "/", host="192.168.1.5:8000", listen="0.0.0.0")[0],
                         "a wildcard address answers any name, as the warning says")

    def test_serves_every_view_and_answers_a_missing_path_with_the_not_found_page(self) -> None:
        site = self.site()

        for path in ("/", "/projects/", "/projects/alpha/beta/", "/docs/guide/", "/site.json", "/assets/site.js"):
            self.assertEqual(200, self.fetch(site, "/", path)[0], path)
        code, body = self.fetch(site, "/", "/no/such/page/")
        self.assertEqual(404, code)
        self.assertEqual((site.current / "404.html").read_text(), body)

    def test_serves_the_site_under_its_base_and_nothing_outside_it(self) -> None:
        site = self.site(base="/example/")

        self.assertEqual(200, self.fetch(site, "/example/", "/example/projects/alpha/")[0])
        self.assertEqual(404, self.fetch(site, "/example/", "/projects/alpha/")[0])
        self.assertEqual(404, self.fetch(site, "/example/", "/examples/")[0])

    def test_rebuilds_only_when_a_source_changes_and_removes_old_builds(self) -> None:
        site = self.site()
        first = site.current

        self.assertIsNone(site.refresh(), "nothing changed")
        (self.repo / "docs/added.md").write_text("# Added later\n")
        self.assertIsNotNone(site.refresh())
        self.assertEqual(200, self.fetch(site, "/", "/docs/added/")[0])
        self.assertTrue(first.is_dir(), "a request may still be reading the build it replaced")
        (self.repo / "docs/added.md").unlink()
        site.refresh()
        self.assertFalse(first.exists())
        self.assertEqual(404, self.fetch(site, "/", "/docs/added/")[0])

    def test_a_failed_rebuild_keeps_serving_the_last_build_until_the_next_change(self) -> None:
        site = self.site()
        good = site.current
        site.build = mock.Mock(side_effect=RuntimeError("broken"))
        (self.repo / "docs/added.md").write_text("# Added later\n")

        with self.assertRaises(RuntimeError):
            site.refresh()

        self.assertEqual(good, site.current)
        self.assertEqual([good.name], [path.name for path in site.scratch.iterdir()], "the partial build is removed")
        self.assertIsNone(site.refresh(), "retried on the next change, not every poll")

    def published_remote(self, ref: str = summary.PAGES_REF, root: str = summary.PAGES_ROOT,
                         name: str = "summary.json") -> Path:
        """A remote of this checkout whose `ref` holds two heads of pull request 9 under `root`, each stored as `name`."""
        remote = Path(tempfile.mkdtemp())
        run_git(remote, "init", "--quiet")
        for when, head in ((1_700_000_000, "c" * 40), (1_700_000_500, "d" * 40)):
            folder = remote / root / "9" / head
            folder.mkdir(parents=True)
            (folder / name).write_text(json.dumps(served_summary(9, head)))
            (folder / "diff.patch").write_text(PLAN_DIFF)
            run_git(remote, "add", ".")
            run_git(remote, "commit", "--quiet", "-m", head[:1], when=when)
        run_git(remote, "update-ref", ref, "HEAD")
        run_git(self.repo, "init", "--quiet")
        run_git(self.repo, "remote", "add", "origin", str(remote))
        return remote

    def assert_serves_both_heads(self, ref: str, root: str) -> None:
        site = self.site(summaries=serve.extract_summaries(self.repo, ref, Path(tempfile.mkdtemp()), root))
        manifest = json.loads(self.fetch(site, "/", "/site.json")[1])
        self.assertEqual([("d" * 40, 2)], [(w["head"], w["heads"]) for w in manifest["reviews"]],
                         "each summary is dated by its own commit, not the ref's newest")
        self.assertEqual(200, self.fetch(site, "/", f"/reviews/9/{'c' * 40}/")[0])

    def test_serves_summaries_from_the_fetched_ref_newest_head_first(self) -> None:
        self.published_remote()

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        found = serve.summaries_refs(self.repo, "origin")
        self.assertEqual([("refs/projector/remotes/origin/summaries", "summaries")], found)
        self.assert_serves_both_heads(*found[0])

    def test_a_republished_summary_keeps_the_date_its_head_was_first_published(self) -> None:
        # Both heads were published as spec.json, then republished newest first.
        remote = self.published_remote(name="spec.json")
        for when, head in ((1_700_001_000, "d" * 40), (1_700_001_500, "c" * 40)):
            (remote / summary.PAGES_ROOT / "9" / head / "summary.json").write_text(json.dumps(served_summary(9, head)))
            run_git(remote, "add", ".")
            run_git(remote, "commit", "--quiet", "-m", head[:1], when=when)
        run_git(remote, "update-ref", summary.PAGES_REF, "HEAD")

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        self.assert_serves_both_heads(*serve.summaries_refs(self.repo, "origin")[0])
        out = Path(tempfile.mkdtemp())
        with redirect_stdout(io.StringIO()):
            cli.build_checkout(self.repo.resolve(), out, remote / summary.PAGES_ROOT, "/", "owner/example")
        reviews = json.loads((out / "site.json").read_text())["reviews"]
        self.assertEqual([("d" * 40, 2)], [(review["head"], review["heads"]) for review in reviews],
                         "dated from Git, as the site action builds a checkout of the ref")

    def test_reads_the_old_walkthroughs_ref_and_reports_its_spec_json_summaries_as_skipped(self) -> None:
        # Every release that published to the old ref stored each summary as spec.json.
        self.published_remote(summary.LEGACY_PAGES_REF, summary.LEGACY_PAGES_ROOT, "spec.json")

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        found = serve.summaries_refs(self.repo, "origin")
        self.assertEqual([("refs/projector/remotes/origin/walkthroughs", "walkthroughs")], found)
        extracted = serve.extract_summaries(self.repo, found[0][0], Path(tempfile.mkdtemp()), found[0][1])
        with redirect_stdout(io.StringIO()):
            built = cli.build_checkout(self.repo.resolve(), Path(tempfile.mkdtemp()), extracted, "/", "owner/example")
        self.assertTrue(built.endswith(" 0 reviews, 2 reviews skipped"), built)

    def test_the_summaries_ref_wins_over_the_old_walkthroughs_ref(self) -> None:
        remote = self.published_remote()
        run_git(remote, "update-ref", summary.LEGACY_PAGES_REF, "HEAD~1")

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        self.assertEqual([("refs/projector/remotes/origin/summaries", "summaries")],
                         serve.summaries_refs(self.repo, "origin"))
        old = serve.run_git(self.repo, "rev-parse", "--verify", "--quiet", "refs/projector/remotes/origin/walkthroughs")
        self.assertNotEqual(0, old.returncode, "the old ref is not fetched once the new one exists")

    def test_a_checkout_without_the_ref_serves_no_summaries(self) -> None:
        run_git(self.repo, "init", "--quiet")

        self.assertNotEqual("", serve.fetch_summaries(self.repo, "origin"))
        self.assertEqual([], serve.summaries_refs(self.repo, "origin"))

    def publish_locally(self, number: int, head: str) -> None:
        """Commit a summary for pull request `number` to this checkout's own summaries ref, as a local publish does."""
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tempfile.mkdtemp()) / "index"), GIT_AUTHOR_NAME="Test",
                   GIT_AUTHOR_EMAIL="test@example.com", GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")

        def git(*args: str, text: str | None = None) -> str:
            return subprocess.run(["git", *args], cwd=self.repo, env=env, input=text, check=True, capture_output=True,
                                  text=True).stdout.strip()

        for name, content in (("summary.json", json.dumps(served_summary(number, head))), ("diff.patch", PLAN_DIFF)):
            blob = git("hash-object", "-w", "--stdin", text=content)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},summaries/{number}/{head}/{name}")
        git("update-ref", summary.PAGES_REF, git("commit-tree", git("write-tree"), "-m", "Publish locally"))

    def test_a_server_shows_summaries_kept_local_beside_the_remotes(self) -> None:
        self.published_remote()
        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        published = serve.Summaries(self.repo, "origin")
        before = published.version()

        self.publish_locally(10, "e" * 40)

        self.assertNotEqual(before, published.version(), "a local publish is a change the server rebuilds for")
        self.assertEqual([("refs/projector/remotes/origin/summaries", "summaries"), (summary.PAGES_REF, "summaries")],
                         serve.summaries_refs(self.repo, "origin"))
        site = self.site(summaries=published.extract(Path(tempfile.mkdtemp())))
        manifest = json.loads(self.fetch(site, "/", "/site.json")[1])
        self.assertEqual({9, 10}, {review["number"] for review in manifest["reviews"]})

    def test_a_failed_fetch_says_what_failed(self) -> None:
        run_git(self.repo, "init", "--quiet")
        run_git(self.repo, "remote", "add", "origin", str(self.repo / "missing"))

        reason = serve.fetch_summaries(self.repo, "origin")

        self.assertIn("does not appear to be a git repository", reason)
        self.assertNotIn("and the repository exists", reason)

    def assert_fetch_leaves_fetch_head_alone(self) -> None:
        # The user's own `git fetch` may have just written it, for a `git checkout FETCH_HEAD` to follow.
        fetch_head = self.repo / ".git" / "FETCH_HEAD"
        fetch_head.write_text("0123456789012345678901234567890123456789\t\tbranch 'topic' of elsewhere\n")
        before = fetch_head.read_text()

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        self.assertEqual(before, fetch_head.read_text())

    def test_fetching_summaries_leaves_the_checkouts_fetch_head_alone(self) -> None:
        self.published_remote()
        self.assert_fetch_leaves_fetch_head_alone()

    def test_fetching_the_old_walkthroughs_ref_leaves_fetch_head_alone_too(self) -> None:
        self.published_remote(summary.LEGACY_PAGES_REF, summary.LEGACY_PAGES_ROOT, "spec.json")
        self.assert_fetch_leaves_fetch_head_alone()

    def test_a_running_server_serves_summaries_published_after_it_started(self) -> None:
        remote = self.published_remote()
        published = serve.Summaries(self.repo, "origin")

        def build(out: Path) -> str:
            with tempfile.TemporaryDirectory() as extracted, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return cli.build_checkout(self.repo.resolve(), out, published.extract(Path(extracted)), "/", "owner/example")

        site = serve.Site(build, lambda: (serve.fingerprint([self.repo / "docs"]), published.version()))
        self.sites.append(site)
        site.refresh(force=True)

        def reviews() -> list[tuple[str, int]]:
            manifest = json.loads(self.fetch(site, "/", "/site.json")[1])
            return [(review["head"], review["heads"]) for review in manifest["reviews"]]

        self.assertEqual([], reviews(), "nothing is fetched yet")
        self.assertIsNone(site.refresh(), "no change, no rebuild")

        self.assertEqual("", published.fetch())
        self.assertIsNotNone(site.refresh(), "the ref that appeared after startup is a change")
        self.assertEqual([("d" * 40, 2)], reviews())

        run_git(remote, "update-ref", summary.PAGES_REF, "HEAD~1")
        self.assertEqual("", published.fetch())
        self.assertIsNotNone(site.refresh(), "a fetched ref that moved is a change")
        self.assertEqual([("c" * 40, 1)], reviews())

    def test_a_served_site_shows_a_summary_published_after_the_server_started(self) -> None:
        self.published_remote()
        env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).parents[1]))
        process = subprocess.Popen(
            [sys.executable, "-m", "projector", "site", "serve", "--port", "0", "--no-fetch",
             "--repo-root", str(self.repo)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            url = ""
            for line in process.stdout:
                if line.startswith("serving http://"):
                    url = line.split()[1].rstrip(";")
                    break

            def reviews() -> list:
                with urllib.request.urlopen(url + "site.json") as response:
                    return json.loads(response.read())["reviews"]

            self.assertEqual([], reviews())
            # `summary publish` updates the same local copy of the remote's ref.
            self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
            for _ in range(100):
                if reviews():
                    break
                time.sleep(0.1)
            self.assertEqual(["d" * 40], [review["head"] for review in reviews()])
        finally:
            process.terminate()
            process.communicate(timeout=10)

    def test_the_background_fetch_cannot_prompt_on_the_servers_terminal(self) -> None:
        published = serve.Summaries(self.repo, "origin")
        calls: list[dict] = []

        def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
            calls.append(options)
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with mock.patch.object(serve.subprocess, "run", side_effect=run):
            published.fetch()
            published.fetch_quietly()

        startup, background = calls
        self.assertNotIn("start_new_session", startup, "the startup fetch may still prompt whoever started the server")
        # A session of its own has no terminal, so an ssh git starts cannot ask for a passphrase either.
        self.assertTrue(background.get("start_new_session"))
        self.assertIs(subprocess.DEVNULL, background.get("stdin"))
        self.assertEqual("0", background["env"]["GIT_TERMINAL_PROMPT"])

    def test_keep_fetching_reports_a_failure_once_and_again_only_when_it_changes(self) -> None:
        stop = threading.Event()
        reasons = iter(["couldn't find remote ref", "", "couldn't find remote ref", "offline", "offline"])
        reported: list[str] = []

        def fetch() -> str:
            reason = next(reasons, None)
            if reason is None:
                stop.set()
                return ""
            return reason

        serve.keep_fetching(fetch, stop, 0, reported.append, last="couldn't find remote ref")

        self.assertEqual(["could not fetch summaries: couldn't find remote ref", "could not fetch summaries: offline"],
                         reported, "the startup failure is not repeated, and a recovery resets it")

    @unittest.skipIf(os.name == "nt", "SIGINT is how a terminal stops the server on POSIX")
    def test_ctrl_c_or_a_termination_signal_stops_the_server_and_removes_its_builds(self) -> None:
        for stop in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=stop.name):
                scratch = Path(tempfile.mkdtemp())
                env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).parents[1]), TMPDIR=str(scratch))
                process = subprocess.Popen(
                    [sys.executable, "-m", "projector", "site", "serve", "--port", "0", "--no-fetch",
                     "--repo-root", str(self.repo)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                try:
                    for line in process.stdout:
                        if line.startswith("serving http://127.0.0.1:"):
                            break
                    self.assertTrue(any(scratch.glob("projector-site-*")))
                    process.send_signal(stop)
                    _, err = process.communicate(timeout=10)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

                self.assertEqual(0, process.returncode, err)
                self.assertEqual([], list(scratch.glob("projector-site-*")))


@unittest.skipUnless(shutil.which("node"), "the route test needs node")
class RouteTests(SiteRepoCase):
    def routes(self) -> list[str]:
        """Build the site and return routeFor, run under node, of every file it serves."""
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo), "--base", "projector"])
        manifest = json.loads((self.out / "site.json").read_text())
        paths = [manifest["readme"], manifest["projectsReadme"], *(d["path"] for d in manifest["docs"])]
        for project in manifest["projects"]:
            paths += [project["path"], *(f["path"] for f in project["files"])]
        program = f"var site = {json.dumps(manifest)}, base = '/projector/';\n" + \
            "\n".join(js_function(n) for n in ("esc", "repoUrl", "routeFor")) + \
            f"\nprocess.stdout.write(JSON.stringify({json.dumps(paths)}.map(routeFor)));"
        return json.loads(subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout)

    def test_every_link_the_page_makes_to_a_file_has_a_shell(self) -> None:
        (self.repo / "docs/README.md").write_text("# About these docs\n")

        routes = self.routes()

        self.assertEqual(
            ["/projector/docs/", "/projector/projects/", "/projector/docs/readme/", "/projector/docs/guide/",
             "/projector/projects/alpha/", "/projector/projects/alpha/notes/",
             "/projector/projects/alpha/research/survey/", "/projector/projects/alpha/beta/"],
            routes,
        )
        for route in routes:
            self.assertTrue((self.out / route.removeprefix("/projector/") / "index.html").is_file(), route)

    def test_a_file_named_like_a_folder_keeps_a_route_of_its_own(self) -> None:
        # alpha/beta.md beside the nested project alpha/beta/, and guide.md beside guide/readme.md.
        (self.repo / "docs/projects/alpha/beta.md").write_text("# Beta file\n")
        (self.repo / "docs/guide").mkdir()
        (self.repo / "docs/guide/readme.md").write_text("# Guide folder\n")

        routes = self.routes()

        self.assertEqual(len(routes), len(set(routes)), routes)
        self.assertIn("/projector/projects/alpha/beta/", routes)
        self.assertIn("/projector/projects/alpha/beta.md/", routes)
        self.assertIn("/projector/docs/guide/", routes)
        self.assertIn("/projector/docs/guide.md/", routes)
        for route in routes:
            self.assertTrue((self.out / route.removeprefix("/projector/") / "index.html").is_file(), route)


class PrepareTests(SiteRepoCase):
    MAKE = "mkdir -p docs/made && printf '<title>{title}</title>' > docs/made/{name}.html"

    def setUp(self) -> None:
        super().setUp()
        self.home = Path(tempfile.mkdtemp())

    def configure(self, prepare: object) -> None:
        (self.repo / ".projector.toml").write_text(f"[site]\nprepare = {json.dumps(prepare)}\n")

    def build(self, *flags: str, ci: bool = False) -> tuple[int, dict, str]:
        err = io.StringIO()
        environ = {"GITHUB_REPOSITORY": "owner/example", "HOME": str(self.home),
                   "XDG_STATE_HOME": str(self.home / "state"), "GITHUB_ACTIONS": "true" if ci else ""}
        with mock.patch.dict(os.environ, environ), redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo), *flags])
        manifest = json.loads((self.out / "site.json").read_text()) if (self.out / "site.json").exists() else {}
        return code, manifest, err.getvalue()

    def made(self, manifest: dict) -> dict:
        return {d["path"]: d["title"] for d in manifest["docs"] if d["path"].startswith("docs/made/")}

    def test_a_checkout_s_command_is_skipped_until_it_is_allowed(self) -> None:
        self.configure(self.MAKE.format(title="Made", name="page"))

        code, skipped, err = self.build()

        self.assertEqual(0, code, "the site still builds, without what the command would have made")
        self.assertEqual({}, self.made(skipped))
        self.assertIn("site.prepare is not allowed for this checkout", err)
        self.assertIn("mkdir -p docs/made", err, "the skipped command is shown, so the reader can judge it")
        self.assertIn("--allow-prepare", err)

    def test_an_allowed_command_runs_and_stays_allowed_until_it_changes(self) -> None:
        self.configure(self.MAKE.format(title="Made", name="page"))

        _, allowed, err = self.build("--allow-prepare")
        shutil.rmtree(self.repo / "docs/made")
        _, remembered, _ = self.build()
        shutil.rmtree(self.repo / "docs/made")
        self.configure(self.MAKE.format(title="Changed", name="changed"))
        _, changed, changed_err = self.build()

        self.assertEqual({"docs/made/page.html": "Made"}, self.made(allowed))
        self.assertIn("preparing the site: mkdir -p docs/made", err, "a command from the repository is named first")
        self.assertEqual({"docs/made/page.html": "Made"}, self.made(remembered))
        self.assertEqual({}, self.made(changed), "a changed command is a new command to allow")
        self.assertIn("site.prepare is not allowed for this checkout", changed_err)
        self.assertNotIn(str(self.repo), (self.home / "state/projector/allowed-prepare").read_text(),
                         "the record holds hashes, not the paths and commands themselves")

    def test_a_deploy_runs_the_merged_command_unasked(self) -> None:
        self.configure(self.MAKE.format(title="Made", name="page"))

        _, manifest, _ = self.build(ci=True)

        self.assertEqual({"docs/made/page.html": "Made"}, self.made(manifest))

    def test_the_flags_replace_or_skip_site_prepare(self) -> None:
        self.configure(self.MAKE.format(title="Configured", name="configured"))
        given = self.MAKE.format(title="Given", name="given")

        _, replaced, _ = self.build("--prepare", given)
        shutil.rmtree(self.repo / "docs/made")
        _, skipped, err = self.build("--no-prepare", "--allow-prepare")

        self.assertEqual({"docs/made/given.html": "Given"}, self.made(replaced),
                         "a command given on the command line needs no allowing")
        self.assertEqual({}, self.made(skipped))
        self.assertNotIn("preparing the site", err)

    def test_a_failing_prepare_command_stops_the_build(self) -> None:
        self.configure("echo half-made; exit 3")

        code, manifest, err = self.build("--allow-prepare")

        self.assertEqual(65, code)
        self.assertIn("the prepare command exited with status 3: echo half-made; exit 3", err)
        self.assertEqual({}, manifest, "nothing is built from a checkout the command left half-prepared")

    def test_site_prepare_must_be_a_string(self) -> None:
        self.configure(["make", "docs"])

        code, _, err = self.build()

        self.assertNotEqual(0, code)
        self.assertIn("site.prepare must be a string, not list", err)

    def test_serving_reruns_prepare_on_an_edit_but_not_on_what_it_generated(self) -> None:
        runs = []

        def prepare() -> None:
            runs.append(1)
            # A command that rewrites its output every time, as many generators do.
            (self.repo / "docs/generated.html").write_text(f"<title>Run {len(runs)}</title>")

        site = serve.Site(lambda out: out.mkdir(parents=True) or "built",
                          lambda: serve.fingerprint([self.repo / "docs"]), prepare)
        self.addCleanup(site.close)

        first = site.refresh(force=True)
        quiet = site.refresh()
        (self.repo / "docs/guide.md").write_text("# Use the guide\n\nEdited.\n")
        edited = site.refresh()

        self.assertEqual(("built", None, "built"), (first, quiet, edited))
        self.assertEqual(2, len(runs), "the file prepare wrote did not count as an edit")


class HtmlPageTests(SiteRepoCase):
    def setUp(self) -> None:
        super().setUp()
        files = {
            "docs/explorer/README.md": "# Explorer\n",
            "docs/explorer/explorer.html": "<!doctype html><title>Orders &amp; wire</title>"
                                           "<script>var secret = 'zzz';</script><h1>Wire map</h1>",
            "docs/explorer/template.html.in": "<title>__TITLE__</title>",
            "docs/demo/index.html": "<title>Demo</title><p>Try it.</p>",
            "docs/projects/alpha/playground/readme.md": plan("Name playground", "completed", None),
            "docs/projects/alpha/playground/index.html": "<title>Playground</title>",
            "docs/projects/alpha/playground/cases.json": "[]",
        }
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        (self.repo / "docs/projects/alpha/playground/dist").mkdir()
        (self.repo / "docs/projects/alpha/playground/dist/app.wasm").write_bytes(b"\0asm\1\0\0\0")

    def build(self) -> dict:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo)]))
        return json.loads((self.out / "site.json").read_text())

    def test_an_html_page_is_a_doc_with_its_title_and_a_route(self) -> None:
        manifest = self.build()

        docs = {d["path"]: (d["title"], d["route"]) for d in manifest["docs"]}
        self.assertEqual(("Orders & wire", "docs/explorer/explorer/"), docs["docs/explorer/explorer.html"])
        self.assertEqual(("Explorer", "docs/explorer/"), docs["docs/explorer/README.md"])
        self.assertEqual(("Demo", "docs/demo/"), docs["docs/demo/index.html"], "an index.html takes a free folder route")
        self.assertNotIn("docs/explorer/template.html.in", docs, "only .md and .html files are pages")
        for route in ("docs/explorer/explorer/", "docs/explorer/", "docs/demo/"):
            self.assertTrue((self.out / route / "index.html").is_file(), route)
        self.assertEqual((self.repo / "docs/explorer/explorer.html").read_bytes(),
                         (self.out / "content/docs/explorer/explorer.html").read_bytes(), "the frame loads this copy")

    def test_a_playground_in_a_project_is_served_with_the_files_it_loads(self) -> None:
        manifest = self.build()

        playground = next(p for p in manifest["projects"] if p["name"] == "alpha/playground")
        self.assertEqual([{"path": "docs/projects/alpha/playground/index.html", "title": "Playground",
                           "route": "projects/alpha/playground/index/"}], playground["files"],
                         "the readme keeps the folder's route, so the index moves to index/")
        self.assertTrue((self.out / "projects/alpha/playground/index/index.html").is_file())
        for path in ("docs/projects/alpha/playground/cases.json", "docs/projects/alpha/playground/dist/app.wasm",
                     "docs/explorer/template.html.in"):
            self.assertIn(path, manifest["files"])
            self.assertEqual((self.repo / path).read_bytes(), (self.out / "content" / path).read_bytes(), path)

    def test_search_reads_an_html_page_s_visible_text(self) -> None:
        self.build()

        index = {e["route"]: e for e in json.loads((self.out / "search.json").read_text())}
        page = index["docs/explorer/explorer/"]
        self.assertEqual(("Orders & wire", "doc"), (page["title"], page["kind"]))
        self.assertIn("Wire map", page["text"])
        self.assertNotIn("zzz", page["text"], "a script is not searchable text")


class FakeGitHub:
    """The slice of the GitHub API `init` uses to set up the site, for one repository."""

    def __init__(self, private: bool = False, pages: dict | None = None, private_pages: bool = True,
                 admin: bool = True, homepage: str = "") -> None:
        self.private, self.pages, self.private_pages = private, pages, private_pages
        self.admin, self.homepage = admin, homepage
        self.calls: list[tuple[str, ...]] = []

    def lookup(self, *args: str) -> str | None:
        self.calls.append(args)
        endpoint = args[1]
        if endpoint == "repos/owner/example/pages":
            return None if self.pages is None else json.dumps(self.pages)
        if endpoint == "repos/owner/example":
            return "true\n" if self.private else "false\n"
        raise summary.SummaryError(f"unexpected lookup {args}")

    def gh(self, *args: str) -> str:
        self.calls.append(args)
        if args == ("api", "repos/owner/example/pages"):
            return json.dumps(self.pages)
        if args == ("api", "repos/owner/example"):
            return json.dumps({"private": self.private, "homepage": self.homepage or None,
                               "permissions": {"admin": self.admin, "push": True, "pull": True}})
        if not self.admin:
            raise summary.SummaryError("gh api failed: HTTP 403 Must have admin rights to Repository.")
        if args == ("api", "-X", "DELETE", "repos/owner/example/pages"):
            self.pages = None
            return ""
        method, endpoint, _, field = args[2], args[3], args[4], args[5]
        if (method, endpoint) == ("POST", "repos/owner/example/pages"):
            self.pages = {"build_type": "workflow", "public": True,
                          "html_url": "https://owner.github.io/example/"}
        elif (method, endpoint, field) == ("PUT", "repos/owner/example/pages", "build_type=workflow"):
            self.pages["build_type"] = "workflow"
        elif (method, endpoint, field) == ("PUT", "repos/owner/example/pages", "public=false"):
            if not self.private_pages:
                raise summary.SummaryError("gh api failed: HTTP 422 private Pages are not available")
            self.pages["public"] = False
            # GitHub serves a private site at its own subdomain unless it has a custom domain.
            if not self.pages.get("cname"):
                self.pages["html_url"] = "https://example-private.pages.github.io/"
        elif (method, endpoint) == ("PUT", "repos/owner/example/pages") and field.startswith("cname="):
            # GitHub reports a custom domain over http:// until it enforces HTTPS.
            self.pages["cname"] = field.removeprefix("cname=")
            self.pages["html_url"] = f"http://{self.pages['cname']}/"
        elif (method, endpoint) == ("PATCH", "repos/owner/example") and field.startswith("homepage="):
            self.homepage = field.removeprefix("homepage=")
        else:
            raise summary.SummaryError(f"unexpected call {args}")
        return "{}"

    def writes(self) -> list[tuple[str, ...]]:
        return [call[2:] for call in self.calls if "-X" in call]


SITE_READY = {"build_type": "workflow", "public": True, "html_url": "https://owner.github.io/example/"}


class InitSiteTests(SiteRepoCase):
    def setUp(self) -> None:
        super().setUp()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.origin("https://github.com/owner/example.git")

    def origin(self, url: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", url], check=True)

    def init(self, github: FakeGitHub, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(summary, "gh_lookup", side_effect=github.lookup), \
             mock.patch.object(summary, "gh", side_effect=github.gh), \
             mock.patch.object(cli.shutil, "which", return_value="/usr/bin/gh"), \
             redirect_stdout(out), redirect_stderr(err):
            # The site is under test; the Claude Code settings step has its own tests.
            code = cli.main(["--root", str(self.repo), "init", "--no-publish-rule", *extra])
        return code, out.getvalue(), err.getvalue()

    def workflow(self) -> Path:
        return self.repo / summary.WORKFLOW_PATH

    def test_sets_up_the_site_by_default_then_changes_nothing_on_a_rerun(self) -> None:
        github = FakeGitHub()

        code, out, err = self.init(github)

        self.assertEqual(0, code, err)
        self.assertEqual([("POST", "repos/owner/example/pages", "-f", "build_type=workflow"),
                          ("PATCH", "repos/owner/example", "-f", "homepage=https://owner.github.io/example")],
                         github.writes())
        self.assertIn(f"created {summary.WORKFLOW_PATH}\n", out)
        self.assertIn("created GitHub Pages site https://owner.github.io/example/\n", out)
        self.assertIn("updated repository website https://owner.github.io/example\n", out)
        self.assertIn("through a pull request", err)
        self.assertEqual(summary.workflow_text("v0", "main"), self.workflow().read_text())

        github.calls.clear()
        code, out, _ = self.init(github, "--json")

        self.assertEqual(0, code)
        self.assertEqual([], github.writes())
        report = json.loads(out)
        self.assertIn({"path": summary.WORKFLOW_PATH, "action": "unchanged"}, report["files"])
        self.assertEqual({"pages": "unchanged", "visibility": "unchanged", "url": "https://owner.github.io/example/",
                          "public": True, "website": "unchanged"}, report["site"])

    def test_init_updates_a_workflow_projector_wrote_in_an_earlier_shape(self) -> None:
        self.workflow().parent.mkdir(parents=True)
        self.workflow().write_text(summary.workflow_text("v0", "main").replace(
            ", .projector.toml, .github/workflows/projector-site.yml", ""))

        code, out, err = self.init(FakeGitHub(pages=SITE_READY))

        self.assertEqual(0, code, err)
        self.assertIn(f"updated {summary.WORKFLOW_PATH}\n", out)
        self.assertEqual(summary.workflow_text("v0", "main"), self.workflow().read_text())

    def test_init_keeps_a_workflow_edited_by_hand(self) -> None:
        # A workflow that sets up a toolchain for site.prepare, as a repository
        # with a generated explorer needs, must survive a rerun of init.
        edited = summary.workflow_text("v0", "main").replace(
            "    steps:\n", "    steps:\n      - uses: actions/setup-python@v5\n")
        self.workflow().parent.mkdir(parents=True)
        self.workflow().write_text(edited)

        code, out, err = self.init(FakeGitHub(pages=SITE_READY))

        self.assertEqual(0, code, err)
        self.assertEqual(edited, self.workflow().read_text())
        self.assertIn(f"kept {summary.WORKFLOW_PATH}", out)
        self.assertIn("left alone", out + err)

    def test_writing_the_workflow_refuses_a_hand_edit_until_forced(self) -> None:
        edited = "# Deploys the docs and the explorer.\n" + summary.workflow_text("v0", "main")
        self.workflow().parent.mkdir(parents=True)
        self.workflow().write_text(edited)

        def write(*extra: str) -> tuple[int, str]:
            err = io.StringIO()
            with mock.patch.object(cli.Path, "cwd", return_value=self.repo), \
                 redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = cli.main(["site", "workflow", "--write", *extra])
            return code, err.getvalue()

        code, err = write()
        self.assertEqual(1, code)
        self.assertIn("--write --force", err)
        self.assertEqual(edited, self.workflow().read_text())

        code, err = write("--force")
        self.assertEqual(0, code, err)
        self.assertEqual(summary.workflow_text("v0", "main"), self.workflow().read_text())

    def test_no_site_or_the_config_key_leaves_github_alone_and_site_overrides_the_key(self) -> None:
        github = FakeGitHub()

        self.assertEqual(0, self.init(github, "--no-site")[0])
        (self.repo / ".projector.toml").write_text("[site]\nenabled = false\n")
        self.assertEqual(0, self.init(github)[0])

        self.assertEqual([], github.calls)
        self.assertFalse(self.workflow().exists())
        self.assertEqual(0, self.init(github, "--site")[0])
        self.assertTrue(self.workflow().exists())

    def test_a_repository_not_on_github_is_adopted_quietly(self) -> None:
        github = FakeGitHub()
        self.origin("https://gitlab.com/owner/example.git")

        code, out, err = self.init(github)

        self.assertEqual(0, code)
        self.assertEqual("", err, "a repository not on GitHub has no site to miss")
        self.assertIn("created AGENTS.md", out)
        self.assertEqual([], github.calls)
        self.assertFalse(self.workflow().exists())
        self.assertEqual(65, self.init(github, "--site")[0], "--site requires the site")

    def test_keeps_a_repositorys_own_branch_site_unless_site_asks_to_replace_it(self) -> None:
        github = FakeGitHub(pages=dict(SITE_READY, build_type="legacy"), homepage="https://docs.example.com")

        code, _, err = self.init(github)

        self.assertEqual(0, code)
        self.assertEqual([], github.writes())
        self.assertIn("already serves its own GitHub Pages site from a branch; pass --site", err)
        self.assertFalse(self.workflow().exists())

        code, out, _ = self.init(github, "--site", "--action-ref", "v0.5.5")

        self.assertEqual(0, code)
        self.assertEqual([("PUT", "repos/owner/example/pages", "-f", "build_type=workflow")], github.writes())
        self.assertIn("updated GitHub Pages site", out)
        self.assertIn("uses: ninjudd/projector/actions/site@v0.5.5", self.workflow().read_text())

    def test_keeps_a_site_another_workflow_deploys_unless_site_asks_to_add_one(self) -> None:
        github = FakeGitHub(pages=SITE_READY)
        docs = self.repo / ".github/workflows/docs.yml"
        docs.parent.mkdir(parents=True)
        docs.write_text("jobs:\n  deploy:\n    steps:\n      - uses: actions/deploy-pages@v4\n")

        code, _, err = self.init(github)

        self.assertEqual(0, code)
        self.assertEqual([], github.calls, "a deployer in the checkout is found before asking GitHub")
        self.assertIn(".github/workflows/docs.yml already deploys a GitHub Pages site; pass --site", err)
        self.assertFalse(self.workflow().exists())

        self.assertEqual(0, self.init(github, "--site")[0])
        self.assertTrue(self.workflow().exists())
        self.assertEqual(0, self.init(github)[0], "Projector's own workflow is not another deployer")

    def test_makes_a_private_repositorys_site_private(self) -> None:
        github = FakeGitHub(private=True)

        code, out, _ = self.init(github)

        self.assertEqual(0, code)
        self.assertIn(("PUT", "repos/owner/example/pages", "-F", "public=false"), github.writes())
        self.assertFalse(github.pages["public"])
        self.assertIn("updated GitHub Pages visibility: private\n", out)

    def test_writes_no_workflow_when_a_private_repositorys_site_would_stay_public(self) -> None:
        for flags, expected in (((), 0), (("--site",), 65)):
            with self.subTest(flags=flags):
                github = FakeGitHub(private=True, private_pages=False)

                code, out, err = self.init(github, *flags)

                self.assertEqual(expected, code)
                self.assertIn("owner/example is private but GitHub will not make its Pages site private", err)
                self.assertIn("project site serve", err)
                self.assertIn("AGENTS.md", out, "what init wrote is still reported")
                self.assertFalse(self.workflow().exists())
                self.assertIsNone(github.pages, "the public site init created is deleted")
                self.assertIn("the public Pages site init created was deleted", err)

    def test_never_deletes_a_public_site_it_did_not_create(self) -> None:
        github = FakeGitHub(private=True, private_pages=False, pages=SITE_READY)

        code, _, err = self.init(github)

        self.assertEqual(0, code)
        self.assertEqual([("PUT", "repos/owner/example/pages", "-F", "public=false")], github.writes())
        self.assertEqual(SITE_READY, github.pages)
        self.assertNotIn("was deleted", err)
        self.assertFalse(self.workflow().exists())

    def test_keeps_a_website_link_that_points_elsewhere(self) -> None:
        for homepage, action in (("http://owner.github.io/example", "unchanged"), ("https://example.com", "kept")):
            with self.subTest(homepage=homepage):
                github = FakeGitHub(pages=SITE_READY, homepage=homepage)

                code, out, err = self.init(github, "--json")

                self.assertEqual(0, code)
                self.assertEqual([], github.writes())
                self.assertEqual(action, json.loads(out)["site"]["website"])
                if action == "kept":
                    self.assertIn("already links its website to https://example.com", err)
                    self.assertIn("set it to https://owner.github.io/example to link the site", err)

    def test_links_a_custom_domain_website_without_a_trailing_slash(self) -> None:
        github = FakeGitHub(pages=dict(SITE_READY, html_url="https://docs.example.com/", https_enforced=True))

        code, out, err = self.init(github)

        self.assertEqual(0, code, err)
        self.assertEqual([("PATCH", "repos/owner/example", "-f", "homepage=https://docs.example.com")], github.writes())
        self.assertEqual("https://docs.example.com", github.homepage)
        self.assertIn("updated repository website https://docs.example.com\n", out)

    def test_writes_the_workflow_for_a_non_admin_whether_or_not_the_site_is_set_up(self) -> None:
        github = FakeGitHub(admin=False, pages=SITE_READY)

        code, out, err = self.init(github)

        self.assertEqual(0, code)
        self.assertEqual([], github.writes())
        self.assertIn(f"created {summary.WORKFLOW_PATH}", out)
        self.assertIn("you are not an admin of owner/example, so its website does not link", err)
        self.assertIn("ask an admin to run `gh api -X PATCH repos/owner/example -f "
                      "homepage=https://owner.github.io/example`", err)

        self.workflow().unlink()
        for flags, expected in (((), 0), (("--site",), 65)):
            with self.subTest(flags=flags):
                github = FakeGitHub(admin=False)

                code, out, err = self.init(github, *flags)

                self.assertEqual(expected, code)
                self.assertEqual([], github.writes(), "nothing is attempted without admin")
                self.assertIn("you are not an admin of owner/example, so init cannot turn on its GitHub Pages site", err)
                self.assertIn(f"created {summary.WORKFLOW_PATH}", out, "the workflow comes with the admin's commands")
                self.workflow().unlink()

    def run_as_admin(self, github: FakeGitHub, commands: list[str]) -> None:
        """Run `commands` in a shell, as the admin would, applying each one's calls to `github` before the next."""
        stub = Path(tempfile.mkdtemp())
        log = stub / "calls"
        # The stub logs each call one argument per line and answers the lookup
        # for the site's address that the website link reads.
        (stub / "gh").write_text('#!/bin/sh\nprintf "%s\\n" "$@" "" >> "$GH_LOG"\n'
                                 '[ "$2" = repos/owner/example/pages ] && printf "%s\\n" "$SITE_URL"\nexit 0\n')
        (stub / "gh").chmod(0o755)
        github.admin = True
        for command in commands:
            log.write_text("")
            site = (github.pages or {}).get("html_url", "").rstrip("/")
            env = dict(os.environ, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", GH_LOG=str(log), SITE_URL=site)
            subprocess.run(["bash", "-c", command], check=True, env=env)
            for record in log.read_text().split("\n\n")[:-1]:
                args = tuple(record.split("\n"))
                if "-X" in args:
                    github.gh(*args)
                else:
                    self.assertEqual(("api", "repos/owner/example/pages", "--jq", '.html_url | rtrimstr("/")'), args)
        github.admin, github.calls = False, []

    @unittest.skipIf(shutil.which("bash") is None, "the admin runs the commands in a shell")
    def test_hands_a_non_admin_the_commands_that_finish_the_site(self) -> None:
        create = "gh api -X POST repos/owner/example/pages -f build_type=workflow"
        switch = "gh api -X PUT repos/owner/example/pages -f build_type=workflow"
        private = "gh api -X PUT repos/owner/example/pages -F public=false"
        # Without --url the website command reads the address back, because
        # making a site private moves it to a subdomain of its own.
        link = ("gh api -X PATCH repos/owner/example -f homepage=\"$(gh api repos/owner/example/pages "
                "--jq '.html_url | rtrimstr(\"/\")')\"")
        domain = "gh api -X PUT repos/owner/example/pages -f cname=projects.example.com"
        link_domain = "gh api -X PATCH repos/owner/example -f homepage=https://projects.example.com"
        vercel = "https://example.vercel.app"
        cases = (
            (dict(), (), [create, link]),
            (dict(private=True), (), [create, private, link]),
            (dict(homepage="https://example.com"), (), [create]),
            (dict(pages=dict(SITE_READY, build_type="legacy")), ("--site",), [switch, link]),
            (dict(private=True, pages=dict(SITE_READY)), (), [private, link]),
            (dict(private=True, homepage=vercel), ("--url", "projects.example.com"),
             [create, private, domain, link_domain]),
            (dict(pages=dict(SITE_READY), homepage=vercel), ("--url", "https://projects.example.com/"),
             [domain, link_domain]),
        )
        for options, flags, commands in cases:
            with self.subTest(options=options, flags=flags):
                self.workflow().unlink(missing_ok=True)
                github = FakeGitHub(admin=False, **options)

                code, out, err = self.init(github, "--json", *flags)

                self.assertEqual(65 if "--site" in flags else 0, code)
                self.assertEqual([], github.writes(), "nothing is attempted without admin")
                self.assertIn("ask an admin to run these commands; the site deploys once they have and the site "
                              "workflow is on the default branch:", err)
                self.assertIn("".join(f"\n    {command}" for command in commands), err)
                self.assertNotIn("project init", err, "the workflow is written now, so no second init is needed")
                if "--site" not in flags:
                    self.assertEqual(commands, json.loads(out)["site"]["admin_commands"])
                    self.assertIn({"path": summary.WORKFLOW_PATH, "action": "created"}, json.loads(out)["files"])
                self.assertTrue(self.workflow().exists())

                self.run_as_admin(github, commands)
                code, out, err = self.init(github, *flags)

                self.assertEqual(0, code, err)
                self.assertEqual([], github.writes(), "the rerun needs no admin")
                self.assertIn(f"unchanged {summary.WORKFLOW_PATH}", out)
                self.assertNotIn("not an admin", err)
                self.assertEqual("workflow", github.pages["build_type"])
                self.assertEqual(not github.private, github.pages["public"])
                if "--url" in flags:
                    self.assertEqual("projects.example.com", github.pages["cname"])
                    self.assertEqual("https://projects.example.com", github.homepage)
                else:
                    self.assertEqual(options.get("homepage", github.pages["html_url"].rstrip("/")), github.homepage)

    def test_serves_the_site_at_the_url_init_is_given_and_links_the_website_to_it(self) -> None:
        github = FakeGitHub(private=True, homepage="https://example.vercel.app")

        code, out, err = self.init(github, "--url", "projects.example.com")

        self.assertEqual(0, code, err)
        self.assertEqual([
            ("POST", "repos/owner/example/pages", "-f", "build_type=workflow"),
            ("PUT", "repos/owner/example/pages", "-F", "public=false"),
            ("PUT", "repos/owner/example/pages", "-f", "cname=projects.example.com"),
            ("PATCH", "repos/owner/example", "-f", "homepage=https://projects.example.com"),
        ], github.writes(), "the domain is set once the site is private, and the website link is replaced")
        self.assertIn("created GitHub Pages site https://projects.example.com/\n", out)
        self.assertIn("updated repository website https://projects.example.com\n", out)
        self.assertTrue(self.workflow().exists())

        github.calls = []
        code, out, _ = self.init(github, "--url", "https://projects.example.com")

        self.assertEqual(0, code)
        self.assertEqual([], github.writes(), "a rerun changes nothing")
        self.assertIn("unchanged GitHub Pages site https://projects.example.com/\n", out)

    def test_refuses_a_url_that_is_not_a_custom_domains_address(self) -> None:
        for url in ("https://example.com/docs", "ftp://example.com", "localhost", "https://example.com:8443",
                    "https://user@example.com", "https://example.com/?q=1", "projects.example.com;touch pwned",
                    "projects example.com", "$(id).example.com", "projects-.example.com", "projects..example.com",
                    "projects_site.example.com"):
            with self.subTest(url=url):
                github = FakeGitHub()

                code, _, err = self.init(github, "--url", url)

                self.assertEqual(2, code)
                self.assertIn("--url must be a custom domain's address", err)
                self.assertEqual([], github.calls)
                self.assertFalse((self.repo / "AGENTS.md").exists(), "nothing is written")

        code, _, err = self.init(FakeGitHub(), "--url", "projects.example.com", "--no-site")

        self.assertEqual(2, code)
        self.assertIn("--url sets the site's address", err)

    def test_reads_a_url_without_a_scheme_as_https(self) -> None:
        self.assertEqual("https://projects.example.com/", cli.site_address("projects.example.com"))
        self.assertEqual("https://projects.example.com/", cli.site_address("https://Projects.Example.com/"))
        self.assertEqual("http://projects.example.com/", cli.site_address("http://projects.example.com"))


class WorkflowTests(unittest.TestCase):
    def test_the_action_hands_its_prepare_input_to_the_build_after_its_checkout(self) -> None:
        action = (Path(__file__).parents[1] / "actions" / "site" / "action.yml").read_text()

        self.assertIn("  prepare:\n", action)
        self.assertIn("PREPARE: ${{ inputs.prepare }}", action)
        checkout, build = action.index("actions/checkout"), action.index("site build")
        self.assertLess(checkout, build, "the checkout would remove what the command generated")
        self.assertIn('${PREPARE:+--prepare "$PREPARE"}', action[build:action.index("upload-pages-artifact")])

    def fetch_step(self) -> str:
        """The shell script of the action's step that fetches the summaries."""
        lines = (Path(__file__).parents[1] / "actions" / "site" / "action.yml").read_text().splitlines()
        start = lines.index("    - name: Fetch the summaries")
        start = lines.index("      run: |", start) + 1
        end = next(i for i in range(start, len(lines)) if not lines[i].startswith("        "))
        return "\n".join(line[8:] for line in lines[start:end]) + "\n"

    @unittest.skipIf(shutil.which("bash") is None, "the action's steps run in bash")
    def test_the_action_reads_the_old_walkthroughs_ref_while_the_summaries_ref_does_not_exist(self) -> None:
        script = self.fetch_step()
        for refs, expected in (
            ((summary.LEGACY_PAGES_REF,), summary.LEGACY_PAGES_ROOT),
            ((summary.PAGES_REF, summary.LEGACY_PAGES_REF), summary.PAGES_ROOT),
            ((summary.PAGES_REF,), summary.PAGES_ROOT),
            ((), None),
        ):
            with self.subTest(refs=refs):
                tmp = Path(tempfile.mkdtemp())
                remote = tmp / "remote"
                run_git(tmp, "init", "--quiet", str(remote))
                (remote / "README.md").write_text("main\n")
                run_git(remote, "add", ".")
                run_git(remote, "commit", "--quiet", "-m", "main")
                for ref in refs:
                    root = summary.PAGES_ROOT if ref == summary.PAGES_REF else summary.LEGACY_PAGES_ROOT
                    def git_in(*args: str, text: str) -> str:
                        return subprocess.run(["git", *args], cwd=remote, check=True, capture_output=True, text=True,
                                              input=text).stdout.strip()

                    blob = git_in("hash-object", "-w", "--stdin", text="summary\n")
                    tree = git_in("mktree", text=f"100644 blob {blob}\t{root}\n")
                    run_git(remote, "update-ref", ref, run_git(remote, "commit-tree", tree, "-m", root))
                checkout = tmp / "checkout"
                run_git(tmp, "clone", "--quiet", str(remote), str(checkout))
                env_file = tmp / "github_env"
                env_file.write_text("")
                env = dict(os.environ, REF=summary.PAGES_REF, ROOT=summary.PAGES_ROOT, RUNNER_TEMP=str(tmp),
                           GITHUB_ENV=str(env_file))
                subprocess.run(["bash", "-e", "-c", script], cwd=checkout, env=env, check=True, capture_output=True)

                written = env_file.read_text()
                if expected is None:
                    self.assertEqual("", written, "no ref: the site builds without reviews")
                else:
                    self.assertEqual(f"SUMMARIES={tmp}/summaries-ref/{expected}\n", written)
                    self.assertTrue((tmp / "summaries-ref" / expected).is_file())

    def test_the_workflow_listens_only_for_the_summaries_event(self) -> None:
        text = summary.workflow_text("v0", "main")
        self.assertIn("    types: [projector-summaries]\n", text)
        self.assertNotIn(summary.LEGACY_DISPATCH_EVENT, text)

    def test_docs_changes_on_the_default_branch_rebuild_the_site(self) -> None:
        text = summary.workflow_text("v0", "trunk")
        self.assertIn("  push:\n    branches: [trunk]\n    paths: [README.md, 'docs/**', .projector.toml, .github/workflows/projector-site.yml]\n", text)
        self.assertIn("uses: ninjudd/projector/actions/site@v0", text)

    def test_a_projects_directory_outside_docs_is_watched_too(self) -> None:
        self.assertIn("paths: [README.md, 'docs/**', 'plans/**', .projector.toml, .github/workflows/projector-site.yml]", summary.workflow_text("v0", "main", "plans"))
        self.assertIn("paths: [README.md, 'docs/**', .projector.toml, .github/workflows/projector-site.yml]\n", summary.workflow_text("v0", "main", "docs/projects"))

    def test_merging_the_workflow_or_changing_projector_toml_rebuilds_the_site(self) -> None:
        # The first merge adds only the workflow, so without its own path the site
        # waits for the next docs change before it deploys at all.
        paths = summary.workflow_text("v0", "main").split("    paths: [", 1)[1].split("]", 1)[0].split(", ")

        self.assertIn(summary.WORKFLOW_PATH, paths)
        self.assertIn(".projector.toml", paths)

    def test_every_workflow_projector_wrote_is_its_own_and_an_edited_one_is_not(self) -> None:
        current = summary.workflow_text("v0.5.3", "trunk", "plans")
        shapes = {
            "current": current,
            "before the new paths": current.replace(", .projector.toml, .github/workflows/projector-site.yml", ""),
            "walkthroughs step": summary.PREVIOUS_WORKFLOWS[1].format(
                event="projector-walkthroughs", ref="v0", branch="main", paths="README.md, 'docs/**'"),
            "no push trigger": summary.PREVIOUS_WORKFLOWS[0].format(event="projector-walkthroughs", ref="0123abc"),
        }
        for name, text in shapes.items():
            with self.subTest(name):
                self.assertTrue(summary.generated_workflow(text, "plans" if "plans" in text else None))

        setup = current.replace("    steps:\n", "    steps:\n      - uses: actions/setup-python@v5\n")
        self.assertFalse(summary.generated_workflow(setup), "a toolchain step for site.prepare")
        self.assertFalse(summary.generated_workflow("# Deploys the docs.\n" + current), "a comment")
        self.assertFalse(summary.generated_workflow(current.replace("paths: [README.md", "paths:\n      - README.md")))
        # A path or branch added on the one line Projector writes is still a hand edit.
        self.assertFalse(summary.generated_workflow(current.replace("'plans/**'", "'plans/**', 'proto/**'"), "plans"),
                         "an added watched path")
        self.assertFalse(summary.generated_workflow(current.replace("branches: [trunk]", "branches: [trunk, release]"),
                                                    "plans"), "an added branch")
        plain = summary.workflow_text("v0", "main")
        self.assertFalse(summary.generated_workflow(plain.replace("'docs/**'", "'docs/**', 'proto/**'")),
                         "an added glob where a projects directory would go")


if __name__ == "__main__":
    unittest.main()
