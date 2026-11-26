---
status: completed
priority: now
---

# Publish versioned artifacts on the Projector site

Use `/artifact` to create or revise an interactive page, save it on a hidden
Git ref, and browse it and its versions on the repository's site.

## 1. Problem

Projector hosts plans, documents, and pull request summaries on a repository's
GitHub Pages site. Summaries have a directory and earlier versions, and
publishing them keeps their content off ordinary branches.

There is no equivalent for an interactive explanation, chart, diagram, or
small tool. Committing HTML under `docs/` works but requires a source change
and pull request for every revision. A conversation artifact lives outside
the repository's site, directory, and access rules.

## 2. Solution

Add a shared `artifact` skill and `project artifact` commands. The
skill authors and previews a static HTML bundle. The CLI saves immutable
versions on `refs/projector/artifacts`, and the site adds a searchable
Artifacts directory with a latest link and a version picker. GitHub
Pages controls who can read the whole site.

### Author and publish

1. Resolve the repository from the request or checkout. Give a new
   artifact a stable repository-local ID, such as `cache-explorer`.
   Recover a published artifact's source before revising it, keeping
   its ID.
2. Create a dedicated working directory with `project artifact init`.
   Author `index.html` and its assets, listing the intended files in
   `artifact.json`. Compile framework source before publication;
   Pages serves the finished bundle without a server or build at view time.
3. Preview with `project site serve --artifacts WORKDIR`. This overlays
   the source bundle without a ref write. Exercise its controls at desktop
   and phone widths, and verify relative asset and data loads. Previewing
   never runs a bundle's build commands or changes `site.prepare`.
4. Run `project artifact publish --dir WORKDIR` directly. It validates
   the bundle, computes its version, and checks hosting through the existing
   Pages rules. Where hosting is ready, it appends the version and updates
   this ID's latest pointer in one commit and ordinary fast-forward push.
5. Dispatch the existing site workflow on its default branch using
   `workflow_dispatch`, the manual trigger generated workflows already have.
   This avoids adding a new repository event or rewriting existing workflows.
6. Return latest and fixed-version links. Report deployment as pending until
   its result is known. A dispatch failure reports that content was saved
   but deployment did not start, and names the workflow command to retry.

Where hosting is absent, cannot be checked, or would expose private content,
keep the version on the checkout's local `refs/projector/artifacts` and
push nothing. `--local` requests this behavior even with hosting. Print a
local preview route instead of a hosted URL. Setting up Pages or changing
its visibility requires a separate user request.

### Commands

Artifact commands accept `--checkout` and `--remote`, defaulting to the
current checkout and `origin`. IDs match `[a-z0-9]+(?:-[a-z0-9]+)*`, with at
most 80 characters. Reject a differently cased ID rather than normalizing it.

| Command | Behavior |
| --- | --- |
| `project artifact init ID --dir WORKDIR --title TITLE [--description TEXT]` | Create a source manifest and HTML starter. Refuse to overwrite existing files. |
| `project artifact init ID --dir WORKDIR --from-published [--version VERSION]` | Recover latest, or an exact full version ID, into a new working directory. |
| `project artifact list [--id ID] [--json]` | List IDs, latest titles and versions, and version counts. With an ID, list its versions and mark latest and local previews. |
| `project artifact publish --dir WORKDIR [--local] [--no-dispatch]` | Validate and save one version, updating latest only for new content. Deploy when hosted unless dispatch is disabled. |
| `project site serve --artifacts WORKDIR` | Overlay one source bundle on the published directory. Normal `site serve` includes local and remote saved versions. |
| `project site build --artifacts DIR` | Read an extracted directory of saved artifacts, as supplied by the site action. |

`--version` accepts a full digest, not an abbreviation or mutable branch name.
Source recovery and listing fetch first, with `--no-fetch` for offline use.
A failed fetch reports that the result is cached. Publication writes only
the requested bundle, never other local previews.

### Data and versions

The source manifest explicitly lists files, so publication cannot include
unrelated working files by accident.

| Source field | Value |
| --- | --- |
| `schema` | `1`; reject unsupported schemas. |
| `id` | The stable repository-local ID. |
| `title` | Nonempty plain text, at most 120 characters. |
| `description` | Plain text for the directory, at most 500 characters; empty is allowed. |
| `entry` | `index.html` for this format version. |
| `files` | Unique relative paths, including `index.html`; normalize their order before hashing. |

Read listed regular files as bytes. Reject absolute paths, traversal,
symlinks, hidden components, backslashes, control characters, and paths that
collide after case folding. Reject missing files, more than 1,000 files,
a file over 25 MiB, or a bundle over 50 MiB. Apply these checks to stored
records before extraction too, including file sizes from Git objects.

