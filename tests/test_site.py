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
from projector import site as site_build
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
        # A build that knows its repository asks GitHub for its stacks and for each
        # pull request's state and reviews. No test reaches GitHub; one that needs
        # them supplies them.
        for name in ("pr_status", "repo_stacks"):
            offline = mock.patch.object(summary, name, side_effect=summary.SummaryError("gh api failed: offline"))
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
        self.assertLess(home.index('src="/assets/summary.js"'), home.index('src="/assets/site.js"'),
                        "site.js draws with the helpers summary.js shares")
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

    def test_a_projects_directory_in_a_hidden_folder_still_builds_the_site(self) -> None:
        # The site copies nothing under a hidden folder, so search has no copy of such a plan to read.
        (self.repo / ".projector.toml").write_text('[projects]\ndir = ".plans"\n')
        (self.repo / ".plans/alpha").mkdir(parents=True)
        (self.repo / ".plans/alpha/readme.md").write_text(plan("Plan alpha", "draft", "later"))

        code, manifest, err = self.build()

        self.assertEqual(0, code, err)
        self.assertEqual(["alpha"], [p["name"] for p in manifest["projects"]])
        routes = [e["route"] for e in json.loads((self.out / "search.json").read_text())]
        self.assertIn("docs/guide/", routes)
        self.assertNotIn("projects/alpha/", routes, "search lists no page the site cannot load")


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
BOTH_DIFF = """diff --git a/docs/projects/alpha/readme.md b/docs/projects/alpha/readme.md
index 1111111..2222222 100644
--- a/docs/projects/alpha/readme.md
+++ b/docs/projects/alpha/readme.md
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
"""


def github_stack(number: int, *pulls: int, merged: bool = False) -> dict:
    """A stack as GitHub's stacks API lists it, with its pull requests bottom first."""
    return {"number": number, "open": not merged, "base": {"ref": "main"},
            "pull_requests": [{"number": n, "state": "closed" if merged else "open", "draft": False,
                               "merged_at": "2026-10-10T12:00:00Z" if merged else None,
                               "head": {"ref": f"feature-{n}", "sha": f"{n:040x}"}} for n in pulls]}


# Without a doctype a browser lays the page out in quirks mode, and without the
# viewport a phone lays it out at desktop width and ignores the narrow-screen rules.
STANDARDS_HEAD = ('<!doctype html>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n')


