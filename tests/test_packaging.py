from __future__ import annotations

import configparser
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
PUBLISHED_SKILLS = {
    "plan",
    "implement",
    "finish",
    "review-changes",
    "start-review-loop",
    "start-fix-loop",
    "gh-stack",
    "summarize-changes",
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
        configuration = configparser.ConfigParser()
        configuration.read(ROOT / "setup.cfg")

        # A host caches an installed plugin under a directory named by this
        # string, so a one-sided bump updates one host and leaves the other on
        # a stale copy with nothing reporting it. One release tag also names
        # the CLI and the plugin together, so all three must agree.
        self.assertEqual(claude["version"], codex["version"])
        self.assertEqual(claude["version"], configuration["metadata"]["version"])
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

    def test_the_installed_command_is_project(self) -> None:
        configuration = configparser.ConfigParser()
        configuration.read(ROOT / "setup.cfg")

        scripts = configuration["options.entry_points"]["console_scripts"].strip()

        self.assertEqual("project = projector.cli:main", scripts)
        self.assertEqual("projector-cli", configuration["metadata"]["name"])

    def test_every_required_skill_has_matching_frontmatter_name(self) -> None:
        for name in PUBLISHED_SKILLS:
            lines = (ROOT / "skills" / name / "SKILL.md").read_text().splitlines()
            closing = lines[1:].index("---") + 1
            self.assertIn(f"name: {name}", lines[:closing])


if __name__ == "__main__":
    unittest.main()
