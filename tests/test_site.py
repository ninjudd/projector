from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, walkthrough
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
        for needed in ('src="/assets/site.js"', "marked", "purify.min.js", 'href="/assets/site.css"', 'rel="icon"',
                       'data-base="/"'):
            self.assertIn(needed, home)
        for asset in ("site.js", "site.css", "walkthrough.js", "walkthrough.css"):
            self.assertTrue((self.out / "assets" / asset).is_file(), asset)

    def test_every_view_has_a_real_path_with_the_same_shell(self) -> None:
        self.build()

        home = (self.out / "index.html").read_text()
        for route in ("projects/", "projects/alpha/", "projects/alpha/beta/", "prs/", "docs/guide/",
                      "docs/projects/alpha/notes/"):
            self.assertEqual(home, (self.out / route / "index.html").read_text(), route)
        self.assertEqual(home, (self.out / "404.html").read_text())
        self.assertFalse((self.out / "docs" / "projects" / "alpha" / "index.html").exists(),
                         "a plan lives under projects/, not docs/")

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

    def test_a_walkthrough_links_to_the_plans_its_diff_changes_and_back(self) -> None:
        self.publish(9, "c" * 40)

        manifest = self.build()

        self.assertEqual([["alpha"]], [w["projects"] for w in manifest["walkthroughs"]])
        walkthroughs = {p["name"]: p["walkthroughs"] for p in manifest["projects"]}
        self.assertEqual({"alpha": [9], "alpha/beta": []}, walkthroughs, "the deepest project owns the file")
        data = json.loads((self.out / "prs" / "9" / ("c" * 40) / "data.json").read_text())
        self.assertEqual([{"name": "alpha", "title": "Build alpha", "url": "/projects/alpha/"}], data["projects"])

    def test_a_spec_can_name_a_plan_its_diff_does_not_touch(self) -> None:
        self.publish(9, "c" * 40, projects=["alpha/beta", "no-such-plan"])

        manifest = self.build()

        self.assertEqual(["alpha/beta", "alpha"], manifest["walkthroughs"][0]["projects"],
                         "named plans first, unknown names dropped, then the ones the diff touches")

    def test_files_under_docs_are_served_beside_the_markdown(self) -> None:
        manifest = self.build()

        self.assertEqual(["docs/images/diagram.png"], manifest["files"])
        self.assertEqual(b"\x89PNG fake", (self.out / "content/docs/images/diagram.png").read_bytes())
        self.assertFalse((self.out / "content/docs/.hidden").exists())

    def test_the_search_index_covers_every_served_document(self) -> None:
        self.build()

        index = json.loads((self.out / "search.json").read_text())
        self.assertEqual(
            [("", "Home", "readme"), ("docs/guide/", "Use the guide", "doc"), ("projects/", "How projects work", "doc"),
             ("projects/alpha/", "Build alpha", "project"), ("docs/projects/alpha/notes/", "notes.md", "doc"),
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


def run_git(cwd: Path, *args: str, when: int | None = None) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f"{when} +0000", GIT_COMMITTER_DATE=f"{when} +0000")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class ServeTests(SiteRepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.sites: list[serve.Site] = []

    def tearDown(self) -> None:
        for site in self.sites:
            site.close()

    def site(self, walkthroughs: Path | None = None, base: str = "/") -> serve.Site:
        def build(out: Path) -> str:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return cli.build_checkout(self.repo.resolve(), out, walkthroughs, base, "owner/example")

        site = serve.Site(build, lambda: serve.fingerprint([self.repo]))
        self.sites.append(site)
        site.refresh(force=True)
        return site

    def fetch(self, site: serve.Site, base: str, path: str) -> tuple[int, str]:
        server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.handler(site, base))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}{path}"
            try:
                with urllib.request.urlopen(url) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.read().decode()
        finally:
            server.shutdown()
            server.server_close()

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

    def test_serves_walkthroughs_from_the_fetched_ref_newest_head_first(self) -> None:
        remote = Path(tempfile.mkdtemp())
        run_git(remote, "init", "--quiet")
        for when, head in ((1_700_000_000, "c" * 40), (1_700_000_500, "d" * 40)):
            folder = remote / "walkthroughs" / "9" / head
            folder.mkdir(parents=True)
            spec = {"version": 1, "name": "Walkthrough 9", "overview": {"summary": [], "cards": []},
                    "pr": {"repo": "owner/example", "number": 9, "title": "Change 9", "head": head,
                           "base": "b" * 40, "baseRef": "main"},
                    "groups": [{"id": "all", "title": "All", "files": [{"path": "docs/projects/alpha/readme.md"}, {"path": "src/app.py"}]}]}
            (folder / "spec.json").write_text(json.dumps(spec))
            (folder / "diff.patch").write_text(PLAN_DIFF)
            run_git(remote, "add", ".")
            run_git(remote, "commit", "--quiet", "-m", head[:1], when=when)
        run_git(remote, "update-ref", walkthrough.PAGES_REF, "HEAD")
        run_git(self.repo, "init", "--quiet")
        run_git(self.repo, "remote", "add", "origin", str(remote))

        self.assertEqual("", serve.fetch_walkthroughs(self.repo, "origin"))
        ref = serve.walkthroughs_ref(self.repo, "origin")
        self.assertEqual("refs/projector/remotes/origin/walkthroughs", ref)
        specs = serve.extract_walkthroughs(self.repo, ref, Path(tempfile.mkdtemp()))
        site = self.site(walkthroughs=specs)

        manifest = json.loads(self.fetch(site, "/", "/site.json")[1])
        self.assertEqual([("d" * 40, 2)], [(w["head"], w["heads"]) for w in manifest["walkthroughs"]],
                         "each spec is dated by its own commit, not the ref's newest")
        self.assertEqual(200, self.fetch(site, "/", f"/prs/9/{'c' * 40}/")[0])

    def test_a_checkout_without_the_ref_serves_no_walkthroughs(self) -> None:
        run_git(self.repo, "init", "--quiet")

        self.assertNotEqual("", serve.fetch_walkthroughs(self.repo, "origin"))
        self.assertIsNone(serve.walkthroughs_ref(self.repo, "origin"))

    @unittest.skipIf(os.name == "nt", "SIGINT is how a terminal stops the server on POSIX")
    def test_ctrl_c_stops_the_server_and_removes_its_builds(self) -> None:
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
            process.send_signal(signal.SIGINT)
            _, err = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

        self.assertEqual(0, process.returncode, err)
        self.assertEqual([], list(scratch.glob("projector-site-*")))