Create file records with `path`, `bytes`, and `sha256`, sorted by path.
The version is SHA-256 of canonical UTF-8 JSON
`{"manifest": <normalized source manifest>, "files": <file records>}`:
sorted object keys, compact separators, and unescaped Unicode. Metadata and
file bytes affect identity; filesystem and publication timestamps do not.
The version ID is the full lowercase digest.

```text
refs/projector/artifacts
└── artifacts/cache-explorer/
    ├── latest.json
    └── <version>/
        ├── artifact.json
        ├── revision.json
        └── files/
            ├── index.html
            └── cases.json
```

| Stored record | Contents |
| --- | --- |
| `artifact.json` | The normalized source manifest. |
| `revision.json` | `version`, `publishedAt` as a UTC timestamp, and the file records. Publication time is outside the hash. |
| `files/` | Exact listed bytes, without Git filters or line-ending conversion. Include editable source here when the page needs compilation. |
| `latest.json` | `{"version":"<full digest>"}`. |

Versions are immutable and retained without automatic deletion. Republishing
existing content returns its saved version, makes no new commit, and leaves
latest unchanged, including when that version is older. Recovering a saved
bundle reproduces its digest.

Reuse the publisher's five-attempt retry for competing ref updates, fetching
and rebuilding on the new tip. Stop immediately for authentication or
validation failures. Snapshot source bytes once so a retry cannot publish a
partly edited bundle. Local writes use compare-and-swap retries too.

Keep local and fetched remote refs separate. Local preview shows their union;
a local latest pointer wins for its ID and is marked as a preview. Remote
publication starts from the remote tip and includes only the requested
bundle. Concurrent publishes preserve both complete versions, with the last
successful new publication setting latest.

### Directory and page routes

All paths honor the existing site base, including a project site's prefix.

| Route | Page |
| --- | --- |
| `artifacts/` | One row per ID: latest title, description, update time, and version count. Filter by title, description, or ID; order by update time, then ID. |
| `artifacts/<id>/` | Latest version, with directory link and version picker. |
| `artifacts/<id>/<version>/` | Fixed version, publication time, abbreviated digest, and link to latest. |
| `content/artifacts/<id>/<version>/...` | Bundle files with their relative paths preserved. |

Add Artifacts to the common menu when any valid ID has a latest version.
Write real shell files for direct loads. Reuse the wide HTML frame and
full-screen controls. Every version loads its own complete asset directory.
Pass the wrapper URL's query and hash to the page, preserving both when
selecting a version and when navigating back.

Add an `artifacts` array to `site.json`, defaulting to empty when reading
older manifests. Each item has `id`, `latest`, and version records containing
`version`, `title`, `description`, `publishedAt`, and the entry URL. Escape
metadata as text. Search indexes only latest title, description, and visible
HTML text, excluding scripts and styles, and links to the latest route.

Fetch the artifact ref independently of summaries in the action and
local server. Missing means an empty directory. Skip a malformed version
with its ID, version, and reason reported, preserving valid neighbors. An
invalid latest pointer removes that ID from the directory with a diagnostic;
valid fixed-version routes remain. Do not guess another latest version.

### Access and skill behavior

Repository write access controls publication; GitHub Pages controls reading.
There is no Projector account or per-item sharing list. Hidden refs do not
hide content from repository readers. Require confirmed private Pages for
private or internal repositories, and preserve the deploy visibility check
for every version and asset.

HTML carries the same site-origin trust as existing HTML documents. Its
iframe is a layout boundary, not isolation, and files are also directly
reachable. Publication accepts content from an authorized repository writer;
reading an untrusted pull request does not authorize publishing its code.
The CLI never runs a bundle's scripts during publication or deployment.

Write `skills/artifact/SKILL.md` once for both hosts, using the skill-creator
workflow. It handles requests for an artifact or small interactive
explanation or tool, and later revisions. It chooses presentation and
controls for the question, with keyboard access, labeled controls, readable
contrast, and phone layouts. An explicit `/artifact` request uses configured
hosting by default; a request for local preview stays local. Recover saved
source for edits, respect repository publication policy and the audience of
source material, and return the resulting links or local route. Neither
host needs an artifact API or MCP server.

### Components

| Component | Change |
| --- | --- |
| `skills/artifact/SKILL.md` | Add authoring, recovery, preview, validation, and publication guidance. |
| `src/projector/artifact.py` | Own manifests, validation, content identity, and saved records. |
| `src/projector/summary.py` and a shared ref module | Extract hidden-ref fetch, temporary-index commits, and bounded push retries. Move summary callers without changing their schema, legacy migration, or publication behavior. |
| `src/projector/cli.py` | Register commands and preview/build inputs. Reuse checkout, repository, hosting, and visibility helpers, extending visibility handling for internal repositories if needed. |
| `src/projector/site/__init__.py` and `serve.py` | Read bundles, combine previews, copy files, emit routes and search data, and watch both ref families. |
| `actions/site/action.yml` | Fetch the artifact ref alongside summaries; let the build read validated Git objects directly. |
| `site/src/site.ts`, `site/src/globals.d.ts`, and `site/assets/site.css` | Add directory, routes, menu, version picker, and types. Reuse HTML layout and search; commit rebuilt JavaScript. |
| `tests/test_artifact.py`, `tests/test_site.py`, `tests/test_summary.py`, `tests/test_packaging.py` | Cover the boundaries below; add `artifact` to the exact published skill set. |
| `README.md` and `docs/site.md` | Document commands, links, preview, access, limits, and action-pin upgrades. Leave completed plans unchanged. |

