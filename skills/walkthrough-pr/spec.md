# Walkthrough spec

`walkthrough.py init` writes the skeleton and `walkthrough.py build` reads
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
  request has moved past it.
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
`&lt;`. These classes are styled for cards:

| Class | Use |
| --- | --- |
| `tblwrap` around `table.tbl` | A table that scrolls on narrow screens |
| `td.r` | A bold result column |
| `note` on a `p` | Muted explanatory text |
| `tight` on a `ul` | A compact list |
| `count` on a `span` | A muted count after a label |
| `pre` | A command block |