class FakeGitHub:
    """The slice of the GitHub API `init --site` uses, for one repository."""

    def __init__(self, private: bool = False, pages: dict | None = None, private_pages: bool = True) -> None:
        self.private, self.pages, self.private_pages = private, pages, private_pages
        self.calls: list[tuple[str, ...]] = []

    def lookup(self, *args: str) -> str | None:
        self.calls.append(args)
        endpoint = args[1]
        if endpoint == "repos/owner/example/pages":
            return None if self.pages is None else json.dumps(self.pages)
        if endpoint == "repos/owner/example":
            return "true\n" if self.private else "false\n"
        raise walkthrough.SpecError(f"unexpected lookup {args}")

    def gh(self, *args: str) -> str:
        self.calls.append(args)
        if args[:2] == ("api", "repos/owner/example/pages"):
            return json.dumps(self.pages)
        method, endpoint, _, field = args[2], args[3], args[4], args[5]
        if (method, endpoint) == ("POST", "repos/owner/example/pages"):
            self.pages = {"build_type": "workflow", "public": True,
                          "html_url": "https://owner.github.io/example/"}
        elif (method, endpoint, field) == ("PUT", "repos/owner/example/pages", "build_type=workflow"):
            self.pages["build_type"] = "workflow"
        elif (method, endpoint, field) == ("PUT", "repos/owner/example/pages", "public=false"):
            if not self.private_pages:
                raise walkthrough.SpecError("gh api failed: HTTP 422 private Pages are not available")
            self.pages["public"] = False
        else:
            raise walkthrough.SpecError(f"unexpected call {args}")
        return "{}"

    def writes(self) -> list[tuple[str, ...]]:
        return [call[2:] for call in self.calls if "-X" in call]