class SiteContentTests(SiteRepoCase):
    def publish(self, number: int, head: str, project: object = ..., name: str | None = None,
                projects: list[str] | None = None, diff: str = PLAN_DIFF, **pr: str) -> None:
        """Publish a summary of `number` at `head`, which records `project`, or none at all when it is `...`,
        as a summary published before the field existed does."""
        data = {
            "version": 1,
            "name": f"Summary {number}" if name is None else name,
            "pr": {"repo": "owner/example", "number": number, "title": f"Change {number}", "head": head,
                   "base": "b" * 40, "baseRef": "main", **pr},
            "overview": {"summary": [], "cards": []},
            "groups": [{"id": "all", "title": "All", "files": [{"path": f["path"]} for f in summary.parse_diff(diff)]}],
        }
        if project is not ...:
            data["project"] = project
        if projects is not None:
            data["projects"] = projects
        folder = self.summaries / str(number) / head
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps(data))
        (folder / "diff.patch").write_text(diff)

    def setUp(self) -> None:
        super().setUp()
        # A checkout inside a hidden directory, as a worktree under .claude/ is,
        # must still serve its files; only hidden paths inside the repository are skipped.
        hidden = Path(tempfile.mkdtemp()) / ".worktrees" / "repo"
        hidden.parent.mkdir()
        self.repo = Path(shutil.move(str(self.repo), str(hidden)))
        self.reset()
        (self.repo / "docs/images").mkdir(parents=True)
        (self.repo / "docs/images/diagram.png").write_bytes(b"\x89PNG fake")
        (self.repo / "docs/.hidden").write_text("not served")

    def reset(self) -> None:
        """Start over with no summaries and no site built."""
        self.summaries = Path(tempfile.mkdtemp()) / "summaries"
        self.out = Path(tempfile.mkdtemp()) / "site"

    def build(self, github: list[dict] | None = None) -> dict:
        """Build the site and return its site.json, with GitHub listing the stacks in `github`, or unable to list
        them when it is None. The build's output is in `self.printed`. A build of summaries says how it found
        their stacks in exactly one line, with no warning about them."""
        printed = io.StringIO()
        listed = mock.patch.object(summary, "repo_stacks", return_value=(github, 1)) if github is not None \
            else nullcontext()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), listed, \
             redirect_stdout(printed), redirect_stderr(printed):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo),
                             "--summaries", str(self.summaries)])
        self.assertEqual(0, code)
        self.printed = printed.getvalue()
        lines = self.printed.splitlines()
        self.assertEqual(int(self.summaries.is_dir()), sum(line.startswith("stacks: ") for line in lines), self.printed)
        self.assertEqual([], [line for line in lines if "::warning" in line and "stack" in line.lower()])
        self.assertNotIn("different projects", self.printed)
        return json.loads((self.out / "site.json").read_text())

    def data(self, number: int) -> dict:
        """The data of pull request `number`'s page, which has one head."""
        return json.loads(next((self.out / "reviews" / str(number)).glob("*/data.json")).read_text())

    def sidebars(self) -> dict[int, list[int]]:
        """The pull requests each review's sidebar lists in its stack, with [] for one alone."""
        reviews = json.loads((self.out / "site.json").read_text())["reviews"]
        return {r["number"]: [row["number"] for row in self.data(r["number"])["stack"]] for r in reviews}

    @staticmethod
    def written(manifest: dict) -> dict[str, list[str]]:
        """Each project's stacks, newest first, each written as its segments: the project its header names, or else
        its title, then its members, with a ` · ` between segments, as `alpha/beta 9 · alpha 10`."""
        def segment(s: dict) -> str:
            return " ".join([s["header"].get("project", s["header"]["title"]), *map(str, s["members"])])
        return {p["name"]: [" · ".join(map(segment, stack)) for stack in p["stacks"]] for p in manifest["projects"]}

    def groups(self) -> list[tuple[int, int | None]]:
        """Each review on the Reviews index, in its order, with the number its stack is grouped under."""
        return [(r["number"], r["stack"]) for r in json.loads((self.out / "site.json").read_text())["reviews"]]

    def stacks(self, pulls: dict[str, str] | None = None) -> tuple[dict[int, list[int]], list[str]]:
        """The pull requests in each review's stack, as its page lists them, and the head branches the build asked GitHub about."""
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

        def stack(number: int) -> list[int]:
            page = next((self.out / "reviews" / str(number)).glob("*/data.json"))
            return [row["number"] for row in json.loads(page.read_text())["stack"]]

        return {r["number"]: stack(r["number"]) for r in reviews}, asked

    def test_a_review_shares_a_stack_with_the_review_its_pull_request_is_stacked_on(self) -> None:
        self.publish(9, "c" * 40, headRef="feature-a")
        self.publish(10, "d" * 40, headRef="feature-b", baseRef="feature-a")

        self.assertEqual(({9: [9, 10], 10: [9, 10]}, []), self.stacks(), "a review's head branch answers without GitHub")

    def test_a_summary_that_recorded_the_pull_request_beneath_it_needs_no_lookup(self) -> None:
        self.publish(10, "d" * 40, baseRef="feature-a", basePr=7)

        self.assertEqual(({10: [7, 10]}, []), self.stacks())

    def test_an_older_summary_asks_github_once_per_base_branch(self) -> None:
        self.publish(10, "d" * 40, baseRef="feature-a")
        self.publish(11, "e" * 40, baseRef="feature-a")
        self.publish(12, "f" * 40, baseRef="release")

        stacked, asked = self.stacks({"feature-a": "7"})

        self.assertEqual({10: [7, 10, 11], 11: [7, 10, 11], 12: []}, stacked,
                         "a base branch no pull request uses marks nothing")
        self.assertEqual(["feature-a", "release"], sorted(asked))

    def test_a_pull_request_on_the_default_branch_is_never_stacked_or_looked_up(self) -> None:
        self.publish(9, "c" * 40, headRef="main")
        self.publish(10, "d" * 40)

        self.assertEqual(({9: [], 10: []}, []), self.stacks({"main": "9"}))

    def test_a_default_branch_misjudged_is_still_not_a_stack(self) -> None:
        # A checkout on a feature branch that never recorded origin's HEAD takes feature for the default.
        subprocess.run(["git", "init", "-q", "-b", "feature", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "start"], check=True)
        self.publish(10, "d" * 40)

        self.assertEqual(({10: []}, ["main"]), self.stacks({}),
                         "on a checkout that does not know its default branch, main is asked about and is no pull request's head")

    def test_the_default_branch_comes_from_origin_not_the_branch_checked_out(self) -> None:
        subprocess.run(["git", "init", "-q", "-b", "feature", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "symbolic-ref", "refs/remotes/origin/HEAD",
                        "refs/remotes/origin/trunk"], check=True)
        self.publish(9, "c" * 40, baseRef="trunk")

        self.assertEqual(({9: []}, []), self.stacks({}), "trunk is known to be the default, so it is not asked about")

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

    def test_an_incremental_review_links_the_full_review_it_builds_on(self) -> None:
        statuses = self.statuses([projector_review("changes-requested", "c" * 40, "2026-10-01T10:00:00Z", 1),
                                  projector_review("clean", "c" * 40, "2026-10-02T10:00:00Z", 2),
                                  incremental_review("d" * 40, "c" * 40, "2026-10-03T10:00:00Z", 3)])

        self.assertEqual({"c": {"status": "clean", "url": review_url(2)},
                          "d": {"status": "clean", "url": review_url(3),
                                "incrementalFrom": {"head": "c" * 40, "url": review_url(2)}}}, statuses)

    def test_the_newest_review_of_a_head_decides_whichever_model_wrote_it(self) -> None:
        statuses = self.statuses([
            projector_review("changes-requested", "c" * 40, "2026-10-01T10:00:00Z", 1, model="gpt-5.5"),
            projector_review("clean", "c" * 40, "2026-10-01T11:00:00Z", 2, model="claude-opus"),
            projector_review("clean", "d" * 40, "2026-10-02T10:00:00Z", 3, model="claude-opus"),
            projector_review("changes-requested", "d" * 40, "2026-10-02T11:00:00Z", 4, model="gpt-5.5"),
        ])

        self.assertEqual({"status": "clean", "url": review_url(2)}, statuses["c"],
                         "a clean verdict after another model's change request is the head's status")
        self.assertEqual({"status": "changes-requested", "url": review_url(4)}, statuses["d"],
                         "a change request after another model's clean verdict is the head's status")

    def test_an_incremental_review_builds_on_its_own_models_full_review(self) -> None:
        statuses = self.statuses([
            projector_review("clean", "c" * 40, "2026-10-01T10:00:00Z", 1, model="claude-opus"),
            projector_review("clean", "c" * 40, "2026-10-01T11:00:00Z", 2, model="gpt-5.5"),
            incremental_review("d" * 40, "c" * 40, "2026-10-02T10:00:00Z", 3, model="Claude-Opus[1m]"),
        ])

        self.assertEqual({"status": "clean", "url": review_url(3),
                          "incrementalFrom": {"head": "c" * 40, "url": review_url(1)}}, statuses["d"],
                         "the link names claude-opus's full review, not gpt-5.5's newer one")

    def test_an_incremental_line_without_a_review_marker_sets_no_status(self) -> None:
        line = incremental_review("d" * 40, "c" * 40, "2026-10-03T10:00:00Z", 3)["body"].splitlines()[3]
        statuses = self.statuses([{"body": f"Quoting:\n{line}\n", "url": review_url(3), "at": "2026-10-03T10:00:00Z",
                                   "association": "OWNER"}])

        self.assertEqual({"c": {"status": "unreviewed"}, "d": {"status": "unreviewed"}}, statuses)

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
        self.assertEqual([{"number": 7, "title": "", "url": "https://github.com/owner/example/pull/7", "current": False},
                          {"number": 13, "title": "Change 13", "url": "/reviews/13/", "current": True}],
                         stack(13, "a" * 40), "a pull request with no summary has no title, and links to GitHub")
        self.assertEqual([], stack(20, "b" * 40), "a pull request alone has no stack")

    def test_a_stack_github_cannot_answer_for_shows_nothing(self) -> None:
        self.publish(10, "d" * 40, baseRef="feature-a")

        self.assertEqual({10: []}, self.stacks(None)[0])

    def test_the_reviews_index_lists_each_stack_together_from_the_default_branch_up(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-a")
        self.publish(10, "b" * 40, headRef="feature-b", baseRef="feature-a")
        self.publish(11, "c" * 40, headRef="feature-c", baseRef="feature-b")
        self.publish(12, "d" * 40, headRef="feature-d", baseRef="feature-b")
        self.publish(13, "e" * 40, headRef="feature-e", baseRef="feature-x", basePr=7)
        self.publish(14, "f" * 40, headRef="alone")
        self.publish(15, "a" * 40, headRef="feature-p")
        self.publish(16, "b" * 40, headRef="between")
        self.publish(18, "c" * 40, headRef="feature-q", baseRef="feature-p")
        self.publish(30, "d" * 40, headRef="feature-s", baseRef="feature-r", basePr=25)
        self.publish(31, "e" * 40, headRef="feature-t", baseRef="feature-r", basePr=25)
        self.stacks()

        reviews = json.loads((self.out / "site.json").read_text())["reviews"]

        self.assertEqual([(30, 30), (31, 30), (15, 15), (18, 15), (16, None), (14, None), (13, None),
                          (9, 9), (10, 9), (11, 9), (12, 9)], [(r["number"], r["stack"]) for r in reviews],
                         "a stack sits where its newest pull request would, and lists its pull requests from the "
                         "bottom up; two on a base with no review here stay together, and one alone is no stack")
        stacks = self.written(json.loads((self.out / "site.json").read_text()))
        self.assertEqual(["alpha 25 30 31", "alpha 15 18", "alpha 16", "alpha 14", "alpha 7 13", "alpha 9 10 11 12"],
                         stacks["alpha"], "a project lists its stacks in the index's order, newest first, each whole")

    def test_a_stack_numbered_out_of_order_is_listed_by_what_sits_on_what(self) -> None:
        # 35 was opened before 40 but rebased onto it, and 50 sits on 35.
        self.publish(40, "a" * 40, headRef="feature-40")
        self.publish(35, "b" * 40, headRef="feature-35", baseRef="feature-40")
        self.publish(50, "c" * 40, headRef="feature-50", baseRef="feature-35")
        self.publish(41, "d" * 40, headRef="alone-41")
        self.publish(38, "e" * 40, headRef="alone-38")
        self.stacks()

        reviews = json.loads((self.out / "site.json").read_text())["reviews"]

        self.assertEqual([(40, 40), (35, 40), (50, 40), (41, None), (38, None)],
                         [(r["number"], r["stack"]) for r in reviews],
                         "the stack runs from its bottom up, not by number, and sits where its highest number would")

    def test_the_reviews_index_carries_each_pull_requests_state_and_its_newest_verdict(self) -> None:
        self.publish(9, "d" * 40)
        self.publish(10, "e" * 40)
        states = {9: "open", 10: "merged"}

        def pr_status(repo: str, number: int) -> dict:
            return {"state": states[number], "reviews": [projector_review("clean", "d" * 40, "2026-10-01T10:00:00Z", 1)]}

        with mock.patch.object(summary, "pr_status", side_effect=pr_status):
            reviews = self.build()["reviews"]

        self.assertEqual([(10, "merged", {"status": "unreviewed"}), (9, "open", {"status": "clean", "url": review_url(1)})],
                         [(r["number"], r["state"], r["review"]) for r in reviews],
                         "each row carries what its newest head's page does")

    def test_the_reviews_index_titles_each_review_as_its_page_does(self) -> None:
        self.publish(9, "c" * 40, project="")

        reviews = self.build()["reviews"]

        self.assertEqual([(9, "Change 9", {"title": "Summary 9"})], [(r["number"], r["title"], r["header"]) for r in reviews],
                         "the pull request's title, under the summary's name as its stack's title")
        self.assertEqual([], [key for key in ("name", "projects") for r in reviews if key in r])

    def test_the_reviews_index_leaves_out_a_status_github_cannot_tell(self) -> None:
        self.publish(9, "c" * 40)

        review = self.build()["reviews"][0]

        self.assertNotIn("state", review)
        self.assertNotIn("review", review)

    def test_a_review_is_headed_by_the_project_it_records_and_listed_on_its_page(self) -> None:
        self.publish(9, "c" * 40, project="alpha/beta")

        manifest = self.build()

        beta = {"project": "alpha/beta", "title": "Finish beta", "url": "/projects/alpha/beta/"}
        self.assertEqual({"alpha": [], "alpha/beta": ["alpha/beta 9"]}, self.written(manifest),
                         "the recorded project, not the one whose plan the diff changes")
        data = self.data(9)
        self.assertEqual((beta, [{"header": beta, "members": [9]}]), (data["header"], data["segments"]))
        self.assertEqual([beta], [r["header"] for r in manifest["reviews"]])
        self.assertNotIn("projects", data)
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

    def test_a_pull_request_alone_is_headed_by_its_own_project_or_its_title(self) -> None:
        alpha = {"project": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}
        beta = {"project": "alpha/beta", "title": "Finish beta", "url": "/projects/alpha/beta/"}
        titled = {"title": "Summary 9"}
        # PLAN_DIFF changes alpha's plan, and BOTH_DIFF alpha's and alpha/beta's.
        for case, published, header in (
                ("a project the site has", {"project": "alpha"}, alpha),
                ("a project the site lacks", {"project": "no-such-project"}, titled),
                ("no project, while the diff changes alpha's plan", {"project": ""}, titled),
                ("an older summary whose diff changes one project's files", {}, alpha),
                ("an older summary whose projects list names one the site has",
                 {"projects": ["no-such-project", "alpha/beta"]}, beta),
                ("a null project, read as an older summary's", {"project": None}, alpha),
                ("an older summary whose diff changes two projects' files", {"diff": BOTH_DIFF}, titled)):
            with self.subTest(case):
                self.reset()
                self.publish(9, "c" * 40, **published)

                manifest = self.build()

                self.assertEqual(header, self.data(9)["header"])
                self.assertEqual([{"header": header, "members": [9]}], self.data(9)["segments"], "one segment")
                self.assertEqual({"alpha": ["alpha 9"] if header is alpha else [],
                                  "alpha/beta": ["alpha/beta 9"] if header is beta else []}, self.written(manifest))

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

    # Stacks GitHub lists

    def test_a_github_stack_keeps_a_layer_whose_base_moved_to_the_default_branch(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-9")
        self.publish(10, "b" * 40, headRef="feature-10")

        self.build([github_stack(30, 9, 10)])

        self.assertEqual({9: [9, 10], 10: [9, 10]}, self.sidebars())
        self.assertEqual([(9, 9), (10, 9)], self.groups())
        self.assertIn("stacks: read 1 GitHub stack in 1 request\n", self.printed)

    def test_github_decides_its_members_whatever_their_base_branches_say(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-9")
        self.publish(10, "b" * 40, headRef="feature-10", baseRef="feature-14")
        self.publish(14, "c" * 40, headRef="feature-14")

        self.build([github_stack(30, 9, 10)])

        self.assertEqual({9: [9, 10], 10: [9, 10], 14: []}, self.sidebars())

    def test_a_merged_github_stack_stays_one_group_under_closed(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-9")
        self.publish(10, "b" * 40, headRef="feature-10")

        merged = {"state": "merged", "base": None, "reviews": []}
        with mock.patch.object(summary, "pr_status", return_value=merged):
            reviews = self.build([github_stack(30, 9, 10, merged=True)])["reviews"]

        self.assertEqual([(9, 9, "merged"), (10, 9, "merged")], [(r["number"], r["stack"], r["state"]) for r in reviews])
        self.assertEqual({9: [9, 10], 10: [9, 10]}, self.sidebars())

    def test_a_github_stack_member_without_a_summary_stays_in_the_sidebar(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-9")

        self.build([github_stack(30, 9, 11)])

        self.assertEqual([{"number": 9, "title": "Change 9", "url": "/reviews/9/", "current": True},
                          {"number": 11, "title": "", "url": "https://github.com/owner/example/pull/11", "current": False}],
                         self.data(9)["stack"])
        self.assertEqual([(9, None)], self.groups(), "the index's group holds the reviews alone")

    def test_a_pull_request_chained_by_hand_joins_a_github_stack_after_its_members(self) -> None:
        for beneath in (10, 9):
            with self.subTest(based_on=beneath):
                self.reset()
                self.publish(9, "a" * 40, headRef="feature-9")
                self.publish(10, "b" * 40, headRef="feature-10")
                self.publish(15, "c" * 40, headRef="feature-15", baseRef=f"feature-{beneath}")

                self.build([github_stack(30, 9, 10)])

                self.assertEqual({9: [9, 10, 15], 10: [9, 10, 15], 15: [9, 10, 15]}, self.sidebars())
                self.assertEqual([(9, 9), (10, 9), (15, 9)], self.groups())

    def test_a_branch_between_two_github_stacks_joins_neither(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-9")
        self.publish(10, "b" * 40, headRef="feature-10")
        self.publish(20, "c" * 40, headRef="feature-20", baseRef="feature-10")
        self.publish(21, "d" * 40, headRef="feature-21", baseRef="feature-20")

        self.build([github_stack(30, 9, 10), github_stack(40, 20, 21)])

        self.assertEqual({9: [9, 10], 10: [9, 10], 20: [20, 21], 21: [20, 21]}, self.sidebars())

    def test_a_pull_request_in_no_github_stack_sits_on_its_base_branch(self) -> None:
        self.publish(12, "a" * 40, headRef="feature-12", baseRef="feature-13")
        self.publish(13, "b" * 40, headRef="feature-13")

        self.build([github_stack(30, 1, 2)])

        self.assertEqual({12: [13, 12], 13: [13, 12]}, self.sidebars())

    def test_a_stack_or_member_github_lists_without_a_number_is_left_out_and_counted(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-9")
        self.publish(10, "b" * 40, headRef="feature-10", baseRef="feature-9")
        self.publish(20, "c" * 40, headRef="feature-20")
        self.publish(21, "d" * 40, headRef="feature-21")
        unnumbered = github_stack(30, 10, 9)
        del unnumbered["number"]
        unlisted = github_stack(31)
        del unlisted["pull_requests"]
        partial = github_stack(40, 20, 99, 21)
        partial["pull_requests"][1]["number"] = "99"

        self.build([unnumbered, unlisted, partial])

        self.assertEqual({9: [9, 10], 10: [9, 10], 20: [20, 21], 21: [20, 21]}, self.sidebars(),
                         "the unnumbered stack's pull requests sit on their base branches, and the gap closes")
        self.assertIn("stacks: read 3 GitHub stacks in 1 request; left out 1 stack with no number, whose pull requests "
                      "sit on their base branches; left out 1 stack with no list of pull requests; left out 1 stack "
                      "member with no number\n", self.printed)

    def test_a_pull_request_in_two_github_stacks_belongs_to_the_higher_one(self) -> None:
        for number, head in ((9, "a"), (10, "b"), (11, "c"), (12, "d")):
            self.publish(number, head * 40, headRef=f"feature-{number}")

        self.build([github_stack(30, 9, 10, 11), github_stack(40, 10, 12)])

        self.assertEqual({9: [9, 11], 10: [10, 12], 11: [9, 11], 12: [10, 12]}, self.sidebars())

    # Stacks GitHub cannot list

    def test_without_github_s_stacks_every_pull_request_sits_on_its_base_branch(self) -> None:
        self.publish(9, "a" * 40, headRef="feature-9")
        self.publish(10, "b" * 40, headRef="feature-10", baseRef="feature-9")

        self.build()

        self.assertEqual({9: [9, 10], 10: [9, 10]}, self.sidebars())
        self.assertIn("stacks: GitHub's stacks could not be read, so every pull request sits on its base branch\n",
                      self.printed)

    def test_a_base_branch_github_cannot_look_up_leaves_only_its_own_pull_request_alone(self) -> None:
        self.publish(12, "a" * 40, headRef="feature-12", baseRef="feature-13")
        self.publish(17, "b" * 40, headRef="feature-17", baseRef="feature-16")

        def lookup(*args: str) -> str | None:
            head = next(a for a in args if a.startswith("head=")).split(":", 1)[1]
            if head == "feature-16":
                raise summary.SummaryError("gh api failed: HTTP 502")
            return {"feature-13": "13\n"}.get(head, "\n")

        with mock.patch.object(summary, "gh_lookup", side_effect=lookup):
            self.build()

        self.assertEqual({12: [13, 12], 17: []}, self.sidebars())

    # Headers

    ALPHA = {"project": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}
    BETA = {"project": "alpha/beta", "title": "Finish beta", "url": "/projects/alpha/beta/"}

    def layered(self, *projects: object, github: bool = True, merged: bool = False) -> dict:
        """Build a stack of #9 and up, each layer based on the one beneath, that record `projects` bottom first;
        return site.json. GitHub lists the stack unless `github` is false, and reports it merged when `merged`."""
        numbers = range(9, 9 + len(projects))
        for number, project in zip(numbers, projects):
            self.publish(number, f"{number:040x}", project, headRef=f"feature-{number}",
                         **({"baseRef": f"feature-{number - 1}"} if number > 9 else {}))
        status = {"state": "merged" if merged else "open", "base": None, "reviews": []}
        with mock.patch.object(summary, "pr_status", return_value=status):
            return self.build([github_stack(30, *numbers, merged=merged)] if github else None)

    def assert_segments(self, manifest: dict, expected: list[tuple[dict, list[int]]]) -> None:
        """Every page of the stack carries `expected` as its segments, and every page and row the header of its own."""
        segments = [{"header": header, "members": members} for header, members in expected]
        own = {n: header for header, members in expected for n in members}
        for row in manifest["reviews"]:
            data = self.data(row["number"])
            self.assertEqual(segments, data["segments"], row["number"])
            self.assertEqual(own[row["number"]], data["header"], row["number"])
            self.assertEqual(own[row["number"]], row["header"], row["number"])

    def test_a_layer_with_no_project_sits_under_the_project_of_the_layer_beneath(self) -> None:
        manifest = self.layered("alpha", "")

        self.assert_segments(manifest, [(self.ALPHA, [9, 10])])
        self.assertEqual(["alpha 9 10"], self.written(manifest)["alpha"])

    def test_a_merged_stack_keeps_the_header_of_its_bottom_layer(self) -> None:
        manifest = self.layered("alpha", "", merged=True)

        self.assert_segments(manifest, [(self.ALPHA, [9, 10])])

    def test_a_stack_splits_into_a_segment_for_each_run_of_layers_that_carry_one_project(self) -> None:
        alpha, beta = self.ALPHA, self.BETA
        for projects, expected in (
                (("", "alpha"), [(alpha, [9, 10])]),
                (("alpha", "alpha", "alpha/beta"), [(alpha, [9, 10]), (beta, [11])]),
                (("alpha", "", "alpha/beta"), [(alpha, [9, 10]), (beta, [11])]),
                (("", "alpha", "alpha/beta"), [(alpha, [9, 10]), (beta, [11])]),
                (("alpha", "no-such-project", "alpha/beta"), [(alpha, [9, 10]), (beta, [11])]),
                (("alpha", "", "alpha"), [(alpha, [9, 10, 11])]),
                (("alpha", "alpha/beta", "alpha"), [(alpha, [9]), (beta, [10]), (alpha, [11])]),
                (("alpha", "alpha/beta"), [(alpha, [9]), (beta, [10])])):
            with self.subTest(projects=projects):
                self.reset()
                manifest = self.layered(*projects)

                self.assert_segments(manifest, expected)
                whole = " · ".join(" ".join([h["project"], *map(str, m)]) for h, m in expected)
                self.assertEqual({"alpha": [whole] if any(h is alpha for h, _ in expected) else [],
                                  "alpha/beta": [whole] if any(h is beta for h, _ in expected) else []},
                                 self.written(manifest), "each project the stack carries out lists it once, whole")

    def test_a_stack_found_from_base_branches_splits_as_a_github_stack_does(self) -> None:
        self.assert_segments(self.layered("alpha", "alpha/beta", github=False), [(self.ALPHA, [9]), (self.BETA, [10])])

    def test_a_merged_stack_of_two_projects_keeps_both_segments(self) -> None:
        manifest = self.layered("alpha", "alpha/beta", merged=True)

        self.assert_segments(manifest, [(self.ALPHA, [9]), (self.BETA, [10])])
        self.assertEqual(["merged", "merged"], [r["state"] for r in manifest["reviews"]])

    def test_a_member_without_a_summary_joins_the_segment_beneath_it(self) -> None:
        self.publish(9, "a" * 40, "alpha", headRef="feature-9")
        self.publish(10, "b" * 40, "alpha/beta", headRef="feature-10")

        manifest = self.build([github_stack(30, 9, 11, 10)])

        self.assert_segments(manifest, [(self.ALPHA, [9, 11]), (self.BETA, [10])])
        self.assertEqual([9, 11, 10], [row["number"] for row in self.data(9)["stack"]])

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_a_member_without_a_summary_is_drawn_under_the_header_beneath_it(self) -> None:
        self.publish(9, "a" * 40, "alpha", headRef="feature-9")
        self.publish(10, "b" * 40, "alpha/beta", headRef="feature-10")
        self.build([github_stack(30, 9, 11, 10)])

        html = rendered_summary(self.data(9))

        self.assertRegex(html, r'<div class="stackhead"><a href="/projects/alpha/">Build alpha</a></div>'
                               r'<ul class="stack" aria-label="Pull requests for Build alpha">'
                               r'<li class="srow current"[^>]*>.*?#9</span>.*?</li>'
                               r'<li><a class="srow" href="https://github.com/owner/example/pull/11">.*?#11</span>.*?</ul>'
                               r'<div class="stackhead"><a href="/projects/alpha/beta/">Finish beta</a></div>')

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_each_merged_page_of_a_two_project_stack_draws_both_headers(self) -> None:
        self.layered("alpha", "alpha/beta", merged=True)

        for number in (9, 10):
            html = rendered_summary(self.data(number))
            self.assertEqual(['<a href="/projects/alpha/">Build alpha</a>', '<a href="/projects/alpha/beta/">Finish beta</a>'],
                             re.findall(r'<div class="stackhead">(.*?)</div>', html), number)
            self.assertIn('<span class="rstatus merged">Merged</span>', html)

    def test_a_pull_request_chained_by_hand_joins_the_segment_drawn_above_it(self) -> None:
        self.publish(9, "a" * 40, "alpha", headRef="feature-9")
        self.publish(10, "b" * 40, "alpha/beta", headRef="feature-10")
        self.publish(15, "c" * 40, "", headRef="feature-15", baseRef="feature-9")

        manifest = self.build([github_stack(30, 9, 10)])

        self.assertEqual([9, 10, 15], [row["number"] for row in self.data(15)["stack"]])
        self.assert_segments(manifest, [(self.ALPHA, [9]), (self.BETA, [10, 15])])

    def test_a_stack_with_no_project_is_titled_by_its_lowest_name(self) -> None:
        for names, title in ((("", "Second layer's title"), "Second layer's title"), (("", ""), "Change 9")):
            with self.subTest(names=names):
                self.reset()
                for number, name in zip((9, 10), names):
                    self.publish(number, f"{number:040x}", "", name, headRef=f"feature-{number}")

                manifest = self.build([github_stack(30, 9, 10)])

                self.assert_segments(manifest, [({"title": title}, [9, 10])])

    def test_every_row_carries_its_header_and_no_name_or_projects(self) -> None:
        self.publish(9, "a" * 40, "alpha", projects=["alpha/beta"])
        self.publish(10, "b" * 40, "")

        reviews = self.build()["reviews"]

        self.assertEqual([(10, {"title": "Summary 10"}), (9, self.ALPHA)], [(r["number"], r["header"]) for r in reviews])
        self.assertEqual([], [key for key in ("name", "projects") for r in reviews if key in r])

    # A project's stacks

    def test_a_project_lists_its_stack_whole(self) -> None:
        self.assertEqual(["alpha 9 10 11"], self.written(self.layered("alpha", "alpha", "alpha"))["alpha"])

    def test_a_project_lists_its_newer_stack_first(self) -> None:
        self.publish(8, "8" * 40, "alpha", headRef="feature-8")

        manifest = self.layered("alpha", "alpha", "alpha")

        self.assertEqual(["alpha 9 10 11", "alpha 8"], self.written(manifest)["alpha"], "as the Reviews index orders them")

    def test_a_project_lists_a_layer_without_a_summary_in_its_stack(self) -> None:
        self.publish(9, "a" * 40, "alpha", headRef="feature-9")

        manifest = self.build([github_stack(30, 9, 12)])

        self.assertEqual(["alpha 9 12"], self.written(manifest)["alpha"])

    def test_a_project_lists_its_merged_and_closed_layers(self) -> None:
        self.publish(9, "a" * 40, "alpha", headRef="feature-9")
        self.publish(10, "b" * 40, "alpha", headRef="feature-10", baseRef="feature-9")
        states = {9: "merged", 10: "closed"}

        with mock.patch.object(summary, "pr_status",
                               side_effect=lambda repo, n: {"state": states[n], "base": None, "reviews": []}):
            manifest = self.build([github_stack(30, 9, 10)])

        self.assertEqual(["merged", "closed"], [r["state"] for r in manifest["reviews"]])
        self.assertEqual(["alpha 9 10"], self.written(manifest)["alpha"])

    def test_a_project_no_layer_records_lists_no_stack(self) -> None:
        self.assertEqual([], self.written(self.layered("alpha/beta"))["alpha"])

    def test_a_stack_of_two_projects_is_listed_whole_under_each(self) -> None:
        self.assertEqual({"alpha": ["alpha/beta 9 · alpha 10"], "alpha/beta": ["alpha/beta 9 · alpha 10"]},
                         self.written(self.layered("alpha/beta", "alpha")))

    def test_a_stack_that_returns_to_a_project_is_listed_once_under_it(self) -> None:
        self.assertEqual(["alpha 9 · alpha/beta 10 · alpha 11"],
                         self.written(self.layered("alpha", "alpha/beta", "alpha"))["alpha"])

    def test_a_stack_headed_by_its_title_alone_is_listed_under_no_project(self) -> None:
        self.assertEqual({"alpha": [], "alpha/beta": []}, self.written(self.layered("", "")))

    def test_each_stack_a_project_lists_is_the_segments_its_pages_carry(self) -> None:
        self.publish(8, "8" * 40, "alpha/beta", headRef="feature-8")
        self.publish(9, "9" * 40, "alpha", headRef="feature-9")
        self.publish(10, "a" * 40, "alpha/beta", headRef="feature-10")

        manifest = self.build([github_stack(30, 9, 10, 12)])

        self.assertEqual({"alpha": ["alpha 9 · alpha/beta 10 12"], "alpha/beta": ["alpha 9 · alpha/beta 10 12", "alpha/beta 8"]},
                         self.written(manifest))
        summarized = {r["number"] for r in manifest["reviews"]}
        for project in manifest["projects"]:
            for stack in project["stacks"]:
                for number in (n for s in stack for n in s["members"] if n in summarized):
                    self.assertEqual(stack, self.data(number)["segments"], (project["name"], number))

    def test_no_project_lists_its_reviews(self) -> None:
        manifest = self.layered("alpha", "alpha/beta")

        self.assertEqual([], [p["name"] for p in manifest["projects"] if "reviews" in p])


def new_file_diff(path: str, content: str | bytes) -> str:
    """The diff that adds `path` with `content`: its lines, or a binary file's one line."""
    head = f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..1111111\n"
    if isinstance(content, bytes):
        return head + f"Binary files /dev/null and b/{path} differ\n"
    lines = content.split("\n")
    ends = content.endswith("\n")
    if ends:
        lines.pop()
    body = "".join(f"+{line}\n" for line in lines) + ("" if ends else "\\ No newline at end of file\n")
    return head + f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + body


GAMMA = plan("Ship gamma", "ready", "next")


class ProposedProjectTests(SiteRepoCase):
    """The projects that open pull requests propose, read from their newest summaries' diffs."""

    def setUp(self) -> None:
        super().setUp()
        self.summaries = Path(tempfile.mkdtemp()) / "summaries"
        self.out_text = ""

    def publish(self, number: int, files: dict[str, str | bytes], head: str | None = None, when: int = 1_700_000_000) -> str:
        """Publish pull request `number`'s summary at `head`, whose diff adds `files` under docs/projects/."""
        head = head or f"{number:02d}" * 20
        paths = {f"docs/projects/{name}": content for name, content in files.items()}
        data = {"version": 1, "name": f"Summary {number}",
                "pr": {"repo": "owner/example", "number": number, "title": f"Change {number}", "head": head,
                       "base": "b" * 40, "baseRef": "main", "headRef": f"change-{number}"},
                "overview": {"summary": [], "cards": []},
                "groups": [{"id": "all", "title": "All", "files": [{"path": path} for path in paths]}]}
        folder = self.summaries / str(number) / head
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps(data))
        (folder / "diff.patch").write_text("".join(new_file_diff(path, content) for path, content in paths.items()))
        os.utime(folder / "summary.json", (when, when))
        return head

    def build(self, states: dict[int, str] | None = None, forks: tuple[int, ...] = (), offline: bool = False) -> dict:
        """Build the site, with each pull request open unless `states` says otherwise and in this repository
        unless `forks` names it, and return site.json. GitHub is asked once for each pull request."""
        asked: list[int] = []

        def pr_status(repo: str, number: int) -> dict:
            asked.append(number)
            if offline:
                raise summary.SummaryError("gh api failed: offline")
            return {"state": (states or {}).get(number, "open"), "base": "main", "fork": number in forks,
                    "reviews": []}

        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             mock.patch.object(summary, "pr_status", side_effect=pr_status), \
             redirect_stdout(out), redirect_stderr(out):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo),
                             "--summaries", str(self.summaries)])
        self.out_text = out.getvalue()
        self.assertEqual(0, code, self.out_text)
        self.assertEqual(sorted(asked), sorted(int(p.name) for p in self.summaries.iterdir()),
                         "the build asks once for each pull request")
        return json.loads((self.out / "site.json").read_text())

    @staticmethod
    def project(manifest: dict, name: str) -> dict | None:
        return next((p for p in manifest["projects"] if p["name"] == name), None)

    def warnings(self, title: str) -> list[str]:
        return [line for line in self.out_text.splitlines() if line.startswith(f"::warning title={title}::")]

    def test_an_open_pull_request_proposes_the_plan_its_diff_adds_at_the_address_it_keeps(self) -> None:
        head = self.publish(9, {"gamma/readme.md": GAMMA})

        manifest = self.build()

        gamma = self.project(manifest, "gamma")
        self.assertEqual({"name": "gamma", "title": "Ship gamma", "status": "ready", "priority": "next", "owner": None,
                          "path": "docs/projects/gamma/readme.md", "files": [],
                          "stacks": [[{"header": {"project": "gamma", "title": "Ship gamma", "url": "/projects/gamma/"},
                                       "members": [9]}]],
                          "proposed": {"number": 9, "head": head, "also": []}}, gamma)
        self.assertNotIn("proposed", self.project(manifest, "alpha"))
        self.assertEqual(GAMMA.encode(), (self.out / "content/docs/projects/gamma/readme.md").read_bytes())
        self.assertTrue((self.out / "projects/gamma/index.html").is_file())
        index = {e["route"]: e for e in json.loads((self.out / "search.json").read_text())}
        self.assertEqual(9, index["projects/gamma/"]["proposed"])
        self.assertIn("Ship gamma", index["projects/gamma/"]["text"])
        self.assertNotIn("proposed", index["projects/alpha/"])
        data = json.loads((self.out / "reviews" / "9" / head / "data.json").read_text())
        self.assertEqual({"name": "gamma", "title": "Ship gamma", "url": "/projects/gamma/"},
                         data["files"][0]["project"])
        self.assertIn("2 projects, 1 proposed project, 1 reviews, 0 reviews skipped", self.out_text)

    def test_a_stack_segment_that_carries_out_a_proposed_project_is_headed_by_its_proposed_page(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA})
        upper = self.publish(10, {"alpha/more.md": "# More\n"})
        path = self.summaries / "10" / upper / "summary.json"
        data = json.loads(path.read_text())
        data["project"] = "gamma"
        data["pr"]["baseRef"] = "change-9"
        path.write_text(json.dumps(data))

        manifest = self.build()

        gamma = {"project": "gamma", "title": "Ship gamma", "url": "/projects/gamma/"}
        self.assertEqual([(9, gamma), (10, gamma)], [(r["number"], r["header"]) for r in manifest["reviews"]],
                         "the bottom layer's diff proposes gamma, and the upper layer records it")
        self.assertEqual([[{"header": gamma, "members": [9, 10]}]], self.project(manifest, "gamma")["stacks"])
        self.assertEqual([], self.project(manifest, "alpha")["stacks"])

    def test_a_merged_or_closed_pull_request_proposes_nothing(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA})
        for state in ("merged", "closed"):
            with self.subTest(state):
                self.assertIsNone(self.project(self.build({9: state}), "gamma"))

    def test_a_forks_pull_request_proposes_nothing_and_keeps_its_review(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA})

        manifest = self.build(forks=(9,))

        self.assertIsNone(self.project(manifest, "gamma"))
        self.assertEqual([9], [r["number"] for r in manifest["reviews"]])

    def test_a_pull_request_whose_status_is_unknown_proposes_nothing_and_the_build_says_so(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA})
        self.publish(10, {"alpha/notes-2.md": "# More notes\n"})

        manifest = self.build(offline=True)

        self.assertIsNone(self.project(manifest, "gamma"))
        self.assertEqual(["::warning title=Proposed projects left out::the status of 1 pull request that adds a plan "
                          "is unknown, because GitHub could not be asked, so its plan is not proposed"],
                         self.warnings("Proposed projects left out"))

    def test_only_the_newest_summary_proposes(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA}, head="a" * 40, when=1_700_000_000)
        self.publish(9, {"alpha/notes-2.md": "# More notes\n"}, head="b" * 40, when=1_700_000_100)

        self.assertIsNone(self.project(self.build(), "gamma"))

    def test_a_plan_without_a_final_newline_keeps_it_out(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA.rstrip("\n")})

        self.build()

        self.assertEqual(GAMMA.rstrip("\n").encode(), (self.out / "content/docs/projects/gamma/readme.md").read_bytes())

    def test_a_plan_that_does_not_load_is_skipped_with_a_warning(self) -> None:
        self.publish(9, {"gamma/readme.md": plan("Ship gamma", "maybe", "next")})

        manifest = self.build()

        self.assertIsNone(self.project(manifest, "gamma"))
        self.assertEqual(["::warning title=Proposed project skipped::#9 docs/projects/gamma/readme.md: status must be "
                          "one of draft|ready|in-progress|completed"], self.warnings("Proposed project skipped"))
        self.assertFalse((self.out / "content/docs/projects/gamma").exists())

    def test_the_checkout_wins_over_a_proposal_of_the_same_name(self) -> None:
        checkout = (self.repo / "docs/projects/alpha/readme.md").read_bytes()
        self.publish(9, {"alpha/readme.md": plan("Rewrite alpha", "draft", "later")})

        alpha = self.project(self.build(), "alpha")

        self.assertEqual("Build alpha", alpha["title"])
        self.assertNotIn("proposed", alpha)
        self.assertEqual(checkout, (self.out / "content/docs/projects/alpha/readme.md").read_bytes())

    def test_a_repositorys_first_plan_can_be_proposed(self) -> None:
        shutil.rmtree(self.repo / "docs/projects")
        self.publish(9, {"gamma/readme.md": GAMMA})

        manifest = self.build()

        self.assertEqual(["gamma"], [p["name"] for p in manifest["projects"]])
        self.assertEqual(9, manifest["projects"][0]["proposed"]["number"])
        self.assertEqual("docs/projects", manifest["projectsDir"])

    def test_the_lowest_pull_request_takes_a_name_and_the_others_are_named_beside_it(self) -> None:
        self.publish(12, {"gamma/readme.md": plan("Ship gamma later", "draft", "later")})
        self.publish(9, {"gamma/readme.md": GAMMA})

        gamma = self.project(self.build(), "gamma")

        self.assertEqual(("Ship gamma", 9, [12]), (gamma["title"], gamma["proposed"]["number"], gamma["proposed"]["also"]))

    def test_a_plan_that_does_not_load_takes_no_name(self) -> None:
        self.publish(9, {"gamma/readme.md": plan("Ship gamma", "maybe", "next")})
        self.publish(12, {"gamma/readme.md": plan("Ship gamma later", "draft", "later")})

        gamma = self.project(self.build(), "gamma")

        self.assertEqual(("Ship gamma later", 12, []), (gamma["title"], gamma["proposed"]["number"], gamma["proposed"]["also"]))
        self.assertEqual(1, len(self.warnings("Proposed project skipped")))
        self.assertIn("#9 ", self.warnings("Proposed project skipped")[0])

    def test_a_forks_proposal_of_the_same_name_is_not_named_beside_it(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA})
        self.publish(12, {"gamma/readme.md": plan("Ship gamma later", "draft", "later")})

        gamma = self.project(self.build(forks=(12,)), "gamma")

        self.assertEqual(("Ship gamma", []), (gamma["title"], gamma["proposed"]["also"]))

    def test_a_nested_plan_can_be_proposed_under_a_checkout_project(self) -> None:
        self.publish(9, {"alpha/delta/readme.md": plan("Add delta", "draft", "later")})

        delta = self.project(self.build(), "alpha/delta")

        self.assertEqual(9, delta["proposed"]["number"])
        self.assertEqual("projects/alpha/delta/", next(
            e["route"] for e in json.loads((self.out / "search.json").read_text()) if e["title"] == "Add delta"))

    def test_a_nested_plan_loads_without_a_plan_above_it_as_in_the_checkout(self) -> None:
        self.publish(9, {"omega/x/readme.md": plan("Explore x", "draft", "later")})

        self.assertEqual(9, self.project(self.build(), "omega/x")["proposed"]["number"])

    def test_only_markdown_outside_hidden_folders_comes_with_a_proposed_plan(self) -> None:
        self.publish(9, {"gamma/readme.md": GAMMA, "gamma/design.md": "# Gamma design\n",
                         "gamma/.notes/x.md": "# Hidden\n", "gamma/page.html": "<title>Page</title>",
                         "gamma/data.json": "{}\n", "gamma/logo.png": b"\x89PNG"})

        gamma = self.project(self.build(), "gamma")

        self.assertEqual([{"path": "docs/projects/gamma/design.md", "title": "Gamma design",
                           "route": "projects/gamma/design/"}], gamma["files"])
        self.assertEqual(["design.md", "readme.md"],
                         sorted(p.name for p in (self.out / "content/docs/projects/gamma").rglob("*") if p.is_file()))
        self.assertTrue((self.out / "projects/gamma/design/index.html").is_file())


