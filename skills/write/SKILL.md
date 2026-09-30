---
name: write
description: Write or revise prose for people to read, to Projector's writing guide. That includes documentation, a README section, a pull request body, a commit message, an issue, and a reply. Use when the user asks to write, rewrite, edit, tighten, or proofread prose, or to check text against the writing guide.
---

# Write

Write prose to `../writing.md`, the writing guide that every Projector skill
writes to. Read the whole guide before you start. This skill says only how to
start and how to finish. The guide holds the rules.

## Start from the reader

1. Read the repository's own documentation rules, such as its `CLAUDE.md`,
   `AGENTS.md`, or style guide. They outrank the guide where the two
   disagree.
2. Decide who reads the text and what they already know (§ 2). Decide which
   kind of document it is (§ 3).
3. For a revision, read the text and the code or facts that it describes.
   Keep every fact. Add no claim that the source does not support, and mark
   an inference as one (§ 9).

## Write, then reread

Write the draft. Then reread it as the reader you chose, and check it:

- **Structure.** It leads with what matters, and each part has the form its
  content calls for (§ 4).
- **Sentences.** Each one reads cleanly the first time and has one meaning
  (§ 6 and § 7).
- **Filler.** Nothing is left that reads as machine-written (§ 8).
- **History.** It says what is true now, and leaves how it came to be to
  the commit log (§ 11).

Rewrite each sentence that makes sense only to someone who already knows the
subject.

## Hand it over

Edit a file in place, and match the line wrapping around the edit. Do not
hard-wrap text bound for GitHub, such as a pull request body or a comment,
because GitHub renders line breaks. For text the user pasted, give the
revised text. Then say in a few lines what changed and why, and cite the
guide's sections. Do not commit, push, or post the text unless the user asks.
