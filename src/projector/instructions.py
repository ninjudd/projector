"""The Projector section of a repository's agent instructions.

A repository that adopts Projector carries one block Projector owns in its root
`AGENTS.md`, between two HTML-comment markers, and reaches the same block from
`CLAUDE.md`, because Claude Code reads `CLAUDE.md` and Codex reads `AGENTS.md`:
`CLAUDE.md` is a link to or an import of `AGENTS.md`, or, when the two are
distinct files, carries its own copy of the block. Everything outside the
markers belongs to the repository. `init` writes and refreshes the block;
`check` reports when it has drifted from the template this package ships. Both render the template through `render`, so they compare
the same string.

The template's first line is its own begin marker, and the integer in that
marker is the template version. It changes only when the text changes, so a
repository's block can be compared with the installed template without a
content hash: an older version is refreshed, a newer one is left alone, and an
equal version with different text was edited by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from typing import Optional


PLACEHOLDER = "{projects_dir}"
IMPORT_LINE = "@AGENTS.md"

# A marker owns its line. The lookahead leaves a CRLF file's `\r` outside the
# block, so the block text ends at the marker whatever the line endings are.
BEGIN = re.compile(r"^<!-- projector:begin (\d+) -->(?=\r?$)", re.MULTILINE)
END = re.compile(r"^<!-- projector:end -->(?=\r?$)", re.MULTILINE)

# Claude Code recognizes `@path` anywhere outside a code span or fenced block,
# inline in a sentence included, and resolves a relative path against the
# importing file, so `@./AGENTS.md` is the same import as `@AGENTS.md`. The
# token starts at a line start, after whitespace, or after an opening bracket,
# so an address like `someone@AGENTS.md` is not one, and it ends before
# whitespace or sentence punctuation, so `Read @AGENTS.md.` still imports while
# `@AGENTS.md.bak` names a different file.
IMPORT = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[]))@(?:\./)?AGENTS\.md(?=$|\s|[),;:!?\]]|\.(?:\s|$))",
    re.MULTILINE,
)
FENCE = re.compile(r"^(`{3,}|~{3,})")
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)


class MalformedBlock(Exception):
    """The markers in a file do not delimit exactly one block."""


@dataclass(frozen=True)
class Block:
    version: int
    start: int
    end: int
    text: str


def template() -> str:
    return (
        resources.files("projector")
        .joinpath("templates/agents-block.md")
        .read_text(encoding="utf-8")
    )


def template_version() -> int:
    match = BEGIN.match(template())
    if match is None:
        raise RuntimeError("agents-block.md must begin with its projector:begin marker")
    return int(match.group(1))


def render(projects_dir: str) -> str:
    """The block for a repository, without a trailing newline.

    `projects_dir` is the projects directory as written in the block, relative
    to the repository root when it lies inside it.
    """

    return template().replace(PLACEHOLDER, projects_dir).rstrip("\n")


def find_block(text: str) -> Optional[Block]:
    """Locate the Projector block in `text`, or return None when there is none.

    Raises `MalformedBlock` when the markers cannot be read as exactly one
    block: a begin without an end, an end without a begin, or a second pair.
    """

    begins = list(BEGIN.finditer(text))
    ends = list(END.finditer(text))
    if not begins:
        if ends:
            raise MalformedBlock("an end marker with no begin marker")
        return None
    if len(begins) > 1:
        raise MalformedBlock("more than one begin marker")
    begin = begins[0]
    ends = [end for end in ends if end.start() > begin.end()]
    if not ends:
        raise MalformedBlock("a begin marker with no end marker")
    if len(ends) > 1:
        raise MalformedBlock("more than one end marker")
    end = ends[0]
    return Block(
        version=int(begin.group(1)),
        start=begin.start(),
        end=end.end(),
        text=text[begin.start() : end.end()],
    )


def block_matches(block: Block, rendered: str) -> bool:
    """Whether a block's text is the rendered template, line endings aside."""

    return block.text.replace("\r\n", "\n") == rendered


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _appended(text: str, addition: str) -> str:
    """`text` followed by one blank line and `addition`, ending in a newline."""

    newline = _newline(text)
    if not text:
        return addition.replace("\n", newline) + newline
    prefix = text if text.endswith("\n") else text + newline
    if not prefix.endswith(newline * 2):
        prefix += newline
    return prefix + addition.replace("\n", newline) + newline


def with_block(text: str, rendered: str) -> str:
    """`text` with the rendered block inserted or refreshed.

    A file without a block gains one after a blank line at its end. A file with
    a block keeps every byte outside the markers, including its line endings.
    """

    block = find_block(text)
    if block is None:
        return _appended(text, rendered)
    body = rendered.replace("\n", _newline(text))
    return text[: block.start] + body + text[block.end :]


def imports_agents(text: str) -> bool:
    """Whether `text` imports `AGENTS.md` the way Claude Code would read it."""

    kept: list[str] = []
    fence: Optional[str] = None
    for line in text.splitlines():
        match = FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)[0]
                continue
            kept.append(line)
        elif match and match.group(1)[0] == fence:
            fence = None
    prose = CODE_SPAN.sub(" ", "\n".join(kept))
    return IMPORT.search(prose) is not None


def with_import(text: str) -> str:
    """`text` with the `@AGENTS.md` import appended after a blank line."""

    return _appended(text, IMPORT_LINE)
