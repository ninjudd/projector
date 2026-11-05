# Testing guide

Projector's skills write tests and judge them. `implement` follows this
guide when it adds tests to a change, and `fix-pr` follows it when it writes
a regression test. The review method's written-rules pass reads changed
tests against it, and `write-tests` applies it to any tests you ask for. A
repository's own testing rules outrank this guide where the two disagree.
Cite a section by this file and its section number, as `testing.md § 2`.

## 1. Write a test only when it can catch a bug

A test exists to fail when the code is wrong. Coverage, speed, and tidy
setup all matter, but only in a test that does that first.

Before you write a test, name the bug it catches. A bug here is a plausible
change that someone could make, that leaves the code wrong, and that turns
this test red. "Someone drops the empty-input check, and the parser crashes
on a blank file" names a bug. "Someone changes the retry limit" names a
decision, not a bug. When you cannot name a bug, do not write the test.

Every test has a cost, and the cost never stops:

- **Time.** The test runs on every contributor's machine and in every CI
  job, on every change, for as long as it exists.
- **Edits.** Each change to the code it covers can need a matching change
  to the test.
- **Trust.** A test that fails when nothing is wrong teaches people to
  rerun the suite, or to ignore red, and then they miss the failure that
  matters.

Write a test when the bug it catches costs more than that.

## 2. Do not restate the code

The plainest test that catches nothing asserts that a value is what the
code sets it to:

```python
def test_default_retry_limit(self):
    self.assertEqual(3, client.DEFAULT_RETRY_LIMIT)

def test_merged_label(self):
    self.assertEqual("Merged", STATUS_LABELS["merged"])
```

Each of these tests is a second copy of a line of source. It catches
nothing, for three reasons:

- **It shares the code's mistake.** One belief produced both the value and
  the assertion. When 3 is the wrong limit, the test says 3 too.
- **It fails only on purpose.** A refactor does not move a literal, and
  neither does a bug somewhere else in the code. The only change that turns
  the test red is a deliberate change to the value. So every failure is a
  false alarm, and the fix is always to edit the test to match.
- **It doubles the cost of the change it guards.** A new value now needs two
  edits. The second edit tells a reviewer nothing that the first did not.

The same goes for a test that checks that a configuration has the keys
written in it, that a component carries the class name in its template,
that a route table lists a route, or that an enumeration has its members.
When you could write the assertion by copying the source, the assertion is
the source.

A literal in a test is not the problem. `assertEqual("3 days ago",
humanize(now - 3 * DAY))` compares a literal with a value that the code
computes, so a bug in `humanize` turns it red. The problem is a literal
compared with a literal that the code holds and does not compute.

## 3. Test what a value must agree with

A value matters when something else depends on it. Test that dependency
instead of the literal. The test then fails when the dependency breaks, and
keeps passing when someone changes the value correctly.

| Instead of asserting | Assert |
| --- | --- |
| The logo path is `img/logo.svg` | Every asset path that a page references exists in the build output |
| The version is `1.4.2` | The package, the plugin manifest, and the lock file carry the same version |
| The default timeout is 30 seconds | A request that outlasts the timeout is cancelled, and its caller receives the timeout error |
| The serializer writes the field `created_at` | A record survives a round trip through the serializer and the parser, and the parser reads a file that an older release wrote |
| The label for `merged` is "Merged" | Every status that the server can send has a label, so no status renders blank |

Each test in the right column fails on a change that breaks something, such
as an asset deleted while a page still names it. Each one passes when
someone chooses a new value on purpose.

Some values depend on nothing. A color, a line of copy, or a default that
someone picks is a judgment, and no test can say whether the judgment is
right. A reviewer sees the new value in the diff. Leave it to the review.

Pin an exact value only when code outside your control depends on it, such
as an exit code that scripts check, a constant in a wire protocol, or a
file format that other programs read. There, an accidental change is a real
bug, because the person who makes it may not know that anything depends on
the value. Pin the value where the outside code reads it, such as the exit
code of the command, and not the constant behind it. Name the test after
the dependency, so that whoever breaks it learns why the value cannot move.

## 4. Recognize other tests that catch nothing

Restated code is the plainest case. These tests catch nothing for the same
reason: no plausible bug turns them red.

- **A test of the mock.** It replaces the code under test, or everything
  the code calls, with stubs. Then it asserts that the stubs returned what
  the test told them to return. Fake only a boundary that is slow,
  nondeterministic, or outside your control, and assert on what your code
  does with the result.
- **A test of the language or a library.** It checks that a data class
  keeps a field, that a dictionary returns what you stored, or that the
  database library saves a row. The library's own tests cover that. Test
  your code's use of the library instead.
- **A test that cannot fail.** Its assertions sit in a loop over an empty
  collection. Or it catches the exception it means to check, never awaits
  the coroutine it calls, or asserts that an object that always exists is
  truthy. § 7 shows how to find these.
- **A test for coverage.** It runs a line and asserts nothing about the
  result, or asserts only that no exception escaped. A component test that
  renders and checks nothing is one of these. Coverage measures what ran,
  not what a test checked, so a line run by a test with no assertion is
  still untested.
