from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "walk-through-pr" / "scripts" / "walkthrough.py"

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
        "pr": {"repo": "owner/repo", "number": 7, "title": "Change the core", "head": "a" * 40, "baseRef": "main"},
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
        js = (ROOT / "skills" / "walk-through-pr" / "assets" / "walkthrough.js").read_text()
        self.assertNotIn("fetch(", js)


if __name__ == "__main__":
    unittest.main()