SITE_JS = Path(__file__).parents[1] / "site" / "assets" / "site.js"
SUMMARY_JS = SITE_JS.with_name("summary.js")


def shared_js() -> str:
    """The helpers at the top of the compiled summary.js, which every page loads before site.js."""
    text = SUMMARY_JS.read_text()
    return text[:text.index("\n(function () {")]


def js_const(name: str) -> str:
    """One constant of the compiled site.js, as it declares it."""
    return next(line.strip() for line in SITE_JS.read_text().splitlines() if line.startswith(f"    const {name} = "))


def page_constants() -> str:
    """The constants of the compiled site.js that the projects views draw with."""
    return "\n".join(js_const(name) for name in ("STATUS_ORDER", "PRIORITY_ORDER", "PROPOSED"))


def js_function(name: str) -> str:
    """One function of the compiled site.js, from its signature to the brace that closes it."""
    lines = SITE_JS.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"    function {name}("))
    if lines[start].endswith("}"):
        return lines[start]
    end = next(i for i in range(start, len(lines)) if lines[i] == "    }")
    return "\n".join(lines[start:end + 1])


def head_file(lines: list[str], ending: str = "\n", last: str = "\n") -> bytes:
    """A file at the head holding `lines`, each ended by `ending` except the last, ended by `last`."""
    return (ending.join(lines) + last).encode()


class HunkGapTests(unittest.TestCase):
    """The hidden lines the build finds around a file's hunks, which its page can expand."""

    def context(self, hunks: str, data: bytes) -> dict | None:
        """The `context` the build gives f.txt, whose diff is `hunks` and whose head copy is `data`."""
        tmp = Path(tempfile.mkdtemp()).resolve()
        (tmp / "head").mkdir()
        (tmp / "head" / "f.txt").write_bytes(data)
        payload = {"pr": {"number": 7, "head": "c" * 40},
                   "files": summary.parse_diff(f"diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n{hunks}")}
        with redirect_stdout(io.StringIO()):
            site_build.add_context(payload, tmp / "head", tmp / "site", "/", {})
        return payload["files"][0].get("context")

    def gaps(self, hunks: str, lines: list[str]) -> list[dict]:
        return self.context(hunks, head_file(lines))["gaps"]

    def test_a_first_hunk_at_line_1_leaves_no_gap_above(self) -> None:
        lines = ["1", "two", *map(str, range(3, 11))]
        self.assertEqual([{"before": 1, "start": 5, "end": 10, "oldStart": 5}],
                         self.gaps("@@ -1,4 +1,4 @@\n 1\n-2\n+two\n 3\n 4\n", lines))

    def test_adjacent_hunks_leave_no_gap_between(self) -> None:
        self.assertEqual([{"before": 0, "start": 1, "end": 1, "oldStart": 1},
                          {"before": 2, "start": 4, "end": 5, "oldStart": 4}],
                         self.gaps("@@ -2 +2 @@\n-2\n+two\n@@ -3 +3 @@\n-3\n+three\n", ["1", "two", "three", "4", "5"]))

    def test_a_pure_addition_sits_after_its_old_start(self) -> None:
        lines = ["1", "2", "3", "4", "x", "y", *map(str, range(5, 11))]
        self.assertEqual([{"before": 0, "start": 1, "end": 4, "oldStart": 1},
                          {"before": 1, "start": 7, "end": 12, "oldStart": 5}],
                         self.gaps("@@ -4,0 +5,2 @@\n+x\n+y\n", lines))

    def test_a_pure_deletion_sits_after_its_new_start(self) -> None:
        lines = ["1", "2", "3", "4", "7", "8", "9", "10"]
        self.assertEqual([{"before": 0, "start": 1, "end": 4, "oldStart": 1},
                          {"before": 1, "start": 5, "end": 8, "oldStart": 7}],
                         self.gaps("@@ -5,2 +4,0 @@\n-5\n-6\n", lines))

    def test_a_last_hunk_at_the_end_of_the_file_leaves_no_gap_below(self) -> None:
        lines = [*map(str, range(1, 10)), "ten"]
        self.assertEqual([{"before": 0, "start": 1, "end": 6, "oldStart": 1}],
                         self.gaps("@@ -7,4 +7,4 @@\n 7\n 8\n 9\n-10\n+ten\n", lines))

    def test_a_last_line_without_a_newline_is_a_line(self) -> None:
        context = self.context("@@ -2,3 +2,3 @@\n 2\n-3\n+three\n 4\n", head_file(["1", "2", "three", "4", "5", "6"], last=""))
        self.assertEqual(6, context["lines"])
        self.assertEqual([{"before": 0, "start": 1, "end": 1, "oldStart": 1},
                          {"before": 1, "start": 5, "end": 6, "oldStart": 5}], context["gaps"])
        self.assertEqual([{"before": 0, "start": 1, "end": 2, "oldStart": 1}],
                         self.gaps("@@ -3,3 +3,3 @@\n 3\n 4\n-5\n\\ No newline at end of file\n+five\n"
                                   "\\ No newline at end of file\n", ["1", "2", "3", "4", "five"]))

    def test_crlf_lines_match_the_diff_that_reads_them_as_lf(self) -> None:
        context = self.context("@@ -2,3 +2,3 @@\n 2\n-3\n+three\n 4\n",
                               head_file(["1", "2", "three", "4", "5", "6", "7"], ending="\r\n", last="\r\n"))
        self.assertEqual(7, context["lines"])
        self.assertEqual([{"before": 0, "start": 1, "end": 1, "oldStart": 1},
                          {"before": 1, "start": 5, "end": 7, "oldStart": 5}], context["gaps"])

    def test_a_file_its_hunks_show_whole_gets_no_context(self) -> None:
        self.assertIsNone(self.context("@@ -1,3 +1,3 @@\n 1\n-2\n+two\n 3\n", head_file(["1", "two", "3"])))


CONTEXT_DIFF = """diff --git a/crlf.txt b/crlf.txt
index 1111111..2222222 100644
--- a/crlf.txt
+++ b/crlf.txt
@@ -2,3 +2,3 @@
 2
-3
+three
 4
diff --git a/wrong.txt b/wrong.txt
index 1111111..2222222 100644
--- a/wrong.txt
+++ b/wrong.txt
@@ -2,3 +2,3 @@
 2
-3
+three
 4
"""
CRLF_FILE = b"1\r\n2\r\nthree\r\n4\r\n5\r\n6\r\n7\r\n"


class ContextBuildTests(unittest.TestCase):
    """The build checks each head file `publish` stored, serves it once, and tells the page where its gaps are."""

    def setUp(self) -> None:
        self.summaries = Path(tempfile.mkdtemp()) / "summaries"
        self.out = Path(tempfile.mkdtemp()) / "site"

    def store(self, head: str, diff: str, files: dict[str, bytes]) -> Path:
        """Store a summary of `diff` at `head`, as `publish` does, with `files` under head/; return its folder."""
        folder = self.summaries / "7" / head
        (folder / "head").mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps({
            "version": 1, "pr": {"repo": "owner/example", "number": 7, "title": "Seven", "head": head, "base": "b" * 40,
                                 "baseRef": "main"},
            "groups": [{"id": "all", "title": "All",
                        "files": [{"path": f["path"]} for f in summary.parse_diff(diff)]}]}))
        (folder / "diff.patch").write_text(diff)
        for path, data in files.items():
            (folder / "head" / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / "head" / path).write_bytes(data)
        return folder

    def build(self) -> str:
        printed = io.StringIO()
        with redirect_stdout(printed):
            _, failures, _ = site_build.build_site(self.out, summaries=self.summaries, base="/projector/")
        self.assertEqual([], failures)
        return printed.getvalue()

    def files(self, head: str) -> dict[str, dict]:
        data = json.loads((self.out / "reviews" / "7" / head / "data.json").read_text())
        return {f["path"]: f for f in data["files"]}

    def test_each_head_file_is_checked_and_served_once_under_the_base(self) -> None:
        for head in ("c" * 40, "d" * 40):
            self.store(head, CONTEXT_DIFF, {"crlf.txt": CRLF_FILE, "wrong.txt": b"1\n2\nTHREE\n4\n5\n"})

        printed = self.build()

        blob = subprocess.run(["git", "hash-object", "--stdin"], input=CRLF_FILE, capture_output=True,
                              check=True).stdout.decode().strip()
        for head in ("c" * 40, "d" * 40):
            files = self.files(head)
            self.assertEqual({"url": f"/projector/reviews/blobs/{blob}.txt", "lines": 7,
                              "gaps": [{"before": 0, "start": 1, "end": 1, "oldStart": 1},
                                       {"before": 1, "start": 5, "end": 7, "oldStart": 5}]},
                             files["crlf.txt"]["context"], "the url names Git's id for the stored bytes")
            self.assertNotIn("context", files["wrong.txt"], "a file that disagrees with its diff shows no lines")
            self.assertIn(f"::warning title=Context dropped::7/{head}: wrong.txt at the head does not match its diff",
                          printed)
        self.assertEqual([f"{blob}.txt"], [p.name for p in (self.out / "reviews" / "blobs").iterdir()],
                         "two heads with the same file share it")
        self.assertEqual(b"1\n2\nthree\n4\n5\n6\n7\n", (self.out / "reviews" / "blobs" / f"{blob}.txt").read_bytes())
        self.assertIn("wrote 1 head file for summary pages to expand, 18 bytes, under reviews/blobs/", printed)

    def test_a_summary_published_without_head_files_has_no_context(self) -> None:
        self.store("c" * 40, CONTEXT_DIFF, {})

        printed = self.build()

        self.assertEqual([None, None], [f.get("context") for f in self.files("c" * 40).values()])
        self.assertFalse((self.out / "reviews" / "blobs").exists())
        self.assertIn("wrote 0 head files for summary pages to expand, 0 bytes, under reviews/blobs/", printed)

    def test_a_path_that_leads_out_of_the_head_folder_reads_nothing(self) -> None:
        # Whoever can push the summaries ref writes the diff and the files beside it.
        diff = CONTEXT_DIFF.replace("wrong.txt", "../outside.txt").replace("crlf.txt", "link.txt")
        folder = self.store("c" * 40, diff, {})
        matching = b"1\n2\nthree\n4\n5\n"
        (folder / "outside.txt").write_bytes(matching)
        (folder.parent / "secret.txt").write_bytes(matching)
        os.symlink("../../secret.txt", folder / "head" / "link.txt")

        self.build()

        self.assertEqual({"../outside.txt": None, "link.txt": None},
                         {path: f.get("context") for path, f in self.files("c" * 40).items()})
        self.assertFalse((self.out / "reviews" / "blobs").exists())


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
            "const base = '/';\n"
            f"const site = {{projects: {json.dumps(projects)}, projectsDir: 'docs/projects', projectsReadme: null}};\n"
            "let drawn = '';\n"
            "function frame(active, title, body) { drawn = body; return {querySelector: function () {"
            " return {addEventListener: function () {}}; }}; }\n"
        )
        program = stub + shared_js() + "\n" + page_constants() + "\n" + \
            "\n".join(js_function(n) for n in ("badge", "proposedBadge", "depth", "ownerName", "showProjects")) + \
            "\nshowProjects(); process.stdout.write(drawn);"
        return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout

    @staticmethod
    def project(name: str, owner: str | None, status: str = "in-progress") -> dict:
        return {"name": name, "title": name.title(), "status": status, "priority": "now", "owner": owner,
                "path": f"docs/projects/{name}/readme.md", "files": [], "reviews": []}

    def test_an_owner_column_lists_each_owner_and_the_filter_matches_it(self) -> None:
        html = self.projects_html([self.project("alpha", "aparsuri-poly"), self.project("beta", None)])

        self.assertIn('<colgroup><col><col class="priority"><col class="owner"><col class="name"></colgroup>'
                      '<thead><tr><th>Project</th><th>Priority</th><th>Owner</th><th class="name">Name</th></tr></thead>',
                      html)
        self.assertIn('<td class="owner">aparsuri-poly</td>', html)
        self.assertIn('<td class="owner"></td>', html)
        self.assertIn('data-search="alpha alpha aparsuri-poly"', html)

    def test_no_owner_column_when_no_plan_names_an_owner(self) -> None:
        html = self.projects_html([self.project("alpha", None), self.project("beta", None)])

        self.assertIn('<colgroup><col><col class="priority"><col class="name"></colgroup>'
                      '<thead><tr><th>Project</th><th>Priority</th><th class="name">Name</th></tr></thead>', html)
        self.assertNotIn("Owner", html)
        self.assertNotIn("owner", html)

    def test_every_status_table_sets_the_same_column_widths(self) -> None:
        html = self.projects_html([self.project("alpha", "octocat"), self.project("alpha/beta", None),
                                   self.project("gamma", None, "ready"), self.project("delta", None, "completed")])

        colgroups = re.findall(r"<colgroup>.*?</colgroup>", html)
        self.assertEqual(3, len(colgroups), "one table for each status")
        self.assertEqual(1, len(set(colgroups)), "every table names the same columns")
        nested = re.search(r'<tr data-search="alpha/beta[^"]*">(.*?)</tr>', html)
        assert nested is not None
        self.assertTrue(nested.group(1).startswith('<td style="padding-left:2rem">'),
                        "a nested project's indent sits inside the project's column")
        self.assertEqual(4, nested.group(1).count("<td"), "a nested row has every column")
        css = (SITE_JS.parent / "site.css").read_text()
        self.assertIn("table.tbl.projects { table-layout: fixed; }", css, "the columns line up from one status to the next")
        for col in ("priority", "owner", "name"):
            self.assertRegex(css, rf"table\.tbl\.projects col\.{col} {{ width: [\d.]+em; }}")
        self.assertRegex(css, r"@media \(max-width: 760px\) \{\s*table\.tbl\.projects \.name \{ display: none; \}",
                         "a narrow window leaves the name out where a section's sidebar moves above its page")
        self.assertIn("@media (max-width: 760px) {\n  .sitemain.withside { display: block; }", css,
                      "the sidebar's breakpoint is the one the name column shares")

    def test_the_manifest_carries_each_owner(self) -> None:
        repo = Path(tempfile.mkdtemp())
        path = repo / "docs/projects/alpha/readme.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nstatus: draft\npriority: now\nowner: aparsuri-poly\n---\n\n# Alpha\n")
        out = Path(tempfile.mkdtemp()) / "site"
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(["site", "build", "--out", str(out), "--repo-root", str(repo)]))

        self.assertEqual("aparsuri-poly", json.loads((out / "site.json").read_text())["projects"][0]["owner"])