- **A change detector.** It pins how the code works instead of what it
  does: which private helper runs, the order of internal calls, or how many
  times a method is called. It fails on every refactor and passes when the
  output is wrong. Assert on the outcome that a caller sees.
- **An unread snapshot.** People regenerate a large snapshot whenever it
  fails, so it checks only that the output did not change. Keep a snapshot
  when the output is the product itself, such as a generated file. Keep it
  small enough to read in a diff, and read the diff when it changes.
- **A duplicate.** It checks a behavior that another test already checks,
  through the same path. Keep the test whose failure says the most, and
  delete the other.

## 5. Aim at the places bugs live

Bugs gather where code makes a decision, and where it meets something that
it does not control. Put tests there:

- **Boundaries.** Zero, one, empty, missing, the maximum, and one past the
  maximum.
- **Branches.** Each side of a condition that changes the result,
  especially the side that the happy path never takes.
- **Failures.** What the caller sees when a file is missing, a command
  exits with an error, a request times out, or the input is malformed.
  Check that the error is the right one, and that the failure leaves
  nothing half-written.
- **Repetition and order.** The operation run twice, run after a partial
  failure, and run twice at once when the code allows that.
- **Round trips.** Data written and read back, parsed and printed, or
  encoded and decoded.
- **State changes.** Each allowed move from one state to another, and the
  refusal of each move that is not allowed.
- **Seams.** The place where two components meet, such as a caller and the
  format that it expects from a callee. Each side's own tests can pass while
  the two sides disagree.
- **Regressions.** The exact input that broke the code once. Someone can
  bring back a bug that happened once.

When the input space is large, write one table-driven test instead of many
copies of one test. Consider a property test that checks an invariant
across generated inputs, such as "parsing the printed form returns the
original".

## 6. Test through the interface that callers use

Test a behavior at the boundary where something depends on it: a public
function, the output and exit code of a command, a file that the code
writes, or the response of an endpoint. A test there passes after a
refactor that keeps the behavior, and fails when the behavior breaks. A
test of a private helper does the opposite.

Use real collaborators when they are fast and deterministic. A temporary
directory is a better file system than a mock of one, and a temporary Git
repository is a better Git than a stub of its output. Replace a
collaborator only when the real one is slow, nondeterministic, costs money,
or reaches outside the machine, such as a network service or the system
clock. Then replace it at the narrowest point you can.

Test each behavior once, at the level where its failure is clearest. An
end-to-end test proves that the parts connect. A unit test covers the
combinations that would be slow to reach from outside. One behavior checked
at every level costs several times as much, and catches nothing more.

## 7. Watch each test fail

A test that you have never seen fail has not shown that it can. After you
write a test, break the code it covers in the way that the test claims to
catch: invert the condition, drop the check, or return early. Run the test.
See it fail with a message that names the problem, and then restore the
code. For a regression test, run it against the code from before the fix.

When the test still passes with the code broken, it is one of the tests in
§ 4. Fix the test, or delete it.

Do this by hand for each new test. Where a repository has a mutation
testing tool, the tool does the same across a whole suite.

## 8. Make each failure explain itself

The person who reads a failed test is usually the person whose change made
it fail. Write the test for them.

- **Name the test after the rule it checks**, as a sentence:
  `test_a_second_import_of_the_same_file_adds_no_rows`, not
  `test_import_2`. When the test fails, its name says what stopped being
  true.
- **Check one behavior in each test.** A test can make several assertions
  about one outcome. Split a test that checks two unrelated behaviors, so
  that each failure names one.
- **Keep the input in view.** Put the input data in the test, small and
  literal, so that the reader sees what goes in beside what comes out. Move
  setup into a helper only when the helper hides detail that the reader
  does not need.
- **Compare whole values.** `assertEqual(("merged", 2, 0), (state, added,
  failed))` shows every field that differs in one failure. Three separate
  assertions stop at the first difference.
- **Write the expected value as a literal.** A test that computes its
  expected value the way the code does repeats the code's bugs.
- **Keep logic out of the test.** A loop that builds the expected values,
  or a condition that picks which assertion runs, needs tests of its own.
  Give many cases to one test as a table of subtests instead.

## 9. Keep the suite fast and deterministic

People run a slow suite less often, and they stop trusting a suite that
fails at random. Either way, real failures get through.

- **Control time and randomness.** Pass the clock and the random seed in
  as inputs. To wait for an event, wait on the event itself with a timeout.
  Never sleep for a fixed time.
- **Isolate each test.** Give each test its own temporary directory,
  repository, and environment, and leave nothing behind. A test that passes
  only after another test, or only when it runs alone, is broken.
- **Stay off the network.** Replace the service at its boundary (§ 6).
- **Fix or delete a flaky test as soon as you see it.** A retry until the
  test passes hides the failure, and the failure is often a real race in
  the code.

## 10. Delete tests that catch nothing

A deleted test that caught nothing is a gain. The suite runs faster, each
later change costs less, and nothing is lost, because the test never
caught anything.

When a change touches code that has such a test, delete the test in the
same change. When you find one elsewhere, name it when you hand the work
over, instead of widening the change. Before you delete a test, check
whether it is the only test that runs some behavior. When it is, rewrite it
to check that behavior instead.

A review flags a new test that catches nothing as a violation of a written
rule, and cites the section of this guide that the test breaks.
