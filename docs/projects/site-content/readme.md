---
status: completed
priority: now
---

# Connect and search the site's content

## 1. Outcome

The Projector site deferred three things when it shipped (`site` section 5).
This project delivers them: a plan and the walkthroughs of the pull requests
that implement it link to each other, a reader can search every document the
site serves, and images and other files under `docs/` load from the site
rather than from GitHub.

## 2. Acceptance criteria

- A walkthrough links to every project whose plan files its diff changes,
  and to any project its spec names in an optional `projects` list; each
  plan page lists the walkthroughs that link to it, and the PRs view shows
  each walkthrough's plans.
- The site header carries a search box on every page, walkthroughs included,
  and `search/` ranks the README, docs, and plans that contain every word of
  the query, with a snippet around the first match.
- Every non-Markdown file under `docs/` is copied into the site, and a
  relative image or link to one resolves to the site's copy.
- None of it asks GitHub for anything at deploy or view time.

## 3. Design

**Links come from the diff the walkthrough already stores.** The site build
has each walkthrough's changed files. A file under a project's directory
links the walkthrough to the deepest project that contains it, so a change to
a nested plan links the nested project rather than its parent. A spec may
also list projects by name for a pull request that implements a plan without
editing it; names that match no project are dropped. Only the newest version
of a walkthrough decides its links. The build writes the links into each
version's `data.json`, which the walkthrough page renders beside its
versions, and into `site.json` in both directions.

**Search is a static index and a page.** The build writes `search.json`: each
served document's site path, title, kind, and its text with frontmatter,
link targets, and Markdown punctuation removed. The search page fetches it
on first use and keeps a document only when every query word appears in its
title or text, ranking title matches above text matches and more matches
above fewer. The header's search form is a plain GET to `search/`, so it
works on walkthrough pages, which do not run the site's router, and renders
in place everywhere else. A repository's documentation is small enough that
a whole-text index loads quickly; a repository whose docs outgrow that can
move to a per-term index without changing the page.

**Files are copied, not linked.** A relative image in a document used to
resolve to GitHub's raw file, which a reader without access to a private
repository cannot load, so a private site showed broken images. The build now
copies every file under `docs/` that is not Markdown and not hidden, skipping
any larger than 20 MB with a warning, and lists them in `site.json`; the page
points a relative image or link at the site's copy when one exists and at
GitHub otherwise. Files outside `docs/`, such as an image the README links
from the repository root, still resolve to GitHub.

## 4. Outcome

Shipped. Every criterion in section 2 holds, tested in `tests/test_site.py`
and checked in a browser against a build of this repository and its
published walkthroughs: #62's walkthrough, whose diff edits the
`pr-walkthrough` plan, lists it under Plans, and that plan's page lists #62
under Pull requests; a search for "hidden ref" from the header ranks the
walkthrough plan, the CLI guide, and the README with the phrase highlighted;
and an image under `docs/` loads from the site's copy. Building from a
checkout inside a hidden directory, as a worktree under `.claude/` is, first
skipped every file under `docs/` because the hidden-path check read the
absolute path; it reads the path inside the repository, and a test builds
from such a checkout.
