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
import webbrowser
from contextlib import redirect_stderr
from pathlib import Path
from typing import Optional
from unittest import mock

from projector import cli, console, inbox, repo_inbox, watch
from projector.inbox import read_json, write_json

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
        # Local, with no `BROWSER`, so a run over SSH or with a browser helper opens URLs as a run at the machine
        # does.
        environ = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.scratch / "state"), "PROJECTOR_REMOTE": "0",
                                               "BROWSER": ""})
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

    def focused(self) -> list[str]:
        """What the focus is on: a control's label after its `›`, or a row's text up to its first gap. A single
        focus gives a list of one."""
        found = []
        for line in self.console.lines(100)[0]:
            if "› " in line.text:
                found.append(line.text.split("› ", 1)[1].split("   ")[0])
            elif line.selected:
                found.append(line.text.strip().split("  ")[0])
        return found

    def focus_on(self, label: str) -> None:
        """Press Down until the focus is on `label`."""
        for _ in range(30):
            if self.focused() == [label]:
                return
            self.press("down")
        self.fail(f"Down never reached {label!r}; the focus is on {self.focused()}")

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
        self.press("1", "enter")

        answers = [inbox.read_item(inbox.session_dir(name), "3").get("answer") for name in ("first", "second")]
        self.assertEqual([None, "a"], [answer and answer["letter"] for answer in answers])

    def test_an_answer_or_a_message_from_the_console_is_recorded_and_left_to_the_sessions_deliverer(self) -> None:
        self.claude("s1")
        self.ask("s1")
        self.ask("s1", "Merge it now?")

        self.open_entry("s1", "item:1")
        self.press("1", "enter")
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
            self.press("1", "enter")

        self.assertEqual([("codex-session", "1")], self.woken)


