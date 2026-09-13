# Review method

`SKILL.md` says when a review runs, as whom it posts, and how a verdict is
recorded. This file says what you do between checking out the exact head and
publishing. Work through the sections in order.

The method is a sequence one agent performs in one conversation. Where the
host runs subagents, the passes in section 3 may run in parallel and the
second look in section 5 may go to a fresh agent. Nothing here depends on
that. Name no host tool and no path on your own machine in anything you
write; names relative to the repository under review, and Projector's own
configuration files, are the only paths a review needs.

## 1. State the intent

Before you read a hunk, write two to four sentences on what the change is
meant to accomplish and what it knowingly leaves temporary. Draw on the pull
request body, the commit subjects, `git diff --stat`, and the hunk headers.
Read individual hunks only when those do not settle it.

The paragraph opens the review body, and every later judgment is made
against it. A finding is about whether the change does what it set out to
do.

## 2. Sort the changed files

Give each changed file the first kind in this table that fits it. Adapt the
patterns to what the repository documents about its layout, its test naming,
and its generated code.

| Kind | Passes |
|---|---|
| Tests | Simplicity, written rules |
| Generated or vendored code, lockfiles, snapshots | None. Count them as skipped in the coverage line. |
| Dependency manifests | Exposure |
| CI, build, and configuration files | Exposure, written rules |
| Documentation | None. Read it yourself for accuracy against the code it describes, broken references, and contradictions. |
| Scripts and tooling | Correctness, simplicity, exposure, written rules |
| Source code | Every pass |

Run the written-rules pass only when the repository has rules to cite:
`AGENTS.md`, `CLAUDE.md`, a contributing guide, a pull request template, or
style documents under `docs/`. The review runs the union of passes across
the kinds present.

## 3. Run the passes

A pass is one way of reading the change. For each pass, read the diff, then
read every file assigned to the pass from top to bottom, because a hunk
shows an edit and hides the lifecycle around it. Report only what falls
inside the pass's question, and name every assigned file in which the pass
found nothing, so section 7 can account for the whole change.

### Correctness

Does the code still hold when time passes, when data is missing, and when
something fails?

Between a check and the action it guards, ask what can change. An await, a
callback, a dispatch to another queue, or a timer opens a gap, and a
condition that was true before the gap may be false after it. Ask what a
completion handler does when its operation was cancelled or failed rather
than finishing, and whether a flag that marks work as pending is reset on
every exit path, disconnect included, and only once the work is really done.
Ask whether two threads, tasks, or handlers can touch the same state at
once, and whether a handler written to run once can run twice.

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

### Placement

Is this code where the repository keeps code like it?

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

### Simplicity

What does the change leave behind, and what does it reinvent?

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

### Cost

What does the change make the program do, and how many times?

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

### Exposure

Where does untrusted data go, and who can reach the new surface?

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
than it needs.

When the change adds or bumps a dependency, write down the package and
version. Where the host can reach the network, look up known advisories for
that exact version. Where it cannot, say so in the review body as section 7
describes. Do not guess either way.

### Written rules

Does the change follow what the repository wrote down?

Read the parts of the repository's rule documents that bear on the changed
files, then read the diff against them. A finding here names the document and
the section it enforces, or it is a preference and does not ship. When the
rule is one the repository's own merged commits keep breaking, report the
gap between the document and the practice as a P3 rather than as this
change's fault.

## 4. Follow the change outward

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

## 5. Verify before you post

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
defect again. Where the host runs subagents, hand the claim to a fresh one
with a wider brief: the full files, the callers, the related tests. Otherwise
set the first look aside, read those same sources again, and write the
failure sequence from scratch. A second look that confirms produces a
concrete sequence from inputs and state to a wrong result. One that refutes
names the guard that prevents it. A second look that cannot decide is a
dropped candidate, never a question handed to the author.

Run a test or reproduction when the code is runnable and the cost is
proportional to the risk, as `SKILL.md` requires. The protocol is what
verifies a finding when running it is not possible.

## 6. Decide what ships

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

Then assign a priority, as `SKILL.md` defines them: P1 and P2 are defects the
change introduces, and they post as threads and block; P3 is a change the
code is correct without, and it goes in the review body under a
`Suggestions` heading, never as a thread.

There is no cap on the number of findings. The rules above are the filter,
and every thread is one the author must resolve, so post nothing you would
not defend in the thread.

## 7. Write the review

After the signature line and marker `SKILL.md` requires, the review body
reads in this order:

1. The intent paragraph from section 1.
2. The thread census `SKILL.md` requires.
3. A coverage line: how many changed files a pass or you read, and how many
   were skipped as generated, for example
   `Covered 11 of 13 changed files; 2 generated files skipped.` Every changed
   file that is not generated must be accounted for by a finding, by a pass's
   clean list, or by your own direct read. Read any file no pass covered
   before the review posts. The marker's `covered=` field carries the same
   two numbers.
4. Disclosures, when any: an advisory check skipped for a new dependency, a
   file too large for a pass to read in full, a test that could not run.
5. `Suggestions`, the P3 list, when any, each as one line naming the
   `path:line` and the change.
6. On a clean head, what was checked, as `SKILL.md` requires.

Each P1 or P2 thread's first comment, after its marker, has three parts:

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
mechanical. Write plain declarative sentences. Do not hedge: the protocol
either produced a sequence or the finding did not ship. Keep pass names and
protocol vocabulary out of anything the author reads. Do not restate the
change's context; the intent paragraph carries it.

When a re-review shows an open finding was wrong, reply in its thread, open
the reply with `<!-- projector-reply v=1 -->` so no loop reads it as a
finding, name the guard or behavior that shows the finding was wrong, and say
the finding is withdrawn. The author resolves the thread; you never do.
