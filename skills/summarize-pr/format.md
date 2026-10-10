# Summary format

`project summary init` writes the skeleton and `project site page` reads
it. The summary is JSON:

```json
{
  "version": 1,
  "name": "Card Name Check Summary",
  "pr": {
    "repo": "owner/name",
    "number": 2062,
    "title": "The pull request title",
    "head": "full head commit SHA",
    "headRef": "the pull request's head branch",
    "base": "full merge-base commit SHA",
    "baseRef": "main",
    "basePr": 2061
  },
  "overview": {
    "summary": ["<p>-level HTML paragraph", "..."],
    "cards": [
      {"id": "history", "title": "Review history so far", "html": "<ul class=\"tight\">...</ul>"}
    ]
  },
  "groups": [
    {
      "id": "core",
      "title": "The shared decision core",
      "kicker": "One line on what the group covers",
      "intro": ["Paragraph", "Paragraph"],
      "checks": [
        {"kind": "context", "text": "An idea that ties this group's files together"}
      ],
      "files": [
        {"path": "src/core/check.go", "note": "What to look at in this file",
         "checks": [
           {"kind": "verify", "text": "An invariant checked in this file, and how"},
           {"kind": "flag", "text": "An observation that needs a decision", "line": 42}
         ]},
        {"path": "src/core/check_test.go", "collapsed": true}
      ]
    }
  ]
}
```

## Fields

- `name` labels the summary where the site lists reviews: in the Reviews
  table and in a project's list of reviews. Make it a short name for the
  change, not a sentence. The page and its browser tab take the pull
  request's title, and use `name` only when that title is empty.
- `pr.head` is the commit the page describes. `build` stops when the pull
  request has moved past it, unless `--at-head` asks for that head exactly.
- `pr.base` is the merge base the diff is taken from. `init` records it; the
  page shows the diff from `pr.base` to `pr.head`.
- `pr.headRef`, and `pr.basePr` when the base branch is another open pull
  request's head, are what the site's review list uses to mark a stacked pull
  request. `init` records both; keep them when you edit a summary by hand.
  Each file's **File** link opens the file on `pr.headRef`, so it shows the
  current version. It opens the file at `pr.head` instead when a summary has
  no `headRef`, and when the site's last build found the pull request merged
  or closed, because its branch is usually deleted then.
- `overview.summary` is what the pull request does, in a few paragraphs.
  The page adds the line counts and the list of files itself.
- Each item of `overview.summary` and of a group's `intro` renders as a
  paragraph, except an item that starts with a block, `<ul>`, `<ol>`,
  `<table>`, `<div>`, `<pre>`, `<h3>`, `<h4>` or `<p>`, which renders as
  written. Give a list, table or heading an item of its own.
- `overview.cards` lay out two per row. A card with an `id` takes it as its
  anchor.
- `groups[].id` must be unique; it is the section anchor.
- `checks` are review notes, on a group or on one of its files. Each has a
  `kind`: `context` for what to hold in mind, `verify` for an invariant
  the author checked, or `flag` for something that needs a decision. The
  page shows a `verify` note as verified, so write one only for what you
  checked, and say how. A file's
  note may name a `line` its diff shows, on the new side unless `side` is
  `"old"`, and then renders under that line; `build` refuses a line the diff
  does not show. A group's notes take no `line`. A group's older `concepts`
  list still builds, as `context` notes.
- The reader marks each file Reviewed, which collapses it. Marking a group
  Reviewed closes the group and its files without marking any file, and a
  group is marked for the reader once all its files are. The reader can
  also check off each `flag` note; `context` and `verify` notes have no box.
- `groups[].files[].collapsed` overrides the default, which collapses
  generated, test and documentation files.
- A file is generated when the repository's `.gitattributes` marks it
  `linguist-generated`, as GitHub does when it collapses the file in a pull
  request's diff. `-linguist-generated` or `linguist-generated=false` keeps a
  file out. A file the attributes do not mention is generated when its path
  looks generated, such as `package-lock.json` or a file under `gen/`. The
  CLI reads the attributes at the head when the checkout it runs in has that
  commit, and from the working tree otherwise. `publish` fetches the head
  when the checkout lacks it and stores the head's attributes for the site.

## HTML in text fields

Every text field except `name`, `pr` and the file paths is inserted as HTML,
so write `<code>`, `<b>` and links directly and escape a literal `<` as
`&lt;`. `build` rebuilds that HTML from an allowlist before it reaches the
page, because a Pages site deploys whatever summary is on its ref: the tags
`a`, `b`, `br`, `code`, `div`, `em`, `h3`, `h4`, `i`, `li`, `ol`, `p`, `pre`,
`span`, `strong`, `table`, `tbody`, `td`, `th`, `thead`, `tr` and `ul`; the
classes below; and `href` only for `http`, `https` and `#` links. Anything
else, such as `style`, an event handler or a `<script>`, is dropped. These
classes are styled for cards:

| Class | Use |
| --- | --- |
| `tblwrap` around `table.tbl` | A table that scrolls on narrow screens |
| `td.r` | A bold result column |
| `note` on a `p` | Muted explanatory text |
| `tight` on a `ul` | A compact list |
| `count` on a `span` | A muted count after a label |
| `mono` on a `span` | Monospace text outside `<code>` |
| `pre` | A command block |
