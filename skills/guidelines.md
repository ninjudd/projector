# Code guidelines

These rules apply to code any Projector skill writes or judges. `implement`
follows them, the review method's written-rules pass reads a head against
them, and `fix-pr` follows them in each fix and verifies findings that cite
them. One file carries them so the three skills cannot drift apart: a rule the
implementer skipped is the rule the reviewer flags, and the fix answers the
same text the finding cited.

A repository's own rule documents outrank these where the two disagree,
because a loop takes its policy from the repository it works in. Cite a rule
by this file and its section number, as `guidelines.md § 1`, so a finding and
its fix name the same rule.

## 1. Comments

Write code that needs no comment. Choose names that say what a value holds and
what a function does, and structure the code so the order of operations is
the explanation. When a stretch of code seems to need explaining, simplify it
before you explain it; the comment a complicated block seems to need is
usually a sign the block should be two.

Add a comment only to preserve a constraint, requirement, or reason a future
maintainer could not recover from the code and its history: a limit an
external system imposes, an order two operations must keep, a case that looks
redundant and is not. Keep it short and in plain language, and put it beside
the code it protects.

Never use a comment to narrate what the code beneath it plainly does, to
excuse complexity the change could have removed, or to assert that a decision
is correct rather than say why it was made. A review flags each of those as a
violation of a written rule, a P2 thread under the written-rules pass citing
this section; the fix is to delete the comment, or to simplify the code until
nothing is left to explain. A comment that misstates what the code does is a
correctness finding, because a maintainer who trusts it changes the wrong
thing.

## 2. Tests

Write and judge tests by the testing guide in `testing.md`. Its first rule
is that a test must be able to catch a bug. Before you write a test, name a
plausible change that leaves the code wrong and turns the test red. Write
no test for which you cannot. A test that restates a value the code holds,
tests a mock or a library, or cannot fail catches nothing, and it costs time
on every run (`testing.md` § 2 and § 4).

Every section of `testing.md` is a written rule. A review flags a test that
breaks any of them as a P2 thread under the written-rules pass that cites
the section the test breaks: a test that catches nothing, and also a test
named `test_import_2` rather than after the rule it checks (`testing.md`
§ 8). The fix for a test that catches nothing is to delete it, or to
rewrite it to check behavior that the code computes; the fix for any other
break is the one its section describes.
