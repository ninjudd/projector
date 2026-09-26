from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from projector import cli, site, walkthrough

ROOT = Path(__file__).parents[1]

DIFF = """diff --git a/src/core.go b/src/core.go
index 1111111..2222222 100644
--- a/src/core.go
+++ b/src/core.go
@@ -10,3 +10,4 @@ func Check() {
     a := 1
-    b := 2
+    b := 3
+    c := "</script>"
     return
diff --git a/src/core_test.go b/src/core_test.go
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/src/core_test.go
@@ -0,0 +1,2 @@
+package core
+func TestCheck() {}
diff --git a/gen/api.pb.go b/gen/api.pb.go
index 4444444..5555555 100644
--- a/gen/api.pb.go
+++ b/gen/api.pb.go
@@ -1 +1 @@
-old
+new
\\ No newline at end of file
"""


def make_spec(groups: list[dict]) -> dict:
    return {
        "version": 1,
        "name": "Core Walkthrough",
        "pr": {"repo": "owner/repo", "number": 7, "title": "Change the core", "head": "a" * 40, "base": "b" * 40, "baseRef": "main"},
        "overview": {"summary": ["It changes <code>Check</code>."], "cards": []},
        "groups": groups,
    }


GOOD_GROUPS = [
    {"id": "core", "title": "Core", "checks": [{"kind": "flag", "text": "Look at <code>c</code>."}],
     "files": [{"path": "src/core.go", "note": "The change."}, {"path": "src/core_test.go"}]},
    {"id": "gen", "title": "Generated", "files": [{"path": "gen/api.pb.go"}]},
]


class ParseDiffTests(unittest.TestCase):
    def test_parses_files_counts_line_numbers_and_kinds(self) -> None:
        files = {f["path"]: f for f in walkthrough.parse_diff(DIFF)}

        core = files["src/core.go"]
        self.assertEqual((2, 1), (core["adds"], core["dels"]))
        self.assertEqual("go", core["lang"])
        self.assertEqual("", core["kind"])
        lines = core["hunks"][0]["lines"]
        self.assertEqual(["c", 10, 10, "    a := 1"], lines[0])
        self.assertEqual(["d", 11, "", "    b := 2"], lines[1])
        self.assertEqual(["a", "", 11, "    b := 3"], lines[2])
        self.assertEqual(["c", 12, 13, "    return"], lines[4])

        self.assertTrue(files["src/core_test.go"]["new"])
        self.assertEqual("test", files["src/core_test.go"]["kind"])
        self.assertEqual("generated", files["gen/api.pb.go"]["kind"])
        self.assertEqual("m", files["gen/api.pb.go"]["hunks"][0]["lines"][-1][0])

    def test_quoted_spaced_deleted_and_renamed_paths(self) -> None:
        diff = "\n".join([
            'diff --git "a/caf\\303\\251 \\"x\\".md" "b/caf\\303\\251 \\"x\\".md"',
            "index 1..2 100644",
            '--- "a/caf\\303\\251 \\"x\\".md"',
            '+++ "b/caf\\303\\251 \\"x\\".md"',
            "@@ -1 +1 @@",
            "--- a removed line that looks like a header",
            "+y",
            "diff --git a/docs/a b/c.md b/docs/a b/c.md",
            "index 1..2 100644",
            "--- a/docs/a b/c.md",
            "+++ b/docs/a b/c.md",
            "@@ -1 +1 @@",
            "-x",
            "+y",
            "diff --git a/gone.txt b/gone.txt",
            "deleted file mode 100644",
            "index 1..0",
            "--- a/gone.txt",
            "+++ /dev/null",
            "@@ -1 +0,0 @@",
            "-x",
            "diff --git a/old name.txt b/new name.txt",
            "similarity index 100%",
            "rename from old name.txt",
            "rename to new name.txt",
            "",
        ])
        files = walkthrough.parse_diff(diff)
        self.assertEqual(['café "x".md', "docs/a b/c.md", "gone.txt", "new name.txt"], [f["path"] for f in files])
        self.assertEqual((1, 1), (files[0]["adds"], files[0]["dels"]), "a removed line starting with -- stays a removed line")
        self.assertTrue(files[2]["deleted"])

    def test_anchor_matches_github_files_tab(self) -> None:
        core = walkthrough.parse_diff(DIFF)[0]
        # GitHub anchors a file on the files tab by the SHA-256 of its path.
        self.assertEqual(
            "diff-" + __import__("hashlib").sha256(b"src/core.go").hexdigest(),
            core["anchor"],
        )