PROJECT_PAGE_FUNCTIONS = ("errorMessage", "repoUrl", "contentUrl", "routeFor", "folderOf", "ownerOf", "slug", "badge",
                          "proposedBadge", "nav", "header", "isHtml", "frame", "tree", "showHtml", "showPage",
                          "showDocument", "sectionsOf", "showProjectFile", "projectNav", "itemOf", "foldedSections",
                          "parentOf", "neighborsHtml", "proposalBanner", "reviewFor")


def project_page(site: dict, path: str, body: list[list[str]] | None = None) -> dict:
    """Draw `path`'s page with the compiled showProjectFile under node, with `site` as site.json, and return it.

    The page's Markdown renders as `body`, its elements as [tag, markup], by
    default a heading and a paragraph, and each heading takes the id the
    compiled `slug` gives its text, as renderMarkdown gives it. `page` is the
    markup `frame` draws, with the body's place empty, and `body` what went
    into that place: the rendered elements, after any element showProjectFile
    puts before them, each as [tag, markup]. `ref` is the ref the Markdown
    rendered its links at, and `title` the document's title.
    """
    rendered = [["H1", "Build alpha"], ["P", "Text."]] if body is None else body
    stub = (
        "const base = '/';\n"
        f"const site = {json.dumps(site)};\n"
        "const pageFiles = new Set(), siteFiles = new Set();\n"
        "let fitFrame = null, main = null, ref = null;\n"
        "const location = {hash: '', pathname: '/', search: ''};\n"
        "const window = {innerHeight: 800};\n"
        "const MARK = '<svg class=\"sitemark\"></svg>', MENU_ICON = '<svg menu></svg>', FULL_ICON = '<svg full></svg>',"
        " EXIT_FULL_ICON = '<svg exit></svg>';\n"
        "const root = {innerHTML: ''};\n"
        "function element(tag, html) { return {tagName: tag, innerHTML: html, id: '', className: '', children: [],"
        " parent: null, style: {}, get textContent() { return this.innerHTML.replace(/<[^>]*>/g, ''); },"
        " get firstElementChild() { return this.children[0] ?? null; },"
        " get firstChild() { return this.children[0] ?? null; },"
        " appendChild: function (child) { child.parent = this; this.children.push(child); return child; },"
        " insertBefore: function (child, next) { child.parent = this;"
        " this.children.splice(next === null ? this.children.length : this.children.indexOf(next), 0, child); return child; },"
        " remove: function () { this.parent.children.splice(this.parent.children.indexOf(this), 1); },"
        " getBoundingClientRect: function () { return {top: 0}; }, addEventListener: function () {},"
        " cloneNode: function () { return element(this.tagName, this.innerHTML); },"
        " querySelectorAll: function (selector) { const self = this; return selector !== 'a' ? [] :"
        " Array.from(this.innerHTML.matchAll(/<a\\b[^>]*>(.*?)<\\/a>/g), function (m) { return {childNodes: [m[1]],"
        " replaceWith: function (...nodes) { self.innerHTML = self.innerHTML.replace(m[0], nodes.join('')); }}; }); }}; }\n"
        "const document = {title: '', body: {classList: {remove: function () {}}},"
        " createElement: function (tag) { return element(tag.toUpperCase(), ''); },"
        " getElementById: function () { main = element('DIV', ''); return main; },"
        " querySelector: function () { return null; }};\n"
        "function loadMarkdown() { return {then: function (loaded) { loaded(''); }}; }\n"
        "function renderMarkdown(text, path, at) { ref = at; const box = element('DIV', ''); box.className = 'markdown';"
        f" {json.dumps(rendered)}.forEach(function (e) {{ const el = box.appendChild(element(e[0], e[1]));"
        " if (/^H[1-6]$/.test(e[0])) el.id = slug(el.textContent); }); return box; }\n"
    )
    program = stub + shared_js() + "\n" + page_constants() + "\n" + \
        "\n".join(js_function(n) for n in PROJECT_PAGE_FUNCTIONS) + (
            f"\nshowProjectFile({json.dumps(path)});"
            "\nconst drawn = main.children.flatMap(function (c) { return c.className === 'markdown'"
            " ? c.children.map(function (e) { return [e.tagName, e.innerHTML]; }) : [[c.tagName, c.innerHTML]]; });"
            "\nprocess.stdout.write(JSON.stringify({page: root.innerHTML, body: drawn, ref: ref, title: document.title}));")
    return json.loads(node(program))


@unittest.skipUnless(shutil.which("node"), "the proposed project views need node")
class ProposedViewTests(unittest.TestCase):
    HEAD = "15cb40d18e911face2cd2393af143b526d6392e9"

    def gamma(self, also: list[int] | None = None) -> dict:
        header = {"project": "gamma", "title": "Ship gamma", "url": "/projects/gamma/"}
        return {"name": "gamma", "title": "Ship gamma", "status": "ready", "priority": "next", "owner": None,
                "path": "docs/projects/gamma/readme.md", "files": [], "stacks": [[{"header": header, "members": [9]}]],
                "proposed": {"number": 9, "head": self.HEAD, "also": also or []}}

    @staticmethod
    def alpha() -> dict:
        return {"name": "alpha", "title": "Build alpha", "status": "ready", "priority": "now", "owner": None,
                "path": "docs/projects/alpha/readme.md", "files": [], "stacks": []}

    @staticmethod
    def site(projects: list[dict], title: str = "Plan gamma") -> dict:
        return {"repo": "owner/example", "branch": "main", "content": "content", "readme": None, "docs": [],
                "projectsDir": "docs/projects", "projectsReadme": None, "projects": projects, "files": [],
                "reviews": [{"number": 9, "name": "Summary 9", "title": title, "heads": 1, "stack": None,
                             "state": "open", "updated": "2026-10-10T12:00:00Z",
                             "review": {"status": "changes-requested",
                                        "url": "https://github.com/owner/example/pull/9#pullrequestreview-1"}}]}

    def run_js(self, site: dict, functions: tuple[str, ...], main: str) -> dict:
        """Run `main` under node beside the compiled `functions`, with `site` as site.json, and return what it writes as JSON."""
        stub = (
            "const base = '/';\n"
            f"const site = {json.dumps(site)};\n"
            "const pageFiles = new Set(), siteFiles = new Set();\n"
            "let drawn = '', filtered = null, rows = [];\n"
            "const window = {DOMPurify: {sanitize: function (h) { return h; }}, marked: {parse: function (t) { return t; }}};\n"
            "const links = [];\n"
            "const document = {createElement: function () { return {querySelectorAll: function (selector) {"
            " return selector === 'a[href]' ? links : []; }}; }};\n"
            "function frame(active, title, body) { drawn = body;"
            " rows = Array.from(body.matchAll(/<tr data-search=\"([^\"]*)\">/g),"
            " function (m) { return {dataset: {search: m[1]}, hidden: false}; });"
            " return {querySelector: function () { return {addEventListener: function (type, f) { filtered = f; }}; },"
            " querySelectorAll: function () { return rows; }}; }\n"
        )
        program = stub + shared_js() + "\n" + page_constants() + "\n" + \
            "\n".join(js_function(n) for n in functions) + "\n" + main
        return json.loads(node(program))

    def test_the_projects_page_lists_a_proposed_project_in_its_status_group_with_its_badge(self) -> None:
        drawn = self.run_js(self.site([self.alpha(), self.gamma()]),
                            ("badge", "proposedBadge", "depth", "ownerName", "showProjects"),
                            "showProjects(); process.stdout.write(JSON.stringify({html: drawn}));")["html"]

        self.assertIn('<p class="note">2 projects under <span class="mono">docs/projects</span>, '
                      '1 of them proposed in open pull requests. ', drawn)
        ready = drawn.split("<h2>ready ")[1].split("</section>")[0]
        self.assertIn('<a href="/projects/gamma/">Ship gamma</a> <a class="badge proposed" href="/reviews/9/">'
                      'Proposed #9</a></td>', ready)
        self.assertNotIn("Proposed", ready.split("Ship gamma")[0], "only the proposed row carries the badge")

    def test_the_filter_word_proposed_keeps_only_the_proposed_rows(self) -> None:
        kept = self.run_js(self.site([self.alpha(), self.gamma()]),
                           ("badge", "proposedBadge", "depth", "ownerName", "showProjects"),
                           "showProjects(); filtered({target: {value: 'Proposed'}}); process.stdout.write(JSON.stringify("
                           "rows.filter(function (r) { return !r.hidden; }).map(function (r) { return r.dataset.search; })));")

        self.assertEqual(["gamma ship gamma  proposed"], kept)

    def test_a_proposed_projects_page_opens_its_content_with_the_banner_and_links_github_at_its_head(self) -> None:
        shown = project_page(self.site([self.alpha(), self.gamma(also=[12])]), "docs/projects/gamma/readme.md")

        self.assertEqual(["DIV", "P"], [tag for tag, _ in shown["body"]],
                         "the banner opens the content column, above the plan, whose heading the title row shows")
        banner = shown["body"][0][1]
        self.assertIn('<div class="proposal"><p><b>Proposed in</b> <a href="/reviews/9/">#9</a> Plan gamma · '
                      '<span class="eyebrow"><a class="rstatus changes-requested" '
                      'href="https://github.com/owner/example/pull/9#pullrequestreview-1" target="_blank" '
                      'rel="noopener">Changes requested</a></span></p>', banner)
        self.assertIn('is not on <span class="mono">main</span> yet. It shows head <span class="mono">15cb40d</span>', banner)
        self.assertIn('A plan of this name is also proposed in <a href="/reviews/12/">#12</a>.', banner)
        top = re.search(r'<header class="top">.*?</header>', shown["page"])
        assert top is not None
        self.assertIn('<span class="badge status-ready">ready</span>', top.group(0), "the badges sit in the title row")
        self.assertNotIn("proposal", shown["page"], "the banner opens the content column, below the title row")
        self.assertIn(f' · <a href="https://github.com/owner/example/blob/{self.HEAD}/docs/projects/gamma/'
                      'readme.md">View on GitHub</a></div>', top.group(0))
        self.assertIn(f'<a class="history" href="https://github.com/owner/example/commits/{self.HEAD}/docs/projects/gamma/'
                      'readme.md"', shown["page"], "the site's branch lacks the file, so its history is at the head")
        self.assertIn('<nav class="nav" aria-label="Project"><div class="prblock">'
                      '<div class="stackhead current" aria-current="page"><span>Ship gamma</span></div><div class="stackgroup">'
                      '<ul class="stack"', shown["page"])
        self.assertEqual(self.HEAD, shown["ref"], "the page renders its links at the proposal's head")

    def test_a_relative_link_to_a_file_the_site_lacks_points_at_the_given_ref(self) -> None:
        functions = ("repoUrl", "contentUrl", "routeFor", "resolve", "stripFrontmatter", "slug", "renderMarkdown")
        links = self.run_js(self.site([self.gamma()]), functions,
                            "links.push({attrs: {href: '../../../src/app.py'}, getAttribute: function (n) {"
                            " return this.attrs[n]; }, setAttribute: function (n, v) { this.attrs[n] = v; }});"
                            f" renderMarkdown('text', 'docs/projects/gamma/readme.md', {json.dumps(self.HEAD)});"
                            " process.stdout.write(JSON.stringify(links.map(function (a) { return a.attrs.href; })));")

        self.assertEqual([f"https://github.com/owner/example/blob/{self.HEAD}/src/app.py"], links)

    def test_a_checkout_projects_page_has_no_banner_and_its_sidebar_badges_a_proposed_nested_project(self) -> None:
        delta = dict(self.gamma(), name="alpha/delta", title="Add delta", path="docs/projects/alpha/delta/readme.md")

        shown = project_page(self.site([self.alpha(), delta]), "docs/projects/alpha/readme.md")

        self.assertNotIn("proposal", json.dumps(shown))
        self.assertIn("/blob/main/docs/projects/alpha/readme.md", shown["page"])
        self.assertEqual("main", shown["ref"])
        pages = re.search(r'<div class="navpages">(.*?)</div>', shown["page"])
        assert pages is not None
        self.assertIn('>Add delta</a> <a class="badge proposed" href="/reviews/9/">Proposed #9</a>', pages.group(1))

    def test_a_pull_request_title_shows_as_text(self) -> None:
        shown = project_page(self.site([self.gamma()], title="<script>alert(1)</script>"), "docs/projects/gamma/readme.md")

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", shown["body"][0][1])
        self.assertNotIn("<script>", json.dumps(shown))

    def test_a_search_result_for_a_proposed_page_carries_the_badge(self) -> None:
        index = [{"route": "projects/gamma/", "title": "Ship gamma", "kind": "project", "text": "Gamma text",
                  "proposed": 9},
                 {"route": "projects/alpha/", "title": "Build alpha", "kind": "project", "text": "Gamma too"}]
        results = self.run_js(self.site([]), ("proposedBadge", "snippet", "highlight", "search"),
                              f"process.stdout.write(JSON.stringify(search('gamma', {json.dumps(index)})));")

        self.assertIn('<span class="note">project</span> <a class="badge proposed" href="/reviews/9/">Proposed #9</a>'
                      '<div class="snippet">', results[0])
        self.assertNotIn("badge", results[1])


MINIMAL_SUMMARY = {"pr": {"repo": "owner/example", "number": 9, "title": "Change 9", "head": "c" * 40},
                   "files": [], "groups": [], "segments": [{"header": {"title": "Summary 9"}, "members": [9]}],
                   "header": {"title": "Summary 9"},
                   "stats": {"files": 0, "adds": 0, "dels": 0, "hand": 0, "test": 0, "generated": 0, "docs": 0}}


