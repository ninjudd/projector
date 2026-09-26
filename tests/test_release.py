from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / ".github" / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

FILES = ("pyproject.toml", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json")


def copy_version_files(dest: Path) -> None:
    for name in FILES:
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, dest / name)


def run(cwd: Path, *args: str) -> str:
    return subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        copy_version_files(self.root)
        self.start = release.current(self.root)

    def test_writes_the_version_to_all_three_files_and_nothing_else(self) -> None:
        major, minor, patch = release.parse(self.start)
        new = f"{major}.{minor + 1}.0"
        before = (self.root / ".codex-plugin/plugin.json").read_text()

        release.set_version(self.root, new)

        self.assertEqual({name: new for name in FILES}, release.versions(self.root))
        after = (self.root / ".codex-plugin/plugin.json").read_text()
        self.assertEqual(before.replace(f'"{self.start}"', f'"{new}"'), after)
        self.assertEqual(json.loads(after)["name"], "projector")

    def test_bump_raises_one_part_and_resets_the_smaller_ones(self) -> None:
        major, minor, patch = release.parse(self.start)
        for part, expected in (("patch", f"{major}.{minor}.{patch + 1}"),
                               ("minor", f"{major}.{minor + 1}.0"),
                               ("major", f"{major + 1}.0.0")):
            with self.subTest(part=part):
                copy_version_files(self.root)
                self.assertEqual(expected, release.bump(self.root, part))
                self.assertEqual(expected, release.current(self.root))

    def test_bump_prints_only_the_new_version(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, release.main(["bump", "patch"], root=self.root))
        major, minor, patch = release.parse(self.start)
        self.assertEqual(f"{major}.{minor}.{patch + 1}\n", out.getvalue())

    def test_refuses_a_version_that_does_not_go_up(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "not above the current version"):
            release.set_version(self.root, self.start)
        with self.assertRaisesRegex(release.ReleaseError, "not X.Y.Z"):
            release.set_version(self.root, "1.0")

    def test_refuses_files_that_disagree(self) -> None:
        path = self.root / ".codex-plugin/plugin.json"
        path.write_text(path.read_text().replace(f'"{self.start}"', '"9.9.9"'))
        with self.assertRaisesRegex(release.ReleaseError, "the version files disagree"):
            release.current(self.root)


class TagTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.remote = tmp / "remote.git"
        run(tmp, "git", "init", "--quiet", "--bare", str(self.remote))
        self.repo = tmp / "repo"
        run(tmp, "git", "clone", "--quiet", str(self.remote), str(self.repo))
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            run(self.repo, "git", "config", key, value)
        copy_version_files(self.repo)
        self.commit("0.5.0")
        self.releases: list[tuple[str, str]] = []
        patcher = mock.patch.object(release, "create_release", side_effect=lambda root, remote, tag: self.releases.append((remote, tag)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def commit(self, version: str) -> str:
        for name in FILES:
            path = self.repo / name
            if name == "pyproject.toml":
                path.write_text('[project]\nname = "projector-cli"\nversion = "%s"\n' % version)
            else:
                data = json.loads(path.read_text())
                data["version"] = version
                path.write_text(json.dumps(data, indent=2) + "\n")
        run(self.repo, "git", "add", "-A")
        run(self.repo, "git", "commit", "--quiet", "-m", version)
        # The branch is not main: a developer's pre-push hook may refuse pushes to main.
        run(self.repo, "git", "push", "--quiet", "origin", "HEAD:trunk")
        return run(self.repo, "git", "rev-parse", "HEAD")

    def remote_tag(self, name: str) -> str:
        return run(self.remote, "git", "rev-parse", f"{name}^{{commit}}")

    def test_tags_the_release_and_moves_the_major_tag(self) -> None:
        first = run(self.repo, "git", "rev-parse", "HEAD")
        release.tag(self.repo, branch="trunk")
        self.assertEqual(first, self.remote_tag("v0.5.0"))
        self.assertEqual(first, self.remote_tag("v0"))

        second = self.commit("0.5.1")
        release.tag(self.repo, branch="trunk")
        self.assertEqual(first, self.remote_tag("v0.5.0"), "a release tag never moves")
        self.assertEqual(second, self.remote_tag("v0.5.1"))
        self.assertEqual(second, self.remote_tag("v0"))

    def test_creates_the_github_release_for_the_exact_tag(self) -> None:
        release.tag(self.repo, branch="trunk")
        self.assertEqual([("origin", "v0.5.0")], self.releases)

    def test_no_release_pushes_only_the_tags(self) -> None:
        release.tag(self.repo, branch="trunk", github_release=False)
        self.assertEqual([], self.releases)
        self.assertTrue(self.remote_tag("v0.5.0"))

    def test_a_failed_release_says_the_tags_are_pushed_and_how_to_finish(self) -> None:
        with mock.patch.object(release, "create_release", side_effect=release.ReleaseError("gh release create failed: HTTP 403")):
            with self.assertRaisesRegex(release.ReleaseError, r"v0\.5\.0 and v0 are pushed.*gh release create v0\.5\.0"):
                release.tag(self.repo, branch="trunk")
        self.assertTrue(self.remote_tag("v0.5.0"))

    def test_refuses_to_retag_a_released_version(self) -> None:
        release.tag(self.repo, branch="trunk")
        with self.assertRaisesRegex(release.ReleaseError, "v0.5.0 already exists"):
            release.tag(self.repo, branch="trunk")

    def released(self) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = release.main(["released", "--branch", "trunk"], root=self.repo)
        return code, out.getvalue()

    def test_released_says_whether_the_merged_version_is_tagged(self) -> None:
        self.assertEqual((release.NOT_RELEASED, "v0.5.0 is not released yet\n"), self.released())

        release.tag(self.repo, branch="trunk")
        self.commit_other_change()
        self.assertEqual((0, "v0.5.0 is released\n"), self.released())

        self.commit("0.5.1")
        self.assertEqual((release.NOT_RELEASED, "v0.5.1 is not released yet\n"), self.released())

    def test_released_fails_as_an_error_when_it_cannot_tell(self) -> None:
        run(self.repo, "git", "remote", "set-url", "origin", str(self.repo.parent / "missing.git"))
        err = io.StringIO()
        with redirect_stderr(err):
            code = release.main(["released", "--branch", "trunk"], root=self.repo)
        self.assertEqual(1, code, "an error is not an answer the workflow can act on")
        self.assertIn("git fetch", err.getvalue())

    def commit_other_change(self) -> None:
        (self.repo / "notes.txt").write_text("not a release\n")
        run(self.repo, "git", "add", "-A")
        run(self.repo, "git", "commit", "--quiet", "-m", "notes")
        run(self.repo, "git", "push", "--quiet", "origin", "HEAD:trunk")

    def test_reads_the_version_from_the_remote_branch_not_the_checkout(self) -> None:
        # An unpushed local bump must not name the release.
        (self.repo / "pyproject.toml").write_text('[project]\nname = "projector-cli"\nversion = "0.9.0"\n')
        release.tag(self.repo, branch="trunk")
        self.assertTrue(self.remote_tag("v0.5.0"))


class RepoSlugTests(unittest.TestCase):
    def test_reads_github_remotes(self) -> None:
        for url in ("git@github.com:o/r.git", "ssh://git@github.com/o/r.git", "https://github.com/o/r.git", "https://github.com/o/r"):
            self.assertEqual("o/r", release.repo_slug(url), url)
        self.assertEqual("", release.repo_slug("/tmp/remote.git"))


if __name__ == "__main__":
    unittest.main()