class RepositoryInbox(ConsoleCase):
    """A session registered in `acme/app`, whose repository inbox holds a peer's question, an item its agent sent,
    and the peer's reply to it."""

    SENT = "u-" + "1" * 32

    def setUp(self) -> None:
        super().setUp()
        self.claude("s1")
        write_json(self.folder("s1") / "agent.json", {"schema_version": 1, "name": "fixer", "repo": "acme/app"})
        common = {"schema_version": 1, "location": "acme/app", "sent_at": int(self.clock.now * 1000)}
        repo_inbox.mirror(self.root, "acme/app", {**common, "id": "u-" + "2" * 32, "kind": "question",
                                                  "subject": "Keep the old parser?", "detail": "It is slower.",
                                                  "choices": ["Yes", "No"], "from_name": "lexer",
                                                  "from_host": "claude"},
                          role="incoming", peer="jason", key_id="a" * 16, now=self.clock.now)
        repo_inbox.mirror(self.root, "acme/app", {**common, "id": self.SENT, "kind": "request",
                                                  "subject": "What is your progress?", "detail": "", "sent": True,
                                                  "to": "a" * 16, "from": "s1", "from_host": "claude"},
                          role="sent", peer="jason", key_id="b" * 16, now=self.clock.now)
        repo_inbox.mirror(self.root, "acme/app", {**common, "id": "u-" + "3" * 32, "answers": self.SENT,
                                                  "subject": "What is your progress?", "outcome": "reply",
                                                  "text": "Half done", "by": "user", "closes": False},
                          role="reply", peer="jason", key_id="a" * 16, now=self.clock.now)

    def test_each_entry_shows_by_its_role_and_an_option_records_the_users_answer_with_no_line_to_the_session(
            self) -> None:
        responded = []
        with mock.patch.object(repo_inbox, "sealing_key"), mock.patch.object(repo_inbox.config, "peers"), \
                mock.patch.object(repo_inbox, "respond", side_effect=lambda *args, **options: responded.append(
                    (args[5], args[6], options["by"], options["letter"])) or {}):
            self.open_entry("s1", "repo:r1")
            screen = self.screen()
            self.press("1")

        self.assertIn("- jason's agent lexer asks: Keep the old parser?", screen)
        self.assertIn("It is slower.", screen)
        self.assertIn("- you requested of jason: What is your progress?", screen)
        self.assertIn("- jason replied: Half done", screen)
        self.assertEqual([("r1", "answer", "user", "a")], responded)
        self.assertEqual([], inbox.load_items(inbox.session_dir("s1")))
        self.assertEqual([], self.woken)

    def test_a_sent_items_drawer_lists_its_replies_and_offers_close(self) -> None:
        self.open_entry("s1", "repo:r2")

        self.assertIn("jason replied: Half done", self.screen().split("you requested of jason", 1)[1])
        self.assertIn("6: Close", self.screen())

    def test_projectors_local_notice_offers_dismiss_and_no_snooze(self) -> None:
        """A local notice takes a dismissal and nothing else, so its drawer offers no Snooze. Dismissing it seals and
        relays nothing, so it needs no relay key, which a notice that the relay is off may well have none of."""
        repo_inbox.post_local(self.root, "acme/app", "Relay off: the AWS sign-in for profile acme expired.",
                              self.clock.now)
        self.open_entry("s1", "repo:r4")
        drawer = self.screen()
        with mock.patch.object(repo_inbox.relay, "command_key", return_value=(None, "no agent.key")):
            self.press("8")

        self.assertIn("Relay off: the AWS sign-in for profile acme expired.", drawer)
        self.assertIn("8: Dismiss", drawer)
        self.assertNotIn("Snooze", drawer)
        self.assertIsNone(repo_inbox.by_id(self.root, "acme/app", read_json(
            repo_inbox.folder(self.root, "acme/app") / "notice.json")["id"]))


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

    def open_pull(self) -> None:
        self.claude("s1")
        self.subscribe("s1", "acme/app#12 fix")
        self.cache("acme/app", 12)
        self.open_entry("s1", "pr:acme/app#12")

    def test_o_opens_the_pull_request_in_a_browser_and_shows_no_url_on_a_local_machine(self) -> None:
        # Catches a local console that shows the URL even though the browser opened it.
        self.open_pull()

        self.press("o")

        self.assertEqual((["https://github.com/acme/app/pull/12"], False),
                         (self.opened, "https://github.com" in self.screen()))

    def test_a_remote_console_shows_the_whole_url_under_the_actions_until_the_next_key(self) -> None:
        # Catches a remote console opening a browser on the far machine, a URL cut at the window's edge, or one
        # that stays after the next key.
        url = "https://github.com/acme/app/pull/12"
        for environ in ({"SSH_CONNECTION": "10.0.0.2 52000 10.0.0.1 22"}, {"SSH_CLIENT": "10.0.0.2 52000 22"},
                        {"SSH_TTY": "/dev/pts/3"}, {"PROJECTOR_REMOTE": "1"}):
            with self.subTest(environ=environ):
                self.opened.clear()
                self.console = self.new_console()
                self.open_pull()
                with mock.patch.dict(os.environ, {"PROJECTOR_REMOTE": "", **environ}):
                    self.press("o")
                body = self.console.lines(20)[0]
                under = [line.text for line in body[[index for index, line in enumerate(body)
                                                     if "o: Open in GitHub" in line.text][0] + 1:]]
                shown = "".join(under[:2])
                self.press("down")

                self.assertEqual(([], url, False),
                                 (self.opened, shown, url in self.screen()))

    def test_projector_remote_0_opens_locally_even_over_ssh(self) -> None:
        # Catches the SSH variables winning over `PROJECTOR_REMOTE=0`, which a person sets when the browser does
        # open where they sit.
        self.open_pull()
        with mock.patch.dict(os.environ, {"PROJECTOR_REMOTE": "0", "SSH_CONNECTION": "10.0.0.2 52000 10.0.0.1 22"}):
            self.press("o")

        self.assertEqual((["https://github.com/acme/app/pull/12"], False),
                         (self.opened, "https://github.com" in self.screen()))

    def test_a_remote_console_sends_the_url_to_the_clipboard_with_osc_52_and_a_local_one_sends_nothing(self) -> None:
        # Catches a sequence the terminal would not read, the wrong base64, a tmux passthrough that tmux would swallow,
        # a sequence sent again on the next draw, or any bytes a local console writes.
        encoded = "aHR0cHM6Ly9naXRodWIuY29tL2FjbWUvYXBwL3B1bGwvMTI="
        plain = f"\x1b]52;c;{encoded}\x07"
        for environ, expected in (({"PROJECTOR_REMOTE": "1", "TMUX": ""}, plain),
                                  ({"PROJECTOR_REMOTE": "1", "TMUX": "/tmp/tmux-501/default,1234,0"},
                                   f"\x1bPtmux;\x1b\x1b]52;c;{encoded}\x07\x1b\\"),
                                  ({"PROJECTOR_REMOTE": "0", "TMUX": ""}, "")):
            with self.subTest(environ=environ):
                self.opened.clear()
                self.console = self.new_console()
                out = io.StringIO()
                screen = console.Screen(FakeWindow(30, 100), self.console, FakeCurses, out)
                self.open_pull()
                with mock.patch.dict(os.environ, environ):
                    self.press("o")
                    screen.draw()
                    screen.draw()

                self.assertEqual((expected, bool(expected)),
                                 (out.getvalue(),
                                  "Sent to your clipboard where your terminal allows it" in self.screen()))

    def test_a_remote_console_with_browser_set_opens_through_it_and_shows_the_url_only_when_that_fails(self) -> None:
        # Catches a remote console that ignores `BROWSER`, which a person points at a helper that opens the URL on
        # their own machine, or one that leaves them nothing to follow when that helper fails.
        url = "https://github.com/acme/app/pull/12"
        remote = {"PROJECTOR_REMOTE": "1", "BROWSER": "open-on-my-laptop", "TMUX": ""}
        for opens, shown in ((mock.Mock(return_value=True), False), (mock.Mock(return_value=False), True),
                             (mock.Mock(side_effect=webbrowser.Error("tunnel down")), True)):
            with self.subTest(opens=opens):
                self.console = console.Console(self.root, clock=self.clock, gh=self.gh, open_url=opens)
                out = io.StringIO()
                screen = console.Screen(FakeWindow(30, 100), self.console, FakeCurses, out)
                self.open_pull()
                with mock.patch.dict(os.environ, remote):
                    self.press("o")
                    screen.draw()
                    screen.draw()

                opens.assert_called_once_with(url)
                sent = "\x1b]52;c;aHR0cHM6Ly9naXRodWIuY29tL2FjbWUvYXBwL3B1bGwvMTI=\x07"
                self.assertEqual((shown, sent if shown else ""), (url in self.screen(), out.getvalue()))

    def test_a_local_open_that_fails_shows_the_url(self) -> None:
        # Catches a failed open, which `webbrowser.open` reports by returning False or raising, leaving the person
        # with nothing to follow.
        for fails in (lambda url: False, mock.Mock(side_effect=webbrowser.Error("no browser"))):
            with self.subTest(fails=fails):
                self.console = console.Console(self.root, clock=self.clock, gh=self.gh, open_url=fails)
                self.open_pull()
                self.press("o")

                self.assertIn("https://github.com/acme/app/pull/12", self.screen())