@unittest.skipUnless(shutil.which("node"), "the project page tests need node")
class ProjectPageTests(unittest.TestCase):
    """A project's page in a summary page's layout, with a summary's sidebar of its stacks and pages."""

    ALPHA = {"project": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}
    BETA = {"project": "alpha/beta", "title": "Build beta", "url": "/projects/alpha/beta/"}
    README = "docs/projects/alpha/readme.md"

    @staticmethod
    def project(name: str, title: str, files: tuple[tuple[str, str], ...] = (), stacks: list | None = None,
                owner: str | None = None) -> dict:
        return {"name": name, "title": title, "status": "in-progress", "priority": "now", "owner": owner,
                "path": f"docs/projects/{name}/readme.md", "stacks": stacks or [],
                "files": [{"path": f"docs/projects/{name}/{file}", "title": file_title,
                           "route": f"projects/{name}/{file.rsplit('.', 1)[0]}/"} for file, file_title in files]}

    def site(self, alpha: list | None = None, beta: list | None = None, title: str = "Build alpha",
             owner: str | None = None, more: tuple[dict, ...] = ()) -> dict:
        """site.json with `alpha` and its nested `alpha/beta`, each with its stacks and files, and the reviews of
        #8 to #11. Alpha's files are notes.md, research/survey.md, and an HTML page, demo.html."""
        projects = [self.project("alpha", title, (("notes.md", "Alpha notes"), ("research/survey.md", "Survey"),
                                                  ("demo.html", "Alpha demo")), alpha, owner),
                    self.project("alpha/beta", "Build beta", (("design.md", "Beta design"),), beta), *more]
        reviews = [{"number": n, "title": f"Change {n}", "header": self.ALPHA, "heads": 1, "stack": None,
                    "updated": "2026-10-10T12:00:00Z"} for n in (11, 10, 9, 8)]
        return {"repo": "owner/example", "branch": "main", "content": "content", "readme": None, "docs": [],
                "projectsDir": "docs/projects", "projectsReadme": None, "projects": projects, "files": [],
                "reviews": reviews}

    @staticmethod
    def stack(*segments: tuple[dict, list[int]]) -> list[dict]:
        return [{"header": header, "members": members} for header, members in segments]

    ALPHA_HERE = '<div class="stackhead current" aria-current="page"><span>Build alpha</span></div>'
    ALPHA_LINK = '<div class="stackhead"><a href="/projects/alpha/">Build alpha</a></div>'
    BETA_LINK = '<div class="stackhead"><a href="/projects/alpha/beta/">Build beta</a></div>'

    @staticmethod
    def sidebar(page: str) -> str:
        found = re.search(r'<aside class="side"[^>]*>(.*?)</aside>', page)
        assert found is not None, page
        return found.group(1)

    @staticmethod
    def outline(side: str) -> list[str]:
        """The sidebar's headers and rows in order: each header's markup, `|` where a stack starts, `-` where a
        list opens with a rule, and `#n` for each row."""
        found = re.findall(r'(<div class="stackhead[^>]*>.*?</div>)|(<div class="stackgroup">)|(<ul class="stack split")|'
                           r'<span class="snum">(#\d+)</span>', side)
        return [head or ("|" if group else "-" if split else row) for head, group, split, row in found]

    def drawn(self, site: dict, path: str = README, body: list[list[str]] | None = None) -> tuple[dict, str]:
        """The page drawn for `path`, and its sidebar, which holds exactly one current page and no current location,
        which only the watch on its sections marks."""
        shown = project_page(site, path, body)
        side = self.sidebar(shown["page"])
        self.assertEqual(1, side.count('aria-current="page"'), side)
        self.assertEqual(1, side.count("aria-current"), side)
        return shown, side

    # The layout

    def test_a_readme_takes_a_summary_pages_layout_under_its_title_row(self) -> None:
        shown, _ = self.drawn(self.site([self.stack((self.ALPHA, [9, 10, 11]))]))

        page = shown["page"]
        self.assertTrue(page.startswith('<header class="sitebar scrolls"><span class="sitebtn menuspace"'), page)
        self.assertIn('<main class="sitemain withnav"><div class="wrap"><header class="top"><div><div class="eyebrow">'
                      '<a href="/">Projects</a> / <span class="mono">alpha</span> · '
                      '<span class="badge status-in-progress">in-progress</span><span class="badge priority-now">now</span> · '
                      '<a href="https://github.com/owner/example/blob/main/docs/projects/alpha/readme.md">View on GitHub</a>'
                      '</div><h1>Build alpha</h1></div></header>'
                      '<div class="layout"><aside class="side"><nav class="nav" aria-label="Project">', page)
        self.assertTrue(page.endswith('</nav></aside><div class="sidebody" id="sidebody"></div></div></div></main>'), page)
        self.assertNotIn("sitefoot", page, "the sidebar's footer row carries the credit")
        self.assertNotIn("phead", page)
        self.assertEqual("Build alpha · owner/example", shown["title"])

    def test_a_readme_names_its_owner_in_the_title_row(self) -> None:
        shown, _ = self.drawn(self.site(owner="octocat"))

        self.assertIn('<span class="badge priority-now">now</span> · Owner octocat · <a href=', shown["page"])

    def test_a_summary_page_and_a_project_page_draw_one_title_row(self) -> None:
        summary_top = re.search(r'<header class="top">.*?</header>', rendered_summary(MINIMAL_SUMMARY))
        project_top = re.search(r'<header class="top">.*?</header>', self.drawn(self.site())[0]["page"])
        assert summary_top is not None and project_top is not None

        def skeleton(top: str) -> str:
            top = re.sub(r'<div class="sub">.*</div></header>$', '</header>', top)
            return re.sub(r'<div class="eyebrow">.*?</div><h1>.*?</h1>', '<div class="eyebrow"></div><h1></h1>', top)

        self.assertIn('<div class="sub">', summary_top.group(0))
        self.assertEqual('<header class="top"><div><div class="eyebrow"></div><h1></h1></div></header>',
                         skeleton(summary_top.group(0)))
        self.assertEqual(skeleton(summary_top.group(0)), skeleton(project_top.group(0)))

    def test_the_plan_leaves_out_its_first_heading_which_the_title_row_shows(self) -> None:
        for case, body, expected in (
                ("an h1 first", [["H1", "Build alpha"], ["P", "Text."], ["H2", "1. Outcome"]],
                 [["P", "Text."], ["H2", "1. Outcome"]]),
                ("a paragraph, then an h1", [["P", "Intro."], ["H1", "Build alpha"]],
                 [["P", "Intro."], ["H1", "Build alpha"]]),
                ("an h2 first", [["H2", "1. Outcome"], ["P", "Text."]], [["H2", "1. Outcome"], ["P", "Text."]])):
            with self.subTest(case):
                self.assertEqual(expected, self.drawn(self.site(), body=body)[0]["body"])

    def test_a_supplemental_pages_title_row_ends_its_breadcrumbs_in_the_file(self) -> None:
        shown, _ = self.drawn(self.site([self.stack((self.ALPHA, [9]))]), "docs/projects/alpha/notes.md",
                              [["H1", "Alpha notes"], ["P", "Text."]])

        self.assertIn('<div class="eyebrow"><a href="/">Projects</a> / <a href="/projects/alpha/">alpha</a> / '
                      '<span class="mono">notes.md</span> · <a href="https://github.com/owner/example/blob/main/docs/projects/alpha/'
                      'notes.md">View on GitHub</a></div><h1>Alpha notes</h1>', shown["page"])
        self.assertEqual([["P", "Text."]], shown["body"])

    def test_an_html_page_keeps_its_frame_with_the_sidebar_in_its_column(self) -> None:
        shown, _ = self.drawn(self.site([self.stack((self.ALPHA, [9]))]), "docs/projects/alpha/demo.html")

        page = shown["page"]
        self.assertIn('class="sitebtn sidetoggle" aria-controls="siteside" aria-expanded="false"', page)
        self.assertIn('<main class="sitemain withside wide collapsed"><aside class="side" id="siteside">'
                      '<nav class="nav" aria-label="Project">', page)
        self.assertNotIn('<header class="top">', page)
        self.assertEqual(["DIV", "IFRAME"], [tag for tag, _ in shown["body"]])
        self.assertEqual('<div class="crumbs"><a href="/">Projects</a> / <a href="/projects/alpha/">alpha</a> / '
                         '<span class="mono">demo.html</span></div><div class="pmeta"><a class="note" href="https://github.com/owner/example/blob/'
                         'main/docs/projects/alpha/demo.html">View on GitHub</a></div>', shown["body"][0][1])

    # The sidebar

    def test_a_readme_opens_its_sidebar_with_its_own_header_current_and_its_stack_as_rows(self) -> None:
        shown, side = self.drawn(self.site([self.stack((self.ALPHA, [9, 10, 11]))]))

        self.assertTrue(side.startswith(f'<nav class="nav" aria-label="Project"><div class="prblock">{self.ALPHA_HERE}'
                                        '<div class="stackgroup"><ul class="stack" aria-label="Pull requests in this stack">'),
                        side)
        self.assertEqual([self.ALPHA_HERE, "|", "#9", "#10", "#11"], self.outline(side),
                         "a stack that is all this project's shows only rows")
        self.assertEqual(["/reviews/9/", "/reviews/10/", "/reviews/11/"], re.findall(r'<a class="srow" href="([^"]*)">', side))
        self.assertNotIn("srow current", side, "no pull request is this page")
        self.assertNotIn("plist", shown["page"])

    def test_a_project_lists_each_of_its_stacks_below_its_one_header(self) -> None:
        _, side = self.drawn(self.site([self.stack((self.ALPHA, [9, 10, 11])), self.stack((self.ALPHA, [8]))]))

        self.assertEqual([self.ALPHA_HERE, "|", "#9", "#10", "#11", "|", "#8"], self.outline(side),
                         "the header shows once, above the stacks, newest first")

    def test_a_stack_of_two_projects_heads_only_the_other_projects_segment_with_a_link(self) -> None:
        _, side = self.drawn(self.site([self.stack((self.BETA, [9]), (self.ALPHA, [10]))]))

        self.assertEqual([self.ALPHA_HERE, "|", self.BETA_LINK, "#9", "-", "#10"], self.outline(side))
        self.assertIn(f'{self.ALPHA_HERE}<div class="stackgroup">{self.BETA_LINK}'
                      '<ul class="stack" aria-label="Pull requests for Build beta">', side)

    def test_a_projects_segment_after_another_projects_opens_with_a_rule_not_its_header(self) -> None:
        # As #222, recording `project-page-sections`, sits on #219, recording `project-page-nav`.
        _, side = self.drawn(self.site([self.stack((self.BETA, [9]), (self.ALPHA, [10]))]))

        self.assertIn(f'{self.BETA_LINK}<ul class="stack" aria-label="Pull requests for Build beta"><li><a class="srow" '
                      'href="/reviews/9/"><span class="snum">#9</span><span class="stitle">Change 9</span></a></li></ul>'
                      '<ul class="stack split" aria-label="Pull requests for Build alpha"><li><a class="srow" '
                      'href="/reviews/10/">', side, "a rule, not alpha's name, ends beta's segment above #10")
        self.assertEqual(1, side.count(">Build alpha<"), "the project's name shows once, at the top")
        css = (SITE_JS.parent / "summary.css").read_text()
        self.assertIn(".prblock .stack.split { margin-top: 4px; padding-top: 4px; border-top: 1px solid var(--border); }",
                      css)

    def test_a_stack_that_returns_to_the_project_never_repeats_its_header(self) -> None:
        _, side = self.drawn(self.site([self.stack((self.ALPHA, [9]), (self.BETA, [10]), (self.ALPHA, [11])),
                                        self.stack((self.ALPHA, [8]))]))

        self.assertEqual([self.ALPHA_HERE, "|", "#9", self.BETA_LINK, "#10", "-", "#11", "|", "#8"], self.outline(side),
                         "a rule ends beta's segment, and none opens a stack, which its group's divider does")

    def test_a_nested_project_heads_its_sidebar_and_lists_only_its_own_pages(self) -> None:
        stack = self.stack((self.BETA, [9]), (self.ALPHA, [10]))

        _, side = self.drawn(self.site([stack], [stack]), "docs/projects/alpha/beta/readme.md")

        self.assertEqual(['<div class="stackhead current" aria-current="page"><span>Build beta</span></div>',
                          "|", "#9", self.ALPHA_LINK, "#10"], self.outline(side))
        pages = re.search(r'<div class="navpages">(.*?)</div>', side)
        assert pages is not None
        self.assertEqual(['<a href="/projects/alpha/beta/design/">Beta design</a>'],
                         re.findall(r"<a [^>]*>.*?</a>", pages.group(1)))

    def test_a_layer_without_a_review_links_github_with_no_title(self) -> None:
        _, side = self.drawn(self.site([self.stack((self.ALPHA, [9, 12]))]))

        self.assertIn('<a class="srow" href="https://github.com/owner/example/pull/12"><span class="snum">#12</span>'
                      '<span class="stitle"></span></a>', side)

    def test_a_project_without_stacks_shows_its_header_and_says_so(self) -> None:
        _, side = self.drawn(self.site())

        self.assertIn('<div class="prblock"><div class="stackhead current" aria-current="page"><span>Build alpha</span>'
                      '</div><p class="snone">No reviews of this project.</p></div>', side)
        self.assertNotIn('<ul class="stack"', side)

    def test_a_supplemental_page_links_the_project_and_is_current_among_the_pages(self) -> None:
        _, side = self.drawn(self.site([self.stack((self.ALPHA, [9]))]), "docs/projects/alpha/notes.md")

        self.assertEqual([self.ALPHA_LINK, "|", "#9"], self.outline(side))
        self.assertIn('<div class="navpages"><ul class="sidetree">', side)
        self.assertIn('<a href="/projects/alpha/notes/" class="active" aria-current="page">Alpha notes</a>', side)

    def test_a_projects_pages_are_its_files_and_nested_projects_without_its_readme(self) -> None:
        _, side = self.drawn(self.site())

        pages = re.search(r'<div class="navpages">(.*?)</div>', side)
        assert pages is not None
        self.assertEqual(["Alpha demo", "Alpha notes", "Build beta", "Beta design", "Survey"],
                         re.findall(r"<a [^>]*>(.*?)</a>", pages.group(1)))
        self.assertNotIn('href="/projects/alpha/"', pages.group(1), "the project's headers link its readme")

    def test_a_project_with_only_its_readme_lists_no_pages(self) -> None:
        _, side = self.drawn(self.site(more=(self.project("gamma", "Ship gamma"),)), "docs/projects/gamma/readme.md")

        self.assertNotIn("navpages", side)

    def test_the_sidebar_ends_with_the_credit_and_the_pages_history(self) -> None:
        for path in (self.README, "docs/projects/alpha/notes.md"):
            with self.subTest(path):
                _, side = self.drawn(self.site([self.stack((self.ALPHA, [9]))]), path)

                self.assertIn('<div class="navfoot"><div class="credit">Generated by <a href="https://projector.bot">'
                              'Projector</a></div><a class="history" href="https://github.com/owner/example/commits/main/'
                              f'{path}" aria-label="History on GitHub" title="History on GitHub"><svg', side)
                self.assertTrue(side.endswith("</svg></a></div></nav>"), side)

    def test_a_project_title_is_escaped(self) -> None:
        title = "<script>alert(1)</script>"
        for stacks in ([self.stack((dict(self.BETA, title=title), [9]), (dict(self.ALPHA, title=title), [10]))], []):
            with self.subTest(stacks=len(stacks)):
                shown, side = self.drawn(self.site(stacks, title=title))

                self.assertIn('<div class="stackhead current" aria-current="page">'
                              "<span>&lt;script&gt;alert(1)&lt;/script&gt;</span></div>", side)
                if stacks:
                    self.assertIn('<a href="/projects/alpha/beta/">&lt;script&gt;alert(1)&lt;/script&gt;</a>', side)
                self.assertIn("<h1>&lt;script&gt;alert(1)&lt;/script&gt;</h1>", shown["page"])
                self.assertNotIn("<script>", shown["page"])

    # The sections and the neighbors

    FOLD = ('<div class="navsections"><button type="button" class="navfold" aria-expanded="false" '
            'aria-controls="pagesections"><span class="chev" aria-hidden="true"></span>Sections</button>'
            '<ol class="sections" id="pagesections">')

    @staticmethod
    def section_rows(side: str) -> list[tuple[str, str, str]]:
        """Each listed section's link target, number, and title markup."""
        return re.findall(r'<li><a href="#([^"]*)"><span class="nnum">([^<]*)</span><span class="ntitle">(.*?)</span></a></li>',
                          side)

    def test_a_readme_lists_its_sections_between_its_stacks_and_its_pages(self) -> None:
        body = [["H1", "Build alpha"], ["P", "Intro."], ["H2", "1. Problem"], ["H3", "1.1 Detail"], ["H2", "2. Solution"],
                ["H2", "Background"]]

        _, side = self.drawn(self.site([self.stack((self.ALPHA, [9]))]), body=body)

        self.assertIn(f'</div></div>{self.FOLD}<li><a href="#1-problem">', side, "the block follows the stacks")
        self.assertEqual([("1-problem", "1", "Problem"), ("2-solution", "2", "Solution"), ("background", "", "Background")],
                         self.section_rows(side), "## only, each number in its own column, and none for Background")
        self.assertLess(side.index("navsections"), side.index('<div class="navpages">'))

    def test_a_section_number_is_digits_and_a_dot_or_a_dotted_number(self) -> None:
        for heading, expected in (("10. Outcome", ("10", "Outcome")), ("2.1 Detail", ("2.1", "Detail")),
                                  ("2.1. Detail", ("2.1", "Detail")), ("2026 roadmap", ("", "2026 roadmap")),
                                  ("1.5x faster", ("", "1.5x faster"))):
            with self.subTest(heading):
                _, side = self.drawn(self.site(), body=[["H2", heading]])

                self.assertEqual([expected], [row[1:] for row in self.section_rows(side)])

    def test_a_section_title_keeps_its_code_and_drops_its_links(self) -> None:
        body = [["H2", "3. <code>project init</code> writes"], ["H2", '4. See <a href="https://example.com">the guide</a>']]

        _, side = self.drawn(self.site(), body=body)

        self.assertEqual([("3-project-init-writes", "3", "<code>project init</code> writes"),
                          ("4-see-the-guide", "4", "See the guide")], self.section_rows(side))

    def test_a_page_without_a_top_level_section_lists_none(self) -> None:
        _, side = self.drawn(self.site(), body=[["P", "Intro."], ["BLOCKQUOTE", "<h2>1. Quoted</h2>"], ["H3", "1.1 Sub"]])

        self.assertNotIn("navsections", side)
        self.assertNotIn("<ol", side)

    def test_a_supplemental_page_lists_its_sections_under_its_entry(self) -> None:
        body = [["H1", "Alpha notes"], ["H2", "1. First"], ["H2", "2. Second"]]

        _, side = self.drawn(self.site([self.stack((self.ALPHA, [9]))]), "docs/projects/alpha/notes.md", body)

        self.assertIn(f'<li><a href="/projects/alpha/notes/" class="active" aria-current="page">Alpha notes</a>{self.FOLD}'
                      '<li><a href="#1-first">', side)
        self.assertEqual(["1-first", "2-second"], [row[0] for row in self.section_rows(side)])
        self.assertEqual(1, side.count("navsections"), "the readme's block is not drawn as well")
        self.assertLess(side.index('<div class="navpages">'), side.index("navsections"))

    def test_an_html_page_lists_no_sections(self) -> None:
        shown, side = self.drawn(self.site(), "docs/projects/alpha/demo.html")

        self.assertNotIn("navsections", shown["page"])
        self.assertIn('<a href="/projects/alpha/demo/" class="active" aria-current="page">Alpha demo</a></li>', side)

    def test_a_nested_project_lists_its_parent_and_siblings_after_its_pages(self) -> None:
        site = self.site(more=(self.project("alpha/gamma", "Build gamma"), self.project("alpha/beta/zeta", "Build zeta")))

        _, side = self.drawn(site, "docs/projects/alpha/beta/readme.md")

        self.assertIn('<div class="navnear"><div class="sidetop"><a href="/projects/alpha/">Build alpha</a></div>'
                      '<ul class="sidetree"><li><a href="/projects/alpha/gamma/">Build gamma</a></li></ul></div>'
                      '<div class="navfoot">', side, "beta's own nested project is not its sibling")
        pages = side[side.index('<div class="navpages">'):side.index('<div class="navnear">')]
        self.assertIn('<a href="/projects/alpha/beta/zeta/">Build zeta</a>', pages)
        self.assertNotIn("Build gamma", pages)
        self.assertEqual(1, side.count(">Build beta<"), "the current project is in neither block")

    def test_a_nested_projects_neighbors_follow_its_sections_when_it_has_no_pages(self) -> None:
        site = self.site(more=(self.project("alpha/gamma", "Build gamma"),))

        _, side = self.drawn(site, "docs/projects/alpha/gamma/readme.md", [["H2", "1. Problem"]])

        self.assertNotIn("navpages", side)
        self.assertIn('</ol></div><div class="navnear"><div class="sidetop"><a href="/projects/alpha/">Build alpha</a></div>'
                      '<ul class="sidetree"><li><a href="/projects/alpha/beta/">Build beta</a></li></ul></div>', side)

    def test_a_top_level_project_lists_no_neighbors(self) -> None:
        _, side = self.drawn(self.site(more=(self.project("gamma", "Ship gamma"),)))

        self.assertNotIn("navnear", side)

    def test_the_sidebar_holds_its_stacks_sections_pages_neighbors_and_footer_in_that_order(self) -> None:
        site = self.site([self.stack((self.ALPHA, [9]))], [self.stack((self.BETA, [10]))],
                         more=(self.project("alpha/gamma", "Build gamma"),))

        _, side = self.drawn(site, "docs/projects/alpha/beta/readme.md", [["H2", "1. Problem"]])

        blocks = ('<div class="prblock">', '<div class="navsections">', '<div class="navpages">', '<div class="navnear">',
                  '<div class="navfoot">')
        self.assertEqual(sorted(side.index(b) for b in blocks), [side.index(b) for b in blocks])


@unittest.skipUnless(shutil.which("node"), "the section watch tests need node")
class SectionWatchTests(unittest.TestCase):
    """The watch that marks the section in view, run under node with a stub IntersectionObserver and a window 1,000
    pixels tall."""

    # The stub observer reports as a browser's does: once with every target when it starts, and then with each
    # target whose intersection with its band changed, edges included, when `settle` runs after a scroll. Its band
    # is the window grown or shrunk by its root margin. Each heading is 40 pixels tall, at a place on the page, and
    # the end marker is a line at `container.endTop`.
    STUB = (
        "const observers = [], ends = [], targets = {};\n"
        "function viewTop(element) { return element.pageTop - window.scrollY; }\n"
        "class IntersectionObserver { constructor(callback, options) { this.callback = callback; this.options = options ?? {};"
        " this.targets = []; this.disconnected = false; this.seen = new Map(); observers.push(this); }"
        " observe(target) { this.targets.push(target); } disconnect() { this.disconnected = true; }"
        " report() { const m = (this.options.rootMargin ?? '0px 0px 0px 0px').split(' ').map(function (v) {"
        " return v.endsWith('%') ? parseFloat(v) / 100 * window.innerHeight : parseFloat(v); });"
        " const top = -m[0], bottom = window.innerHeight + m[2], seen = this.seen;"
        " const entries = this.targets.flatMap(function (t) {"
        " const now = viewTop(t) <= bottom && viewTop(t) + t.height >= top;"
        " if (seen.get(t) === now) return []; seen.set(t, now); return [{target: t, isIntersecting: now}]; });"
        " if (!this.disconnected && entries.length > 0) this.callback(entries); } }\n"
        "function settle() { observers.forEach(function (o) { o.report(); }); }\n"
        "function scrollTo(y) { window.scrollY = y; settle(); }\n"
        "const window = {innerHeight: 1000, scrollY: 0, IntersectionObserver: IntersectionObserver};\n"
        "const container = {endTop: 100000, after: function (element) { element.after = this; element.pageTop = this.endTop;"
        " ends.push(element); }};\n"
        "const document = {getElementById: function (id) { return targets[id] ?? null; },"
        " createElement: function () { return {attrs: {}, removed: false, pageTop: 0, height: 0,"
        " setAttribute: function (n, v) { this.attrs[n] = v; }, remove: function () { this.removed = true; },"
        " getBoundingClientRect: function () { return {top: viewTop(this)}; }}; }};\n"
        "function heading(id, top) { targets[id] = {id: id, pageTop: top + window.scrollY, height: 40, parentElement: container,"
        " getBoundingClientRect: function () { return {top: viewTop(this)}; }}; }\n"
        "function list(ids) { const links = ids.map(function (id) { return {attrs: {href: '#' + id},"
        " getAttribute: function (n) { return this.attrs[n] ?? null; }, setAttribute: function (n, v) { this.attrs[n] = v; },"
        " removeAttribute: function (n) { delete this.attrs[n]; }}; });"
        " return {links: links, querySelectorAll: function () { return links; }}; }\n"
        "function marks(l) { return l.links.map(function (a) { return a.attrs['aria-current'] ?? null; }); }\n"
    )

    def run_watch(self, program: str) -> object:
        return json.loads(node(self.STUB + shared_js() + "\n" + program))

    def marks(self, tops: list[int], end: bool | None = None, scrolled: int = 0) -> list[str | None]:
        """The marks after the watch on sections whose tops are `tops` in the window hears from its targets, and then,
        when `end` is given, hears whether the marker after the sections is in view, on a page scrolled by `scrolled`."""
        ids = [f"s{i}" for i in range(len(tops))]
        return self.run_watch(
            f"window.scrollY = {scrolled};\n" +
            "".join(f"heading({json.dumps(i)}, {top});\n" for i, top in zip(ids, tops)) +
            f"const l = list({json.dumps(ids)});\nwatchSections(l);\n"
            "observers[0].callback([]);\n" +
            (f"observers[1].callback([{{isIntersecting: {json.dumps(end)}}}]);\n" if end is not None else "") +
            "process.stdout.write(JSON.stringify(marks(l)));")

    def test_the_last_section_above_the_line_30_percent_down_the_window_is_marked(self) -> None:
        self.assertEqual([None, "location", None], self.marks([-500, 200, 600]))
        self.assertEqual([None, None, "location"], self.marks([-900, -500, 300]))

    def test_no_section_is_marked_above_the_first(self) -> None:
        self.assertEqual([None, None], self.marks([400, 900]))

    def test_at_the_end_of_a_scrolled_page_the_last_section_in_view_is_marked(self) -> None:
        self.assertEqual([None, None, "location", None], self.marks([-500, 200, 600, 1200], end=True, scrolled=100))

    def test_the_line_stays_where_it_is_at_the_end_of_a_page_at_its_top_or_away_from_the_end(self) -> None:
        self.assertEqual([None, "location", None], self.marks([-500, 200, 600], end=True, scrolled=0))
        self.assertEqual([None, "location", None], self.marks([-500, 200, 600], end=False, scrolled=100))

    def test_a_jump_that_carries_a_heading_past_the_line_and_out_of_the_window_moves_the_mark(self) -> None:
        # As a link to a ### heading, the Home key, or a dragged scrollbar
        # does: from the top, where no heading has reached the line, into the
        # middle of the third section, then to the end, then back to the top.
        seen = self.run_watch(
            "container.endTop = 9500;\nheading('a', 400); heading('b', 3400); heading('c', 6400);\n"
            "const l = list(['a', 'b', 'c']);\nwatchSections(l);\nsettle();\nconst seen = [marks(l)];\n"
            "scrollTo(7400); seen.push(marks(l));\nscrollTo(4000); seen.push(marks(l));\n"
            "scrollTo(8600); seen.push(marks(l));\nscrollTo(0); seen.push(marks(l));\n"
            "process.stdout.write(JSON.stringify(seen));")

        self.assertEqual([[None, None, None], [None, None, "location"], [None, "location", None],
                          [None, None, "location"], [None, None, None]], seen)

    def test_the_watch_reports_targets_crossing_the_line_and_the_end_marker_against_the_window(self) -> None:
        seen = self.run_watch(
            "heading('a', 0); heading('b', 0);\nconst l = list(['a', 'missing', 'b']);\nwatchSections(l);\n"
            "process.stdout.write(JSON.stringify({options: observers.map(function (o) { return o.options; }),"
            " targets: observers.map(function (o) { return o.targets.map(function (t) { return t.id ?? t.attrs; }); }),"
            " after: ends[0].after === container}));")

        self.assertEqual([{"rootMargin": "10000000px 0px -70% 0px"}, {}], seen["options"],
                         "the targets' band runs from far above the page down to the line")
        self.assertEqual([["a", "b"], [{"aria-hidden": "true"}]], seen["targets"], "a link to no section is left out")
        self.assertTrue(seen["after"], "the end marker follows the sections' container")

    def test_a_new_watch_or_none_stops_the_one_before_and_removes_its_end_marker(self) -> None:
        seen = self.run_watch(
            "heading('a', 0);\nwatchSections(list(['a']));\nwatchSections(list(['a']));\n"
            "const second = [ends[0].removed, observers.map(function (o) { return o.disconnected; })];\n"
            "watchSections(null);\n"
            "process.stdout.write(JSON.stringify({second: second, none: [ends[1].removed,"
            " observers.map(function (o) { return o.disconnected; })], count: observers.length}));")

        self.assertEqual([True, [True, True, False, False]], seen["second"])
        self.assertEqual([True, [True, True, True, True]], seen["none"])
        self.assertEqual(4, seen["count"], "watching nothing makes no observer")

    def test_a_summary_lists_its_sections_with_the_shared_list_and_its_check_dots(self) -> None:
        data = dict(MINIMAL_SUMMARY, groups=[{"id": "g-one", "title": "First <code>x</code>", "files": []},
                                             {"id": "g-two", "title": "Second", "files": []}])

        html = rendered_summary(data)

        self.assertIn('<a href="#files">Files</a></div><ol class="sections">'
                      '<li><a href="#g-one"><span class="nnum">1</span><span class="ntitle">First <code>x</code></span>'
                      '<span class="ncheck" data-gid="g-one"></span></a></li>'
                      '<li><a href="#g-two"><span class="nnum">2</span><span class="ntitle">Second</span>'
                      '<span class="ncheck" data-gid="g-two"></span></a></li></ol>', html)


