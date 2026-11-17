"""`project console`'s model, driven without a terminal against a temporary state directory.

Each test points `XDG_STATE_HOME` at its own directory, writes the files a live session leaves there, and drives
`console.Console` with key names and an injected clock, `gh`, browser, and Codex wake.
"""

from __future__ import annotations

import io
import json
import os
import shutil
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
    def test_a_session_with_more_open_questions_lists_first(self) -> None:
        for session in ("one", "two"):
            self.claude(session)
        self.ask("one", "Retry the mirrors?")
        self.ask("two")
        self.ask("two", "Merge it now?")

        self.console.tick()

        self.assertEqual(["two", "one"], self.home_names())

    def test_an_items_drawer_lists_the_messages_you_sent_about_it(self) -> None:
        self.claude("s1")
        item = self.ask("s1")
        inbox.discuss("s1", item["ref"], "Which mirrors?")

        self.open_entry("s1", "item:1")

        self.assertIn("you: Which mirrors?", self.screen())


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
                          discussed["messages"][0].get("deliveredAt"), discussed["messages"][0]["text"]))

    def test_a_codex_sessions_answer_wakes_its_delivery_and_a_claude_sessions_does_not(self) -> None:
        self.codex("codex-session")
        self.claude("claude-session")
        for session in ("codex-session", "claude-session"):
            self.ask(session)
        for session in ("codex-session", "claude-session"):
            self.open_entry(session, "item:1")
            self.press("1")

        self.assertEqual([("codex-session", "1")], self.woken)


class Messages(ConsoleCase):
    def test_seven_with_no_drawer_open_sends_a_message_about_an_empty_session(self) -> None:
        self.claude("idle")
        self.console.tick()
        self.console.open_panel("idle")

        self.press("7")
        self.type("hello")
        self.press("enter")

        item = inbox.read_item(inbox.session_dir("idle"), "1")
        self.assertEqual(("message", None, "DISCUSS 1 About this session → hello"),
                         (item["kind"], item["pr"], inbox.message(item)))

    def test_a_message_reads_sending_like_a_notice_offers_no_unclear_and_leaves_once_delivered(self) -> None:
        self.claude("s1")
        self.console.tick()
        self.console.open_panel("s1")
        self.press("7")
        self.type("hello")
        self.press("enter")
        sending = self.screen()
        self.press("enter")
        drawer = self.screen()
        token = inbox.claim("s1", "1")["claim"]["token"]
        inbox.settle("s1", "1", token, True)
        self.console.reload()
        delivered = self.screen()
        self.clock.now += 31
        self.console.reload()

        self.assertEqual((True, True, False, True, False),
                         ("- About this session  sending" in sending, "7: Discuss" in drawer,
                          "6: I don't understand" in drawer, "Delivered ✓" in delivered,
                          "About this session" in self.screen()))

    def test_discussing_a_pull_request_two_sessions_hold_sends_the_message_to_the_viewed_session(self) -> None:
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
                         tuple([(item["pr"], item["messages"][0]["text"])
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
                          inbox.read_item(inbox.session_dir("s1"), "1")["messages"][0]["text"]))


class FakeCurses:
    """The parts of `curses` that `console.Screen` draws with, for a terminal with no colors."""

    A_DIM, A_BOLD, A_REVERSE = 1, 2, 4
    KEY_NPAGE, KEY_PPAGE, KEY_HOME, KEY_END = 338, 339, 262, 360

    class error(Exception):
        pass

    @staticmethod
    def has_colors() -> bool:
        return False

    @staticmethod
    def curs_set(visibility: int) -> None:
        pass


class FakeWindow:
    """A terminal `height` rows tall that keeps the text each row was last drawn with."""

    def __init__(self, height: int, width: int = 80) -> None:
        self.height, self.width = height, width
        self.rows: dict[int, str] = {}

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def erase(self) -> None:
        self.rows = {}

    def addstr(self, y: int, x: int, text: str, attributes: int) -> None:
        self.rows[y] = self.rows.get(y, "").ljust(x) + text

    def move(self, y: int, x: int) -> None:
        pass

    def refresh(self) -> None:
        pass

    def text(self) -> str:
        return "\n".join(self.rows.get(y, "") for y in range(self.height))


