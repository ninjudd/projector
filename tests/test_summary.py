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

from projector import cli, site, summary

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


def make_summary(groups: list[dict]) -> dict:
    return {
        "version": 1,
        "name": "Core Summary",
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
        files = {f["path"]: f for f in summary.parse_diff(DIFF)}

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
        files = summary.parse_diff(diff)
        self.assertEqual(['café "x".md', "docs/a b/c.md", "gone.txt", "new name.txt"], [f["path"] for f in files])
        self.assertEqual((1, 1), (files[0]["adds"], files[0]["dels"]), "a removed line starting with -- stays a removed line")
        self.assertTrue(files[2]["deleted"])

    def test_anchor_matches_github_files_tab(self) -> None:
        core = summary.parse_diff(DIFF)[0]
        # GitHub anchors a file on the files tab by the SHA-256 of its path.
        self.assertEqual(
            "diff-" + __import__("hashlib").sha256(b"src/core.go").hexdigest(),
            core["anchor"],
        )


class BuildTests(unittest.TestCase):
    def build(self, groups: list[dict], live: object = None) -> tuple[int, Path, str]:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "pr.diff").write_text(DIFF)
        (tmp / "summary.json").write_text(json.dumps(make_summary(groups)))
        err = io.StringIO()
        lookup = mock.patch.object(summary, "pr_metadata", side_effect=live or summary.SummaryError("gh pr view failed: offline"))
        with lookup, redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["site", "page", "--summary", str(tmp / "summary.json"), "--out", str(tmp / "site"), "--diff", str(tmp / "pr.diff")])
        return code, tmp / "site", err.getvalue()

    def test_writes_the_page_and_the_renderer(self) -> None:
        code, site, err = self.build(GOOD_GROUPS)

        self.assertEqual(0, code, err)
        html = (site / "index.html").read_text()
        self.assertTrue(html.startswith("<title>Change the core</title>"))
        self.assertTrue((site / "summary.js").is_file())
        self.assertTrue((site / "summary.css").is_file())
        self.assertNotIn("sitebar", html, "a standalone page has no site to link to")
        # A literal </script> inside the diff must not end the data block.
        self.assertNotIn('"</script>"', html)
        start = html.index('type="application/json">') + len('type="application/json">')
        data = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual(3, data["stats"]["files"])
        self.assertEqual({"files": 3, "adds": 5, "dels": 2, "hand": 3, "test": 2, "generated": 2, "docs": 0}, data["stats"])
        self.assertEqual(["core", "gen"], [g["id"] for g in data["groups"]])

    def test_a_diff_file_build_still_refuses_a_moved_head(self) -> None:
        moved = lambda repo, number, stack=True: dict(make_summary([])["pr"], head="c" * 40)
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
        self.assertIn("core: each check needs a kind (context, verify, flag) and a text", err)

    def test_carries_file_checks_and_their_lines_into_the_page(self) -> None:
        groups = [dict(GOOD_GROUPS[0], concepts=["Hold <code>b</code>."],
                       files=[{"path": "src/core.go", "checks": [
                           {"kind": "flag", "text": "Is <code>c</code> escaped?", "line": 12},
                           {"kind": "verify", "text": "The old value.", "line": 11, "side": "old"},
                           {"kind": "context", "text": "Whole file."}]},
                           {"path": "src/core_test.go"}]),
                  GOOD_GROUPS[1]]
        code, site, err = self.build(groups)

        self.assertEqual(0, code, err)
        html = (site / "index.html").read_text()
        start = html.index('type="application/json">') + len('type="application/json">')
        data = json.loads(html[start:html.index("</script>", start)])
        core = data["groups"][0]
        self.assertNotIn("concepts", core, "the old concepts list reaches the page as context checks")
        self.assertEqual([{"kind": "context", "text": "Hold <code>b</code>."},
                          {"kind": "flag", "text": "Look at <code>c</code>."}], core["checks"])
        self.assertEqual([{"kind": "flag", "text": "Is <code>c</code> escaped?", "line": 12, "side": "new"},
                          {"kind": "verify", "text": "The old value.", "line": 11, "side": "old"},
                          {"kind": "context", "text": "Whole file."}], core["files"][0]["checks"])
        self.assertNotIn("checks", core["files"][1])

    def test_refuses_a_check_on_a_line_its_file_does_not_show(self) -> None:
        groups = [dict(GOOD_GROUPS[0], checks=[{"kind": "verify", "text": "x", "line": 10}],
                       files=[{"path": "src/core.go", "checks": [
                           {"kind": "flag", "text": "gone", "line": 99},
                           {"kind": "flag", "text": "added, not old", "line": 13, "side": "old"},
                           {"kind": "maybe", "text": "x"}]},
                           {"path": "src/core_test.go"}]),
                  GOOD_GROUPS[1]]
        code, _, err = self.build(groups)
        self.assertEqual(65, code)
        self.assertIn("src/core.go: line 99 (new side) is not in its diff", err)
        self.assertIn("src/core.go: line 13 (old side) is not in its diff", err)
        self.assertIn("src/core.go: each check needs a kind (context, verify, flag) and a text", err)
        self.assertIn("core: a check with a line belongs on its file", err)

    def test_refuses_a_duplicate_group_id(self) -> None:
        groups = [GOOD_GROUPS[0], dict(GOOD_GROUPS[1], id="core")]
        code, _, err = self.build(groups)
        self.assertEqual(65, code)
        self.assertIn("group id 'core' is used twice", err)


class RendererTests(unittest.TestCase):
    def test_renderer_ships_only_allowlisted_remote_scripts(self) -> None:
        # Claude Artifacts load scripts only from a CDN allowlist.
        html = site.page("T", make_summary(GOOD_GROUPS) | {"pr": make_summary([])["pr"]})
        for src in __import__("re").findall(r'src="(https?://[^"]+)"', html):
            self.assertTrue(src.startswith("https://cdnjs.cloudflare.com/"), src)


