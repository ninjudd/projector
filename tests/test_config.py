from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from projector import config as config_module
from projector.cli import main
from projector.config import SETTINGS, ConfigError, config_paths, load, merge, user_config_path
from projector.core import json_scalar


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "home"
        self.repo = self.home / "work" / "repo"
        self.repo.mkdir(parents=True)
        self.environ = {"HOME": str(self.home)}

    def user_layer(self, text: str) -> Path:
        return write(self.home / ".projector.toml", text)

    def merged(self, start: Path | None = None):
        return load(start or self.repo, self.home, self.environ)

    def test_nearer_files_win_and_tables_merge_key_by_key(self) -> None:
        self.user_layer('reviewer = "user"\n[review]\neffort = "medium"\nmodel = "sonnet"\n')
        write(self.home / "work" / ".projector.toml", '[review]\neffort = "xhigh"\n')
        write(self.repo / ".projector.toml", '[review]\nmodel = "fable"\n')

        config = self.merged()

        # Each table key resolves independently: the parent's effort survives
        # the repo's file, which only names model.
        self.assertEqual("xhigh", config.get("review.effort"))
        self.assertEqual("fable", config.get("review.model"))
        self.assertEqual("user", config.get("reviewer"))

    def test_sources_name_the_file_whose_value_survived(self) -> None:
        self.user_layer('[review]\neffort = "medium"\nmodel = "sonnet"\n')
        parent = write(self.home / "work" / ".projector.toml", '[review]\neffort = "xhigh"\n')

        config = self.merged()

        self.assertEqual(parent, config.source("review.effort"))
        self.assertEqual(self.home / ".projector.toml", config.source("review.model"))

    def test_walk_stops_at_home(self) -> None:
        write(self.base / ".projector.toml", 'reviewer = "above-home"\n')
        self.user_layer("inside = true\n")

        config = self.merged()

        self.assertTrue(config.get("inside"))
        self.assertIsNone(config.get("reviewer"))

    def test_the_home_file_is_read_once_though_two_rules_reach_it(self) -> None:
        # It is both the user layer and the top of the walk. Reading it twice
        # would not change a value but would list the path twice.
        user = self.user_layer('reviewer = "user"\n')

        config = self.merged()

        self.assertEqual([user], config.paths)
        self.assertEqual(user, config.source("reviewer"))

    def test_repo_outside_home_reads_no_ancestor_but_keeps_the_user_layer(self) -> None:
        # The case the separate user layer exists for: a checkout with no home
        # directory among its ancestors. A walk alone would find nothing at
        # all, so personal configuration would silently vanish.
        self.user_layer('reviewer = "user"\n')
        outside = self.base / "opt" / "src" / "thing"
        outside.mkdir(parents=True)
        write(self.base / "opt" / ".projector.toml", 'reviewer = "stranger"\n')
        write(outside / ".projector.toml", 'local = true\n')

        config = self.merged(outside)

        self.assertEqual("user", config.get("reviewer"))
        self.assertTrue(config.get("local"))

    def test_paths_are_ordered_lowest_precedence_first(self) -> None:
        user = self.user_layer("a = 1\n")
        parent = write(self.home / "work" / ".projector.toml", "b = 2\n")
        repo = write(self.repo / ".projector.toml", "c = 3\n")

        self.assertEqual([user, parent, repo], config_paths(self.repo, self.home, self.environ))

    def test_missing_files_are_simply_absent(self) -> None:
        config = self.merged()

        self.assertEqual([], config.paths)
        self.assertEqual({}, config.values)
        self.assertIsNone(config.get("anything"))
        self.assertEqual("fallback", config.get("anything", "fallback"))

    def test_arrays_replace_rather_than_append(self) -> None:
        # Appending to an inherited array is not something a reader could
        # predict from looking at one file.
        self.user_layer("skills = [\"a\", \"b\"]\n")
        write(self.repo / ".projector.toml", "skills = [\"c\"]\n")

        self.assertEqual(["c"], self.merged().get("skills"))

    def test_a_table_may_replace_a_scalar_without_stale_provenance(self) -> None:
        self.user_layer('review = "medium"\n')
        repo = write(self.repo / ".projector.toml", '[review]\neffort = "xhigh"\n')

        config = self.merged()

        self.assertEqual({"effort": "xhigh"}, config.get("review"))
        self.assertEqual(repo, config.source("review.effort"))
        self.assertIsNone(config.source("review"))

    def test_invalid_toml_names_the_file(self) -> None:
        broken = write(self.repo / ".projector.toml", "reviewer = \n")

        with self.assertRaises(ConfigError) as caught:
            self.merged()

        self.assertIn(str(broken), str(caught.exception))

    def test_the_user_layer_is_the_same_filename_in_the_home_directory(self) -> None:
        self.assertEqual(self.home / ".projector.toml", user_config_path(self.environ))

    def test_a_worktree_under_the_repository_still_sees_the_repository(self) -> None:
        # Claude Code puts worktrees at <repo>/.claude/worktrees/<name>, which
        # keeps the repository and its parents on the walk.
        self.user_layer('reviewer = "user"\n')
        org = write(self.home / "work" / ".projector.toml", 'org = "ninjudd"\n')
        repo = write(self.repo / ".projector.toml", 'repo = true\n')
        worktree = self.repo / ".claude" / "worktrees" / "feature"
        worktree.mkdir(parents=True)

        config = self.merged(worktree)

        self.assertEqual("ninjudd", config.get("org"))
        self.assertTrue(config.get("repo"))
        self.assertIn(org, config.paths)
        self.assertIn(repo, config.paths)

    def test_merge_does_not_mutate_its_inputs(self) -> None:
        base = {"review": {"effort": "medium"}}
        overlay = {"review": {"model": "fable"}}

        result = merge(base, overlay)

        self.assertEqual({"effort": "medium", "model": "fable"}, result["review"])
        self.assertEqual({"review": {"effort": "medium"}}, base)
        self.assertEqual({"review": {"model": "fable"}}, overlay)