class Keys(ConsoleCase):
    def test_left_closes_the_open_drawer_then_returns_home_but_moves_the_cursor_in_a_text_field(self) -> None:
        # Catches Left leaving the panel with a drawer open, so you cannot close a question without losing the
        # session, or Left in a text field doing either instead of moving the cursor.
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
        closed = (self.console.view, self.console.drawer, self.focused())
        self.press("left")

        self.assertEqual((("panel", "item:1"), ("panel", None, ["- Decline the finding?"]), ("home", None), "abc"),
                         (in_field, closed, (self.console.view, self.console.drawer),
                          inbox.read_item(inbox.session_dir("s1"), "1")["messages"][0]["text"]))

    def test_q_is_an_ordinary_key_and_ctrl_c_quits_at_once_only_from_the_home_list(self) -> None:
        # Catches `q` still quitting, which the person hits by mistake, or a single Ctrl-C quitting a panel.
        self.claude("s1")
        self.ask("s1")
        self.open_entry("s1", "item:1")

        self.press("q")
        after_q = self.console.quit
        self.press("ctrl-c")
        after_one = (self.console.quit, self.console.footer()[-1].text)
        self.press("left", "left", "ctrl-c")

        self.assertEqual((False, (False, "Press Ctrl-C again to exit"), True),
                         (after_q, after_one, self.console.quit))

    def test_a_second_ctrl_c_quits_a_panel_unless_another_key_or_the_wait_came_between(self) -> None:
        # Catches a panel that quits on one Ctrl-C, never quits, or still counts a Ctrl-C that a key or the hint's
        # timing out has cleared.
        self.claude("s1")
        self.ask("s1")
        for between in ((), ("down",), "wait"):
            with self.subTest(between=between):
                self.console = self.new_console()
                self.open_entry("s1", "item:1")
                self.press("ctrl-c")
                if between == "wait":
                    self.clock.now += console.EXIT_WINDOW + 1
                else:
                    self.press(*between)
                self.press("ctrl-c")
                self.assertEqual(between == (), self.console.quit)

    def test_ctrl_c_in_a_text_field_cancels_the_field_without_counting_toward_quitting(self) -> None:
        # Catches Ctrl-C typed into the field, or counted as the first of the two presses that quit, so two
        # presses meant to clear a field quit the console.
        self.claude("s1")
        self.ask("s1")
        self.open_entry("s1", "item:1")
        self.press("7")
        self.type("half a thought")

        self.press("ctrl-c")
        cancelled = (self.console.field, self.console.quit)
        self.press("ctrl-c")
        armed = self.console.quit
        self.press("ctrl-c")

        self.assertEqual(((None, False), False, True), (cancelled, armed, self.console.quit))

    def test_escape_sequences_the_terminal_sends_for_the_arrows_are_read_as_the_arrows(self) -> None:
        # Catches the console reading `ESC [ B`, the cursor key a terminal sends outside application mode, as Esc,
        # `[`, and `B`, which closed the drawer instead of moving down.
        cases = ((["\x1b", "[", "B"], ["down"]), (["\x1b", "O", "B"], ["down"]), (["\x1b", "[", "A"], ["up"]),
                 (["\x1b", "[", "D"], ["left"]), (["\x1b", "[", "6", "~"], ["pagedown"]),
                 (["\x1b", "[", "1", ";", "5", "B"], ["down"]), (["\x1b"], ["esc"]),
                 (["\x1b", "\x1b"], ["esc", "esc"]), (["\x03"], ["ctrl-c"]))
        for pressed, names in cases:
            with self.subTest(pressed=pressed):
                window = FakeWindow(12)
                window.pressed = list(pressed)
                screen = console.Screen(window, self.console, FakeCurses)
                self.assertEqual(names, [screen.read_key() for _ in names])


