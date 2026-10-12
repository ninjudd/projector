"""The `project question` commands, against a temporary state directory.

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
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from projector import cli, questions


class QuestionCase(unittest.TestCase):
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
            code = cli.main(["question", *args])
        return code, out.getvalue(), err.getvalue()

    def ask(self, question: str = "Decline the finding?", *args: str, session: str = "s1",
            cwd: Path | None = None) -> str:
        code, out, err = self.run_cli("ask", question, "--session", session, *args, cwd=cwd)
        self.assertEqual(0, code, err)
        return out.strip()

    def folder(self, session: str = "s1") -> Path:
        return self.state / "projector" / "questions" / session


class AskTests(QuestionCase):
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
        self.assertCountEqual(["#271.1\topen\tAbout app?", "octo/web#271.1\topen\tAbout web?",
                               "#271.2\topen\tApp again?"], self.run_cli("list", "--session", "s1")[1].splitlines())

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
                     ["x" * 121, "--recommend", "x"],
                     ["Empty?", "--recommend", " "],
                     ["Many?", "--recommend", "a", *[f"--option={n}" for n in "bcdef"], "--option=g"],
                     ["Repeat?", "--recommend", "Same", "--option", "Same"],
                     ["Bad pull?", "--recommend", "x", "--pr", "app#12"]):
            with self.subTest(args=args[0][:12]):
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
        self.assertEqual("q1", json.loads(out)["question"]["ref"])


class AnswerTests(QuestionCase):
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
        outcomes: list[str] = []
        start = threading.Barrier(2)

        def answer(letter: str) -> None:
            start.wait()
            try:
                questions.answer("s1", "#337.1", letter)
                outcomes.append("recorded")
            except questions.QuestionError:
                outcomes.append("refused")

        threads = [threading.Thread(target=answer, args=(letter,)) for letter in "ab"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(["recorded", "refused"], sorted(outcomes))
        self.assertIn(json.loads((self.folder() / "%23337.1.json").read_text())["answer"]["letter"], "ab")

    def test_a_stale_lock_is_cleared(self) -> None:
        lock = self.folder() / ".lock"
        lock.mkdir()
        old = time.time() - questions.LOCK_STALE_SECONDS - 1
        os.utime(lock, (old, old))
        self.assertEqual(0, self.run_cli("answer", "#337.1.a", "--session", "s1")[0])


class DeliveryTests(QuestionCase):
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

    def test_renews_only_with_the_claims_token(self) -> None:
        _, token = self.claim()
        self.assertEqual(0, self.claim("--renew", token)[0])
        self.assertEqual(65, self.claim("--renew", "not-the-token")[0])

    def test_a_claim_not_renewed_for_a_minute_is_taken_by_a_new_claim(self) -> None:
        _, token = self.claim()
        later = questions.now_ms() + questions.CLAIM_EXPIRES_MS + 1
        with mock.patch.object(questions, "now_ms", return_value=later):
            code, fresh = self.claim()
            self.assertEqual(0, code)
            self.assertNotEqual(token, fresh)
            self.assertEqual(65, self.run_cli("delivered", "#337.1", "--claim", token, "--session", "s1")[0])
            self.assertEqual(0, self.run_cli("delivered", "#337.1", "--claim", fresh, "--session", "s1")[0])

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
            self.assertEqual(65, cli.main(["question", "watch", "--once", "--session", "s1"]))
        self.assertIn("\tdelivering #337.1.b\t", self.run_cli("list", "--session", "s1")[1])
        later = questions.now_ms() + questions.CLAIM_EXPIRES_MS + 1
        with mock.patch.object(questions, "now_ms", return_value=later):
            self.assertEqual("ANSWER #337.1.b Decline it? → Add a budget\n",
                             self.run_cli("watch", "--once", "--session", "s1")[1])

    def test_keeps_the_newest_delivered_questions(self) -> None:
        with mock.patch.object(questions, "KEEP_DELIVERED", 2):
            for number in range(3):
                ref = self.ask(f"Q{number}?", "--recommend", "x")
                self.run_cli("answer", f"{ref}.a", "--session", "s1")
                self.run_cli("watch", "--once", "--session", "s1")
        names = sorted(path.name for path in self.folder().glob("q*.json"))
        self.assertEqual(["q2.json", "q3.json"], names)


class ListTests(QuestionCase):
    def test_lists_every_sessions_open_questions_without_a_session(self) -> None:
        self.ask("One?", "--recommend", "x", session="s1")
        self.ask("Two?", "--recommend", "x", session="s2")
        out = self.run_cli("list")[1]
        self.assertEqual(["s1\tq1\topen\tOne?", "s2\tq1\topen\tTwo?"], out.splitlines())

    def test_skips_a_file_that_is_not_a_question(self) -> None:
        self.ask("One?", "--recommend", "x")
        (self.folder() / "q7.json").write_text("{not json")
        (self.folder() / "misnamed.json").write_text(json.dumps({"ref": "q8", "question": "?"}))
        self.assertEqual(["q1\topen\tOne?"], self.run_cli("list", "--session", "s1")[1].splitlines())

    def test_show_prints_the_lettered_answers(self) -> None:
        self.ask("Decline it?", "--recommend", "Decline it", "--option", "Add a budget")
        out = self.run_cli("show", "q1")[1]
        self.assertIn("a. Decline it (recommended)\nb. Add a budget\nc. type an answer with --text\nopen", out)


class ContextTests(QuestionCase):
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
        self.assertEqual([("#337.3", "b")], questions.references_in("Go with #337.3.b."))
        self.assertEqual([("octo/app#337.3", "b")], questions.references_in("octo/app#337.3.b"))
        self.assertEqual([("q2", "a")], questions.references_in("Let's do q2.a."))
        for text in ("#337", "#337.3.4", "x#337.3", "2", "Q2", "eq2", "q2.4"):
            with self.subTest(text=text):
                self.assertEqual([], questions.references_in(text))


if __name__ == "__main__":
    unittest.main()
