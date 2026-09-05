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
        self.assertIn(f"claude plugin marketplace add {ROOT} --scope user", log)
        self.assertIn("claude plugin install projector@projector --scope user", log)
        self.assertIn(f"codex plugin marketplace add {ROOT}", log)
        self.assertIn("codex plugin add projector@projector", log)

    def host_has(self, host: str, *, marketplace: str, version: str) -> None:
        """Make the fake host report the marketplace and an installed plugin.

        `marketplace` is `directory`, `github`, or `git`, which is the field
        install.sh reads to decide whether there is a snapshot to refresh.
        """

        if host == "claude":
            markets = [{"name": "projector", "source": marketplace, "path": str(ROOT)}]
            plugins = [{"id": "projector@projector", "version": version}]
        else:
            markets = {"marketplaces": [{"name": "projector", "marketplaceSource": {
                "sourceType": marketplace, "source": str(ROOT)}}]}
            plugins = {"installed": [{"pluginId": "projector@projector", "version": version}]}
        (self.state / f"{host}-marketplaces.json").write_text(json.dumps(markets))
        (self.state / f"{host}-plugins.json").write_text(json.dumps(plugins))

    def test_host_installs_update_what_is_already_installed(self) -> None:
        self.host_has("claude", marketplace="directory", version="0.2.0")
        self.host_has("codex", marketplace="git", version="0.2.0")

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

    def test_codex_refreshes_only_a_git_marketplace(self) -> None:
        # A local marketplace is read live; asking Codex to upgrade it is an
        # error rather than a no-op.
        self.host_has("codex", marketplace="local", version="0.2.0")

        result = self.install("codex")

        self.assertEqual(0, result.returncode, result.stderr)
        log = self.log.read_text()
        self.assertNotIn("marketplace upgrade", log)
        self.assertNotIn("marketplace add", log)
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

    def installed_copy(self, *, diverge: bool) -> str:
        """A stand-in for the package a copying installer left behind."""

        target = self.user_root / "site-packages" / "projector"
        shutil.copytree(ROOT / "src" / "projector", target,
                        ignore=shutil.ignore_patterns("__pycache__"))
        if diverge:
            (target / "cli.py").write_text(
                (target / "cli.py").read_text() + "\n# shipped since this install\n"
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
        # bumped setup.cfg, so the installed copy still calls itself current.
        self.fake_project(self.installed_copy(diverge=True))

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


if __name__ == "__main__":
    unittest.main()
