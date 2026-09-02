from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from projector.config import ConfigError, config_paths, load, merge, user_config_path


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