class TerminalCase(ConsoleCase):
    """Draws the console with the real `Screen` into a 12-row fake window. It holds no tests of its own, so each
    class built on it runs only the tests it defines."""

    def setUp(self) -> None:
        super().setUp()
        self.window = FakeWindow(12)
        self.terminal = console.Screen(self.window, self.console, FakeCurses)

    def draw(self) -> str:
        self.terminal.draw()
        return self.window.text()

    def press_terminal(self, *pressed: object) -> None:
        """Press keys as `get_wch` returns them, so the curses key names are tested too."""
        for one in pressed:
            name = console.key_name(one, FakeCurses)
            if name is not None:
                self.console.key(name)
            self.terminal.draw()


class Scrolling(TerminalCase):
    def tall_question(self) -> None:
        self.claude("s1")
        item = inbox.ask("s1", "Decline the finding?", "Decline it", ["Fix it"],
                         detail="\n".join(f"Detail line {number}." for number in range(1, 41)))
        inbox.discuss("s1", item["ref"], "The newest message.")
        self.open_entry("s1", f"item:{item['ref']}")

    def test_page_down_reaches_the_last_message_and_controls_of_a_drawer_taller_than_the_screen(self) -> None:
        self.tall_question()
        opened = self.draw()
        self.press_terminal(FakeCurses.KEY_NPAGE, FakeCurses.KEY_NPAGE, FakeCurses.KEY_NPAGE, FakeCurses.KEY_NPAGE,
                            FakeCurses.KEY_NPAGE)
        scrolled = self.draw()
        self.console.tick()

        self.assertEqual((True, False, False, True, True, True),
                         ("- Decline the finding?" in opened, "you: The newest message." in opened,
                          "8: Dismiss" in opened, "you: The newest message." in scrolled,
                          "1: Decline it (recommended)" in scrolled, "8: Dismiss" in self.draw()))

    def test_end_and_home_jump_to_a_tall_drawers_controls_and_back_to_its_row(self) -> None:
        self.tall_question()
        self.draw()

        self.press_terminal(FakeCurses.KEY_END)
        at_end = self.draw()
        self.press("g")
        self.terminal.draw()
        self.press(" ", "b")
        at_top = self.draw()

        self.assertEqual((True, False, True, False),
                         ("8: Dismiss" in at_end, "- Decline the finding?" in at_end,
                          "- Decline the finding?" in at_top, "8: Dismiss" in at_top))

    def test_the_cursor_still_closes_a_tall_drawer_and_moves_to_the_next_row(self) -> None:
        self.claude("s1")
        self.ask("s1", "Merge it now?")
        self.tall_question()
        self.draw()
        self.press_terminal(FakeCurses.KEY_NPAGE)
        # Both items can share a millisecond, so either may list first.
        step = "down" if self.console.keys()[0] == "item:2" else "up"

        self.press(step)

        self.assertEqual((None, "item:1"), (self.console.drawer, self.console.selected_key()))
        self.assertIn("- Merge it now?", self.draw())


