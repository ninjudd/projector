"""The `project inbox` commands, against a temporary state directory.

Each test points `XDG_STATE_HOME` at its own directory and runs the CLI from a temporary
Git checkout whose origin is `acme/app`, so a question about `acme/app` takes the short
`#<number>.<count>` form and one about any other repository spells the repository out.
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
from urllib.parse import quote
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
        self.checkout = self.repository("app", "acme/app")
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

    def run_cli(self, *args: str, stdin: str = "", cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.chdir(cwd or self.checkout), mock.patch("sys.stdin", io.StringIO(stdin)), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["inbox", *args])
        return code, out.getvalue(), err.getvalue()

    def ask(self, question: str = "Decline the finding?", *args: str, session: str = "s1",
            cwd: Path | None = None) -> str:
        code, out, err = self.run_cli("ask", question, "--session", session, *args, cwd=cwd)
        self.assertEqual(0, code, err)
        return out.strip()

    def folder(self, session: str = "s1") -> Path:
        return self.state / "projector" / "inbox" / session


class AskTests(InboxCase):
    def test_numbers_each_pull_request_and_the_session_count_apart(self) -> None:
        recommend = ["--recommend", "Decline it"]
        self.assertEqual("#337.1", self.ask("First?", *recommend, "--pr", "acme/app#337"))
        self.assertEqual("#337.2", self.ask("Second?", *recommend, "--pr", "ACME/App#337"))
        self.assertEqual("#12.1", self.ask("Other pull?", *recommend, "--pr", "acme/app#12"))
        self.assertEqual("octo/web#337.1", self.ask("Elsewhere?", *recommend, "--pr", "octo/web#337"))
        self.assertEqual("q1", self.ask("No pull?", *recommend))
        self.assertEqual("q2", self.ask("Another?", *recommend))
        self.assertEqual("q1", self.ask("Other session?", *recommend, session="s2"))

    def test_shortens_only_the_repository_of_the_sessions_first_ask(self) -> None:
        web = self.repository("web", "octo/web")
        recommend = ["--recommend", "x"]
        self.assertEqual("#271.1", self.ask("About app?", *recommend, "--pr", "acme/app#271"))
        self.assertEqual("octo/web#271.1", self.ask("About web?", *recommend, "--pr", "octo/web#271", cwd=web))
        self.assertEqual("#271.2", self.ask("App again?", *recommend, "--pr", "acme/app#271", cwd=web))
        self.assertCountEqual(["#271.1\tquestion\topen\tAbout app?", "octo/web#271.1\tquestion\topen\tAbout web?",
                               "#271.2\tquestion\topen\tApp again?"], self.run_cli("list", "--session", "s1")[1].splitlines())

    def test_letters_the_recommendation_a_and_records_the_question(self) -> None:
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--detail", "Why.",
                 "--pr", "acme/app#337", "--agent", "agent-7")
        question = json.loads((self.folder() / "%23337.1.json").read_text())
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
        self.assertFalse((self.folder() / "counts.json").exists())

    def test_accepts_five_other_options_and_a_question_of_120_characters(self) -> None:
        self.assertEqual("q1", self.ask("x" * 120, "--recommend", "a", *[f"--option={n}" for n in "bcdef"]))

    def test_needs_a_session_and_takes_it_from_the_environment(self) -> None:
        code, _, err = self.run_cli("ask", "Who?", "--recommend", "x")
        self.assertEqual(2, code, err)
        with mock.patch.dict(os.environ, {"PROJECTOR_SESSION": "s9"}):
            code, out, _ = self.run_cli("ask", "Who?", "--recommend", "x")
        self.assertEqual((0, "q1"), (code, out.strip()))
        self.assertTrue((self.folder("s9") / "q1.json").exists())

    def test_refuses_a_session_id_that_is_not_a_plain_name(self) -> None:
        code, _, _ = self.run_cli("ask", "Where?", "--recommend", "x", "--session", "../escape")
        self.assertEqual(65, code)

    def test_prints_the_question_as_json(self) -> None:
        code, out, _ = self.run_cli("ask", "Json?", "--recommend", "x", "--session", "s1", "--json")
        self.assertEqual(0, code)
        self.assertEqual("q1", json.loads(out)["item"]["ref"])

    def test_records_the_question_in_the_inbox_store(self) -> None:
        self.ask("Where?", "--recommend", "x")
        item = json.loads((self.folder() / "q1.json").read_text())
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
        self.assertEqual((0, "answered #337.1.a\n", ""), self.run_cli("answer", "#337.1.a", "--session", "s1"))
        code, _, err = self.run_cli("answer", "#337.1.b", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("already answered, as #337.1.a", err)

    def test_records_a_typed_answer_under_the_last_letter(self) -> None:
        code, _, _ = self.run_cli("answer", "#337.1.c", "--text", "keep the cap", "--session", "s1")
        self.assertEqual(0, code)
        question = json.loads((self.folder() / "%23337.1.json").read_text())
        self.assertEqual({"letter": "c", "text": "keep the cap"},
                         {key: question["answer"][key] for key in ("letter", "text")})

    def test_refuses_a_letter_the_question_does_not_have(self) -> None:
        for args in (["#337.1.d"], ["#337.1.c"], ["#337.1.a", "--text", "x"], ["#337.1"]):
            with self.subTest(args=args):
                self.assertEqual(65, self.run_cli("answer", *args, "--session", "s1")[0])

    def test_refuses_a_typed_answer_of_more_than_one_line(self) -> None:
        code, _, err = self.run_cli("answer", "#337.1.c", "--text", "line one\nline two", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("one line", err)
        self.assertNotIn("answer", json.loads((self.folder() / "%23337.1.json").read_text()))

    def test_refuses_an_answer_letter_on_every_other_command(self) -> None:
        for args in (["show", "#337.1.a"], ["dismiss", "#337.1.a"], ["snooze", "#337.1.a"], ["claim", "#337.1.a"],
                     ["delivered", "#337.1.a", "--claim", "x"], ["reopen", "#337.1.a", "--claim", "x"]):
            with self.subTest(command=args[0]):
                code, out, err = self.run_cli(*args, "--session", "s1")
                self.assertEqual((65, ""), (code, out))
                self.assertIn("#337.1, without an answer letter", err)
        question = json.loads((self.folder() / "%23337.1.json").read_text())
        self.assertEqual([], [key for key in ("answer", "snoozedUntil", "claim") if key in question])

    def test_finds_the_session_without_one_and_refuses_an_ambiguous_reference(self) -> None:
        self.assertEqual(0, self.run_cli("answer", "#337.1.a")[0])
        self.ask("Same reference?", "--recommend", "x", "--pr", "acme/app#337", session="s2")
        self.ask("Again?", "--recommend", "x", "--pr", "acme/app#338", session="s2")
        self.ask("Again?", "--recommend", "x", "--pr", "acme/app#338", session="s3")
        code, _, err = self.run_cli("answer", "#338.1.a")
        self.assertEqual(67, code)
        self.assertIn("s2, s3", err)

    def test_an_unknown_reference_exits_66(self) -> None:
        self.assertEqual(66, self.run_cli("answer", "#999.1.a")[0])
        self.assertEqual(66, self.run_cli("answer", "q4.a", "--session", "s1")[0])

    def test_dismisses_once(self) -> None:
        self.assertEqual((0, "dismissed #337.1\n", ""), self.run_cli("dismiss", "#337.1", "--session", "s1"))
        self.assertEqual(65, self.run_cli("answer", "#337.1.a", "--session", "s1")[0])

    def test_two_answers_at_once_record_one(self) -> None:
        self.assertEqual(["done", "refused"], at_once(lambda: inbox.answer("s1", "#337.1", "a"),
                                                      lambda: inbox.answer("s1", "#337.1", "b")))
        self.assertIn(json.loads((self.folder() / "%23337.1.json").read_text())["answer"]["letter"], "ab")

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

    def test_a_lock_another_process_holds_makes_a_command_wait_then_exit_75(self) -> None:
        holder = self.hold_lock("wait")
        with mock.patch.object(inbox, "LOCK_WAIT_SECONDS", 0.3):
            started = time.monotonic()
            code, _, err = self.run_cli("answer", "#337.1.a", "--session", "s1")
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual(75, code)
        self.assertIn("another command holds", err)
        holder.stdin.close()
        self.assertEqual(0, holder.wait())
        self.assertEqual(0, self.run_cli("answer", "#337.1.a", "--session", "s1")[0])

    def test_a_holder_that_exits_without_unlocking_leaves_the_lock_free_at_once(self) -> None:
        holder = self.hold_lock("exit")
        self.assertEqual(0, holder.wait())
        with mock.patch.object(inbox, "LOCK_WAIT_SECONDS", 0):
            self.assertEqual(0, self.run_cli("answer", "#337.1.a", "--session", "s1")[0])


class DeliveryTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--pr", "acme/app#337")
        self.run_cli("answer", "#337.1.b", "--session", "s1")

    def claim(self, *args: str) -> tuple[int, str]:
        code, out, _ = self.run_cli("claim", "#337.1", "--session", "s1", *args)
        return code, out.split("\t")[0]

    def test_claims_once_and_prints_the_text_to_deliver(self) -> None:
        code, out, _ = self.run_cli("claim", "#337.1", "--session", "s1")
        self.assertEqual(0, code)
        self.assertTrue(out.endswith("\tANSWER #337.1.b Decline it? → Add a budget\n"), out)
        self.assertEqual(65, self.claim()[0])

    def test_two_claims_at_once_take_one(self) -> None:
        self.assertEqual(["done", "refused"], at_once(lambda: inbox.claim("s1", "#337.1"),
                                                      lambda: inbox.claim("s1", "#337.1")))

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
            self.assertEqual(65, self.run_cli("delivered", "#337.1", "--claim", token, "--session", "s1")[0])
            self.assertEqual(0, self.run_cli("delivered", "#337.1", "--claim", fresh, "--session", "s1")[0])

    def test_a_lapsed_claims_token_settles_it_until_a_new_claim_replaces_it(self) -> None:
        _, token = self.claim()
        later = inbox.now_ms() + inbox.CLAIM_EXPIRES_MS + 1
        with mock.patch.object(inbox, "now_ms", return_value=later):
            self.assertEqual(0, self.claim("--renew", token)[0])
        with mock.patch.object(inbox, "now_ms", return_value=later + inbox.CLAIM_EXPIRES_MS + 1):
            self.assertEqual(0, self.run_cli("delivered", "#337.1", "--claim", token, "--session", "s1")[0])

    def test_delivered_hides_the_question_from_the_open_list(self) -> None:
        _, token = self.claim()
        self.assertEqual(0, self.run_cli("delivered", "#337.1", "--claim", token, "--session", "s1")[0])
        self.assertEqual("", self.run_cli("list", "--session", "s1")[1])
        self.assertIn("delivered #337.1.b", self.run_cli("list", "--all", "--session", "s1")[1])
        self.assertEqual(65, self.claim()[0])

    def test_reopen_clears_the_answer(self) -> None:
        _, token = self.claim()
        self.assertEqual(0, self.run_cli("reopen", "#337.1", "--claim", token, "--session", "s1")[0])
        self.assertIn("\topen\t", self.run_cli("list", "--session", "s1")[1])
        self.assertEqual(0, self.run_cli("answer", "#337.1.a", "--session", "s1")[0])

    def test_watch_delivers_each_answer_once_and_skips_a_live_claim(self) -> None:
        self.ask("Run init?", "--recommend", "Yes")
        self.run_cli("dismiss", "q1", "--session", "s1")
        self.assertEqual(0, self.claim()[0])
        code, out, _ = self.run_cli("watch", "--once", "--session", "s1")
        self.assertEqual((0, "DISMISSED q1 Run init?\n"), (code, out))
        self.assertEqual("", self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_watch_leaves_an_answer_it_could_not_print_to_the_next_deliverer(self) -> None:
        class ClosedPipe(io.StringIO):
            def write(self, text: str) -> int:
                raise BrokenPipeError(32, "Broken pipe")

        with contextlib.chdir(self.checkout), redirect_stdout(ClosedPipe()), redirect_stderr(io.StringIO()):
            self.assertEqual(65, cli.main(["inbox", "watch", "--once", "--session", "s1"]))
        self.assertIn("\tdelivering #337.1.b\t", self.run_cli("list", "--session", "s1")[1])
        later = inbox.now_ms() + inbox.CLAIM_EXPIRES_MS + 1
        with mock.patch.object(inbox, "now_ms", return_value=later):
            self.assertEqual("ANSWER #337.1.b Decline it? → Add a budget\n",
                             self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_watch_refuses_an_interval_that_is_not_a_positive_number(self) -> None:
        for interval in ("-1", "0", "nan", "inf", "soon"):
            with self.subTest(interval=interval):
                err = io.StringIO()
                with self.assertRaises(SystemExit) as stop, redirect_stderr(err):
                    cli.main(["inbox", "watch", "--interval", interval, "--once", "--session", "s1"])
                self.assertEqual(2, stop.exception.code)
                self.assertIn("--interval", err.getvalue())

    def test_keeps_the_newest_delivered_questions(self) -> None:
        with mock.patch.object(inbox, "KEEP_DELIVERED", 2):
            for number in range(3):
                ref = self.ask(f"Q{number}?", "--recommend", "x")
                self.run_cli("answer", f"{ref}.a", "--session", "s1")
                self.run_cli("watch", "--once", "--session", "s1")
        names = sorted(path.name for path in self.folder().glob("q*.json"))
        self.assertEqual(["q2.json", "q3.json"], names)


class ListTests(InboxCase):
    def test_lists_every_sessions_open_questions_without_a_session(self) -> None:
        self.ask("One?", "--recommend", "x", session="s1")
        self.ask("Two?", "--recommend", "x", session="s2")
        out = self.run_cli("list")[1]
        self.assertEqual(["s1\tq1\tquestion\topen\tOne?", "s2\tq1\tquestion\topen\tTwo?"], out.splitlines())

    def test_skips_a_file_that_is_not_an_item(self) -> None:
        self.ask("One?", "--recommend", "x")
        (self.folder() / "q7.json").write_text("{not json")
        (self.folder() / "misnamed.json").write_text(json.dumps({"ref": "q8", "subject": "?"}))
        self.assertEqual(["q1\tquestion\topen\tOne?"], self.run_cli("list", "--session", "s1")[1].splitlines())

    def test_every_command_refuses_a_file_that_is_not_an_item(self) -> None:
        self.ask("One?", "--recommend", "x")
        (self.folder() / "q7.json").write_text(json.dumps({"ref": "q7", "subject": "No kind?"}))
        (self.folder() / "q8.json").write_text(json.dumps({"ref": "q9", "kind": "notice", "subject": "Misnamed?"}))
        for ref in ("q7", "q8"):
            for args in (["show", ref], ["answer", f"{ref}.a"], ["dismiss", ref], ["snooze", ref]):
                with self.subTest(args=args):
                    code, _, err = self.run_cli(*args, "--session", "s1")
                    self.assertEqual(66, code)
                    self.assertIn("is not an inbox item", err)
            with self.subTest(context=ref):
                self.assertEqual((0, ""), self.run_cli("context", "--session", "s1", stdin=f"see {ref}")[:2])
        self.assertEqual({"ref": "q7", "subject": "No kind?"}, json.loads((self.folder() / "q7.json").read_text()))

    def test_lists_every_session_past_a_directory_that_is_not_one(self) -> None:
        self.ask("One?", "--recommend", "x")
        (self.folder().parent / "not a session").mkdir()
        self.assertEqual((0, "s1\tq1\tquestion\topen\tOne?\n", ""), self.run_cli("list"))

    def test_lists_and_shows_a_dismissed_question_as_dismissed(self) -> None:
        self.ask("One?", "--recommend", "x")
        self.run_cli("dismiss", "q1", "--session", "s1")
        self.assertEqual(["q1\tquestion\tdismissed\tOne?"], self.run_cli("list", "--session", "s1")[1].splitlines())
        self.assertEqual("DISMISSED q1 One?\n", self.run_cli("watch", "--once", "--session", "s1")[1])
        self.assertEqual(["q1\tquestion\tdismissed\tOne?"],
                         self.run_cli("list", "--all", "--session", "s1")[1].splitlines())
        self.assertTrue(self.run_cli("show", "q1")[1].endswith("\ndismissed\n"))

    def test_show_prints_the_lettered_answers(self) -> None:
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget")
        out = self.run_cli("show", "q1")[1]
        self.assertIn("a. Decline it (recommended)\nb. Add a budget\nc. type an answer with --text\nopen", out)


class ContextTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget", "--pr", "acme/app#337")
        self.ask("Run init?", "--recommend", "Yes")
        self.ask("Rebase?", "--recommend", "Yes")
        self.ask("Other?", "--recommend", "Yes", "--pr", "octo/web#337")

    def context(self, text: str) -> list[str]:
        code, out, _ = self.run_cli("context", "--session", "s1", stdin=text)
        self.assertEqual(0, code)
        return out.splitlines()

    def test_matches_each_form_standing_alone(self) -> None:
        self.assertEqual(["#337.1.b answers Decline it? with Add a budget"], self.context("Go with #337.1.b."))
        self.assertEqual(["q2.a answers Rebase? with Yes"], self.context("Let's do q2.a."))
        self.assertEqual(["octo/web#337.1 is the question: Other?"], self.context("see octo/web#337.1, then"))
        self.assertEqual(["q1 is the question: Run init?", "q2 is the question: Rebase?"],
                         self.context("q1 then q2\nand q1 again"))

    def test_leaves_text_that_only_looks_like_a_reference(self) -> None:
        for text in ("#337", "#337.1.4", "x#337.1", "web#337.1", "rerun the 2 failing tests", "Q2", "eq2", "q2.4",
                     "#337.1.z", "q9"):
            with self.subTest(text=text):
                self.assertEqual([], self.context(text))

    def test_finds_references_in_text_by_the_plans_table(self) -> None:
        self.assertEqual([("#337.3", "b")], inbox.references_in("Go with #337.3.b."))
        self.assertEqual([("octo/app#337.3", "b")], inbox.references_in("octo/app#337.3.b"))
        self.assertEqual([("q2", "a")], inbox.references_in("Let's do q2.a."))
        for text in ("#337", "#337.3.4", "x#337.3", "2", "Q2", "eq2", "q2.4"):
            with self.subTest(text=text):
                self.assertEqual([], inbox.references_in(text))


class NoticeTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--pr", "acme/app#12")

    def tell(self, notice: str = "Force-pushed #12 to drop a stray file", *args: str) -> str:
        code, out, err = self.run_cli("tell", notice, "--pr", "acme/app#12", "--session", "s1", *args)
        self.assertEqual(0, code, err)
        return out.strip()

    def item(self, ref: str) -> dict:
        return json.loads((self.folder() / f"{quote(ref, safe='')}.json").read_text())

    def test_numbers_a_notice_in_the_counts_questions_use(self) -> None:
        self.assertEqual("#12.2", self.tell())
        notice = self.item("#12.2")
        self.assertEqual(("notice", "Force-pushed #12 to drop a stray file"), (notice["kind"], notice["subject"]))
        self.assertNotIn("choices", notice)
        self.assertEqual("#12.3", self.ask("Rebase?", "--recommend", "Yes", "--pr", "acme/app#12"))
        code, out, _ = self.run_cli("tell", "No pull", "--session", "s1", "--json")
        self.assertEqual((0, "q1"), (code, json.loads(out)["item"]["ref"]))

    def test_records_the_detail_and_the_posting_subagent(self) -> None:
        detail = self.scratch / "detail.md"
        detail.write_text("The file held a token.\n")
        self.tell("Dropped a file", "--detail-file", str(detail), "--agent", "agent-7")
        notice = self.item("#12.2")
        self.assertEqual(("The file held a token.", "agent-7"), (notice["detail"], notice["agentId"]))

    def test_refuses_a_notice_that_fails_a_check_and_writes_nothing(self) -> None:
        counts = (self.folder() / "counts.json").read_text()
        for args in (["Two\nlines"], ["A\ttab"], ["x" * 121], [" "], ["Bad pull", "--pr", "12"]):
            with self.subTest(args=args[0][:12]):
                code, _, err = self.run_cli("tell", *args, "--session", "s1")
                self.assertEqual(65, code, err)
        self.assertEqual(counts, (self.folder() / "counts.json").read_text())
        self.assertEqual(["#12.1"], [item["ref"] for item in inbox.load_items(self.folder())])

    def test_dismissing_a_notice_delivers_it_at_once(self) -> None:
        self.tell()
        self.assertEqual((0, "dismissed #12.2\n", ""), self.run_cli("dismiss", "#12.2", "--session", "s1"))
        notice = self.item("#12.2")
        self.assertTrue(notice["answer"]["dismissed"])
        self.assertEqual(notice["answer"]["answeredAt"], notice["deliveredAt"])
        code, _, err = self.run_cli("dismiss", "#12.2", "--session", "s1")
        self.assertEqual(65, code)
        self.assertIn("already dismissed", err)
        self.assertEqual("", self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_dismissing_a_question_still_waits_for_its_deliverer(self) -> None:
        self.run_cli("dismiss", "#12.1", "--session", "s1")
        self.assertNotIn("deliveredAt", self.item("#12.1"))
        self.assertEqual("DISMISSED #12.1 Decline it?\n", self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_a_notice_takes_no_answer_and_no_claim(self) -> None:
        self.tell()
        for args in (["answer", "#12.2.a"], ["claim", "#12.2"]):
            with self.subTest(command=args[0]):
                code, _, err = self.run_cli(*args, "--session", "s1")
                self.assertEqual(65, code)
                self.assertIn("project inbox dismiss", err)

    def test_lists_and_shows_a_notice_by_its_kind(self) -> None:
        self.tell("Dropped a file", "--detail", "It held a token.")
        self.assertEqual(["#12.1\tquestion\topen\tDecline it?", "#12.2\tnotice\topen\tDropped a file"],
                         self.run_cli("list", "--session", "s1")[1].splitlines())
        self.assertEqual("#12.2 Dropped a file\nabout acme/app#12\n\nIt held a token.\n\nopen\n",
                         self.run_cli("show", "#12.2")[1])
        self.run_cli("dismiss", "#12.2", "--session", "s1")
        self.assertEqual(["#12.1\tquestion\topen\tDecline it?"], self.run_cli("list", "--session", "s1")[1].splitlines())
        self.assertIn("#12.2\tnotice\tdismissed\tDropped a file", self.run_cli("list", "--all", "--session", "s1")[1])


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
        self.assertEqual((0, f"#12.1 snoozed until {later}\n", ""), self.run_cli("snooze", "#12.1", "--session", "s1"))
        self.assertEqual({"#12.1": f"snoozed until {later}", "#12.2": "open"}, self.states())
        self.assertEqual(0, self.run_cli("snooze", "#12.1", "--for", "0", "--session", "s1")[0])
        self.assertEqual({"#12.1": "open", "#12.2": "open"}, self.states())

    def test_wakes_an_item_by_ending_its_snooze_now(self) -> None:
        self.run_cli("snooze", "#12.1", "--session", "s1")
        self.run_cli("snooze", "#12.1", "--for", "0", "--session", "s1")
        self.assertEqual(self.NOW, json.loads((self.folder() / "%2312.1.json").read_text())["snoozedUntil"])

    def test_takes_the_minutes_and_returns_the_item_once_they_pass(self) -> None:
        self.run_cli("snooze", "#12.2", "--for", "5", "--session", "s1")
        self.assertEqual(self.NOW + 300_000, json.loads((self.folder() / "%2312.2.json").read_text())["snoozedUntil"])
        with mock.patch.object(inbox, "now_ms", return_value=self.NOW + 300_000):
            self.assertEqual("open", self.states()["#12.2"])

    def test_refuses_a_resolved_item_and_a_negative_time(self) -> None:
        self.run_cli("dismiss", "#12.2", "--session", "s1")
        self.assertEqual(65, self.run_cli("snooze", "#12.2", "--session", "s1")[0])
        self.assertEqual(65, self.run_cli("snooze", "#12.1", "--for", "-1", "--session", "s1")[0])

    def test_refuses_a_snooze_longer_than_a_day_and_writes_nothing(self) -> None:
        for minutes in ("1441", "99999999999999"):
            with self.subTest(minutes=minutes):
                code, out, err = self.run_cli("snooze", "#12.1", "--for", minutes, "--session", "s1")
                self.assertEqual((65, ""), (code, out))
                self.assertIn("1440", err)
        self.assertNotIn("snoozedUntil", json.loads((self.folder() / "%2312.1.json").read_text()))
        self.assertEqual({"#12.1": "open", "#12.2": "open"}, self.states())

    def test_names_the_day_of_a_snooze_that_ends_after_today(self) -> None:
        tomorrow = time.strftime("%H:%M", time.localtime(self.NOW / 1000 + 86_400))
        self.assertEqual((0, f"#12.1 snoozed until tomorrow {tomorrow}\n", ""),
                         self.run_cli("snooze", "#12.1", "--for", "1440", "--session", "s1"))
        path = self.folder() / "%2312.2.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "snoozedUntil": self.NOW + 3 * 86_400_000}))
        later = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.NOW / 1000 + 3 * 86_400))
        self.assertEqual({"#12.1": f"snoozed until tomorrow {tomorrow}", "#12.2": f"snoozed until {later}"},
                         self.states())

    def test_answering_a_snoozed_question_clears_its_snooze(self) -> None:
        self.run_cli("snooze", "#12.1", "--session", "s1")
        self.assertEqual(0, self.run_cli("answer", "#12.1.a", "--session", "s1")[0])
        self.assertNotIn("snoozedUntil", json.loads((self.folder() / "%2312.1.json").read_text()))

    def test_dismissing_a_snoozed_item_clears_its_snooze(self) -> None:
        for ref in ("#12.1", "#12.2"):
            self.run_cli("snooze", ref, "--session", "s1")
            self.assertEqual(0, self.run_cli("dismiss", ref, "--session", "s1")[0])
        self.assertEqual({"#12.1": "dismissed", "#12.2": "dismissed"}, self.states("--all"))


class NoticeContextTests(InboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask("Decline it?", "--recommend", "Decline it", "--pr", "acme/app#12")
        self.run_cli("tell", "Force-pushed #12 to drop a stray file", "--pr", "acme/app#12", "--session", "s1")

    def test_names_a_notice_and_gives_no_answer_for_its_letter(self) -> None:
        self.assertEqual((0, "#12.2 is the notice: Force-pushed #12 to drop a stray file\n", ""),
                         self.run_cli("context", "--session", "s1", stdin="see #12.2"))
        self.assertEqual((0, "", ""), self.run_cli("context", "--session", "s1", stdin="see #12.2.a"))


if __name__ == "__main__":
    unittest.main()
