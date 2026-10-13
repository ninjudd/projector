from __future__ import annotations

import importlib.util
import io
import json
import os
import re
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

FAKE_GH = r"""#!/usr/bin/env bash
# Plays GitHub's releases: $FAKE_GH_DIR/releases lists the tags that have one.
# Logs one line per call to $FAKE_GH_DIR/calls.log. With FAKE_GH_FAIL set to a
# command such as "release create", that command fails as GitHub refusing it.
set -u
printf '%s\n' "$*" >> "$FAKE_GH_DIR/calls.log"
if [ "$1 $2" = "${FAKE_GH_FAIL:-}" ]; then
  echo "HTTP 403: Resource not accessible by integration" >&2
  exit 1
fi
case "$1 $2" in
  "release view")
    if grep -qxF "$3" "$FAKE_GH_DIR/releases" 2>/dev/null; then
      printf '{"tagName":"%s"}\n' "$3"
    else
      echo "release not found" >&2
      exit 1
    fi ;;
  "release create") printf '%s\n' "$3" >> "$FAKE_GH_DIR/releases" ;;
  *) echo "fake gh: unexpected $*" >&2; exit 2 ;;
esac
"""

# A pre-receive hook that rejects every update to v0. It stands in for GitHub
# rejecting the push of the major tag after it accepted the version tag.
REFUSE_MAJOR_TAG = r"""#!/usr/bin/env bash
while read -r old new ref; do
  if [ "$ref" = refs/tags/v0 ]; then
    echo "fatal error in commit_refs" >&2
    exit 1
  fi
done
"""


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
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.remote = tmp / "remote.git"
        run(tmp, "git", "init", "--quiet", "--bare", str(self.remote))
        self.repo = tmp / "repo"
        run(tmp, "git", "clone", "--quiet", str(self.remote), str(self.repo))
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            run(self.repo, "git", "config", key, value)
        copy_version_files(self.repo)
        self.commit("0.5.0")
        self.github = tmp / "github"
        self.github.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        env = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                                           "FAKE_GH_DIR": str(self.github)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FAKE_GH_FAIL", None)
        # The test remote is a local path, which names no GitHub repository.
        slug = mock.patch.object(release, "repo_slug", return_value="test/projector")
        slug.start()
        self.addCleanup(slug.stop)

    def releases(self) -> list[str]:
        path = self.github / "releases"
        return path.read_text().split() if path.exists() else []

    def gh_calls(self) -> list[str]:
        path = self.github / "calls.log"
        return path.read_text().splitlines() if path.exists() else []

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

    def commit_other_change(self) -> None:
        (self.repo / "notes.txt").write_text("not a release\n")
        run(self.repo, "git", "add", "-A")
        run(self.repo, "git", "commit", "--quiet", "-m", "notes")
        run(self.repo, "git", "push", "--quiet", "origin", "HEAD:trunk")

    def tag_by_hand(self, name: str, commit: str) -> None:
        run(self.repo, "git", "tag", "-a", name, commit, "-m", name)
        run(self.repo, "git", "push", "--quiet", "origin", f"refs/tags/{name}")

    def remote_tag(self, name: str) -> str:
        return run(self.remote, "git", "rev-parse", f"{name}^{{commit}}")

    def remote_tag_object(self, name: str) -> str:
        return run(self.remote, "git", "rev-parse", f"refs/tags/{name}")

    def tag(self, **kwargs) -> str:
        return release.tag(self.repo, branch="trunk", **kwargs)

    def released(self) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = release.main(["released", "--branch", "trunk"], root=self.repo)
        return code, out.getvalue()

    def test_a_fresh_release_tags_moves_the_major_tag_and_publishes(self) -> None:
        first = run(self.repo, "git", "rev-parse", "HEAD")
        self.assertEqual(f"v0.5.0 at {first[:9]} on origin/trunk: tagged v0.5.0, moved v0, published the GitHub release",
                         self.tag())
        self.assertEqual(first, self.remote_tag("v0.5.0"))
        self.assertEqual(first, self.remote_tag("v0"))
        self.assertEqual(["v0.5.0"], self.releases())

        second = self.commit("0.5.1")
        self.tag()
        self.assertEqual(first, self.remote_tag("v0.5.0"), "a release tag never moves")
        self.assertEqual(second, self.remote_tag("v0.5.1"))
        self.assertEqual(second, self.remote_tag("v0"))
        self.assertEqual(["v0.5.0", "v0.5.1"], self.releases())

    def test_creates_the_github_release_for_the_exact_tag(self) -> None:
        self.tag()
        self.assertIn("release create v0.5.0 --verify-tag --generate-notes --repo test/projector", self.gh_calls())

    def test_no_release_pushes_only_the_tags(self) -> None:
        self.tag(github_release=False)
        self.assertEqual([], self.gh_calls())
        self.assertTrue(self.remote_tag("v0.5.0"))
        commit = self.remote_tag("v0")
        self.assertEqual((release.NOT_RELEASED, "v0.5.0 is not released yet; it still needs the GitHub release\n"),
                         self.released())
        self.assertEqual(f"v0.5.0 at {commit[:9]} on origin/trunk: published the GitHub release "
                         "(already done: the v0.5.0 tag, v0)", self.tag())

    def test_finishes_a_release_whose_major_tag_push_failed(self) -> None:
        first = run(self.repo, "git", "rev-parse", "HEAD")
        self.tag()
        second = self.commit("0.5.1")
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text(REFUSE_MAJOR_TAG)
        hook.chmod(0o755)
        # A developer's global core.hooksPath would hide the remote's own hooks.
        run(self.remote, "git", "config", "core.hooksPath", str(hook.parent))
        with self.assertRaisesRegex(release.ReleaseError,
                                    r"(?s)fatal error in commit_refs.*Before that it tagged v0\.5\.1\. "
                                    r"Run tag again, or re-run the Release workflow, to finish v0\.5\.1"):
            self.tag()
        self.assertEqual(second, self.remote_tag("v0.5.1"))
        self.assertEqual(first, self.remote_tag("v0"))
        self.assertEqual(["v0.5.0"], self.releases())
        tagged = self.remote_tag_object("v0.5.1")

        hook.unlink()
        self.commit_other_change()
        # This clone still has the v0 it moved before the push failed, which
        # the remote's v0 has to replace.
        self.assertEqual((release.NOT_RELEASED,
                          f"v0.5.1 is not released yet; it still needs v0 at {second[:9]}, the GitHub release\n"),
                         self.released())
        self.assertEqual(f"v0.5.1 at {second[:9]} on origin/trunk: moved v0, published the GitHub release "
                         "(already done: the v0.5.1 tag)", self.tag())
        self.assertEqual(tagged, self.remote_tag_object("v0.5.1"), "finishing a release never retags it")
        self.assertEqual(second, self.remote_tag("v0"), "v0 follows the release, not the branch's newer head")
        self.assertEqual(["v0.5.0", "v0.5.1"], self.releases())
        self.assertEqual((0, "v0.5.1 is released\n"), self.released())

    def test_a_failed_github_release_is_finished_by_running_tag_again(self) -> None:
        commit = run(self.repo, "git", "rev-parse", "HEAD")
        os.environ["FAKE_GH_FAIL"] = "release create"
        with self.assertRaisesRegex(release.ReleaseError,
                                    r"gh release create failed: HTTP 403.*Before that it tagged v0\.5\.0, moved v0\."):
            self.tag()
        del os.environ["FAKE_GH_FAIL"]
        self.assertEqual(f"v0.5.0 at {commit[:9]} on origin/trunk: published the GitHub release "
                         "(already done: the v0.5.0 tag, v0)", self.tag())
        self.assertEqual(["v0.5.0"], self.releases())

    def test_tag_does_nothing_once_the_version_is_released(self) -> None:
        commit = run(self.repo, "git", "rev-parse", "HEAD")
        self.tag()
        tagged, major = self.remote_tag_object("v0.5.0"), self.remote_tag_object("v0")
        self.assertEqual(f"v0.5.0 at {commit[:9]} on origin/trunk: nothing to do "
                         "(already done: the v0.5.0 tag, v0, the GitHub release)", self.tag())
        self.assertEqual((tagged, major), (self.remote_tag_object("v0.5.0"), self.remote_tag_object("v0")))
        self.assertEqual(1, sum(call.startswith("release create") for call in self.gh_calls()))

    def test_released_needs_the_tag_the_major_tag_and_the_github_release(self) -> None:
        head = run(self.repo, "git", "rev-parse", "HEAD")
        self.assertEqual((release.NOT_RELEASED, "v0.5.0 is not released yet; it still needs the v0.5.0 tag, "
                          f"v0 at {head[:9]}, the GitHub release\n"), self.released())

        self.tag()
        self.commit_other_change()
        self.assertEqual((0, "v0.5.0 is released\n"), self.released())

        bump = self.commit("0.5.1")
        self.assertEqual((release.NOT_RELEASED, "v0.5.1 is not released yet; it still needs the v0.5.1 tag, "
                          f"v0 at {bump[:9]}, the GitHub release\n"), self.released())

    def assert_refused(self, message: str) -> None:
        major = run(self.remote, "git", "tag", "--list", "v0")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(1, release.main(["released", "--branch", "trunk"], root=self.repo))
        self.assertIn(message, err.getvalue())
        with self.assertRaisesRegex(release.ReleaseError, re.escape(message)):
            self.tag()
        self.assertEqual(major, run(self.remote, "git", "tag", "--list", "v0"), "a refusal moves no tag")
        self.assertFalse(any(call.startswith("release create") for call in self.gh_calls()))

    def test_refuses_a_version_tag_that_is_not_on_the_branch(self) -> None:
        side = run(self.repo, "git", "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "side")
        self.tag_by_hand("v0.5.0", side)
        self.assert_refused(f"v0.5.0 already names {side[:9]}, which is not on origin/trunk")

    def test_refuses_a_version_tag_on_a_commit_that_names_another_version(self) -> None:
        early = run(self.repo, "git", "rev-parse", "HEAD")
        self.tag_by_hand("v0.5.1", early)
        self.commit("0.5.1")
        self.assert_refused(f"v0.5.1 already names {early[:9]}, but origin/trunk names 0.5.0 at {early[:9]}")

    def test_refuses_a_version_the_branch_left_and_came_back_to(self) -> None:
        released_at = self.commit("0.5.1")
        self.tag_by_hand("v0.5.1", released_at)
        reverted = self.commit("0.5.0")
        self.commit("0.5.1")
        self.assert_refused(f"v0.5.1 already names {released_at[:9]}, but origin/trunk names 0.5.0 at {reverted[:9]}")

    def test_released_fails_as_an_error_when_it_cannot_tell(self) -> None:
        for broken, expected in (("gh", "gh release view failed: HTTP 403"), ("git", "git fetch")):
            with self.subTest(broken=broken):
                if broken == "gh":
                    os.environ["FAKE_GH_FAIL"] = "release view"
                else:
                    run(self.repo, "git", "remote", "set-url", "origin", str(self.repo.parent / "missing.git"))
                err = io.StringIO()
                with redirect_stderr(err), redirect_stdout(io.StringIO()):
                    code = release.main(["released", "--branch", "trunk"], root=self.repo)
                self.assertEqual(1, code, "an error is not an answer the workflow can act on")
                self.assertIn(expected, err.getvalue())

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
