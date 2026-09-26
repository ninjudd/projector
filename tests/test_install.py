from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.user_root = Path(self.temporary.name)
        self.claude = self.user_root / "claude"
        self.codex = self.user_root / "codex"
        self.fake_bin = self.user_root / "bin"
        self.log = self.user_root / "commands.log"
        self.fake_bin.mkdir()
        for command, executable in (
            ("pipx", "pipx"),
            ("claude", "host-a"),
            ("codex", "host-b"),
        ):
            path = self.fake_bin / executable
            path.write_text(
                "#!/bin/sh\n"
                f"printf '%s %s\\n' '{command}' \"$*\" >> \"$PROJECTOR_TEST_LOG\"\n"
                "exit 0\n"
            )
            path.chmod(0o755)
        # Each host is asked what it has before anything is installed, and
        # answers from state files a test writes; no file means nothing yet.
        self.state = self.user_root / "host-state"
        self.state.mkdir()
        for command, executable in (("claude", "host-a"), ("codex", "host-b")):
            (self.fake_bin / executable).write_text(
                "#!/bin/sh\n"
                f"printf '%s %s\\n' '{command}' \"$*\" >> \"$PROJECTOR_TEST_LOG\"\n"
                'case "$*" in\n'
                f'  "plugin marketplace list --json") cat "$PROJECTOR_TEST_STATE/{command}-marketplaces.json" 2>/dev/null ;;\n'
                f'  "plugin list --json") cat "$PROJECTOR_TEST_STATE/{command}-plugins.json" 2>/dev/null ;;\n'
                "esac\n"
                "exit 0\n"
            )
        # pipx is asked where its venvs live and is handed an interpreter when
        # one of them already holds this package; the fake reports both.
        (self.fake_bin / "pipx").write_text(
            "#!/bin/sh\n"
            "printf '%s %s\\n' pipx \"$*\" >> \"$PROJECTOR_TEST_LOG\"\n"
            "[ -n \"${PIPX_DEFAULT_PYTHON:-}\" ] && "
            "printf 'PIPX_DEFAULT_PYTHON=%s\\n' \"$PIPX_DEFAULT_PYTHON\" >> \"$PROJECTOR_TEST_LOG\"\n"
            "case \"$1\" in environment) printf '%s\\n' \"${PROJECTOR_TEST_VENVS:-}\" ;; esac\n"
            "exit 0\n"
        )
        self.environment = {
            **os.environ,
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "HOME": str(self.user_root),
            "PROJECTOR_USER_ROOT": str(self.user_root),
            "PROJECTOR_CLAUDE_DIR": str(self.claude),
            "PROJECTOR_CODEX_DIR": str(self.codex),
            "PROJECTOR_TEST_LOG": str(self.log),
            "PROJECTOR_TEST_STATE": str(self.state),
            "PROJECTOR_CLAUDE_COMMAND": str(self.fake_bin / "host-a"),
            "PROJECTOR_CODEX_COMMAND": str(self.fake_bin / "host-b"),
            # The installer fetches the checkout's upstream before it says
            # whether the checkout is behind; this repository's upstream is
            # GitHub, which a unit test must not reach for.
            "PROJECTOR_OFFLINE": "1",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def install(self, target: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROOT / "install.sh"), target],
            cwd=ROOT,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def installer_in(
        self, checkout: Path, target: str, *, offline: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """Run the copy of install.sh inside `checkout`, so its REPO is that clone.

        The clone's remote is a bare repository on disk, so fetching it is
        safe; `offline` keeps the switch the other tests set.
        """

        environment = dict(self.environment)
        if not offline:
            del environment["PROJECTOR_OFFLINE"]
        return subprocess.run(
            [str(checkout / "install.sh"), target],
            cwd=checkout,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def git(self, repo: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
             "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def clone_with_remote(self) -> tuple[Path, Path]:
        """A clone of a bare remote holding the installer, and a second clone
        through which a test moves that remote ahead."""

        # The branch is `trunk` rather than `main`, so that a hook on a
        # developer's machine that guards the default branch names on every
        # remote does not refuse the fixture's own pushes.
        remote = self.user_root / "remote.git"
        subprocess.run(
            ["git", "init", "--quiet", "--bare", "--initial-branch=trunk", str(remote)],
            check=True, capture_output=True,
        )
        seed = self.user_root / "seed"
        self.git(self.user_root, "clone", "--quiet", str(remote), str(seed))
        shutil.copy(ROOT / "install.sh", seed / "install.sh")
        (seed / ".claude-plugin").mkdir()
        shutil.copy(ROOT / ".claude-plugin" / "plugin.json", seed / ".claude-plugin" / "plugin.json")
        self.git(seed, "add", "-A")
        self.git(seed, "commit", "--quiet", "-m", "Seed")
        self.git(seed, "push", "--quiet", "-u", "origin", "trunk")
        checkout = self.user_root / "checkout"
        self.git(self.user_root, "clone", "--quiet", str(remote), str(checkout))
        return checkout, seed

    def advance(self, seed: Path, count: int) -> None:
        for index in range(count):
            (seed / f"change-{index}.txt").write_text("moved on\n")
            self.git(seed, "add", "-A")
            self.git(seed, "commit", "--quiet", "-m", f"Change {index}")
        self.git(seed, "push", "--quiet", "origin", "trunk")

    def test_status_says_how_far_behind_its_upstream_the_checkout_is(self) -> None:
        checkout, seed = self.clone_with_remote()
        self.advance(seed, 2)

        result = self.installer_in(checkout, "status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("repo-behind    ⚠️  trunk is 2 commits behind origin/trunk", result.stdout)
        self.assertIn(f"run git pull in {checkout}", result.stdout)
        # Captured output is not a terminal, so the row carries no color.
        self.assertNotIn("\033[", result.stdout)

    def test_status_reports_a_checkout_matching_its_upstream(self) -> None:
        checkout, _ = self.clone_with_remote()

        result = self.installer_in(checkout, "status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("repo-current   trunk matches origin/trunk\n", result.stdout)
        self.assertNotIn("⚠️", result.stdout)

    def test_install_ends_by_saying_the_checkout_is_behind(self) -> None:
        checkout, seed = self.clone_with_remote()
        self.advance(seed, 1)

        result = self.installer_in(checkout, "cli")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"pipx install --force {checkout}", self.log.read_text())
        self.assertIn("trunk is 1 commit behind origin/trunk", result.stdout.splitlines()[-1])

    def test_an_unreachable_remote_compares_against_the_last_fetch(self) -> None:
        checkout, seed = self.clone_with_remote()
        self.advance(seed, 1)
        self.git(checkout, "remote", "set-url", "origin", str(self.user_root / "gone.git"))

        result = self.installer_in(checkout, "status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "repo-current   trunk matches origin/trunk "
            "(could not fetch origin; compared against its last fetch)",
            result.stdout,
        )

    def test_offline_skips_the_fetch_and_says_so(self) -> None:
        checkout, seed = self.clone_with_remote()
        self.advance(seed, 1)

        result = self.installer_in(checkout, "status", offline=True)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "repo-current   trunk matches origin/trunk "
            "(offline; compared against the last fetch of origin)",
            result.stdout,
        )

    def test_a_detached_checkout_has_nothing_to_compare_against(self) -> None:
        checkout, _ = self.clone_with_remote()
        self.git(checkout, "checkout", "--quiet", "--detach")

        result = self.installer_in(checkout, "status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("repo-untracked detached HEAD has no upstream", result.stdout)

    def test_a_pruned_upstream_is_named_rather_than_read_as_current(self) -> None:
        # A feature branch whose remote counterpart was deleted after merge:
        # the clone still tracks it, and a pruning fetch removes the ref in
        # the middle of the check, so the count has nothing to run against.
        checkout, seed = self.clone_with_remote()
        self.git(seed, "checkout", "--quiet", "-b", "feat")
        (seed / "feat.txt").write_text("merged and gone\n")
        self.git(seed, "add", "-A")
        self.git(seed, "commit", "--quiet", "-m", "Feature")
        self.git(seed, "push", "--quiet", "-u", "origin", "feat")
        self.git(checkout, "fetch", "--quiet", "origin")
        self.git(checkout, "checkout", "--quiet", "feat")
        self.git(seed, "push", "--quiet", "origin", "--delete", "feat")
        self.git(checkout, "config", "fetch.prune", "true")

        result = self.installer_in(checkout, "status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "repo-untracked feat tracks origin/feat, which no longer exists", result.stdout
        )
        self.assertNotIn("repo-current", result.stdout)

    def test_host_installs_remove_only_exact_legacy_links(self) -> None:
        self.claude.mkdir()
        self.codex.mkdir()
        (self.claude / "skills").symlink_to(ROOT / "skills")
        (self.codex / "skills").symlink_to(ROOT / "skills")
        (self.user_root / "CLAUDE.md").symlink_to(ROOT / "AGENTS.md")
        (self.codex / "AGENTS.md").symlink_to(ROOT / "AGENTS.md")
        settings = self.claude / "settings.json"
        settings.write_text('{"user": true}\n')
        unrelated = self.codex / "keep"
        unrelated.symlink_to(self.user_root / "somewhere-else")

        claude = self.install("claude")
        codex = self.install("codex")

        self.assertEqual(0, claude.returncode, claude.stderr)
        self.assertEqual(0, codex.returncode, codex.stderr)
        self.assertFalse((self.claude / "skills").exists())
        self.assertFalse((self.codex / "skills").exists())
        self.assertFalse((self.user_root / "CLAUDE.md").exists())
        self.assertFalse((self.codex / "AGENTS.md").exists())
        self.assertEqual('{"user": true}\n', settings.read_text())
        self.assertTrue(unrelated.is_symlink())
        log = self.log.read_text()
        # The source is this checkout's own origin, whatever it is on the
        # machine running the tests; the fixture tests below pin exact ones.
        self.assertRegex(log, r"claude plugin marketplace add \S+ --scope user\n")
        self.assertIn("claude plugin install projector@projector --scope user", log)
        self.assertRegex(log, r"codex plugin marketplace add \S+\n")
        self.assertIn("codex plugin add projector@projector", log)

    def host_has(
        self, host: str, *, marketplace: str, version: str, source: str | None = None
    ) -> None:
        """Make the fake host report the marketplace and an installed plugin.

        `marketplace` is the source kind each host lists: `directory` or
        `github` for Claude Code, `local` or `git` for Codex. install.sh reads
        it to decide whether the marketplace reads a checkout, which it moves,
        or a remote, which it refreshes. `source` is the path or repository the
        marketplace reads, this checkout by default.
        """

        source = source or str(ROOT)
        if host == "claude":
            record = {"name": "projector", "source": marketplace}
            record["repo" if marketplace == "github" else "path"] = source
            markets = [record]
            plugins = [{"id": "projector@projector", "version": version}]
        else:
            markets = {"marketplaces": [{"name": "projector", "marketplaceSource": {
                "sourceType": marketplace, "source": source}}]}
            plugins = {"installed": [{"pluginId": "projector@projector", "version": version}]}
        (self.state / f"{host}-marketplaces.json").write_text(json.dumps(markets))
        (self.state / f"{host}-plugins.json").write_text(json.dumps(plugins))

    def checkout_with_origin(self, url: str | None) -> Path:
        """A clone whose `origin` is `url`, or that has no `origin` at all.

        The installer reads the marketplace source off that remote, so this is
        how a test pins one without depending on the origin of the checkout
        running the tests.
        """

        checkout, _ = self.clone_with_remote()
        if url is None:
            self.git(checkout, "remote", "remove", "origin")
        else:
            self.git(checkout, "remote", "set-url", "origin", url)
        return checkout

    def test_hosts_install_the_plugin_from_the_checkouts_github_origin(self) -> None:
        for url in ("git@github.com:acme/projector.git", "https://github.com/acme/projector",
                    "ssh://git@github.com/acme/projector.git"):
            with self.subTest(origin=url):
                self.log.write_text("")
                checkout = self.checkout_with_origin(url)

                claude = self.installer_in(checkout, "claude", offline=True)
                codex = self.installer_in(checkout, "codex", offline=True)

                self.assertEqual(0, claude.returncode, claude.stderr)
                self.assertEqual(0, codex.returncode, codex.stderr)
                log = self.log.read_text()
                self.assertIn("claude plugin marketplace add acme/projector --scope user\n", log)
                self.assertIn("codex plugin marketplace add https://github.com/acme/projector.git\n", log)
                self.assertNotIn(str(checkout), log.replace(f"pipx install --force {checkout}", ""))
                shutil.rmtree(checkout)
                shutil.rmtree(self.user_root / "seed")
                shutil.rmtree(self.user_root / "remote.git")

    def test_a_remote_that_is_not_github_is_handed_to_both_hosts_as_it_is(self) -> None:
        checkout = self.checkout_with_origin("https://git.example.com/acme/projector.git")

        claude = self.installer_in(checkout, "claude", offline=True)
        codex = self.installer_in(checkout, "codex", offline=True)

        self.assertEqual(0, claude.returncode, claude.stderr)
        self.assertEqual(0, codex.returncode, codex.stderr)
        log = self.log.read_text()
        self.assertIn(
            "claude plugin marketplace add https://git.example.com/acme/projector.git --scope user\n", log
        )
        self.assertIn("codex plugin marketplace add https://git.example.com/acme/projector.git\n", log)

    def test_a_checkout_without_an_origin_installs_from_itself(self) -> None:
        checkout = self.checkout_with_origin(None)

        claude = self.installer_in(checkout, "claude", offline=True)
        codex = self.installer_in(checkout, "codex", offline=True)

        self.assertEqual(0, claude.returncode, claude.stderr)
        self.assertEqual(0, codex.returncode, codex.stderr)
        log = self.log.read_text()
        self.assertIn(f"claude plugin marketplace add {checkout} --scope user\n", log)
        self.assertIn(f"codex plugin marketplace add {checkout}\n", log)

    def test_a_marketplace_reading_a_checkout_is_moved_to_the_repository(self) -> None:
        checkout = self.checkout_with_origin("https://github.com/acme/projector.git")
        self.host_has("claude", marketplace="directory", version="0.2.0", source=str(checkout))
        self.host_has("codex", marketplace="local", version="0.2.0", source=str(checkout))

        claude = self.installer_in(checkout, "claude", offline=True)
        codex = self.installer_in(checkout, "codex", offline=True)

        self.assertEqual(0, claude.returncode, claude.stderr)
        self.assertEqual(0, codex.returncode, codex.stderr)
        log = self.log.read_text()
        self.assertLess(
            log.index("claude plugin marketplace remove projector\n"),
            log.index("claude plugin marketplace add acme/projector --scope user\n"),
        )
        self.assertIn("claude plugin update projector@projector\n", log)
        self.assertLess(
            log.index("codex plugin marketplace remove projector\n"),
            log.index("codex plugin marketplace add https://github.com/acme/projector.git\n"),
        )
        self.assertIn("codex plugin add projector@projector\n", log)
        self.assertNotIn("marketplace update", log)
        self.assertNotIn("marketplace upgrade", log)
        self.assertIn(f"moved          claude marketplace {checkout} -> acme/projector", claude.stdout)
        self.assertIn(
            f"moved          codex marketplace {checkout} -> https://github.com/acme/projector.git",
            codex.stdout,
        )

    def test_a_local_marketplace_stays_when_there_is_nowhere_to_move_it(self) -> None:
        # Codex reads a local marketplace live, and asking it to upgrade one is
        # an error rather than a no-op; with no origin to move to, it is left
        # exactly as it is.
        checkout = self.checkout_with_origin(None)
        self.host_has("claude", marketplace="directory", version="0.2.0", source=str(checkout))
        self.host_has("codex", marketplace="local", version="0.2.0", source=str(checkout))

        claude = self.installer_in(checkout, "claude", offline=True)
        codex = self.installer_in(checkout, "codex", offline=True)

        self.assertEqual(0, claude.returncode, claude.stderr)
        self.assertEqual(0, codex.returncode, codex.stderr)
        log = self.log.read_text()
        self.assertIn("claude plugin marketplace update projector\n", log)
        self.assertNotIn("marketplace remove", log)
        self.assertNotIn("marketplace add", log)
        self.assertNotIn("marketplace upgrade", log)
        self.assertIn("codex plugin add projector@projector\n", log)
        self.assertNotIn("moved", claude.stdout + codex.stdout)

    def test_host_installs_update_what_is_already_installed(self) -> None:
        self.host_has("claude", marketplace="github", version="0.2.0", source="acme/projector")
        self.host_has("codex", marketplace="git", version="0.2.0",
                      source="https://github.com/acme/projector.git")

        claude = self.install("claude")
        codex = self.install("codex")

        self.assertEqual(0, claude.returncode, claude.stderr)
        self.assertEqual(0, codex.returncode, codex.stderr)
        log = self.log.read_text()
        self.assertIn("claude plugin marketplace update projector\n", log)
        self.assertIn("claude plugin update projector@projector\n", log)
        self.assertNotIn("marketplace add", log)
        self.assertNotIn("plugin install", log)
        self.assertIn("codex plugin marketplace upgrade projector\n", log)
        self.assertIn("codex plugin add projector@projector\n", log)

    def test_status_compares_installed_plugins_with_the_checkout(self) -> None:
        expected = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
        self.host_has("claude", marketplace="directory", version="0.2.0")
        self.host_has("codex", marketplace="git", version=expected)

        result = self.install("status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"claude installed 0.2.0, checkout {expected}", result.stdout)
        self.assertIn("plugin-stale", result.stdout)
        self.assertIn(f"plugin-current codex {expected}", result.stdout)
        self.assertIn(f"marketplace    codex git {ROOT}", result.stdout)

    def test_status_flags_a_marketplace_that_reads_a_checkout(self) -> None:
        checkout = self.checkout_with_origin("https://github.com/acme/projector.git")
        self.host_has("claude", marketplace="directory", version="0.2.0", source=str(checkout))
        self.host_has("codex", marketplace="git", version="0.2.0",
                      source="https://github.com/acme/projector.git")

        result = self.installer_in(checkout, "status", offline=True)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"marketplace    claude directory {checkout}", result.stdout)
        self.assertIn(
            "from-checkout  ⚠️  claude installs from a checkout and goes stale with it "
            "-- run ./install.sh claude to move it to acme/projector",
            result.stdout,
        )
        self.assertEqual(1, result.stdout.count("from-checkout"))

    def test_status_reports_a_host_with_no_plugin(self) -> None:
        result = self.install("status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("plugin-absent  claude", result.stdout)
        self.assertIn("plugin-absent  codex", result.stdout)

    def test_all_skips_one_missing_host_and_installs_the_other(self) -> None:
        self.environment["PROJECTOR_CLAUDE_COMMAND"] = "missing-claude"

        result = self.install("all")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("skipped", result.stdout)
        self.assertIn("codex plugin add projector@projector", self.log.read_text())

    def test_all_fails_when_both_hosts_are_missing(self) -> None:
        self.environment["PROJECTOR_CLAUDE_COMMAND"] = "missing-claude"
        self.environment["PROJECTOR_CODEX_COMMAND"] = "missing-codex"

        result = self.install("all")

        self.assertEqual(69, result.returncode)
        self.assertIn("neither Claude Code nor Codex", result.stderr)

    def test_cli_install_uses_an_isolated_pipx_application(self) -> None:
        result = self.install("cli")

        self.assertEqual(0, result.returncode, result.stderr)
        log = self.log.read_text().splitlines()
        self.assertEqual(f"pipx install --force {ROOT}", log[-1])
        # No venv yet, so pipx picks the interpreter as it always has.
        self.assertNotIn("PIPX_DEFAULT_PYTHON", self.log.read_text())

    def test_cli_reinstall_keeps_the_interpreter_the_venv_was_made_with(self) -> None:
        venv = self.user_root / "venvs" / "projector-cli"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text(
            "home = /opt/py/bin\n"
            "version = 3.12.4\n"
            "executable = /opt/py/bin/python3.12\n"
        )
        self.environment["PROJECTOR_TEST_VENVS"] = str(venv.parent)

        result = self.install("cli")

        self.assertEqual(0, result.returncode, result.stderr)
        log = self.log.read_text().splitlines()
        self.assertEqual(f"pipx install --force {ROOT}", log[-2])
        self.assertEqual("PIPX_DEFAULT_PYTHON=/opt/py/bin/python3.12", log[-1])

    def fake_project(self, package_dir: str | None) -> None:
        """A `project` on PATH ahead of any real one.

        Answers `--package-dir` with `package_dir`, or fails the way a command
        too old to know the flag would when it is None.
        """

        path = self.fake_bin / "project"
        if package_dir is None:
            body = 'echo "usage: project" >&2; exit 2'
        else:
            body = (
                'case "$1" in\n'
                f'  --package-dir) echo "{package_dir}" ;;\n'
                '  --version) echo "project 9.9.9" ;;\n'
                'esac'
            )
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def installed_copy(self, *, diverge: bool = False, diverge_asset: bool = False) -> str:
        """A stand-in for the package a copying installer left behind.

        Laid out as the wheel lays it out: the source package, with the site's
        assets from site/assets/ at projector/site/assets/.
        """

        target = self.user_root / "site-packages" / "projector"
        shutil.copytree(ROOT / "src" / "projector", target,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "site" / "assets", target / "site" / "assets")
        if diverge:
            (target / "cli.py").write_text(
                (target / "cli.py").read_text() + "\n# shipped since this install\n"
            )
        if diverge_asset:
            (target / "site" / "assets" / "site.css").write_text(
                (target / "site" / "assets" / "site.css").read_text() + "\n/* shipped since this install */\n"
            )
        return str(target)

    def test_status_reports_a_cli_matching_the_checkout(self) -> None:
        self.fake_project(self.installed_copy(diverge=False))

        result = self.install("status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("cli-current", result.stdout)
        self.assertNotIn("cli-stale", result.stdout)

    def test_status_reports_a_cli_left_behind_by_the_checkout(self) -> None:
        # The case a version comparison misses: the CLI changed and nobody
        # bumped the version, so the installed copy still calls itself current.
        self.fake_project(self.installed_copy(diverge=True))

        result = self.install("status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("cli-stale", result.stdout)
        self.assertIn("differs from this checkout", result.stdout)

    def test_status_reports_a_cli_whose_site_assets_changed(self) -> None:
        # The assets are built in site/assets/, outside src/projector, so a
        # stylesheet that changed since the install must still read as stale.
        self.fake_project(self.installed_copy(diverge_asset=True))

        result = self.install("status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("cli-stale", result.stdout)
        self.assertIn("differs from this checkout", result.stdout)

    def test_status_treats_a_cli_that_cannot_locate_itself_as_stale(self) -> None:
        # An install old enough to predate --package-dir cannot answer, and an
        # command that cannot say where it lives is stale by definition.
        self.fake_project(None)

        result = self.install("status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("cli-stale", result.stdout)
        self.assertIn("cannot report its source", result.stdout)

    def test_unknown_target_is_command_misuse(self) -> None:
        result = self.install("unknown")
        self.assertEqual(64, result.returncode)
        self.assertIn("usage:", result.stderr)



ARCHIVE = "https://github.com/ninjudd/projector/archive/v0.tar.gz"


class ReleaseInstallTests(unittest.TestCase):
    """The installer piped from curl, with no checkout beside it."""

    setUp = InstallTests.setUp
    tearDown = InstallTests.tearDown
    fake_project = InstallTests.fake_project

    def piped(self, *arguments: str, environment: dict | None = None) -> subprocess.CompletedProcess[str]:
        with open(ROOT / "install.sh") as script:
            return subprocess.run(
                ["bash", "-s", "--", *arguments],
                stdin=script,
                cwd=self.user_root,
                env=environment or self.environment,
                check=False,
                capture_output=True,
                text=True,
            )

    def logged(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_the_cli_installs_from_the_release_archive(self) -> None:
        result = self.piped("cli")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"pipx install --force {ARCHIVE} --pip-args=--no-cache-dir", self.logged())

    def test_a_ref_and_a_fork_choose_the_archive(self) -> None:
        environment = {**self.environment, "PROJECTOR_REF": "v0.5.6", "PROJECTOR_REPO": "someone/projector"}

        self.piped("cli", environment=environment)

        self.assertIn("pipx install --force https://github.com/someone/projector/archive/v0.5.6.tar.gz "
                      "--pip-args=--no-cache-dir", self.logged())

    def test_the_plugins_install_from_the_github_marketplace(self) -> None:
        result = self.piped("all")

        self.assertEqual(0, result.returncode, result.stderr)
        log = self.logged()
        self.assertIn("claude plugin marketplace add ninjudd/projector --scope user", log)
        self.assertIn("claude plugin install projector@projector --scope user", log)
        self.assertIn("codex plugin marketplace add https://github.com/ninjudd/projector.git", log)
        self.assertIn("codex plugin add projector@projector", log)

    def test_a_marketplace_left_reading_a_checkout_moves_to_github(self) -> None:
        (self.state / "claude-marketplaces.json").write_text(json.dumps(
            [{"name": "projector", "source": "directory", "path": "/old/projector"}]))

        result = self.piped("claude")

        self.assertEqual(0, result.returncode, result.stderr)
        log = self.logged()
        self.assertIn("claude plugin marketplace remove projector", log)
        self.assertIn("claude plugin marketplace add ninjudd/projector --scope user", log)
        self.assertIn("moved          claude marketplace /old/projector -> ninjudd/projector", result.stdout)

    def fake_curl(self, version: str) -> None:
        # Multi-line, as the real manifest is, which is what the installer reads.
        manifest = json.dumps({"name": "projector", "version": version}, indent=2)
        path = self.fake_bin / "curl"
        path.write_text("#!/bin/sh\n"
                        "printf '%s %s\\n' curl \"$*\" >> \"$PROJECTOR_TEST_LOG\"\n"
                        f"printf '%s\\n' '{manifest}'\n")
        path.chmod(0o755)

    def test_status_compares_the_installed_versions_with_the_release(self) -> None:
        self.fake_project("/unused")  # answers --version with 9.9.9
        self.fake_curl("9.9.9")
        (self.state / "claude-plugins.json").write_text(json.dumps([{"id": "projector@projector", "version": "9.9.8"}]))
        environment = {k: v for k, v in self.environment.items() if k != "PROJECTOR_OFFLINE"}

        result = self.piped("status", environment=environment)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("release        ninjudd/projector v0", result.stdout)
        self.assertIn("cli-current    9.9.9", result.stdout)
        self.assertIn("plugin-stale   claude installed 9.9.8, release 9.9.9 -- run project upgrade claude", result.stdout)
        self.assertIn("curl -fsSL --max-time 15 "
                      "https://raw.githubusercontent.com/ninjudd/projector/v0/.claude-plugin/plugin.json", self.logged())

    def test_status_offline_reports_versions_without_a_verdict(self) -> None:
        self.fake_project("/unused")

        result = self.piped("status")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("cli-installed  9.9.9 (no release version to compare against)", result.stdout)
        self.assertNotIn("repo-", result.stdout, "no checkout, so nothing to compare with an upstream")

    def fake_python(self, *, new_enough: bool = True) -> Path:
        """A python3 that passes or fails the version check and makes a fake venv."""

        path = self.user_root / "python3"
        template = self.user_root / "venv-python"
        template.write_text('#!/bin/sh\nprintf "venv-python %s\\n" "$*" >> "$PROJECTOR_TEST_LOG"\n')
        template.chmod(0o755)
        path.write_text(
            "#!/bin/sh\n"
            'printf "python3 %s\\n" "$*" >> "$PROJECTOR_TEST_LOG"\n'
            'case "$1" in\n'
            f"  -c) exit {0 if new_enough else 1} ;;\n"
            f'  -m) mkdir -p "$3/bin" && cp "{template}" "$3/bin/python" && cp "{template}" "$3/bin/project" ;;\n'
            "esac\n"
        )
        path.chmod(0o755)
        return path

    def without_pipx(self, python: Path) -> dict:
        (self.fake_bin / "pipx").unlink()
        return {**self.environment, "PATH": f"{self.fake_bin}:/usr/bin:/bin", "PROJECTOR_PYTHON": str(python),
                "PROJECTOR_VENV": str(self.user_root / "venv"), "PROJECTOR_BIN_DIR": str(self.user_root / "local-bin")}

    def test_without_pipx_the_cli_gets_a_venv_and_a_link(self) -> None:
        environment = self.without_pipx(self.fake_python())

        result = self.piped("cli", environment=environment)

        self.assertEqual(0, result.returncode, result.stderr)
        venv = self.user_root / "venv"
        self.assertIn(f"python3 -m venv {venv}", self.logged())
        self.assertIn(f"venv-python -m pip install --quiet --upgrade --no-cache-dir {ARCHIVE}", self.logged())
        link = self.user_root / "local-bin" / "project"
        self.assertEqual(venv / "bin" / "project", Path(os.readlink(link)))
        self.assertIn("not-on-path", result.stdout, "the link's directory is not on this PATH")

    def test_without_pipx_an_old_python_is_refused(self) -> None:
        environment = self.without_pipx(self.fake_python(new_enough=False))

        result = self.piped("cli", environment=environment)

        self.assertEqual(69, result.returncode)
        self.assertIn("Projector needs Python 3.11 or newer", result.stderr)
        self.assertFalse((self.user_root / "venv").exists())


if __name__ == "__main__":
    unittest.main()