class Options(ConsoleCase):
    def answer(self, ref: str = "1", session: str = "s1") -> dict:
        return inbox.read_item(inbox.session_dir(session), ref).get("answer")

    def test_enter_on_the_focused_option_opens_its_note_field_and_records_that_option(self) -> None:
        # Catches the focus leaving an open question's options for the wrong place, or Enter recording an option
        # other than the focused one.
        for session, keys in (("s1", ("up", "down", "down")), ("s2", ("down", "down", "up"))):
            with self.subTest(keys=keys):
                self.console = self.new_console()
                self.claude(session)
                self.ask(session)
                self.open_entry(session, "item:1")

                self.press(*keys, "enter")
                field = self.console.field
                self.press("enter")

                self.assertEqual(("note", "item:1", "b"),
                                 (field and field.purpose, self.console.drawer, self.answer("1", session)["letter"]))
                self.assertNotIn("note", self.answer("1", session))

    def test_a_revision_while_the_note_field_is_open_records_nothing_and_keeps_the_note(self) -> None:
        # Catches a choice recorded by its letter or words alone, so a revision the drawer had not yet shown makes
        # Enter record an option the person never read, or a refusal that drops the note they typed.
        for kind in ("question", "request"):
            with self.subTest(kind=kind):
                self.console = self.new_console()
                self.claude(kind)
                if kind == "question":
                    posted = inbox.ask(kind, "Build it?", "Build locally", ["Wait"])
                    revise = {"recommend": "Deploy production"}
                else:
                    posted = inbox.request(kind, "Check the build", options=["Built locally", "Not yet"])
                    revise = {"options": ["Deployed production", "Not yet"]}
                ref = posted["ref"]
                self.open_entry(kind, f"item:{ref}")
                self.press("1")
                self.type("keep the cap")
                inbox.revise(kind, ref, **revise)
                self.press("enter")
                refused = (inbox.read_item(inbox.session_dir(kind), ref).get("answer"), self.console.field,
                           self.console.status)
                self.press("1")
                kept = self.console.field.text if self.console.field else None
                self.press("enter")

                self.assertEqual((None, None, f"{ref} changed while you were choosing; read it and choose again"),
                                 refused)
                self.assertEqual("keep the cap", kept)
                answered = inbox.read_item(inbox.session_dir(kind), ref)["answer"]
                self.assertEqual(("Deploy production" if kind == "question" else "Deployed production",
                                  "keep the cap"), (answered["text"], answered["note"]))

    def test_a_kept_note_comes_back_only_on_its_own_sessions_item_and_not_after_leaving_the_panel(self) -> None:
        # Catches a kept note matched by item number alone, which repeats across sessions, so it fills another
        # session's field with words meant for a different agent.
        for session in ("first", "second"):
            self.claude(session)
            inbox.ask(session, "Build it?", "Build locally", ["Wait"])
        self.open_entry("first", "item:1")
        self.press("1")
        self.type("for the first agent")
        inbox.revise("first", "1", recommend="Deploy production")
        self.press("enter")
        kept = self.console.kept_note
        self.press("left", "left")
        self.open_entry("second", "item:1")
        self.press("1")
        other = self.console.field.text if self.console.field else None
        self.press("esc", "left", "left")
        self.open_entry("first", "item:1")
        self.press("1")

        self.assertEqual((("first", "1", "for the first agent"), "", ""),
                         (kept, other, self.console.field.text if self.console.field else None))

    def test_a_question_offers_no_typed_answer_and_t_records_nothing(self) -> None:
        # Catches `type an answer` left among a question's options or in the focus ring, or `t` still opening the
        # typed answer's field, when Discuss is how the person writes their own words.
        self.claude("s1")
        self.ask("s1")
        self.open_entry("s1", "item:1")

        ring = [control for key, control in self.console.ring() if key == "item:1" and control is not None]
        drawn = self.screen()
        self.press("t")

        self.assertEqual((["1", "2", "6", "7", "8", "9"], False, None, None),
                         (ring, "type an answer" in drawn, self.console.field, self.answer()))

    def test_an_option_opens_a_note_field_under_itself_and_records_only_when_the_field_is_sent(self) -> None:
        # Catches a number that records at once, leaving no room for a note, a note field drawn away from its
        # option, Esc recording the option anyway, or a typed note that never reaches the agent's line.
        self.claude("s1")
        self.ask("s1")
        self.open_entry("s1", "item:1")

        self.press("2")
        self.type("never sent")
        lines = self.screen().splitlines()
        self.press("esc")
        after_esc = (self.answer(), self.console.field, self.console.drawer)
        self.press("1")
        self.type("keep the cap")
        self.press("enter")

        self.assertIn(("› 2: Fix it", "Note (optional): never sent"),
                      [(above.strip(), line.strip()) for above, line in zip(lines, lines[1:])])
        self.assertEqual((None, None, "item:1"), after_esc)
        self.assertEqual("ANSWER 1.a Decline the finding? → Decline it — note: keep the cap",
                         inbox.message(inbox.read_item(inbox.session_dir("s1"), "1")))

    def test_a_requests_highlight_starts_on_its_first_choice_and_records_done_with_the_choice_and_note(self) -> None:
        # Catches the highlight starting on Start, which Enter would press, or a request's choice and note
        # recorded as anything but `done`.
        self.claude("s1")
        inbox.request("s1", "Test the gutters", start="Install it", options=["Looks good", "Some feedback"])
        self.open_entry("s1", "item:1")

        self.press("enter")
        self.type("works on the phone")
        self.press("enter")

        item = inbox.read_item(inbox.session_dir("s1"), "1")
        self.assertEqual((None, "DONE 1 Test the gutters → Looks good — note: works on the phone"),
                         (item.get("started"), inbox.message(item)))

    def test_a_note_field_closes_when_its_item_is_resolved_elsewhere(self) -> None:
        # Catches a note field left open after its option is gone from the drawer, where it is no longer drawn but
        # still takes every key, so Ctrl-C and Esc seem to do nothing.
        for session, resolve in (("answered", lambda: inbox.answer("answered", "1", "b")),
                                 ("withdrawn", lambda: inbox.dismiss("withdrawn", "1", withdrawn=True))):
            with self.subTest(session):
                self.console = self.new_console()
                self.claude(session)
                self.ask(session)
                self.open_entry(session, "item:1")
                self.press("1")
                resolve()
                self.clock.now += console.INBOX_EVERY

                self.console.tick()
                self.press("ctrl-c", "ctrl-c")

                self.assertEqual((None, True), (self.console.field, self.console.quit))



