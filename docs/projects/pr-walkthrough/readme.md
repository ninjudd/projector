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
  who can read that repository, from specs on a hidden ref (section 4).

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

A repository hosts its own walkthroughs. Specs, not built pages, live on the
hidden ref `refs/projector/walkthroughs` at
`walkthroughs/<number>/<head>/spec.json`. `walkthrough.py publish` commits a
spec there through Git plumbing, so it never touches the checkout, and then
sends a `repository_dispatch` event. A workflow on the default branch, added
once with `walkthrough.py workflow --write`, answers that event by calling
Projector's composite action, `ninjudd/projector/actions/walkthroughs`. The
action fetches the ref, runs `walkthrough.py site`, which rebuilds every spec
at its recorded head (`build --at-head`, the diff from the compare API for
`pr.base` and `pr.head`), writes an index, points `/<number>/` at each pull
request's newest head, and deploys the result to Pages.

A hidden ref rather than a branch, because a branch intrudes on the
repository's users: every push to one makes GitHub offer "had recent pushes,
compare and pull request" to its pusher, and the branch shows up in branch
lists and in every clone. A ref outside `refs/heads` and `refs/tags` does
none of that; a probe on Projector's repository confirmed GitHub accepts it,
lists no branch for it, and a default clone does not fetch it. The cost is
that a push to it triggers no workflow, hence the explicit dispatch, and that
GitHub runs dispatched workflows only from the default branch, hence the one
workflow file there. That file is a reviewed part of the repository rather
than something that appears on a side branch, and because the workflow runs
from the default branch, the `github-pages` environment's default rule
already allows it to deploy.

Committing specs rather than built pages keeps the ref small, because the
diff comes from GitHub at build time, and makes the spec, the part that needs
judgment, the only thing an agent writes. The spec on the ref is also the
durable copy a later session starts from when the pull request moves.
Section 8 changes the first half: the diff now sits on the ref beside its
spec, so a deploy no longer asks GitHub for it.

The shared piece is a composite action rather than a reusable workflow
because a composite action is downloaded at the ref the caller names, with
the renderer beside it, while a reusable workflow cannot tell which ref it was
called at. Each repository's workflow names the ref, and that choice decides
what code runs with a token that can read the repository:

- `@v0`, the moving major-version tag of Projector's one version, is the
  default. Repositories get fixes from deliberate releases, never from an
  unreviewed commit. The spec's `version` is the compatibility contract:
  every renderer on a major tag accepts every spec of the version it
  shipped with.
- A full commit SHA locks the renderer for organizations that want it, as
  GitHub's security hardening guide recommends; Dependabot keeps such pins
  current.
- `@main` is rejected. With it, the next publish in every adopting
  repository would run whatever is on Projector's `main` at that moment, with
  read access to that repository's code, so a half-finished or compromised
  commit would reach every adopter without anyone releasing it.

Nothing runs when Projector changes. A repository's workflow runs only when
that repository publishes a spec, so a release reaches each site on that
site's next publish, which rebuilds all of its walkthroughs with the new
renderer, and a bad release breaks no live site until then. Releases follow
`docs/plugins.md`: an immutable `vX.Y.Z` tag per release and a major tag that
moves to it.

Setup is once and needs admin rights: enable Pages with the GitHub Actions
source and merge the workflow file. For a private repository the site must be
private, which needs GitHub Enterprise Cloud; the skill stops if GitHub
refuses. The built renderer ships inside the site, so a viewer's browser only
runs the version that built the page.

Other stores do not fit. Actions artifacts expire and need a login to fetch,
Pages cannot read Git notes, secret gists are unlisted rather than private,
and a pull request comment holds 65,536 characters while a large diff runs
to megabytes.

Projector's own repository dogfoods the design. Its workflow file lands with
this change, pinned to `@v0`, and runs once the first `v0` tag exists. Until
then its site serves a walkthrough of #62 deployed by the earlier
branch-based design, whose `projector-pages` branch is deleted once the hidden
ref deploys.

Open: squashing the ref's history once it grows large.

## 5. Status log

- **2026-09-25** The skill, renderer, build script and tests land with the
  Artifacts path. The renderer began as a hand-built page for a 66-file pull
  request in another repository, reviewed in eleven groups, then was split
  into the fixed renderer and the spec format.
- **2026-09-26** The Pages publisher lands (section 4): `publish`, `site`,
  `build --at-head`, the composite action, and Projector's own site with a
  walkthrough of #62. The specs first lived on a `projector-pages` branch;
  GitHub's "had recent pushes" banner for it moved them to the hidden ref
  `refs/projector/walkthroughs`, with a dispatched workflow on the default
  branch. The shared piece became a composite action instead of
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

## 7. The site belongs to Projector, not to the skill

The Pages site is Projector's, and it outgrows walkthroughs as soon as
browsing `docs/projects` (section 6) joins it, so it cannot live inside the
`walkthrough-pr` skill. The skill writes the data, a spec, and nothing else.
The CLI owns the rest in two modules: `projector.walkthrough` for the spec
itself (skeletons, the diff it is checked against, sanitizing, publishing to
the hidden ref, and whether a repository hosts a site), and `projector.site`
for turning content into pages (the renderer, the index, the redirects, and
the favicon shared with Projector's homepage). The commands are `project
walkthrough init|publish` and `project site page|build|status|workflow`.

The composite action keeps its path, `actions/walkthroughs`, so the workflow
files repositories have already merged keep working; it runs the CLI from its
own checkout, `python3 -m projector site build`, without installing it, which
sidesteps a runner's system Python refusing a package install. The site's
package owns its assets as package data, so an installed CLI renders the same
page the action deploys. The projects view extends `projector.site` and reads
plans through the CLI's own project model rather than parsing them again.

## 8. Publish the data once and let the page load it

A deploy used to rebuild every walkthrough from scratch: fetch each spec's
diff from GitHub, parse it, check the spec against it, sanitize, and embed
the result in each page. That work grows with every walkthrough a repository
publishes, and each deploy spends one GitHub request per spec. Now
`project walkthrough publish` fetches the diff once and commits it beside the
spec as `diff.patch`, so a deploy asks GitHub for nothing. The site build
still parses, checks, and sanitizes each stored diff, because anyone who can
push to the hidden ref can write it, and that work is local and fast. Each
site page is a shell that loads its `data.json` when it opens; `project site
page` still embeds its data, since a browser will not fetch a file beside a
page opened from disk.

Serving data from GitHub at view time, with no deploy at all, was rejected: a
browser cannot read a private repository without the viewer's token, which
would give up section 3's private-site case, and unauthenticated reads are
rate limited per visitor. Specs published before diffs were stored still
build, by fetching their diff as before, until they are republished.
