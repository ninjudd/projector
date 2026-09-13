---
status: ready
priority: now
---

# Give the review loop a review method

## 1. Outcome

A review the loop publishes rests on a written method rather than on whatever
the reviewing session thinks to try. Before the first finding, the reviewer
states what the change is for. It sorts the changed files, checks each one
through a fixed set of passes that name failure shapes, follows every changed
symbol to the code that consumes it, and verifies each candidate finding by a
four-step protocol before that finding becomes a thread. Rules decide which
verified findings ship and at what priority, and only findings that block go
out as threads. The review body says how much of the change was covered,
beside the thread census it already prints. The fix loop verifies and declines
findings by the same protocol.

The method runs under both hosts as one agent working in sequence. Parallel
subagents are an optimization a host may offer, never something the method
depends on.

## 2. Constraints

- Write every sentence of the method for Projector, in the register the
  repository documents. Do not paste text from another review skill or tool,
  whatever its license or provenance.
- Name failure shapes, not framework APIs, so one method covers every language
  a repository might contain.
- Name no host tool and no path outside the skill directory. The text says
  what to read and check; the host decides how.
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

Write two to four sentences on what the change delivers and what it knowingly
leaves temporary, from the pull request body, the commit subjects,
`git diff --stat`, and the hunk headers. Read individual hunks only when those
do not settle it. The paragraph opens the review body, and every later
judgment is made against it: a finding is about whether the change does what
it is for.

### 3.2 Sort the changed files

Assign each changed file to the first matching kind, adapting the patterns to
the conventions the repository documents:

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

Each pass reads the diff and then each assigned file in full, because state
and lifecycle are invisible in hunks. A pass reports only findings inside its
own focus and lists every assigned file it found nothing in, so the coverage
line in section 3.7 can account for the whole change.

**Correctness.** How the code behaves across time, absence, and failure:

- A condition checked before an await, callback, or dispatch and acted on
  after it without being checked again.
- A completion handler that ignores its cancelled or failed status and
  mutates state anyway.
- Work deferred to a queue, tick, or timer that reads mutable state without
  re-checking it when it runs.
- In-flight, joined, or pending flags that are not reset on disconnect or
  are cleared before the operation completes.
- Shared mutable state reached from more than one thread, task, or handler
  without coordination, and handlers written as if they fire once.
- Not-yet-loaded read as empty, zero, or false, above all on state that gates
  permissions, balances, or features.
- Two absent values compared as equal where "both unknown" is not "same".
- Teardown that clears some state and not all of it, so reuse sees leftovers.
- New cached or persisted state not cleared on logout or context reset where
  the repository clears such state.
- A cache or memo whose key omits one of the computation's inputs.
- Zero, negative, and maximum values where the change does arithmetic,
  indexing, or sizing, and empty confused with absent.
- A value decoded from an API, storage, or configuration that the code does
  not recognize and therefore crashes on or silently drops.
- Errors swallowed without handling, cancellation shown to the user as
  failure, and one failure hiding another when work runs in parallel.
- Listeners, observers, and timers registered without a matching removal,
  background work started without a handle to cancel it, and files, sockets,
  and handles without a close path on the error branch.

**Placement.** Whether code is where the repository's structure says it goes:

- Business logic in presentation code, or presentation concerns in domain or
  data code, judged against the layering the repository practices and
  documents.
- A new import that points the wrong way through the module graph.
- Logic derived from shared state implemented inside one consumer when other
  consumers need the same answer.
- Reaching through an abstraction instead of extending it: grabbing a
  dependency's internals, duplicating a client's request logic beside it,
  bypassing the established access path for storage, network, or config.
- A second way to do what the codebase already does one way. The existing
  way is the fix.
- New public surface that exposes internals or takes parameters only one
  caller can produce.

**Simplicity.** What the change leaves behind or reinvents. Skip anything the
repository's linters already catch:

- Members, parameters, and functions nothing reads after the change. Search
  for the old name before deciding.
- A marker value whose only producer the change removed, leaving branches
  keyed on it dead.
- Commented-out code and empty branches or handlers.
- A helper the repository or its standard library already has.
- Near-identical blocks in the diff that should be one.
- An interface with one implementation, a wrapper that only forwards, an
  option no call site varies.
- A new file of a kind the repository has an established shape for that
  diverges from that shape without saying why.

**Cost.** Concrete runtime cost the change adds. Every finding names what
runs, how often, and why it is expensive:

- Nested scans on real data sizes where a set or map would do, and invariant
  work recomputed inside a loop.
