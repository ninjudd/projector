---
status: ready
priority: now
---

# Hand a finished plan to the user for review

`spec` ends by opening the plan as a draft pull request and asking the user,
in the inbox, to read the plan as GitHub renders it, and it marks the pull
request ready for review once the user answers that it looks good.

This plan combines [#393](https://github.com/ninjudd/projector/issues/393)
and [#439](https://github.com/ninjudd/projector/issues/439).

## 1. Problem

The `spec` skill writes a project's plan under `docs/projects/`. A plan's
decisions are the user's to make, so the user reads every plan before
anyone builds it. That review needs a pull request, because a pull request
is where Projector's review and fix loops run and where the user merges.

Today `spec` gets in the way of both halves of that review:

- **It opens no pull request.** Its "Validate and hand over" section says
  to leave the plan uncommitted and not to push or open a pull request
  unless asked. Users expect the opposite, so after every spec they have to
  ask for the pull request, and a plan left in a worktree reaches no
  reviewer. In one session the user's reply was "You should always create a
  PR after making a spec."
- **It never asks the user to read the plan.** When it does open a pull
  request, the hand-off is a URL in the conversation. That URL can scroll
  past in a long report or arrive while the user is away, and it opens on
  the diff, not on the plan as a reader sees it. In one session the user
  had to ask whether two finished specs were ready to review and where to
  read them.

A third problem follows from the first fix. `spec` marks its pull request
ready as soon as the plan is written, and marking it ready starts the
automated review. That review then reads a plan whose design the user is
about to change, so its cycles are spent on text that will not survive.

## 2. Solution

When `spec` finishes a plan, it commits it on a branch, pushes, and opens a
draft pull request. It then posts one inbox `request` asking the user to
review the plan. The request links to the readme rendered on the branch and
to the pull request. The pull request stays a draft until the user answers:
`Looks good` marks it ready with `project review ready`, `Some feedback`
makes `spec` revise the plan and the same request, and declining marks it
ready too. A blocking question is posted as an `ask` before the review
request, which waits until the answer settles it. Where Projector's inbox
tools are missing, `spec` marks the pull request ready at the hand-over, as
it does today. This is the pattern `implement` already uses for a device
test, applied to a plan.

### 2.1 The flow

1. `spec` writes the plan and runs `project show`, `project check`, and
   `git diff --check`, then reads the plan as its reviewer would, as it
   does today.
2. It commits the plan on a branch, never on the default branch, runs
   `git diff --check origin/<default-branch>...HEAD`, and pushes. Step 1's
   bare `git diff --check` reads only tracked changes in the working tree,
   so it misses a new plan file and anything already committed; the range
   form reads the whole branch. If that branch already has an open pull
   request, such as a plan being refined in an earlier spec's pull
   request, it pushes there and skips the next step.
3. It opens the pull request with `gh pr create --draft`. The title
   follows the pattern of the default branch's recent subjects. The body
   explains why the plan exists and ends with a `## Testing` section that
   lists `project show <name>`, `project check`, and
   `git diff --check origin/<default-branch>...HEAD`. Where the
   repository's instructions or the user say otherwise, `spec` follows
   them, except that the merge stays the user's.
4. It subscribes the session to the pull request with
   `project watch add <owner>/<repo>#<number>`, as it does today.
5. For each open question that blocks implementation and needs the user's
   decision, it posts an `ask`. The ask's detail links the section on the
   rendered plan. As each answer arrives, `spec` records the decision in
   the plan, moves the status with `project status` once the plan's
   readiness claim is honest, and pushes.
6. Once no blocking question waits on the user, it posts the review
   request, which [§ 2.2](#22-the-review-request) describes. It posts one
   request per pull request, and only for a pull request this session
   opened in step 3.
7. It hands over: the pull request URL, the rendered plan's URL, that a
   review request waits in the inbox, and whether the session is
   subscribed.
8. It acts on the user's answer:

   | Answer | What `spec` does |
   | --- | --- |
   | `Looks good` | Runs `project review ready <number> --repo <owner>/<repo>`. |
   | `Some feedback` | Revises the plan from the notes, pushes, and updates the same request with the `revise` tool, which returns it to open. The pull request stays a draft. |
   | Declined | Runs `project review ready`, because a declined request is the user's no to reviewing it in the inbox, not to the plan. |
   | A reply in the user's own words | Answers it. The request stays open. |

   The user's approval changes only the pull request's draft state, never
   the plan's `status`. The status is `spec`'s readiness claim, and
   step 5 has already made it honest. When the user asks for the automated
   review to go first, `spec` marks the pull request ready at the hand-over
   instead and still posts the request, so the user's answer only revises
   the plan.
9. Where the `ask`, `tell`, and `request` tools are not available, `spec`
   skips steps 5, 6, and 8. It raises blocking questions in the
   conversation, gives the rendered plan's URL in the hand-over, and marks
   the pull request ready there, because no inbox answer would ever arrive
   to do it.

When the session ends while the request is still open, `cleanup` lists the
pull request as a gap. It already lists an open request and a held draft as
gaps. When the user chooses to leave it, `cleanup` marks it ready, as it
does for a pull request held by `project review hold`.

### 2.2 The review request

| Field | Value |
| --- | --- |
| `request` | Names the project and what it makes true in a few words, such as `Review the spec-handover plan: specs end with a review request`. |
| `detail` | Leads with `Spec:` and `https://github.com/<owner>/<repo>/blob/<branch>/<path>/readme.md`, then `PR:` and the pull request's URL. Then a numbered list of the sections to read, each with what the user should find there, such as "§ 2.1 The flow: the steps `spec` takes after `project check` passes." |
| `options` | `Looks good`, `Some feedback`. |
| `start` | None. There is nothing to prepare. |
| `pr` | `<owner>/<repo>#<number>`. |

`spec` reads the owner, repository, and branch for the link from
`gh pr view <number> --json headRepositoryOwner,headRepository,headRefName`,
so the link names a fork's branch correctly. It posts the request only after
the push in step 2 or 5, so the link never names a branch that GitHub does
not have.

### 2.3 Files

| File | Change |
| --- | --- |
| `skills/spec/SKILL.md` | Replace the "Leave the plan changes visible" paragraph, and the "When you open a pull request for the plan" paragraph with its `project watch add` block, with the flow in § 2.1 and the request in § 2.2. Keep the "Subscribe as `../fix-pr/SKILL.md` describes" paragraph, moved under step 4. Keep the `## Validate and hand over` heading, and add `### Open the pull request` and `### Ask the user to review the plan` under it. |
| `src/projector/templates/agents-block.md` | Say that a plan written with `spec` is opened as a pull request the same way, and widen "When the hand-over asks the person to test the change" to also cover a request to review a plan. Bump the begin marker from 25 to 26. |
| `tests/test_instructions.py` | Pin version 26's hash in `PINNED`. |
| `AGENTS.md`, `CLAUDE.md` | Refresh the generated section with `project init`. Widen "while a test request waits on the user" in the repository's own "Hand work over for review" section the same way. |
| `skills/cleanup/SKILL.md` | Add a draft kept while a test or plan review request waits to the held-pull-request gap, so the "mark it ready when the user leaves it" rule covers it. |
| `skills/fix-pr/SKILL.md` | Widen "one `../implement/SKILL.md` keeps while a test request waits on the person" in "Report the result" to also cover a draft `../spec/SKILL.md` keeps while a plan review request waits, so `fix-pr` never marks it ready. |
| `skills/work/SKILL.md` | Widen "while a request asks the user to test it and they have not answered" to also cover a request to review a plan, so `work` keeps a plan pull request a draft until the user answers. |
| `docs/plugins.md` | Say that `spec` opens a draft pull request and asks you to review the plan in the inbox. |
| `tests/test_review.py` | Add `spec` to `test_the_skills_move_draft_state_through_the_commands_alone`. |

## 3. Why this design

- **The user's review comes before the automated one.** A plan's review is
  mostly the user's decisions, and their feedback often changes the design
  itself, not just its wording. An automated review of a design the user
  is about to change finds problems in text that will not survive, and its
  findings then collide with the user's. For code, the cheaper reviewer
  usually goes first. For a plan, the user's review is what makes the
  automated one worth running. The rejected alternative is marking the
  pull request ready at the hand-over and posting the request beside it,
  which runs both reviews at once. A user who wants the automated review
  first says so, and `spec` follows step 8's exception.
- **The draft hold reuses `implement`'s rule.** `implement` keeps a pull
  request a draft while a device-test request waits and marks it ready on
  `Looks good` or a decline. The plan review follows the same rule, so the
  generated section, `fix-pr`, and `work` each widen one sentence instead
  of adding an exception, and `cleanup` handles both waits in one gap.
- **A blocking question gets an `ask`, not a line in the request.** The
  generated section already says to use `ask` for a decision and `request`
  for work only the user can do. An `ask` carries a recommendation and
  lettered options, so the answer comes back as a decision `spec` can
  record. The rejected alternative folds open questions into the review
  request's steps, so a decision would arrive as free-text notes on
  `Some feedback`.
- **A decline marks the pull request ready.** Otherwise a user who reviews
  the plan on GitHub instead of in the inbox would leave it a draft
  forever, and GitHub refuses to merge a draft. `implement` treats a
  declined test the same way.
- **The link opens the rendered plan.** A `blob` link on the branch shows
  the plan as its reader sees it and follows each push, so the request
  stays valid through `Some feedback` revisions. The pull request link is
  still there for the diff and the review threads.
- **`spec` moves draft state only through `project review ready`.** #439
  names `gh pr ready`, but a host's automatic approval can refuse a bare
  `gh pr ready` as self-approval, and `project review ready` confirms that
  the state changed.

## 4. Acceptance criteria

Run `spec` in a temporary repository with a GitHub remote and Projector's
inbox tools available.

| Do | Expect |
| --- | --- |
| Ask for a plan with no blocking question. | A draft pull request opens, the session is subscribed, and one request waits in the inbox whose detail starts with a `Spec:` link that opens the rendered readme. |
| Answer `Looks good`. | `gh pr view --json isDraft` prints `false`, and the plan's `status` is unchanged. |
| Answer `Some feedback` with a note on one section. | The plan changes on the branch, the same request reopens with its revised detail, no second request appears, and the pull request is still a draft. |
| Decline the request. | The pull request is marked ready. |
| Ask for a plan with a question only the user can answer. | An `ask` appears first, and no review request does. After the answer, the decision is in the plan and the review request appears. |
| Refine that plan in a second `spec` run on the same branch. | The push lands on the existing pull request, and no new pull request or request opens. |
| Run `spec` where the inbox tools are not available. | The pull request opens and is marked ready at the hand-over, and the hand-over gives the rendered plan's URL. |
| Run `cleanup` while the review request is open, and choose to leave it. | The pull request is marked ready, and the request is withdrawn. |

`test_the_skills_move_draft_state_through_the_commands_alone` with `spec`
added fails before the change if a `spec` command block runs a bare
`gh pr ready`. `test_the_template_text_is_pinned_to_its_version` fails if
the template changes without its version bump.

## 5. Cost

Cost does not matter here. The change adds one `gh pr create`, one
`gh pr view`, and one inbox item to each `spec` run, and nothing to any
loop.

## 6. Open questions

- **A session that ends without `cleanup`.** Its plan pull request stays a
  draft until someone marks it ready. The panel already shows a draft's
  state, so this plan defers any automatic fix.