class SectionStyleTests(unittest.TestCase):
    """The rules that fold a project page's sections on a narrow screen and mark the section in view."""

    @staticmethod
    def blocks(sheet: str) -> tuple[str, dict[str, str]]:
        """`sheet`'s rules outside every media query, and the body of each media query by its condition."""
        css = re.sub(r"/\*.*?\*/", "", (SITE_JS.parent / sheet).read_text(), flags=re.S)
        outside, queries, at = [], {}, 0
        for found in re.finditer(r"@media ([^{]+)\{", css):
            if found.start() < at:
                continue
            depth, end = 1, found.end()
            while depth:
                depth += {"{": 1, "}": -1}.get(css[end], 0)
                end += 1
            outside.append(css[at:found.start()])
            queries[found.group(1).strip()] = queries.get(found.group(1).strip(), "") + css[found.end():end - 1]
            at = end
        outside.append(css[at:])
        return "".join(outside), queries

    def test_a_wide_screen_hides_the_toggle_and_a_narrow_one_folds_the_list_behind_it(self) -> None:
        outside, queries = self.blocks("site.css")

        self.assertRegex(outside, r"(^|\n)\.nav \.navfold \{ display: none; \}")
        self.assertNotRegex(outside, r"aria-expanded=\"false\"\] \+ ol")
        narrow = queries["(max-width: 980px)"]
        self.assertRegex(narrow, r"\.nav \.navfold \{\s*display: grid;")
        self.assertIn('.nav .navfold[aria-expanded="false"] + ol { display: none; }', narrow)

    def test_the_section_in_view_is_marked_in_every_list_of_sections(self) -> None:
        outside, _ = self.blocks("summary.css")

        self.assertIn('.nav ol a[aria-current="location"] { position: relative; background: var(--surface-2); }', outside)
        self.assertRegex(outside, r'\.nav ol a\[aria-current="location"\]::before \{[^}]*background: var\(--accent\);')


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
        program = stub + shared_js() + "\n" + js_function("header") + \
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
        program = shared_js() + "\n" + js_function("highlight") + \
            f"\nprocess.stdout.write(highlight({json.dumps(text)}, {json.dumps(words)}));"
        return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout

    def test_a_later_word_never_matches_inside_an_earlier_mark(self) -> None:
        self.assertEqual("Write <mark>a</mark> <mark>plan</mark> before you st<mark>a</mark>rt.",
                         self.highlight("Write a plan before you start.", ["plan", "a"]))

    def test_a_word_never_matches_inside_an_escaped_entity(self) -> None:
        self.assertEqual("Tom &amp; Jerry <mark>map</mark>", self.highlight("Tom & Jerry map", ["map", "amp"]))

    def test_the_text_is_escaped_and_matching_ignores_case(self) -> None:
        self.assertEqual("&lt;b&gt;<mark>Plan</mark>&lt;/b&gt;", self.highlight("<b>Plan</b>", ["plan"]))


def node(program: str) -> str:
    """What `program` writes to stdout under node, in UTC and US English, so a time it formats is the same everywhere."""
    env = dict(os.environ, TZ="UTC", LC_ALL="en_US.UTF-8")
    return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True, env=env).stdout


@unittest.skipUnless(shutil.which("node"), "the reviews view test needs node")
class ReviewsViewTests(unittest.TestCase):
    STUB = r"""
const base = '/';
const location = {pathname: '/reviews/', search: SEARCH, hash: '#top'};
let address = location.pathname + location.search + location.hash, page = '';
const history = {replaceState: function (state, title, url) { address = url; }};
const list = {innerHTML: ''}, boxes = [];
function frame(active, title, body) {
  page = body;
  return {
    querySelector: function () { return list; },
    querySelectorAll: function () {
      for (const m of body.matchAll(/<input type="checkbox" value="(\w+)"( checked)?>/g)) {
        boxes.push({value: m[1], checked: m[2] !== undefined, addEventListener: function (type, f) { this.changed = f; }});
      }
      return boxes;
    },
  };
}
"""

    def draw(self, reviews: list[dict], search: str = "", toggles: tuple[tuple[str, bool], ...] = ()) -> dict:
        """The Reviews index for `reviews`, drawn by the compiled showReviews at an address whose query is `search`.

        `page` is the view around the list, and `boxes` each box's value and
        whether it starts checked. `steps` holds the list and the address as
        the page opens and after each of `toggles`: a box's value, and
        whether the reader checks or unchecks it.
        """
        stub = self.STUB.replace("SEARCH", json.dumps(search)) + \
            f"const site = {{repo: 'owner/example', projects: [], reviews: {json.dumps(reviews)}}};\n"
        functions = ("stateOf", "listedStates", "reviewRow", "sameHeader", "reviewsTable", "showReviews")
        program = stub + shared_js() + "\n" + "\n".join(js_function(n) for n in functions) + (
            "\nshowReviews();"
            "\nconst opened = boxes.map(function (b) { return [b.value, b.checked]; });"
            "\nconst steps = [{list: list.innerHTML, address: address}];"
            f"\nfor (const [value, on] of {json.dumps(toggles)}) {{"
            "\n  const b = boxes.find(function (x) { return x.value === value; }); b.checked = on; b.changed();"
            "\n  steps.push({list: list.innerHTML, address: address});"
            "\n}"
            "\nprocess.stdout.write(JSON.stringify({page: page, boxes: opened, steps: steps}));")
        return json.loads(node(program))

    @staticmethod
    def review(number: int, stack: int | None = None, state: str | None = None, review: dict | None = None,
               header: dict | None = None) -> dict:
        """A row of site.json's reviews, under `header`, or else its stack's title; without a `state`, GitHub
        could not be asked."""
        row = {"number": number, "title": f"Change {number}", "heads": 1, "stack": stack,
               "header": header or {"title": f"Stack {stack or number}"}, "updated": "2023-11-14T22:21:40Z"}
        if state is not None:
            row["state"] = state
            row["review"] = review or {"status": "unreviewed"}
        return row

    def bodies(self, html: str) -> list[list[int]]:
        """The pull requests in each stack the list draws, one for each review alone too.

        Each segment of a stack is a table body that opens with its header
        row, and every body of a stack but its last is `continued`. Each row
        is its pull request's number and title, drawn as a stack's row in a
        summary's sidebar is.
        """
        return [[n for segment in stack for n in segment] for stack in self.stacks(html)]

    def stacks(self, html: str) -> list[list[list[int]]]:
        """The pull requests in each segment of each stack the list draws."""
        found: list[list[list[int]]] = [[]]
        for continued, head, rows in re.findall(
                r'<tbody( class="continued")?><tr class="stackhead"><th colspan="3" scope="rowgroup">(.*?)</th></tr>'
                r'(.*?)</tbody>', html):
            self.assertRegex(head, r'^(<a href="[^"]*">[^<]*</a>|<span>[^<]*</span>)$')
            numbers = [int(n) for n in re.findall(
                r'<tr><td class="pr"><a class="srow" href="/reviews/(\d+)/"><span class="snum">#\1</span>'
                r'<span class="stitle">Change \1</span></a></td>', rows)]
            self.assertEqual(rows.count("<tr>"), len(numbers), rows)
            found[-1].append(numbers)
            if not continued:
                found.append([])
        self.assertEqual(html.count("<tbody"), sum(len(stack) for stack in found), html)
        self.assertEqual([], found.pop(), "the last body ends its stack")
        return found

    def heads(self, html: str) -> list[str]:
        """Each table body's header, as drawn."""
        return re.findall(r'<tr class="stackhead"><th colspan="3" scope="rowgroup">(.*?)</th></tr>', html)

    def test_open_reviews_are_listed_first_and_each_box_changes_the_list_and_the_address(self) -> None:
        reviews = [self.review(30, 30, "open"), self.review(31, 30, "open"), self.review(16, None, "closed"),
                   self.review(9, 9, "merged"), self.review(10, 9, "open"), self.review(11, 9, "open"), self.review(8)]

        drawn = self.draw(reviews, toggles=(("closed", True), ("open", False), ("closed", False), ("open", True)))

        self.assertIn('<div class="reviewstates" role="group" aria-label="Pull request states">'
                      '<label><input type="checkbox" value="open" checked> Open <span class="count">5</span></label>'
                      '<label><input type="checkbox" value="closed"> Closed <span class="count">2</span></label></div>',
                      drawn["page"], "merged counts as closed, and a state GitHub could not tell as open")
        self.assertEqual([["open", True], ["closed", False]], drawn["boxes"])
        steps = [(step["address"], step["list"]) for step in drawn["steps"]]
        self.assertEqual(["/reviews/#top", "/reviews/?state=open,closed#top", "/reviews/?state=closed#top",
                          "/reviews/?state=#top", "/reviews/#top"], [address for address, _ in steps])
        self.assertEqual([[30, 31], [10, 11], [8]], self.bodies(steps[0][1]),
                         "a stack keeps its open reviews together when a merged one is left out")
        self.assertEqual([[30, 31], [16], [9, 10, 11], [8]], self.bodies(steps[1][1]))
        self.assertEqual([[16], [9]], self.bodies(steps[2][1]), "a stack's last review drawn stands alone")
        self.assertEqual('<p class="note">Check Open or Closed to list their reviews.</p>', steps[3][1])
        self.assertEqual(steps[0][1], steps[4][1])

    def test_a_stack_keeps_site_jsons_order_not_its_numbers(self) -> None:
        drawn = self.draw([self.review(40, 40, "open"), self.review(35, 40, "open"), self.review(50, 40, "open")])

        self.assertEqual([[40, 35, 50]], self.bodies(drawn["steps"][0]["list"]))

    def test_the_address_says_which_boxes_start_checked(self) -> None:
        reviews = [self.review(9, None, "open"), self.review(8, None, "merged")]
        for search, boxes, listed in (("", [True, False], [9]), ("?state=closed", [False, True], [8]),
                                      ("?state=open,closed", [True, True], [9, 8]), ("?state=", [False, False], []),
                                      ("?state=closed,bogus", [False, True], [8]), ("?other=1", [True, False], [9])):
            with self.subTest(search=search):
                drawn = self.draw(reviews, search)

                self.assertEqual([["open", boxes[0]], ["closed", boxes[1]]], drawn["boxes"])
                self.assertEqual(listed, [n for numbers in self.bodies(drawn["steps"][0]["list"]) for n in numbers])

    def test_a_state_no_review_is_in_says_so(self) -> None:
        drawn = self.draw([self.review(9, None, "merged")])

        self.assertEqual('<p class="note">No open pull request has a review here.</p>', drawn["steps"][0]["list"])

    def test_each_row_shows_its_status_and_when_it_was_updated_in_the_readers_time_zone(self) -> None:
        clean = {"status": "clean", "url": review_url(1)}
        drawn = self.draw([self.review(12, None, "open", clean), self.review(11), self.review(10, None, "merged", clean)],
                          "?state=open,closed")
        html = drawn["steps"][0]["list"]

        self.assertIn('<colgroup><col><col class="status"><col class="date"></colgroup>'
                      '<thead><tr><th>Pull request</th><th>Status</th><th class="date">Updated</th></tr></thead>', html)
        self.assertNotIn("Summary 12", html, "a row names its pull request by its title alone")
        statuses = re.findall(r'<td class="status"><span class="eyebrow">(.*?)</span></td>', html)
        self.assertEqual([f'<a class="rstatus clean" href="{review_url(1)}" target="_blank" rel="noopener">Clean</a>',
                          "", '<span class="rstatus merged">Merged</span>'], statuses,
                         "a status GitHub could not tell is left blank, and a merged pull request's verdict gives way")
        self.assertRegex(html, r'<td class="date"><time datetime="2023-11-14T22:21:40Z">Nov 14, 2023, 10:21\sPM</time></td>')
        for gone in ("Head", "Projects", "Stacked on", "summarize-pr", "Show merged"):
            self.assertNotIn(gone, drawn["page"] + html)

    def test_a_stack_draws_no_divider_between_its_rows_and_no_link_is_underlined(self) -> None:
        css = (SITE_JS.parent / "site.css").read_text()

        self.assertIn("table.tbl.reviews td { vertical-align: baseline; padding-block: 0; border-bottom: 0; }", css)
        self.assertIn("table.tbl.reviews tbody tr:last-child td { padding-bottom: 6px; border-bottom: 1px solid "
                      "var(--border); }", css, "a divider only below each stack, or each review alone")
        self.assertIn("table.tbl.reviews { table-layout: fixed; }", css, "checking a box never moves a column")
        self.assertIn("table.tbl.reviews a, table.tbl.reviews a:hover { text-decoration: none; }", css)

    ALPHA = {"project": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}
    BETA = {"project": "alpha/beta", "title": "Finish beta", "url": "/projects/alpha/beta/"}

    def test_each_stack_and_review_alone_opens_with_its_header(self) -> None:
        drawn = self.draw([self.review(10, 9, "open", header=self.ALPHA), self.review(11, 9, "open", header=self.ALPHA),
                           self.review(8, None, "open", header={"title": "Fix loop stack subagent name"})])
        html = drawn["steps"][0]["list"]

        self.assertEqual([[[10, 11]], [[8]]], self.stacks(html))
        self.assertEqual(['<a href="/projects/alpha/">Build alpha</a>', "<span>Fix loop stack subagent name</span>"],
                         self.heads(html), "a project's header links to it, and a title's is plain text")

    def test_a_header_stays_above_the_rows_a_box_leaves(self) -> None:
        drawn = self.draw([self.review(9, 9, "merged", header=self.ALPHA), self.review(10, 9, "open", header=self.ALPHA)])

        self.assertEqual([[[10]]], self.stacks(drawn["steps"][0]["list"]))
        self.assertEqual(['<a href="/projects/alpha/">Build alpha</a>'], self.heads(drawn["steps"][0]["list"]))

    def test_a_stack_of_two_segments_draws_a_body_for_each_and_one_divider(self) -> None:
        drawn = self.draw([self.review(9, 9, "open", header=self.ALPHA), self.review(10, 9, "open", header=self.ALPHA),
                           self.review(11, 9, "open", header=self.BETA)])
        html = drawn["steps"][0]["list"]

        self.assertEqual([[[9, 10], [11]]], self.stacks(html))
        self.assertEqual(['<a href="/projects/alpha/">Build alpha</a>', '<a href="/projects/alpha/beta/">Finish beta</a>'],
                         self.heads(html))
        self.assertEqual(1, html.count('<tbody class="continued">'))
        css = (SITE_JS.parent / "site.css").read_text()
        self.assertIn("table.tbl.reviews tbody.continued tr:last-child td { padding-bottom: 0; border-bottom: 0; }", css,
                      "a segment the stack continues below draws no divider")
        self.assertNotIn("tr:first-child td", css, "the header row takes the body's top padding")
        self.assertRegex(css, r"table\.tbl\.reviews tr\.stackhead th \{ padding: 6px [^;]*; border-bottom: 0;")

    def test_segments_on_either_side_of_hidden_rows_share_a_header_when_they_name_one_project(self) -> None:
        drawn = self.draw([self.review(9, 9, "open", header=self.ALPHA), self.review(10, 9, "merged", header=self.BETA),
                           self.review(11, 9, "open", header=self.ALPHA)])

        self.assertEqual([[[9, 11]]], self.stacks(drawn["steps"][0]["list"]))

    def test_the_last_segment_drawn_ends_the_stack_when_the_boxes_hide_the_rest(self) -> None:
        drawn = self.draw([self.review(9, 9, "open", header=self.ALPHA), self.review(10, 9, "merged", header=self.BETA)])
        html = drawn["steps"][0]["list"]

        self.assertEqual([[[9]]], self.stacks(html))
        self.assertNotIn("continued", html)

    def test_on_a_phone_the_hidden_time_column_takes_no_width_from_the_titles(self) -> None:
        # A header spans all three columns, so a column left out of the table
        # entirely would come back as one with no set width and take the room
        # the titles need.
        css = (SITE_JS.parent / "site.css").read_text()
        phone = re.search(r"@media \(max-width: 640px\) \{(.*?)\n\}", css, re.S)
        assert phone is not None
        self.assertIn("table.tbl.reviews th.date, table.tbl.reviews td.date { display: none; }", phone.group(1))
        self.assertIn("table.tbl.reviews col.date { width: 0; }", phone.group(1))
        self.assertNotIn("col.date { display: none", css)

    def test_a_header_s_title_is_escaped(self) -> None:
        drawn = self.draw([self.review(9, None, "open", header={"title": "<script>alert(1)</script>"})])

        self.assertEqual(["<span>&lt;script&gt;alert(1)&lt;/script&gt;</span>"], self.heads(drawn["steps"][0]["list"]))