- Synchronous I/O, parsing, or heavy computation on the main thread or the
  request path, and render work triggered per frame or per keystroke.
- A request or query per item where a batch exists, and sequential awaits
  that could run together.
- Allocation, formatter or regex construction, and serialization inside
  loops or per-request code.
- Eager work at startup or import that could be lazy.
- Caches and collections that only grow, and listeners accumulated per view
  or per event without removal.
- New reads or endpoints with no limit or pagination.
- Verbose logging or diagnostics left in a hot path.

**Exposure.** Vulnerabilities and data exposure the change introduces:

- An externally controlled value reaching a shell, SQL, HTML, URL, format
  string, or eval sink without the repository's established sanitization.
- A new handler, route, or endpoint missing the authorization its siblings
  have, authorization decided from input the client controls, and an
  identifier accepted without an ownership check.
- A credential, token, or key added to code, configuration, logs, or CI, or
  moved from a secure store to a less secure one.
- Personal data, tokens, or internal error detail newly routed to logs,
  analytics, error reporters, URLs, the clipboard, or user-visible text.
  Trace the value to every producer and judge the sink by the most sensitive
  one.
- Untrusted input parsed into executable or type-polymorphic forms.
- Plaintext transport or unencrypted storage where the repository has a
  secure pattern, and disabled certificate checks.
- Deep links, webhooks, IPC, query parameters, and uploads validated for
  shape but not origin, and navigation driven by unvalidated input.
- A CI workflow that runs attacker-controllable code with secrets in scope,
  or that widens token permissions.
- A new or bumped external dependency. Record its coordinates. When the host
  allows network access, check its advisories at the pinned version;
  otherwise record the skipped check in the review body as section 3.7
  describes. Never guess.

**Written rules.** Conformance to rules the repository wrote down. Read the
sections of the rule documents that apply to the changed files, then the
diff. Every finding cites the file and section it enforces. A rule that merged
history routinely ignores is reported as a P3 divergence between the document
and practice, never as a violation of this change.

### 3.4 Follow the change outward

A changed symbol is only as correct as the code that consumes it, and that
code usually did not change. For every changed function, type, and field:

- List its callers and readers across the repository, including the ones the
  change did not touch, and check the change holds at each.
- Re-check every caller against a new precondition, a changed return value, a
  newly raised error, or a changed side effect.
- Check when callers actually run. A guard inside the changed code runs when
  it is entered, not when a deferred, queued, batched, or repeated call lands.
- When a value reaches a new sink, find every producer of that value and judge
  the sink by the most sensitive one.
- When a persisted or wire type changes shape, check every place that builds
  it and every place that reads it.

### 3.5 Verify before you post

Walk four steps for every candidate finding, against the exact head SHA in
the scratch worktree:

1. Quote the current lines. A candidate with no specific code is speculation
   and is dropped.
2. Trace the runtime path with concrete values: which branch runs, what the
   variables hold, what the outcome is.
3. Write the triggering sequence, a specific series of user actions or system
   events that this application can actually produce. No plausible trigger
   means the candidate is theory and is dropped.
4. Search the surrounding code for a guard, assertion, or upstream check that
   already prevents the scenario. For anything repeated or re-entrant, trace
   the second call, because the first often sets the flag or fills the cache
   that stops the path afterwards.

A candidate that passes all four steps is verified. A candidate that passes
the first three with the fourth unclear gets a second look before it posts.
The second look re-derives the defect from the claim alone: the pass, the
`path:line`, the symbol, and a one-line headline. It never re-reads the first
look's reasoning, because agreeing with an argument is not the same as
finding the defect again. Where the host runs subagents, hand the claim to a
fresh one with a wider brief: the full files, the callers, the related tests.
Otherwise set the first look aside, read those same sources again, and write
the failure scenario from scratch. A second look that confirms produces a
concrete failure scenario, inputs and state to wrong outcome. One that refutes
names the mechanism that prevents it. A second look that cannot decide is a
dropped candidate, never a question handed to the author.

Run a test or reproduction when the code is runnable and the cost is
proportional to the risk, as the skill already requires. The protocol is what
verifies a finding when running it is not possible.

### 3.6 Decide what ships

Apply these rules to the verified set, in this order:

- **Every finding names its fix** in one sentence. A finding with no nameable
  change is an observation and is dropped.
- **The change must introduce or worsen the defect.** Compare against the
  base. A defect already present at the base is at most a P3 and says so.
- **A rule violation cites its rule** by file and section. Without a citation
  it is a style opinion and is dropped.
