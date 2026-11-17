"""`project console`'s model, driven without a terminal against a temporary state directory.

Each test points `XDG_STATE_HOME` at its own directory, writes the files a live session leaves there, and drives
`console.Console` with key names and an injected clock, `gh`, browser, and Codex wake.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from projector import cli, console, inbox, watch
from projector.inbox import write_json

SOURCE = Path(cli.__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def pull_node(number: int, *, author: str = "operator", threads: int = 0) -> dict:
    return {"repository": {"pullRequest": {
        "title": f"Pull request {number}", "state": "OPEN", "author": {"login": author}, "headRefName": "feature",
        "reviewThreads": {"nodes": [{"isResolved": False} for _ in range(threads)]}}}}


class ConsoleCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        environ = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.scratch / "state")})
        environ.start()
        self.addCleanup(environ.stop)
        self.root = watch.state_root()
        self.clock = Clock()
        self.queries: list[list[str]] = []
        self.opened: list[str] = []
        self.woken: list[tuple[str, str]] = []
        self.console = self.new_console()

    def new_console(self) -> console.Console:
        return console.Console(self.root, clock=self.clock, gh=self.gh, open_url=self.opened.append,
                               wake=lambda session, item: self.woken.append((session, item["ref"])))

    def gh(self, args: list[str]) -> watch.GhResult:
        self.queries.append(args)
        return watch.GhResult(0, json.dumps({"data": {"r0": {}}}))

    def folder(self, session: str) -> Path:
        folder = watch.session_folder(self.root, session)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def claude(self, session: str, renewed_ago: float = 0) -> None:
        write_json(self.folder(session) / "session.json",
                   {"deliverer": "mod", "renewed_at": watch.stamp(self.clock.now - renewed_ago)})

    def codex(self, session: str, **fields) -> None:
        write_json(self.folder(session) / "codex.json",
                   {"schema_version": 1, "enabled": True, "expires_at": self.clock.now + 86400, **fields})

    def subscribe(self, session: str, *lines: str) -> None:
        (self.folder(session) / "subscriptions").write_text("".join(f"{line}\n" for line in lines))

    def cache(self, repo: str, number: int, **fields) -> None:
        path = watch.pull_cache(self.root, repo, number)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, pull_node(number, **fields))

    def ask(self, session: str, question: str = "Decline the finding?") -> dict:
        return inbox.ask(session, question, "Decline it", ["Fix it"])

    def press(self, *keys: str) -> None:
        for key in keys:
            self.console.key(key)

    def type(self, text: str) -> None:
        self.press(*text)

    def home_names(self) -> list[str]:
        return [session.name for session in self.console.home()]

    def screen(self) -> str:
        return "\n".join(line.text for line in self.console.lines(100)[0])

    def open_entry(self, session: str, key: str) -> None:
        """Open the session's panel and the drawer of the row under `key`."""
        self.console.tick()
        self.console.open_panel(session)
        self.console.selected = key
        self.press("enter")
        self.assertEqual(key, self.console.drawer)


class SessionSet(ConsoleCase):
    def test_the_home_list_holds_live_claude_and_codex_sessions_and_leaves_out_a_lapsed_one(self) -> None:
        self.claude("claude-session")
        write_json(self.folder("claude-session") / "agent.json",
                   {"schema_version": 1, "name": "fixer", "repo": "acme/app", "branch": "main"})
        checkout = self.scratch / "checkout"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "codex-branch"], cwd=checkout, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/web.git"], cwd=checkout, check=True)
        self.codex("019a2b3c-0000-7000-8000-00000000c0de", cwd=str(checkout))
        self.claude("lapsed-session", renewed_ago=600)
        self.ask("lapsed-session")

        self.console.tick()

        self.assertEqual([("00000000c0de", "codex", "acme/web codex-branch"), ("fixer", "claude", "acme/app main")],
                         sorted((one.label, one.host, one.place) for one in self.console.home()))


