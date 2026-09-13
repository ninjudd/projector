---
status: completed
priority: now
---

# Give the review loop a review method

## 1. Outcome

A review the loop publishes rests on a written method rather than on whatever
the reviewing session thinks to try. Before the first finding, the reviewer
states what the change is meant to accomplish. It sorts the changed files,
reads each one through a fixed set of passes that each ask one question of
the code, follows every changed definition to the code that depends on it,
and verifies each candidate finding by a four-step protocol before that
finding becomes a thread. Rules decide which verified findings ship and at
what priority, and only findings that block go out as threads. The review
body says how much of the change was read, beside the thread census it
already prints. The fix loop verifies and declines findings by the same
protocol.

The method runs under both hosts as one agent working in sequence. Parallel
subagents are an optimization a host may offer, never something the method
depends on.

## 2. Constraints

- Write every sentence of the method for Projector, in the register the
  repository documents. Do not paste text from another review skill or tool,
  whatever its license or provenance.
- Describe how code fails, not which library or framework call is involved,
  so one method serves every language a repository contains.
- In `method.md` and in the edits section 6 introduces, name no
  host-specific tool and no path on the reviewer's machine, such as a home
  directory or a scratch directory. Names relative to the repository under
  review, such as `AGENTS.md` and `docs/`, are fine, and so are Projector's
  own configuration files, `.projector.toml` and `~/.projector.toml`, which
  are Projector's contract rather than a host's layout. The text says what to
  read and check; the host decides how.
- Keep every rule in `skills/start-review-loop/SKILL.md` about identity,
  exact heads, start comments, markers, verdicts, and draft state unchanged
  except where section 6 names an edit. This project changes how a head is
  inspected and how a finding is written, not when or as whom a review posts.
- Review every head with the whole method. A fix-only head gets every pass,
  because the loop already treats every pushed SHA as a complete cycle.

## 3. The method

The method lives in `skills/start-review-loop/method.md` and has the parts
below, in the order the reviewer performs them.

### 3.1 State the intent

Write two to four sentences on what the change is meant to accomplish and
what it knowingly leaves temporary, from the pull request body, the commit
subjects, `git diff --stat`, and the hunk headers. Read individual hunks only
when those do not settle it. The paragraph opens the review body, and every
later judgment is made against it: a finding is about whether the change
does what it set out to do.

### 3.2 Sort the changed files

Give each changed file the first kind in this table that fits it, adapting
the patterns to the conventions the repository documents:

| Kind | Passes |
|---|---|
| Tests | Simplicity, written rules |
| Generated or vendored code, lockfiles, snapshots | None. Count them as skipped in the coverage line. |
| Dependency manifests | Exposure |
| CI, build, and configuration files | Exposure, written rules |
| Documentation | Read directly for accuracy against the code it describes, broken references, and contradictions |
| Scripts and tooling | Correctness, simplicity, exposure, written rules |
| Source code | Every pass |

The written-rules pass runs only when the repository has written rules to
cite: `AGENTS.md`, `CLAUDE.md`, a contributing guide, a pull request template,
or style documents under `docs/`. The union of passes across the kinds present
is the set the review runs.

### 3.3 Run the passes

A pass is one way of reading the change. Each pass reads the diff, then reads
every file assigned to it from top to bottom, because a hunk shows an edit and
hides the lifecycle around it. A pass reports only what falls inside its
question and names every assigned file in which it found nothing, so the
coverage line in section 3.7 can account for the whole change.

**Correctness: does the code still hold when time passes, when data is
missing, and when something fails?**

Between a check and the action it guards, ask what can change. An await, a
callback, a dispatch to another queue, or a timer opens a gap, and a
condition true before the gap may be false after it. Ask what a completion
handler does when its operation was cancelled or failed rather than
finishing, and whether a flag that marks work as pending is reset on every
exit path, disconnect included, and only once the work is really done. Ask
whether two threads, tasks, or handlers can touch the same state at once, and
whether a handler written to run once can run twice.

