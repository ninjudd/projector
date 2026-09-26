from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "walkthrough-pr" / "scripts" / "walkthrough.py"

spec = importlib.util.spec_from_file_location("walkthrough", SCRIPT)
walkthrough = importlib.util.module_from_spec(spec)
spec.loader.exec_module(walkthrough)

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

    def test_anchor_matches_github_files_tab(self) -> None:
        core = walkthrough.parse_diff(DIFF)[0]
        # GitHub anchors a file on the files tab by the SHA-256 of its path.
        self.assertEqual(
            "diff-" + __import__("hashlib").sha256(b"src/core.go").hexdigest(),
            core["anchor"],
        )


class BuildTests(unittest.TestCase):
    def build(self, groups: list[dict]) -> tuple[int, Path, str]:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "pr.diff").write_text(DIFF)
        (tmp / "spec.json").write_text(json.dumps(make_spec(groups)))
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = walkthrough.main(["build", "--spec", str(tmp / "spec.json"), "--out", str(tmp / "site"), "--diff", str(tmp / "pr.diff")])
        return code, tmp / "site", err.getvalue()

    def test_writes_the_page_and_the_renderer(self) -> None:
        code, site, err = self.build(GOOD_GROUPS)

        self.assertEqual(0, code, err)
        html = (site / "index.html").read_text()
        self.assertTrue(html.startswith("<title>Core Walkthrough</title>"))
        self.assertTrue((site / "walkthrough.js").is_file())
        self.assertTrue((site / "walkthrough.css").is_file())
        # A literal </script> inside the diff must not end the data block.
        self.assertNotIn('"</script>"', html)
        start = html.index('type="application/json">') + len('type="application/json">')
        data = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual(3, data["stats"]["files"])
        self.assertEqual({"files": 3, "adds": 5, "dels": 2, "hand": 3, "test": 2, "generated": 2, "docs": 0}, data["stats"])
        self.assertEqual(["core", "gen"], [g["id"] for g in data["groups"]])

    def test_refuses_a_file_in_no_group(self) -> None:
        code, _, err = self.build([GOOD_GROUPS[0]])
        self.assertEqual(1, code)
        self.assertIn("gen/api.pb.go is in no group", err)

    def test_refuses_a_file_in_two_groups(self) -> None:
        groups = [GOOD_GROUPS[0], {"id": "gen", "title": "Generated", "files": [{"path": "gen/api.pb.go"}, {"path": "src/core.go"}]}]
        code, _, err = self.build(groups)
        self.assertEqual(1, code)
        self.assertIn("src/core.go is in both core and gen", err)

    def test_refuses_a_path_outside_the_diff_and_a_bad_check(self) -> None:
        groups = [dict(GOOD_GROUPS[0], checks=[{"kind": "maybe", "text": "x"}]),
                  {"id": "gen", "title": "Generated", "files": [{"path": "gen/api.pb.go"}, {"path": "src/gone.go"}]}]
        code, _, err = self.build(groups)
        self.assertEqual(1, code)
        self.assertIn("gen: src/gone.go is not in the diff", err)
        self.assertIn("core: each check needs kind verify or flag", err)

    def test_refuses_a_duplicate_group_id(self) -> None:
        groups = [GOOD_GROUPS[0], dict(GOOD_GROUPS[1], id="core")]
        code, _, err = self.build(groups)
        self.assertEqual(1, code)
        self.assertIn("group id 'core' is used twice", err)


class RendererTests(unittest.TestCase):
    def test_renderer_ships_only_allowlisted_remote_scripts(self) -> None:
        # Claude Artifacts load scripts only from a CDN allowlist; the page
        # template and renderer must not reach anywhere else.
        html = walkthrough.page("T", make_spec(GOOD_GROUPS) | {"pr": make_spec([])["pr"]})
        for src in __import__("re").findall(r'src="(https?://[^"]+)"', html):
            self.assertTrue(src.startswith("https://cdnjs.cloudflare.com/"), src)
        js = (ROOT / "skills" / "walkthrough-pr" / "assets" / "walkthrough.js").read_text()
        self.assertNotIn("fetch(", js)


class AtHeadTests(unittest.TestCase):
    def test_at_head_fetches_the_recorded_merge_base_and_head_without_checking_the_live_pr(self) -> None:
        out = Path(tempfile.mkdtemp()) / "site"
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF) as fetch, \
             mock.patch.object(walkthrough, "pr_metadata", side_effect=AssertionError("must not check the live PR")):
            walkthrough.build_page(make_spec(GOOD_GROUPS), out, at_head=True)
        fetch.assert_called_once_with("owner/repo", "b" * 40, "a" * 40)
        self.assertTrue((out / "index.html").is_file())

    def test_default_build_refuses_a_moved_head(self) -> None:
        live = dict(make_spec([])["pr"], head="c" * 40)
        with mock.patch.object(walkthrough, "pr_metadata", return_value=live), \
             mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF):
            with self.assertRaisesRegex(walkthrough.SpecError, "moved from aaaaaaaaa to ccccccccc"):
                walkthrough.build_page(make_spec(GOOD_GROUPS), Path(tempfile.mkdtemp()))


