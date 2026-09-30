---
name: write
description: Write or revise prose for people to read so that it follows Projector's writing guide. That includes documentation, a README section, a pull request body, a commit message, an issue, and a reply. Use when the user asks to write, rewrite, edit, tighten, or proofread prose, or to check text against the writing guide.
---

# Write

Every Projector skill that writes prose follows the writing guide in
`../writing.md`, and so does this one. Read the whole guide before you
start. This skill says only how to start and how to finish. The guide holds
the rules.

## Start from the reader

1. Read the repository's own documentation rules, such as its `CLAUDE.md`,
   `AGENTS.md`, or style guide. They outrank the guide where the two
   disagree.
2. Decide who reads the text and what they already know (§ 2). Decide which
   kind of document it is (§ 3). A pull request body or a commit message
   follows § 10 instead.
3. For a revision, read the text and the code or facts that it describes.
   Keep every fact that the reader needs. Cut only what the guide tells you
   to cut: history (§ 11) and detail that nobody would act on (§ 2). Add no
   claim that the source does not support, and mark an inference as one
   (§ 9).

## Write, then reread

Write the draft. Then reread it as the reader you chose, and check it:

- **The three rules.** Every word does work, each word is the everyday one,
  and the reader comes first (§ 1).
- **Structure.** It leads with what matters, and each part has the form its
  content calls for (§ 4).
- **Voice.** It speaks to the reader, names the actor, and uses the present
  tense (§ 5).
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
guide's sections. Name each fact that you cut, so that the user can put back
one they need. Do not commit, push, or post the text unless the user asks.
