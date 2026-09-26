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
                raise summary.SpecError(f"gh api {endpoint} failed: connection refused")
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


class SiteContentTests(SiteRepoCase):
    def publish(self, number: int, head: str, projects: list[str] | None = None) -> None:
        spec = {
            "version": 1,
            "name": f"Summary {number}",
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
        self.specs = Path(tempfile.mkdtemp()) / "summaries"
        (self.repo / "docs/images").mkdir(parents=True)
        (self.repo / "docs/images/diagram.png").write_bytes(b"\x89PNG fake")
        (self.repo / "docs/.hidden").write_text("not served")

    def build(self) -> dict:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo),
                             "--summaries", str(self.specs)])
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
    """One function of the compiled site.js, from its signature to the brace that closes it."""
    lines = SITE_JS.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"    function {name}("))
    end = next(i for i in range(start, len(lines)) if lines[i] == "    }")
    return "\n".join(lines[start:end + 1])


TSC = SITE_JS.parents[1] / "node_modules" / ".bin" / "tsc"


@unittest.skipUnless(TSC.exists(), "the compiled-script check needs `npm ci` in src/projector/site")
class CompiledScriptTests(unittest.TestCase):
    def test_the_committed_javascript_is_what_the_typescript_compiles_to(self) -> None:
        assets = SITE_JS.parent
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([str(TSC), "-p", str(assets.parent / "ts"), "--outDir", out],
                           check=True, capture_output=True, text=True)
            built = sorted(p.name for p in Path(out).glob("*.js"))
            self.assertEqual(["site.js", "summary.js"], built)
            for name in built:
                self.assertEqual((Path(out) / name).read_text(), (assets / name).read_text(),
                                 f"{name} is stale: run `npm run build` and commit it")


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

    def published_remote(self, ref: str = summary.PAGES_REF, root: str = summary.PAGES_ROOT) -> Path:
        """A remote of this checkout whose `ref` holds two heads of pull request 9 under `root`."""
        remote = Path(tempfile.mkdtemp())
        run_git(remote, "init", "--quiet")
        for when, head in ((1_700_000_000, "c" * 40), (1_700_000_500, "d" * 40)):
            folder = remote / root / "9" / head
            folder.mkdir(parents=True)
            spec = {"version": 1, "name": "Summary 9", "overview": {"summary": [], "cards": []},
                    "pr": {"repo": "owner/example", "number": 9, "title": "Change 9", "head": head,
                           "base": "b" * 40, "baseRef": "main"},
                    "groups": [{"id": "all", "title": "All", "files": [{"path": "docs/projects/alpha/readme.md"}, {"path": "src/app.py"}]}]}
            (folder / "spec.json").write_text(json.dumps(spec))
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
                         "each spec is dated by its own commit, not the ref's newest")
        self.assertEqual(200, self.fetch(site, "/", f"/reviews/9/{'c' * 40}/")[0])

    def test_serves_summaries_from_the_fetched_ref_newest_head_first(self) -> None:
        self.published_remote()

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        found = serve.summaries_ref(self.repo, "origin")
        self.assertEqual(("refs/projector/remotes/origin/summaries", "summaries"), found)
        self.assert_serves_both_heads(*found)

    def test_serves_the_old_walkthroughs_ref_while_the_summaries_ref_does_not_exist(self) -> None:
        self.published_remote(summary.LEGACY_PAGES_REF, summary.LEGACY_PAGES_ROOT)

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        found = serve.summaries_ref(self.repo, "origin")
        self.assertEqual(("refs/projector/remotes/origin/walkthroughs", "walkthroughs"), found)
        self.assert_serves_both_heads(*found)

    def test_the_summaries_ref_wins_over_the_old_walkthroughs_ref(self) -> None:
        remote = self.published_remote()
        run_git(remote, "update-ref", summary.LEGACY_PAGES_REF, "HEAD~1")

        self.assertEqual("", serve.fetch_summaries(self.repo, "origin"))
        self.assertEqual(("refs/projector/remotes/origin/summaries", "summaries"), serve.summaries_ref(self.repo, "origin"))
        old = serve.run_git(self.repo, "rev-parse", "--verify", "--quiet", "refs/projector/remotes/origin/walkthroughs")
        self.assertNotEqual(0, old.returncode, "the old ref is not fetched once the new one exists")

    def test_a_checkout_without_the_ref_serves_no_summaries(self) -> None:
        run_git(self.repo, "init", "--quiet")

        self.assertNotEqual("", serve.fetch_summaries(self.repo, "origin"))
        self.assertIsNone(serve.summaries_ref(self.repo, "origin"))

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
            "docs/explorer/explorer.html": "<!doctype html><title>EP5000 &amp; wire</title>"
                                           "<script>var secret = 'zzz';</script><h1>Wire map</h1>",
            "docs/explorer/template.html.in": "<title>__TITLE__</title>",
            "docs/demo/index.html": "<title>Demo</title><p>Try it.</p>",
            "docs/projects/alpha/playground/readme.md": plan("Name playground", "completed", None),
            "docs/projects/alpha/playground/index.html": "<title>Name matcher</title>",
            "docs/projects/alpha/playground/cases.json": "[]",
        }
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        (self.repo / "docs/projects/alpha/playground/dist").mkdir()
        (self.repo / "docs/projects/alpha/playground/dist/namematch.wasm").write_bytes(b"\0asm\1\0\0\0")

    def build(self) -> dict:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/example"}), redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(["site", "build", "--out", str(self.out), "--repo-root", str(self.repo)]))
        return json.loads((self.out / "site.json").read_text())

    def test_an_html_page_is_a_doc_with_its_title_and_a_route(self) -> None:
        manifest = self.build()

        docs = {d["path"]: (d["title"], d["route"]) for d in manifest["docs"]}
        self.assertEqual(("EP5000 & wire", "docs/explorer/explorer/"), docs["docs/explorer/explorer.html"])
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
        self.assertEqual([{"path": "docs/projects/alpha/playground/index.html", "title": "Name matcher",
                           "route": "projects/alpha/playground/index/"}], playground["files"],
                         "the readme keeps the folder's route, so the index moves to index/")
        self.assertTrue((self.out / "projects/alpha/playground/index/index.html").is_file())
        for path in ("docs/projects/alpha/playground/cases.json", "docs/projects/alpha/playground/dist/namematch.wasm",
                     "docs/explorer/template.html.in"):
            self.assertIn(path, manifest["files"])
            self.assertEqual((self.repo / path).read_bytes(), (self.out / "content" / path).read_bytes(), path)

    def test_search_reads_an_html_page_s_visible_text(self) -> None:
        self.build()

        index = {e["route"]: e for e in json.loads((self.out / "search.json").read_text())}
        page = index["docs/explorer/explorer/"]
        self.assertEqual(("EP5000 & wire", "doc"), (page["title"], page["kind"]))
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
        raise summary.SpecError(f"unexpected lookup {args}")

    def gh(self, *args: str) -> str:
        self.calls.append(args)
        if args == ("api", "repos/owner/example/pages"):
            return json.dumps(self.pages)
        if args == ("api", "repos/owner/example"):
            return json.dumps({"private": self.private, "homepage": self.homepage or None,
                               "permissions": {"admin": self.admin, "push": True, "pull": True}})
        if not self.admin:
            raise summary.SpecError("gh api failed: HTTP 403 Must have admin rights to Repository.")
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
                raise summary.SpecError("gh api failed: HTTP 422 private Pages are not available")
            self.pages["public"] = False
        elif (method, endpoint) == ("PATCH", "repos/owner/example") and field.startswith("homepage="):
            self.homepage = field.removeprefix("homepage=")
        else:
            raise summary.SpecError(f"unexpected call {args}")
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
            code = cli.main(["--root", str(self.repo), "init", *extra])
        return code, out.getvalue(), err.getvalue()

    def workflow(self) -> Path:
        return self.repo / summary.WORKFLOW_PATH

    def test_sets_up_the_site_by_default_then_changes_nothing_on_a_rerun(self) -> None:
        github = FakeGitHub()

        code, out, err = self.init(github)

        self.assertEqual(0, code, err)
        self.assertEqual([("POST", "repos/owner/example/pages", "-f", "build_type=workflow"),
                          ("PATCH", "repos/owner/example", "-f", "homepage=https://owner.github.io/example/")],
                         github.writes())
        self.assertIn(f"created {summary.WORKFLOW_PATH}\n", out)
        self.assertIn("created GitHub Pages site https://owner.github.io/example/\n", out)
        self.assertIn("updated repository website https://owner.github.io/example/\n", out)
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

    def test_warns_a_non_admin_and_writes_the_workflow_only_for_a_site_already_set_up(self) -> None:
        github = FakeGitHub(admin=False, pages=SITE_READY)

        code, out, err = self.init(github)

        self.assertEqual(0, code)
        self.assertEqual([], github.writes())
        self.assertIn(f"created {summary.WORKFLOW_PATH}", out)
        self.assertIn("you are not an admin of owner/example, so its website does not link", err)

        self.workflow().unlink()
        for flags, expected in (((), 0), (("--site",), 65)):
            with self.subTest(flags=flags):
                github = FakeGitHub(admin=False)

                code, _, err = self.init(github, *flags)

                self.assertEqual(expected, code)
                self.assertEqual([], github.writes(), "nothing is attempted without admin")
                self.assertIn("you are not an admin of owner/example, so init cannot turn on its GitHub Pages site", err)
                self.assertFalse(self.workflow().exists())