class HomeOrder(ConsoleCase):
    def test_a_session_with_an_unread_reply_lists_before_one_with_more_open_questions(self) -> None:
        for session in ("asking", "replying"):
            self.claude(session)
        self.ask("asking")
        self.ask("asking", "Merge it now?")
        replied = self.ask("replying", "Retry the mirrors?")
        inbox.discuss("replying", replied["ref"], "Which mirrors?")
        inbox.reply("replying", replied["ref"], "The two behind the proxy.")

        self.console.tick()

        self.assertEqual(["replying", "asking"], self.home_names())
        self.assertIn("‣ Retry the mirrors?  replied", self.screen())

    def test_opening_a_drawer_shows_the_reply_and_marks_it_read(self) -> None:
        self.claude("s1")
        item = self.ask("s1")
        inbox.reply("s1", item["ref"], "The two behind the proxy.")

        self.open_entry("s1", "item:1")

        self.assertIn("agent: The two behind the proxy.", self.screen())
        self.assertFalse(inbox.has_unread_reply(inbox.read_item(inbox.session_dir("s1"), "1")))
        self.press("left")
        self.assertNotIn("replied", self.screen())


class Recording(ConsoleCase):
    def test_an_option_answers_the_item_of_the_viewed_session_when_another_session_has_the_same_number(self) -> None:
        for session in ("first", "second"):
            self.claude(session)
            for question in ("One?", "Two?", "Three?"):
                self.ask(session, question)

        self.open_entry("second", "item:3")
        self.press("1")

        answers = [inbox.read_item(inbox.session_dir(name), "3").get("answer") for name in ("first", "second")]
        self.assertEqual([None, "a"], [answer and answer["letter"] for answer in answers])

    def test_an_answer_or_a_message_from_the_console_is_recorded_and_left_to_the_sessions_deliverer(self) -> None:
        self.claude("s1")
        self.ask("s1")
        self.ask("s1", "Merge it now?")

        self.open_entry("s1", "item:1")
        self.press("1")
        self.console.selected = "item:2"
        self.press("enter", "7")
        self.type("Why now?")
        self.press("enter")

        answered, discussed = (inbox.read_item(inbox.session_dir("s1"), ref) for ref in ("1", "2"))
        self.assertEqual((None, None, None, None, "Why now?"),
                         (answered.get("claim"), answered.get("deliveredAt"), discussed.get("claim"),
                          discussed["thread"][0].get("deliveredAt"), discussed["thread"][0]["text"]))

    def test_a_codex_sessions_answer_wakes_its_delivery_and_a_claude_sessions_does_not(self) -> None:
        self.codex("codex-session")
        self.claude("claude-session")
        for session in ("codex-session", "claude-session"):
            self.ask(session)
        for session in ("codex-session", "claude-session"):
            self.open_entry(session, "item:1")
            self.press("1")

        self.assertEqual([("codex-session", "1")], self.woken)


class Discussions(ConsoleCase):
    def test_seven_with_no_drawer_open_starts_a_discussion_about_an_empty_session(self) -> None:
        self.claude("idle")
        self.console.tick()
        self.console.open_panel("idle")

        self.press("7")
        self.type("hello")
        self.press("enter")

        item = inbox.read_item(inbox.session_dir("idle"), "1")
        self.assertEqual(("discussion", None, "DISCUSS 1 About this session → hello"),
                         (item["kind"], item["pr"], inbox.message(item)))

    def test_discussing_a_pull_request_two_sessions_hold_starts_the_discussion_in_the_viewed_session(self) -> None:
        self.claude("first")
        self.claude("second")
        self.subscribe("first", "acme/app#12 fix")
        self.subscribe("second", "acme/app#12 review claude-opus-5-5 operator")
        self.cache("acme/app", 12)

        self.open_entry("second", "pr:acme/app#12")
        self.press("7")
        self.type("Why the second retry loop?")
        self.press("enter")

        self.assertEqual(([], [("acme/app#12", "Why the second retry loop?")]),
                         tuple([(item["pr"], item["thread"][0]["text"])
                                for item in inbox.load_items(inbox.store_root() / name)]
                               for name in ("first", "second")))