class BuildTests(unittest.TestCase):
    def build(self, groups: list[dict], live: object = None) -> tuple[int, Path, str]:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "pr.diff").write_text(DIFF)
        (tmp / "spec.json").write_text(json.dumps(make_spec(groups)))
        err = io.StringIO()
        lookup = mock.patch.object(walkthrough, "pr_metadata", side_effect=live or walkthrough.SpecError("gh pr view failed: offline"))
        with lookup, redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["site", "page", "--spec", str(tmp / "spec.json"), "--out", str(tmp / "site"), "--diff", str(tmp / "pr.diff")])
        return code, tmp / "site", err.getvalue()

    def test_writes_the_page_and_the_renderer(self) -> None:
        code, site, err = self.build(GOOD_GROUPS)

        self.assertEqual(0, code, err)
        html = (site / "index.html").read_text()
        self.assertTrue(html.startswith("<title>Core Walkthrough</title>"))
        self.assertTrue((site / "walkthrough.js").is_file())
        self.assertTrue((site / "walkthrough.css").is_file())
        self.assertNotIn("sitebar", html, "a standalone page has no site to link to")
        # A literal </script> inside the diff must not end the data block.
        self.assertNotIn('"</script>"', html)
        start = html.index('type="application/json">') + len('type="application/json">')
        data = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual(3, data["stats"]["files"])
        self.assertEqual({"files": 3, "adds": 5, "dels": 2, "hand": 3, "test": 2, "generated": 2, "docs": 0}, data["stats"])
        self.assertEqual(["core", "gen"], [g["id"] for g in data["groups"]])

    def test_a_diff_file_build_still_refuses_a_moved_head(self) -> None:
        moved = lambda repo, number: dict(make_spec([])["pr"], head="c" * 40)
        code, _, err = self.build(GOOD_GROUPS, live=moved)
        self.assertEqual(65, code)
        self.assertIn("moved from aaaaaaaaa to ccccccccc", err)

    def test_a_diff_file_build_warns_when_it_cannot_check_the_head(self) -> None:
        code, site, err = self.build(GOOD_GROUPS)
        self.assertEqual(0, code, err)
        self.assertIn("could not check whether the PR moved past aaaaaaaaa", err)
        self.assertTrue((site / "index.html").is_file())

    def test_refuses_a_file_in_no_group(self) -> None:
        code, _, err = self.build([GOOD_GROUPS[0]])
        self.assertEqual(65, code)
        self.assertIn("gen/api.pb.go is in no group", err)

    def test_refuses_a_file_in_two_groups(self) -> None:
        groups = [GOOD_GROUPS[0], {"id": "gen", "title": "Generated", "files": [{"path": "gen/api.pb.go"}, {"path": "src/core.go"}]}]
        code, _, err = self.build(groups)
        self.assertEqual(65, code)
        self.assertIn("src/core.go is in both core and gen", err)

    def test_refuses_a_path_outside_the_diff_and_a_bad_check(self) -> None:
        groups = [dict(GOOD_GROUPS[0], checks=[{"kind": "maybe", "text": "x"}]),
                  {"id": "gen", "title": "Generated", "files": [{"path": "gen/api.pb.go"}, {"path": "src/gone.go"}]}]
        code, _, err = self.build(groups)
        self.assertEqual(65, code)
        self.assertIn("gen: src/gone.go is not in the diff", err)
        self.assertIn("core: each check needs kind verify or flag", err)

    def test_refuses_a_duplicate_group_id(self) -> None:
        groups = [GOOD_GROUPS[0], dict(GOOD_GROUPS[1], id="core")]
        code, _, err = self.build(groups)
        self.assertEqual(65, code)
        self.assertIn("group id 'core' is used twice", err)


