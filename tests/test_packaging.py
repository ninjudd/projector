from __future__ import annotations

import importlib.util
import json
import re
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / ".github" / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
PUBLISHED_SKILLS = {
    "spec",
    "implement",
    "finish",
    "review-pr",
    "fix-pr",
    "review-repo",
    "summarize-pr",
    "artifact",
    "write",
    "rewrite",
    "cleanup",
    "write-tests",
    "work",
    "message",
}
# A skill cites another skill's section as: `../<skill>/SKILL.md` describes
# under "<heading>". The heading may be at any level.
SECTION_CITATION = re.compile(r'`\.\./([\w-]+)/SKILL\.md` describes under "([^"]+)"')
HEADING = re.compile(r"^#{1,6} (.+?)\s*$", re.MULTILINE)


class PackagingTests(unittest.TestCase):
    def manifest(self, host: str) -> dict[str, object]:
        return json.loads((ROOT / f".{host}-plugin" / "plugin.json").read_text())

    def test_both_hosts_package_the_same_canonical_skill_tree(self) -> None:
        claude = self.manifest("claude")
        codex = self.manifest("codex")

        self.assertEqual("projector", claude["name"])
        self.assertEqual("projector", codex["name"])
        self.assertEqual("./skills/", claude["skills"])
        self.assertEqual(claude["skills"], codex["skills"])

        discovered = {
            path.parent.name
            for path in (ROOT / "skills").glob("*/SKILL.md")
        }
        self.assertEqual(PUBLISHED_SKILLS, discovered)
        self.assertFalse((ROOT / ".mcp.json").exists())

    def test_one_version_covers_the_cli_and_both_plugin_manifests(self) -> None:
        claude = self.manifest("claude")
        codex = self.manifest("codex")
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

        # A host caches an installed plugin under a directory named by this
        # string, so a one-sided bump updates one host and leaves the other on
        # a stale copy with nothing reporting it. One release tag also names
        # the CLI and the plugin together, so all three must agree.
        self.assertEqual(claude["version"], codex["version"])
        self.assertEqual(claude["version"], project["version"])
        self.assertRegex(str(claude["version"]), r"^\d+\.\d+\.\d+$")

    def test_host_marketplaces_resolve_the_root_plugin(self) -> None:
        claude = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text()
        )
        codex = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text()
        )

        self.assertEqual(".", claude["plugins"][0]["source"])
        self.assertEqual("./", codex["plugins"][0]["source"]["path"])
        self.assertEqual("projector", claude["plugins"][0]["name"])
        self.assertEqual("projector", codex["plugins"][0]["name"])
        self.assertTrue((ROOT / ".claude-plugin" / "plugin.json").exists())
        self.assertTrue((ROOT / ".codex-plugin" / "plugin.json").exists())

    def gh_stack_entries(self) -> dict[str, dict]:
        """Each host's marketplace entry for gh-stack, by host."""

        return {
            host: next(entry for entry in json.loads((ROOT / path).read_text())["plugins"]
                       if entry["name"] == "gh-stack")
            for host, path in (("claude", ".claude-plugin/marketplace.json"),
                               ("codex", ".agents/plugins/marketplace.json"))
        }

    def test_both_hosts_serve_upstreams_gh_stack_skill_from_one_pin(self) -> None:
        # install.sh reads the pin from the Claude Code entry to decide when the
        # gh stack extension is too old, so the entries must name one release.
        entries = self.gh_stack_entries()
        for host, entry in entries.items():
            with self.subTest(host=host):
                source = entry["source"]
                self.assertEqual("git-subdir", source["source"])
                self.assertEqual("https://github.com/github/gh-stack.git", source["url"])
                self.assertEqual("skills", source["path"])
                self.assertEqual(["./gh-stack"], entry["skills"])
                self.assertRegex(source["ref"], r"^v\d+\.\d+\.\d+$")
                self.assertRegex(source["sha"], r"^[0-9a-f]{40}$")
                self.assertEqual(source["ref"], "v" + entry["version"])
        claude, codex = entries["claude"], entries["codex"]
        self.assertEqual(claude["source"], codex["source"])
        self.assertEqual(claude["version"], codex["version"])

    def test_only_claude_code_loads_the_hooks_module(self) -> None:
        # Claude Code finds hooks/hooks.json at the plugin root by itself; the
        # manifest names the module's state contract. Codex uses command hooks instead.
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        [module] = hooks["modules"]
        self.assertTrue((ROOT / "hooks" / module).is_file())
        self.assertEqual("./hooks/types.d.ts", self.manifest("claude")["types"])
        self.assertTrue((ROOT / "hooks" / "types.d.ts").is_file())
        self.assertNotIn("types", self.manifest("codex"))
        # Codex reads hooks/hooks.json too unless the manifest names its hooks, and it
        # cannot parse the module manifest. A real hook here would also need trusting
        # again after every release, so the root plugin names an empty set.
        self.assertEqual({}, self.manifest("codex")["hooks"]["hooks"])
        hooks_plugin = json.loads((ROOT / "codex-hooks" / ".codex-plugin" / "plugin.json").read_text())
        codex_hooks = json.loads((ROOT / "codex-hooks" / hooks_plugin["hooks"]).read_text())
        self.assertNotIn("modules", codex_hooks)
        commands = [handler["command"] for groups in codex_hooks["hooks"].values()
                    for group in groups for handler in group["hooks"]]
        self.assertTrue(commands)
        from projector import cli
        for command in commands:
            args = cli.parser().parse_args(command.split()[1:])
            self.assertEqual("codex-hook", args.watch_command)

    def test_codex_hooks_ship_in_a_plugin_that_a_release_leaves_unchanged(self) -> None:
        # Codex trusts a plugin's hooks by a hash of every file in the plugin, so
        # any file a release touches here would make every user trust the hooks
        # again. The release bumps only the root manifests.
        root = ROOT / "codex-hooks"
        files = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
        self.assertEqual([".codex-plugin/plugin.json", "hooks.json"], files)
        plugin = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual("projector-hooks", plugin["name"])
        self.assertEqual("./hooks.json", plugin["hooks"])
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            for name in release.VERSION_FILES:
                (copy / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / name, copy / name)
            shutil.copytree(root, copy / "codex-hooks")
            # A release that wrote the plugin's own version over it would change
            # no byte, so release a version other than the plugin's.
            while release.bump(copy, "patch") == plugin["version"]:
                pass
            for name in files:
                self.assertEqual((root / name).read_bytes(), (copy / "codex-hooks" / name).read_bytes(), name)
        [entry] = [entry for entry in json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text())["plugins"]
                   if entry["name"] == "projector-hooks"]
        self.assertEqual({"source": "local", "path": "./codex-hooks"}, entry["source"])

    def test_only_claude_code_declares_gh_stack_as_a_dependency(self) -> None:
        # Codex has no plugin dependencies; install.sh adds gh-stack there.
        self.assertEqual(["gh-stack"], self.manifest("claude")["dependencies"])
        self.assertNotIn("dependencies", self.manifest("codex"))

    def test_every_python_package_is_listed_for_the_wheel(self) -> None:
        # The package list is explicit because site/assets is mapped in from
        # outside src/; a package missing from it would silently not ship.
        setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
        on_disk = {
            ".".join(init.parent.relative_to(ROOT / "src").parts)
            for init in (ROOT / "src").rglob("__init__.py")
        }

        self.assertEqual(on_disk | {"projector.site.assets"}, set(setuptools["packages"]))
        self.assertEqual("site/assets", setuptools["package-dir"]["projector.site.assets"])
        self.assertTrue((ROOT / "site" / "assets" / "site.js").is_file())

    def test_neither_the_wheel_nor_a_plugin_ships_ios_or_services(self) -> None:
        # The app and the push service deploy on their own. The wheel maps
        # only src/ and site/assets, and each plugin loads only the paths its
        # manifest and hooks module name, so neither may reach into them.
        apart = ("ios", "services")
        setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
        for package, directory in setuptools["package-dir"].items():
            self.assertNotIn(Path(directory).parts[0], apart, package)
        loaded = [self.manifest("claude")["skills"], self.manifest("claude")["types"], self.manifest("codex")["skills"]]
        loaded += [f"hooks/{module}" for module in json.loads((ROOT / "hooks" / "hooks.json").read_text())["modules"]]
        for path in loaded:
            self.assertNotIn(Path(path).parts[0], apart, path)
        for directory in apart:
            files = [path for path in (ROOT / directory).rglob("*") if "node_modules" not in path.parts]
            # A plugin root or skill in here would be published with Projector,
            # and `claude plugin test .` runs every *.test.ts under the root as
            # a test of the hooks module, so the service names its tests otherwise.
            for path in files:
                self.assertNotIn(path.name, {"plugin.json", "SKILL.md", "hooks.json"}, path)
                self.assertFalse(path.name.endswith((".test.ts", ".test.tsx")), path)
        self.assertTrue((ROOT / "services" / "push" / "src" / "lib.ts").is_file())

    def test_the_installed_command_is_project(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

        self.assertEqual({"project": "projector.cli:main"}, project["scripts"])
        self.assertEqual("projector-cli", project["name"])

    def test_every_citation_of_the_stack_section_names_a_heading_implement_has(self) -> None:
        implement = (ROOT / "skills" / "implement" / "SKILL.md").read_text()
        self.assertIn("\n## Stack dependent work\n", implement)
        for citing in (
            ROOT / "src" / "projector" / "templates" / "agents-block.md",
            ROOT / "skills" / "fix-pr" / "SKILL.md",
        ):
            prose = " ".join(citing.read_text().split())
            self.assertIn('under "Stack dependent work"', prose, str(citing.relative_to(ROOT)))

    def test_every_citation_of_another_skills_section_names_a_heading_it_has(self) -> None:
        cited = set()
        for citing in sorted((ROOT / "skills").rglob("*.md")):
            prose = " ".join(citing.read_text().split())
            for skill, heading in SECTION_CITATION.findall(prose):
                cited.add((skill, heading))
                target = (ROOT / "skills" / skill / "SKILL.md").read_text()
                with self.subTest(citing=str(citing.relative_to(ROOT)), skill=skill):
                    self.assertIn(heading, HEADING.findall(target))
        self.assertIn(("fix-pr", "Hold the pull request while changes are built"), cited)
        self.assertIn(("fix-pr", "Subscribe the session to the pull request"), cited)
        self.assertIn(("review-pr", "Subscribe the session to the pull request"), cited)
        self.assertIn(("review-pr", "Act on the session's review events"), cited)

    def test_no_skill_starts_a_watcher_or_names_a_removed_loop_skill(self) -> None:
        # The watch daemon replaced both loop skills and their watcher scripts, so a skill that still
        # named one would send an agent to a file that no longer ships.
        removed = ("start-watch-loop", "start-review-loop", "watch-threads", "watch-prs", "project loop",
                   "--tracked")
        for path in sorted((ROOT / "skills").rglob("*")):
            if path.is_file():
                text = path.read_text()
                for name in removed:
                    with self.subTest(path=str(path.relative_to(ROOT)), name=name):
                        self.assertNotIn(name, text)

    def test_a_citation_resolves_to_a_heading_at_any_level_but_not_to_prose(self) -> None:
        skill = "# Skill\n\n## Section\n\n### Subsection\n\nProse that mentions ## Elsewhere.\n"
        headings = HEADING.findall(skill)
        for heading, resolves in (
            ("Section", True),
            ("Subsection", True),
            ("Elsewhere", False),
            ("Missing", False),
        ):
            citing = f'as `../review-pr/SKILL.md` describes under\n"{heading}".'
            [(skill_name, cited)] = SECTION_CITATION.findall(" ".join(citing.split()))
            with self.subTest(heading=heading):
                self.assertEqual(("review-pr", heading), (skill_name, cited))
                self.assertEqual(resolves, cited in headings)

    def test_every_required_skill_has_matching_frontmatter_name(self) -> None:
        for name in PUBLISHED_SKILLS:
            lines = (ROOT / "skills" / name / "SKILL.md").read_text().splitlines()
            closing = lines[1:].index("---") + 1
            self.assertIn(f"name: {name}", lines[:closing])


if __name__ == "__main__":
    unittest.main()
