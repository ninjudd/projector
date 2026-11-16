from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from projector import cli, codex_watch as codex, watch
from projector.core import UsageError
from projector.inbox import locked, read_json, write_json


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex watch ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = watch.session_folder(self.root, "session-A")
        self.folder.mkdir(parents=True)
        self.now = 1000.0
        self.executable = self.root / "codex"
        self.executable.write_text(f"#!{sys.executable}\n" + '''import json, pathlib, sys
root = pathlib.Path(__file__).parent
if sys.argv[1:] == ["queue", "--help"]:
    print("--thread --message --resume")
else:
    with (root / "calls.jsonl").open("a") as stream:
        stream.write(json.dumps({"args": sys.argv[1:], "cwd": str(pathlib.Path.cwd())}) + "\\n")
    if (root / "warning").exists():
        print("could not resume; a private prompt must not appear in diagnostics", file=sys.stderr)
    if (root / "fail").exists():
        sys.exit(1)
''')
        self.executable.chmod(0o755)
        self.patch = mock.patch("shutil.which", return_value=str(self.executable))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        codex.register(self.folder, "session-A", str(self.root), self.now)
        watch.add(self.root, "session-A", watch.Subscription("Acme/App", 14, "fix"))
        self.dispatcher = codex.Dispatcher(wall=lambda: self.now)
        self.addCleanup(self.dispatcher.close)

    def event(self, text="FINDING Acme/App#14 src/x.py:3 — run fix-pr", revision="a"):
        return codex.Event(text, ["acme/app", 14, "fix", "thread-T", revision])

    def enqueue(self, *events):
        watch.append_events(self.folder, list(events or [self.event()]))

    def prepare(self):
        with locked(self.folder):
            return codex.prepare(self.folder, self.now)

    def send(self):
        batch = self.prepare()
        self.dispatcher.send(self.folder, batch["batch_id"])
        return batch

    def tick(self):
        self.dispatcher.tick([self.folder])
        for job in list(self.dispatcher.jobs.values()):
            job.result()

    def calls(self):
        path = self.root / "calls.jsonl"
        return path.read_text().splitlines() if path.exists() else []

    def hook(self, name, transcript=None, **fields):
        event = {"hook_event_name": name, "session_id": "session-A", "cwd": str(self.root), **fields}
        if transcript is not None:
            event["transcript_path"] = str(transcript)
        return codex.hook(self.root, event, self.now, mock.Mock())

    def transcript(self, folder="sessions"):
        path = self.root / "codex-home" / folder / "rollout-2026-11-15T12-50-37-session-A.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_idle_unload_keeps_delivery_and_the_next_batch_resumes_the_thread(self):
        self.hook("SessionEnd", self.transcript(), reason="other")
        self.assertEqual("codex", watch.delivery(self.root, "session-A", self.now))
        self.assertEqual(self.now, codex.report(self.folder, self.now)["unloaded_at"])
        self.enqueue()
        self.tick()
        self.assertEqual(["--resume"], json.loads(self.calls()[0])["args"][-1:])
        batch = codex.pending(self.folder)[0][1]
        self.hook("UserPromptSubmit", prompt=codex.message("session-A", batch))
        self.assertEqual([], codex.pending(self.folder))
        self.assertIsNone(codex.report(self.folder, self.now)["unloaded_at"])

    def test_archived_conversation_lapses_at_once_until_resumed(self):
        transcript = self.transcript()
        self.hook("SessionStart", transcript, source="startup")
        self.hook("SessionEnd", transcript, reason="other")
        archived = self.transcript("archived_sessions")
        transcript.unlink()
        self.assertEqual("none", watch.delivery(self.root, "session-A", self.now))
        self.enqueue()
        self.tick()
        self.assertEqual([], self.calls())
        report = codex.report(self.folder, self.now)
        self.assertEqual("disconnected", report["state"])
        self.assertIn("archived", report["last_error"])
        self.hook("SessionStart", archived, source="resume")
        self.tick()
        self.assertEqual(1, len(self.calls()))

    def test_session_end_starts_the_daemon_only_for_a_registered_agent(self):
        ensure = mock.Mock()
        event = {"hook_event_name": "SessionEnd", "session_id": "session-A", "cwd": str(self.root), "reason": "other"}
        codex.hook(self.root, event, self.now, ensure)
        ensure.assert_not_called()
        (self.folder / "agent.json").write_text("{}")
        codex.hook(self.root, event, self.now, ensure)
        ensure.assert_called_once()

    def test_lost_acknowledgment_is_confirmed_from_the_transcript_and_unblocks_the_next_batch(self):
        transcript = self.transcript()
        self.hook("SessionStart", transcript, source="startup")
        self.hook("Stop", self.root / "codex-home" / "sessions" / "rollout-subagent-B.jsonl")
        self.assertEqual(str(transcript), codex.registration(self.folder)["transcript_path"])
        self.enqueue()
        self.tick()
        first = codex.pending(self.folder)[0][1]
        self.enqueue(self.event("FINDING Acme/App#14 new reply — run fix-pr", "b"))
        self.tick()
        self.assertEqual(1, len(self.calls()))
        with transcript.open("a") as stream:
            stream.write(json.dumps({"payload": {"role": "user", "text": codex.message("session-A", first)}}) + "\n")
        self.now += 1
        self.tick()
        self.assertEqual(1, len(self.calls()))
        self.now += codex.CONFIRM_EVERY
        self.tick()
        self.assertTrue((self.folder / "codex-receipts" / f"{first['batch_id']}.json").exists())
        self.assertEqual(2, len(self.calls()))
        self.assertIn("new reply", json.loads(self.calls()[1])["args"][4])

    def test_transcript_text_from_before_submission_cannot_confirm_a_batch(self):
        transcript = self.transcript()
        self.hook("SessionStart", transcript, source="startup")
        self.enqueue()
        batch = self.prepare()
        transcript.write_text(json.dumps({"text": codex.message("session-A", batch)}) + "\n")
        self.dispatcher.send(self.folder, batch["batch_id"])
        self.now += codex.CONFIRM_EVERY
        self.tick()
        self.assertEqual("queued", codex.pending(self.folder)[0][1]["state"])

    def test_reminder_wording_does_not_queue_another_prompt_but_new_reply_survives(self):
        self.enqueue(self.event())
        batch = self.send()
        self.enqueue(self.event("FINDING Acme/App#14 (still open) src/x.py:3 — run fix-pr"),
                     self.event("FINDING Acme/App#14 new reply — run fix-pr", "b"))
        self.assertEqual(["b"], [json.loads(item["key"])[-1] for item in codex.queued(self.folder)])
        self.assertIsNone(self.prepare())
        consumed, lines = codex.acknowledge(self.folder, "session-A", codex.message("session-A", batch), self.now)
        self.assertEqual((True, [str(self.event())]), (consumed, lines))
        self.assertEqual("b", json.loads(self.prepare()["items"][0]["key"])[-1])

    def test_queue_uses_registered_thread_and_preserves_spaces_and_shell_text(self):
        text = "FINDING Acme/App#14 literal \\0 and NUL\0 `touch nope` $(touch nope) — run fix-pr"
        self.enqueue(self.event(text))
        batch = self.send()
        calls = [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]
        self.assertEqual([{"args": ["queue", "--thread", "session-A", "--message",
                                    codex.message("session-A", batch), "--resume"], "cwd": str(self.root)}], calls)
        self.assertFalse((self.root / "nope").exists())
        self.assertIn("NUL\\0", calls[0]["args"][4])

    def test_resume_warning_is_queued_and_never_retried_or_logged_verbatim(self):
        (self.root / "warning").touch()
        self.enqueue()
        self.send()
        self.now += 3600
        self.assertIsNone(self.prepare())
        report = codex.report(self.folder, self.now)
        self.assertEqual("queued", report["state"])
        self.assertIn("warning", report["last_error"])
        self.assertNotIn("private", report["last_error"])
        self.assertEqual(1, len((self.root / "calls.jsonl").read_text().splitlines()))

    def test_acknowledgment_racing_command_completion_cannot_resurrect_batch(self):
        self.enqueue()
        batch = self.prepare()
        def run(*args, **kwargs):
            codex.acknowledge(self.folder, "session-A", codex.message("session-A", batch), self.now)
            return subprocess.CompletedProcess(args[0], 0, "", "")
        self.dispatcher.run = run
        self.dispatcher.send(self.folder, batch["batch_id"])
        self.assertEqual([], codex.pending(self.folder))
        self.assertEqual((True, []), codex.acknowledge(self.folder, "session-A",
                                                    codex.message("session-A", batch), self.now))
        self.assertEqual("consumed", codex.report(self.folder, self.now)["state"])

    def test_definite_failure_backs_off_and_timeout_is_resent_after_the_resend_delay(self):
        (self.root / "fail").touch()
        self.enqueue()
        self.send()
        self.assertIsNone(self.prepare())
        self.now += 30
        batch = self.prepare()
        self.assertIsNotNone(batch)
        self.dispatcher.run = mock.Mock(side_effect=subprocess.TimeoutExpired("codex", codex.TIMEOUT))
        self.dispatcher.send(self.folder, batch["batch_id"])
        self.assertEqual("uncertain", codex.report(self.folder, self.now)["state"])
        self.now += codex.RESEND - 1
        self.assertIsNone(self.prepare())
        codex.retry(self.folder, self.now)
        self.assertEqual(batch["batch_id"], self.prepare()["batch_id"])

    def test_timed_out_submission_is_resent_once_unless_the_transcript_shows_it_arrived(self):
        transcript = self.transcript()
        self.hook("SessionStart", transcript, source="startup")
        self.enqueue()
        batch = self.prepare()
        self.dispatcher.run = mock.Mock(side_effect=subprocess.TimeoutExpired("codex", codex.TIMEOUT))
        self.dispatcher.send(self.folder, batch["batch_id"])
        self.dispatcher.run = subprocess.run
        self.now += codex.RESEND - 1
        self.tick()
        self.assertEqual([], self.calls())
        self.now += 1
        self.tick()
        self.assertEqual(1, len(self.calls()))
        self.assertEqual(batch["batch_id"], codex.pending(self.folder)[0][0].stem)
        self.assertEqual("queued", codex.pending(self.folder)[0][1]["state"])

    def test_timed_out_submission_that_arrived_is_consumed_instead_of_resent(self):
        transcript = self.transcript()
        self.hook("SessionStart", transcript, source="startup")
        self.enqueue()
        batch = self.prepare()
        self.dispatcher.run = mock.Mock(side_effect=subprocess.TimeoutExpired("codex", codex.TIMEOUT))
        self.dispatcher.send(self.folder, batch["batch_id"])
        self.dispatcher.run = subprocess.run
        with transcript.open("a") as stream:
            stream.write(json.dumps({"text": codex.message("session-A", batch)}) + "\n")
        self.now += codex.RESEND
        self.tick()
        self.assertEqual([], self.calls())
        self.assertTrue((self.folder / "codex-receipts" / f"{batch['batch_id']}.json").exists())

    def test_repeated_queue_failures_wait_until_each_retry_deadline(self):
        (self.root / "fail").touch()
        self.enqueue()
        self.send()
        for delay in (30, 60, 120, 300, 300):
            with self.subTest(delay=delay):
                self.now += delay - 1
                self.assertIsNone(self.prepare(), "failed submission retried before its deadline")
                self.now += 1
                batch = self.prepare()
                self.assertIsNotNone(batch, "failed submission did not retry at its deadline")
                self.dispatcher.send(self.folder, batch["batch_id"])

    def test_restart_recovers_a_claim_without_losing_concurrent_new_revision(self):
        self.enqueue()
        batch = self.prepare()
        batch["state"] = "submitting"
        write_json(self.folder / "codex-pending" / f"{batch['batch_id']}.json", batch)
        new = self.event("FINDING Acme/App#14 new reply — run fix-pr", "b")
        codex.save_queue(self.folder, [*batch["items"], {"key": new.key, "line": str(new)}])
        with locked(self.folder):
            codex.recover(self.folder)
        self.assertEqual([{"key": new.key, "line": str(new)}], codex.queued(self.folder))
        self.assertEqual("uncertain", codex.pending(self.folder)[0][1]["state"])

    def test_a_live_submission_cannot_be_recovered_or_explicitly_retried(self):
        self.enqueue()
        batch = self.prepare()
        batch["state"] = "submitting"
        path = self.folder / "codex-pending" / f"{batch['batch_id']}.json"
        write_json(path, batch)
        with codex.submission(self.folder):
            with locked(self.folder):
                codex.recover(self.folder)
            self.assertEqual("submitting", read_json(path)["state"])
            with self.assertRaises(UsageError):
                codex.retry(self.folder, self.now)

    def test_drop_before_submission_filters_work_but_keeps_terminal_notice(self):
        self.enqueue()
        batch = self.prepare()
        watch.drop(self.root, "session-A", "acme/app", 14, "fix", None)
        self.dispatcher.send(self.folder, batch["batch_id"])
        self.assertFalse((self.root / "calls.jsonl").exists())
        self.enqueue("MERGED Acme/App#14 into main — clean up")
        self.assertEqual("MERGED Acme/App#14 into main — clean up", self.send()["items"][0]["line"])

    def test_a_late_accepted_batch_is_acknowledged_but_dropped_work_is_not_routed(self):
        self.enqueue()
        batch = self.send()
        watch.remove(self.root, "session-A", "acme/app", 14, "fix")
        self.assertEqual((True, []), codex.acknowledge(self.folder, "session-A",
                                                    codex.message("session-A", batch), self.now))

    def test_disconnect_allows_stream_to_take_unsent_items_but_not_accepted_batch(self):
        self.enqueue()
        self.send()
        self.enqueue(self.event("FINDING Acme/App#14 newer — run fix-pr", "b"))
        codex.disconnect(self.folder)
        watch.renew(self.root, "session-A", "stream", self.now)
        self.assertEqual(["FINDING Acme/App#14 newer — run fix-pr"], watch.take(self.root, "session-A", self.now))
        self.assertEqual("queued", codex.pending(self.folder)[0][1]["state"])

    def test_lease_expires_across_sleep_without_changing_legacy_liveness(self):
        watch.renew(self.root, "claude-A", "mod", self.now)
        self.assertEqual(("codex", "mod"), (watch.delivery(self.root, "session-A", self.now),
                                            watch.delivery(self.root, "claude-A", self.now)))
        self.assertEqual(("codex", "none"), (watch.delivery(self.root, "session-A", self.now + 301),
                                             watch.delivery(self.root, "claude-A", self.now + 301)))
        self.assertEqual("none", watch.delivery(self.root, "session-A", self.now + 86401))

    def test_transports_cannot_steal_each_others_events(self):
        with self.assertRaises(UsageError):
            watch.take(self.root, "session-A", self.now)
        with self.assertRaises(UsageError):
            watch.renew(self.root, "session-A", "mod", self.now)
        watch.renew(self.root, "claude-A", "mod", self.now)
        with self.assertRaises(UsageError):
            codex.register(self.root / "sessions" / "claude-A", "claude-A", str(self.root), self.now)

    def test_no_events_start_no_process_and_write_no_queue(self):
        for _ in range(3):
            self.dispatcher.tick([self.folder])
        self.assertFalse((self.root / "calls.jsonl").exists())
        self.assertFalse((self.folder / "codex-events.json").exists())

    def test_registration_reuses_capability_probe_and_hook_renews_parent(self):
        event = {"hook_event_name": "SessionStart", "session_id": "session-A", "cwd": str(self.root)}
        probe = mock.Mock(side_effect=AssertionError("capability probe repeated"))
        output = codex.hook(self.root, event, self.now + 1, mock.Mock(), run=probe)
        self.assertIn("--session session-A", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.now + 1 + 86400, codex.registration(self.folder)["expires_at"])

    def test_an_ordinary_prompt_renews_delivery_without_repeating_startup_context(self):
        event = {"hook_event_name": "UserPromptSubmit", "session_id": "session-A", "cwd": str(self.root),
                 "prompt": "Continue the implementation."}
        output = codex.hook(self.root, event, self.now + 1, mock.Mock())
        self.assertEqual({}, output)
        self.assertEqual(self.now + 1 + 86400, codex.registration(self.folder)["expires_at"])
        self.enqueue()
        batch = self.send()
        event["prompt"] = codex.message("session-A", batch)
        output = codex.hook(self.root, event, self.now + 2, mock.Mock())
        self.assertIn(str(self.event()), output["hookSpecificOutput"]["additionalContext"])

    def test_duplicate_consumed_marker_is_blocked_before_another_turn(self):
        self.enqueue()
        batch = self.send()
        event = {"hook_event_name": "UserPromptSubmit", "session_id": "session-A", "cwd": str(self.root),
                 "prompt": codex.message("session-A", batch)}
        codex.hook(self.root, event, self.now, mock.Mock())
        self.assertEqual("block", codex.hook(self.root, event, self.now, mock.Mock())["decision"])

    def test_malformed_hook_is_advisory_and_cannot_start_daemon(self):
        for event in ({"hook_event_name": "SessionStart", "session_id": "../wrong"},
                      {"hook_event_name": "UserPromptSubmit", "session_id": "fresh-bad",
                       "cwd": str(self.root), "prompt": []}):
            with self.subTest(event=event), mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
                    mock.patch("projector.watch.state_root", return_value=self.root), \
                    mock.patch("projector.watch.ensure_daemon") as ensure, \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(0, cli.main(["watch", "codex-hook"]))
                result = json.loads(output.getvalue())
                self.assertIn("unavailable", result["systemMessage"])
                self.assertNotIn("schema_version", result)
                ensure.assert_not_called()
                self.assertFalse((self.root / "sessions" / "fresh-bad").exists())

    def test_old_cli_capability_failure_is_cached_and_does_not_claim_delivery(self):
        folder = self.root / "sessions" / "old-cli"
        folder.mkdir()
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "--thread --message", ""))
        with self.assertRaises(UsageError):
            codex.register(folder, "old-cli", str(self.root), self.now, run=run)
        with self.assertRaises(UsageError):
            codex.register(folder, "old-cli", str(self.root), self.now, run=run)
        self.assertEqual(1, run.call_count)
        self.assertEqual("none", watch.delivery(self.root, "old-cli", self.now))

    def test_push_output_from_child_hooks_uses_parent_and_respects_disabled_auto_start(self):
        event = {"hook_event_name": "PostToolUse", "session_id": "session-A", "cwd": str(self.root),
                 "tool_input": {"command": "gh pr create --draft"},
                 "tool_name": "Bash", "tool_response": {"exit_code": 0, "output": "https://github.com/acme/app/pull/17"}}
        config = mock.Mock()
        config.setting.return_value = False
        with mock.patch("projector.config.load", return_value=config):
            codex.hook(self.root, event, self.now, mock.Mock())
        self.assertEqual([14], [line.number for line in watch.subscriptions(self.folder)])
        config.setting.return_value = True
        with mock.patch("projector.config.load", return_value=config):
            codex.hook(self.root, event, self.now, mock.Mock())
        self.assertEqual([14, 17], [line.number for line in watch.subscriptions(self.folder)])
        self.assertEqual(["session-A"], watch.session_names(self.root))

    def test_running_shell_output_cannot_subscribe_until_command_completes(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "gh pr create --draft"},
                 "tool_response": {"exit_code": None, "session_id": 12,
                 "output": "https://github.com/acme/app/pull/17"}}
        self.assertIsNone(codex.tool_output(event))
        event["tool_response"] = {"exit_code": 0, "output": "https://github.com/acme/app/pull/17"}
        self.assertEqual("https://github.com/acme/app/pull/17", codex.tool_output(event))

    def test_real_codex_text_output_is_parsed_only_for_authoring_commands(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "gh pr create --draft"},
                 "tool_response": "https://github.com/acme/app/pull/17"}
        self.assertEqual(event["tool_response"], codex.tool_output(event))
        event["tool_input"]["command"] = "cat notes.md"
        self.assertIsNone(codex.tool_output(event), "reading a PR URL must not subscribe the session to fix it")
        self.assertTrue(codex.push_command("git -C 'some dir' push origin feature"))
        self.assertTrue(codex.push_command("git status\ngit push"))
        self.assertFalse(codex.push_command("echo 'git push'"))

    def test_hung_session_does_not_block_another_and_pool_has_no_unbounded_backlog(self):
        entered, release, other_done = threading.Event(), threading.Event(), threading.Event()
        def run(args, **kwargs):
            if args[args.index("--thread") + 1] == "session-A":
                entered.set()
                if not release.wait(5):
                    raise AssertionError("test did not release the slow call")
            else:
                other_done.set()
            return subprocess.CompletedProcess(args, 0, "", "")
        self.dispatcher.run = run
        self.enqueue()
        folders = [self.folder]
        for number in range(5):
            name = f"other-{number}"
            folder = watch.session_folder(self.root, name)
            codex.register(folder, name, str(self.root), self.now)
            watch.add(self.root, name, watch.Subscription("acme/app", 14, "fix"))
            watch.append_events(folder, [self.event()])
            folders.append(folder)
        try:
            self.dispatcher.tick(folders)
            self.assertTrue(entered.wait(5))
            self.assertTrue(other_done.wait(5), "a hung session blocked another session")
            self.assertLessEqual(len(self.dispatcher.jobs), 4)
        finally:
            release.set()

    def test_bounded_batches_keep_later_complete_lines_without_losing_them(self):
        first = self.event("FINDING Acme/App#14 " + "x" * 5000, "a")
        second = self.event("FINDING Acme/App#14 " + "y" * 5000, "b")
        self.enqueue(first, second)
        batch = self.send()
        self.assertEqual([str(first)], [item["line"] for item in batch["items"]])
        self.assertEqual([str(second)], [item["line"] for item in codex.queued(self.folder)])
        codex.acknowledge(self.folder, "session-A", codex.message("session-A", batch), self.now)
        self.assertEqual([str(second)], [item["line"] for item in self.prepare()["items"]])


