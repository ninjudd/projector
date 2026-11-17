"""A session's pull request rows and a drawer's threads, as `src/projector/rows.py` reads them.

The cases in `tests/fixtures/rows/` run here and, through the panel's own model, in `hooks/rows.test.ts`, so
a rule that changes in one copy only fails the other copy's tests. Each case file is a TypeScript module,
because the panel's test runtime can import a module but not read a file, and its body after
`export default ` is JSON.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from projector import rows, watch

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rows"
PREFIX = "export default "


def case_files() -> list[Path]:
    return sorted(path for path in FIXTURES.glob("*.ts") if path.name != "index.ts")


def load_cases() -> list[dict]:
    cases = []
    for path in case_files():
        text = path.read_text(encoding="utf-8")
        if not text.startswith(PREFIX):
            raise AssertionError(f"{path.name} does not start with {PREFIX!r}")
        cases += json.loads(text[len(PREFIX):])
    return cases


def write_files(root: Path, files: dict) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


def grouped(threads: list[rows.Thread]) -> list:
    return [[path, [asdict(thread) for thread in found]] for path, found in rows.threads_by_file(threads)]


CASES = load_cases()


class SharedFixtures(unittest.TestCase):
    def test_the_panel_test_runs_every_case_file(self):
        index = (FIXTURES / "index.ts").read_text(encoding="utf-8")
        imported = dict(re.findall(r"^import (\w+) from '\./([\w-]+)'$", index, re.M))
        exported = re.search(r"^export default \[(.*)\]$", index, re.M)
        self.assertEqual([path.stem for path in case_files()],
                         sorted(imported[name.strip()] for name in (exported[1] if exported else "").split(",")))

    def test_each_case_gives_the_rows_the_panel_lists(self):
        found = [case for case in CASES if "files" in case]
        self.assertTrue(found)
        for case in found:
            with self.subTest(case["name"]), tempfile.TemporaryDirectory() as folder:
                write_files(Path(folder), case["files"])
                self.assertEqual(case["rows"], [asdict(row) for row in rows.rows_for(Path(folder), case["session"])])

    def test_the_thread_query_is_the_panels(self):
        found = [case for case in CASES if "query" in case]
        self.assertTrue(found)
        for case in found:
            with self.subTest(case["name"]):
                self.assertEqual(case["query"], rows.threads_query(case["pr"]["repo"], case["pr"]["number"]))

    def test_each_response_gives_the_threads_the_drawer_lists(self):
        found = [case for case in CASES if "stdout" in case]
        self.assertTrue(found)
        for case in found:
            with self.subTest(case["name"]):
                stdout = case["stdout"] if isinstance(case["stdout"], str) else json.dumps(case["stdout"])
                threads = rows.parse_threads(case["pr"]["number"], stdout)
                self.assertEqual(case["threads"], None if threads is None else grouped(threads))


class Files(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_a_session_that_subscribes_to_nothing_has_no_rows(self):
        (self.root / "sessions" / "s1").mkdir(parents=True)
        self.assertEqual([], rows.rows_for(self.root, "s1"))

    def test_a_cache_file_that_cannot_be_read_gives_a_loading_row(self):
        write_files(self.root, {"sessions/s1/subscriptions": "acme/app#3 fix\n"})
        watch.pull_cache(self.root, "acme/app", 3).mkdir(parents=True)
        self.assertEqual([("#3", True)], [(row.label, row.pull.is_loading) for row in rows.rows_for(self.root, "s1")])


class FetchThreads(unittest.TestCase):
    RESPONSE = json.dumps({"data": {"r0": {"p12": {"reviewThreads": {"nodes": [
        {"isResolved": False, "path": "src/a.py", "line": 3, "comments": {"nodes": [{"bodyText": "Rename this."}]}},
    ]}}}}})

    def setUp(self):
        self.calls: list[list[str]] = []

    def gh(self, stdout: str, returncode: int = 0):
        def run(args: list[str]) -> watch.GhResult:
            self.calls.append(args)
            return watch.GhResult(returncode, stdout)
        return run

    def test_a_pull_request_without_an_unresolved_thread_asks_github_nothing(self):
        self.assertEqual([], rows.fetch_threads(rows.loading_pull("acme/app", 12), self.gh(self.RESPONSE)))
        self.assertEqual([], self.calls)

    def test_a_pull_request_with_an_unresolved_thread_sends_the_thread_query(self):
        pull = rows.to_pull("acme/app", 12, {"reviewThreads": {"nodes": [{"isResolved": False}]}})
        threads = rows.fetch_threads(pull, self.gh(self.RESPONSE))
        self.assertEqual(([["api", "graphql", "-f", f"query={rows.threads_query('acme/app', 12)}"]],
                          [("src/a.py", 3, "Rename this.")]),
                         (self.calls, [(thread.path, thread.line, thread.excerpt) for thread in threads or []]))

    def test_a_failed_query_gives_none_so_the_drawer_keeps_what_it_listed(self):
        pull = rows.to_pull("acme/app", 12, {"reviewThreads": {"nodes": [{"isResolved": False}]}})
        self.assertIsNone(rows.fetch_threads(pull, self.gh("", returncode=1)))


if __name__ == "__main__":
    unittest.main()