class WorkflowTests(unittest.TestCase):
    def test_the_action_hands_its_prepare_input_to_the_build_after_its_checkout(self) -> None:
        action = (Path(__file__).parents[1] / "actions" / "site" / "action.yml").read_text()

        self.assertIn("  prepare:\n", action)
        self.assertIn("PREPARE: ${{ inputs.prepare }}", action)
        checkout, build = action.index("actions/checkout"), action.index("site build")
        self.assertLess(checkout, build, "the checkout would remove what the command generated")
        self.assertIn('${PREPARE:+--prepare "$PREPARE"}', action[build:action.index("upload-pages-artifact")])

    def fetch_step(self) -> str:
        """The shell script of the action's step that fetches the specs."""
        lines = (Path(__file__).parents[1] / "actions" / "site" / "action.yml").read_text().splitlines()
        start = lines.index("    - name: Fetch the summary specs")
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

                    blob = git_in("hash-object", "-w", "--stdin", text="spec\n")
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
                    self.assertEqual(f"SUMMARIES={tmp}/summary-specs/{expected}\n", written)
                    self.assertTrue((tmp / "summary-specs" / expected).is_file())

    def test_the_workflow_listens_only_for_the_summaries_event(self) -> None:
        text = summary.workflow_text("v0", "main")
        self.assertIn("    types: [projector-summaries]\n", text)
        self.assertNotIn(summary.LEGACY_DISPATCH_EVENT, text)

    def test_docs_changes_on_the_default_branch_rebuild_the_site(self) -> None:
        text = summary.workflow_text("v0", "trunk")
        self.assertIn("  push:\n    branches: [trunk]\n    paths: [README.md, 'docs/**']\n", text)
        self.assertIn("uses: ninjudd/projector/actions/site@v0", text)

    def test_a_projects_directory_outside_docs_is_watched_too(self) -> None:
        self.assertIn("paths: [README.md, 'docs/**', 'plans/**']", summary.workflow_text("v0", "main", "plans"))
        self.assertIn("paths: [README.md, 'docs/**']\n", summary.workflow_text("v0", "main", "docs/projects"))


if __name__ == "__main__":
    unittest.main()