Ask how the code reads state that has not arrived yet. A value still loading
that reads as zero, empty, or false decides wrongly whenever it gates a
permission, a balance, or a feature. Two values that are both missing are not
thereby equal. A teardown that resets most of the state leaves the rest for
the object's next user, and a cache keyed on fewer inputs than the
computation uses returns answers for the wrong inputs. Anything cached or
persisted per account must be dropped wherever the repository drops account
state on sign-out.

Ask what happens at the edges: zero, negative, and maximum values in
arithmetic, indexing, and sizing; an empty collection handled like a missing
one; an enumeration value arriving from an API, a store, or a config file
that the code has never seen.

Ask where failures go. An error caught and discarded, a cancellation the user
sees as a failure, and a parallel step whose failure a sibling's success
hides are all findings. So are a listener, observer, or timer with no
matching removal, a background task with no handle that could cancel it, and
a file, socket, or connection with no close on the error path.

**Placement: is this code where the repository keeps code like it?**

Read the directory layout and the imports around the change before judging.
Ask whether the new code depends on a module the repository's structure says
it should not know about, whether it puts a decision that belongs to the
domain into a view or a piece of rendering into the data layer, and whether
it computes from shared state something other parts of the system will also
need, in a place only this part can reach. Ask whether the change builds a
parallel path for something the codebase already has one path for, or gets
what it needs by going around a boundary rather than through it, for example
by copying a client's internals or by reading storage or configuration
directly where the repository has a single access layer. Ask whether a new
public interface hands out internal types or accepts arguments only its first
caller could produce.

**Simplicity: what does the change leave behind, and what does it reinvent?**

This pass searches the repository more than it reads the diff. When the
change alters a signature or removes a code path, search for every reader of
the old fields, parameters, and functions and flag the ones nothing reads
anymore, including branches keyed on a constant whose last producer is gone.
Flag code left commented out and branches or handlers with nothing in them.
Search for an existing helper before accepting a new one, in the repository
and in the standard library it already uses, and flag two stretches of the
diff that differ by a name or two. Flag an interface that has one
implementation, a wrapper that adds a hop and nothing else, and a parameter
every caller passes the same way. When the change adds a file whose kind the
repository already has many examples of, compare it with the nearest existing
one and flag differences the change does not explain. Leave to the linters
what the linters already report.

**Cost: what does the change make the program do, and how many times?**

Establish the frequency first by finding the callers of the changed code,
because how often code runs is something to check, not guess. Then look for a
loop that searches a list where a lookup table would do, or recomputes inside
itself a value that never changes; blocking work, such as file or network
access or a heavy parse, on the main thread or inside a request handler; a
request or query issued once per item when the API offers a batch, and awaits
chained one after another that share no data; objects, regular expressions,
or formatters constructed inside a loop or a per-request path; work done at
startup or import that nothing needs until later; a collection or cache with
no eviction, and a subscription added for every view or event and never
removed; a new list read with no page size or upper bound; and logging left
in a path that runs often. A cost finding states the operation, its
frequency, and its price, or it does not ship.

**Exposure: where does untrusted data go, and who can reach the new
surface?**

For every value the change routes somewhere new, find where the value comes
from and judge the destination by its most sensitive source. Look for input
reaching a shell, a database query, markup, a URL, a format string, or an
interpreter without the sanitization the repository applies elsewhere. Look
for a new route or handler without the authorization check its neighbors
carry, an authorization decision made from data the client controls, and an
object identifier accepted without checking that the caller owns it. Look for
a secret written into code, configuration, a log line, or a CI file, or moved
from a protected store to a weaker one, and for personal data, tokens, or
internal error text now reaching logs, analytics, an error tracker, a URL,
the clipboard, or the screen. Look for input parsed into code or into a type
the input itself chooses, for connections or storage that skip the encryption
the repository uses elsewhere, for certificate checks turned off, and for
query strings, deep links, webhooks, inter-process messages, and uploads
whose shape is checked but whose origin is trusted. Look for a redirect whose
target comes from input. In CI, look for a workflow that runs code an
outsider can change while secrets are in scope, or that grants a token more
than it needs. When the change adds or bumps a dependency, write down the
package and version. Where the host can reach the network, look up known
advisories for that exact version. Where it cannot, say so in the review body
as section 3.7 describes. Do not guess either way.

