---
status: ready
priority: next
---

# Expand the hidden lines around a summary's diff hunks

A reader of a summary page can reveal the unchanged lines above, between, and
below each hunk of a changed file, as GitHub's diff view allows.

## 1. Problem

A summary page shows each changed file as its diff hunks: the changed lines
and the three unchanged lines of context Git puts around them. The page has
nothing else of the file. When a reader needs the function a hunk sits in, or
the lines between two nearby hunks, they leave the page for GitHub's Files tab
or the file on the branch, and lose their place in the summary's reading order.

The page cannot fetch the file from GitHub itself. A private repository's
site has no token to fetch with, and the summary describes one head, which the
branch may have moved past. The file content has to travel with the summary.

## 2. Solution

`project summary publish` records the head's version of each changed file
beside the summary, the site build serves each such file once and tells each
page where its hidden gaps are, and the page fetches a file the first time a
reader expands one of its gaps.

### 2.1 Publish records the head's files

`summary publish` already writes `summary.json`, `diff.patch`, and
`attributes.json` under `summaries/<number>/<head>/` on
`refs/projector/summaries`, and already fetches the head when the checkout
lacks it (`publish_attributes` in `src/projector/summary.py`). It now also
writes `summaries/<number>/<head>/head/<path>` for each eligible file: an index
entry that points at the head tree's existing blob, so the summaries ref gains
tree entries and no new file content.

A file is eligible when all of these hold:

- Its diff has at least one hunk, so it is text and has something to expand.
- It is neither new nor deleted. The diff of a new or deleted file already
  shows every line.
- Its head tree entry is a regular file, mode `100644` or `100755`. A symlink
  or a submodule is skipped.
- Its blob is at most `CONTEXT_MAX_BYTES`, 1 MiB.
- It is not a `.gitattributes` file. The deploy checks out the summaries ref
  with `git worktree add`, and `project site serve` reads it with
  `git archive`. Both apply a stored `.gitattributes` to the files stored
  beside it, where `eol`, `working-tree-encoding`, and `ident` change their
  bytes and `export-ignore` drops them from the archive.

When the head cannot be read, publish prints why on stderr, as it does for
attributes, and writes no `head/` entries. A republish of the same head with an
unreadable head keeps the `head/` entries an earlier publish wrote.

### 2.2 The build serves each file once and computes the gaps

`build_summaries` in `src/projector/site/__init__.py` reads
`<number>/<head>/head/<path>` for each file of the page's diff. For each file
it finds, it:

1. Decodes the bytes as UTF-8 with replacement, so a file that is not UTF-8
   cannot stop the build. It replaces each `\r\n` with `\n` and splits the
   text into lines on `\n`, dropping the empty string after a final newline.
   The replacement matches the diff, which `gh` reads from the compare API and
   `build_summaries` reads from `diff.patch` in Python's text mode. Text mode
   turns `\r\n` into `\n`, so without the replacement every line of a CRLF
   file would fail step 2.
2. Checks every context and added line of every hunk against the line with the
   same new-side number. On any mismatch it drops the file's context and prints
   a `::warning::` naming the file, so a wrong blob never shows wrong lines.
3. Writes the decoded text, with each `\r\n` replaced, once as UTF-8 to
   `reviews/blobs/<id>.txt` in the site, where `<id>` is the Git blob id of the
   stored bytes. The page splits that text on `\n`, so it shows exactly the
   lines the build checked. Two heads or two pull requests with the same file
   content share one file.
4. Computes the file's gaps and adds `context` to the file in the page data.

The page data gains, on each file that has stored content:

```json
"context": {
  "url": "<base>reviews/blobs/<id>.txt",
  "lines": 412,
  "gaps": [
    {"before": 0, "start": 1, "end": 40, "oldStart": 1},
    {"before": 1, "start": 47, "end": 120, "oldStart": 45},
    {"before": 2, "start": 131, "end": 412, "oldStart": 127}
  ]
}
```

`url` starts with the site's base path, the `base` that `build_summaries`
already puts in front of every address it writes, such as
`{base}reviews/{number}/{head}/data.json`. GitHub Pages serves a project site
under `/<repo>/`, where `/reviews/blobs/<id>.txt` without the base is a 404.
The page fetches `url` as given.

