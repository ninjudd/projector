from __future__ import annotations

import hashlib
import unittest

from projector import instructions


# One hash per template version. `check` compares a repository's block with the
# shipped text by the version in its begin marker, so a text change under the
# same version reads as a hand edit in every adopted repository. Changing the
# template therefore means bumping the version in its first line and pinning
# the new hash here; this test is what says so.
PINNED = {
    1: "af0cc51a4646f05d1570453cfb941d9e4fcecb272349387e784d87d5567598c4",
}


class TemplateTests(unittest.TestCase):
    def test_the_template_text_is_pinned_to_its_version(self) -> None:
        version = instructions.template_version()
        digest = hashlib.sha256(instructions.template().encode("utf-8")).hexdigest()
        self.assertIn(version, PINNED, "pin the new template version in PINNED")
        self.assertEqual(
            PINNED[version],
            digest,
            f"agents-block.md changed under version {version}: bump the version in its"
            " begin marker and pin the new hash",
        )

    def test_the_template_begins_and_ends_with_its_markers(self) -> None:
        text = instructions.template()
        self.assertRegex(text.splitlines()[0], r"^<!-- projector:begin \d+ -->$")
        self.assertEqual("<!-- projector:end -->", text.rstrip("\n").splitlines()[-1])
        self.assertIn(instructions.PLACEHOLDER, text)

    def test_render_fills_in_the_projects_dir_and_is_what_check_compares(self) -> None:
        rendered = instructions.render("plans")
        self.assertIn("`plans/README.md`", rendered)
        self.assertNotIn(instructions.PLACEHOLDER, rendered)
        self.assertFalse(rendered.endswith("\n"))
        block = instructions.find_block(rendered + "\n")
        self.assertIsNotNone(block)
        self.assertTrue(instructions.block_matches(block, rendered))


class BlockTests(unittest.TestCase):
    def test_find_block_returns_none_for_a_file_without_markers(self) -> None:
        self.assertIsNone(instructions.find_block("# Rules\n\nBe kind.\n"))
        self.assertIsNone(instructions.find_block(""))

    def test_find_block_locates_one_block_and_reads_its_version(self) -> None:
        text = "# Rules\n\n<!-- projector:begin 7 -->\nbody\n<!-- projector:end -->\n\ntail\n"
        block = instructions.find_block(text)
        self.assertEqual(7, block.version)
        self.assertEqual("<!-- projector:begin 7 -->\nbody\n<!-- projector:end -->", block.text)
        self.assertEqual(text[block.start : block.end], block.text)

    def test_find_block_reads_crlf_files(self) -> None:
        text = "# Rules\r\n<!-- projector:begin 1 -->\r\nbody\r\n<!-- projector:end -->\r\n"
        block = instructions.find_block(text)
        self.assertEqual(1, block.version)
        self.assertTrue(instructions.block_matches(block, "<!-- projector:begin 1 -->\nbody\n<!-- projector:end -->"))

    def test_find_block_rejects_markers_that_are_not_one_pair(self) -> None:
        begin = "<!-- projector:begin 1 -->\n"
        end = "<!-- projector:end -->\n"
        for text, detail in (
            (begin + "body\n", "no end marker"),
            ("body\n" + end, "no begin marker"),
            (begin + end + begin + end, "more than one begin marker"),
            (begin + "body\n" + end + end, "more than one end marker"),
        ):
            with self.assertRaises(instructions.MalformedBlock, msg=text) as raised:
                instructions.find_block(text)
            self.assertIn(detail, str(raised.exception))

    def test_with_block_appends_after_one_blank_line_or_replaces_in_place(self) -> None:
        rendered = "<!-- projector:begin 2 -->\nnew\n<!-- projector:end -->"
        self.assertEqual(rendered + "\n", instructions.with_block("", rendered))
        self.assertEqual("# Rules\n\n" + rendered + "\n", instructions.with_block("# Rules", rendered))
        self.assertEqual("# Rules\n\n" + rendered + "\n", instructions.with_block("# Rules\n\n", rendered))
        old = "# Rules\n\n<!-- projector:begin 1 -->\nold\n<!-- projector:end -->\n\ntail\n"
        self.assertEqual("# Rules\n\n" + rendered + "\n\ntail\n", instructions.with_block(old, rendered))
        crlf = "# Rules\r\n\r\n<!-- projector:begin 1 -->\r\nold\r\n<!-- projector:end -->\r\n"
        self.assertEqual(
            "# Rules\r\n\r\n<!-- projector:begin 2 -->\r\nnew\r\n<!-- projector:end -->\r\n",
            instructions.with_block(crlf, rendered),
        )


class ImportTests(unittest.TestCase):
    def test_imports_are_recognized_where_claude_code_recognizes_them(self) -> None:
        for text in (
            "@AGENTS.md\n",
            "@AGENTS.md",
            "Read @AGENTS.md before making changes.\n",
            "See @./AGENTS.md for the rules\n",
            "- git workflow @AGENTS.md\n",
            "(@AGENTS.md)\n",
        ):
            self.assertTrue(instructions.imports_agents(text), text)

    def test_mentions_that_are_not_imports(self) -> None:
        for text in (
            "",
            "Read `@AGENTS.md` before making changes.\n",
            "```\n@AGENTS.md\n```\n",
            "~~~md\n@AGENTS.md\n~~~\n",
            "Mail someone@AGENTS.md about it.\n",
            "@AGENTS.md.bak\n",
            "@docs/AGENTS.md\n",
            "@OTHER.md\n",
        ):
            self.assertFalse(instructions.imports_agents(text), text)

    def test_an_unterminated_fence_hides_everything_after_it(self) -> None:
        self.assertFalse(instructions.imports_agents("```\n@AGENTS.md\n"))
        self.assertTrue(instructions.imports_agents("@AGENTS.md\n```\nignored\n"))

    def test_with_import_appends_after_one_blank_line(self) -> None:
        self.assertEqual("@AGENTS.md\n", instructions.with_import(""))
        self.assertEqual("# Claude\n\n@AGENTS.md\n", instructions.with_import("# Claude"))
        self.assertEqual("# Claude\n\n@AGENTS.md\n", instructions.with_import("# Claude\n\n"))
        self.assertEqual("# Claude\r\n\r\n@AGENTS.md\r\n", instructions.with_import("# Claude\r\n"))


if __name__ == "__main__":
    unittest.main()