**Written rules: does the change follow what the repository wrote down?**

Read the parts of the repository's rule documents that bear on the changed
files, then read the diff against them. A finding here names the document and
the section it enforces, or it is a preference and does not ship. When the
rule is one the repository's own merged commits keep breaking, report the
gap between the document and the practice as a P3 rather than as this
change's fault.

### 3.4 Follow the change outward

The diff shows a definition changing. The code that depends on that
definition is usually outside the diff and did not change, so the change is
correct only if it holds there too. For each function, type, or field the
change touches:

- Find every caller and reader in the repository and check the change at each
  one, not only at the ones the change edited.
- Where the change adds a requirement on inputs, alters what it returns,
  raises something new, or changes a side effect, confirm every existing
  caller can live with it.
- Look at when each caller runs. A guard evaluated on entry protects nothing
  if the effect lands later, from a queue, a batch, or a retry.
- Where a value now flows to a new destination, trace it back to every place
  that produces it and judge the destination by the most sensitive of them.
- Where a stored or transmitted type changes shape, check every writer and
  every reader of that type, including data already saved in the old shape.

### 3.5 Verify before you post

Walk four steps for every candidate finding, against the exact head SHA in
the scratch worktree:

1. Quote the lines as they are at the head SHA. Without specific lines there
   is no finding.
2. Walk the execution with concrete values: which branch is taken, what each
   variable holds, what comes out.
3. Write down the sequence of inputs and events that leads there, one this
   system can actually receive. Without a realistic sequence the finding is
   theory and is dropped.
4. Look for what already stops it: a guard upstream, an assertion, validation
   at the boundary. For code that runs more than once, walk the second
   execution as well as the first, because the first run often leaves behind
   a flag or a cache entry that changes what the second one does.

A candidate that passes all four steps is verified. A candidate that passes
the first three with the fourth unclear gets a second look before it posts.
The second look re-derives the defect from the claim alone: the pass, the
`path:line`, the symbol, and a one-line headline. It never re-reads the first
look's reasoning, because agreeing with an argument is not finding the
defect again. Where the host runs subagents, hand the claim to a
fresh one with a wider brief: the full files, the callers, the related tests.
Otherwise set the first look aside, read those same sources again, and write
the failure sequence from scratch. A second look that confirms produces a
concrete sequence from inputs and state to a wrong result. One that refutes
names the guard that prevents it. A second look that cannot decide is a
dropped candidate, never a question handed to the author.

Run a test or reproduction when the code is runnable and the cost is
proportional to the risk, as the skill already requires. The protocol is what
verifies a finding when running it is not possible.

### 3.6 Decide what ships

Apply these rules to the verified set, in this order:

- **Every finding names its fix** in one sentence. A finding that cannot say
  what to change is an observation and is dropped.
- **The change must introduce or worsen the defect.** Compare against the
  base. A defect already present at the base is at most a P3 and says so.
- **A rule violation cites its rule** by document and section. Without a
  citation it is a style opinion and is dropped.
- **One root cause is one finding.** The same defect at several sites becomes
  one finding that lists every location and anchors at the first. Two
  candidates on the same defect merge, keeping a second framing only when it
  adds information.
- **Impact is concrete.** A cost finding states the operation and its
  frequency; a correctness finding states the wrong result. Theory does not
  ship.

Then assign a priority:

- **P1.** A defect the change introduces or worsens with concrete impact on
  users, data, money, availability, or security. Posts as a thread. Blocks.
- **P2.** A defect the change introduces with bounded impact, or a violation
  of a rule the repository wrote down. Posts as a thread. Blocks.