class Ring(ConsoleCase):
    def setUp(self) -> None:
        super().setUp()
        self.claude("s1")
        self.subscribe("s1", "acme/app#12 fix")
        self.cache("acme/app", 12)
        self.ask("s1")

    def test_the_arrows_move_one_focus_through_the_rows_and_the_open_drawers_options_and_actions(self) -> None:
        # Catches arrows that stop at an open question's last option, skip its actions or the conversation's
        # heading, wrap past either end, or close the drawer when the focus leaves it.
        self.open_entry("s1", "item:1")
        seen = [self.focused()]
        for name in ("up", "up", "up", *["down"] * 9):
            self.press(name)
            seen.append(self.focused())

        self.assertEqual([["1: Decline it (recommended)"], ["- Decline the finding?"], ["#12"], ["#12"],
                          ["- Decline the finding?"], ["1: Decline it (recommended)"], ["2: Fix it"],
                          ["6: I don't understand"], ["7: Discuss"], ["8: Dismiss"],
                          ["9: Snooze"], ["▸ Conversation"], ["▸ Conversation"]], seen)
        self.assertEqual("item:1", self.console.drawer)

    def test_a_pull_request_drawers_actions_follow_its_row_and_come_before_the_next_row(self) -> None:
        # Catches a drawer without options keeping its actions out of the ring.
        self.open_entry("s1", "pr:acme/app#12")
        seen = [self.focused()]
        for _ in range(4):
            self.press("down")
            seen.append(self.focused())

        self.assertEqual([["#12"], ["7: Discuss"], ["8: Dismiss"], ["o: Open in GitHub"], ["- Decline the finding?"]],
                         seen)
        self.assertEqual("pr:acme/app#12", self.console.drawer)

    def test_enter_on_another_row_closes_the_open_drawer_and_opens_that_rows(self) -> None:
        # Catches Enter on another row acting on the open drawer, or opening a second drawer beside it.
        self.open_entry("s1", "item:1")
        self.press("up", "up")

        self.press("enter")

        self.assertEqual(("pr:acme/app#12", ["#12"]), (self.console.drawer, self.focused()))
        self.assertNotIn("1: Decline it", self.screen())

    def test_enter_on_the_open_drawers_own_row_closes_it(self) -> None:
        # Catches Enter on the open row reopening the drawer, or leaving the focus on a control nobody can see.
        self.open_entry("s1", "item:1")
        self.press("up")

        self.press("enter")

        self.assertEqual((None, ["- Decline the finding?"]), (self.console.drawer, self.focused()))

    def test_enter_on_discuss_opens_its_field_and_on_dismiss_dismisses(self) -> None:
        # Catches Enter on an action doing nothing, or pressing another control than the focused one.
        self.open_entry("s1", "item:1")
        self.focus_on("7: Discuss")
        self.press("enter")
        discussing = self.console.field and self.console.field.purpose
        self.press("esc")
        self.focus_on("8: Dismiss")

        self.press("enter")

        self.assertEqual(("discuss", True),
                         (discussing, inbox.read_item(inbox.session_dir("s1"), "1")["answer"].get("dismissed")))

    def test_enter_on_snooze_puts_the_lengths_in_the_ring_and_enter_on_one_snoozes(self) -> None:
        # Catches the lengths left out of the ring, so Snooze cannot be finished from the arrows.
        self.open_entry("s1", "item:1")
        self.focus_on("9: Snooze")
        self.press("enter")
        offered = self.focused()
        self.press("down")

        self.press("enter")

        until = inbox.read_item(inbox.session_dir("s1"), "1")["snoozedUntil"]
        self.assertEqual((["1: 15 min"], 60), (offered, round((until - self.console.now_ms()) / 60_000)))

    def test_enter_on_a_pull_requests_dismiss_still_asks_before_dropping_it(self) -> None:
        # Catches Enter on Dismiss dropping the pull request without its confirmation.
        self.open_entry("s1", "pr:acme/app#12")
        self.focus_on("8: Dismiss")

        self.press("enter")
        asked = (self.focused(), watch.read_lines(self.folder("s1")))
        self.press("enter")

        self.assertEqual(((["8: Yes, dismiss it"], ["acme/app#12 fix"]), ["acme/app#12 fix", "!acme/app#12 fix"]),
                         (asked, watch.read_lines(self.folder("s1"))))