- **One root cause is one finding.** The same defect at several sites becomes
  one finding that lists every location and anchors at the first. Two
  candidates on the same defect merge, keeping a second framing only when it
  adds information.
- **Impact is concrete.** A cost finding names what runs and how often; a
  correctness finding names the wrong outcome. Theory does not ship.

Then assign a priority:

- **P1.** A defect the change introduces or worsens with concrete impact on
  users, data, money, availability, or security. Posts as a thread. Blocks.
- **P2.** A defect the change introduces with bounded impact, or a violation
  of a rule the repository wrote down. Posts as a thread. Blocks.
- **P3.** An improvement the code works without: a simplification, dead code,
  a divergence between a written rule and practice, a defect already present
  at the base. Listed in the review body under a `Suggestions` heading. Never
  a thread, never blocks.

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

The review marker gains `covered=<read>/<changed>` after `seconds=`. The
version stays `v=1`: no watcher parses the review marker, and the collision
check matches `projector-review .* sha=`, so an added field breaks nothing.

Each finding thread's first comment, after its marker, has three parts:

```
**P1 · Signing out leaves the previous account's balance cached for the next sign-in**

`signOut` clears the session token and the positions map but not `balanceCache`,
so the next account to sign in on the device reads the old balance until its
first refresh completes.

**Fix:** clear `balanceCache` in `signOut` beside the positions map.
```

The headline states the problem and its consequence in about a dozen words,
with no pass name and no path; the path is the thread's anchor. The body is
one or two sentences of concrete behavior that name the symbol. The fix is one
sentence, with a snippet of a few lines only when the change is mechanical.
Sentences are plain and declarative. No hedging, because the protocol either
produced a trigger or the finding did not ship. No pass names or protocol
vocabulary anywhere the author reads. No restating the change's context; the
intent paragraph carries it.

When a re-review shows an open finding was wrong, reply in its thread, open
the reply with `<!-- projector-reply v=1 -->` so no loop reads it as a
finding, name the mechanism that refutes it, and say the finding is
withdrawn. The author resolves the thread; the reviewer never does.

## 4. The fix loop verifies the same way

`skills/start-fix-loop/SKILL.md` step 1 under "Verify and fix findings" cites
section 3.5 of the method by path: verify the claim by the four steps, and
decline a finding by naming the guard or mechanism with the quoted code that
shows it. Nothing else in that skill changes.

## 5. Host neutrality

The method is a sequence one agent performs in one conversation. Where the
host runs subagents, the passes in section 3.3 may run in parallel and the
second look in section 3.5 may go to a fresh agent. The text describes what
to read and check and never names a host's tool, a permission model, or a
path outside `skills/start-review-loop/`.

## 6. Files

- `skills/start-review-loop/method.md`: new. Sections 3.1 through 3.7 as
  instructions to the reviewer.
- `skills/start-review-loop/SKILL.md`: steps 5 and 6 under "Review an exact
  head" become a pointer to `method.md` with a one-sentence summary. "Label
  every review and finding" gains the `covered=` field, the body order in
  section 3.7, the three-part finding shape, and the priority definitions in
  section 3.6, replacing its current sentence about visible finding text.
- `skills/start-fix-loop/SKILL.md`: the verify step cites the protocol.
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
  exposure, and written rules, each a list of failure shapes; an
  outward-following step; the four-step protocol with the second-look rule;
  the shipping rules and P1, P2, P3 definitions; and the body order and
  finding shape.
- `skills/start-review-loop/SKILL.md` cites `method.md` from "Review an exact
  head", documents `covered=` in the review marker, and shows the three-part
  finding shape.
- `skills/start-fix-loop/SKILL.md` cites the protocol by path.
- Neither skill names a host tool or a path outside `skills/`.
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
  fixing a publishing rule should not scroll through failure shapes, and a
  reader tuning a pass should not scroll through GraphQL. A separate skill was
  rejected because the loop is its only consumer, and a skill the loop must
  remember to load is a second thing to install and a second place to drift.
- **Passes are named by failure shape.** A pass that names framework APIs
  covers one language. Shapes cover every language a repository contains and
  survive a framework migration.
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
- **P3 lives in the body.** A suggestion posted as a thread puts the pull
  request in draft and makes the author resolve a nitpick before a reader sees
  the head as clean. The fix loop already handles body-only findings, so
  nothing new is needed on that side.
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

None block implementation. Whether P3 items should ever become threads, for
instance when an author asks for them, is deferred until a few reviews under
the method show whether the body list gets acted on. Whether a host runs the
passes in parallel affects wall-clock time and nothing in this plan.