- **P3.** A change the code is correct without: a simplification, dead code,
  a gap between a written rule and practice, a defect already present at the
  base. Listed in the review body under a `Suggestions` heading. Never a
  thread, never blocks.

There is no cap on the number of findings. The rules above are the filter,
and every thread is one the author must resolve, so post nothing you would not
defend in the thread.

### 3.7 Write the review

The review body, after the signature line and marker the skill already
requires, reads in this order:

1. The intent paragraph from section 3.1.
2. The thread census the skill already prints.
3. A coverage line: how many changed files a pass or the reviewer read, and
   how many were skipped as generated, for example
   `Covered 11 of 13 changed files; 2 generated files skipped.` Every changed
   file that is not generated must be accounted for by a finding, by a pass's
   clean list, or by the reviewer's direct read. A file no pass covered gets a
   direct read before the review posts.
4. Disclosures, when any: an advisory check skipped for a new dependency, a
   file too large for a pass to read in full, a test that could not run.
5. `Suggestions`, the P3 list, when any, each as one line naming the
   `path:line` and the change.
6. On a clean head, what was checked, as the skill already requires.

The review marker gains `covered=<read>/<changed>` after `seconds=`. Its
`findings=` count is the number of P1 and P2 threads this review opens; P3
items in the body are not findings and are not counted. The version stays
`v=1`: no watcher parses the review marker, and the collision check matches
`projector-review .* sha=`, so an added field breaks nothing.

Each finding thread's first comment, after its marker, has three parts:

```
**P1 · Signing out leaves the previous account's balance cached for the next sign-in**

`signOut` clears the session token and the positions map but not `balanceCache`,
so the next account to sign in on the device reads the old balance until its
first refresh completes.

**Fix:** clear `balanceCache` in `signOut` beside the positions map.
```

The first line says what goes wrong and what it costs, in roughly a dozen
words, with no pass name and no path; the path is the thread's anchor. The
middle is one or two sentences of concrete behavior that name the symbol. The
fix is one sentence, with a snippet of a few lines only when the change is
mechanical. Sentences are plain and declarative. No hedging, because the
protocol either produced a sequence or the finding did not ship. No pass
names or protocol vocabulary anywhere the author reads. No restating the
change's context; the intent paragraph carries it.

When a re-review shows an open finding was wrong, reply in its thread, open
the reply with `<!-- projector-reply v=1 -->` so no loop reads it as a
finding, name the guard or behavior that shows the finding was wrong, and say
the finding is withdrawn. The author resolves the thread; the reviewer never
does.

## 4. The fix loop verifies the same way

`skills/start-fix-loop/SKILL.md` step 1 under "Verify and fix findings" cites
section 3.5 of the method by path: verify the claim by the four steps, and
decline a finding by naming the guard with the quoted code that shows it.

The same skill gains one rule about the `Suggestions` list: it is not a
body-only finding, and no item on it is outstanding work. The fix loop weighs
each item itself, taking it when the improvement is worth a push and the
review cycle that follows and leaving it otherwise. A `REVIEW` event whose
body carries a clean verdict and a `Suggestions` list is a clean head either
way, and an item the loop leaves needs no reply. Nothing else in that skill
changes.

## 5. Host neutrality

The method is a sequence one agent performs in one conversation. Where the
host runs subagents, the passes in section 3.3 may run in parallel and the
second look in section 3.5 may go to a fresh agent. The text describes what
to read and check and never names a host's tool, a permission model, or a
path on the reviewer's machine. Names relative to the repository under
review, such as `AGENTS.md` and `docs/`, are what the method reads and are
allowed, as are Projector's own configuration files, `.projector.toml` and
`~/.projector.toml`.

## 6. Files

- `skills/start-review-loop/method.md`: new. Sections 3.1 through 3.7 as
  instructions to the reviewer.