@unittest.skipUnless(shutil.which("node"), "the shared helper test needs node")
class SharedHelperTests(unittest.TestCase):
    def test_merged_or_closed_takes_the_place_of_the_verdict(self) -> None:
        program = shared_js() + (
            "\nprocess.stdout.write(JSON.stringify([statusHtml('merged', {status: 'clean', url: 'u'}),"
            " statusHtml('closed', undefined), statusHtml('open', {status: 'changes-requested', url: 'https://x/?a&b'}),"
            " statusHtml('open', {status: 'unreviewed'}), statusHtml(undefined, undefined)]));")

        self.assertEqual(['<span class="rstatus merged">Merged</span>', '<span class="rstatus closed">Closed</span>',
                          '<a class="rstatus changes-requested" href="https://x/?a&amp;b" target="_blank" '
                          'rel="noopener">Changes requested</a>', '<span class="rstatus unreviewed">Unreviewed</span>',
                          ""], json.loads(node(program)))

    def test_every_site_page_carries_the_summary_pages_credit(self) -> None:
        stub = ("const root = {innerHTML: ''}, main = {innerHTML: ''};\n"
                "const document = {title: '', body: {classList: {remove: function () {}}},"
                " getElementById: function () { return main; }};\n"
                "let fitFrame = null;\nconst site = {repo: 'owner/example'};\n"
                "function header() { return ''; }\n")
        program = stub + shared_js() + "\n" + js_function("frame") + \
            "\nframe('docs', 'Docs', 'body'); process.stdout.write(root.innerHTML);"

        self.assertTrue(node(program).endswith(
            '<footer class="sitefoot">Generated by <a href="https://projector.bot">Projector</a></footer>'))

    def test_summary_js_does_nothing_on_a_page_without_a_summary_but_look_for_one(self) -> None:
        # Every site page loads summary.js for the helpers above, so on a page
        # with no summary it must not draw, fetch, or listen for anything.
        program = (
            "const touched = [], asked = [];\n"
            "function watch(name, value) { return new Proxy(value, {get: function (target, key) {"
            " touched.push(name + '.' + String(key)); return target[key]; }}); }\n"
            "const document = watch('document', {getElementById: function (id) { asked.push(id); return null; }});\n"
            "const window = watch('window', {}), localStorage = watch('localStorage', {});\n"
            "function fetch() { touched.push('fetch'); return new Promise(function () {}); }\n"
            + SUMMARY_JS.read_text() +
            "\nprocess.stdout.write(JSON.stringify({touched: touched, asked: asked}));")

        self.assertEqual({"touched": ["document.getElementById"], "asked": ["summary"]}, json.loads(node(program)))


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


def rendered_summary(data: dict, popovers: bool = True, then: str = "process.stdout.write(root.innerHTML);") -> str:
    """The markup the compiled renderSummary draws for `data`, run under node, in a browser with or without popovers.

    The script `then` runs after it, with the context views renderSummary returns as `views`, and writes the output.
    """
    lines = SUMMARY_JS.read_text().splitlines()
    # The renderer and the declarations above it in the page's function.
    start = lines.index("(function () {") + 1
    end = next(i for i in range(lines.index("    function renderSummary(data) {"), len(lines)) if lines[i] == "    }")
    program = ("const root = {innerHTML: ''};\n"
               "const document = {getElementById: function () { return root; }, body: root};\n"
               "const window = {};\n"
               f"function HTMLElement() {{}}\n{'HTMLElement.prototype.popover = null;' if popovers else ''}\n" +
               shared_js() + "\n" + "\n".join(lines[start:end + 1]) +
               f"\nconst views = renderSummary({json.dumps(data)});\n{then}")
    return subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True).stdout


EXPANDER_DIFF = """diff --git a/lines.txt b/lines.txt
index 1111111..2222222 100644
--- a/lines.txt
+++ b/lines.txt
@@ -10,3 +10,3 @@
 line 10
-line 11
+LINE 11
 line 12
@@ -50,3 +50,3 @@ def fifty():
 line 50
-line 51
+LINE 51
 line 52
diff --git a/plain.txt b/plain.txt
index 1111111..2222222 100644
--- a/plain.txt
+++ b/plain.txt
@@ -1 +1 @@
-old
+new
"""


@unittest.skipUnless(shutil.which("node"), "the expander test needs node")
class ExpanderTests(unittest.TestCase):
    """A file the build gave `context` draws an expander in each gap; the page reveals its lines escaped."""

    def setUp(self) -> None:
        summaries = Path(tempfile.mkdtemp()) / "summaries"
        folder = summaries / "7" / ("c" * 40)
        (folder / "head").mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps({
            "version": 1, "pr": {"repo": "owner/example", "number": 7, "title": "Seven", "head": "c" * 40},
            "groups": [{"id": "all", "title": "All", "files": [{"path": "lines.txt"}, {"path": "plain.txt"}]}]}))
        (folder / "diff.patch").write_text(EXPANDER_DIFF)
        lines = [f"LINE {n}" if n in (11, 51) else f"line {n}" for n in range(1, 101)]
        (folder / "head" / "lines.txt").write_text("".join(f"{line}\n" for line in lines))
        out = Path(tempfile.mkdtemp()) / "site"
        with redirect_stdout(io.StringIO()):
            site_build.build_site(out, summaries=summaries, base="/projector/")
        self.data = json.loads((out / "reviews" / "7" / ("c" * 40) / "data.json").read_text())
        self.fid = {f["path"]: f["id"] for f in self.data["files"]}

    def card(self, html: str, path: str) -> str:
        start = re.search(f'<article class="file[^"]*" id="{self.fid[path]}"', html).start()
        return html[start:html.index("</article>", start)]

    def test_each_gap_puts_the_controls_its_place_allows_in_the_gutter_of_the_header_below_it(self) -> None:
        html = rendered_summary(self.data)
        card = self.card(html, "lines.txt")

        self.assertEqual(2, card.count('<table class="diff">'), "no table or row of its own for a gap")
        trays = re.findall(r'<tr class="(hunk|gaptail)"((?: data-\w+="\d+")*)><td class="ln xctl" colspan="2">(.*?)</td>'
                           r'<td class="code">([^<]*)</td></tr>', card)
        self.assertEqual([("hunk", ' data-hunk="0" data-gap="0"', "@@ -10,3 +10,3 @@"),
                          ("hunk", ' data-hunk="1" data-gap="1"', "@@ -50,3 +50,3 @@ def fifty():"),
                          ("gaptail", ' data-gap="2"', "")],
                         [(kind, marks, text) for kind, marks, _, text in trays],
                         "the gap above each hunk in its header's gutter, and the gap below the last in a row of its own")
        controls = [re.findall(r'data-expand="(\w+)" aria-label="([^"]+)" title="([^"]+)"', tray) for _, _, tray, _ in trays]
        self.assertEqual([[("all", "Expand all", "Expand all")],
                          [("down", "Expand down", "Expand down"), ("up", "Expand up", "Expand up"),
                           ("all", "Expand all", "Expand all")],
                          [("down", "Expand down", "Expand down"), ("all", "Expand all", "Expand all")]], controls,
                         "no expand up below the last hunk, and only expand all for 20 lines or fewer")
        self.assertIn('<tr class="hunk"><td class="ln" colspan="2"></td><td class="code">@@ -1 +1 @@</td></tr>',
                      self.card(html, "plain.txt"), "a file without context draws its header as it always has")
        self.assertNotIn("xbtn", self.card(html, "plain.txt"))

    def test_a_header_shows_its_range_as_revealed_lines_grow_it(self) -> None:
        texts = json.loads(rendered_summary(self.data, then=(
            f"const view = views[{json.dumps(self.fid['lines.txt'])}];\n"
            "const first = view.range(1);\n"
            "process.stdout.write(JSON.stringify([view.headerText(1, first),\n"
            "  view.headerText(1, {...first, oldFirst: 30, newFirst: 30}),\n"
            "  view.headerText(0, {...view.range(0), oldLast: 32, newLast: 32}),\n"
            "  view.headerText(0, {oldFirst: 1, oldLast: 52, newFirst: 1, newLast: 52}),\n"
            "  view.headerText(0, {oldFirst: 5, oldLast: 4, newFirst: 4, newLast: 4})]));")))

        self.assertEqual(["@@ -50,3 +50,3 @@ def fifty():",
                          "@@ -30,23 +30,23 @@ def fifty():",
                          "@@ -10,23 +10,23 @@",
                          "@@ -1,52 +1,52 @@",
                          "@@ -4,0 +4 @@"], texts,
                         "lines above a hunk move its start, lines below grow its count, a side with no lines names "
                         "the line before it, and the text after the second @@ stays")

    def test_revealed_lines_are_escaped_context_rows_numbered_on_both_sides(self) -> None:
        rows = rendered_summary(self.data, then=(
            f"process.stdout.write(views[{json.dumps(self.fid['lines.txt'])}].contextRows("
            "['<script>alert(1)</script>', 'b &amp; c'], null, 1, 2, 5));"))

        self.assertEqual('<tr class="ctx"><td class="ln">5</td><td class="ln">1</td><td class="code"><span class="sign"> </span>'
                         '<span class="src">&lt;script&gt;alert(1)&lt;/script&gt;</span></td></tr>'
                         '<tr class="ctx"><td class="ln">6</td><td class="ln">2</td><td class="code"><span class="sign"> </span>'
                         '<span class="src">b &amp;amp; c</span></td></tr>', rows)
        self.assertNotIn("<script>", rows)

    def test_a_highlighted_line_is_drawn_from_the_highlighters_markup(self) -> None:
        rows = rendered_summary(self.data, then=(
            f"process.stdout.write(views[{json.dumps(self.fid['lines.txt'])}].contextRows("
            "['x'], ['<span class=\"hljs-keyword\">x</span>'], 1, 1, 1));"))

        self.assertIn('<span class="src"><span class="hljs-keyword">x</span></span>', rows)

    def test_a_file_with_context_has_a_hidden_collapse_control_and_its_diff_as_first_drawn(self) -> None:
        html = rendered_summary(self.data)
        card = self.card(html, "lines.txt")

        self.assertIn('<tr class="hunk" data-hunk="0" data-gap="0"><td class="ln xctl" colspan="2"><button class="xbtn '
                      'xcollapse" type="button" hidden aria-label="Collapse expanded lines" title="Collapse expanded lines">'
                      '<svg', card, "the control leads the first header's gutter, hidden until a gap is expanded")
        self.assertEqual(1, card.count("xcollapse"))
        self.assertNotIn("xcollapse", self.card(html, "plain.txt"))
        body = card[card.index(f'<div class="fbody" id="{self.fid["lines.txt"]}-body">'):]
        body = body[body.index(">") + 1:body.rindex("</div>")]
        self.assertEqual(body, rendered_summary(self.data, then=(
            f"process.stdout.write(views[{json.dumps(self.fid['lines.txt'])}].bodyHtml);")),
                         "collapsing puts back exactly the hunks and expanders the page first drew")

    def test_only_a_file_with_context_has_a_view(self) -> None:
        self.assertEqual([self.fid["lines.txt"]], json.loads(rendered_summary(
            self.data, then="process.stdout.write(JSON.stringify(Object.keys(views)));")))