class SiteTests(unittest.TestCase):
    def write_spec(self, root: Path, head: str, name: str) -> Path:
        spec = make_spec(GOOD_GROUPS)
        spec["pr"]["head"] = head
        spec["name"] = name
        path = root / "7" / head / "spec.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(spec))
        return path

    def test_builds_every_head_an_index_and_a_link_to_the_newest(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        old = self.write_spec(tmp / "walkthroughs", "a" * 40, "Old")
        new = self.write_spec(tmp / "walkthroughs", "c" * 40, "New")
        times = {old: 100, new: 200}
        with mock.patch.object(walkthrough, "fetch_diff", return_value=DIFF), \
             mock.patch.object(walkthrough, "spec_time", side_effect=lambda p: times[p]), \
             redirect_stdout(io.StringIO()):
            walkthrough.build_site(tmp / "walkthroughs", tmp / "site")

        site = tmp / "site"
        self.assertIn('url=cccccccccccccccccccccccccccccccccccccccc/', (site / "7" / "index.html").read_text())
        index = (site / "index.html").read_text()
        self.assertIn('href="7/"', index)
        self.assertIn(">New<", index)
        self.assertTrue((site / ".nojekyll").exists())
        page = (site / "7" / ("a" * 40) / "index.html").read_text()
        data = json.loads(page[page.index('type="application/json">') + 24:page.index("</script>", page.index('type="application/json">'))])
        self.assertEqual("../../", data["indexUrl"])
        self.assertEqual([("c" * 40, False), ("a" * 40, True)], [(h["head"], h["current"]) for h in data["heads"]])

    def test_refuses_a_spec_filed_under_the_wrong_head(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        path = self.write_spec(tmp / "walkthroughs", "a" * 40, "Old")
        path.rename(path.parent.parent / "spec-moved.json")
        wrong = tmp / "walkthroughs" / "7" / ("d" * 40) / "spec.json"
        wrong.parent.mkdir()
        wrong.write_text((path.parent.parent / "spec-moved.json").read_text())
        with self.assertRaisesRegex(walkthrough.SpecError, "must sit at <pr.number>/<pr.head>/spec.json"):
            walkthrough.build_site(tmp / "walkthroughs", tmp / "site")


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
            with mock.patch.object(walkthrough, "dispatch", side_effect=self.dispatches.append), redirect_stdout(io.StringIO()):
                return walkthrough.publish(self.spec, "origin", **kwargs)
        finally:
            os.chdir(cwd)

    def ref_files(self) -> list[str]:
        return run(self.remote, "git", "ls-tree", "-r", "--name-only", "refs/projector/walkthroughs").splitlines()

    def test_first_publish_creates_the_hidden_ref_dispatches_and_leaves_the_checkout_alone(self) -> None:
        (self.repo / "README.md").write_text("dirty\n")
        (self.repo / "untracked.txt").write_text("keep\n")
        before = run(self.repo, "git", "rev-parse", "HEAD")

        self.assertIsNotNone(self.publish("a" * 40))

        self.assertEqual([f"walkthroughs/7/{'a' * 40}/spec.json"], self.ref_files())
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

    def test_no_dispatch_pushes_without_starting_the_workflow(self) -> None:
        self.publish("a" * 40, send_dispatch=False)
        self.assertEqual([], self.dispatches)
        self.assertTrue(self.ref_files())

    def test_refuses_a_spec_for_another_repository(self) -> None:
        run(self.repo, "git", "remote", "set-url", "origin", "git@github.com:someone/else.git")
        with self.assertRaisesRegex(walkthrough.SpecError, "origin is someone/else, but the spec is for owner/repo"):
            self.publish("a" * 40)


class WorkflowTests(unittest.TestCase):
    def test_the_workflow_runs_on_dispatch_and_pins_the_action_ref(self) -> None:
        text = walkthrough.workflow_text("v0")
        self.assertIn("repository_dispatch:\n    types: [projector-walkthroughs]", text)
        self.assertIn("uses: ninjudd/projector/actions/walkthroughs@v0", text)
        self.assertIn("${{ steps.walkthroughs.outputs.page_url }}", text)
        self.assertNotIn("push:", text)
        self.assertIn("@0123abc", walkthrough.workflow_text("0123abc"))

    def test_repo_slug_reads_github_remotes(self) -> None:
        for url in ("git@github.com:o/r.git", "ssh://git@github.com/o/r.git", "https://github.com/o/r.git", "https://github.com/o/r"):
            self.assertEqual("o/r", walkthrough.repo_slug(url), url)
        self.assertEqual("", walkthrough.repo_slug("https://gitlab.com/o/r.git"))


if __name__ == "__main__":
    unittest.main()