class RendererTests(unittest.TestCase):
    def test_renderer_ships_only_allowlisted_remote_scripts(self) -> None:
        # Claude Artifacts load scripts only from a CDN allowlist.
        html = site.page("T", make_spec(GOOD_GROUPS) | {"pr": make_spec([])["pr"]})
        for src in __import__("re").findall(r'src="(https?://[^"]+)"', html):
            self.assertTrue(src.startswith("https://cdnjs.cloudflare.com/"), src)


class AtHeadTests(unittest.TestCase):
    def test_at_head_fetches_the_recorded_merge_base_and_head_without_checking_the_live_pr(self) -> None:
        out = Path(tempfile.mkdtemp()) / "site"
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF) as fetch, \
             mock.patch.object(walkthrough, "pr_metadata", side_effect=AssertionError("must not check the live PR")):
            site.build_page(make_spec(GOOD_GROUPS), out, at_head=True)
        fetch.assert_called_once_with("owner/repo", "b" * 40, "a" * 40)
        self.assertTrue((out / "index.html").is_file())

    def test_default_build_refuses_a_moved_head(self) -> None:
        live = dict(make_spec([])["pr"], head="c" * 40)
        with mock.patch.object(walkthrough, "pr_metadata", return_value=live), \
             mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF):
            with self.assertRaisesRegex(walkthrough.SpecError, "moved from aaaaaaaaa to ccccccccc"):
                site.build_page(make_spec(GOOD_GROUPS), Path(tempfile.mkdtemp()))