- `skills/start-review-loop/SKILL.md`: steps 5 and 6 under "Review an exact
  head" become a pointer to `method.md` with a one-sentence summary. "Label
  every review and finding" gains the `covered=` field, the definition of
  `findings=` as the count of P1 and P2 threads, the body order in section
  3.7, the three-part finding shape, and the priority definitions in section
  3.6, replacing its current sentence about visible finding text. "Publish
  one review" changes in two places: the sentence that every finding goes out
  as an inline thread now says every P1 and P2 finding does and that P3 items
  are listed in the body, and the clean-head rule counts only P1 and P2, so a
  review that posts no thread is clean whatever its `Suggestions` list holds.
- `skills/start-fix-loop/SKILL.md`: the verify step cites the protocol, and a
  new paragraph in "Establish the loop", beside the rule for body-only
  findings, says a `Suggestions` list is weighed item by item at the loop's
  discretion and never counts as outstanding work.
- `tests/test_packaging.py`: a test asserting that
  `skills/start-review-loop/method.md` exists and that both loop skills cite
  it, so a rename fails the gate instead of leaving a dangling reference.
- `README.md`, where the two loops are named: one sentence that reviews
  follow the method in `method.md`.

## 7. Implementation sequence

1. Write `method.md`.
2. Edit `skills/start-review-loop/SKILL.md` as section 6 describes.
3. Edit `skills/start-fix-loop/SKILL.md`.
4. Add the packaging test and run the full gate.
5. Update `README.md`.
6. Open the pull request as a draft and let the review loop review it with
   the working-tree skill, so the first review under the method is of the
   change that introduces it. Fix what it finds.

## 8. Acceptance criteria

- `skills/start-review-loop/method.md` exists and contains: an intent step; a
  file-kind table; six passes named correctness, placement, simplicity, cost,
  exposure, and written rules, each stating the question it asks and the
  failures it looks for; an outward-following step; the four-step protocol
  with the second-look rule; the shipping rules and P1, P2, P3 definitions;
  and the body order and finding shape.
- `skills/start-review-loop/SKILL.md` cites `method.md` from "Review an exact
  head", documents `covered=` and the `findings=` count in the review marker,
  shows the three-part finding shape, and states in "Publish one review" that
  only P1 and P2 findings are threads and that only they decide whether a
  head is clean.
- `skills/start-fix-loop/SKILL.md` cites the protocol by path and says a
  `Suggestions` list is not a body-only finding and is weighed at the loop's
  discretion.
- `method.md` and the text section 6 adds to either skill name no
  host-specific tool and no path on the reviewer's machine. Names relative
  to the repository, such as `AGENTS.md` and `docs/`, and Projector's own
  configuration files, `.projector.toml` and `~/.projector.toml`, are
  allowed.
- The packaging test fails when `method.md` is removed and passes on the
  branch.
- The full validation gate in `AGENTS.md` passes from the repository root.
- A review the loop publishes on a pull request in this repository after the
  change opens with an intent paragraph, prints a census line and a coverage
  line, carries `covered=` in its marker, lists any P3 items in the body
  rather than as threads, and each finding thread's first comment has a
  headline line, a behavior sentence, and a `Fix:` line.

## 9. Decisions

- **The method is a supplemental file, not more of `SKILL.md`.** That file is
  already long and is about identity, heads, markers, and verdicts. A reader
  fixing a publishing rule should not scroll through failure descriptions,
  and a reader tuning a pass should not scroll through GraphQL. A separate
  skill was rejected because the loop is its only consumer, and a skill the
  loop must remember to load is a second thing to install and a second place
  to drift.
- **Passes describe failures, not APIs.** A pass written around one
  framework's calls covers one language. A pass written around how code
  fails covers every language a repository contains and survives a framework
  migration.
- **Threads are the memory across heads.** A finding stays open until the
  author resolves it, the census counts it, and a `RESPONDED` re-review reads
  it. A per-branch file recording findings, verdicts, and acknowledgements
  was rejected because it duplicates thread state, can disagree with it, and
  is invisible on GitHub.
- **Every head gets every pass.** Reviewing only the range since the last
  reviewed head was rejected because a fix can break a file the last review
  cleared, and the skill already promises a complete cycle per SHA.