class FakeCurses:
    """The parts of `curses` that `console.Screen` draws with, for a terminal with no colors."""

    A_DIM, A_BOLD, A_REVERSE = 1, 2, 4
    KEY_NPAGE, KEY_PPAGE, KEY_HOME, KEY_END, KEY_DOWN = 338, 339, 262, 360, 258

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
        self.drawn: list[tuple[str, int]] = []
        self.pressed: list[object] = []

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def erase(self) -> None:
        self.rows = {}
        self.drawn = []

    def addstr(self, y: int, x: int, text: str, attributes: int) -> None:
        self.rows[y] = self.rows.get(y, "").ljust(x) + text
        self.drawn.append((text, attributes))

    def get_wch(self) -> object:
        if not self.pressed:
            raise FakeCurses.error("no input")
        return self.pressed.pop(0)

    def timeout(self, milliseconds: int) -> None:
        pass

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

    def test_moving_the_highlight_scrolls_it_and_its_note_field_into_a_tall_drawers_view(self) -> None:
        # Catches a highlight that moves below the screen, where you cannot see what Enter would pick, or a note
        # field drawn at the bottom of the screen instead of under its option.
        self.tall_question()
        opened = self.draw()
        self.press_terminal(FakeCurses.KEY_DOWN)
        moved = self.draw()
        self.press_terminal("\n")
        rows = self.draw().splitlines()

        under = ["2: Fix it" in above and "Note (optional):" in row for above, row in zip(rows, rows[1:])]
        self.assertEqual((False, True, True), ("2: Fix it" in opened, "› 2: Fix it" in moved, any(under)))

    def test_the_focus_leaves_a_tall_notice_drawer_open_and_scrolls_to_the_next_row(self) -> None:
        # Catches the arrows closing the drawer they leave, or a focused row left below the screen.
        self.claude("s1")
        self.ask("s1", "Merge it now?")
        notice = inbox.tell("s1", "Force-pushed the branch",
                            detail="\n".join(f"Detail line {number}." for number in range(1, 41)))
        self.open_entry("s1", f"item:{notice['ref']}")
        self.draw()
        self.press_terminal(FakeCurses.KEY_NPAGE)
        # Both items can share a millisecond, so either may list first.
        step = "down" if self.console.keys()[0] == "item:2" else "up"

        # Down passes the notice's four actions to reach the question, and Up reaches it and stays there.
        for _ in range(5):
            self.press(step)

        self.assertEqual((f"item:{notice['ref']}", "item:1"), (self.console.drawer, self.console.selected_key()))
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

    def test_the_conversation_heading_is_the_rings_last_place_and_enter_on_it_shows_or_hides_it(self) -> None:
        # Catches a ring that stops before the heading, Enter there opening nothing or a drawer instead of toggling
        # the conversation as `v` does, or a shown conversation that keeps the focus or scrolls away from its newest
        # line when the focus comes back to it.
        self.claude("s1")
        self.ask("s1")
        self.say(self.transcript("s1"), *(("agent", f"Old line {number}.") for number in range(1, 31)))
        self.open_entry("s1", "item:1")
        self.focus_on("▸ Conversation")
        reached = (self.console.drawer, self.console.expanded)
        self.press("enter")
        self.console.tick()
        opened = self.draw()
        self.press_terminal(FakeCurses.KEY_PPAGE)
        back = self.draw()
        self.press("up")
        left = (self.focused(), self.draw())
        self.press("down")
        returned = self.draw()
        self.press("enter")

        self.assertEqual((("item:1", set()), True, None, True, False, ["- Decline the finding?"], True, True,
                          set(), "▸ Conversation"),
                         (reached, "agent: Old line 30." in opened, self.console.drawer, "Old line 20." in back,
                          "Old line 30." in back, left[0], "- Decline the finding?" in left[1],
                          "agent: Old line 30." in returned, self.console.expanded,
                          self.screen().splitlines()[-1][:14]))

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
class ColorCurses(FakeCurses):
    """A terminal with `colors` colors, which keeps the foreground `init_pair` gave each pair."""

    COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW, COLOR_BLUE, COLOR_MAGENTA, COLOR_CYAN, COLOR_WHITE = range(8)

    def __init__(self, colors: int) -> None:
        self.COLORS = colors
        self.pairs: dict[int, int] = {}

    def has_colors(self) -> bool:
        return True

    def start_color(self) -> None:
        pass

    def use_default_colors(self) -> None:
        pass

    def init_pair(self, pair: int, foreground: int, background: int) -> None:
        self.pairs[pair] = foreground

    def color_pair(self, pair: int) -> int:
        return pair << 8