class SiteTests(unittest.TestCase):
    def write_spec(self, root: Path, head: str, name: str) -> Path:
        spec = make_spec(GOOD_GROUPS)
        spec["pr"]["head"] = head
        spec["name"] = name
        path = root / "7" / head / "spec.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(spec))
        path.with_name("diff.patch").write_text(DIFF)
        return path

    def test_builds_every_head_an_index_and_a_link_to_the_newest(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        old = self.write_spec(tmp / "walkthroughs", "a" * 40, "Old")
        new = self.write_spec(tmp / "walkthroughs", "c" * 40, "New")
        times = {old: 100, new: 200}
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF), \
             mock.patch.object(site, "spec_time", side_effect=lambda p: times[p]), \
             redirect_stdout(io.StringIO()):
            _, failures = site.build_site(tmp / "site", walkthroughs=tmp / "walkthroughs")

        self.assertEqual([], failures)
        built_site = tmp / "site"
        self.assertIn('data-src="/prs/7/cccccccccccccccccccccccccccccccccccccccc/data.json"',
                      (built_site / "prs" / "7" / "index.html").read_text(), "the newest head, rendered in place")
        index = (built_site / "index.html").read_text()
        listed = json.loads((built_site / "site.json").read_text())["walkthroughs"]
        self.assertEqual([(7, "New", "c" * 40, 2)], [(w["number"], w["name"], w["head"], w["heads"]) for w in listed])
        self.assertTrue((built_site / ".nojekyll").exists())
        page = (built_site / "prs" / "7" / ("a" * 40) / "index.html").read_text()
        self.assertNotIn("walkthrough-data", page)
        self.assertIn('<div id="sitebar" data-base="/"></div>', page, "a site page carries the site's menu")
        self.assertIn('src="/assets/site.js"', page)
        self.assertIn('<meta charset="utf-8">', page)
        data = json.loads((built_site / "prs" / "7" / ("a" * 40) / "data.json").read_text())
        self.assertEqual("/prs/", data["indexUrl"])
        self.assertEqual(["/prs/7/" + "c" * 40 + "/", "/prs/7/" + "a" * 40 + "/"], [h["url"] for h in data["heads"]])
        self.assertEqual([("c" * 40, False), ("a" * 40, True)], [(h["head"], h["current"]) for h in data["heads"]])
        for built in (page, index, (built_site / "prs" / "7" / "index.html").read_text()):
            self.assertIn(site.icon_link(), built)

    def test_a_spec_published_with_its_diff_builds_without_github(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        spec_path = self.write_spec(tmp / "walkthroughs", "a" * 40, "Stored")
        spec_path.with_name("diff.patch").write_text(DIFF)
        offline = walkthrough.SpecError("gh api failed: offline")
        with mock.patch.object(walkthrough, "fetch_diff", side_effect=offline), \
             mock.patch.object(walkthrough, "merge_base", side_effect=offline), \
             redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", walkthroughs=tmp / "walkthroughs")

        self.assertEqual(([], 1), (failures, len(entries)))
        data = json.loads((tmp / "site" / "prs" / "7" / ("a" * 40) / "data.json").read_text())
        self.assertEqual(3, data["stats"]["files"])

    def test_a_spec_without_a_stored_diff_is_skipped_rather_than_fetched(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.write_spec(tmp / "walkthroughs", "a" * 40, "Stored")
        bare = self.write_spec(tmp / "walkthroughs", "c" * 40, "Bare")
        bare.with_name("diff.patch").unlink()
        with mock.patch.object(walkthrough, "fetch_diff", side_effect=AssertionError("must not fetch")), \
             mock.patch.object(walkthrough, "merge_base", side_effect=AssertionError("must not fetch")), \
             redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", walkthroughs=tmp / "walkthroughs")

        self.assertEqual(1, entries[0]["heads"])
        self.assertEqual(1, len(failures))
        self.assertIn("has no diff.patch beside it; republish it", failures[0])

    def test_a_stored_diff_is_checked_and_sanitized_like_a_fetched_one(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        spec = make_spec(GOOD_GROUPS)
        spec["pr"]["head"] = "a" * 40
        spec["groups"][0]["intro"] = ['<script>alert(1)</script><b onclick="x()">bold</b>']
        folder = tmp / "walkthroughs" / "7" / ("a" * 40)
        folder.mkdir(parents=True)
        (folder / "spec.json").write_text(json.dumps(spec))
        (folder / "diff.patch").write_text(DIFF)
        with redirect_stdout(io.StringIO()):
            site.build_site(tmp / "site", walkthroughs=tmp / "walkthroughs")

        data = json.loads((tmp / "site" / "prs" / "7" / ("a" * 40) / "data.json").read_text())
        self.assertEqual(["<b>bold</b>"], data["groups"][0]["intro"])

    def test_skips_a_misfiled_spec_and_still_deploys_the_rest(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.write_spec(tmp / "walkthroughs", "a" * 40, "Good")
        wrong = tmp / "walkthroughs" / "8" / ("d" * 40) / "spec.json"
        wrong.parent.mkdir(parents=True)
        wrong.write_text(json.dumps(make_spec(GOOD_GROUPS)))
        out = io.StringIO()
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF), redirect_stdout(out):
            entries, failures = site.build_site(tmp / "site", walkthroughs=tmp / "walkthroughs")
        self.assertEqual([7], [e["number"] for e in entries])
        self.assertEqual(1, len(failures))
        self.assertIn("must sit at <pr.number>/<pr.head>/spec.json", failures[0])
        self.assertIn("::error", out.getvalue())
        self.assertTrue((tmp / "site" / "index.html").is_file())

    def test_one_spec_that_does_not_match_its_diff_costs_only_its_own_page(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        good = self.write_spec(tmp / "walkthroughs", "a" * 40, "Good")
        bad = self.write_spec(tmp / "walkthroughs", "c" * 40, "Bad")
        spec = json.loads(bad.read_text())
        spec["groups"] = spec["groups"][:1]
        bad.write_text(json.dumps(spec))
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF), \
             mock.patch.object(site, "spec_time", side_effect=lambda p: {good: 100, bad: 200}[p]), \
             redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", walkthroughs=tmp / "walkthroughs")
        self.assertEqual(1, len(failures))
        self.assertIn("gen/api.pb.go is in no group", failures[0])
        self.assertFalse((tmp / "site" / "prs" / "7" / ("c" * 40)).exists())
        self.assertIn(f"/prs/7/{'a' * 40}/data.json", (tmp / "site" / "prs" / "7" / "index.html").read_text(),
                      "the newest page that built")
        self.assertEqual(1, entries[0]["heads"])

    def test_skips_a_spec_for_another_repository_when_the_workflow_names_its_own(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.write_spec(tmp / "walkthroughs", "a" * 40, "Theirs")
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF), \
             mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/else"}), redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", walkthroughs=tmp / "walkthroughs")
        self.assertEqual([], entries)
        self.assertIn("it is for owner/repo, not this repository", failures[0])
        self.assertTrue((tmp / "site" / "index.html").is_file(), "the rest of the site still deploys")


class SanitizeTests(unittest.TestCase):
    def test_keeps_the_documented_markup_and_drops_everything_else(self) -> None:
        clean = walkthrough.sanitize_html
        self.assertEqual('<code>a &lt; b</code> &amp; <b>c</b>', clean('<code>a &lt; b</code> &amp; <b>c</b>'))
        self.assertEqual('<p class="note">x</p>', clean('<p class="note evil" style="color:red" onclick="x()">x</p>'))
        self.assertEqual('', clean('<img src=x onerror=alert(1)>'))
        self.assertEqual('before after', clean('before <script>alert(1)</script>after'))
        self.assertEqual('<a href="https://example.com/x?a=1&amp;b=2">x</a>', clean('<a href="https://example.com/x?a=1&amp;b=2" target="_top">x</a>'))
        self.assertEqual('<a>x</a>', clean('<a href="javascript:alert(1)">x</a>'))
        self.assertEqual('<a href="#core">x</a>', clean('<a href="#core">x</a>'))
        self.assertEqual('<div class="tblwrap"><table class="tbl"><tr><td class="r">1</td></tr></table></div>',
                         clean('<div class="tblwrap"><table class="tbl"><tr><td class="r">1</td></tr></table></div>'))
        self.assertEqual('x &lt;y', clean('x <y'))

    def test_a_built_page_carries_no_script_from_the_spec(self) -> None:
        spec = make_spec(GOOD_GROUPS)
        spec["groups"][0]["intro"] = ['Hi <img src=x onerror="alert(1)"><script>alert(2)</script>']
        spec["overview"]["cards"] = [{"title": "<b>T</b>", "html": '<a href="javascript:x">y</a>'}]
        out = Path(tempfile.mkdtemp())
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF):
            site.build_page(spec, out, at_head=True)
        page = (out / "index.html").read_text()
        for bad in ("onerror", "alert(2)", "javascript:"):
            self.assertNotIn(bad, page)
        self.assertIn("<b>T<\\/b>", page, "kept, with </ escaped inside the data block")


def run(cwd: Path, *args: str) -> str:
    return subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class PublishTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.remote = tmp / "remote.git"
        run(tmp, "git", "init", "--quiet", "--bare", str(self.remote))
        self.repo = tmp / "repo"
        run(tmp, "git", "clone", "--quiet", str(self.remote), str(self.repo))
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"), ("commit.gpgsign", "false")):
            run(self.repo, "git", "config", key, value)
        (self.repo / "README.md").write_text("main\n")
        run(self.repo, "git", "add", "README.md")
        run(self.repo, "git", "commit", "--quiet", "-m", "main")
        # The branch is not main: a developer's pre-push hook may refuse pushes to main.
        run(self.repo, "git", "push", "--quiet", "origin", "HEAD:trunk")
        self.spec = tmp / "spec.json"
        self.dispatches: list[str] = []

    def publish(self, head: str, **kwargs: object) -> object:
        spec = make_spec(GOOD_GROUPS)
        spec["pr"]["head"] = head
        self.spec.write_text(json.dumps(spec))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(walkthrough, "dispatch", side_effect=self.dispatches.append), \
                 mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF), redirect_stdout(io.StringIO()):
                return walkthrough.publish(self.spec, "origin", **kwargs)
        finally:
            os.chdir(cwd)

    def test_publish_stores_a_given_diff_without_fetching_one(self) -> None:
        diff_file = self.spec.with_name("pr.diff")
        diff_file.write_text(DIFF)
        spec = make_spec(GOOD_GROUPS)
        spec["pr"]["head"] = "a" * 40
        self.spec.write_text(json.dumps(spec))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(walkthrough, "dispatch", side_effect=self.dispatches.append), \
                 mock.patch.object(walkthrough, "fetch_diff", side_effect=AssertionError("must not fetch")), \
                 redirect_stdout(io.StringIO()):
                walkthrough.publish(self.spec, "origin", diff_path=diff_file)
        finally:
            os.chdir(cwd)
        self.assertIn(f"walkthroughs/7/{'a' * 40}/diff.patch", self.ref_files())

    def ref_files(self) -> list[str]:
        return run(self.remote, "git", "ls-tree", "-r", "--name-only", "refs/projector/walkthroughs").splitlines()

    def test_first_publish_creates_the_hidden_ref_dispatches_and_leaves_the_checkout_alone(self) -> None:
        (self.repo / "README.md").write_text("dirty\n")
        (self.repo / "untracked.txt").write_text("keep\n")
        before = run(self.repo, "git", "rev-parse", "HEAD")

        self.assertIsNotNone(self.publish("a" * 40))

        self.assertEqual([f"walkthroughs/7/{'a' * 40}/diff.patch", f"walkthroughs/7/{'a' * 40}/spec.json"],
                         self.ref_files())
        self.assertEqual(DIFF, run(self.remote, "git", "show", f"refs/projector/walkthroughs:walkthroughs/7/{'a' * 40}/diff.patch")
                         + "\n")
        self.assertEqual("", run(self.remote, "git", "log", "--format=%P", "-1", "refs/projector/walkthroughs"), "orphan")
        self.assertEqual(["trunk"], run(self.remote, "git", "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines(),
                         "no branch is created")
        self.assertEqual(["owner/repo"], self.dispatches)
        self.assertEqual(before, run(self.repo, "git", "rev-parse", "HEAD"))
        self.assertEqual("dirty\n", (self.repo / "README.md").read_text())
        self.assertEqual("M README.md\n?? untracked.txt", run(self.repo, "git", "status", "--porcelain"), "worktree and index untouched")

    def test_later_publishes_add_heads_and_an_unchanged_spec_neither_pushes_nor_dispatches(self) -> None:
        first = self.publish("a" * 40)
        second = self.publish("c" * 40)
        self.assertEqual(first, run(self.remote, "git", "log", "--format=%P", "-1", "refs/projector/walkthroughs"))
        self.assertIn(f"walkthroughs/7/{'c' * 40}/spec.json", self.ref_files())
        self.assertIsNone(self.publish("c" * 40))
        self.assertEqual(second, run(self.remote, "git", "rev-parse", "refs/projector/walkthroughs"))
        self.assertEqual(["owner/repo", "owner/repo"], self.dispatches)

    def test_refuses_to_push_a_spec_that_does_not_build(self) -> None:
        spec = make_spec(GOOD_GROUPS[:1])
        self.spec.write_text(json.dumps(spec))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF), \
                 mock.patch.object(walkthrough, "dispatch", side_effect=self.dispatches.append):
                with self.assertRaisesRegex(walkthrough.SpecError, "gen/api.pb.go is in no group"):
                    walkthrough.publish(self.spec, "origin")
        finally:
            os.chdir(cwd)
        self.assertEqual("", run(self.remote, "git", "for-each-ref", "refs/projector"), "nothing pushed")
        self.assertEqual([], self.dispatches)

    def test_no_dispatch_pushes_without_starting_the_workflow(self) -> None:
        self.publish("a" * 40, send_dispatch=False)
        self.assertEqual([], self.dispatches)
        self.assertTrue(self.ref_files())

    def test_refuses_a_spec_for_another_repository(self) -> None:
        run(self.repo, "git", "remote", "set-url", "origin", "git@github.com:someone/else.git")
        with self.assertRaisesRegex(walkthrough.SpecError, "origin is someone/else, but the spec is for owner/repo"):
            self.publish("a" * 40)