## 3. Why this design

- **Name the thing you make.** Use `artifact` for the skill, commands, files,
  and directory. “Make an /artifact” fits the request, and the name covers
  interactive tools as well as visual explanations.
- **Extend the site.** Hidden refs, Pages access, HTML rendering, and search
  already exist. A separate artifact service adds another storage and access
  system to configure.
- **Use content identities and explicit latest pointers.** Concurrent writers
  cannot assign one version to different bytes, retries create no duplicates,
  and clocks cannot choose the wrong latest version.
- **Derive the directory.** Each ID owns its pointer and versions; publishers
  need no shared catalog to update.
- **Share mechanics, separate formats.** Summaries need pull request heads
  and diffs; artifacts need complete bundles. Extract Git mechanics
  rather than making either content schema serve both.

## 4. Acceptance criteria

| Exercise | Expected evidence |
| --- | --- |
| Preview a slider, relative JSON fetch, SVG, and binary asset at `/` and `/demo/`. | Controls work at desktop and phone widths; source recovery and copied assets preserve exact bytes. |
| Publish two revisions, then republish the first content unchanged. | One directory row, two immutable versions, and latest still points to the second. Changing metadata creates another version. |
| Recover latest and a full older version ID. | Each reproduces its digest; unknown IDs, wrong casing, and occupied output directories fail without overwriting files. |
| Race publishers for different IDs, then the same ID, both locally and remotely. | Both complete bundles survive; latest names a stored version; ordinary branches, the index, and dirty files are unchanged. |
| Reject a push, then separately fail dispatch after a successful push. | A rejected push changes no remote pointer. Dispatch failure distinguishes saved content from missing deployment and gives a usable retry command. |
| Publish without hosting, with unknown visibility, or with private or internal content and public Pages. | No remote write or dispatch; local preview works. A deploy with unknown or unsafe visibility fails before copying content. |
| Publish locally and fetch remotely while `site serve` runs. | Both changes appear without a restart; local previews are marked; summaries remain visible; offline preview works. |
| Direct-load both routes, change versions, and use browser back. | Static routes work, assets come from the selected version, and query and hash state reach the page. |
| Search for text changed across versions. | Latest appears once; older text and script-only text do not appear. |
| Supply traversal, symlinks, hidden files, case collisions, bad hashes, NUL-containing binary files, and oversized bundles. | Invalid input fails before a ref write or unsafe extraction; valid binary bytes survive; malformed stored versions leave valid neighbors usable. |
| Build sites with only artifacts and with none; install each plugin. | Navigation and search work; existing sections retain their behavior; both hosts discover the shared skill without MCP. |

Add regression tests that fail before implementation, pinning a changed JSON
value and binary payload through publication, recovery, version selection,
and site output. Use temporary Git repositories and bare remotes for mutation
tests. Inject competing pushes deterministically and assert the other
publisher's data and dirty checkout bytes survive. Existing summary race and
preview tests cover the shared-ref extraction. Check the browser controls,
relative fetches, picker, navigation, layout, and full-screen behavior.

## 5. Cost

Snapshot and hash a bundle once per publish, reusing it for retries. Git
shares unchanged blobs across versions, but each deploy includes every saved
version, so storage and deploy size grow with history. Report aggregate
artifact bytes during build; the per-bundle limits do not bound history.

Read each stored manifest and file once at build, with no per-version GitHub
API request. Load metadata for the directory and bundles only when opened.
Measure publish and build time, bytes read, and output size with 100 small
artifacts at five versions each and one near-limit bundle. Record the
measurements in the implementation hand-over. No backend service is added.

## 6. Rollout

1. Ship the skill, commands, ref extraction, site reader, and action together,
   preserving existing summary behavior through regression tests.
2. Repositories using `actions/site@v0` receive the reader with the compatible
   release. Exact release or commit pins need a reviewed action update before
   publishing. Projector's own action follows its default branch after merge.
3. When a test site is available, publish a harmless interactive example and
   record the deploy and version-link checks in the outcome. Local and action
   tests establish merge criteria without requiring live Pages or changing
   access settings.

## 7. Deferred scope