class Colors(ConsoleCase):
    def foregrounds(self, curses: ColorCurses, environ: dict[str, str]) -> dict[str, object]:
        """The foreground each piece of text on the screen was drawn in, or None when it has no color."""
        window = FakeWindow(30, 120)
        with mock.patch.dict(os.environ, environ):
            screen = console.Screen(window, self.console, curses)
        screen.draw()
        return {text.strip(): curses.pairs.get(attributes >> 8) for text, attributes in window.drawn}

    def test_a_question_line_and_a_conflict_draw_in_the_panels_permission_and_error_colors(self) -> None:
        # Catches the console drawing in colors of its own rather than the panel's: a question in cyan, as it did,
        # a theme color off by a cube step, or color drawn under NO_COLOR.
        self.claude("s1")
        self.subscribe("s1", "acme/app#12 fix")
        node = pull_node(12)
        node["repository"]["pullRequest"]["mergeable"] = "CONFLICTING"
        path = watch.pull_cache(self.root, "acme/app", 12)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, node)
        self.ask("s1")
        self.console.tick()
        self.console.open_panel("s1")
        dark, light = console.THEMES["dark"], console.THEMES["light"]
        cases = ((256, {"NO_COLOR": "", "COLORFGBG": ""}, console.xterm_color(dark["permission"]),
                  console.xterm_color(dark["error"])),
                 (256, {"NO_COLOR": "", "COLORFGBG": "0;15"}, console.xterm_color(light["permission"]),
                  console.xterm_color(light["error"])),
                 (8, {"NO_COLOR": "", "COLORFGBG": ""}, ColorCurses.COLOR_BLUE, ColorCurses.COLOR_RED),
                 (256, {"NO_COLOR": "1"}, None, None))
        for colors, environ, question, conflict in cases:
            with self.subTest(colors=colors, environ=environ):
                drawn = self.foregrounds(ColorCurses(colors), environ)
                self.assertEqual((question, conflict), (drawn["- Decline the finding?"], drawn["conflict"]))
        self.assertEqual((67, 239, 147), (console.xterm_color((95, 135, 175)), console.xterm_color((80, 80, 80)),
                                          console.xterm_color((177, 185, 249))))


