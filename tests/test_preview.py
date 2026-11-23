"""`project preview`: print a pull request's Vercel preview, which the panel's drawer and the console link.

The cases in `tests/fixtures/previews.json` each give a pull request's node, as the watch daemon's query returns
it, and the preview it gives.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, review, rows, watch

CASES = json.loads((Path(__file__).resolve().parent / "fixtures" / "previews.json").read_text(encoding="utf-8"))


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name)
        self.root = self.state / "projector" / "watch"
        environment = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)})
        environment.start()
        self.addCleanup(environment.stop)
        self.queries: list[list[str]] = []
        self.live: object = None
        github = mock.patch.object(review, "run_gh", self.github)
        github.start()
        self.addCleanup(github.stop)

    def github(self, args: list[str], token: str | None = None) -> str:
        self.queries.append(args)
        return json.dumps(self.live)

    def cache(self, number: int, node: dict) -> None:
        path = watch.pull_cache(self.root, "acme/app", number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"repository": {"pullRequest": node}}), encoding="utf-8")

    def project(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["preview", *args])
        return code, out.getvalue(), err.getvalue()

    def test_each_case_gives_its_preview_from_the_daemons_result(self) -> None:
        # Catches a rule that links a skipped project, an older comment, a person's look-alike, a host that is not
        # one, or a failed build, or that hides the branch preview while a push builds.
        for number, case in enumerate(CASES, start=1):
            with self.subTest(case["name"]):
                self.cache(number, case["node"])
                self.assertEqual(case["preview"], rows.pull_preview(self.root, "acme/app", number))
                self.assertEqual(case["preview"], rows.cached_preview(self.root, "acme/app", number))
        # The daemon's result costs GitHub nothing.
        self.assertEqual([], self.queries)

    def test_prints_the_preview_and_exits_66_when_there_is_none(self) -> None:
        # Catches a command that prints nothing for a preview, or succeeds without one, which the panel would
        # take for a link.
        found = next(case for case in CASES if case["preview"] is not None)
        missing = next(case for case in CASES if case["preview"] is None)
        self.cache(1, found["node"])
        self.cache(2, missing["node"])

        self.assertEqual((0, f"{found['preview']}\n", ""), self.project("acme/app#1"))
        self.assertEqual((0, f"{found['preview']}\n", ""), self.project("https://github.com/acme/app/pull/1"))
        self.assertEqual((66, "", "project: acme/app#2 has no Vercel preview\n"), self.project("acme/app#2"))

    def test_refuses_a_repository_without_a_pull_request(self) -> None:
        code, out, err = self.project("acme/app")
        self.assertEqual((2, ""), (code, out))
        self.assertIn("names a repository", err)

    def test_asks_github_for_a_pull_request_the_daemon_has_not_fetched(self) -> None:
        # Catches a command that answers only for watched pull requests, or that asks for the wrong one.
        case = CASES[0]
        self.live = {"data": {"repository": {"pullRequest": case["node"]}}}

        self.assertEqual((0, f"{case['preview']}\n", ""), self.project("acme/app#9"))
        self.assertEqual([["api", "graphql", "-f", f"query={rows.PREVIEW_QUERY}", "-F", "o=acme", "-F", "r=app",
                           "-F", "n=9"]], self.queries)

    def test_says_when_github_has_no_such_pull_request(self) -> None:
        self.live = {"data": {"repository": {"pullRequest": None}}}
        code, out, err = self.project("acme/app#9")
        self.assertEqual((1, ""), (code, out))
        self.assertIn("acme/app#9 does not exist or is not visible to this account", err)


if __name__ == "__main__":
    unittest.main()
