# Walkthrough spec

`project walkthrough init` writes the skeleton and `project site page` reads
it. The spec is JSON:

```json
{
  "version": 1,
  "name": "Card Name Check Walkthrough",
  "pr": {
    "repo": "owner/name",
    "number": 2062,
    "title": "The pull request title",
    "head": "full head commit SHA",
    "base": "full merge-base commit SHA",
    "baseRef": "main"
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
      "concepts": ["Idea the reviewer must hold while reading"],
      "checks": [
        {"kind": "verify", "text": "An invariant worth tracing"},
        {"kind": "flag", "text": "An observation that needs a decision"}
      ],
      "files": [
        {"path": "src/core/check.go", "note": "What to look at in this file"},
        {"path": "src/core/check_test.go", "collapsed": true}
      ]
    }
  ]
}
```

## Fields

- `name` is the page title: a short name for the change, not a sentence.
  It becomes the browser tab and gallery title.
- `pr.head` is the commit the page describes. `build` stops when the pull
  request has moved past it, unless `--at-head` asks for that head exactly.
- `pr.base` is the merge base the diff is taken from. `init` records it; the
  page shows the diff from `pr.base` to `pr.head`.
- `overview.summary` is what the pull request does, in a few paragraphs.
  The page adds the line counts and a legend itself.
- `overview.cards` lay out two per row. A card with an `id` also gets a
  link at the bottom of the sidebar.
- `groups[].id` must be unique; it is the section anchor and the key for the
  reader's "Reviewed" checkbox.
- `groups[].files[].collapsed` overrides the default, which collapses
  generated, test and documentation files.

## HTML in text fields

Every text field except `name`, `pr` and the file paths is inserted as HTML,
so write `<code>`, `<b>` and links directly and escape a literal `<` as
`&lt;`. `build` rebuilds that HTML from an allowlist before it reaches the
page, because a Pages site deploys whatever spec is on its ref: the tags
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
