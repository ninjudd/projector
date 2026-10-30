"""The `project inbox` commands, against a temporary state directory.

Each test points `XDG_STATE_HOME` at its own directory and runs the CLI from a temporary
directory. A test that shows the checkout's repository does not shape a number runs the CLI from
a temporary Git checkout instead.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Callable
from unittest import mock

from projector import cli, inbox

HOLD_LOCK = """
import os, sys
from pathlib import Path
from projector import inbox

with inbox.locked(Path(sys.argv[1])):
    print("held", flush=True)
    if sys.argv[2] == "exit":
        os._exit(0)
    sys.stdin.read()
"""


def at_once(*calls: Callable[[], object]) -> list[str]:
    """Start the calls together, each in its own thread, and return how each ended, sorted:
    `done`, or `refused` when it raised `InboxError`."""
    outcomes: list[str] = []
    start = threading.Barrier(len(calls))

    def run(call: Callable[[], object]) -> None:
        start.wait()
        try:
            call()
            outcomes.append("done")
        except inbox.InboxError:
            outcomes.append("refused")

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(outcomes)


class InboxCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.state = self.scratch / "state"
        environ = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)})
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("PROJECTOR_SESSION", None)

    def repository(self, name: str, origin: str) -> Path:
        checkout = self.scratch / name
        checkout.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
        subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{origin}.git"], cwd=checkout,
                       check=True)
        return checkout

    def run_cli(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.chdir(cwd or self.scratch), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["inbox", *args])
        return code, out.getvalue(), err.getvalue()

    def ask(self, question: str = "Decline the finding?", *args: str, session: str = "s1",
            cwd: Path | None = None) -> str:
        code, out, err = self.run_cli("ask", question, "--session", session, *args, cwd=cwd)
        self.assertEqual(0, code, err)
        return out.strip()

    def folder(self, session: str = "s1") -> Path:
        return self.state / "projector" / "inbox" / session

    def item(self, number: int | str, session: str = "s1") -> dict:
        return json.loads((self.folder(session) / f"{number}.json").read_text())

    def hold_lock(self, then: str) -> subprocess.Popen:
        """Start a process that takes the session's lock and, once it holds it, either waits for its
        standard input to close (`wait`) or exits without unlocking (`exit`)."""
        holder = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(self.folder()), then],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                  env={**os.environ, "PYTHONPATH": str(Path(inbox.__file__).parents[1])})
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.stdin.close)
        self.assertEqual("held\n", holder.stdout.readline())
        return holder


class NumberingTests(InboxCase):
    def test_numbers_every_item_from_one_in_one_count_across_pull_requests_and_kinds(self) -> None:
        app = self.repository("app", "acme/app")
        self.assertEqual("1", self.ask("First?", "--recommend", "x", "--pr", "acme/app#337", cwd=app))
        code, out, err = self.run_cli("tell", "Pushed", "--pr", "octo/web#12", "--session", "s1", cwd=app)
        self.assertEqual((0, "2\n"), (code, out), err)
        self.assertEqual("3", self.ask("No pull?", "--recommend", "x", cwd=app))
        self.assertEqual(["1.json", "2.json", "3.json", "count.json"],
                         sorted(path.name for path in self.folder().glob("*.json")))
        self.assertEqual({"last": 3}, json.loads((self.folder() / "count.json").read_text()))
        self.assertEqual((3, "3", None), (self.item(3)["number"], self.item(3)["ref"], self.item(3)["pr"]))
        self.assertEqual("octo/web#12", self.item(2)["pr"])

    def test_a_checkout_of_another_repository_takes_the_next_number_in_the_same_count(self) -> None:
        app, web = self.repository("app", "acme/app"), self.repository("web", "octo/web")
        self.assertEqual("1", self.ask("First?", "--recommend", "x", "--pr", "acme/app#337", cwd=app))
        self.assertEqual("2", self.ask("First?", "--recommend", "x", "--pr", "acme/app#337", cwd=web))

    def test_each_session_counts_from_one(self) -> None:
        self.assertEqual("1", self.ask("First?", "--recommend", "x", session="s1"))
        self.assertEqual("2", self.ask("Second?", "--recommend", "x", session="s1"))
        self.assertEqual("1", self.ask("Other?", "--recommend", "x", session="s2"))

    def test_lists_each_item_by_its_number(self) -> None:
        self.ask("First?", "--recommend", "x", "--pr", "acme/app#337")
        self.run_cli("tell", "Pushed", "--pr", "octo/web#12", "--session", "s1")
        self.ask("No pull?", "--recommend", "x")
        self.assertEqual(["1\tquestion\topen\tFirst?", "2\tnotice\topen\tPushed", "3\tquestion\topen\tNo pull?"],
                         self.run_cli("list", "--session", "s1")[1].splitlines())

    def test_watch_delivers_an_answer_named_by_its_number(self) -> None:
        self.ask("First?", "--recommend", "x", "--pr", "acme/app#337")
        self.assertEqual((0, "answered 1.a\n", ""), self.run_cli("answer", "1.a", "--session", "s1"))
        self.assertEqual("ANSWER 1.a First? → x\n", self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_a_number_two_sessions_hold_needs_the_session(self) -> None:
        self.ask("First?", "--recommend", "x", session="s1")
        self.ask("Other?", "--recommend", "x", session="s2")
        code, out, err = self.run_cli("answer", "1.a")
        self.assertEqual((67, ""), (code, out))
        self.assertIn("s1, s2", err)
        self.assertIn("--session", err)
        self.assertEqual((0, "answered 1.a\n"), self.run_cli("answer", "1.a", "--session", "s2")[:2])

    def test_a_bare_number_names_the_one_session_that_holds_it_open(self) -> None:
        self.ask("Delivered?", "--recommend", "x", session="s1")
        self.run_cli("answer", "1.a", "--session", "s1")
        self.run_cli("watch", "--once", "--session", "s1")
        self.run_cli("tell", "Read", "--session", "s1")
        self.run_cli("dismiss", "2", "--session", "s1")
        resolved = [self.item(1, "s1"), self.item(2, "s1")]
        self.ask("Open?", "--recommend", "x", session="s2")
        self.run_cli("tell", "Unread", "--session", "s2")

        self.assertTrue(self.run_cli("show", "1")[1].startswith("1 Open?\n"))
        self.assertEqual(0, self.run_cli("snooze", "1")[0])
        self.assertEqual((0, "answered 1.a\n"), self.run_cli("answer", "1.a")[:2])
        self.assertEqual((0, "dismissed 2\n"), self.run_cli("dismiss", "2")[:2])
        self.assertEqual(("a", True), (self.item(1, "s2")["answer"]["letter"], self.item(2, "s2")["answer"]["dismissed"]))
        self.assertNotIn("snoozedUntil", self.item(1, "s2"))
        self.assertEqual(resolved, [self.item(1, "s1"), self.item(2, "s1")])

    def test_a_number_open_in_two_sessions_needs_the_session_beside_a_resolved_one(self) -> None:
        for session in ("s1", "s2", "s3"):
            self.ask("First?", "--recommend", "x", session=session)
        self.run_cli("dismiss", "1", "--session", "s3")
        for args in (["show", "1"], ["answer", "1.a"], ["dismiss", "1"], ["withdraw", "1"], ["snooze", "1"]):
            with self.subTest(command=args[0]):
                code, out, err = self.run_cli(*args)
                self.assertEqual((67, ""), (code, out))
                self.assertIn("2 sessions: s1, s2;", err)

    def test_a_number_no_session_holds_open_is_looked_up_among_every_item(self) -> None:
        self.ask("First?", "--recommend", "x", session="s1")
        self.run_cli("answer", "1.a", "--session", "s1")
        code, _, err = self.run_cli("answer", "1.b")
        self.assertEqual(65, code)
        self.assertIn("already answered, as 1.a", err)
        self.assertTrue(self.run_cli("show", "1")[1].startswith("1 First?\n"))
        self.ask("Other?", "--recommend", "x", session="s2")
        self.run_cli("dismiss", "1", "--session", "s2")
        code, _, err = self.run_cli("show", "1")
        self.assertEqual(67, code)
        self.assertIn("2 sessions: s1, s2;", err)

    def test_a_session_from_the_environment_is_the_only_one_looked_in(self) -> None:
        self.ask("First?", "--recommend", "x", session="s1")
        self.run_cli("dismiss", "1", "--session", "s1")
        self.ask("Open?", "--recommend", "x", session="s2")
        with mock.patch.dict(os.environ, {"PROJECTOR_SESSION": "s1"}):
            code, _, err = self.run_cli("answer", "1.a")
        self.assertEqual(65, code)
        self.assertIn("already dismissed", err)
        self.assertNotIn("answer", self.item(1, "s2"))

    def test_refuses_a_reference_in_an_old_form_with_the_new_ones(self) -> None:
        self.ask("First?", "--recommend", "x", "--pr", "acme/app#337")
        for args in (["show", "#337.1"], ["answer", "q1.a"], ["dismiss", "octo/web#12.1"], ["snooze", "#337.1.a"],
                     ["show", "0"], ["show", "01"]):
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args, "--session", "s1")
                self.assertEqual((65, ""), (code, out))
                self.assertIn("such as 3,", err)
                self.assertIn("such as 3.a", err)
        self.assertNotIn("answer", self.item(1))

    def test_leaves_items_stored_under_old_references_unread_in_place(self) -> None:
        folder = self.folder()
        folder.mkdir(parents=True)
        old = {
            "q1.json": {"ref": "q1", "kind": "question", "number": 1, "pr": None, "postedAt": 1, "subject": "Old?",
                        "detail": "", "choices": ["x"], "answer": {"letter": "a", "text": "x", "answeredAt": 2}},
            "%23337.1.json": {"ref": "#337.1", "kind": "notice", "number": 1, "pr": "acme/app#337", "postedAt": 1,
                              "subject": "Old notice", "detail": "", "answer": {"dismissed": True, "answeredAt": 2},
                              "deliveredAt": 2},
            "counts.json": {"q": 1, "acme/app#337": 1},
            "session.json": {"repo": "acme/app"},
        }
        for name, value in old.items():
            (folder / name).write_text(json.dumps(value))
        self.assertEqual("", self.run_cli("list", "--all", "--session", "s1")[1])
        self.assertEqual("", self.run_cli("watch", "--once", "--session", "s1")[1])

        self.assertEqual("1", self.ask("New?", "--recommend", "x"))
        self.assertEqual(("New?", {"last": 1}),
                         (self.item(1)["subject"], json.loads((folder / "count.json").read_text())))
        # Pruning every delivered item removes the new one and leaves the old ones.
        with mock.patch.object(inbox, "KEEP_DELIVERED", 0):
            self.run_cli("answer", "1.a", "--session", "s1")
            self.assertEqual("ANSWER 1.a New? → x\n", self.run_cli("watch", "--once", "--session", "s1")[1])
        self.assertFalse((folder / "1.json").exists())
        for name, value in old.items():
            with self.subTest(old=name):
                self.assertEqual(value, json.loads((folder / name).read_text()))

    def test_the_context_command_is_gone(self) -> None:
        with self.assertRaises(SystemExit) as stop, redirect_stderr(io.StringIO()):
            cli.main(["inbox", "context", "--session", "s1"])
        self.assertEqual(2, stop.exception.code)


class AskTests(InboxCase):
    def test_letters_the_recommendation_a_and_records_the_question(self) -> None:
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--detail", "Why.",
                 "--pr", "acme/app#337", "--agent", "agent-7")
        question = self.item(1)
        self.assertEqual(["Decline it", "Add a budget"], question["choices"])
        self.assertEqual("agent-7", question["agentId"])
        self.assertEqual("Why.", question["detail"])
        self.assertEqual("acme/app#337", question["pr"])

    def test_refuses_a_question_that_fails_a_check_and_writes_nothing(self) -> None:
        for args in (["Two\nlines?", "--recommend", "x"],
                     ["A\ttab?", "--recommend", "x"],
                     ["x" * 121, "--recommend", "x"],
                     ["Empty?", "--recommend", " "],
                     ["Two-line answer?", "--recommend", "line one\nline two"],
                     ["Two-line option?", "--recommend", "x", "--option", "line one\rline two"],
                     ["Many?", "--recommend", "a", *[f"--option={n}" for n in "bcdef"], "--option=g"],
                     ["Repeat?", "--recommend", "Same", "--option", "Same"],
                     ["Twice?", "--recommend", "a", "--option", "Other", "--option", " Other "],
                     ["Bad pull?", "--recommend", "x", "--pr", "app#12"]):
            with self.subTest(args=args[0][:16]):
                code, _, err = self.run_cli("ask", *args, "--session", "s1")
                self.assertEqual(65, code, err)
        self.assertFalse((self.folder() / "count.json").exists())

    def test_accepts_five_other_options_and_a_question_of_120_characters(self) -> None:
        self.assertEqual("1", self.ask("x" * 120, "--recommend", "a", *[f"--option={n}" for n in "bcdef"]))

    def test_needs_a_session_and_takes_it_from_the_environment(self) -> None:
        code, _, err = self.run_cli("ask", "Who?", "--recommend", "x")
        self.assertEqual(2, code, err)
        with mock.patch.dict(os.environ, {"PROJECTOR_SESSION": "s9"}):
            code, out, _ = self.run_cli("ask", "Who?", "--recommend", "x")
        self.assertEqual((0, "1"), (code, out.strip()))
        self.assertTrue((self.folder("s9") / "1.json").exists())

    def test_refuses_a_session_id_that_is_not_a_plain_name(self) -> None:
        code, _, _ = self.run_cli("ask", "Where?", "--recommend", "x", "--session", "../escape")
        self.assertEqual(65, code)

    def test_prints_the_question_as_json(self) -> None:
        code, out, _ = self.run_cli("ask", "Json?", "--recommend", "x", "--session", "s1", "--json")
        self.assertEqual(0, code)
        self.assertEqual(("1", 1), (json.loads(out)["item"]["ref"], json.loads(out)["item"]["number"]))

    def test_records_the_question_in_the_inbox_store(self) -> None:
        self.ask("Where?", "--recommend", "x")
        item = self.item(1)
        self.assertEqual(("question", "Where?"), (item["kind"], item["subject"]))
        self.assertIsInstance(item["postedAt"], int)
        self.assertNotIn("question", item)
        self.assertFalse((self.state / "projector" / "questions").exists())

    def test_the_old_question_command_is_gone(self) -> None:
        with self.assertRaises(SystemExit) as stop, redirect_stderr(io.StringIO()):
            cli.main(["question", "list"])
        self.assertEqual(2, stop.exception.code)


class AnswerTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--pr", "acme/app#337")

    def test_records_a_letter_once(self) -> None:
        self.assertEqual((0, "answered 1.a\n", ""), self.run_cli("answer", "1.a", "--session", "s1"))
        code, _, err = self.run_cli("answer", "1.b", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("already answered, as 1.a", err)

    def test_records_a_typed_answer_under_the_last_letter(self) -> None:
        code, _, _ = self.run_cli("answer", "1.c", "--text", "keep the cap", "--session", "s1")
        self.assertEqual(0, code)
        self.assertEqual({"letter": "c", "text": "keep the cap"},
                         {key: self.item(1)["answer"][key] for key in ("letter", "text")})

    def test_refuses_a_letter_the_question_does_not_have(self) -> None:
        for args in (["1.d"], ["1.c"], ["1.a", "--text", "x"], ["1"]):
            with self.subTest(args=args):
                self.assertEqual(65, self.run_cli("answer", *args, "--session", "s1")[0])

    def test_refuses_a_typed_answer_of_more_than_one_line(self) -> None:
        code, _, err = self.run_cli("answer", "1.c", "--text", "line one\nline two", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("one line", err)
        self.assertNotIn("answer", self.item(1))

    def test_refuses_an_answer_letter_on_every_other_command(self) -> None:
        for args in (["show", "1.a"], ["dismiss", "1.a"], ["withdraw", "1.a"], ["snooze", "1.a"], ["restore", "1.a"],
                     ["claim", "1.a"], ["delivered", "1.a", "--claim", "x"], ["reopen", "1.a", "--claim", "x"]):
            with self.subTest(command=args[0]):
                code, out, err = self.run_cli(*args, "--session", "s1")
                self.assertEqual((65, ""), (code, out))
                self.assertIn("1, without an answer letter", err)
        self.assertEqual([], [key for key in ("answer", "snoozedUntil", "claim") if key in self.item(1)])

    def test_finds_the_session_without_one_and_refuses_an_ambiguous_number(self) -> None:
        self.assertEqual(0, self.run_cli("answer", "1.a")[0])
        self.ask("Same number?", "--recommend", "x", session="s2")
        self.ask("Again?", "--recommend", "x", session="s2")
        self.ask("Again?", "--recommend", "x", session="s3")
        self.ask("Again?", "--recommend", "x", session="s3")
        code, _, err = self.run_cli("answer", "2.a")
        self.assertEqual(67, code)
        self.assertIn("s2, s3", err)

    def test_an_unknown_number_exits_66(self) -> None:
        self.assertEqual(66, self.run_cli("answer", "999.a")[0])
        self.assertEqual(66, self.run_cli("answer", "4.a", "--session", "s1")[0])

    def test_dismisses_once(self) -> None:
        self.assertEqual((0, "dismissed 1\n", ""), self.run_cli("dismiss", "1", "--session", "s1"))
        self.assertEqual(65, self.run_cli("answer", "1.a", "--session", "s1")[0])

    def test_two_answers_at_once_record_one(self) -> None:
        self.assertEqual(["done", "refused"], at_once(lambda: inbox.answer("s1", "1", "a"),
                                                      lambda: inbox.answer("s1", "1", "b")))
        self.assertIn(self.item(1)["answer"]["letter"], "ab")

    def test_a_lock_another_process_holds_makes_a_command_wait_then_exit_75(self) -> None:
        holder = self.hold_lock("wait")
        with mock.patch.object(inbox, "LOCK_WAIT_SECONDS", 0.3):
            started = time.monotonic()
            code, _, err = self.run_cli("answer", "1.a", "--session", "s1")
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual(75, code)
        self.assertIn("another command holds", err)
        holder.stdin.close()
        self.assertEqual(0, holder.wait())
        self.assertEqual(0, self.run_cli("answer", "1.a", "--session", "s1")[0])

    def test_a_holder_that_exits_without_unlocking_leaves_the_lock_free_at_once(self) -> None:
        holder = self.hold_lock("exit")
        self.assertEqual(0, holder.wait())
        with mock.patch.object(inbox, "LOCK_WAIT_SECONDS", 0):
            self.assertEqual(0, self.run_cli("answer", "1.a", "--session", "s1")[0])


class DeliveryTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--pr", "acme/app#337")
        self.run_cli("answer", "1.b", "--session", "s1")

    def claim(self, *args: str) -> tuple[int, str]:
        code, out, _ = self.run_cli("claim", "1", "--session", "s1", *args)
        return code, out.split("\t")[0]

    def test_claims_once_and_prints_the_text_to_deliver(self) -> None:
        code, out, _ = self.run_cli("claim", "1", "--session", "s1")
        self.assertEqual(0, code)
        self.assertTrue(out.endswith("\tANSWER 1.b Decline it? → Add a budget\n"), out)
        self.assertEqual(65, self.claim()[0])

    def test_two_claims_at_once_take_one(self) -> None:
        self.assertEqual(["done", "refused"], at_once(lambda: inbox.claim("s1", "1"),
                                                      lambda: inbox.claim("s1", "1")))

    def test_renews_only_with_the_claims_token(self) -> None:
        _, token = self.claim()
        self.assertEqual(0, self.claim("--renew", token)[0])
        self.assertEqual(65, self.claim("--renew", "not-the-token")[0])

    def test_a_claim_not_renewed_for_a_minute_is_taken_by_a_new_claim(self) -> None:
        _, token = self.claim()
        later = inbox.now_ms() + inbox.CLAIM_EXPIRES_MS + 1
        with mock.patch.object(inbox, "now_ms", return_value=later):
            code, fresh = self.claim()
            self.assertEqual(0, code)
            self.assertNotEqual(token, fresh)
            self.assertEqual(65, self.run_cli("delivered", "1", "--claim", token, "--session", "s1")[0])
            self.assertEqual(0, self.run_cli("delivered", "1", "--claim", fresh, "--session", "s1")[0])

    def test_a_lapsed_claims_token_settles_it_until_a_new_claim_replaces_it(self) -> None:
        _, token = self.claim()
        later = inbox.now_ms() + inbox.CLAIM_EXPIRES_MS + 1
        with mock.patch.object(inbox, "now_ms", return_value=later):
            self.assertEqual(0, self.claim("--renew", token)[0])
        with mock.patch.object(inbox, "now_ms", return_value=later + inbox.CLAIM_EXPIRES_MS + 1):
            self.assertEqual(0, self.run_cli("delivered", "1", "--claim", token, "--session", "s1")[0])

    def test_delivered_hides_the_question_from_the_open_list(self) -> None:
        _, token = self.claim()
        self.assertEqual(0, self.run_cli("delivered", "1", "--claim", token, "--session", "s1")[0])
        self.assertEqual("", self.run_cli("list", "--session", "s1")[1])
        self.assertIn("delivered 1.b", self.run_cli("list", "--all", "--session", "s1")[1])
        self.assertEqual(65, self.claim()[0])

    def test_reopen_clears_the_answer(self) -> None:
        _, token = self.claim()
        self.assertEqual(0, self.run_cli("reopen", "1", "--claim", token, "--session", "s1")[0])
        self.assertIn("\topen\t", self.run_cli("list", "--session", "s1")[1])
        self.assertEqual(0, self.run_cli("answer", "1.a", "--session", "s1")[0])

    def test_watch_delivers_each_answer_once_and_skips_a_live_claim(self) -> None:
        self.ask("Run init?", "--recommend", "Yes")
        self.run_cli("dismiss", "2", "--session", "s1")
        self.assertEqual(0, self.claim()[0])
        code, out, _ = self.run_cli("watch", "--once", "--session", "s1")
        self.assertEqual((0, "DISMISSED 2 Run init?\n"), (code, out))
        self.assertEqual("", self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_watch_leaves_an_answer_it_could_not_print_to_the_next_deliverer(self) -> None:
        read, write = os.pipe()
        os.close(read)
        stderr = io.StringIO()
        with open(write, "w") as closed_pipe, contextlib.chdir(self.scratch), redirect_stdout(closed_pipe), \
                redirect_stderr(stderr):
            self.assertEqual(141, cli.main(["inbox", "watch", "--once", "--session", "s1"]))
        self.assertEqual("", stderr.getvalue())
        self.assertIn("\tdelivering 1.b\t", self.run_cli("list", "--session", "s1")[1])
        later = inbox.now_ms() + inbox.CLAIM_EXPIRES_MS + 1
        with mock.patch.object(inbox, "now_ms", return_value=later):
            self.assertEqual("ANSWER 1.b Decline it? → Add a budget\n",
                             self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_watch_refuses_an_interval_that_is_not_a_positive_number(self) -> None:
        for interval in ("-1", "0", "nan", "inf", "soon"):
            with self.subTest(interval=interval):
                err = io.StringIO()
                with self.assertRaises(SystemExit) as stop, redirect_stderr(err):
                    cli.main(["inbox", "watch", "--interval", interval, "--once", "--session", "s1"])
                self.assertEqual(2, stop.exception.code)
                self.assertIn("--interval", err.getvalue())

    def test_keeps_the_newest_delivered_items_and_never_reuses_a_number(self) -> None:
        with mock.patch.object(inbox, "KEEP_DELIVERED", 2):
            for number in range(3):
                ref = self.ask(f"Q{number}?", "--recommend", "x")
                self.run_cli("answer", f"{ref}.a", "--session", "s1")
                self.run_cli("watch", "--once", "--session", "s1")
        names = sorted(path.name for path in self.folder().glob("[0-9]*.json"))
        self.assertEqual(["3.json", "4.json"], names)
        self.assertEqual("5", self.ask("Next?", "--recommend", "x"))


class ListTests(InboxCase):
    def test_lists_every_sessions_open_questions_without_a_session(self) -> None:
        self.ask("One?", "--recommend", "x", session="s1")
        self.ask("Two?", "--recommend", "x", session="s2")
        out = self.run_cli("list")[1]
        self.assertEqual(["s1\t1\tquestion\topen\tOne?", "s2\t1\tquestion\topen\tTwo?"], out.splitlines())

    def test_skips_a_file_that_is_not_an_item(self) -> None:
        self.ask("One?", "--recommend", "x")
        (self.folder() / "7.json").write_text("{not json")
        (self.folder() / "misnamed.json").write_text(json.dumps({"ref": "8", "kind": "question", "subject": "?"}))
        self.assertEqual(["1\tquestion\topen\tOne?"], self.run_cli("list", "--session", "s1")[1].splitlines())

    def test_every_command_refuses_a_file_that_is_not_an_item(self) -> None:
        self.ask("One?", "--recommend", "x")
        (self.folder() / "7.json").write_text(json.dumps({"ref": "7", "subject": "No kind?"}))
        (self.folder() / "8.json").write_text(json.dumps({"ref": "9", "kind": "notice", "subject": "Misnamed?"}))
        for ref in ("7", "8"):
            for args in (["show", ref], ["answer", f"{ref}.a"], ["dismiss", ref], ["withdraw", ref], ["snooze", ref]):
                with self.subTest(args=args):
                    code, _, err = self.run_cli(*args, "--session", "s1")
                    self.assertEqual(66, code)
                    self.assertIn("is not an inbox item", err)
        self.assertEqual({"ref": "7", "subject": "No kind?"}, self.item(7))

    def test_lists_every_session_past_a_directory_that_is_not_one(self) -> None:
        self.ask("One?", "--recommend", "x")
        (self.folder().parent / "not a session").mkdir()
        self.assertEqual((0, "s1\t1\tquestion\topen\tOne?\n", ""), self.run_cli("list"))

    def test_lists_and_shows_a_dismissed_question_as_dismissed(self) -> None:
        self.ask("One?", "--recommend", "x")
        self.run_cli("dismiss", "1", "--session", "s1")
        self.assertEqual(["1\tquestion\tdismissed\tOne?"], self.run_cli("list", "--session", "s1")[1].splitlines())
        self.assertEqual("DISMISSED 1 One?\n", self.run_cli("watch", "--once", "--session", "s1")[1])
        self.assertEqual(["1\tquestion\tdismissed\tOne?"],
                         self.run_cli("list", "--all", "--session", "s1")[1].splitlines())
        self.assertTrue(self.run_cli("show", "1")[1].endswith("\ndismissed\n"))

    def test_show_prints_the_lettered_answers(self) -> None:
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget")
        out = self.run_cli("show", "1")[1]
        self.assertTrue(out.startswith("1 Decline it?\n"), out)
        self.assertIn("a. Decline it (recommended)\nb. Add a budget\nc. type an answer with --text\nopen", out)


class NoticeTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--pr", "acme/app#12")

    def tell(self, notice: str = "Force-pushed #12 to drop a stray file", *args: str) -> str:
        code, out, err = self.run_cli("tell", notice, "--pr", "acme/app#12", "--session", "s1", *args)
        self.assertEqual(0, code, err)
        return out.strip()

    def test_numbers_a_notice_in_the_count_questions_use(self) -> None:
        self.assertEqual("2", self.tell())
        notice = self.item(2)
        self.assertEqual(("notice", "Force-pushed #12 to drop a stray file"), (notice["kind"], notice["subject"]))
        self.assertNotIn("choices", notice)
        self.assertEqual("3", self.ask("Rebase?", "--recommend", "Yes", "--pr", "acme/app#12"))
        code, out, _ = self.run_cli("tell", "No pull", "--session", "s1", "--json")
        self.assertEqual((0, "4"), (code, json.loads(out)["item"]["ref"]))

    def test_records_the_detail_and_the_posting_subagent(self) -> None:
        detail = self.scratch / "detail.md"
        detail.write_text("The file held a token.\n")
        self.tell("Dropped a file", "--detail-file", str(detail), "--agent", "agent-7")
        notice = self.item(2)
        self.assertEqual(("The file held a token.", "agent-7"), (notice["detail"], notice["agentId"]))

    def test_refuses_a_notice_that_fails_a_check_and_writes_nothing(self) -> None:
        count = (self.folder() / "count.json").read_text()
        for args in (["Two\nlines"], ["A\ttab"], ["x" * 121], [" "], ["Bad pull", "--pr", "12"]):
            with self.subTest(args=args[0][:12]):
                code, _, err = self.run_cli("tell", *args, "--session", "s1")
                self.assertEqual(65, code, err)
        self.assertEqual(count, (self.folder() / "count.json").read_text())
        self.assertEqual(["1"], [item["ref"] for item in inbox.load_items(self.folder())])

    def test_dismissing_a_notice_delivers_it_at_once(self) -> None:
        self.tell()
        self.assertEqual((0, "dismissed 2\n", ""), self.run_cli("dismiss", "2", "--session", "s1"))
        notice = self.item(2)
        self.assertTrue(notice["answer"]["dismissed"])
        self.assertEqual(notice["answer"]["answeredAt"], notice["deliveredAt"])
        code, _, err = self.run_cli("dismiss", "2", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("already dismissed", err)
        self.assertEqual("", self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_dismissing_a_question_still_waits_for_its_deliverer(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        self.assertNotIn("deliveredAt", self.item(1))
        self.assertEqual("DISMISSED 1 Decline it?\n", self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_a_notice_takes_no_answer_and_no_claim(self) -> None:
        self.tell()
        for args in (["answer", "2.a"], ["claim", "2"]):
            with self.subTest(command=args[0]):
                code, _, err = self.run_cli(*args, "--session", "s1")
                self.assertEqual(65, code)
                self.assertIn("project inbox dismiss", err)

    def test_lists_and_shows_a_notice_by_its_kind(self) -> None:
        self.tell("Dropped a file", "--detail", "It held a token.")
        self.assertEqual(["1\tquestion\topen\tDecline it?", "2\tnotice\topen\tDropped a file"],
                         self.run_cli("list", "--session", "s1")[1].splitlines())
        self.assertEqual("2 Dropped a file\nabout acme/app#12\n\nIt held a token.\n\nopen\n",
                         self.run_cli("show", "2")[1])
        self.run_cli("dismiss", "2", "--session", "s1")
        self.assertEqual(["1\tquestion\topen\tDecline it?"], self.run_cli("list", "--session", "s1")[1].splitlines())
        self.assertIn("2\tnotice\tdismissed\tDropped a file", self.run_cli("list", "--all", "--session", "s1")[1])


class WithdrawTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--pr", "acme/app#12")
        self.run_cli("tell", "Force-pushed #12", "--pr", "acme/app#12", "--session", "s1")

    def assert_withdrawn(self, item: dict) -> None:
        self.assertEqual({"dismissed": True, "withdrawn": True, "answeredAt": item["deliveredAt"]}, item["answer"])
        self.assertNotIn("snoozedUntil", item)

    def test_withdraws_an_open_question_and_sends_its_agent_nothing(self) -> None:
        self.run_cli("snooze", "1", "--session", "s1")
        self.assertEqual((0, "withdrew 1\n", ""), self.run_cli("withdraw", "1", "--session", "s1"))
        self.assert_withdrawn(self.item(1))
        self.assertEqual("", self.run_cli("watch", "--once", "--session", "s1")[1])
        self.assertEqual(["2\tnotice\topen\tForce-pushed #12"], self.run_cli("list", "--session", "s1")[1].splitlines())
        self.assertIn("1\tquestion\twithdrawn\tDecline it?", self.run_cli("list", "--all", "--session", "s1")[1])
        self.assertTrue(self.run_cli("show", "1")[1].endswith("\nwithdrawn\n"))

    def test_withdraws_an_open_notice_and_prints_it_as_json(self) -> None:
        code, out, err = self.run_cli("withdraw", "2", "--session", "s1", "--json")
        self.assertEqual(0, code, err)
        self.assertEqual(self.item(2), json.loads(out)["item"])
        self.assert_withdrawn(self.item(2))
        self.assertEqual(["1\tquestion\topen\tDecline it?"], self.run_cli("list", "--session", "s1")[1].splitlines())

    def test_refuses_what_dismiss_refuses_and_changes_nothing(self) -> None:
        self.run_cli("answer", "1.a", "--session", "s1")
        answered = self.item(1)
        code, out, err = self.run_cli("withdraw", "1", "--session", "s1")
        self.assertEqual((65, ""), (code, out))
        self.assertIn("already answered, as 1.a", err)
        code, out, err = self.run_cli("withdraw", "2.a", "--session", "s1")
        self.assertEqual((65, ""), (code, out))
        self.assertIn("2, without an answer letter", err)
        self.assertEqual(66, self.run_cli("withdraw", "3", "--session", "s1")[0])
        self.assertEqual(answered, self.item(1))
        self.assertNotIn("answer", self.item(2))
        self.assertEqual("ANSWER 1.a Decline it? → Decline it\n",
                         self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_refuses_an_item_already_withdrawn_or_dismissed_and_says_which(self) -> None:
        self.run_cli("withdraw", "1", "--session", "s1")
        self.run_cli("dismiss", "2", "--session", "s1")
        for ref, said in (("1", "1 is already withdrawn\n"), ("2", "2 is already dismissed\n")):
            for command in ("withdraw", "dismiss"):
                with self.subTest(ref=ref, command=command):
                    self.assertEqual((65, "", f"project: {said}"), self.run_cli(command, ref, "--session", "s1"))

    def test_answering_a_withdrawn_or_dismissed_question_says_which(self) -> None:
        self.ask("Rebase?", "--recommend", "Yes")
        self.run_cli("withdraw", "1", "--session", "s1")
        self.run_cli("dismiss", "3", "--session", "s1")
        for reference, said in (("1.a", "1 is already withdrawn\n"), ("3.a", "3 is already dismissed\n")):
            with self.subTest(reference=reference):
                self.assertEqual((65, "", f"project: {said}"), self.run_cli("answer", reference, "--session", "s1"))
        self.assertNotIn("letter", self.item(1)["answer"])
        self.assertNotIn("letter", self.item(3)["answer"])


class ReviseTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--detail", "Why.",
                 "--pr", "acme/app#12", "--agent", "agent-7")
        self.run_cli("tell", "Force-pushed #12", "--pr", "acme/app#12", "--session", "s1")

    def revise(self, *args: str) -> tuple[int, str, str]:
        return self.run_cli("revise", "1", "--session", "s1", *args)

    def test_replaces_an_open_questions_text_and_keeps_its_number(self) -> None:
        self.run_cli("snooze", "1", "--session", "s1")
        before = self.item(1)
        self.assertEqual((0, "revised 1\n", ""),
                         self.revise("--question", "Decline the retry finding?", "--recommend", " Decline it: capped ",
                                     "--option", "Add a budget per host", "--option", "Ask the reviewer",
                                     "--detail", "The cap is three in total."))
        after = self.item(1)
        self.assertEqual("Decline the retry finding?", after["subject"])
        self.assertEqual(["Decline it: capped", "Add a budget per host", "Ask the reviewer"], after["choices"])
        self.assertEqual("The cap is three in total.", after["detail"])
        for key in ("ref", "number", "kind", "pr", "postedAt", "agentId", "snoozedUntil"):
            with self.subTest(key=key):
                self.assertEqual(before[key], after[key])
        self.assertEqual({"last": 2}, json.loads((self.folder() / "count.json").read_text()))
        self.assertEqual("answered 1.c\n", self.run_cli("answer", "1.c", "--session", "s1")[1])
        self.assertEqual("Ask the reviewer", self.item(1)["answer"]["text"])

    def test_keeps_each_part_it_is_not_given(self) -> None:
        self.assertEqual(0, self.revise("--option", "Add a budget per host")[0])
        self.assertEqual(("Decline it?", ["Decline it", "Add a budget per host"], "Why."),
                         (self.item(1)["subject"], self.item(1)["choices"], self.item(1)["detail"]))
        self.assertEqual(0, self.revise("--detail", "")[0])
        self.assertEqual(("", ["Decline it", "Add a budget per host"]), (self.item(1)["detail"], self.item(1)["choices"]))
        self.assertEqual(0, self.revise("--no-options")[0])
        self.assertEqual(["Decline it"], self.item(1)["choices"])
        detail = self.scratch / "detail.md"
        detail.write_text("From a file.\n")
        self.assertEqual(0, self.revise("--detail-file", str(detail))[0])
        self.assertEqual("From a file.", self.item(1)["detail"])

    def test_prints_the_revised_question_as_json(self) -> None:
        code, out, err = self.revise("--question", "Decline the retry finding?", "--json")
        self.assertEqual(0, code, err)
        self.assertEqual(self.item(1), json.loads(out)["item"])

    def test_refuses_a_resolved_question_a_notice_and_an_unknown_number_and_changes_nothing(self) -> None:
        self.ask("Rebase?", "--recommend", "Yes")
        self.run_cli("answer", "1.a", "--session", "s1")
        self.run_cli("withdraw", "3", "--session", "s1")
        items = [self.item(ref) for ref in (1, 2, 3)]
        for ref, said in (("1", "1 is already answered, as 1.a"), ("3", "3 is already withdrawn"),
                          ("2", "2 is a notice, and only a question or a request can be revised")):
            with self.subTest(ref=ref):
                code, out, err = self.run_cli("revise", ref, "--detail", "x", "--session", "s1")
                self.assertEqual((65, ""), (code, out))
                self.assertIn(said, err)
        self.assertEqual(66, self.run_cli("revise", "4", "--detail", "x", "--session", "s1")[0])
        self.assertEqual(66, self.run_cli("revise", "4", "--detail", "x")[0])
        self.assertEqual([self.item(ref) for ref in (1, 2, 3)], items)

    def test_refuses_a_revision_that_fails_a_check_and_changes_nothing(self) -> None:
        before = self.item(1)
        for args in ([],
                     ["--question", "x" * 121],
                     ["--question", "Two\nlines?"],
                     ["--question", " "],
                     ["--recommend", "line one\nline two"],
                     ["--recommend", "Add a budget"],
                     ["--option", "Decline it"],
                     ["--option", " "],
                     ["--option", "x", "--option", " x "],
                     [arg for n in "abcdef" for arg in ("--option", n)]):
            with self.subTest(args=args[:2]):
                code, out, _ = self.revise(*args)
                self.assertEqual((65, ""), (code, out))
        self.assertEqual(65, self.run_cli("revise", "1.a", "--detail", "x", "--session", "s1")[0])
        with self.assertRaises(SystemExit) as stop, redirect_stderr(io.StringIO()):
            self.revise("--option", "x", "--no-options")
        self.assertEqual(2, stop.exception.code)
        self.assertEqual(before, self.item(1))

    def test_accepts_five_other_options_and_a_question_of_120_characters(self) -> None:
        options = [arg for n in "bcdef" for arg in ("--option", n)]
        self.assertEqual(0, self.revise("--question", "x" * 120, *options)[0])
        self.assertEqual(6, len(self.item(1)["choices"]))

    def test_two_revisions_at_once_both_land(self) -> None:
        self.assertEqual(["done", "done"],
                         at_once(lambda: inbox.revise("s1", "1", question="Decline the retry finding?"),
                                 lambda: inbox.revise("s1", "1", options=["Add a budget per host"])))
        self.assertEqual(("Decline the retry finding?", ["Decline it", "Add a budget per host"]),
                         (self.item(1)["subject"], self.item(1)["choices"]))

    def test_a_lock_another_process_holds_makes_a_revision_wait_then_exit_75(self) -> None:
        before = self.item(1)
        holder = self.hold_lock("wait")
        with mock.patch.object(inbox, "LOCK_WAIT_SECONDS", 0.3):
            code, _, err = self.revise("--question", "Decline the retry finding?")
        self.assertEqual(75, code)
        self.assertIn("another command holds", err)
        self.assertEqual(before, self.item(1))
        holder.stdin.close()
        self.assertEqual(0, holder.wait())
        self.assertEqual(0, self.revise("--question", "Decline the retry finding?")[0])


class SnoozeTests(InboxCase):
    NOW = 1_800_000_000_000

    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--pr", "acme/app#12")
        self.run_cli("tell", "Force-pushed #12", "--pr", "acme/app#12", "--session", "s1")
        clock = mock.patch.object(inbox, "now_ms", return_value=self.NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def states(self, *args: str) -> dict[str, str]:
        rows = (line.split("\t") for line in self.run_cli("list", "--session", "s1", *args)[1].splitlines())
        return {row[0]: row[2] for row in rows}

    def test_snoozes_an_item_for_an_hour_and_wakes_it_with_zero(self) -> None:
        later = time.strftime("%H:%M", time.localtime((self.NOW + 3_600_000) / 1000))
        self.assertEqual((0, f"1 snoozed until {later}\n", ""), self.run_cli("snooze", "1", "--session", "s1"))
        self.assertEqual({"1": f"snoozed until {later}", "2": "open"}, self.states())
        self.assertEqual(0, self.run_cli("snooze", "1", "--for", "0", "--session", "s1")[0])
        self.assertEqual({"1": "open", "2": "open"}, self.states())

    def test_wakes_an_item_by_ending_its_snooze_now(self) -> None:
        self.run_cli("snooze", "1", "--session", "s1")
        self.run_cli("snooze", "1", "--for", "0", "--session", "s1")
        self.assertEqual(self.NOW, self.item(1)["snoozedUntil"])

    def test_takes_the_minutes_and_returns_the_item_once_they_pass(self) -> None:
        self.run_cli("snooze", "2", "--for", "5", "--session", "s1")
        self.assertEqual(self.NOW + 300_000, self.item(2)["snoozedUntil"])
        with mock.patch.object(inbox, "now_ms", return_value=self.NOW + 300_000):
            self.assertEqual("open", self.states()["2"])

    def test_refuses_a_resolved_item_and_a_negative_time(self) -> None:
        self.run_cli("dismiss", "2", "--session", "s1")
        self.assertEqual(65, self.run_cli("snooze", "2", "--session", "s1")[0])
        self.assertEqual(65, self.run_cli("snooze", "1", "--for", "-1", "--session", "s1")[0])

    def test_refuses_a_snooze_longer_than_a_day_and_writes_nothing(self) -> None:
        for minutes in ("1441", "99999999999999"):
            with self.subTest(minutes=minutes):
                code, out, err = self.run_cli("snooze", "1", "--for", minutes, "--session", "s1")
                self.assertEqual((65, ""), (code, out))
                self.assertIn("1440", err)
        self.assertNotIn("snoozedUntil", self.item(1))
        self.assertEqual({"1": "open", "2": "open"}, self.states())

    def test_names_the_day_of_a_snooze_that_ends_after_today(self) -> None:
        tomorrow = time.strftime("%H:%M", time.localtime(self.NOW / 1000 + 86_400))
        self.assertEqual((0, f"1 snoozed until tomorrow {tomorrow}\n", ""),
                         self.run_cli("snooze", "1", "--for", "1440", "--session", "s1"))
        path = self.folder() / "2.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "snoozedUntil": self.NOW + 3 * 86_400_000}))
        later = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.NOW / 1000 + 3 * 86_400))
        self.assertEqual({"1": f"snoozed until tomorrow {tomorrow}", "2": f"snoozed until {later}"}, self.states())

    def test_prints_the_raw_time_of_a_snooze_too_far_off_to_show(self) -> None:
        path = self.folder() / "1.json"
        for until in (self.NOW + 10**18, 2**70):
            with self.subTest(until=until):
                path.write_text(json.dumps({**json.loads(path.read_text()), "snoozedUntil": until}))
                self.assertEqual({"1": f"snoozed until {until}", "2": "open"}, self.states())
                self.assertTrue(self.run_cli("show", "1")[1].endswith(f"\nsnoozed until {until}\n"))

    def test_answering_a_snoozed_question_clears_its_snooze(self) -> None:
        self.run_cli("snooze", "1", "--session", "s1")
        self.assertEqual(0, self.run_cli("answer", "1.a", "--session", "s1")[0])
        self.assertNotIn("snoozedUntil", self.item(1))

    def test_dismissing_a_snoozed_item_clears_its_snooze(self) -> None:
        for ref in ("1", "2"):
            self.run_cli("snooze", ref, "--session", "s1")
            self.assertEqual(0, self.run_cli("dismiss", ref, "--session", "s1")[0])
        self.assertEqual({"1": "dismissed", "2": "dismissed"}, self.states("--all"))
        self.assertEqual([], [key for ref in (1, 2) for key in ("snoozedUntil", "snoozedAt") if key in self.item(ref)])


class RestoreTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--detail", "Why.",
                 "--pr", "acme/app#12", "--agent", "agent-7")
        self.run_cli("tell", "Force-pushed #12", "--detail", "It held a token.", "--pr", "acme/app#12",
                     "--agent", "agent-7", "--session", "s1")

    def restore(self, ref: str = "1", *args: str) -> tuple[int, str, str]:
        return self.run_cli("restore", ref, "--session", "s1", *args)

    def watch(self) -> str:
        return self.run_cli("watch", "--once", "--session", "s1")[1]

    def test_restores_a_dismissed_question_in_place_and_cancels_its_undelivered_dismissal(self) -> None:
        before = self.item(1)
        self.run_cli("dismiss", "1", "--session", "s1")
        self.assertEqual((0, "restored 1\n", ""), self.restore())
        self.assertEqual(before, self.item(1))
        self.assertEqual({"last": 2}, json.loads((self.folder() / "count.json").read_text()))
        self.assertEqual("1\tquestion\topen\tDecline it?", self.run_cli("list", "--session", "s1")[1].splitlines()[0])
        self.assertEqual("", self.watch())
        self.assertEqual("answered 1.b\n", self.run_cli("answer", "1.b", "--session", "s1")[1])
        self.assertEqual("ANSWER 1.b Decline it? → Add a budget\n", self.watch())

    def test_restores_a_dismissed_notice_and_sends_nothing(self) -> None:
        before = self.item(2)
        self.run_cli("dismiss", "2", "--session", "s1")
        code, out, err = self.restore("2", "--json")
        self.assertEqual(0, code, err)
        self.assertEqual(before, json.loads(out)["item"])
        self.assertEqual(before, self.item(2))
        self.assertEqual("", self.watch())
        self.assertIn("2\tnotice\topen\tForce-pushed #12", self.run_cli("list", "--session", "s1")[1])

    def test_sends_restored_only_after_its_agent_heard_dismissed(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        self.assertEqual("DISMISSED 1 Decline it?\n", self.watch())
        self.assertEqual(0, self.restore()[0])
        restored = self.item(1)
        self.assertEqual([], [key for key in ("answer", "deliveredAt", "claim") if key in restored])
        self.assertIn("restoredAt", restored)
        self.assertEqual("1\tquestion\topen\tDecline it?", self.run_cli("list", "--session", "s1")[1].splitlines()[0])
        self.assertEqual("RESTORED 1 Decline it?\n", self.watch())
        self.assertEqual("", self.watch())
        self.assertNotIn("restoredAt", self.item(1))
        self.run_cli("answer", "1.a", "--session", "s1")
        self.assertEqual("ANSWER 1.a Decline it? → Decline it\n", self.watch())

    def test_a_restore_while_dismissed_is_being_delivered_sends_restored_after_it(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        token = self.run_cli("claim", "1", "--session", "s1")[1].split("\t")[0]
        self.assertEqual(0, self.restore()[0])
        self.assertNotIn("claim", self.item(1))
        self.assertEqual(65, self.run_cli("delivered", "1", "--claim", token, "--session", "s1")[0])
        self.assertEqual("RESTORED 1 Decline it?\n", self.watch())

    def test_watch_survives_a_restore_that_lands_while_its_line_goes_out(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        lines: list[str] = []

        def send(line: str) -> None:
            lines.append(line)
            if line.startswith("DISMISSED"):
                inbox.restore("s1", "1")

        inbox.deliver_pass("s1", send)
        inbox.deliver_pass("s1", send)
        self.assertEqual(["DISMISSED 1 Decline it?", "RESTORED 1 Decline it?"], lines)

    def test_an_answer_or_dismissal_before_restored_goes_out_replaces_it(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        self.watch()
        self.restore()
        self.run_cli("dismiss", "1", "--session", "s1")
        self.assertIn("deliveredAt", self.item(1))
        self.assertEqual("", self.watch())

        self.restore()
        token = self.run_cli("claim", "1", "--session", "s1")[1].split("\t")[0]
        self.run_cli("answer", "1.b", "--session", "s1")
        self.assertEqual(65, self.run_cli("delivered", "1", "--claim", token, "--session", "s1")[0])
        self.assertEqual("ANSWER 1.b Decline it? → Add a budget\n", self.watch())

    def test_dismissing_again_once_restored_is_claimed_sends_dismissed_after_it(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        self.watch()
        self.restore()
        token = self.run_cli("claim", "1", "--session", "s1")[1].split("\t")[0]
        self.run_cli("dismiss", "1", "--session", "s1")
        self.assertNotIn("deliveredAt", self.item(1))
        self.assertEqual(65, self.run_cli("delivered", "1", "--claim", token, "--session", "s1")[0])
        self.assertEqual("DISMISSED 1 Decline it?\n", self.watch())

    def test_a_restored_line_whose_delivery_failed_is_not_sent_again(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        self.watch()
        self.restore()
        token = self.run_cli("claim", "1", "--session", "s1")[1].split("\t")[0]
        self.assertEqual(0, self.run_cli("reopen", "1", "--claim", token, "--session", "s1")[0])
        self.assertEqual([], [key for key in ("answer", "restoredAt", "claim") if key in self.item(1)])
        self.assertEqual("", self.watch())

    def test_restores_a_snoozed_item_by_clearing_its_snooze(self) -> None:
        for ref in ("1", "2"):
            with self.subTest(ref=ref):
                before, started = self.item(ref), inbox.now_ms()
                self.run_cli("snooze", ref, "--session", "s1")
                self.assertTrue(started <= self.item(ref)["snoozedAt"] <= inbox.now_ms())
                self.assertEqual((0, f"restored {ref}\n", ""), self.restore(ref))
                self.assertEqual(before, self.item(ref))
        self.assertEqual("", self.watch())
        self.assertEqual(["1\tquestion\topen\tDecline it?", "2\tnotice\topen\tForce-pushed #12"],
                         self.run_cli("list", "--session", "s1")[1].splitlines())

    def test_refuses_an_open_answered_or_withdrawn_item_and_an_unknown_number(self) -> None:
        self.ask("Rebase?", "--recommend", "Yes")
        self.run_cli("answer", "1.a", "--session", "s1")
        self.run_cli("withdraw", "3", "--session", "s1")
        self.run_cli("snooze", "2", "--for", "0", "--session", "s1")
        items = [self.item(ref) for ref in (1, 2, 3)]
        for ref, said in (("1", "1 is already answered, as 1.a, and only a dismissed or snoozed item can be restored"),
                          ("2", "2 is open, so there is nothing to restore"),
                          ("3", "3 was withdrawn by the agent that posted it, so it is that agent's to post again")):
            with self.subTest(ref=ref):
                self.assertEqual((65, "", f"project: {said}\n"), self.restore(ref))
        self.assertEqual(66, self.restore("4")[0])
        self.assertEqual(66, self.run_cli("restore", "4")[0])
        self.assertEqual([self.item(ref) for ref in (1, 2, 3)], items)
        self.assertEqual("ANSWER 1.a Decline it? → Decline it\n", self.watch())

    def test_a_bare_number_looks_first_among_dismissed_and_snoozed_items(self) -> None:
        self.ask("Other?", "--recommend", "x", session="s2")
        self.run_cli("dismiss", "1", "--session", "s2")
        self.assertEqual((0, "restored 1\n", ""), self.run_cli("restore", "1"))
        self.assertNotIn("answer", self.item(1, "s2"))
        self.run_cli("snooze", "1", "--session", "s1")
        self.assertEqual((0, "restored 1\n", ""), self.run_cli("restore", "1"))
        self.assertNotIn("snoozedUntil", self.item(1))

    def test_two_restores_at_once_restore_once(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        self.assertEqual(["done", "refused"], at_once(lambda: inbox.restore("s1", "1"),
                                                      lambda: inbox.restore("s1", "1")))

    def test_a_lock_another_process_holds_makes_a_restore_wait_then_exit_75(self) -> None:
        self.run_cli("dismiss", "1", "--session", "s1")
        dismissed = self.item(1)
        holder = self.hold_lock("wait")
        with mock.patch.object(inbox, "LOCK_WAIT_SECONDS", 0.3):
            code, _, err = self.restore()
        self.assertEqual(75, code)
        self.assertIn("another command holds", err)
        self.assertEqual(dismissed, self.item(1))
        holder.stdin.close()
        self.assertEqual(0, holder.wait())
        self.assertEqual(0, self.restore()[0])


class RequestTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it")

    def request(self, subject: str = "Test the gutters", *args: str) -> str:
        code, out, err = self.run_cli("request", subject, "--session", "s1", *args)
        self.assertEqual(0, code, err)
        return out.strip()

    def watch(self) -> str:
        return self.run_cli("watch", "--once", "--session", "s1")[1]

    def test_numbers_a_request_in_the_count_every_kind_uses(self) -> None:
        steps = self.scratch / "steps.md"
        steps.write_text("1. Open Fyra.\n2. Hold a track.\n")
        self.assertEqual("2", self.request("Test the gutters", "--detail-file", str(steps), "--agent", "agent-7",
                                           "--pr", "acme/app#12"))
        request = self.item(2)
        self.assertEqual(("request", "Test the gutters", "1. Open Fyra.\n2. Hold a track.", "agent-7", "acme/app#12"),
                         (request["kind"], request["subject"], request["detail"], request["agentId"], request["pr"]))
        self.assertNotIn("choices", request)
        self.assertEqual("3", self.ask("Rebase?", "--recommend", "Yes"))

    def test_refuses_a_request_that_fails_a_check_and_writes_nothing(self) -> None:
        count = (self.folder() / "count.json").read_text()
        for args in (["Two\nlines"], ["x" * 121], [" "], ["Bad pull", "--pr", "12"]):
            with self.subTest(args=args[0][:12]):
                code, _, err = self.run_cli("request", *args, "--session", "s1")
                self.assertEqual(65, code, err)
                self.assertIn("request", err)
        self.assertEqual(count, (self.folder() / "count.json").read_text())

    def test_done_delivers_its_outcome_once(self) -> None:
        self.request()
        self.assertEqual((0, "done 2\n", ""), self.run_cli("done", "2", "--session", "s1"))
        self.assertEqual({"done": True}, {key: value for key, value in self.item(2)["answer"].items()
                                          if key != "answeredAt"})
        self.assertEqual("DONE 2 Test the gutters\n", self.watch())
        self.assertEqual("", self.watch())
        code, _, err = self.run_cli("done", "2", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("already done", err)

    def test_an_outcome_carries_the_persons_one_line(self) -> None:
        self.request()
        self.request("Sign in to TestFlight")
        self.assertEqual(0, self.run_cli("done", "2", "--text", " 4 didn't work ", "--session", "s1")[0])
        self.assertEqual(0, self.run_cli("decline", "3", "--text", "No phone today", "--session", "s1")[0])
        self.assertEqual("DONE 2 Test the gutters → 4 didn't work\nDECLINED 3 Sign in to TestFlight → No phone today\n",
                         self.watch())
        self.request("Run the migration")
        code, _, err = self.run_cli("done", "4", "--text", "two\nlines", "--session", "s1")
        self.assertEqual((65, "open"), (code, cli.item_state(self.item(4))))
        self.assertIn("one line", err)

    def test_declining_is_a_dismissal_that_restore_reopens(self) -> None:
        self.request()
        self.assertEqual((0, "declined 2\n", ""), self.run_cli("decline", "2", "--session", "s1"))
        answered = self.item(2)["answer"]
        self.assertEqual((True, True), (answered["dismissed"], answered["declined"]))
        self.assertNotIn("text", answered)
        self.assertEqual("DECLINED 2 Test the gutters\n", self.watch())
        self.assertEqual(0, self.run_cli("restore", "2", "--session", "s1")[0])
        self.assertEqual("open", cli.item_state(self.item(2)))
        self.assertEqual("RESTORED 2 Test the gutters\n", self.watch())

    def test_dismissing_a_request_declines_it(self) -> None:
        self.request()
        self.assertEqual(0, self.run_cli("dismiss", "2", "--session", "s1")[0])
        self.assertTrue(self.item(2)["answer"]["declined"])
        self.assertEqual("DECLINED 2 Test the gutters\n", self.watch())

    def test_a_withdrawn_request_sends_nothing(self) -> None:
        self.request()
        self.assertEqual(0, self.run_cli("withdraw", "2", "--session", "s1")[0])
        self.assertNotIn("declined", self.item(2)["answer"])
        self.assertEqual("", self.watch())

    def test_a_request_takes_no_answer_and_another_kind_no_outcome(self) -> None:
        self.request()
        code, _, err = self.run_cli("answer", "2.a", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("project inbox done", err)
        self.assertIn("project inbox decline", err)
        self.run_cli("tell", "Pushed", "--session", "s1")
        for args in (["done", "1"], ["decline", "1"], ["done", "3"], ["decline", "3"]):
            with self.subTest(args=args):
                code, _, err = self.run_cli(*args, "--session", "s1")
                self.assertEqual(65, code)
                self.assertIn("only a request", err)

    def test_restore_refuses_a_request_that_was_done(self) -> None:
        self.request()
        self.run_cli("done", "2", "--session", "s1")
        code, _, err = self.run_cli("restore", "2", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("already done", err)

    def test_revises_a_request_with_its_own_flags(self) -> None:
        self.request("Test the gutters", "--detail", "1. Open Fyra.")
        self.assertEqual(0, self.run_cli("revise", "2", "--request", "Test the new gutters", "--session", "s1")[0])
        self.assertEqual(("Test the new gutters", "1. Open Fyra."), (self.item(2)["subject"], self.item(2)["detail"]))
        self.assertEqual(0, self.run_cli("revise", "2", "--detail", "1. Open the new build.", "--session", "s1")[0])
        self.assertEqual("1. Open the new build.", self.item(2)["detail"])
        for args in (["--question", "Why?"], ["--recommend", "Yes"], ["--option", "No"], ["--request", "x" * 121]):
            with self.subTest(args=args[0]):
                code, _, err = self.run_cli("revise", "2", *args, "--session", "s1")
                self.assertEqual(65, code, err)
        code, _, err = self.run_cli("revise", "1", "--request", "Do it", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("--question", err)

    def test_lists_and_shows_a_request_by_its_kind_and_state(self) -> None:
        self.request("Test the gutters", "--detail", "1. Open Fyra.")
        self.request("Sign in")
        self.request("Run the migration")
        self.run_cli("done", "3", "--session", "s1")
        self.run_cli("decline", "4", "--session", "s1")
        self.assertEqual("2 Test the gutters\n\n1. Open Fyra.\n\nopen\n", self.run_cli("show", "2")[1])
        self.assertEqual(["1\tquestion\topen\tDecline it?", "2\trequest\topen\tTest the gutters",
                          "3\trequest\tdone\tSign in", "4\trequest\tdeclined\tRun the migration"],
                         self.run_cli("list", "--session", "s1")[1].splitlines())


if __name__ == "__main__":
    unittest.main()