class RepoSlugTests(unittest.TestCase):
    def test_repo_slug_reads_github_remotes(self) -> None:
        for url in ("git@github.com:o/r.git", "ssh://git@github.com/o/r.git", "https://github.com/o/r.git", "https://github.com/o/r"):
            self.assertEqual("o/r", walkthrough.repo_slug(url), url)
        self.assertEqual("", walkthrough.repo_slug("https://gitlab.com/o/r.git"))


class StatusTests(unittest.TestCase):
    def status(self, answers: dict[str, str | None], *args: str) -> tuple[int, str, str]:
        def lookup(*gh_args: str) -> str | None:
            endpoint = gh_args[1]
            if endpoint not in answers:
                raise walkthrough.SpecError(f"gh api {endpoint} failed: connection refused")
            return answers[endpoint]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(walkthrough, "gh_lookup", side_effect=lookup), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["site", "status", "--repo", "o/r", *args])
        return code, out.getvalue(), err.getvalue()

    WORKFLOW = "repos/o/r/contents/.github/workflows/projector-site.yml"
    LEGACY = "repos/o/r/contents/.github/workflows/walkthroughs.yml"
    PAGES = "repos/o/r/pages"
    REPO = "repos/o/r"
    PRESENT = ".github/workflows/projector-site.yml\n"

    @staticmethod
    def pages(**fields: object) -> str:
        return json.dumps({"html_url": "https://o.example/r/", "public": True, **fields})

    def test_a_repository_with_the_workflow_and_a_site_prints_the_walkthrough_url(self) -> None:
        answers = {self.WORKFLOW: self.PRESENT, self.PAGES: self.pages(), self.REPO: "false\n"}
        self.assertEqual((0, "https://o.example/r/\n", ""), self.status(answers))
        self.assertEqual((0, "https://o.example/r/prs/66/\n", ""), self.status(answers, "--pr", "66"))

    def test_a_certified_custom_domain_is_handed_over_as_https(self) -> None:
        custom = self.pages(html_url="http://o.example/r/", https_enforced=False,
                            https_certificate={"state": "approved"})
        uncertified = self.pages(html_url="http://o.example/r/", https_enforced=False)
        self.assertEqual((0, "https://o.example/r/\n", ""),
                         self.status({self.WORKFLOW: self.PRESENT, self.PAGES: custom, self.REPO: "false\n"}))
        self.assertEqual((0, "http://o.example/r/\n", ""),
                         self.status({self.WORKFLOW: self.PRESENT, self.PAGES: uncertified, self.REPO: "false\n"}))

    def test_a_private_repository_with_a_public_site_is_not_hosting(self) -> None:
        code, out, _ = self.status({self.WORKFLOW: self.PRESENT, self.PAGES: self.pages(), self.REPO: "true\n"})
        self.assertEqual(cli.NOT_HOSTED, code)
        self.assertEqual("not hosted: o/r is private but its GitHub Pages site is public\n", out)

    def test_a_private_repository_with_a_private_site_is_hosting(self) -> None:
        answers = {self.WORKFLOW: self.PRESENT, self.PAGES: self.pages(public=False)}
        self.assertEqual((0, "https://o.example/r/\n", ""), self.status(answers))

    def test_a_pages_site_without_the_workflow_is_not_hosting(self) -> None:
        code, out, _ = self.status({self.WORKFLOW: None, self.LEGACY: None, self.PAGES: self.pages()}, "--pr", "66")
        self.assertEqual(cli.NOT_HOSTED, code)
        self.assertEqual("not hosted: o/r has no .github/workflows/projector-site.yml on its default branch\n", out)

    def test_a_repository_set_up_before_the_rename_still_counts(self) -> None:
        answers = {self.WORKFLOW: None, self.LEGACY: ".github/workflows/walkthroughs.yml\n", self.PAGES: self.pages(), self.REPO: "false\n"}
        self.assertEqual((0, "https://o.example/r/\n", ""), self.status(answers))

    def test_the_workflow_without_a_pages_site_is_not_hosting(self) -> None:
        code, out, _ = self.status({self.WORKFLOW: self.PRESENT, self.PAGES: None})
        self.assertEqual(cli.NOT_HOSTED, code)
        self.assertIn("has the Projector site workflow but no GitHub Pages site", out)

    def test_a_failed_lookup_is_an_error_rather_than_not_hosting(self) -> None:
        code, out, err = self.status({})
        self.assertEqual(65, code)
        self.assertEqual("", out)
        self.assertIn("connection refused", err)

    def test_gh_lookup_reads_only_a_404_as_absent(self) -> None:
        def failing(stderr: str):
            return mock.patch.object(walkthrough.subprocess, "run", side_effect=subprocess.CalledProcessError(
                1, ["gh"], output="", stderr=stderr))

        with failing("gh: Not Found (HTTP 404)\n"):
            self.assertIsNone(walkthrough.gh_lookup("api", "repos/o/r/pages"))
        with failing("gh: Bad credentials (HTTP 401)\n"), self.assertRaisesRegex(walkthrough.SpecError, "HTTP 401"):
            walkthrough.gh_lookup("api", "repos/o/r/pages")


if __name__ == "__main__":
    unittest.main()
