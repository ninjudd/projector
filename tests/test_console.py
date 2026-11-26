"""`project console`'s model, driven without a terminal against a temporary state directory.

Each test points `XDG_STATE_HOME` and `HOME` at its own directories, writes the files a live session leaves there,
and drives `console.Console` with key names and an injected clock, `gh`, browser, and Codex wake.
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
import webbrowser
from contextlib import redirect_stderr
from pathlib import Path
from typing import Optional
from unittest import mock

from projector import cli, config, console, inbox, relay, repo_inbox, session_titles, watch
from projector.inbox import read_json, write_json

SOURCE = Path(cli.__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def pull_node(number: int, *, author: str = "operator", threads: int = 0, **fields) -> dict:
    return {"repository": {"pullRequest": {
        "title": f"Pull request {number}", "state": "OPEN", "author": {"login": author}, "headRefName": "feature",
        "reviewThreads": {"nodes": [{"isResolved": False} for _ in range(threads)]}, **fields}}}


class ConsoleCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        # A home of its own, so no test reads the `~/.projector.toml` of whoever runs it: a key there let a test pass
        # on a machine that has one and fail on CI. Local, with no `BROWSER`, so a run over SSH or with a browser
        # helper opens URLs as a run at the machine does.
        self.home = self.scratch / "home"
        self.home.mkdir()
        environ = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.scratch / "state"), "HOME": str(self.home),
                                               "CODEX_HOME": str(self.home / ".codex"),
                                               "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                                               "PROJECTOR_REMOTE": "0", "BROWSER": ""})
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


class Isolation(ConsoleCase):
    def test_a_console_test_reads_the_user_configuration_from_its_own_home(self) -> None:
        # Catches `ConsoleCase` leaving `HOME` at the real home, whose `~/.projector.toml` held an `agent.key`
        # that let a console test pass on that machine and fail on CI, which has none.
        path = config.user_config_path()
        # Checked before the write, so a case that is not isolated never writes the real file.
        self.assertTrue(path.is_relative_to(self.scratch), path)
        path.write_text('[agent]\nkey = "keychain:0123456789abcdef"\n')

        self.assertEqual("keychain:0123456789abcdef", relay.configured())


class SessionSet(ConsoleCase):
    def test_delivery_hooks_alone_do_not_make_a_projector_session(self) -> None:
        for name in ("bare-claude", "registered", "subscribed", "inbox", "old-inbox", "lapsed"):
            self.claude(name, renewed_ago=600 if name == "lapsed" else 0)
        self.codex("bare-codex", cwd=str(self.scratch))
        write_json(self.folder("registered") / "agent.json", {"schema_version": 1, "name": "worker"})
        self.subscribe("subscribed", "acme/app review claude operator")
        self.ask("inbox")
        self.ask("lapsed")
        old = self.ask("old-inbox")
        old.update(answer={"letter": "a"}, deliveredAt=int(self.clock.now * 1000) - console.RESOLVED_MS)
        write_json(inbox.session_dir("old-inbox") / "1.json", old)

        self.console.tick()

        self.assertEqual({"registered", "subscribed", "inbox"}, set(self.home_names()))

    def test_an_inbox_only_session_leaves_when_its_last_delivered_item_ages_out(self) -> None:
        self.claude("s1")
        self.ask("s1")
        self.console.tick()
        self.assertEqual(["s1"], self.home_names())
        inbox.answer("s1", "1", "a")
        token = inbox.claim("s1", "1")["claim"]["token"]
        inbox.settle("s1", "1", token, True)
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        self.assertEqual(["s1"], self.home_names())

        self.clock.now += console.RESOLVED_MS / 1000
        self.console.tick()

        self.assertEqual([], self.home_names())
        self.assertIsNone(self.console.viewed())

    def test_the_home_list_holds_live_claude_and_codex_sessions_and_leaves_out_a_lapsed_one(self) -> None:
        self.claude("claude-session")
        write_json(self.folder("claude-session") / "agent.json",
                   {"schema_version": 1, "name": "fixer", "repo": "acme/app", "branch": "main"})
        checkout = self.scratch / "checkout"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "codex-branch"], cwd=checkout, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/web.git"], cwd=checkout, check=True)
        self.codex("019a2b3c-0000-7000-8000-00000000c0de", cwd=str(checkout))
        self.subscribe("019a2b3c-0000-7000-8000-00000000c0de", "acme/web#12 fix")
        self.claude("lapsed-session", renewed_ago=600)
        self.ask("lapsed-session")

        self.console.tick()

        self.assertEqual([("Unnamed session", "codex", str(checkout)), ("fixer", "claude", "")],
                         sorted((one.label, one.host, one.cwd) for one in self.console.home()))


class HomeOrder(ConsoleCase):
    def test_sessions_share_a_directory_heading_without_merging_distinct_checkouts(self) -> None:
        for name, cwd in (("first", "/work/app"), ("other", "/work/app-copy"), ("second", "/work/app"),
                          ("upper", "/work/App"), ("nested", "/work/app/nested")):
            self.codex(name, cwd=cwd)
            self.ask(name)
        write_json(self.folder("first") / "agent.json",
                   {"schema_version": 1, "name": "first", "cwd": "/work/old", "repo": "acme/app"})
        self.claude("unknown")
        self.ask("unknown")
        self.ask("second")

        self.console.tick()
        lines = [line.text for line in self.console.lines(100)[0]]

        self.assertEqual(["upper", "second", "first", "other", "nested", "unknown"], self.home_names())
        self.assertEqual(1, lines.count("/work/app"))
        self.assertEqual(1, lines.count("/work/app-copy"))
        self.assertLess(lines.index("/work/app"), lines.index("/work/app-copy"))
        self.assertIn("Unknown working directory", lines)
        self.assertTrue(all(lines[lines.index(heading) - 1] == "" for heading in
                            ("/work/App", "/work/app", "/work/app-copy", "/work/app/nested",
                             "Unknown working directory")))
        self.assertNotIn("/work/old", lines)

    def test_a_refresh_keeps_the_initial_selection_when_another_session_moves_first(self) -> None:
        with mock.patch.object(inbox, "now_ms", return_value=int(self.clock.now * 1000)):
            for name in ("first", "second"):
                self.claude(name)
                self.ask(name)
        self.console.tick()
        self.assertEqual("first", self.console.viewed().name)
        self.ask("second")
        self.clock.now += console.INBOX_EVERY

        self.console.tick()

        self.assertEqual(["second", "first"], self.home_names())
        self.assertEqual("first", self.console.viewed().name)

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


class SessionColumns(ConsoleCase):
    def test_a_codex_rename_appears_without_an_inbox_change_and_a_partial_name_waits_for_its_newline(self) -> None:
        self.codex("s1", cwd="/work/app")
        self.subscribe("s1", "acme/app review claude operator")
        index = self.home / ".codex" / "session_index.jsonl"
        index.parent.mkdir()
        index.write_text('{"id":"s1","thread_name":"First title"}\n')
        self.console.tick()
        self.assertEqual("First title", self.console.viewed().label)

        with index.open("a") as stream:
            stream.write('not json\n{"id":"s1","thread_name":"Renamed')
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        self.assertEqual("First title", self.console.viewed().label)

        with index.open("a") as stream:
            stream.write(' session"}\n')
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        self.assertEqual("Renamed session", self.console.viewed().label)

        index.write_text('{"id":"s1","thread_name":"New"}\n')
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        self.assertEqual("New", self.console.viewed().label)

    def test_a_claude_session_uses_its_native_custom_title_after_a_large_non_title_entry(self) -> None:
        self.claude("s1")
        self.ask("s1")
        transcript = self.home / ".claude" / "projects" / "project" / "s1.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"type": "tool", "content": "x" * 400}) + "\n"
                              + json.dumps({"type": "custom-title", "sessionId": "s1", "customTitle": "Ship preview"})
                              + "\n")
        with mock.patch.object(session_titles, "READ_BYTES", 128):
            for _ in range(6):
                self.console.tick()
                self.clock.now += console.INBOX_EVERY

        self.assertEqual("Ship preview", self.console.viewed().label)
        self.assertNotIn("x" * 20, self.screen())

    def test_session_titles_replace_ids_and_pr_counts_start_in_one_column(self) -> None:
        anonymous = "019a2b3c-0000-7000-8000-00000000c0de"
        self.codex(anonymous, cwd="/work/app")
        index = self.home / ".codex" / "session_index.jsonl"
        index.parent.mkdir()
        index.write_text(json.dumps({"id": anonymous, "thread_name": "Build the preview"}) + "\n")
        self.claude("named")
        write_json(self.folder("named") / "agent.json",
                   {"schema_version": 1, "name": "build-fixer", "cwd": "/work/app", "repo": "acme/app"})
        self.subscribe(anonymous, "acme/app#12 fix")
        self.subscribe("named", "acme/app#12 fix", "acme/app#13 fix")
        self.cache("acme/app", 12)
        self.cache("acme/app", 13)

        self.console.tick()
        lines = [line.text for line in self.console.lines(100)[0]]
        codex = next(line for line in lines if "Build the preview" in line)
        named = next(line for line in lines if "build-fixer" in line)

        self.assertEqual(codex.index("1 PR"), named.index("2 PRs"))
        self.assertNotIn("00000000c0de", "\n".join(lines))
        self.assertNotIn("acme/app", "\n".join(lines))

    def test_directory_headings_shorten_only_paths_within_the_home_directory(self) -> None:
        for name, cwd in (("home", str(self.home)), ("child", str(self.home / "app")),
                          ("sibling", str(self.home) + "-other/app")):
            self.codex(name, cwd=cwd)
            self.subscribe(name, "acme/app review claude operator")

        self.console.tick()
        lines = [line.text for line in self.console.lines(100)[0]]

        self.assertIn("~", lines)
        self.assertIn("~/app", lines)
        self.assertIn(str(self.home) + "-other/app", lines)

    def test_session_rows_follow_the_directory_without_field_headings(self) -> None:
        self.codex("s1", cwd="/work/app")
        self.subscribe("s1", "acme/app review claude operator")
        self.console.tick()
        lines = self.console.home_lines(100)[0]

        self.assertEqual("", lines[1].text)
        self.assertEqual("/work/app", lines[2].text)
        self.assertTrue(lines[3].text.startswith("▸ "))
        self.assertIn("0 PRs", lines[3].text)

    def test_a_newer_notice_does_not_replace_the_latest_open_request(self) -> None:
        self.claude("s1")
        with mock.patch.object(inbox, "now_ms", return_value=int(self.clock.now * 1000) - 3000):
            self.ask("s1", "Check the build?")
        with mock.patch.object(inbox, "now_ms", return_value=int(self.clock.now * 1000) - 2000):
            inbox.request("s1", "Test the preview")
        inbox.tell("s1", "Uploaded the build")

        self.console.tick()
        row = next(line.text for line in self.console.lines(100)[0] if line.text.startswith("▸ "))

        self.assertIn("Test the preview", row)
        self.assertNotIn("Uploaded the build", row)
        self.assertNotIn("Check the build?", row)

        inbox.done("s1", "2")
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        row = next(line.text for line in self.console.lines(100)[0] if line.text.startswith("▸ "))
        self.assertIn("Check the build?", row)
        self.assertNotIn("Test the preview", row)

    def test_the_right_edge_uses_the_pull_update_time_instead_of_polling_or_heartbeat_time(self) -> None:
        self.codex("s1", cwd="/work/app")
        self.subscribe("s1", "acme/app#12 fix")
        self.cache("acme/app", 12, updatedAt=watch.stamp(self.clock.now - 120))
        self.console.tick()

        initial = next(line.text for line in self.console.lines(100)[0] if line.text.startswith("▸ "))
        self.clock.now += 30
        self.codex("s1", cwd="/work/app")
        self.cache("acme/app", 12, updatedAt=watch.stamp(self.clock.now - 150))
        self.console.tick()
        polled = next(line.text for line in self.console.lines(100)[0] if line.text.startswith("▸ "))
        self.cache("acme/app", 12, updatedAt=watch.stamp(self.clock.now - 10))
        self.clock.now += console.ROWS_EVERY
        self.console.tick()
        changed = next(line.text for line in self.console.lines(100)[0] if line.text.startswith("▸ "))

        self.assertEqual((100, "2m", "2m", "15s"),
                         (len(initial), initial.rstrip().split()[-1], polled.rstrip().split()[-1],
                          changed.rstrip().split()[-1]))


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
        self.subscribe("idle", "acme/app review claude operator")
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
        self.subscribe("s1", "acme/app review claude operator")
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

    def test_d_opens_the_vercel_preview_whose_action_comes_between_github_and_projector(self) -> None:
        # Catches a drawer that offers no preview, puts it out of the links' order, or opens the wrong URL.
        preview = "https://app-git-console.vercel.app"
        deployment = {"creator": {"login": "vercel"}, "latestStatus": {"state": "SUCCESS", "environmentUrl": preview}}
        self.claude("s1")
        self.subscribe("s1", "acme/app#12 fix")
        self.cache("acme/app", 12, commits={"nodes": [{"commit": {"deployments": {"nodes": [deployment]}}}]},
                   body="<!-- projector-summary v=1 sha=" + "a" * 40 + " -->\n"
                        "**Projector summary** of aaaaaaa: https://projector.bot/acme/app/reviews/12\n")
        self.open_entry("s1", "pr:acme/app#12")
        seen = []
        for _ in range(5):
            self.press("down")
            seen.append(self.focused())

        self.press("d")

        self.assertEqual(([["7: Discuss"], ["8: Dismiss"], ["o: Open in GitHub"], ["d: Open in Vercel"],
                           ["p: Open in Projector"]], [preview]), (seen, self.opened))

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
        # Catches arrows that stop at an open question's last option, skip its actions, wrap past either end, or close the drawer when the focus leaves it.
        self.open_entry("s1", "item:1")
        seen = [self.focused()]
        for name in ("up", "up", "up", *["down"] * 9):
            self.press(name)
            seen.append(self.focused())

        self.assertEqual([["1: Decline it (recommended)"], ["- Decline the finding?"], ["#12"], ["#12"],
                          ["- Decline the finding?"], ["1: Decline it (recommended)"], ["2: Fix it"],
                          ["6: I don't understand"], ["7: Discuss"], ["8: Dismiss"],
                          ["9: Snooze"], ["9: Snooze"], ["9: Snooze"]], seen)
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


class MouseCurses(FakeCurses):
    KEY_MOUSE = 409
    BUTTON1_RELEASED, BUTTON1_CLICKED, BUTTON1_PRESSED, BUTTON3_CLICKED = 1, 2, 4, 8

    def __init__(self) -> None:
        self.events: list[tuple[int, int, int, int, int]] = []

    def getmouse(self) -> tuple[int, int, int, int, int]:
        if not self.events:
            raise self.error("no mouse event")
        return self.events.pop(0)


class Mouse(TerminalCase):
    def setUp(self) -> None:
        super().setUp()
        self.window.height, self.window.width = 24, 180
        self.curses = MouseCurses()
        self.terminal = console.Screen(self.window, self.console, self.curses)

    def mouse_at(self, x: int, y: int, buttons: int = MouseCurses.BUTTON1_RELEASED) -> None:
        self.curses.events.append((0, x, y, 0, buttons))
        self.window.pressed.append(MouseCurses.KEY_MOUSE)
        self.assertIsNone(self.terminal.read_key())

    def click(self, text: str) -> None:
        self.draw()
        y, row = next((y, row) for y, row in self.window.rows.items() if text in row)
        self.mouse_at(row.index(text), y)

    def sessions(self, count: int = 2) -> None:
        with mock.patch.object(inbox, "now_ms", return_value=int(self.clock.now * 1000)):
            for at in range(count):
                name = f"worker-{at:02}"
                self.codex(name, cwd="/work/app")
                write_json(self.folder(name) / "agent.json", {"schema_version": 1, "name": name})
                self.ask(name, f"Question from {name}?")
        self.console.tick()

    def test_clicking_a_session_then_its_question_records_only_that_sessions_clicked_choice(self) -> None:
        self.sessions()
        self.click("worker-01")
        self.assertEqual(("home", "worker-01"), (self.console.view, self.console.viewed().name))
        self.click("- Question from worker-01?")
        self.assertEqual(("panel", "worker-01", "item:1"),
                         (self.console.view, self.console.session, self.console.drawer))
        self.click("2: Fix it")
        self.assertIsNone(inbox.read_item(inbox.session_dir("worker-01"), "1").get("answer"))
        self.type("Keep the test")
        self.press("enter")

        self.assertEqual(("b", "Keep the test", None),
                         (inbox.read_item(inbox.session_dir("worker-01"), "1")["answer"]["letter"],
                          inbox.read_item(inbox.session_dir("worker-01"), "1")["answer"]["note"],
                          inbox.read_item(inbox.session_dir("worker-00"), "1").get("answer")))

    def test_clicking_a_pull_requests_dismiss_action_keeps_its_confirmation(self) -> None:
        self.codex("s1")
        self.subscribe("s1", "acme/app#12 fix")
        self.cache("acme/app", 12, title="Ready title")
        self.console.tick()
        self.click("Ready title")
        self.click("8: Dismiss")
        self.assertEqual(["acme/app#12 fix"], watch.read_lines(self.folder("s1")))
        self.click("8: Yes, dismiss it")

        self.assertEqual(["acme/app#12 fix", "!acme/app#12 fix"], watch.read_lines(self.folder("s1")))

    def test_click_targets_follow_the_visible_rows_after_both_panes_scroll(self) -> None:
        self.window.height = 12
        self.sessions(20)
        inbox.revise("worker-18", "1", detail="\n".join(f"Detail line {at}." for at in range(40)))
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        self.press("end")
        self.click("worker-18")
        self.assertEqual("worker-18", self.console.viewed().name)
        self.click("- Question from worker-18?")
        self.draw()
        self.press_terminal(*[FakeCurses.KEY_NPAGE] * 12)
        self.click("8: Dismiss")

        self.assertEqual((True, None),
                         (inbox.read_item(inbox.session_dir("worker-18"), "1")["answer"]["dismissed"],
                          inbox.read_item(inbox.session_dir("worker-19"), "1").get("answer")))

    def test_padding_headings_other_buttons_and_typing_do_not_select_or_discard_input(self) -> None:
        self.sessions()
        self.draw()
        y, row = next((y, row) for y, row in self.window.rows.items() if "worker-01" in row)
        for x, clicked_y, buttons in ((0, y, MouseCurses.BUTTON1_RELEASED),
                                      (row.index("worker-01"), y, MouseCurses.BUTTON3_CLICKED),
                                      (row.index("worker-01"), y, MouseCurses.BUTTON1_PRESSED),
                                      (1, 2, MouseCurses.BUTTON1_RELEASED)):
            self.mouse_at(x, clicked_y, buttons)
        self.assertEqual("worker-00", self.console.viewed().name)
        self.click("- Question from worker-00?")
        self.click("1: Decline it")
        self.type("unfinished note")
        self.click("worker-01")

        self.assertEqual(("worker-00", "unfinished note", None),
                         (self.console.session, self.console.field.text,
                          inbox.read_item(inbox.session_dir("worker-00"), "1").get("answer")))

    def test_a_stale_frame_cannot_open_a_different_sessions_same_numbered_item(self) -> None:
        self.sessions()
        self.draw()
        y, row = next((y, row) for y, row in self.window.rows.items() if "- Question from worker-00?" in row)
        (self.folder("worker-00") / "codex.json").unlink()
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        self.mouse_at(row.index("Question"), y)

        self.assertEqual(("home", "worker-01", None),
                         (self.console.view, self.console.viewed().name, self.console.drawer))

    def test_resize_invalidates_the_old_click_positions_until_the_next_draw(self) -> None:
        self.sessions()
        self.draw()
        y, row = next((y, row) for y, row in self.window.rows.items() if "worker-01" in row)
        self.window.height = 12
        self.mouse_at(row.index("worker-01"), y)
        self.assertEqual("worker-00", self.console.viewed().name)
        self.click("worker-01")
        self.assertEqual("worker-01", self.console.viewed().name)

    def test_a_clicked_choice_keeps_the_revision_that_was_on_screen(self) -> None:
        self.sessions(1)
        self.click("- Question from worker-00?")
        self.draw()
        y, row = next((y, row) for y, row in self.window.rows.items() if "2: Fix it" in row)
        inbox.revise("worker-00", "1", recommend="New first choice", options=["New second choice"])
        self.clock.now += console.INBOX_EVERY
        self.console.tick()
        self.mouse_at(row.index("2: Fix it"), y)
        self.press("enter")

        self.assertIsNone(inbox.read_item(inbox.session_dir("worker-00"), "1").get("answer"))
        self.assertIsInstance(self.console.refusal, inbox.ChangedError)


class SplitLayout(TerminalCase):
    def test_only_the_selected_agent_name_has_color_and_neither_session_row_is_reversed(self) -> None:
        for name in ("first", "second"):
            self.codex(name, cwd="/work/app")
            write_json(self.folder(name) / "agent.json", {"schema_version": 1, "name": name})
        self.console.tick()
        self.console.selected = "first"
        lines = self.console.home_lines(100)[0]
        rows = [line for line in lines if line.text.startswith(("▸ first", "  second"))]
        window = FakeWindow(2, 100)
        with mock.patch.dict(os.environ, {"NO_COLOR": ""}):
            screen = console.Screen(window, self.console, ColorCurses(256))
        screen.put(0, rows[0], 100)
        screen.put(1, rows[1], 100)
        names = {text.strip(): attributes for text, attributes in window.drawn if text.strip() in ("first", "second")}

        self.assertGreater(names["first"] >> 8, 0)
        self.assertEqual(0, names["second"])
        self.assertFalse(any(attributes & FakeCurses.A_REVERSE for _, attributes in window.drawn))

    def test_narrow_rows_shorten_the_request_and_preserve_the_rightmost_update_column(self) -> None:
        self.codex("s1", cwd="/work/app")
        self.ask("s1", "Review the new preview before the next release?")
        self.console.tick()

        for width in (20, 40, 80):
            with self.subTest(width=width):
                row = next(line.text for line in self.console.lines(width)[0] if line.text.startswith("▸ "))
                self.assertEqual(width, len(row))
                self.assertTrue(row.endswith("0s "), row)
        row = next(line.text for line in self.console.lines(40)[0] if line.text.startswith("▸ "))
        self.assertIn("…", row)
        self.assertNotIn("release?", row)

    def test_the_drawer_ends_after_its_items_without_a_conversation_row(self) -> None:
        self.claude("s1")
        self.ask("s1")
        self.open_entry("s1", "item:1")
        self.press("v")
        for _ in range(15):
            self.press("down")

        self.assertNotIn("Conversation", self.draw())
        self.assertEqual(["9: Snooze"], self.focused())
        self.assertNotIn("v conversation", self.console.footer()[-1].text)

    def test_status_width_keeps_pull_request_titles_aligned(self) -> None:
        self.claude("s1")
        self.subscribe("s1", "acme/app#12 fix", "acme/app#123 fix", "acme/app#124 fix")
        self.cache("acme/app", 12, title="Ready title")
        self.cache("acme/app", 123, title="Reviewed title", labels={"nodes": [{"name": "reviewing"}]})
        self.cache("acme/app", 124, title="Conflict title", mergeable="CONFLICTING")
        self.console.tick()
        self.console.open_panel("s1")
        lines = [line.text for line in self.console.lines(100)[0]]
        ready = next(line for line in lines if "Ready title" in line)
        reviewed = next(line for line in lines if "Reviewed title" in line)
        conflict = next(line for line in lines if "Conflict title" in line)

        self.assertEqual((ready.index("Ready title"), ready.index("Ready title")),
                         (reviewed.index("Reviewed title"), conflict.index("Conflict title")))

    def test_the_bottom_panel_tracks_the_selected_session_and_records_only_for_it(self) -> None:
        with mock.patch.object(inbox, "now_ms", return_value=int(self.clock.now * 1000)):
            for name in ("first", "second"):
                self.codex(name, cwd="/work/app")
                self.ask(name, f"Question from {name}?")
        self.console.tick()
        drawn = self.draw()
        self.assertTrue(all(line.startswith(" ") for line in drawn.splitlines() if line))
        self.assertTrue(all(len(line) <= self.window.width for line in drawn.splitlines()))
        initial = drawn.split("─" * (self.window.width - 1), 1)[1]

        self.press_terminal(FakeCurses.KEY_DOWN)
        moved = self.draw().split("─" * (self.window.width - 1), 1)[1]
        self.press("right", "enter", "1", "enter")

        self.assertIn("Question from first?", initial)
        self.assertNotIn("Question from second?", initial)
        self.assertIn("Question from second?", moved)
        self.assertNotIn("Question from first?", moved)
        self.assertIn("/work/app", self.draw())
        answers = [inbox.read_item(inbox.session_dir(name), "1").get("answer") for name in ("first", "second")]
        self.assertEqual([None, "a"], [answer and answer["letter"] for answer in answers])

    def test_a_long_session_list_keeps_its_group_and_the_bottom_panel_visible(self) -> None:
        for number in range(20):
            name = f"worker-{number:02}"
            self.codex(name, cwd="/work/app")
            write_json(self.folder(name) / "agent.json", {"schema_version": 1, "name": name})
            self.subscribe(name, "acme/app review claude operator")
        self.console.tick()
        for _ in range(19):
            self.press_terminal(FakeCurses.KEY_DOWN)

        drawn = self.draw()
        self.assertIn("/work/app", drawn)
        self.assertIn("worker-19", drawn)
        self.assertIn("No pull requests or items", drawn)
        self.assertEqual("worker-19", self.console.viewed().name)

    def test_a_disappearing_session_closes_its_field_and_previews_the_remaining_session(self) -> None:
        for name in ("first", "second"):
            self.codex(name, cwd="/work/app")
            self.ask(name, f"Question from {name}?")
        self.open_entry("first", "item:1")
        self.press("1")
        self.type("unfinished note")
        (self.folder("first") / "codex.json").unlink()
        self.clock.now += console.INBOX_EVERY

        self.console.tick()

        self.assertEqual(("home", None, None, "second"),
                         (self.console.view, self.console.field, self.console.drawer, self.console.viewed().name))
        self.assertIn("Question from second?", self.draw())


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
        self.press_terminal(*[FakeCurses.KEY_NPAGE] * 12)
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
                self.assertEqual(None if environ.get("NO_COLOR") else
                                 ColorCurses.COLOR_RED if colors == 8 else console.xterm_color(dark["claude"]),
                                 drawn["1 PR"])
        self.assertEqual((67, 239, 147), (console.xterm_color((95, 135, 175)), console.xterm_color((80, 80, 80)),
                                          console.xterm_color((177, 185, 249))))


class Terminal(unittest.TestCase):
    """`project console` run in a pseudo-terminal, with the bytes a terminal sends for each key."""

    def prepare(self) -> None:
        """A state directory and a home of its own with one live Claude Code session, `S`, asking one question."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.state = str(Path(scratch.name) / "state")
        self.home = Path(scratch.name) / "home"
        self.home.mkdir()
        self.env = {**os.environ, "XDG_STATE_HOME": self.state, "HOME": str(self.home), "PYTHONPATH": str(SOURCE),
                    "TERM": "xterm-256color"}
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

    def click(self, x: int, y: int) -> None:
        import curses

        curses.setupterm(self.env["TERM"])
        prefix = curses.tigetstr("kmous")
        if prefix == b"\x1b[<":
            self.send(f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode())
        else:
            self.send(prefix + bytes((32, x + 32, y + 32)) + prefix + bytes((35, x + 32, y + 32)))

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

    def test_the_console_process_reads_the_user_configuration_from_the_tests_home(self) -> None:
        # Catches the console's process inheriting the real `HOME`, and with it the real `~/.projector.toml`.
        self.prepare()
        (self.home / ".projector.toml").write_text('[panel]\nbackground = "#123456"\n')

        done = subprocess.run([sys.executable, "-m", "projector", "config", "get", "panel.background"], env=self.env,
                              cwd=self.state, capture_output=True, text=True, timeout=60)

        self.assertEqual(("#123456\n", 0), (done.stdout, done.returncode))

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

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_terminal_mouse_reports_open_a_question_and_choose_the_clicked_answer(self) -> None:
        self.prepare()
        self.start()
        self.read_for(10, until=b"Decline the finding?")
        self.click(3, 9)
        self.click(7, 12)
        self.send(b"\r", b"\x1b", b"\x1b", b"\x03")
        code = self.exited(10)
        item = json.loads((Path(self.state) / "projector" / "inbox" / "S" / "1.json").read_text())

        self.assertEqual(("b", 0), ((item.get("answer") or {}).get("letter"), code))
        self.assertNotIn(b"Traceback", bytes(self.shown))


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
