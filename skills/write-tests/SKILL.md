---
name: write-tests
description: Write tests that catch real bugs by Projector's testing guide, or audit existing tests and delete or rewrite the ones that catch nothing. Use when the user asks to write, add, improve, review, audit, or prune tests, or asks what a change needs tested.
---

# Write tests

Every Projector skill that writes or judges tests follows the testing guide
in `../testing.md`, and so does this one. Read the whole guide before you
start. This skill says how to start and how to finish. The guide holds the
rules.

## Learn how the repository tests

1. Read the repository's own testing rules, such as its `CLAUDE.md`,
   `AGENTS.md`, or contributing guide. They outrank the guide where the two
   disagree.
2. Find the command that runs one test and the command that runs the whole
   suite. Run the suite once before you change anything, so that you know
   it passes and how long it takes.
3. Read the tests beside the code you work on. Use their helpers, fixtures,
   and naming instead of adding your own.

## Write tests for a change

1. **List the bugs first.** Read the code under test, or the diff, and list
   what could go wrong in it: each boundary, branch, failure, repetition,
   and seam (§ 5). Write each item as the bug that a test would catch.
2. **Cross off each item that no plausible bug turns red** (§ 1 and § 2).
   For a value, find what the value must agree with, and test that instead
   (§ 3).
3. **Write each test** through the interface that callers use (§ 6), and so
   that its failure explains itself (§ 8).
4. **Watch each test fail.** Break the code it covers, run the test, and
   restore the code (§ 7). Keep no test that you have not seen fail.
5. **Run the suite.** Check that it passes, and that it is not much slower
   than your first run (§ 9).

## Audit existing tests

The user names the tests to audit, such as a file, a directory, or a pull
request.

1. **Read each test and the code it covers.** For each test, name the bug it
   catches, or note that you found none.
2. **Sort the tests.** Keep a test that catches a plausible bug. Rewrite a
   test that runs real behavior but checks it badly, such as one that
   asserts on a mock instead of the outcome. Delete a test that catches
   nothing (§ 2 and § 4), after you check that no behavior is left untested
   (§ 10).
3. **List the gaps.** Name each place where bugs live (§ 5) that no test
   reaches, with the bug a test there would catch. Write those tests only
   when the user asks.
4. **Make the changes and run the suite**, unless the user asked only for a
   report. Compare the suite's time with your first run.

## Hand it over

Do not commit or push unless the user asks. Report, as a list:

- **Each test you added**, with the bug it catches.
- **Each test you deleted or rewrote**, with the section of the guide that
  it broke.
- **The suite's result and time**, from before and after your change.
- **Each gap you left**, with the bug a test there would catch.
