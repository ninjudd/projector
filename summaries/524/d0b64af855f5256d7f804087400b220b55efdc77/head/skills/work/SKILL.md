---
name: work
description: Keep working on the session's current task, or the work the user names, on your own until it is done. Make each decision that has a reasonable answer yourself, post the ones the user must review to their inbox and keep building what they do not block, mark each pull request ready for review once your work on it is done, and run fix-pr on each one as its review work arrives. Use when the user asks the session to work independently, autonomously, or unattended, to keep going without them, or to carry on while they are away.
---

# Work independently

Carry the session's work through to the end without waiting on the user.
The work is what the user names when they run this skill, or, when they
name nothing, what the conversation is already doing: the task they gave
you, the plan you are building, and the pull requests you opened. Restate it
in a sentence or two before you start, so the user can see what you took
it to be.

Run this skill from the conversation the user talks to, never from a
subagent: only that conversation hears the session's events and inbox
answers, and a subagent's processes end when it returns.

Working independently changes when you stop, not what you are allowed to
do. Every rule the repository and the other skills set still holds: build
each change with `implement`, open pull requests and never merge them, and
leave alone what you were not asked to touch.

## Decide what you can

Make each decision that the request, the repository, or a sensible default
answers, and keep going. Read the code, the plan, the repository's
instructions, and the conventions around the change before you call a
choice open; most choices that look open have an answer there.

Some decisions are the user's. Leave a decision to them when it is one of
these:

- **A product choice** that the repository cannot answer, such as what a
  feature should do or how it should look.
- **A change of scope**, such as work beyond what they asked for, or
  dropping part of what they asked for.
- **An action that is hard to undo or reaches outside the repository**,
  such as deleting work that exists nowhere else, rewriting a branch other
  people use, publishing, sending a message, or installing on one of their
  devices, unless they already authorized it.
- **A review finding a person left that you would decline**, because
  declining it overrules them.

When such a decision blocks only part of the work, choose the answer you
would recommend where it is safe to build on and easy to change later,
build on it, and post the decision for review. When no answer is safe to
build on, post it and build everything else first.

## Post what the user must see

Post to the inbox each decision the user must review, each thing only they
can do, and each piece of news they must act on, because they may be away
and the inbox stays above their prompt until they deal with it. Use
Projector's `ask`, `request`, and `tell` tools, or, where the host has none,
the `project inbox ask`, `request`, and `tell` commands, which take the same
fields as flags that `--help` lists. Pass each command the session's id
with `--session`, as `../cleanup/SKILL.md` describes under "Find the
session's id".

Where the host has the tools, Projector's panel delivers the user's answers
to the session. In Codex, when the session-start context says that Codex
watch delivery handles the session, the watch daemon delivers them as new
turns, so start no deliverer. Otherwise nothing delivers them until you
start their deliverer, because `project watch stream` prints only pull
request events.
Start `project inbox watch --session <id>` once, under the host's
persistent process or monitor facility, where it runs until stopped and
prints each answer as one of the lines below. Where the host has no such
facility, run `project inbox watch --once --session <id>` between pieces of
work, which prints the answers that arrived since the last pass and exits.

Post each kind of item this way:

- **A decision** goes to `ask`, with the answer you recommend first. When
  you already built on that answer, say so in the detail, so they know that
  picking another option means a change.
- **Work only they can do**, such as signing in to a service or checking a
  change on their phone, goes to `request`, with numbered steps in the
  detail. Prepare a phone check of an iOS app as `../implement/SKILL.md`
  describes under "Prepare a device test for an iOS app".
- **News they must act on**, such as a failure you cannot fix, goes to
  `tell`.

Name the pull request an item is about with its `pr` field. Leave out what
they can see elsewhere, such as a pull request's state, which the panel
lists, and decisions you made that they do not need to review; record those
in the pull request body or the plan instead, where a reviewer reads them.

Never wait for an answer. Post the item and go on with the work it does not
block. When an answer arrives, as a line that starts `ANSWER`, `DONE`, or
`DECLINED`, act on it before you go on, including undoing what you built on
a recommendation the user did not pick. When a reply in their own words
settles an item, withdraw it, and when a line that starts `UNCLEAR` arrives,
revise the item to explain it better.

## Hand each pull request to review

Open each pull request as a draft, as `../implement/SKILL.md` describes
under "Hand the work over", and subscribe the session to fix it. Mark it
ready as soon as your work on it is done, so a session that reviews the
repository reviews it:

```sh
project review ready <number> --repo <owner>/<repo>
```

Run it as a tool call of its own, not after `cd` and joined to no other
command, and never as a bare `gh pr ready`, as `../fix-pr/SKILL.md`
describes under "Hold the pull request while changes are built".

Do not hold a finished pull request in draft until the rest of the work is
done. Keep one a draft only while you still have changes to push to it, or
while a request asks the user to test it or to review its plan and they
have not answered, as `../implement/SKILL.md` describes for a test request
and `../spec/SKILL.md` for a plan review request.

## Fix each pull request as reviews arrive

When you start, run `fix-pr` once on each pull request the work includes,
because review work may already be waiting on it, in one subagent per pull
request, as `../fix-pr/SKILL.md` describes under "Give each pull request
its own subagent". Then keep fixing
as reviews arrive: each `FINDING`, `VERDICT`, `CONFLICT`, `REVIEW`, and
`CHECKS` line the session receives runs `fix-pr` for its pull request in
that pull request's subagent, as `../fix-pr/SKILL.md` describes under "Act
on the session's events". Run each fix without asking the user first. When
`fix-pr` holds a pull request in draft to build a change, it marks it ready
again once the push that carries the change lands.

A finding you would decline still gets its reply with evidence, as `fix-pr`
describes. When a person left it, also post it with `ask`, because
declining it is their decision.

## Keep going until the work is done

Do not end a turn with a question or a plan for the user to approve. When
one piece is blocked, go on with the next. Stop only when every piece of the
work is done or waits on the user, and then report, as a list:

- **Each pull request** you opened or fixed, with its URL and whether it is
  ready for review or still a draft, and why.
- **Each decision you made** that a reviewer would want to know about, with
  where you recorded it.
- **Each item waiting in the inbox**, named by what it asks, tells, or
  requests.
- **Each piece of work left**, with what it waits on.

The session stays subscribed to its pull requests after the report, so it
keeps fixing them as reviews arrive. When the user is done with the
session, `cleanup` checks the work and releases what the session holds.