class Terminal(unittest.TestCase):
    """`project console` run in a pseudo-terminal, with the bytes a terminal sends for each key."""

    def prepare(self) -> None:
        """A state directory of its own with one live Claude Code session, `S`, asking one question."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.state = scratch.name
        self.env = {**os.environ, "XDG_STATE_HOME": self.state, "PYTHONPATH": str(SOURCE), "TERM": "xterm-256color"}
        self.env.pop("PROJECTOR_SESSION", None)
        for args in (["inbox", "ask", "Decline the finding?", "--recommend", "Decline it", "--option", "Fix it",
                      "--session", "S"], ["watch", "renew", "--deliverer", "mod", "--session", "S"]):
            subprocess.run([sys.executable, "-m", "projector", *args], env=self.env, check=True, capture_output=True,
                           timeout=60)

    def start(self) -> None:
        import fcntl
        import pty
        import struct
        import termios

        self.code: Optional[int] = None
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(sys.executable, [sys.executable, "-m", "projector", "console"], self.env)
        self.addCleanup(os.close, self.fd)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.shown = bytearray()
        # Not `live session`, which `no live sessions` holds before the first refresh finds `S`.
        self.read_for(10, until=b"1 live session")

    def read_for(self, seconds: float, until: bytes = b"") -> None:
        import select

        end = time.time() + seconds
        while time.time() < end and not (until and until in self.shown):
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    self.shown.extend(os.read(self.fd, 65536))
                except OSError:
                    return

    def send(self, *keys: bytes) -> None:
        for one in keys:
            try:
                os.write(self.fd, one)
            except OSError:  # the console already exited
                return
            self.read_for(0.4)

    def exited(self, seconds: float, *, kill: bool = True) -> Optional[int]:
        """The console's exit code once it exits within `seconds`, or None, killing it unless `kill` is false, when
        it still runs."""
        end = time.time() + seconds
        while time.time() < end and self.code is None:
            self.read_for(0.1)
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                self.code = os.waitstatus_to_exitcode(status)
        if self.code is not None:
            return self.code
        if kill:
            os.kill(self.pid, 9)
            os.waitpid(self.pid, 0)
        return None

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_either_down_arrow_encoding_moves_to_the_second_option_and_one_ctrl_c_quits_from_home(self) -> None:
        # Catches the arrows doing nothing in a terminal that sends `ESC [ B` rather than `ESC O B`, as a test by
        # hand found, or a Ctrl-C from the home list that does not quit or leaves a traceback.
        for down in (b"\x1b[B", b"\x1bOB"):
            with self.subTest(down=down):
                self.prepare()
                self.start()
                self.send(b"\r", b"\r", down, b"\r", b"\r")
                # Esc twice leaves any open field and the drawer, and then the panel, so Ctrl-C quits at once.
                self.send(b"\x1b", b"\x1b", b"\x03")
                code = self.exited(10)
                item = json.loads((Path(self.state) / "projector" / "inbox" / "S" / "1.json").read_text())
                self.assertEqual(("b", 0), ((item.get("answer") or {}).get("letter"), code))
                self.assertNotIn(b"Traceback", bytes(self.shown))

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_a_panel_quits_on_the_second_ctrl_c_and_not_the_first(self) -> None:
        # Catches the terminal's Ctrl-C reaching curses as an interrupt that quits at once from a panel.
        self.prepare()
        self.start()
        self.send(b"\r", b"\x03")
        first = self.exited(1, kill=False)
        hinted = b"Press Ctrl-C again to exit" in self.shown
        self.send(b"\x03")

        self.assertEqual((None, True, 0), (first, hinted, self.exited(10)))


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