Each gap is a run of hidden head lines, `start` to `end` inclusive on the new
side. `before` is the index of the hunk the gap sits above, and equals the
number of hunks for the gap below the last hunk. `oldStart` is the old-side
number of `start`; the gap's lines are unchanged, so the old number of new line
`n` is `n - start + oldStart`. A gap with no lines is omitted.

`parse_diff` keeps each hunk header's four numbers, so the build computes gaps
without parsing headers again: `{"oldStart", "oldLines", "newStart",
"newLines"}` join `header` and `lines` on each hunk. A hunk's new side starts
at `newStart`, or at `newStart + 1` when `newLines` is 0, because a hunk with no
new lines sits after line `newStart`. The old side follows the same rule.

### 2.3 The page expands a gap on request

`renderFile` in `site/src/summary.ts` draws an expander row in each gap's
place: above the first hunk's table, between two tables, or below the last.
The row offers:

- **Expand up**, which reveals the 20 lines just above the following hunk,
  on every gap except the one below the last hunk.
- **Expand down**, which reveals the 20 lines just below the preceding hunk,
  on every gap except the one above the first hunk.
- **Expand all**, which reveals the rest of the gap. When 20 or fewer lines
  remain, it is the only control.

The first click on any gap of a file fetches `context.url` once, keyed by URL so
two files with the same content share one request, and highlights the whole
text once with the page's `hl` and `splitLines`. Revealed lines are context
rows built by the same `numbers` function as the diff's own rows, so they show
both line numbers in the file's column layout. Expand down appends rows to the
preceding hunk's table; expand up inserts them into the following hunk's table
above its `tr.hunk` header row. When a gap closes, the page removes its
expander and the following hunk's header row and moves that table's rows into
the preceding table, so the hunks read as one. After each insertion the page
runs `markTokens` on the file, so a marked name is marked in the new lines too.

When the fetch fails, the expander says the file could not be loaded and its
controls stay usable for a retry. A file without `context` draws no expander.

### 2.4 Files that change

| File | Change |
| --- | --- |
| `src/projector/summary.py` | `parse_diff` keeps the hunk numbers. A shared helper makes the head readable, fetching it once, for both `publish_attributes` and the new head-file listing. `summary_tree` takes index entries by blob id. `CONTEXT_MAX_BYTES`. |
| `src/projector/site/__init__.py` | `build_summaries` reads `head/`, checks and writes the blobs, and adds `context`. |
| `site/src/summary.ts`, `site/src/globals.d.ts`, `site/assets/summary.css` | Expander rows, the lazy fetch, row insertion, and the `context` and hunk-number types. |
| `site/assets/summary.js` | Rebuilt with `npm --prefix site run build`. |
| `docs/cli.md`, `skills/summarize-pr/SKILL.md` | What publish stores and what a page can expand. |
| `tests/test_summary.py`, `tests/test_site.py` | The tests in § 4. |

## 3. Why this design

- **Store the head's files by reference at publish.** Publish is the one step
  that has the head: the deploy checks out only the default branch, and
  publish already fetches the head for its attributes. Pointing index
  entries at existing blobs costs the ref no new content. Rejected: fetching
  from GitHub in the page, which a private site cannot do and which follows the
  branch rather than the summarized head; and having the deploy fetch every
  head, which spends a fetch per pull request on every deploy and fails for a
  head GitHub has dropped.
- **Compute gaps in the build, not the page.** The arithmetic around empty
  hunks and file edges is where this breaks. The Python suite always runs it,
  while the tests that run the page's script under node skip where node is
  missing. The page only draws what the data says.
- **Check the blob against the hunks.** Every context and added line of the
  diff must equal the stored line at its number. That catches a wrong or
  missing blob before a reader sees shifted lines, at the cost of one pass over
  lines the build already parsed.
- **Store no `.gitattributes`.** Without one, no attribute on the summaries
  ref can change a stored file's bytes or drop it from an archive, and the only
  cost is that a changed `.gitattributes` has no context to expand. Rejected:
  reading each stored file from the ref with `git cat-file`, which needs the
  ref where `build_summaries` takes a directory.