class AtHeadTests(unittest.TestCase):
    def test_at_head_fetches_the_recorded_merge_base_and_head_without_checking_the_live_pr(self) -> None:
        out = Path(tempfile.mkdtemp()) / "site"
        with mock.patch.object(summary, "fetch_diff", return_value=DIFF) as fetch, \
             mock.patch.object(summary, "pr_metadata", side_effect=AssertionError("must not check the live PR")):
            site.build_page(make_summary(GOOD_GROUPS), out, at_head=True)
        fetch.assert_called_once_with("owner/repo", "b" * 40, "a" * 40)
        self.assertTrue((out / "index.html").is_file())

    def test_default_build_refuses_a_moved_head(self) -> None:
        live = dict(make_summary([])["pr"], head="c" * 40)
        with mock.patch.object(summary, "pr_metadata", return_value=live), \
             mock.patch.object(summary, "fetch_diff", return_value=DIFF):
            with self.assertRaisesRegex(summary.SummaryError, "moved from aaaaaaaaa to ccccccccc"):
                site.build_page(make_summary(GOOD_GROUPS), Path(tempfile.mkdtemp()))


class SiteTests(unittest.TestCase):
    def write_summary(self, root: Path, head: str, name: str) -> Path:
        data = make_summary(GOOD_GROUPS)
        data["pr"]["head"] = head
        data["name"] = name
        path = root / "7" / head / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        path.with_name("diff.patch").write_text(DIFF)
        return path

    def test_builds_every_head_an_index_and_a_link_to_the_newest(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        old = self.write_summary(tmp / "summaries", "a" * 40, "Old")
        new = self.write_summary(tmp / "summaries", "c" * 40, "New")
        times = {old: 100, new: 200}
        with mock.patch.object(summary, "fetch_diff", return_value=DIFF), \
             mock.patch.object(site, "summary_time", side_effect=lambda p: times[p]), \
             redirect_stdout(io.StringIO()):
            _, failures = site.build_site(tmp / "site", summaries=tmp / "summaries")

        self.assertEqual([], failures)
        built_site = tmp / "site"
        self.assertIn('data-src="/reviews/7/cccccccccccccccccccccccccccccccccccccccc/data.json"',
                      (built_site / "reviews" / "7" / "index.html").read_text(), "the newest head, rendered in place")
        index = (built_site / "index.html").read_text()
        listed = json.loads((built_site / "site.json").read_text())["reviews"]
        self.assertEqual([(7, "New", "c" * 40, 2)], [(w["number"], w["name"], w["head"], w["heads"]) for w in listed])
        self.assertTrue((built_site / ".nojekyll").exists())
        page = (built_site / "reviews" / "7" / ("a" * 40) / "index.html").read_text()
        self.assertNotIn("summary-data", page)
        self.assertIn('<div id="sitebar" data-base="/"></div>', page, "a site page carries the site's menu")
        self.assertIn('src="/assets/site.js"', page)
        self.assertIn('<meta charset="utf-8">', page)
        data = json.loads((built_site / "reviews" / "7" / ("a" * 40) / "data.json").read_text())
        self.assertEqual(["/reviews/7/" + "c" * 40 + "/", "/reviews/7/" + "a" * 40 + "/"], [h["url"] for h in data["heads"]])
        self.assertEqual([("c" * 40, False), ("a" * 40, True)], [(h["head"], h["current"]) for h in data["heads"]])
        for built in (page, index, (built_site / "reviews" / "7" / "index.html").read_text()):
            self.assertIn(site.icon_link(), built)

    def test_a_summary_published_with_its_diff_builds_without_github(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        summary_path = self.write_summary(tmp / "summaries", "a" * 40, "Stored")
        summary_path.with_name("diff.patch").write_text(DIFF)
        offline = summary.SummaryError("gh api failed: offline")
        with mock.patch.object(summary, "fetch_diff", side_effect=offline), \
             mock.patch.object(summary, "merge_base", side_effect=offline), \
             redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", summaries=tmp / "summaries")

        self.assertEqual(([], 1), (failures, len(entries)))
        data = json.loads((tmp / "site" / "reviews" / "7" / ("a" * 40) / "data.json").read_text())
        self.assertEqual(3, data["stats"]["files"])

    def test_a_summary_without_a_stored_diff_is_skipped_rather_than_fetched(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.write_summary(tmp / "summaries", "a" * 40, "Stored")
        bare = self.write_summary(tmp / "summaries", "c" * 40, "Bare")
        bare.with_name("diff.patch").unlink()
        with mock.patch.object(summary, "fetch_diff", side_effect=AssertionError("must not fetch")), \
             mock.patch.object(summary, "merge_base", side_effect=AssertionError("must not fetch")), \
             redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", summaries=tmp / "summaries")

        self.assertEqual(1, entries[0]["heads"])
        self.assertEqual(1, len(failures))
        self.assertIn("has no diff.patch beside it; republish it", failures[0])

    def test_a_summary_stored_as_spec_json_is_reported_until_it_is_republished(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        path = self.write_summary(tmp / "summaries", "a" * 40, "Old")
        old = path.rename(path.with_name("spec.json"))
        out = io.StringIO()
        with redirect_stdout(out):
            entries, failures = site.build_site(tmp / "site", summaries=tmp / "summaries")

        self.assertEqual([], entries)
        self.assertEqual(1, len(failures))
        self.assertTrue(failures[0].startswith(f"7/{'a' * 40}/spec.json: "), failures[0])
        self.assertIn("run `project upgrade`, then republish it with `project summary publish`", failures[0])
        self.assertIn(f"::error title=Summary skipped::7/{'a' * 40}/spec.json: ", out.getvalue())

        path.write_text(old.read_text())
        with redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "republished", summaries=tmp / "summaries")

        self.assertEqual(([], 1), (failures, len(entries)), "a republished summary sits beside the old file")

    def test_a_stored_diff_is_checked_and_sanitized_like_a_fetched_one(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        data = make_summary(GOOD_GROUPS)
        data["pr"]["head"] = "a" * 40
        data["groups"][0]["intro"] = ['<script>alert(1)</script><b onclick="x()">bold</b>']
        folder = tmp / "summaries" / "7" / ("a" * 40)
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(json.dumps(data))
        (folder / "diff.patch").write_text(DIFF)
        with redirect_stdout(io.StringIO()):
            site.build_site(tmp / "site", summaries=tmp / "summaries")

        data = json.loads((tmp / "site" / "reviews" / "7" / ("a" * 40) / "data.json").read_text())
        self.assertEqual(["<b>bold</b>"], data["groups"][0]["intro"])

    def test_skips_a_misfiled_summary_and_still_deploys_the_rest(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.write_summary(tmp / "summaries", "a" * 40, "Good")
        wrong = tmp / "summaries" / "8" / ("d" * 40) / "summary.json"
        wrong.parent.mkdir(parents=True)
        wrong.write_text(json.dumps(make_summary(GOOD_GROUPS)))
        out = io.StringIO()
        with mock.patch.object(summary, "fetch_diff", return_value=DIFF), redirect_stdout(out):
            entries, failures = site.build_site(tmp / "site", summaries=tmp / "summaries")
        self.assertEqual([7], [e["number"] for e in entries])
        self.assertEqual(1, len(failures))
        self.assertIn("must sit at <pr.number>/<pr.head>/summary.json", failures[0])
        self.assertIn("::error", out.getvalue())
        self.assertTrue((tmp / "site" / "index.html").is_file())

    def test_one_summary_that_does_not_match_its_diff_costs_only_its_own_page(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        good = self.write_summary(tmp / "summaries", "a" * 40, "Good")
        bad = self.write_summary(tmp / "summaries", "c" * 40, "Bad")
        data = json.loads(bad.read_text())
        data["groups"] = data["groups"][:1]
        bad.write_text(json.dumps(data))
        with mock.patch.object(summary, "fetch_diff", return_value=DIFF), \
             mock.patch.object(site, "summary_time", side_effect=lambda p: {good: 100, bad: 200}[p]), \
             redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", summaries=tmp / "summaries")
        self.assertEqual(1, len(failures))
        self.assertIn("gen/api.pb.go is in no group", failures[0])
        self.assertFalse((tmp / "site" / "reviews" / "7" / ("c" * 40)).exists())
        self.assertIn(f"/reviews/7/{'a' * 40}/data.json", (tmp / "site" / "reviews" / "7" / "index.html").read_text(),
                      "the newest page that built")
        self.assertEqual(1, entries[0]["heads"])

    def test_skips_a_summary_for_another_repository_when_the_workflow_names_its_own(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.write_summary(tmp / "summaries", "a" * 40, "Theirs")
        with mock.patch.object(summary, "fetch_diff", return_value=DIFF), \
             mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/else"}), redirect_stdout(io.StringIO()):
            entries, failures = site.build_site(tmp / "site", summaries=tmp / "summaries")
        self.assertEqual([], entries)
        self.assertIn("it is for owner/repo, not this repository", failures[0])
        self.assertTrue((tmp / "site" / "index.html").is_file(), "the rest of the site still deploys")


class SanitizeTests(unittest.TestCase):
    def test_keeps_the_documented_markup_and_drops_everything_else(self) -> None:
        clean = summary.sanitize_html
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

    def test_a_built_page_carries_no_script_from_the_summary(self) -> None:
        data = make_summary(GOOD_GROUPS)
        data["groups"][0]["intro"] = ['Hi <img src=x onerror="alert(1)"><script>alert(2)</script>']
        data["groups"][0]["files"][0]["checks"] = [{"kind": "flag", "text": '<a href="javascript:x">z</a><script>alert(3)</script>', "line": 12}]
        data["overview"]["cards"] = [{"title": "<b>T</b>", "html": '<a href="javascript:x">y</a>'}]
        out = Path(tempfile.mkdtemp())
        with mock.patch.object(summary, "fetch_diff", return_value=DIFF):
            site.build_page(data, out, at_head=True)
        page = (out / "index.html").read_text()
        for bad in ("onerror", "alert(2)", "alert(3)", "javascript:"):
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
        self.summary_path = tmp / "summary.json"
        self.dispatches: list[str] = []

    def publish(self, head: str, **kwargs: object) -> object:
        data = make_summary(GOOD_GROUPS)
        data["pr"]["head"] = head
        self.summary_path.write_text(json.dumps(data))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(summary, "dispatch", side_effect=self.dispatches.append), \
                 mock.patch.object(summary, "fetch_diff", return_value=DIFF), redirect_stdout(io.StringIO()):
                return summary.publish(self.summary_path, "origin", **kwargs)
        finally:
            os.chdir(cwd)

    def test_publish_stores_a_given_diff_without_fetching_one(self) -> None:
        diff_file = self.summary_path.with_name("pr.diff")
        diff_file.write_text(DIFF)
        data = make_summary(GOOD_GROUPS)
        data["pr"]["head"] = "a" * 40
        self.summary_path.write_text(json.dumps(data))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(summary, "dispatch", side_effect=self.dispatches.append), \
                 mock.patch.object(summary, "fetch_diff", side_effect=AssertionError("must not fetch")), \
                 redirect_stdout(io.StringIO()):
                summary.publish(self.summary_path, "origin", diff_path=diff_file)
        finally:
            os.chdir(cwd)
        self.assertIn(f"summaries/7/{'a' * 40}/diff.patch", self.ref_files())

    def ref_files(self) -> list[str]:
        return run(self.remote, "git", "ls-tree", "-r", "--name-only", "refs/projector/summaries").splitlines()

    def test_first_publish_creates_the_hidden_ref_dispatches_and_leaves_the_checkout_alone(self) -> None:
        (self.repo / "README.md").write_text("dirty\n")
        (self.repo / "untracked.txt").write_text("keep\n")
        before = run(self.repo, "git", "rev-parse", "HEAD")

        self.assertIsNotNone(self.publish("a" * 40))

        self.assertEqual([f"summaries/7/{'a' * 40}/diff.patch", f"summaries/7/{'a' * 40}/summary.json"],
                         self.ref_files())
        self.assertEqual(DIFF, run(self.remote, "git", "show", f"refs/projector/summaries:summaries/7/{'a' * 40}/diff.patch")
                         + "\n")
        self.assertEqual("", run(self.remote, "git", "log", "--format=%P", "-1", "refs/projector/summaries"), "orphan")
        self.assertEqual(["trunk"], run(self.remote, "git", "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines(),
                         "no branch is created")
        self.assertEqual(["refs/projector/summaries"],
                         run(self.remote, "git", "for-each-ref", "--format=%(refname)", "refs/projector").splitlines(),
                         "with no old walkthroughs ref, nothing is seeded or created beside the new ref")
        self.assertEqual(["owner/repo"], self.dispatches)
        self.assertEqual(before, run(self.repo, "git", "rev-parse", "HEAD"))
        self.assertEqual("dirty\n", (self.repo / "README.md").read_text())
        self.assertEqual("M README.md\n?? untracked.txt", run(self.repo, "git", "status", "--porcelain"), "worktree and index untouched")

    def test_later_publishes_add_heads_and_an_unchanged_summary_neither_pushes_nor_dispatches(self) -> None:
        first = self.publish("a" * 40)
        second = self.publish("c" * 40)
        self.assertEqual(first, run(self.remote, "git", "log", "--format=%P", "-1", "refs/projector/summaries"))
        self.assertIn(f"summaries/7/{'c' * 40}/summary.json", self.ref_files())
        self.assertIsNone(self.publish("c" * 40))
        self.assertEqual(second, run(self.remote, "git", "rev-parse", "refs/projector/summaries"))
        self.assertEqual(["owner/repo", "owner/repo"], self.dispatches)

    def publish_legacy(self, head: str, when: str | None = None) -> str:
        """Publish `head` the way a release from before the rename did, to the old ref and root as spec.json, dated `when`."""
        data = make_summary(GOOD_GROUPS)
        data["pr"]["head"] = head
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tempfile.mkdtemp()) / "index"), GIT_AUTHOR_NAME="Test",
                   GIT_AUTHOR_EMAIL="test@example.com", GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
        if when:
            env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)

        def git(*args: str, text: str | None = None) -> str:
            return subprocess.run(["git", *args], cwd=self.remote, env=env, input=text, check=True, capture_output=True,
                                  text=True).stdout.strip()

        parent = subprocess.run(["git", "rev-parse", "--verify", "--quiet", summary.LEGACY_PAGES_REF], cwd=self.remote,
                                capture_output=True, text=True).stdout.strip()
        if parent:
            git("read-tree", parent)
        for name, content in (("spec.json", json.dumps(data)), ("diff.patch", DIFF)):
            blob = git("hash-object", "-w", "--stdin", text=content)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{summary.LEGACY_PAGES_ROOT}/7/{head}/{name}")
        old = git("commit-tree", git("write-tree"), *(["-p", parent] if parent else []), "-m", f"Publish {head[:9]}")
        git("update-ref", summary.LEGACY_PAGES_REF, old)
        return old

    def test_first_publish_seeds_the_summaries_ref_from_the_old_walkthroughs_ref(self) -> None:
        old = self.publish_legacy("a" * 40)

        new = self.publish("c" * 40)

        self.assertEqual([f"summaries/7/{'a' * 40}/diff.patch", f"summaries/7/{'a' * 40}/spec.json",
                          f"summaries/7/{'c' * 40}/diff.patch", f"summaries/7/{'c' * 40}/summary.json"], self.ref_files())
        seed = run(self.remote, "git", "log", "--format=%P", "-1", new)
        self.assertEqual(run(self.remote, "git", "show", "-s", "--format=%an %ae %aI %cI %B", old),
                         run(self.remote, "git", "show", "-s", "--format=%an %ae %aI %cI %B", seed),
                         "the old commit is replayed with its author, dates, and message")
        self.assertEqual(run(self.remote, "git", "rev-parse", f"{old}:walkthroughs"),
                         run(self.remote, "git", "rev-parse", f"{seed}:summaries"), "the old summaries move unchanged")
        self.assertEqual(old, run(self.remote, "git", "rev-parse", summary.LEGACY_PAGES_REF), "the old ref is left in place")
        self.assertEqual(["owner/repo"], self.dispatches)

        self.publish("d" * 40)
        self.assertEqual(new, run(self.remote, "git", "log", "--format=%P", "-1", "refs/projector/summaries"),
                         "later publishes build on the new ref without seeding again")

    def test_republishing_an_old_head_stores_summary_json_beside_its_spec_json(self) -> None:
        old = self.publish_legacy("a" * 40)

        new = self.publish("a" * 40)

        seed = run(self.remote, "git", "log", "--format=%P", "-1", new)
        self.assertEqual(run(self.remote, "git", "rev-parse", f"{old}:walkthroughs"),
                         run(self.remote, "git", "rev-parse", f"{seed}:summaries"))
        self.assertEqual([f"summaries/7/{'a' * 40}/diff.patch", f"summaries/7/{'a' * 40}/spec.json",
                          f"summaries/7/{'a' * 40}/summary.json"], self.ref_files())
        self.assertIsNone(self.publish("a" * 40), "once republished, an unchanged summary publishes nothing")

    def test_seeding_keeps_each_summary_dated_when_it_was_published(self) -> None:
        # Two heads of one pull request, published a month apart under the old
        # names. The site dates a summary by the last commit that touched its
        # path, so a seed that moved both in one commit would tie them.
        self.publish_legacy("a" * 40, when="2026-01-01T12:00:00+02:00")
        self.publish_legacy("b" * 40, when="2026-02-01T12:00:00+02:00")

        self.publish("c" * 40)

        def dated(head: str) -> str:
            return run(self.remote, "git", "log", "-1", "--format=%cI", "refs/projector/summaries", "--",
                       f"summaries/7/{head}/spec.json")
        self.assertEqual("2026-01-01T12:00:00+02:00", dated("a" * 40))
        self.assertEqual("2026-02-01T12:00:00+02:00", dated("b" * 40), "the newer head stays newer")

    def test_dispatch_sends_the_old_event_too_for_workflows_generated_before_the_rename(self) -> None:
        calls: list[tuple[str, ...]] = []
        with mock.patch.object(summary, "gh", side_effect=lambda *args: calls.append(args) or ""):
            summary.dispatch("owner/repo")
        self.assertEqual([("api", "repos/owner/repo/dispatches", "-f", "event_type=projector-summaries"),
                          ("api", "repos/owner/repo/dispatches", "-f", "event_type=projector-walkthroughs")], calls)

    def test_refuses_to_push_a_summary_that_does_not_build(self) -> None:
        data = make_summary(GOOD_GROUPS[:1])
        self.summary_path.write_text(json.dumps(data))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(summary, "fetch_diff", return_value=DIFF), \
                 mock.patch.object(summary, "dispatch", side_effect=self.dispatches.append):
                with self.assertRaisesRegex(summary.SummaryError, "gen/api.pb.go is in no group"):
                    summary.publish(self.summary_path, "origin")
        finally:
            os.chdir(cwd)
        self.assertEqual("", run(self.remote, "git", "for-each-ref", "refs/projector"), "nothing pushed")
        self.assertEqual([], self.dispatches)

    def test_a_local_publish_commits_to_the_checkouts_ref_and_pushes_nothing(self) -> None:
        before = run(self.repo, "git", "rev-parse", "HEAD")

        first = self.publish("a" * 40, push=False, reason="owner/repo has no GitHub Pages site")
        second = self.publish("c" * 40, push=False)

        local = run(self.repo, "git", "ls-tree", "-r", "--name-only", "refs/projector/summaries").splitlines()
        self.assertIn(f"summaries/7/{'a' * 40}/summary.json", local)
        self.assertIn(f"summaries/7/{'c' * 40}/diff.patch", local)
        self.assertEqual(first, run(self.repo, "git", "log", "--format=%P", "-1", "refs/projector/summaries"))
        self.assertEqual(second, run(self.repo, "git", "rev-parse", "refs/projector/summaries"))
        self.assertIsNone(self.publish("c" * 40, push=False), "an unchanged summary commits nothing")
        self.assertEqual("", run(self.remote, "git", "for-each-ref", "refs/projector"), "nothing reaches the remote")
        self.assertEqual("", run(self.repo, "git", "for-each-ref", "refs/projector/remotes"), "nor its copy here")
        self.assertEqual([], self.dispatches)
        self.assertEqual(before, run(self.repo, "git", "rev-parse", "HEAD"))

    def test_no_dispatch_pushes_without_starting_the_workflow(self) -> None:
        self.publish("a" * 40, send_dispatch=False)
        self.assertEqual([], self.dispatches)
        self.assertTrue(self.ref_files())

    def test_refuses_a_summary_for_another_repository(self) -> None:
        run(self.repo, "git", "remote", "set-url", "origin", "git@github.com:someone/else.git")
        with self.assertRaisesRegex(summary.SummaryError, "origin is someone/else, but the summary is for owner/repo"):
            self.publish("a" * 40)


class RepoSlugTests(unittest.TestCase):
    def test_repo_slug_reads_github_remotes(self) -> None:
        for url in ("git@github.com:o/r.git", "ssh://git@github.com/o/r.git", "https://github.com/o/r.git", "https://github.com/o/r"):
            self.assertEqual("o/r", summary.repo_slug(url), url)
        self.assertEqual("", summary.repo_slug("https://gitlab.com/o/r.git"))


class PrMetadataTests(unittest.TestCase):
    def metadata(self, base_ref: str, open_pulls: dict[str, str], merged_pulls: dict[str, str] | None = None) -> dict:
        view = json.dumps({"title": "T", "headRefOid": "d" * 40, "headRefName": "feature-b", "baseRefName": base_ref})
        self.asked: list[str] = []

        def run(*args: str) -> str:
            if args[:2] == ("pr", "view"):
                return view
            if args[:2] == ("api", "repos/owner/example"):
                return "main\n"
            raise AssertionError(f"unexpected gh {args}")

        # GitHub's pulls endpoint lists only open pull requests unless asked for state=all.
        def lookup(*args: str) -> str:
            head = next(a for a in args if a.startswith("head=")).removeprefix("head=")
            self.asked.append(head)
            pulls = dict(open_pulls, **(merged_pulls or {})) if "state=all" in args else open_pulls
            return pulls.get(head, "") + "\n"

        with mock.patch.object(summary, "gh", side_effect=run), \
             mock.patch.object(summary, "merge_base", return_value="c" * 40), \
             mock.patch.object(summary, "gh_lookup", side_effect=lookup):
            return summary.pr_metadata("owner/example", 10)

    def test_records_the_open_pull_request_its_base_branch_belongs_to(self) -> None:
        meta = self.metadata("feature-a", {"owner:feature-a": "9"})

        self.assertEqual(("feature-b", "feature-a", 9), (meta["headRef"], meta["baseRef"], meta["basePr"]))

    def test_a_long_lived_base_branch_is_not_stacked_on_a_merged_pull_request(self) -> None:
        meta = self.metadata("develop", {}, merged_pulls={"owner:develop": "134"})

        self.assertNotIn("basePr", meta, "the last develop -> main release pull request is not what this one sits on")

    def test_a_pull_request_on_the_default_branch_asks_nothing(self) -> None:
        meta = self.metadata("main", {"owner:main": "7"})

        self.assertNotIn("basePr", meta, "an open main -> production pull request does not stack everything on main")
        self.assertEqual([], self.asked)

    def test_the_live_head_check_asks_nothing_about_stacks(self) -> None:
        view = json.dumps({"title": "T", "headRefOid": "d" * 40, "headRefName": "feature-b", "baseRefName": "feature-a"})

        with mock.patch.object(summary, "gh", return_value=view), \
             mock.patch.object(summary, "merge_base", return_value="c" * 40), \
             mock.patch.object(summary, "gh_lookup", side_effect=AssertionError("no stack lookup")):
            meta = summary.pr_metadata("owner/example", 10, stack=False)

        self.assertNotIn("basePr", meta)


class PrReviewsTests(unittest.TestCase):
    def test_reads_every_page_of_reviews_one_row_each(self) -> None:
        rows = [{"body": "First\nline two", "url": "https://github.com/owner/example/pull/7#pullrequestreview-1",
                 "at": "2026-10-01T10:00:00Z", "association": "OWNER"},
                {"body": None, "url": "https://github.com/owner/example/pull/7#pullrequestreview-2",
                 "at": "2026-10-02T10:00:00Z", "association": "NONE"}]
        with mock.patch.object(summary, "gh", return_value="".join(json.dumps(r) + "\n" for r in rows)) as gh:
            reviews = summary.pr_reviews("owner/example", 7)

        self.assertEqual(rows, reviews)
        args = gh.call_args.args
        self.assertEqual(("api", "--paginate", "repos/owner/example/pulls/7/reviews"), args[:3])
        self.assertIn("select(.submitted_at != null)", args[-1], "a pending review has no verdict to show yet")
        self.assertIn("association: .author_association", args[-1], "the site trusts a verdict by who wrote it")


class StatusTests(unittest.TestCase):
    def status(self, answers: dict[str, str | None], *args: str) -> tuple[int, str, str]:
        def lookup(*gh_args: str) -> str | None:
            endpoint = gh_args[1]
            if endpoint not in answers:
                raise summary.SummaryError(f"gh api {endpoint} failed: connection refused")
            return answers[endpoint]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(summary, "gh_lookup", side_effect=lookup), redirect_stdout(out), redirect_stderr(err):
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

    def test_a_repository_with_the_workflow_and_a_site_prints_the_review_url(self) -> None:
        answers = {self.WORKFLOW: self.PRESENT, self.PAGES: self.pages(), self.REPO: "false\n"}
        self.assertEqual((0, "https://o.example/r/\n", ""), self.status(answers))
        self.assertEqual((0, "https://o.example/r/reviews/66\n", ""), self.status(answers, "--pr", "66"))

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
            return mock.patch.object(summary.subprocess, "run", side_effect=subprocess.CalledProcessError(
                1, ["gh"], output="", stderr=stderr))

        with failing("gh: Not Found (HTTP 404)\n"):
            self.assertIsNone(summary.gh_lookup("api", "repos/o/r/pages"))
        with failing("gh: Bad credentials (HTTP 401)\n"), self.assertRaisesRegex(summary.SummaryError, "HTTP 401"):
            summary.gh_lookup("api", "repos/o/r/pages")


HEAD = "a" * 40
PAGE = "https://owner.github.io/repo/reviews/7"


SITE = "https://owner.github.io/repo/"
BLOCK = f"<!-- projector-summary v=1 sha={HEAD} -->\n📽️ **Projector summary** of aaaaaaa: {PAGE}\n"


class DescribeSummaryTests(unittest.TestCase):
    """The pull request's description stands in a string; the fake answers the calls describe_summary makes."""

    def setUp(self) -> None:
        self.body: str | None = "Why this exists.\n\n## Testing\n\nRun the suite."
        self.calls: list[str] = []
        self.refuse = ""

    def gh(self, *args: str, input: str | None = None) -> str:
        if args == ("api", "repos/owner/repo/pulls/7", "--jq", '.body // "" | @json'):
            return json.dumps(self.body or "") + "\n"
        if args == ("api", "-X", "PATCH", "repos/owner/repo/pulls/7", "--input", "-"):
            self.calls.append("patch")
            if self.refuse:
                raise summary.SummaryError(self.refuse)
            self.body = json.loads(input or "")["body"]
            return "{}"
        raise AssertionError(f"unexpected gh call {args}")

    def describe(self, head: str = HEAD, site: str = SITE) -> str:
        with mock.patch.object(summary, "gh", side_effect=self.gh):
            return summary.describe_summary("owner/repo", 7, head, site)

    def test_the_link_goes_at_the_very_bottom_after_a_blank_line(self) -> None:
        result = self.describe()

        self.assertEqual(f"Why this exists.\n\n## Testing\n\nRun the suite.\n\n{BLOCK}", self.body)
        self.assertIn("linked the summary of aaaaaaa at the end of the description", result)

        self.assertIn("already links", self.describe())
        self.assertEqual(["patch"], self.calls, "a rerun on the same head edits nothing")

    def test_a_later_head_replaces_the_link_rather_than_adding_one(self) -> None:
        self.describe()

        self.describe("b" * 40)

        self.assertEqual(1, self.body.count("projector-summary"))
        self.assertTrue(self.body.endswith(f"of bbbbbbb: {PAGE}\n"))
        self.assertTrue(self.body.startswith("Why this exists.\n\n## Testing\n\nRun the suite.\n\n<!--"))

    def test_text_added_after_the_link_moves_above_it(self) -> None:
        self.body = f"Why.\r\n\r\n{BLOCK.replace(chr(10), chr(13) + chr(10))}\r\nA note added in the browser."

        self.describe("b" * 40)

        self.assertTrue(self.body.startswith("Why.\r\n\r\n\r\nA note added in the browser.\n\n<!--"), self.body)
        self.assertEqual(1, self.body.count("projector-summary"))

    def test_a_quoted_marker_is_not_a_link_block(self) -> None:
        self.body = "The comment opens with `<!-- projector-summary v=1 sha=... -->`."

        self.describe()

        self.assertTrue(self.body.startswith("The comment opens with `<!-- projector-summary v=1 sha=... -->`.\n\n"))

    def test_an_empty_description_gets_just_the_link(self) -> None:
        self.body = None

        self.describe()

        self.assertEqual(BLOCK, self.body)

    def test_a_refused_edit_is_reported_rather_than_raised(self) -> None:
        self.refuse = "gh api failed: Resource not accessible by integration (HTTP 403)"

        result = self.describe()

        self.assertIn("not linking the summary from the description: gh api failed", result)

    def test_a_refused_edit_does_not_echo_the_description(self) -> None:
        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if "PATCH" in command:
                raise subprocess.CalledProcessError(1, command, stderr="Resource not accessible by integration (HTTP 403)")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(self.body) + "\n", stderr="")

        with mock.patch.object(summary.subprocess, "run", side_effect=run):
            result = summary.describe_summary("owner/repo", 7, HEAD, SITE)

        self.assertIn("(HTTP 403)", result)
        self.assertNotIn("Why this exists.", result)

    def test_a_site_with_no_pages_url_leaves_the_description_alone(self) -> None:
        with mock.patch.object(summary, "gh", side_effect=AssertionError("no GitHub call")):
            self.assertIn("has no URL", summary.describe_summary("owner/repo", 7, HEAD, ""))


class CommentSummaryTests(unittest.TestCase):
    """The pull request's comments stand in a list; the fake answers the calls comment_summary makes."""

    def setUp(self) -> None:
        self.comments = [{"id": 1, "user": {"login": "teammate"}, "body": "Nice."}]
        self.calls: list[str] = []
        self.hosted: tuple[str | None, str] = ("https://owner.github.io/repo/", "")
        self.next_id = 101

    def gh(self, *args: str) -> str:
        if args == ("api", "user", "--jq", ".login"):
            return "operator\n"
        if args[:3] == ("api", "--paginate", "repos/owner/repo/issues/7/comments"):
            return "".join(json.dumps([c["id"], c["body"] if c["user"]["login"] == "operator"
                                       and "<!-- projector-summary v=1 " in c["body"] else None]) + "\n"
                           for c in self.comments)
        if args[:3] == ("api", "-X", "DELETE"):
            comment_id = int(args[3].rsplit("/", 1)[1])
            self.calls.append(f"delete {comment_id}")
            self.comments.remove(next(c for c in self.comments if c["id"] == comment_id))
            return ""
        if args[:2] == ("api", "repos/owner/repo/issues/7/comments") and args[2] == "-f":
            new = {"id": self.next_id, "user": {"login": "operator"}, "body": args[3].removeprefix("body=")}
            self.next_id += 1
            self.comments.append(new)
            self.calls.append(f"post {new['id']}")
            return json.dumps({"html_url": f"https://github.com/owner/repo/pull/7#issuecomment-{new['id']}"})
        raise AssertionError(f"unexpected gh call {args}")

    def comment(self, head: str = HEAD) -> str:
        with mock.patch.object(summary, "gh", side_effect=self.gh), \
             mock.patch.object(summary, "hosting", return_value=self.hosted):
            return summary.comment_summary("owner/repo", 7, head)

    def summary_comments(self) -> list[str]:
        return [c["body"] for c in self.comments if "projector-summary" in c["body"]]

    def test_the_first_summary_posts_a_comment_linking_it(self) -> None:
        result = self.comment()

        self.assertEqual(["post 101"], self.calls)
        self.assertEqual([f"<!-- projector-summary v=1 sha={HEAD} -->\n"
                          f"📽️ **Projector summary** of aaaaaaa: {PAGE}\n"], self.summary_comments())
        self.assertIn("commented a link to the summary of aaaaaaa", result)

        self.assertIn("already links the summary", self.comment())
        self.assertEqual(["post 101"], self.calls, "a rerun on the same head adds nothing")

    def test_a_later_head_reposts_the_comment_at_the_end_and_deletes_the_old_one(self) -> None:
        self.comment()
        self.comments.append({"id": 2, "user": {"login": "teammate"}, "body": "Pushed a fix."})

        result = self.comment("b" * 40)

        self.assertEqual(["post 101", "post 102", "delete 101"], self.calls, "the new link is up before the old goes")
        self.assertEqual([1, 2, 102], [c["id"] for c in self.comments])
        self.assertIn("sha=" + "b" * 40, self.summary_comments()[0])
        self.assertIn("moved the link to the summary of bbbbbbb to a new comment at the end, deleting comment 101",
                      result)

    def test_a_rerun_on_the_same_head_moves_a_buried_comment_to_the_end(self) -> None:
        self.comment()
        self.comments.append({"id": 2, "user": {"login": "teammate"}, "body": "One question."})

        self.comment()

        self.assertEqual(["post 101", "post 102", "delete 101"], self.calls)
        self.assertEqual([1, 2, 102], [c["id"] for c in self.comments])

    def test_every_older_summary_comment_of_the_account_is_deleted(self) -> None:
        for comment_id in (5, 6):
            self.comments.append({"id": comment_id, "user": {"login": "operator"},
                                  "body": f"<!-- projector-summary v=1 sha={'c' * 40} -->\nOld."})

        result = self.comment()

        self.assertEqual(["post 101", "delete 5", "delete 6"], self.calls)
        self.assertEqual([1, 101], [c["id"] for c in self.comments])
        self.assertIn("deleting comment 5, comment 6", result)

    def test_a_rerun_deletes_an_older_summary_comment_a_failed_delete_left_behind(self) -> None:
        self.comment()
        self.comments.insert(1, {"id": 5, "user": {"login": "operator"},
                                 "body": f"<!-- projector-summary v=1 sha={'c' * 40} -->\nOld."})
        self.calls.clear()

        result = self.comment()

        self.assertEqual(["delete 5"], self.calls, "the newest comment stays, and the straggler goes")
        self.assertEqual([1, 101], [c["id"] for c in self.comments])
        self.assertIn("comment 101 already links the summary of aaaaaaa, deleting comment 5", result)

    def test_another_accounts_summary_comment_is_left_alone(self) -> None:
        self.comments.append({"id": 2, "user": {"login": "teammate"},
                              "body": f"<!-- projector-summary v=1 sha={HEAD} -->\nTheirs."})

        self.comment()

        self.assertEqual(["post"], [call.split()[0] for call in self.calls], "a comment of your own is posted")
        self.assertEqual(f"<!-- projector-summary v=1 sha={HEAD} -->\nTheirs.", self.comments[1]["body"])

    def test_a_site_the_caller_already_found_is_not_looked_up_again(self) -> None:
        with mock.patch.object(summary, "gh", side_effect=self.gh), \
             mock.patch.object(summary, "hosting", side_effect=AssertionError("the caller already found the site")):
            summary.comment_summary("owner/repo", 7, HEAD, site="https://owner.github.io/repo/")

        self.assertEqual([f"<!-- projector-summary v=1 sha={HEAD} -->\n📽️ **Projector summary** of aaaaaaa: {PAGE}\n"],
                         self.summary_comments())

    def test_a_hosted_site_with_no_pages_url_gets_no_comment(self) -> None:
        for site in ("", None):
            with self.subTest(site=site), \
                 mock.patch.object(summary, "gh", side_effect=AssertionError("no GitHub call")), \
                 mock.patch.object(summary, "hosting", return_value=("", "")):
                result = summary.comment_summary("owner/repo", 7, HEAD, site=site)

                self.assertIn("GitHub's Pages answer for owner/repo has no URL", result)
        self.assertEqual([], self.summary_comments(), "a relative reviews/7 would link nowhere")

    def test_a_site_that_is_not_hosted_gets_no_comment(self) -> None:
        self.hosted = (None, "Pages is not enabled")
        with mock.patch.object(summary, "gh", side_effect=AssertionError("no GitHub call")), \
             mock.patch.object(summary, "hosting", return_value=self.hosted):
            self.assertIn("Pages is not enabled", summary.comment_summary("owner/repo", 7, HEAD))

    def cli_publish(self, hosted: tuple[str | None, str] | Exception, *flags: str) -> tuple[list[dict], list[tuple], str]:
        """Run `summary publish` with the repository's hosting answering, or raising, `hosted`.

        Returns the keyword arguments publish got, the comments made, and the output. The
        descriptions linked stand in `self.described`.
        """
        path = Path(tempfile.mkdtemp()) / "summary.json"
        path.write_text(json.dumps({"version": summary.SUMMARY_VERSION, "pr": {"repo": "owner/repo", "number": 7, "head": HEAD}}))
        published: list[dict] = []
        commented: list[tuple] = []
        self.described: list[tuple] = []
        out = io.StringIO()
        answer = {"side_effect": hosted} if isinstance(hosted, Exception) else {"return_value": hosted}
        with mock.patch.object(summary, "publish", side_effect=lambda *a, **k: published.append(k)), \
             mock.patch.object(summary, "hosting", **answer) as lookup, \
             mock.patch.object(summary, "comment_summary",
                               side_effect=lambda *a, **k: commented.append((*a, k)) or "done"), \
             mock.patch.object(summary, "describe_summary",
                               side_effect=lambda *a: self.described.append(a) or "described"), \
             redirect_stdout(out):
            self.assertEqual(0, cli.main(["summary", "publish", "--summary", str(path), *flags]))
        self.assertLessEqual(lookup.call_count, 1, "hosting is looked up at most once")
        return published, commented, out.getvalue()

    def test_summary_publish_comments_the_link_for_the_summary_head(self) -> None:
        published, commented, out = self.cli_publish(("https://owner.github.io/repo/", ""))

        self.assertTrue(published[0]["push"])
        self.assertEqual([("owner/repo", 7, HEAD, {"site": "https://owner.github.io/repo/"})], commented,
                         "the comment reuses the site the push decision found")
        self.assertEqual([("owner/repo", 7, HEAD, "https://owner.github.io/repo/")], self.described)
        self.assertIn("done", out)
        self.assertIn("described", out)

    def test_summary_publish_pushes_where_a_hosted_sites_pages_answer_has_no_url(self) -> None:
        published, commented, _ = self.cli_publish(("", ""))

        self.assertTrue(published[0]["push"], "hosted, as `site status` calls it")
        self.assertEqual([("owner/repo", 7, HEAD, {"site": ""})], commented)

    def test_summary_publish_keeps_the_summary_local_when_the_hosting_check_fails(self) -> None:
        published, commented, _ = self.cli_publish(summary.SummaryError("gh api failed: HTTP 502"))

        self.assertFalse(published[0]["push"])
        self.assertIn("could not tell whether owner/repo hosts its Projector site", published[0]["reason"])
        self.assertEqual([], commented)

    def test_summary_publish_keeps_the_summary_local_where_the_site_is_not_hosted(self) -> None:
        published, commented, _ = self.cli_publish((None, "owner/repo has no GitHub Pages site"))

        self.assertEqual([(False, "owner/repo has no GitHub Pages site")], [(k["push"], k["reason"]) for k in published])
        self.assertEqual([], commented, "a summary kept local has no page on GitHub to link")
        self.assertEqual([], self.described)

    def test_summary_publish_local_previews_even_where_the_site_is_hosted(self) -> None:
        with mock.patch.object(summary, "push_decision", side_effect=AssertionError("--local asks GitHub nothing")):
            published, commented, _ = self.cli_publish(("https://owner.github.io/repo/", ""), "--local")

        self.assertFalse(published[0]["push"])
        self.assertEqual([], commented)


if __name__ == "__main__":
    unittest.main()