- **Independent sharing.** Per-item access and cross-repository directories
  need a separate project. This directory belongs to one repository's site.
- **History management.** Restore, rename, delete, and retention commands are
  deferred; this project preserves every version without rewriting history.
- **Application hosting.** Server code, credential storage, and collaborative
  editing are deferred. The bundle contains finished static output and any
  source needed to revise it.

No blocking question remains. These defaults are reviewed with the plan;
`ready` means executable scope, not approval to skip the plan review.

## 8. Background

- **Existing code.** `summary.publish` owns temporary-index commits and push
  retries; `summary.push_decision` and `require_private_site` check hosting.
  `site/serve.py` combines local and fetched refs. `site.ts` renders HTML in
  a wide frame. Extend these mechanisms without changing completed plans.
- **Pages access.** Private publication requires eligible organization
  repositories on Enterprise Cloud; a private repository alone does not
  make Pages private. See [GitHub's private Pages access rules](https://docs.github.com/en/enterprise-cloud%40latest/pages/getting-started-with-github-pages/about-privately-published-github-pages-sites).
- **Workflow dispatch.** The workflow must exist on the default branch.
  Generated Projector workflows already have the manual trigger. See
  [GitHub's workflow dispatch reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch).


## 9. Implementation and verification

This change ships the shared `artifact` skill, all commands and
routes in section 2, immutable content-based versions, source recovery,
concurrent local and remote publication, and the site's directory, search,
and version picker. Git publication mechanics live in `projector.refs` and
are shared with summaries. The site action reads validated blobs directly
from the fetched artifact ref instead of checking out an extracted bundle;
this avoids applying Git filters or materializing invalid paths before
validation.

Browser verification passes at `/` and `/demo/`, at 1440 × 900 and
375 × 812. `tests.artifact_ui.mjs` exercises directory filtering, direct
latest and fixed routes, version selection, browser back, query and live
hash state, keyboard controls, relative JSON and SVG loads, exact binary
bytes, search, and full-screen controls. The Latest link follows hash
changes inside the page. The browser regression failed on that navigation
before the fix and passes after it. The desktop and phone screenshots were
inspected, with no horizontal overflow or failed local requests.

A running-server regression verifies local publication, fetched remote
publication, and offline updates without a restart, while keeping review
summaries visible. Disabling the artifact ref watcher makes this regression
fail. Storage and shared-ref regressions cover the remaining boundaries,
including summary and relay callers. The original 18 regression tests were
also observed failing under their corresponding temporary mutations; the
source was restored after every check. Reproduction commands for the browser
check are in `docs/site.md`.

The cost fixture uses 100 artifacts with five versions each, then one
52,427,922-byte bundle. In a representative local benchmark, the 500
small publications took 88.561 seconds, the large publication took 0.676
seconds, and reading the saved history and building took 8.074 seconds.
Small publications read 79,000 source bytes; the large one reads its
52,427,922 payload bytes once. The output contains 53,612,930 bytes, including
all versions and shared site assets. `tests.artifact_benchmark` reproduces
the workload in a temporary repository and removes it after measurement.

The live publication check on November 26, 2026 saves two versions of the
public cache explorer fixture on `refs/projector/artifacts`.
[The first version](https://ninjudd.io/projector/artifacts/cache-explorer/b80bb2895a5d15e4023806f3cebb935c30f615b71d1c7df60237c5bc66a5d7fa/)
uses data value 17, and
[the latest version](https://ninjudd.io/projector/artifacts/cache-explorer/7b974bb720128a02e88a04036e65ba83c1e8b624ba96b0267411b7ca208ab598/)
uses 23. Recovery reproduces the first version's content identity and exact
binary bytes. Republishing it creates no version and leaves latest on the
second. Repository settings and Pages visibility stay unchanged.

[The first site run](https://github.com/ninjudd/projector/actions/runs/45785861460)
builds both versions and uploads the site, but GitHub's deployment step
cannot find its upload. The upload is visible through the API after the
failure. [One deployment retry](https://github.com/ninjudd/projector/actions/runs/45786488192)
fails at the same step after uploading successfully. At that point, the
public manifest contains no artifacts, so hosted browser verification waits
for deployment. Neither run changes Pages settings or access rules. The
storage, dispatch failure, existing-workflow selection,
remote-default-branch, and visibility boundaries also have local regression
coverage.

A later check on November 26 confirms that the public site serves both
versions. Browser verification passes against
[the live artifact](https://ninjudd.io/projector/artifacts/cache-explorer/)
at 1440 × 900 and 375 × 812, including direct latest and fixed links,
version selection, browser back, query and live hash state, keyboard
controls, relative JSON and SVG loads, exact binary bytes, search, and
full-screen controls. Both screenshots are inspected, with no horizontal
overflow or failed local requests. The deferred live check is complete.