- **Serve each blob once by its Git id.** Successive heads of one pull request
  share most of their files, so keying by content keeps the site from growing
  with every push. Using Git's own id makes a served file traceable to the
  object it came from.
- **Fetch lazily, per file.** Most readers expand nothing, so `data.json` gains
  only a URL, a count, and gaps per file, and a page loads no file content
  until asked.
- **Reuse what the page and publish already have.** The page's `hl`,
  `splitLines`, `numbers`, `markTokens`, and `esc` render the new rows;
  publish's head fetch and `summary_tree` write the entries. The head fetch
  moves out of `publish_attributes` into a helper both callers use, so a
  publish fetches the head at most once.
- **Twenty lines a click, as GitHub does.** Readers already know the control.

## 4. Acceptance criteria

1. Publish tests, in temporary Git repositories, prove that `head/` entries
   point at the head's blobs and add no new objects; that new, deleted,
   symlinked, submodule, oversized, and `.gitattributes` files get none; that
   a head fetched from the remote works; and that an unreadable head writes
   none and says why.
2. Gap tests prove the computed gaps and `oldStart` for: a first hunk at line 1
   (no gap above), two adjacent hunks (no gap between), a pure-addition hunk, a
   pure-deletion hunk (`newLines` 0), a last hunk that reaches the end of the
   file (no gap below), a file whose last line has no newline, and CRLF lines.
3. A build test, which builds with the base `/projector/`, proves that a blob
   whose lines disagree with the diff gives the file no `context` and prints
   the warning; that a CRLF file keeps its `context`, and its served text has
   no `\r` before a newline; that `context.url` is
   `/projector/reviews/blobs/<id>.txt`; and that two heads with the same file
   content write one `reviews/blobs/<id>.txt`.
4. A page test, run under node against the compiled renderer as the site tests
   already do, proves that a file with `context` draws one expander per gap with
   the controls § 2.3 lists, and a file without `context` draws none.
5. In a browser, on a summary served by
   `project site serve --base /projector/`, a reviewer checks: each control
   reveals the right lines with the right numbers on both sides; a closed gap
   leaves no header row or seam; revealed lines are highlighted; a failed
   fetch shows the message; a summary published before this change shows no
   expanders. The pull request's Testing section records
   these steps.
6. No revealed line is inserted as raw HTML: the page passes each line through
   `hl` or `esc`. A test gives a file a line holding `<script>` and checks the
   rendered text.

## 5. Cost

- **Publish.** One `git ls-tree -r -z` of the head and one
  `git cat-file --batch-check` for the eligible paths, after the head fetch
  publish already does. The pushed objects are tree entries.
- **Deploy.** The build reads each stored file once, checks it against lines it
  already parsed, and writes each distinct blob once. The cap bounds a single
  file at 1 MiB. The build prints the number and total size of the blobs it
  writes, and the implementing pull request reports both for this repository's
  summaries ref.
- **Page.** `data.json` grows by a few numbers per file. The first expand of a
  file fetches it once and highlights it once. Measure, in Chrome with
  `performance.now()`, expanding all of a 5,000-line file, and keep it under
  200 ms on a laptop.

## 6. Rollout

1. Land the data: § 2.1 and § 2.2, with criteria 1 to 3. Pages look the same,
   because nothing draws `context` yet.
2. Land the page: § 2.3, with criteria 4 to 6.

A summary published before step 1 has no `head/` files and shows no expanders.
Its next publish, which the review loop makes for every reviewed head, adds
them.

## 7. Deferred

- **The standalone page.** `project site page` writes one page with its data
  embedded and no site to serve blobs from, so it shows no expanders.
  Embedding file content in that page can follow if someone needs it.
- **Highlighting a hunk from the whole file.** Hunks stay highlighted as they
  are, one hunk at a time, so a token that opens before a hunk can still color
  it wrongly. The stored file would let a later change fix that.
- **Pruning the summaries ref.** Every published head keeps its entries, as its
  summary and diff do today.
