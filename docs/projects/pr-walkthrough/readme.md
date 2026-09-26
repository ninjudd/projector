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
  outside the diff, or describes a head the pull request has moved past.
- The page works from disk in any browser and publishes unchanged as a Claude
  Artifact, with the renderer passed as supporting files.
- The build script uses only the Python standard library and the `gh` CLI,
  and the renderer loads remote scripts only from cdnjs.
- A GitHub Pages publisher serves each repository's walkthroughs to the people
  who can read that repository, from specs committed to a branch (section 4).

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

A repository hosts its own walkthroughs. Specs, not built pages, live on an
orphan `projector-pages` branch at `walkthroughs/<number>/<head>/spec.json`.
`walkthrough.py publish` commits a spec there through Git plumbing, so it
never touches the checkout, and on first use creates the branch with a
workflow file. A push to the branch runs that workflow, which calls
Projector's composite action, `ninjudd/projector/actions/walkthroughs`. The
action runs `walkthrough.py site`, which rebuilds every spec at its recorded
head (`build --at-head`, the diff from the compare API for `pr.base` and
`pr.head`), writes an index, points `/<number>/` at each pull request's newest
head, and deploys the result to Pages.

Committing specs rather than built pages keeps the branch small, because
the diff comes from GitHub at build time, and makes the spec, the part that
needs judgment, the only thing an agent writes. The spec on the branch is also
the durable copy a later session starts from when the pull request moves.

The shared piece is a composite action rather than a reusable workflow
because a composite action is downloaded at the ref the caller names, with
the renderer beside it, while a reusable workflow cannot tell which ref it was
called at. Each repository's workflow names the ref, and that choice decides
what code runs with a token that can read the repository:

- `@v1`, a moving major-version tag, is the default. Repositories get fixes
  from deliberate releases, never from an unreviewed commit. The spec's
  `version` is the compatibility contract: every `v1` renderer accepts every
  version 1 spec, and a breaking change becomes `v2`.
- A full commit SHA locks the renderer for organizations that want it.
- `@main` is rejected: any commit to a public repository would run inside
  every adopting repository with read access to its code.

Setup is one-time and needs admin rights: set the Pages source to GitHub
Actions, and allow `projector-pages` in the `github-pages` environment's
deployment branches, which allow only the default branch until told
otherwise. For a private repository the site must be private, which needs
GitHub Enterprise Cloud; the skill stops if GitHub refuses. The built renderer
ships inside the site, so a viewer's browser only runs the version that built
the page.

Other stores do not fit. Actions artifacts expire and need a login to fetch,
Pages cannot read Git notes, secret gists are unlisted rather than private,
and a pull request comment holds 65,536 characters while a large diff runs
to megabytes.

Projector's own repository dogfoods the design: its site serves a
walkthrough of #62. It calls the action at the `walkthrough-pages` branch
until the first `v1` release exists.

Open: cutting the `v1` tag with the first release and moving Projector's own
workflow onto it, and squashing the branch once its history grows large.

## 5. Status log

- **2026-09-25** The skill, renderer, build script and tests land with the
  Artifacts path. The renderer began as a hand-built page for a 66-file pull
  request in another repository, reviewed in eleven groups, then was split
  into the fixed renderer and the spec format.
- **2026-09-26** The Pages publisher lands (section 4): `publish`, `site`,
  `build --at-head`, the composite action, and Projector's own site with a
  walkthrough of #62. The shared piece became a composite action instead of
  the reusable workflow first proposed, so the ref a repository names also
  picks the renderer. Section 6 records browsing `docs/projects` on the same
  site as later work.

## 6. Browsing projects on the same site

The same Pages site should become a way to browse `docs/projects`
interactively: each plan with its status, priority, nested projects and
sections, beside the walkthroughs of the pull requests that implement it.
This is a later pull request. The site layout leaves room for it:
walkthroughs sit under their own paths, and the root index can grow a
projects view.