def claude_entry(who: str, text: str) -> str:
    if who == "you":
        return json.dumps({"type": "user", "origin": {"kind": "human"},
                           "message": {"role": "user", "content": text}}) + "\n"
    return json.dumps({"type": "assistant",
                       "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}) + "\n"


class Conversation(TerminalCase):
    """The panel's conversation section, read from a Claude Code transcript under a temporary `CLAUDE_CONFIG_DIR`
    and from a Codex rollout that `codex.json` names."""

    def setUp(self) -> None:
        super().setUp()
        environ = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.scratch / "claude"),
                                               "HOME": str(self.scratch / "home")})
        environ.start()
        self.addCleanup(environ.stop)

    def transcript(self, session: str) -> Path:
        path = self.scratch / "claude" / "projects" / "-tmp-made-up" / f"{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def say(self, path: Path, *said: tuple[str, str]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("".join(claude_entry(who, text) for who, text in said))

    def test_the_conversation_is_collapsed_by_default_and_v_shows_it_for_that_session_only(self) -> None:
        for session in ("s1", "s2"):
            self.claude(session)
            self.say(self.transcript(session), ("you", f"Hello from {session}."), ("agent", f"Hi, {session}."))
        self.console.tick()
        self.console.open_panel("s1")
        self.console.tick()
        collapsed = self.screen()

        self.press("v")
        self.console.tick()
        shown = self.screen()
        self.press("left")
        self.console.open_panel("s2")
        self.console.tick()
        other = self.screen()
        self.press("left")
        self.console.open_panel("s1")
        self.console.tick()

        self.assertEqual(("▸ Conversation", False, "▾ Conversation", True, True, "▸ Conversation", False, True),
                         (collapsed.splitlines()[-1][:14], "agent: Hi, s1." in collapsed,
                          shown.splitlines()[-3][:14], "you: Hello from s1." in shown, "agent: Hi, s1." in shown,
                          other.splitlines()[-1][:14], "Hi, s2." in other, "agent: Hi, s1." in self.screen()))

    def test_a_collapsed_conversation_is_never_read(self) -> None:
        self.claude("s1")
        self.say(self.transcript("s1"), ("you", "Hello."))
        self.console.tick()
        self.console.open_panel("s1")
        for _ in range(3):
            self.clock.now += 1
            self.console.tick()

        self.assertEqual(({}, {}), (self.console.readers, self.console.said))

    def test_an_open_conversation_starts_at_its_newest_line_and_follows_it_until_you_scroll_back(self) -> None:
        self.claude("s1")
        self.ask("s1")
        path = self.transcript("s1")
        self.say(path, *(("agent", f"Old line {number}.") for number in range(1, 31)))
        self.console.tick()
        self.console.open_panel("s1")
        self.press("v")
        self.console.tick()
        opened = self.draw()
        self.say(path, ("you", "A newer question."), ("agent", "A newer answer."))
        self.clock.now += 1
        self.console.tick()
        followed = self.draw()
        self.press_terminal(FakeCurses.KEY_PPAGE)
        back = self.draw()
        self.say(path, ("agent", "The newest answer."))
        self.clock.now += 1
        self.console.tick()
        stayed = self.draw()
        self.press_terminal("G")
        self.say(path, ("agent", "One more answer."))
        self.clock.now += 1
        self.console.tick()

        self.assertEqual((True, False, True, True, False, False, True, True),
                         ("agent: Old line 30." in opened, "Old line 1." in opened,
                          "agent: A newer answer." in followed, "Old line 20." in back,
                          "agent: A newer answer." in back, "The newest answer." in stayed, stayed == back,
                          "agent: One more answer." in self.draw()))

    def test_a_codex_sessions_conversation_comes_from_the_rollout_codex_json_names(self) -> None:
        rollout = self.scratch / "rollout.jsonl"
        shutil.copy(Path(__file__).resolve().parent / "fixtures" / "conversation" / "codex.jsonl", rollout)
        self.codex("019a2b3c-0000-7000-8000-00000000c0de", transcript_path=str(rollout))
        self.console.tick()
        self.console.open_panel("019a2b3c-0000-7000-8000-00000000c0de")
        self.press("v")
        self.console.tick()

        self.assertIn("agent: Added farewell, and the README's usage section shows it.", self.screen())

    def test_a_session_with_no_transcript_says_so(self) -> None:
        self.claude("s1")
        self.console.tick()
        self.console.open_panel("s1")
        self.press("v")
        self.console.tick()

        self.assertIn("No transcript found for this session.", self.screen())


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