if __name__ == "__main__":
    unittest.main()


class ConfigCommandTests(unittest.TestCase):
    """The CLI paths. `ConfigTests` covers config.py; these cover cli.py, which
    is where rendering lives and where a TOML date used to crash."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.home = self.root / "home"
        self.home.mkdir()
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def config(self, text: str) -> Path:
        return write(self.root / ".projector.toml", text)

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--root", str(self.root), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_toml_date_renders_as_iso_rather_than_crashing(self) -> None:
        # `deadline = 2026-10-01` is valid TOML and what a person writes
        # without thinking; tomllib hands back a datetime.date.
        self.config("deadline = 2026-10-01\n")

        code, stdout, _ = self.invoke("config", "get", "deadline")

        self.assertEqual(0, code)
        self.assertEqual("2026-10-01\n", stdout)

    def test_one_date_does_not_poison_an_unrelated_listing(self) -> None:
        self.config('deadline = 2026-10-01\nreviewer = "minjudd"\n')

        code, stdout, _ = self.invoke("config", "list")

        self.assertEqual(0, code)
        self.assertIn("reviewer = minjudd", stdout)
        self.assertIn("deadline = 2026-10-01", stdout)

    def test_every_toml_temporal_type_survives_json(self) -> None:
        self.config(
            "day = 2026-10-01\n"
            "at = 09:30:00\n"
            "stamp = 2026-10-01T09:30:00Z\n"
            "window = [2026-10-01, 2026-10-31]\n"
        )

        code, stdout, _ = self.invoke("config", "list", "--json")

        self.assertEqual(0, code)
        values = json.loads(stdout)["config"]
        self.assertEqual("2026-10-01", values["day"])
        self.assertEqual("09:30:00", values["at"])
        self.assertEqual(["2026-10-01", "2026-10-31"], values["window"])
        self.assertTrue(values["stamp"].startswith("2026-10-01T09:30:00"))

    def test_an_unserializable_value_still_fails_loudly(self) -> None:
        # The date handling must not become a blanket str() that hides a real
        # serialization bug.
        with self.assertRaises(TypeError):
            json_scalar(object())

    def test_get_exits_one_when_unset_and_zero_with_a_default(self) -> None:
        self.config('reviewer = "minjudd"\n')

        missing, stdout, _ = self.invoke("config", "get", "nope")
        self.assertEqual(1, missing)
        self.assertEqual("", stdout)

        defaulted, stdout, _ = self.invoke("config", "get", "nope", "--default", "fallback")
        self.assertEqual(0, defaulted)
        self.assertEqual("fallback\n", stdout)

    def test_booleans_print_the_way_the_file_spells_them(self) -> None:
        self.config("[review]\nallow_approve = false\n")

        _, stdout, _ = self.invoke("config", "get", "review.allow_approve")

        self.assertEqual("false\n", stdout)

    def test_projects_dir_comes_from_configuration(self) -> None:
        plans = self.root / "plans"
        plans.mkdir()
        (plans / "README.md").write_text("# Plans\n")
        self.config('[projects]\ndir = "plans"\n')

        code, stdout, _ = self.invoke("list", "--json")

        self.assertEqual(0, code)
        self.assertEqual([], json.loads(stdout)["projects"])

    def test_the_flag_beats_configuration(self) -> None:
        for name in ("plans", "flagged"):
            directory = self.root / name
            directory.mkdir()
            (directory / "README.md").write_text("# Dir\n")
        write(
            self.root / "flagged" / "alpha" / "readme.md",
            "---\nstatus: draft\npriority: later\n---\n\n# Alpha\n\n## 1. Outcome\n\nDone.\n",
        )
        self.config('[projects]\ndir = "plans"\n')

        code, stdout, _ = self.invoke("--projects-dir", "flagged", "list", "--json")

        self.assertEqual(0, code)
        self.assertEqual("alpha", json.loads(stdout)["projects"][0]["name"])

    def test_a_wrongly_typed_projects_dir_is_an_error_not_a_traceback(self) -> None:
        self.config("[projects]\ndir = 42\n")

        code, _, stderr = self.invoke("list")

        self.assertEqual(78, code)
        self.assertIn("projects.dir must be a string", stderr)

    def test_invalid_toml_reports_cleanly_through_the_cli(self) -> None:
        self.config("reviewer = \n")

        code, _, stderr = self.invoke("config", "list")

        self.assertEqual(78, code)
        self.assertIn("invalid TOML", stderr)


class ConfigSetTests(unittest.TestCase):
    """`project config set` and `unset`, which change one line of one file."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve() / "home"
        self.root = self.home / "work" / "repo"
        self.root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.repo_file = self.root / ".projector.toml"
        self.user_file = self.home / ".projector.toml"

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--root", str(self.root), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_set(self, key: str, value: str, *scope: str) -> str:
        code, stdout, stderr = self.invoke("config", "set", key, value, *scope)
        self.assertEqual(0, code, stderr)
        return stdout

    def test_each_type_lands_in_its_table_in_a_new_file(self) -> None:
        self.assert_set("watch.auto_start", "false")
        self.assert_set("review.username", 'mi"n\\judd')

        self.assertEqual(
            "[watch]\nauto_start = false\n\n"
            '[review]\nusername = "mi\\"n\\\\judd"\n',
            self.repo_file.read_text(),
        )
        config = load(self.root)
        self.assertIs(False, config.get("watch.auto_start"))
        self.assertEqual('mi"n\\judd', config.get("review.username"))

    def test_only_the_changed_line_differs_in_a_file_a_person_wrote(self) -> None:
        original = (
            "# Settings for this repository.\n"
            "\n"
            "[review]\n"
            "# Who posts reviews.\n"
            'username = "review-bot"\n'
            "summarize = true\n"
            "\n"
            "# The site.\n"
            "[site]\n"
            'prepare = "make docs"\n'
        )
        self.repo_file.write_bytes(original.encode())

        self.assert_set("review.username", "minjudd")

        self.assertEqual(original.replace('"review-bot"', '"minjudd"'), self.repo_file.read_text())

    def test_a_new_key_joins_its_table_after_the_last_setting(self) -> None:
        self.repo_file.write_text(
            "[review]\n"
            '  username = "review-bot"  # indented\n'
            "\n"
            "# The site comes next.\n"
            "[site]\n"
            "enabled = true\n"
        )

        self.assert_set("review.summarize", "false")

        self.assertEqual(
            "[review]\n"
            '  username = "review-bot"  # indented\n'
            "  summarize = false\n"
            "\n"
            "# The site comes next.\n"
            "[site]\n"
            "enabled = true\n",
            self.repo_file.read_text(),
        )

    def test_a_replaced_value_keeps_its_trailing_comment(self) -> None:
        self.repo_file.write_text('[review]\nallow_approve = false   # off # for now\ngate = "make # all"  # gate\n')

        self.assert_set("review.allow_approve", "true")
        self.assert_set("review.gate", "make check")

        self.assertEqual(
            '[review]\nallow_approve = true   # off # for now\ngate = "make check"  # gate\n',
            self.repo_file.read_text(),
        )

    def test_a_dotted_top_level_key_is_changed_where_it_is_written(self) -> None:
        self.repo_file.write_text('watch.auto_start = true\nreview.username = "bot"\n\n[site]\nenabled = true\n')

        self.assert_set("watch.auto_start", "false")
        self.assert_set("review.summarize", "false")

        self.assertEqual(
            'watch.auto_start = false\nreview.username = "bot"\nreview.summarize = false\n\n'
            "[site]\nenabled = true\n",
            self.repo_file.read_text(),
        )

    def test_a_multi_line_value_is_replaced_whole(self) -> None:
        self.repo_file.write_text('[review]\ngate = """\nmake test # all\n"""  # the gate\nsummarize = true\n')

        self.assert_set("review.gate", "make check")

        self.assertEqual('[review]\ngate = "make check"  # the gate\nsummarize = true\n', self.repo_file.read_text())

    def test_windows_line_endings_survive(self) -> None:
        self.repo_file.write_bytes(b"[watch]\r\nauto_start = true\r\n")

        self.assert_set("watch.auto_start", "false")
        self.assert_set("review.summarize", "false")

        self.assertEqual(
            b"[watch]\r\nauto_start = false\r\n\r\n[review]\r\nsummarize = false\r\n",
            self.repo_file.read_bytes(),
        )

    def test_unset_removes_the_line_and_leaves_the_table(self) -> None:
        self.repo_file.write_text("# keep\n[watch]\nauto_start = false  # mine\n\n[site]\nenabled = false\n")

        code, stdout, _ = self.invoke("config", "unset", "watch.auto_start")

        self.assertEqual(0, code)
        self.assertEqual(f"unset watch.auto_start in {self.repo_file}\n", stdout)
        self.assertEqual("# keep\n[watch]\n\n[site]\nenabled = false\n", self.repo_file.read_text())

    def test_an_unset_key_or_a_missing_file_is_left_unchanged(self) -> None:
        code, stdout, _ = self.invoke("config", "unset", "watch.auto_start")

        self.assertEqual(0, code)
        self.assertEqual(f"unchanged watch.auto_start in {self.repo_file}\n", stdout)
        self.assertFalse(self.repo_file.exists())

        self.repo_file.write_text("[watch]\nauto_start = false\n")
        before = self.repo_file.stat().st_mtime_ns
        code, stdout, _ = self.invoke("config", "set", "watch.auto_start", "false")

        self.assertEqual(0, code)
        self.assertEqual(f"unchanged watch.auto_start = false in {self.repo_file}\n", stdout)
        self.assertEqual(before, self.repo_file.stat().st_mtime_ns)

    def test_json_names_the_action_both_values_and_the_file(self) -> None:
        self.repo_file.write_text("[watch]\nauto_start = true\n")

        code, stdout, _ = self.invoke("config", "set", "watch.auto_start", "false", "--json")

        self.assertEqual(0, code)
        self.assertEqual(
            {"action": "set", "key": "watch.auto_start", "old": True, "new": False,
             "file": str(self.repo_file), "schema_version": 2},
            json.loads(stdout),
        )

        _, stdout, _ = self.invoke("config", "unset", "watch.auto_start", "--json")

        self.assertEqual({"action": "unset", "old": False, "new": None},
                         {key: json.loads(stdout)[key] for key in ("action", "old", "new")})

    def test_an_unknown_key_is_refused_with_the_known_ones(self) -> None:
        for command in (["set", "watch.autostart", "true"], ["unset", "watch.autostart"]):
            code, _, stderr = self.invoke("config", *command)

            self.assertEqual(2, code)
            self.assertIn("unknown key 'watch.autostart'", stderr)
            self.assertIn("watch.auto_start", stderr)
            self.assertFalse(self.repo_file.exists())

    def test_unset_removes_a_key_outside_the_schema_that_the_file_sets(self) -> None:
        self.repo_file.write_text('# mine\n[review]\npeer_wait = "10m"  # no longer read\nsummarize = false\n')

        code, stdout, stderr = self.invoke("config", "unset", "review.peer_wait")

        self.assertEqual(0, code, stderr)
        self.assertEqual(f"unset review.peer_wait in {self.repo_file}\n", stdout)
        self.assertEqual("# mine\n[review]\nsummarize = false\n", self.repo_file.read_text())

    def test_unset_refuses_a_key_that_names_a_table(self) -> None:
        text = "legacy.kept = true\n\n[review]\nsummarize = false\n\n[site]\nlimits = { retries = 3 }\n"
        self.repo_file.write_text(text)

        for key in ("review", "legacy", "site.limits"):
            code, _, stderr = self.invoke("config", "unset", key)

            self.assertEqual(65, code, key)
            self.assertIn(f"{key} is a table, and unset removes one setting, not a table", stderr)
            self.assertEqual(text, self.repo_file.read_text())

    def test_unset_refuses_a_misspelled_key_the_file_does_not_set(self) -> None:
        text = '[review]\npeer_wait = "10m"\n'
        self.repo_file.write_text(text)

        code, stdout, stderr = self.invoke("config", "unset", "review.peer_wiat")

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn(f"unknown key 'review.peer_wiat', which {self.repo_file} does not set", stderr)
        self.assertIn("watch.auto_start", stderr)
        self.assertEqual(text, self.repo_file.read_text())

    def test_a_value_of_the_wrong_type_is_refused(self) -> None:
        cases = [
            ("watch.auto_start", "yes", "must be true or false"),
            ("watch.auto_start", "True", "must be true or false"),
            ("review.username", "\udcff", "must be text that UTF-8 can encode"),
        ]
        for key, value, message in cases:
            code, _, stderr = self.invoke("config", "set", key, value)

            self.assertEqual(2, code)
            self.assertIn(message, stderr)
        self.assertFalse(self.repo_file.exists())

    def test_a_file_that_is_not_valid_toml_is_refused_untouched(self) -> None:
        self.repo_file.write_bytes(b"# mine\nreviewer = \n")

        code, _, stderr = self.invoke("config", "set", "watch.auto_start", "false")

        self.assertEqual(78, code)
        self.assertIn("invalid TOML", stderr)
        self.assertEqual(b"# mine\nreviewer = \n", self.repo_file.read_bytes())

    def test_a_key_in_an_inline_table_is_refused_untouched(self) -> None:
        for text in ("watch = { auto_start = true }\n", "watch = { other = 1 }\n", "[[watch]]\nauto_start = true\n"):
            self.repo_file.write_text(text)

            code, _, stderr = self.invoke("config", "set", "watch.auto_start", "false")

            self.assertEqual(65, code, text)
            self.assertIn("the file is unchanged", stderr)
            self.assertEqual(text, self.repo_file.read_text())

    def test_user_and_file_scopes_write_their_own_files(self) -> None:
        parent = self.home / "work" / ".projector.toml"

        self.assert_set("review.username", "minjudd", "--user")
        self.assert_set("review.summarize", "false", "--file", str(parent))
        self.assert_set("watch.auto_start", "false")

        self.assertEqual('[review]\nusername = "minjudd"\n', self.user_file.read_text())
        self.assertEqual("[review]\nsummarize = false\n", parent.read_text())
        self.assertEqual("[watch]\nauto_start = false\n", self.repo_file.read_text())

    def test_a_file_in_a_missing_directory_is_refused(self) -> None:
        missing = Path(self.temporary.name).resolve() / "typo"

        code, _, stderr = self.invoke("config", "set", "watch.auto_start", "false",
                                      "--file", str(missing / "nested" / ".projector.toml"))

        self.assertEqual(2, code)
        self.assertIn(f"{missing / 'nested'} is not a directory", stderr)
        self.assertFalse(missing.exists())

    def test_a_value_a_nearer_file_overrides_is_reported(self) -> None:
        self.repo_file.write_text("[watch]\nauto_start = true\n")

        code, _, stderr = self.invoke("config", "set", "watch.auto_start", "false", "--user")

        self.assertEqual(0, code)
        self.assertIn(f"{self.repo_file} sets watch.auto_start = true, which applies here", stderr)

        elsewhere = Path(self.temporary.name).resolve() / "elsewhere.toml"
        _, _, stderr = self.invoke("config", "set", "review.summarize", "false", "--file", str(elsewhere))

        self.assertIn(f"{elsewhere} is not read here", stderr)

    def test_an_override_is_reported_when_the_file_already_held_the_value(self) -> None:
        self.user_file.write_text("[watch]\nauto_start = false\n")
        self.repo_file.write_text("[watch]\nauto_start = true\n")

        code, stdout, stderr = self.invoke("config", "set", "watch.auto_start", "false", "--user")

        self.assertEqual(0, code)
        self.assertEqual(f"unchanged watch.auto_start = false in {self.user_file}\n", stdout)
        self.assertIn(f"{self.repo_file} sets watch.auto_start = true, which applies here", stderr)

    def test_outside_a_repository_the_default_file_is_refused(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        previous = Path.cwd()
        os.chdir(outside)
        self.addCleanup(os.chdir, previous)
        stderr = StringIO()

        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            code = main(["config", "set", "watch.auto_start", "false"])

        self.assertEqual(69, code)
        self.assertIn("pass --user or --file", stderr.getvalue())

    def test_a_write_that_does_not_read_back_restores_the_file(self) -> None:
        real_write = config_module.write_through

        def corrupt_first_write(path: Path, text: str) -> None:
            nonlocal corrupted
            real_write(path, text if corrupted else text.replace("false", "true"))
            corrupted = True

        for original in ("# mine\n[watch]\nauto_start = true  # on\n", None):
            if original is None:
                self.repo_file.unlink()
            else:
                self.repo_file.write_text(original)
            corrupted = False
            with mock.patch.object(config_module, "write_through", corrupt_first_write):
                code, _, stderr = self.invoke("config", "set", "watch.auto_start", "false")

            self.assertEqual(65, code)
            self.assertIn("did not read back as written, so the file was restored", stderr)
            if original is None:
                self.assertFalse(self.repo_file.exists())
            else:
                self.assertEqual(original, self.repo_file.read_text())

    def test_a_symlinked_user_file_stays_a_link(self) -> None:
        dotfiles = self.home / "dotfiles" / "projector.toml"
        dotfiles.parent.mkdir()
        dotfiles.write_text("[review]\nsummarize = true\n")
        self.user_file.symlink_to(dotfiles)

        self.assert_set("review.summarize", "false", "--user")

        self.assertTrue(self.user_file.is_symlink())
        self.assertEqual("[review]\nsummarize = false\n", dotfiles.read_text())

    def test_the_documented_keys_are_the_known_keys(self) -> None:
        documentation = (Path(__file__).resolve().parents[1] / "docs" / "cli.md").read_text()
        rows = re.findall(r"^\| `([a-z_.]+)` \| (boolean|string) \|", documentation, re.MULTILINE)
        names = {bool: "boolean", str: "string"}

        self.assertEqual({key: names[kind] for key, kind in SETTINGS.items()}, dict(rows))
