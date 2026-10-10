from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
PUBLISHED_SKILLS = {
    "spec",
    "implement",
    "finish",
    "review-pr",
    "fix-pr",
    "start-review-loop",
    "start-fix-loop",
    "summarize-pr",
    "write",
}


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
            ROOT / "skills" / "start-fix-loop" / "SKILL.md",
        ):
            prose = " ".join(citing.read_text().split())
            self.assertIn('under "Stack dependent work"', prose, str(citing.relative_to(ROOT)))

    def test_every_required_skill_has_matching_frontmatter_name(self) -> None:
        for name in PUBLISHED_SKILLS:
            lines = (ROOT / "skills" / name / "SKILL.md").read_text().splitlines()
            closing = lines[1:].index("---") + 1
            self.assertIn(f"name: {name}", lines[:closing])


if __name__ == "__main__":
    unittest.main()