class InitSiteTests(SiteRepoCase):
    def setUp(self) -> None:
        super().setUp()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def init(self, github: FakeGitHub, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             mock.patch.object(walkthrough, "gh_lookup", side_effect=github.lookup), \
             mock.patch.object(walkthrough, "gh", side_effect=github.gh), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--root", str(self.repo), "init", *extra])
        return code, out.getvalue(), err.getvalue()

    def workflow(self) -> Path:
        return self.repo / walkthrough.WORKFLOW_PATH

    def test_plain_init_never_asks_github_for_anything(self) -> None:
        github = FakeGitHub()

        code, _, _ = self.init(github)

        self.assertEqual(0, code)
        self.assertEqual([], github.calls)
        self.assertFalse(self.workflow().exists())

    def test_creates_the_pages_site_and_the_workflow_then_changes_nothing_on_a_rerun(self) -> None:
        github = FakeGitHub()

        code, out, err = self.init(github, "--site")

        self.assertEqual(0, code, err)
        self.assertEqual([("POST", "repos/owner/example/pages", "-f", "build_type=workflow")], github.writes())
        self.assertIn(f"created {walkthrough.WORKFLOW_PATH}\n", out)
        self.assertIn("created GitHub Pages site https://owner.github.io/example/\n", out)
        self.assertIn("through a pull request", err)
        self.assertEqual(walkthrough.workflow_text("v0", "main"), self.workflow().read_text())

        github.calls.clear()
        code, out, _ = self.init(github, "--site", "--json")

        self.assertEqual(0, code)
        self.assertEqual([], github.writes())
        report = json.loads(out)
        self.assertIn({"path": walkthrough.WORKFLOW_PATH, "action": "unchanged"}, report["files"])
        self.assertEqual({"pages": "unchanged", "visibility": "unchanged",
                          "url": "https://owner.github.io/example/", "public": True}, report["site"])

    def test_switches_an_existing_site_to_deploy_from_github_actions(self) -> None:
        github = FakeGitHub(pages={"build_type": "legacy", "public": True, "html_url": "https://owner.github.io/example/"})

        code, out, _ = self.init(github, "--site", "--action-ref", "v0.5.5")

        self.assertEqual(0, code)
        self.assertEqual([("PUT", "repos/owner/example/pages", "-f", "build_type=workflow")], github.writes())
        self.assertIn("updated GitHub Pages site", out)
        self.assertIn("uses: ninjudd/projector/actions/site@v0.5.5", self.workflow().read_text())

    def test_makes_a_private_repositorys_site_private(self) -> None:
        github = FakeGitHub(private=True)

        code, out, _ = self.init(github, "--site")

        self.assertEqual(0, code)
        self.assertIn(("PUT", "repos/owner/example/pages", "-F", "public=false"), github.writes())
        self.assertFalse(github.pages["public"])
        self.assertIn("updated GitHub Pages visibility: private\n", out)

    def test_writes_no_workflow_when_a_private_repositorys_site_would_stay_public(self) -> None:
        github = FakeGitHub(private=True, private_pages=False)

        code, out, err = self.init(github, "--site")

        self.assertEqual(65, code)
        self.assertIn("owner/example is private but GitHub will not make its Pages site private", err)
        self.assertIn("project site serve", err)
        self.assertIn("created AGENTS.md", out, "what init wrote is still reported")
        self.assertFalse(self.workflow().exists())

    def test_needs_a_github_repository_to_set_up(self) -> None:
        github = FakeGitHub()

        with mock.patch.object(cli, "site_repo", return_value=""):
            code, _, err = self.init(github, "--site")

        self.assertEqual(65, code)
        self.assertIn("--site needs a GitHub remote named origin", err)
        self.assertEqual([], github.calls)
        self.assertFalse(self.workflow().exists())


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
