"""`conversation.py`: reading a session's conversation from Claude Code's and Codex's transcripts.

The fixtures in `tests/fixtures/conversation/` hold made-up entries in each host's transcript format: what the
person typed, what the agent said, and the tool calls, results, thinking, and context the console leaves out.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from projector import conversation, watch
from projector.conversation import Said
from projector.inbox import write_json

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "conversation"
SESSION = "11111111-2222-4333-8444-555555555555"


class Case(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def follow(self, fixture: str, parse) -> tuple[Path, conversation.Reader]:
        path = self.scratch / fixture
        return path, conversation.Reader(lambda: path if path.exists() else None, parse)


class Hosts(Case):
    def test_a_claude_transcript_gives_the_persons_prompts_and_inbox_lines_and_the_agents_text_only(self) -> None:
        _, reader = self.follow("claude.jsonl", conversation.claude_said)
        shutil.copy(FIXTURES / "claude.jsonl", self.scratch / "claude.jsonl")

        self.assertEqual((True, [
            Said("you", "Rename the greeting to hello."),
            Said("agent", "I'll rename it in greet.py."),
            Said("you", "DISCUSS 5 About this session → please keep the old name as an alias"),
            Said("you", "/projector:fix-pr acme/app#12"),
            Said("agent", "Done: greet.py says hello, and greet stays as an alias."),
        ]), reader.read())

    def test_a_codex_rollout_gives_the_persons_text_and_inbox_lines_and_the_agents_text_only(self) -> None:
        _, reader = self.follow("codex.jsonl", conversation.codex_said)
        shutil.copy(FIXTURES / "codex.jsonl", self.scratch / "codex.jsonl")

        self.assertEqual((True, [
            Said("you", "Add a farewell function."),
            Said("agent", "I'll add it beside greet."),
            Said("you", "ANSWER 3.a Add the farewell to the README too? → Yes, in the usage section"),
            Said("agent", "Added farewell, and the README's usage section shows it."),
            Said("you", "A message from an older Codex that records no kinds."),
        ]), reader.read())


class Following(Case):
    def test_a_later_read_returns_only_what_was_added_and_waits_for_a_line_to_end(self) -> None:
        path, reader = self.follow("claude.jsonl", conversation.claude_said)
        lines = (FIXTURES / "claude.jsonl").read_bytes().splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:6]))
        first = reader.read()
        last = lines[-1]
        with path.open("ab") as handle:
            handle.write(b"".join(lines[6:-1]) + last[:20])
        middle = reader.read()
        with path.open("ab") as handle:
            handle.write(last[20:])

        self.assertEqual([
            (True, [Said("you", "Rename the greeting to hello."), Said("agent", "I'll rename it in greet.py.")]),
            (False, [Said("you", "DISCUSS 5 About this session → please keep the old name as an alias"),
                     Said("you", "/projector:fix-pr acme/app#12")]),
            (False, [Said("agent", "Done: greet.py says hello, and greet stays as an alias.")]),
            (False, []),
        ], [first, middle, reader.read(), reader.read()])

    def test_a_replaced_or_shortened_transcript_is_read_again_from_its_start(self) -> None:
        path, reader = self.follow("claude.jsonl", conversation.claude_said)
        shutil.copy(FIXTURES / "claude.jsonl", path)
        reader.read()
        lines = (FIXTURES / "claude.jsonl").read_bytes().splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:2]))

        self.assertEqual((True, [Said("you", "Rename the greeting to hello.")]), reader.read())

    def test_the_first_read_of_a_long_transcript_starts_at_a_whole_line_near_its_end(self) -> None:
        path, reader = self.follow("claude.jsonl", conversation.claude_said)
        filler = b'{"type":"user","message":{"role":"user","content":"An old made-up prompt."}}\n' * 50
        shutil.copy(FIXTURES / "claude.jsonl", self.scratch / "tail.jsonl")
        path.write_bytes(filler + (self.scratch / "tail.jsonl").read_bytes())

        tail = len((self.scratch / "tail.jsonl").read_bytes())
        found = []
        # A tail that starts inside the last filler line, and one that starts exactly where the fixture does.
        for size in (tail + 10, tail):
            with mock.patch.object(conversation, "TAIL_BYTES", size):
                restarted, said = conversation.Reader(lambda: path, conversation.claude_said).read()
            found.append((restarted, said[0]))

        self.assertEqual([(True, Said("you", "Rename the greeting to hello."))] * 2, found)

    def test_the_first_read_keeps_a_line_that_starts_exactly_where_the_tail_does(self) -> None:
        path, _ = self.follow("claude.jsonl", conversation.claude_said)
        first = b'{"type":"user","origin":{"kind":"human"},"message":{"role":"user","content":"Old."}}\n'
        second = b'{"type":"user","origin":{"kind":"human"},"message":{"role":"user","content":"New."}}\n'
        path.write_bytes(first + second)

        with mock.patch.object(conversation, "TAIL_BYTES", len(second)):
            said = conversation.Reader(lambda: path, conversation.claude_said).read()[1]

        self.assertEqual([Said("you", "New.")], said)

    def test_a_transcript_that_disappears_starts_over_and_one_never_found_reads_nothing(self) -> None:
        path, reader = self.follow("claude.jsonl", conversation.claude_said)
        missing = reader.read()
        shutil.copy(FIXTURES / "claude.jsonl", path)
        reader.read()
        path.unlink()

        self.assertEqual([(False, []), (True, [])], [missing, reader.read()])


class Finding(Case):
    def test_a_claude_transcript_is_found_under_claude_config_dir_or_home_and_the_newest_wins(self) -> None:
        config, home = self.scratch / "config", self.scratch / "home"
        for root, project in ((config, "a"), (home / ".claude", "b"), (home / ".claude", "c")):
            (root / "projects" / project).mkdir(parents=True)
            (root / "projects" / project / f"{SESSION}.jsonl").write_text("")
        os.utime(home / ".claude" / "projects" / "b" / f"{SESSION}.jsonl", (1, 1))
        os.utime(config / "projects" / "a" / f"{SESSION}.jsonl", (2, 2))

        self.assertEqual(
            (home / ".claude" / "projects" / "c" / f"{SESSION}.jsonl",
             home / ".claude" / "projects" / "c" / f"{SESSION}.jsonl", None),
            (conversation.claude_transcript(SESSION, {"CLAUDE_CONFIG_DIR": str(config), "HOME": str(home)}),
             conversation.claude_transcript(SESSION, {"HOME": str(home)}),
             conversation.claude_transcript("*", {"HOME": str(home)})))

    def test_a_codex_rollout_is_the_one_codex_json_names(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.scratch / "state")}):
            root = watch.state_root()
            folder = watch.session_folder(root, SESSION)
            folder.mkdir(parents=True)
            rollout = self.scratch / "rollout.jsonl"
            write_json(folder / "codex.json", {"schema_version": 1, "transcript_path": str(rollout)})

            self.assertEqual((rollout, None),
                             (conversation.codex_transcript(root, SESSION),
                              conversation.codex_transcript(root, "another-session")))


if __name__ == "__main__":
    unittest.main()