class PullRequests(ConsoleCase):
    def test_dismissing_a_pull_request_drops_only_the_viewed_sessions_roles_after_confirmation(self) -> None:
        self.claude("first")
        self.claude("second")
        self.subscribe("first", "acme/app#12 fix")
        self.subscribe("second", "acme/app#12 fix", "acme/app#12 review claude-opus-5-5 operator")
        self.cache("acme/app", 12)
        self.open_entry("second", "pr:acme/app#12")

        self.press("8")
        unconfirmed = watch.read_lines(self.folder("second"))
        self.press("8")

        self.assertEqual(
            (["acme/app#12 fix", "acme/app#12 review claude-opus-5-5 operator"],
             ["acme/app#12 fix", "acme/app#12 review claude-opus-5-5 operator", "!acme/app#12 fix",
              "!acme/app#12 review claude-opus-5-5 operator"],
             ["acme/app#12 fix"]),
            (unconfirmed, watch.read_lines(self.folder("second")), watch.read_lines(self.folder("first"))))

    def test_an_open_drawer_asks_for_threads_every_30_seconds_and_never_without_an_unresolved_thread(self) -> None:
        for number, threads, expected in ((12, 2, 3), (13, 0, 0)):
            with self.subTest(threads=threads):
                self.queries.clear()
                self.console = self.new_console()
                self.claude(f"s{number}")
                self.subscribe(f"s{number}", f"acme/app#{number} fix")
                self.cache("acme/app", number, threads=threads)
                self.open_entry(f"s{number}", f"pr:acme/app#{number}")
                start = self.clock.now
                for second in range(66):
                    self.clock.now = start + second
                    self.console.tick()
                self.assertEqual(expected, len(self.queries))

    def test_o_opens_the_pull_request_in_a_browser(self) -> None:
        self.claude("s1")
        self.subscribe("s1", "acme/app#12 fix")
        self.cache("acme/app", 12)
        self.open_entry("s1", "pr:acme/app#12")

        self.press("o")

        self.assertEqual(["https://github.com/acme/app/pull/12"], self.opened)


class Keys(ConsoleCase):
    def test_left_returns_home_closing_the_drawer_but_moves_the_cursor_in_a_text_field(self) -> None:
        self.claude("s1")
        self.ask("s1")
        self.open_entry("s1", "item:1")

        self.press("7")
        self.type("ac")
        self.press("left")
        self.type("b")
        self.press("enter")
        in_field = (self.console.view, self.console.drawer)
        self.press("left")

        self.assertEqual((("panel", "item:1"), ("home", None), "abc"),
                         (in_field, (self.console.view, self.console.drawer),
                          inbox.read_item(inbox.session_dir("s1"), "1")["thread"][0]["text"]))


class Command(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_project_console_without_a_terminal_on_standard_input_exits_69(self) -> None:
        # Standard output is a terminal, as when a person types `project console </dev/null` at a shell.
        primary, secondary = os.openpty()
        self.addCleanup(os.close, primary)
        with os.fdopen(secondary, "wb") as terminal:
            done = subprocess.run([sys.executable, "-m", "projector", "console"], stdin=subprocess.DEVNULL,
                                  stdout=terminal, stderr=subprocess.PIPE, text=True, timeout=60,
                                  env={**os.environ, "PYTHONPATH": str(SOURCE)})

        self.assertEqual((69, "project: project console needs a terminal; run it in its own window or split\n"),
                         (done.returncode, done.stderr))

    def test_project_console_without_curses_exits_69_and_points_to_the_list_commands(self) -> None:
        stderr = io.StringIO()
        with mock.patch.dict(sys.modules, {"curses": None}), mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), redirect_stderr(stderr):
            code = cli.main(["console"])

        self.assertEqual(69, code)
        self.assertIn("`project inbox list` and `project watch list`", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