class FileHeaderTests(SiteRepoCase):
    """The page header's menu links the pull request, and each file card's links its diff, file, and project."""

    def build(self, state: str | None = None, reported_base: object = ..., reviews: list[dict] | None = None,
              **pr: str) -> dict:
        """The data the site build writes for pull request 9, which changes HEADER_FILES.

        GitHub reports the pull request as `state` with `reviews`, or cannot be asked when it is
        None, and reports its base branch as `reported_base`, None once deleted, or not at all when `...`.
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
        reported = {"state": state, "reviews": reviews or [],
                    **({} if reported_base is ... else {"base": reported_base})}
        status = mock.patch.object(summary, "pr_status", return_value=reported) \
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
    def test_the_page_header_menu_links_the_pull_request_on_github(self) -> None:
        html = rendered_summary(self.build())
        top = html[html.index('<header class="top">'):]
        top = top[:top.index("</header>")]

        pull = "https://github.com/owner/example/pull/9"
        self.assertIn('<div class="sub"><button class="more" type="button" popovertarget="pr-menu" aria-haspopup="menu" '
                      'aria-expanded="false" aria-label="Pull request links">', top)
        self.assertIn('<div class="moremenu" id="pr-menu" popover role="menu" aria-label="Pull request links">'
                      f'<a role="menuitem" href="{pull}" target="_blank" rel="noopener">Conversation on GitHub</a>'
                      f'<a role="menuitem" href="{pull}/files" target="_blank" rel="noopener">Files on GitHub</a>'
                      f'<a role="menuitem" href="https://github.com/owner/example/compare/main...{"c" * 40}" target="_blank" '
                      'rel="noopener">Compare on GitHub</a></div>', top)

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_each_reviewed_box_stands_alone_and_is_named_for_what_it_marks(self) -> None:
        data = self.build()
        html = rendered_summary(data)

        fid = next(f["id"] for f in data["files"] if f["path"] == "src/app#1.py")
        self.assertIn(f'<label class="freviewed"><input type="checkbox" class="file-box" id="{fid}-reviewed" '
                      'aria-label="Mark src/app#1.py reviewed"></label>', html)
        self.assertIn('<label class="greviewed"><input type="checkbox" class="reviewed-box" id="all-reviewed" '
                      'aria-label="Mark section 1 reviewed"></label>', html)
        self.assertNotIn("Reviewed</label>", html, "no box shows a visible label")

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_without_popovers_the_links_sit_in_place_instead_of_a_menu(self) -> None:
        # A browser without popovers would draw every menu open, so it gets the
        # links as a row under their short names, with no button and no menu.
        data = self.build(headRef="feature/header-links")
        html = rendered_summary(data, popovers=False)

        pull = "https://github.com/owner/example/pull/9"
        fid, anchor = next((f["id"], f["anchor"]) for f in data["files"] if f["path"] == "docs/projects/alpha/readme.md")
        self.assertNotIn('class="more"', html)
        self.assertNotIn("popover", html)
        self.assertIn(f'<div class="sub"><span class="morelinks"><a href="{pull}" target="_blank" rel="noopener">Conversation</a>'
                      f'<a href="{pull}/files" target="_blank" rel="noopener">Files</a>'
                      f'<a href="https://github.com/owner/example/compare/main...{"c" * 40}" target="_blank" rel="noopener">'
                      'Compare</a></span></div>', html)
        card = html[re.search(f'<article class="file[^"]*" id="{fid}"', html).start():]
        self.assertIn(f'<span class="morelinks"><a href="{pull}/files#{anchor}" target="_blank" rel="noopener">Diff</a>'
                      '<a href="https://github.com/owner/example/blob/feature/header-links/docs/projects/alpha/readme.md" '
                      'target="_blank" rel="noopener">File</a><a href="/projects/alpha/">Project</a></span>',
                      card[:card.index("</header>")])

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_the_header_menu_links_the_diff_the_file_on_its_branch_and_the_project(self) -> None:
        data = self.build(headRef="feature/header-links")
        html = rendered_summary(data)

        def header(path: str) -> str:
            fid = next(f["id"] for f in data["files"] if f["path"] == path)
            start = re.search(f'<article class="file[^"]*" id="{fid}"', html).start()
            return html[start:html.index("</header>", start)]

        path = "docs/projects/alpha/research/survey.md"
        survey = header(path)
        fid, anchor = next((f["id"], f["anchor"]) for f in data["files"] if f["path"] == path)
        self.assertIn(f'<button class="more" type="button" popovertarget="{fid}-menu" aria-haspopup="menu" '
                      f'aria-expanded="false" aria-label="Links for {path}">', survey)
        self.assertIn(f'<div class="moremenu" id="{fid}-menu" popover role="menu" aria-label="Links for {path}">'
                      f'<a role="menuitem" href="https://github.com/owner/example/pull/9/files#{anchor}" target="_blank" '
                      'rel="noopener">View diff on GitHub</a>'
                      '<a role="menuitem" href="https://github.com/owner/example/blob/feature/header-links/docs/projects/'
                      'alpha/research/survey.md" target="_blank" rel="noopener">View file on GitHub</a>'
                      '<a role="menuitem" href="/projects/alpha/">View project</a></div>', survey,
                      "the project opens in the same tab, and GitHub in a new one")
        self.assertIn('<a role="menuitem" href="/projects/alpha/beta/">View project</a>',
                      header("docs/projects/alpha/beta/readme.md"))
        for outside in ("docs/projects/gamma/readme.md", "src/app#1.py"):
            self.assertNotIn(">View project</a>", header(outside))
        self.assertIn('href="https://github.com/owner/example/blob/feature/header-links/src/app%231.py"',
                      header("src/app#1.py"), "each path segment is encoded and the branch keeps its slash")
        self.assertNotIn(">PR</a>", html)

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_the_file_link_shows_the_file_as_it_is_now(self) -> None:
        # An open pull request's file is current on its branch, and a merged
        # one's on the branch it merged into, since the merge usually deletes
        # its own. A closed one never reached its base, so it links the head
        # commit, which stays reachable.
        branch = "https://github.com/owner/example/blob/feature/header-links/src/app%231.py"
        trunk = "https://github.com/owner/example/blob/develop/src/app%231.py"
        at_head = f'https://github.com/owner/example/blob/{"c" * 40}/src/app%231.py'
        for state, expected in (("open", branch), ("merged", trunk), ("closed", at_head), (None, branch)):
            with self.subTest(state=state), mock.patch.object(cli, "trunk_branch", return_value="develop"):
                data = self.build(state=state, headRef="feature/header-links", baseRef="develop")
                self.assertEqual(state, data["pr"].get("state"), "the page records the state the build found")
                self.assertEqual("develop", data["defaultBranch"], "the page records the default branch the build found")
                self.assertIn(f'href="{expected}"', rendered_summary(data))
                shutil.rmtree(self.out)

    @unittest.skipUnless(shutil.which("node"), "the status test needs node")
    def test_the_page_shows_merged_or_closed_in_place_of_its_verdict_and_the_sites_credit(self) -> None:
        for state, expected in (("open", '<span class="rstatus unreviewed">Unreviewed</span>'),
                                ("merged", '<span class="rstatus merged">Merged</span>'),
                                ("closed", '<span class="rstatus closed">Closed</span>')):
            with self.subTest(state=state):
                html = rendered_summary(self.build(state=state))
                self.assertIn(f" · {expected}</div><h1>", html)
                self.assertIn('<div class="credit">Generated by <a href="https://projector.bot">Projector</a></div>', html)
                shutil.rmtree(self.out)
        self.assertNotIn("rstatus", rendered_summary(self.build()), "no status when GitHub could not be asked")

    @unittest.skipUnless(shutil.which("node"), "the status test needs node")
    def test_the_header_of_an_incremental_review_links_the_full_review_it_builds_on(self) -> None:
        full = "a8cf3d5" + "0" * 33
        reviews = [projector_review("clean", full, "2026-10-01T10:00:00Z", 1),
                   incremental_review("c" * 40, full, "2026-10-02T10:00:00Z", 2)]

        open_html = rendered_summary(self.build(state="open", reviews=reviews))
        shutil.rmtree(self.out)
        merged_html = rendered_summary(self.build(state="merged", reviews=reviews))

        self.assertIn(f' · <a class="rstatus clean" href="{review_url(2)}" target="_blank" rel="noopener">Clean</a>'
                      f' · <a class="rstatus incremental" href="{review_url(1)}" target="_blank" rel="noopener">'
                      'incremental from <code>a8cf3d5</code></a></div><h1>', open_html)
        self.assertIn(' · <span class="rstatus merged">Merged</span></div><h1>', merged_html,
                      "a merged pull request shows Merged alone")

    @unittest.skipUnless(shutil.which("node"), "the status test needs node")
    def test_the_header_shows_the_newest_verdict_alone_when_several_models_reviewed_the_head(self) -> None:
        head = "c" * 40
        for verdict, word in (("clean", "Clean"), ("changes-requested", "Changes requested")):
            with self.subTest(verdict):
                other = "changes-requested" if verdict == "clean" else "clean"
                reviews = [projector_review(other, head, "2026-10-02T09:00:00Z", 1, model="gpt-5.5"),
                           projector_review(verdict, head, "2026-10-02T10:00:00Z", 2, model="claude-opus")]
                data = self.build(state="open", reviews=reviews)
                chip = f'<a class="rstatus {verdict}" href="{review_url(2)}" target="_blank" rel="noopener">{word}</a>'
                index_row = node(shared_js() + f"\nprocess.stdout.write(statusHtml('open', {json.dumps(data['review'])}));")
                html = rendered_summary(data)

                self.assertEqual({"status": verdict, "url": review_url(2)}, data["review"], "the page names no models")
                self.assertIn(f" · {chip}</div><h1>", html, "one status chip, linked to the newest review")
                self.assertEqual(1, html.count('class="rstatus '), "no second chip and no list of models")
                self.assertNotIn("<button class=\"rstatus", html)
                self.assertNotIn("*", html[html.index('class="eyebrow"'):html.index("<h1>")])
                self.assertEqual(chip, index_row, "the Reviews row shows the same status")
                shutil.rmtree(self.out)
        self.assertNotIn("rmodel", (SITE_JS.parent / "summary.css").read_text())
        self.assertNotIn("rmodel", SUMMARY_JS.read_text())

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_a_merged_pull_request_links_the_branch_it_merged_into_while_that_branch_exists(self) -> None:
        # A backport, or a stack layer merged into its parent's branch, may
        # never reach the default branch. Once the parent's branch is deleted,
        # as merging the parent usually does, the change is on the default branch.
        def link(branch: str) -> str:
            return f'href="https://github.com/owner/example/blob/{branch}/src/app%231.py"'

        for case, recorded, reported, expected in (
                ("the base branch GitHub reports", "release/1.2", "release/1.2", "release/1.2"),
                ("GitHub's base over the summary's, after a retarget", "stack/parent", "develop", "develop"),
                ("the default branch once the base branch is deleted", "stack/parent", None, "develop"),
                ("the summary's base when the lookup reports none", "release/1.2", ..., "release/1.2")):
            with self.subTest(case), mock.patch.object(cli, "trunk_branch", return_value="develop"):
                data = self.build(state="merged", reported_base=reported, headRef="feature/header-links", baseRef=recorded)
                if reported is not ...:
                    self.assertEqual(reported, data["pr"]["currentBaseRef"], "the page records the base GitHub reported")
                self.assertIn(link(expected), rendered_summary(data))
                shutil.rmtree(self.out)

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_a_merged_pull_request_without_its_base_branch_links_the_default_branch(self) -> None:
        with mock.patch.object(cli, "trunk_branch", return_value="develop"):
            data = self.build(state="merged", headRef="feature/header-links", baseRef="")

        self.assertIn('href="https://github.com/owner/example/blob/develop/src/app%231.py"', rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_a_deleted_file_links_the_merge_base_the_last_commit_that_has_it(self) -> None:
        at_base = f'https://github.com/owner/example/blob/{"b" * 40}/src/app%231.py'
        for state in ("open", "merged", "closed", None):
            with self.subTest(state=state):
                data = self.build(state=state, headRef="feature/header-links")
                next(f for f in data["files"] if f["path"] == "src/app#1.py")["deleted"] = True
                self.assertIn(f'href="{at_base}"', rendered_summary(data))
                shutil.rmtree(self.out)

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_a_merged_pull_request_with_neither_its_base_nor_the_default_branch_links_its_head(self) -> None:
        data = self.build(state="merged", headRef="feature/header-links", baseRef="")
        del data["defaultBranch"]

        self.assertIn(f'href="https://github.com/owner/example/blob/{"c" * 40}/src/app%231.py"', rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the file header test needs node")
    def test_a_summary_without_its_branch_links_the_file_at_its_head(self) -> None:
        data = self.build()
        self.assertNotIn("headRef", data["pr"])

        self.assertIn(f'href="https://github.com/owner/example/blob/{"c" * 40}/src/app%231.py"', rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_a_pull_request_alone_is_a_stack_of_one_in_the_sidebar(self) -> None:
        data = self.build()
        self.assertEqual([], data["stack"])

        self.assertIn('<ul class="stack" aria-label="Pull requests in this stack"><li class="srow current" aria-current="page">'
                      '<span class="snum">#9</span><span class="stitle">Change 9</span></li></ul>',
                      rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_the_versions_toggle_is_an_icon_named_for_how_many_there_are(self) -> None:
        data = self.build()
        data["heads"] = [{"head": "c" * 40, "url": f"/reviews/9/{'c' * 40}/", "current": True, "at": 1_700_000_500},
                         {"head": "d" * 40, "url": f"/reviews/9/{'d' * 40}/", "current": False, "at": 1_700_000_000}]
        html = rendered_summary(data)

        self.assertIn('<div class="navfoot"><div class="credit">Generated by <a href="https://projector.bot">Projector</a>'
                      '</div><details class="versions"><summary aria-label="Versions (2)" title="Versions (2)"><svg', html,
                      "the credit and the versions icon share the box's last row")
        self.assertNotIn("Versions ·", html)
        data["heads"] = data["heads"][:1]
        self.assertIn('<div class="navfoot"><div class="credit">Generated by <a href="https://projector.bot">Projector</a>'
                      '</div></div></nav></aside>', rendered_summary(data), "one version leaves the credit on its own")

    @unittest.skipUnless(shutil.which("node"), "the overview test needs node")
    def test_the_overview_card_holds_only_what_the_summary_wrote(self) -> None:
        self.assertIn('<div class="card prose"><h3>What this PR does</h3></div>', rendered_summary(self.build()),
                      "the page adds no reading guide of its own")

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_the_sidebar_heads_the_stack_with_its_project_linked_to_its_page(self) -> None:
        data = self.build()
        data["segments"][0]["header"] = {"project": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}
        html = rendered_summary(data)

        self.assertIn('<div class="prblock"><div class="stackgroup">'
                      '<div class="stackhead"><a href="/projects/alpha/">Build alpha</a></div>'
                      '<ul class="stack" aria-label="Pull requests in this stack"><li class="srow current" '
                      'aria-current="page"><span class="snum">#9</span><span class="stitle">Change 9</span></li></ul>'
                      '</div></div>', html)
        self.assertNotIn("stack projects", html)

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_the_sidebar_heads_a_stack_with_no_project_with_its_title_unlinked(self) -> None:
        data = self.build()
        self.assertEqual({"title": "Summary 9"}, data["header"], "its diff changes two projects' files")

        self.assertIn('<div class="prblock"><div class="stackgroup"><div class="stackhead"><span>Summary 9</span></div>'
                      '<ul class="stack"', rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_the_sidebar_escapes_a_header_s_title(self) -> None:
        data = self.build()
        data["segments"][0]["header"] = {"title": "<script>alert(1)</script>"}

        self.assertIn('<div class="stackhead"><span>&lt;script&gt;alert(1)&lt;/script&gt;</span></div>',
                      rendered_summary(data))

    @unittest.skipUnless(shutil.which("node"), "the sidebar test needs node")
    def test_the_sidebar_heads_each_segment_of_the_stack(self) -> None:
        data = self.build()
        alpha = {"project": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}
        beta = {"project": "alpha/beta", "title": "Finish beta", "url": "/projects/alpha/beta/"}
        data["segments"] = [{"header": alpha, "members": [7, 9]}, {"header": beta, "members": [11]}]
        data["stack"] = [{"number": 7, "title": "Change 7", "url": "/reviews/7/", "current": False},
                         {"number": 9, "title": "Change 9", "url": "/reviews/9/", "current": True},
                         {"number": 11, "title": "", "url": "https://github.com/owner/example/pull/11", "current": False}]
        html = rendered_summary(data)

        self.assertIn('<div class="prblock"><div class="stackgroup">'
                      '<div class="stackhead"><a href="/projects/alpha/">Build alpha</a></div>'
                      '<ul class="stack" aria-label="Pull requests for Build alpha">'
                      '<li><a class="srow" href="/reviews/7/"><span class="snum">#7</span><span class="stitle">Change 7'
                      '</span></a></li><li class="srow current" aria-current="page"><span class="snum">#9</span>'
                      '<span class="stitle">Change 9</span></li></ul>'
                      '<div class="stackhead"><a href="/projects/alpha/beta/">Finish beta</a></div>'
                      '<ul class="stack" aria-label="Pull requests for Finish beta">'
                      '<li><a class="srow" href="https://github.com/owner/example/pull/11"><span class="snum">#11</span>'
                      '<span class="stitle"></span></a></li></ul></div></div>', html,
                      "each segment's header, then its rows in order, the current one marked in its own segment")

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
        css = (SITE_JS.parent / "summary.css").read_text()
        wide = re.search(r"^\.side \.nav \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(wide, "summary.css has no .side .nav rule")
        self.assertRegex(wide.group(1), r"max-height: calc\(100vh\b")
        self.assertRegex(wide.group(1), r"overflow-y: auto")
        # A scroll the list cannot take passes to the page, which is what pins
        # the sidebar and brings a long list's last sections into the window.
        self.assertNotIn("overscroll-behavior", wide.group(1))
        narrow = re.search(r"@media \(max-width: 980px\) \{ \.layout > \.side \{ position: static; \} (.*) \}$", css, re.M)
        self.assertIsNotNone(narrow, "summary.css has no narrow-screen .side rule")
        self.assertIn(".side .nav { max-height: none; overflow: visible; }", narrow.group(1))

    def test_a_long_section_title_wraps_instead_of_scrolling_the_list_sideways(self) -> None:
        css = (SITE_JS.parent / "summary.css").read_text()
        title = re.search(r"^\.nav \.ntitle \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(title, "summary.css has no .nav .ntitle rule")
        self.assertIn("overflow-wrap: anywhere;", title.group(1))

    def test_the_stack_list_rules_leave_the_reviews_index_alone(self) -> None:
        # The site's own pages load summary.css as well, so a rule written for
        # the sidebar's stack list names the sidebar block the list sits in.
        # What the Reviews index draws the same way, a pull request's row, is
        # in the shared .srow, .snum, and .stitle rules instead.
        css = re.sub(r"/\*.*?\*/", "", (SITE_JS.parent / "summary.css").read_text(), flags=re.S)
        stack = [selector.strip() for rule in re.findall(r"([^{}]+)\{", css)
                 for selector in rule.split(",") if re.search(r"\.stack\b", selector)]
        self.assertTrue(stack, "summary.css has no .stack rule")
        self.assertEqual([], [selector for selector in stack if not selector.startswith(".prblock .stack")])

    def test_a_stack_row_shows_up_to_three_lines_of_its_title_current_or_not(self) -> None:
        css = (SITE_JS.parent / "summary.css").read_text()
        title = re.search(r"^\.stitle \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(title, "summary.css has no .stitle rule")
        self.assertIn("overflow-wrap: anywhere;", title.group(1))
        self.assertIn("-webkit-line-clamp: 3;", title.group(1))
        # Every title has the same weight, because bold text is wider, and a
        # current row in a different weight could wrap onto more lines than
        # the same title in another row.
        self.assertIn("font-weight: 600;", title.group(1))
        current = re.search(r"^\.prblock \.stack li\.current(?:, [^,{]+)* \{([^}]*)\}", css, re.M)
        self.assertIsNotNone(current, "summary.css has no .prblock .stack li.current rule")
        self.assertNotIn("font-weight", current.group(1))
        current_title = re.search(r"^\.prblock \.stack li\.current \.stitle \{", css, re.M)
        self.assertIsNone(current_title, "the current row's title is styled like every other title")


class TreeColumnStyleTests(unittest.TestCase):
    """A project's page holds a summary's sidebar, an aside.side, inside the site's main element, so the rules of
    the docs' tree column must not reach it."""

    @staticmethod
    def selectors() -> list[str]:
        css = re.sub(r"/\*.*?\*/", "", (SITE_JS.parent / "site.css").read_text(), flags=re.S)
        return [selector.strip() for rule in re.findall(r"([^{}]+)\{", css) for selector in rule.split(",")]

    def test_the_columns_rules_name_it_as_the_main_elements_child(self) -> None:
        side = [s for s in self.selectors() if re.search(r"\.side\b", s)]

        self.assertTrue(side, "site.css has no .side rule")
        self.assertEqual([], [s for s in side if not re.match(r"\.sitemain(\.[a-z-]+|:not\(\.[a-z-]+\))* > \.side\b", s)])

    def test_the_trees_list_and_link_rules_name_the_tree(self) -> None:
        styled = [s for s in self.selectors()
                  if re.search(r"\.side", s) and re.search(r"(^|[\s>+~])(ul|li|a)(?![\w-])", s) and ".sidetop" not in s]

        self.assertEqual([], [s for s in styled if ".sidetree" not in s])
        for selector in (".sidetree ul", ".sidetree li", ".sidetree a"):
            self.assertIn(selector, styled)


def review_url(review_id: int) -> str:
    return f"https://github.com/owner/example/pull/9#pullrequestreview-{review_id}"


def projector_review(verdict: str, head: str, at: str, review_id: int, association: str = "OWNER",
                     model: str = "claude-opus") -> dict:
    """A review of pull request 9 as `summary.pr_status` lists it, carrying a Projector review's marker for `head`.

    `association` is the author's relation to the repository, as GitHub's
    `author_association` gives it, and `model` the model its marker names.
    """
    marker = (f"<!-- projector-review v=1 verdict={verdict} projector=0.6.13 model={model} effort=high "
              f"sha={head} findings=0 seconds=60 covered=2/2 -->")
    return {"body": f"**Projector review** · `0.6.13`\n\n{marker}\n\nWhat the review found.",
            "url": review_url(review_id), "at": at, "association": association}


def incremental_review(head: str, full: str, at: str, review_id: int, model: str = "claude-opus") -> dict:
    """A clean incremental review of `head`, building on the full review of `full`, as `projector_review` gives one."""
    review = projector_review("clean", head, at, review_id, model=model)
    marker = review["body"].splitlines()[2]
    line = (f"<!-- projector-incremental v=1 from={full} kind=push insertions=2 deletions=2 files=1 "
            "conflicts=0 -->")
    return dict(review, body=f"**Projector review** · `0.6.13`\n\n{marker}\n{line}\n\nIncremental review.")


def served_summary(number: int, head: str) -> dict:
    """A summary for pull request `number` at `head` that builds against PLAN_DIFF."""
    return {"version": 1, "name": f"Summary {number}", "overview": {"summary": [], "cards": []},
            "pr": {"repo": "owner/example", "number": number, "title": f"Change {number}", "head": head,
                   "base": "b" * 40, "baseRef": "main"},
            "groups": [{"id": "all", "title": "All",
                        "files": [{"path": "docs/projects/alpha/readme.md"}, {"path": "src/app.py"}]}]}


# When `published_remote` commits each head, as the Reviews index dates it.
C_PUBLISHED = "2023-11-14T22:13:20Z"
D_PUBLISHED = "2023-11-14T22:21:40Z"


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
        """A remote of this checkout whose `ref` holds two heads of pull request 9 under `root`, each stored as `name`.

        Head c is committed at C_PUBLISHED and head d at D_PUBLISHED.
        """
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
        self.assertEqual([(D_PUBLISHED, 2)], [(w["updated"], w["heads"]) for w in manifest["reviews"]],
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
        self.assertEqual([(D_PUBLISHED, 2)], [(review["updated"], review["heads"]) for review in reviews],
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
            return [(review["updated"], review["heads"]) for review in manifest["reviews"]]

        self.assertEqual([], reviews(), "nothing is fetched yet")
        self.assertIsNone(site.refresh(), "no change, no rebuild")

        self.assertEqual("", published.fetch())
        self.assertIsNotNone(site.refresh(), "the ref that appeared after startup is a change")
        self.assertEqual([(D_PUBLISHED, 2)], reviews())

        run_git(remote, "update-ref", summary.PAGES_REF, "HEAD~1")
        self.assertEqual("", published.fetch())
        self.assertIsNotNone(site.refresh(), "a fetched ref that moved is a change")
        self.assertEqual([(C_PUBLISHED, 1)], reviews())

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
            self.assertEqual([D_PUBLISHED], [review["updated"] for review in reviews()])
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
            shared_js() + "\n" + "\n".join(js_function(n) for n in ("repoUrl", "routeFor")) + \
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
        shapes = {"before the new paths": summary.workflow_text("v0", "main").replace(
            ", .projector.toml, .github/workflows/projector-site.yml", "")}
        shapes.update((f"earlier shape {i}", text) for i, text in enumerate(earlier_workflows()))
        self.workflow().parent.mkdir(parents=True)
        for name, text in shapes.items():
            with self.subTest(name):
                self.workflow().write_text(text)

                code, out, err = self.init(FakeGitHub(pages=SITE_READY))

                self.assertEqual(0, code, err)
                self.assertIn(f"updated {summary.WORKFLOW_PATH}\n", out)
                self.assertEqual(summary.workflow_text("v0", "main"), self.workflow().read_text())

    def test_check_warns_only_while_the_workflow_is_an_earlier_shape_projector_wrote(self) -> None:
        warning = (f"warning: {summary.WORKFLOW_PATH}: the site workflow is an earlier shape and does not deploy "
                   "when a pull request closes (run 'project init' or 'project site workflow --write' to refresh it) "
                   "[site-workflow-outdated]")
        hand_edited = earlier_workflows()[-1].replace("    steps:\n", "    steps:\n      - uses: actions/setup-python@v5\n")
        cases = {f"earlier shape {i}": (text, True) for i, text in enumerate(earlier_workflows())}
        cases.update({"current shape": (summary.workflow_text("v0", "main"), False),
                      "edited by hand": (hand_edited, False), "no workflow": (None, False)})
        for name, (text, warns) in cases.items():
            with self.subTest(name):
                if text is None:
                    self.workflow().unlink(missing_ok=True)
                else:
                    self.workflow().parent.mkdir(parents=True, exist_ok=True)
                    self.workflow().write_text(text)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--root", str(self.repo), "check"])

                self.assertEqual(0, code, err.getvalue())
                self.assertEqual(warns, warning in err.getvalue().splitlines(), err.getvalue())
                self.assertEqual(warns, "site-workflow-outdated" in err.getvalue(), err.getvalue())

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


def earlier_workflows(projects_dir: str | None = None) -> list[str]:
    """Each earlier shape of the site workflow, as Projector wrote it for `projects_dir` with the newest paths."""
    paths = ", ".join(summary.watched_paths(projects_dir))
    return [template.format(event=summary.DISPATCH_EVENT, ref="v0", branch="main", paths=paths)
            for template in summary.PREVIOUS_WORKFLOWS]


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

    def test_a_pull_request_in_the_repository_deploys_when_it_closes_and_a_forks_does_not(self) -> None:
        text = summary.workflow_text("v0", "main")
        fork = "github.event.pull_request.head.repo.full_name != github.repository"

        self.assertIn("  pull_request_target:\n    types: [closed]\n", text)
        self.assertNotIn("  pull_request:\n", text, "a pull_request run checks out the pull request's merge ref")
        self.assertIn("  publish:\n    if: github.event_name != 'pull_request_target' || "
                      "github.event.pull_request.head.repo.full_name == github.repository\n", text)
        self.assertIn("concurrency:\n  group: ${{ github.event_name == 'pull_request_target' && "
                      f"{fork} && format('fork-{{0}}', github.run_id) || 'pages' }}}}\n  cancel-in-progress: false\n",
                      text)
        self.assertEqual(["contents: read", "pull-requests: read", "pages: write", "id-token: write"],
                         text.split("permissions:\n", 1)[1].split("concurrency:", 1)[0].strip().split("\n  "),
                         "a closed pull request deploys with the permissions every other run has")

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
            "no closed trigger": summary.PREVIOUS_WORKFLOWS[2].format(
                event="projector-summaries", ref="v0.5.3", branch="trunk", paths=", ".join(summary.watched_paths("plans"))),
        }
        for name, text in shapes.items():
            with self.subTest(name):
                projects_dir = "plans" if "plans" in text else None
                self.assertTrue(summary.generated_workflow(text, projects_dir))
                # The paths are not part of the shape: either list deploys when a pull request closes.
                self.assertEqual("current" if name in ("current", "before the new paths") else "earlier",
                                 summary.workflow_shape(text, projects_dir))
        for text in earlier_workflows():
            self.assertEqual("earlier", summary.workflow_shape(text))

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
