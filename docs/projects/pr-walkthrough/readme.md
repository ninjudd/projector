---
status: in-progress
priority: next
---

# Walk reviewers through large pull requests

## 1. Outcome

A reviewer facing a large pull request opens one page instead of the files
tab. The page splits the diff into logical groups in reading order. Each group
opens with what the change does and why, the ideas to hold while reading, and
a checklist, then shows that group's files with every hunk syntax-highlighted.
The reviewer marks files viewed and groups reviewed as they go.

The page framework is a fixed renderer that ships with Projector. An agent
writes only the part that needs judgment, a JSON spec holding the grouping,
the explanations and the checks, and a script combines the spec with the pull
request's diff.

## 2. Acceptance criteria

- The `walkthrough-pr` skill builds a page from a spec and the diff, and
  refuses a spec that leaves a changed file out, repeats one, names one
  outside the diff, or describes a head the pull request has moved past
  whenever GitHub can be reached to say so.
- The page works from disk in any browser and publishes unchanged as a Claude
  Artifact, with the renderer passed as supporting files.
- The build script uses only the Python standard library and the `gh` CLI,
  and the renderer loads remote scripts only from cdnjs.
- A GitHub Pages publisher serves each repository's walkthroughs to the people
  who can read that repository (section 4).

## 3. Hosting

Three hosts were weighed.

- **Claude Artifacts.** No infrastructure, private by default, and available
  now. Sharing is a manual step per artifact because there is no sharing API,
  and only claude.ai users can open the page. This change ships this path.
- **GitHub Pages per repository.** Fully inside GitHub, and access can follow
  the repository's own permissions. It needs private Pages, which GitHub
  offers only on Enterprise Cloud; on any other plan a private repository's
  Pages site is public, so the publisher must refuse to run there.
- **An external Projector site.** Rejected: it would host other people's code
  and would need a GitHub App to enforce repository permissions, which Pages
  provides without either.

## 4. GitHub Pages publisher

Deferred until a repository with private Pages is available to test against.
The design: the agent commits the renderer and one data file per pull request
head, such as `walkthroughs/<number>/<head>.json`, to an orphan branch through
the Git Data API, so no checkout is needed. Pages serves that branch directly
and the renderer loads the data file in the browser, so no Actions workflow
runs. Other stores do not fit: Actions artifacts expire and need a login to
fetch, Pages cannot read Git notes, secret gists are unlisted rather than
private, and a pull request comment holds 65,536 characters while a large diff
runs to megabytes. Open questions: squashing the branch so generated history
does not grow without bound, keeping CI off pushes to it, and an index page
per repository.

## 5. Status log

- **2026-09-25** The skill, renderer, build script and tests land with the
  Artifacts path. The renderer began as a hand-built page for a 66-file pull
  request in another repository, reviewed in eleven groups, then was split
  into the fixed renderer and the spec format.