from tests.test_watch import WatchCase, SHA_A, SHA_B, MODEL, thread


class RoutingTests(WatchCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.daemon.codex.close)

    def codex_session(self, name, line):
        self.subscribe(name, line)
        folder = self.folder(name)
        write_json(folder / "codex.json", {"schema_version": 1, "enabled": True,
                                          "expires_at": self.clock.wall() + 86400})

    def test_real_head_and_finding_reminders_coalesce_without_additional_github_queries(self):
        self.github.pull_request(14, threads=[thread("T1")])
        self.codex_session("codex-review", f"acme/app#14 review {MODEL} operator")
        self.codex_session("codex-fix", "acme/app#14 fix")
        self.subscribe("claude", "acme/app#14 fix")
        self.renew("claude")
        self.daemon.run_pass()
        self.assertEqual(["acme/app#14"], self.github.queried())
        review_folder = self.folder("codex-review")
        review_items = codex.queued(review_folder)
        self.assertEqual(1, len(review_items))
        self.assertIn(SHA_A[:7], review_items[0]["line"])
        self.assertIn("--loop codex-review", review_items[0]["line"])
        self.assertIn("FINDING acme/app#14", self.take("claude")[0])
        self.assertEqual([], codex.queued(self.folder("codex-fix")))
        self.advance(901)
        self.daemon.run_pass()
        self.assertEqual(review_items, codex.queued(review_folder))
        self.assertIn("(still open)", self.take("claude")[0])
        self.github.pull_request(14, head=SHA_B, threads=[thread("T1")])
        self.daemon.run_pass()
        self.assertEqual([SHA_A, SHA_B], [json.loads(item["key"])[4] for item in codex.queued(review_folder)])

    def test_codex_finding_new_reply_survives_while_the_previous_batch_waits(self):
        self.github.pull_request(14, threads=[thread("T1")])
        self.codex_session("codex-fix", "acme/app#14 fix")
        self.daemon.run_pass()
        folder = self.folder("codex-fix")
        first = [item for item in codex.queued(folder) if item["line"].startswith("FINDING")]
        self.advance(901)
        self.daemon.run_pass()
        self.assertEqual(first, [item for item in codex.queued(folder) if item["line"].startswith("FINDING")])
        self.github.pull_request(14, threads=[thread("T1", replies=[{"databaseId": 2, "body": "a new reply"}])])
        self.advance(901)
        self.daemon.run_pass()
        findings = [item for item in codex.queued(folder) if item["line"].startswith("FINDING")]
        self.assertEqual(2, len(findings))
        self.assertIn("new reply", findings[1]["line"])

    def test_wall_clock_expiry_wins_over_sleep_forgiveness_and_daemon_restart(self):
        self.github.pull_request(14)
        self.codex_session("codex-review", f"acme/app#14 review {MODEL} operator")
        self.daemon.scan(self.clock.mono())
        self.assertIn("codex-review", self.daemon.live)
        self.clock.advance(86401)
        self.daemon.forgive(self.clock.mono())
        restarted = self.make_daemon()
        self.addCleanup(restarted.codex.close)
        restarted.scan(self.clock.mono())
        self.assertNotIn("codex-review", restarted.live)
        self.daemon.scan(self.clock.mono())
        self.assertNotIn("codex-review", self.daemon.live)