- **No cap on findings.** A fixed maximum with the remainder withheld was
  rejected because a withheld verified defect is a defect nobody fixes. The
  shipping rules and the P3 body list carry the precision instead.
- **P3 lives in the body, and the fix loop weighs it.** A suggestion posted
  as a thread puts the pull request in draft and makes the author resolve a
  nitpick before a reader sees the head as clean. Whether to act on a body
  item is the fix loop's judgment, made item by item against the cost of a
  push and the review cycle that follows. Acting on every item was rejected
  because it turns each suggestion into a cycle on a pull request already
  marked ready, and acting only when the user asks was rejected because it
  makes a person relay improvements the loop can see for itself.
- **Priorities are defined by introduction and impact,** not by pass. A pass
  says where to look; a priority says what happens if nobody acts.
- **The second look starts from the claim alone.** A verifier that reads the
  first look's argument checks the argument, not the code. Handing over only
  the claim is what makes the second look independent, on either host.
- **`covered=` is additive under `v=1`.** Nothing parses the review marker,
  so a version bump would signal a break that does not exist.
- **Advisory checks are disclosed, never assumed.** The loop runs unattended
  and may have no network. Saying a check was skipped keeps a silent pass from
  reading as a clean one.
- **The fix loop cites the protocol rather than restating it.** Two copies
  of four steps drift. The skills install together, so the path resolves.
- **The reviewer withdraws in the thread.** Deleting or editing a finding
  would erase the record; a reply with the reply marker keeps it and lets the
  author close it.

## 10. Open questions

None block implementation. Whether a host runs the passes in parallel
affects wall-clock time and nothing in this plan.

## 11. Implementation state

Every file in section 6 exists on the implementation branch: `method.md`
carries sections 3.1 through 3.7, the review skill points its inspection
steps at it and defines `covered=`, `findings=`, the priorities, and the
finding shape, the fix skill cites the protocol and leaves a `Suggestions`
list to the author, the packaging test guards the file, and `README.md`
names it. The full gate passes, and the packaging test fails when
`method.md` is removed.

The last criterion in section 8, a review the loop publishes under the
method, was observed on the pull request that carries this implementation.
The review loop inspected its first head, `95a6616`, by the method at that
head and said so in a disclosure: the body opened with an intent paragraph,
printed `1 finding threads: 0 resolved, 1 open` and `Covered 6 of 6 changed
files; 0 generated files skipped.`, carried `covered=6/6` in its marker,
listed one P3 item under `Suggestions`, and posted its one P2 finding with a
headline line, a behavior sentence, and a `Fix:` line.

## 12. Completion record

Shipped. Every file in section 6 exists, and every criterion in section 8
has evidence:

- `method.md` holds the intent step, the file-kind table, the six passes,
  the outward-following step, the four-step protocol with the second-look
  rule, the shipping rules and priorities, and the body order and finding
  shape, as sections 1 through 7 of that file.
- The review skill cites `method.md` from "Review an exact head", documents
  `covered=` and the `findings=` count, shows the finding shape, and scopes
  the inline-thread, clean-head, and approval-verification rules to P1 and
  P2 threads.
- The fix skill cites the protocol by its relative path and weighs a
  `Suggestions` list item by item.
- Neither `method.md` nor the added skill text names a host-specific tool or
  a path on the reviewer's machine.
- `test_review_loops_share_one_review_method` fails with `method.md` moved
  aside and passes with it in place.
- The full gate passes: 120 tests, valid plans, both plugin validations,
  and a clean whitespace check.
- The review under the method is the one section 11 describes.
- The plugin version moves from 0.2.3 to 0.2.4 in both manifests, so a host
  that caches the plugin by version installs the new skill text on its next
  update.

Section 4's rule for a `Suggestions` list changed during implementation,
from acting only when the user asks to weighing each item at the loop's
discretion; sections 4, 6, 8, and 9 carry the current rule.
