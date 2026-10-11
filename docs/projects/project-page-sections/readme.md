---
status: ready
priority: now
---

# List a plan's sections and its nearby projects in the sidebar

A project page's sidebar lists the sections of the plan you are reading, as a
summary's sidebar lists its sections, and lets a nested project's page reach
its parent and sibling projects. The plan sets out the questions the two
raise, their options, and the owner's decisions, and § 2.9 draws the sidebar
those decisions make.

It builds on [`project-page-nav`](../project-page-nav/readme.md), which gives
each project page a summary's title row and sidebar: every stack of pull
requests that holds a segment of the project, each under its segments'
headers, then the project's own supplemental files and nested projects, and
the footer row. On the project's readme, the project's own headers are
current.

## 1. Problem

`project-page-nav` leaves two things out of the project page's sidebar, and
the user wants them designed together, because they share the sidebar's space
and its sense of where you are.

- **No list of the plan's sections.** A summary's sidebar lists its numbered
  sections, so a reader jumps to any of them. A plan has none. This
  repository's plans run to 500 to 800 lines at the long end, with 4 to 14
  `##` sections each, so a reader scrolls to find **4. Acceptance criteria**.
- **A nested project's sidebar no longer reaches its siblings.** Today's tree
  column lists the whole top-level project's folder on every page of it, so a
  sibling project is one click away. `project-page-nav` lists only the
  current project's own files and nested projects, so the reader goes through
  the breadcrumbs to the parent and then to the sibling.

## 2. Solution

Sections 2.1 to 2.8 each state a question, its options, and their
trade-offs, and close with the owner's decision. Section 2.9 puts the
decisions together as the sidebar after the change, § 2.10 says how the
list marks the section in view, and § 2.11 lists the files that change.

### 2.1 Where the headings come from

The browser has a plan's headings only once it renders the Markdown, after
`site.json` arrives and the plan's file loads. The sidebar draws before that.

| Option | How it works | Trade-off |
| --- | --- | --- |
| Read them after the render | `showDocument` fills the list from the rendered headings, whose ids `site.ts` already sets | No build change, and the links match by construction. `showDocument` already redraws the whole frame, sidebar included, when the Markdown arrives, because `frame` rewrites `root.innerHTML` in place of the `Loading…` page. The list arrives in that same redraw, so only the sidebar blocks below it move, at the moment the plan itself appears. |
| Extract them at build time | The build, which already reads every plan for `title_from_text` and for `search_index`, puts each plan's headings in `site.json` | The list draws on first paint. The build must find the same headings the browser renders, and the links must match the rendered ids (§ 2.2). |
| Both | The build's list draws first, and the rendered headings replace it | No jump, and the links always match in the end. Two sources for one list, and a redraw whenever they disagree. |

Extraction has to skip fenced code. The `agent-instructions` plan holds
`## Projector conventions` inside a fenced block, as an example of what
`project init` writes. Setext headings, a line of `-` under text, also render
as `<h2>`, and raw `<h2>` tags pass through.

The owner chose to read them after the render. `showDocument` hands the
rendered page's headings to the sidebar in the redraw that shows the page,
so the list appears with the plan. The build does not change. Fenced code,
setext headings, and raw `<h2>` tags raise no question, because the browser
lists exactly the headings it rendered.

### 2.2 Anchors that match the rendered plan

`renderMarkdown` gives every rendered heading without an id one from `slug`:
its text in lower case, with characters other than letters, digits, `_`,
spaces, and hyphens dropped, and each run of spaces made one hyphen. Links
into plans already use these ids, so a change to them breaks those links. Two
headings with the same text share one id today.

| Option | How the sidebar's link finds its heading | Trade-off |
| --- | --- | --- |
| The build mirrors `slug` | Python strips the heading's inline Markdown and slugs the result | Two copies of one rule. The strip has to match what the browser renders as the heading's text: code spans, emphasis, links, and entities. JavaScript's `\w` matches ASCII only, and Python's needs `re.ASCII` to agree. |
| The browser slugs the build's source | The build sends each heading's raw inline source. The sidebar renders it with `marked.parseInline` and DOMPurify, takes its text, and slugs it with the same `slug`. | One rule, and the text matches the rendered heading exactly. One inline render per heading on first paint, which is tens per plan. |
| The build assigns the ids | The build gives each heading an id, and the browser applies them to the rendered top-level `<h2>` elements in order | Links always land, and duplicates can get distinct ids. It depends on the build finding exactly the headings the browser renders, and it can change the id of an unusual heading, which breaks links to it. |

None is needed. The headings come from the rendered page (§ 2.1), so each
link takes the id that `slug` already gave its heading, and no id changes.
Two headings with the same text still share one id, and the list links both
to the first, as any link into the plan does.

### 2.3 Which headings to list

- **`##` only.** Every plan has them, as its numbered sections: 4 to 14 per
  plan here. The list stays short.
- **`##` and `###`.** Nine plans here have subsections, up to 8 each, so a
  plan's list could pass 20 rows. The subsections could indent under their
  section, or show only under the section you are in.

Plans number their own sections, as `2. Solution`. A summary's list puts a
number in a column of its own, the `nnum` span. The plan's number could move
into that column, leaving the title beside it, or the heading could show as
written with the number column empty. A section with no number needs a rule
either way.

The owner chose `##` only, and the plan's number moves into the `nnum`
column:

- **Which headings.** The `<h2>` elements at the top level of the rendered
  page. A heading inside a quote or a block of raw HTML stays out.
- **The number.** A heading is numbered when its text opens with digits and
  a dot, such as `2. Solution`, or with a dotted number such as `2.1`, and a
  space follows. The column shows the number without its last dot, `2`, and
  the title beside it reads `Solution`.
- **No number.** A heading such as `## Background` or `## 2026 roadmap`
  leaves the column empty, and its whole text is its title.
- **The title's markup.** The title keeps the heading's inline markup, such
  as a code span, and drops its links, because the row is itself a link.

### 2.4 Marking the current section

A summary page does not mark the section you are scrolled to. Its list marks
each section you have reviewed, with the check dot in its last column. A plan
has no reviewed state, so its list would draw no dots.

- **No current section.** It matches the summary page and adds no script.
- **Mark it as you scroll.** An `IntersectionObserver` watches the headings
  and highlights the section in view. The list is shared, so the summary page
  would gain it too, or the two lists would differ. A marked section carries
  `aria-current="location"`. `project-page-nav` says exactly one item in the
  sidebar carries `aria-current="page"`, and that rule would become one
  `page` and at most one `location`.

The owner chose to mark it as you scroll, on plan pages and summary pages
both, as § 2.10 describes. The sidebar holds exactly one item with
`aria-current="page"` and at most one with `aria-current="location"`.

### 2.5 Reusing the summary's section list

The summary's list is an `<ol>` of links, each holding the `nnum`, `ntitle`,
and `ncheck` spans, drawn inline in `renderPage` and styled by the `.nav ol`
rules in `site/assets/summary.css`. At 980 pixels and narrower, those rules
lay the list out as a grid of columns. A shared `sectionListHtml`, beside
`project-page-nav`'s `sideNavHtml`, would draw both lists from one copy: the
summary's with its check dots, the plan's without. Either source in § 2.1
feeds it.

`sectionListHtml(sections, checks, id)` in `site/src/summary.ts` draws both
lists as `<ol class="sections">`, with `id` on the list when it is given.
Each item is a link to its section around the `nnum` and `ntitle` spans, and
with `checks` the `ncheck` dot. Each section is a `PageSection`, a new type
in `site/src/globals.d.ts`: its `id`, its number as `num`, and its title as
markup. The summary page passes its groups, numbered from 1, with `checks`,
so its list draws the markup it draws today, plus the `sections` class and
the mark of § 2.10. A plan's list draws no dots, and its rows leave out the
dot's column, so the title takes its width.

### 2.6 What a nested project's sidebar lists

| Option | What the sidebar lists | Trade-off |
| --- | --- | --- |
| Its own pages | The current project's files and nested projects, as `project-page-nav` does | One project in the pages list. A stack that spans the current project and its parent or a sibling shows that project's header as a link, so the stacks reach a neighbor only when a stack spans the two. Any other sibling is two clicks away, through the breadcrumbs. |
| The whole top-level tree | The top-level project's folder, as today's tree column does, below the current project's stacks | Siblings are one click away. The title row and the stacks' current headers name the child while the tree starts at the parent, and on the parent's readme a project header and the tree's **Overview** entry would both be current. |
| Its own pages, and its neighbors | Its own pages, then a short block naming the parent and the siblings | Siblings are one click away, and the title row and the pages still agree. A third kind of block in the sidebar. |
| The whole tree, folded | The top-level tree, open along the path to the current project and closed elsewhere | Today's reach without today's length. The tree's own entry for the current project repeats the title row. |

The owner chose its own pages and its neighbors:

- **The neighbors block.** Below its pages, a nested project's sidebar
  names its parent project, linked, then the parent's other nested
  projects, each linked, as the docs tree draws a heading over its entries.
- **Parent and siblings.** A project's parent is the deepest other project
  whose folder holds it. Its siblings are the other projects with the same
  parent, so a sibling's own nested projects are not listed.
- **Not the current project.** The block leaves out the project you are on,
  because the sidebar's header already names it, and a sidebar holds one
  current page.
- **No block on a top-level project.** Its neighbors are every other
  top-level project. The Projects page lists them, and the breadcrumbs
  reach that page in one click, so a block would only repeat that page.

### 2.7 How the sections and the pages share the sidebar

- **Separate blocks.** The stacks, the sections, the pages, and the footer,
  in that order. The sections sit where a summary's sit, below its stacks. On
  a page that is not the readme, the sections are that page's.
- **Sections inside the tree.** The current page's entry in the pages list
  opens to show its sections, as many documentation sites do. Each page's
  sections stay with it. The readme has no entry in the pages list, because
  the project's headers link to it, so its sections need a place of their
  own, above the stacks or below them.
- **Length on a wide screen.** A few stacks, each with one or more headers,
  14 sections, and a tree can pass the window's height. The sidebar sticks
  and scrolls on its own, so a long sidebar works, but whichever block comes
  last is the one a reader scrolls to reach.
- **Length on a narrow screen.** At 980 pixels and narrower, `summary.css`
  makes the sidebar static, `project-page-nav` puts it before the plan, and
  the `.nav ol` rules lay a section list out as a grid of columns. Fourteen
  sections then push the plan further down the page, below the stacks and
  the pages.

Only one item may be the current page: on the readme, the first of the
project's headers, and on any other page, the file's entry. The current
section, if § 2.4 marks one, is a location within it.

The owner chose sections inside the tree:

- **The current page's entry opens.** It lists its sections beneath it. No
  other entry opens, because the browser has the headings of only the page
  it rendered.
- **The readme's sections get a block of their own.** The block sits below
  the stacks and above the pages.
- **The order.** The project's header and its stacks, the readme's
  sections on the readme, the pages with a supplemental page's sections
  under its entry, a nested project's neighbors, and the footer row.
- **Folded on a narrow screen.** At 980 pixels and narrower, the section
  list starts folded behind a **Sections** toggle, so the plan stays near
  the top of the page. The toggle is a button whose `aria-expanded` a tap
  flips, and the list shows while it reads `true`. A wider screen hides the
  toggle and shows the list whole.

### 2.8 Pages without sections

An HTML page in a project renders in a frame, so the site does not see its
headings, and its sidebar lists no sections. A supplemental Markdown page has
headings of its own, and § 2.7 decides whether its sidebar lists them.

A supplemental Markdown page lists its own `##` sections under its entry in
the pages, as § 2.7 decides. An HTML page lists none, and neither does a
page with no `##` heading, which draws no section block at all.

### 2.9 The sidebar after the change

On `incremental-review`'s readme, a top-level project with only its readme,
the sections follow the stacks. The bar marks the section in view.

```text
 ┌──────────────────────────────────┐
 │ Review a minimal push            │
 │ incrementally                    │
 │ #212  Plan incremental reviews   │
 │       of minimal pushes          │
 │ ──────────────────────────────── │
 │ 1   Problem                      │
 │ 2   Solution                     │
 │ 3   Why this design              │
 │▐4   Acceptance criteria          │
 │ 5   Cost                         │
 │ 6   Open questions               │
 │ 7   Background                   │
 │ 8   Implementation status        │
 │ ──────────────────────────────── │
 │ Generated by Projector       (⟲) │
 └──────────────────────────────────┘
```

On `alpha/beta/design.md`, a supplemental page of the nested project
`alpha/beta`, whose parent `alpha` also holds `alpha/gamma`, the page's
sections open under its entry, and the neighbors follow the pages:

```text
 ┌──────────────────────────────────┐
 │ Build beta                       │
 │ #9    Plan beta                  │
 │ ──────────────────────────────── │
 │ Beta design                      │
 │   1   Layout                     │
 │   2   Colors                     │
 │ ──────────────────────────────── │
 │ Build alpha                      │
 │   Build gamma                    │
 │ ──────────────────────────────── │
 │ Generated by Projector       (⟲) │
 └──────────────────────────────────┘
```

At 980 pixels and narrower, each list of sections starts folded:

```text
 │ #212  Plan incremental reviews   │
 │       of minimal pushes          │
 │ ──────────────────────────────── │
 │ ›  Sections                      │
 │ ──────────────────────────────── │
 │ Generated by Projector       (⟲) │
```

The pieces draw this way:

- **The sections block.** `<div class="navsections">` holds the toggle,
  `<button class="navfold" aria-expanded="false">`, and the list that
  `sectionListHtml` draws, with the id the toggle's `aria-controls` names.
  On the readme it follows the `.prblock`, and on a supplemental page it
  follows the page's entry in the pages.
- **The neighbors block.** `<div class="navnear">` holds the tree that
  `tree` draws, with the parent as its heading and the siblings as its
  entries, so a proposed sibling keeps its badge.
- **One current page.** The project's header on the readme, or the file's
  entry on another page, carries `aria-current="page"`. Neither new block
  holds a current page.

### 2.10 Marking the section in view

`watchSections(list)`, a shared helper in `site/src/summary.ts`, marks the
link to the section in view in a list of sections, and keeps the mark
current as the page scrolls. A summary page calls it once its page is
wired. A project page calls it each time `showDocument` draws a rendered
page, and `frame` stops the watch before it draws any view.

- **Targets.** Each link's target is the element its `#id` names: the
  `<h2>` on a plan page, and the `<section class="group">` on a summary
  page.
- **The rule.** The section in view is the last one whose top has scrolled
  above a line 30% of the way down the window. Above the first section, as
  in a plan's opening paragraphs, no section is marked.
- **The end of the page.** A short last section never reaches the line,
  because the page stops scrolling first. A zero-height marker that the
  watch puts after the sections' container says when the end of the
  content is in view. Then, on a page scrolled from its top, the line drops
  to the window's bottom, and the last section whose top shows is marked.
- **The observers.** One `IntersectionObserver` watches the targets with a
  root margin of `0px 0px -70% 0px`, so it reports each target that crosses
  the line or the window's top. A second watches the end marker against the
  whole window. Each report reads the targets' positions again and moves
  the mark. No scroll listener runs.
- **The mark.** The marked link carries `aria-current="location"`, and
  `summary.css` draws it as a row's hover band with an accent bar at its
  start. A browser without `IntersectionObserver` marks nothing.

### 2.11 Files that change

| File | Change |
| --- | --- |
| `site/src/summary.ts` | `sectionListHtml` and `watchSections` join the shared helpers. `renderPage` draws its list with `sectionListHtml`, and `wireSummary` calls `watchSections`. |
| `site/src/site.ts` | `sectionsOf` reads a rendered page's sections. `showPage` and `showDocument` take the sidebar as a function of the page's sections. `showDocument` calls it with the rendered page's sections and starts `watchSections`, and `frame` stops it. `projectNav` takes the sections and draws the readme's block, a supplemental page's sections through `tree`, and the neighbors through `parentOf`. `tree` takes markup to put under the current entry. The click handler flips the **Sections** toggle. |
| `site/src/globals.d.ts` | `PageSection` |
| `site/assets/summary.css` | The mark on the section in view |
| `site/assets/site.css` | The sections and neighbors blocks, the two columns of a plan's rows, and the toggle that folds the list at 980 pixels and narrower |
| `site/assets/summary.js`, `site/assets/site.js` | Rebuilt with `npm run build` in `site/` |
| `docs/cli.md` | The project page paragraph, and the sentence on a summary's sidebar |
| `tests/test_site.py` | The tests in § 5 |

## 3. Open questions

The owner answered each on 2026-10-10:

1. **Where the headings come from.** After the render (§ 2.1).
2. **How the links match the rendered ids.** They take the ids the render
   sets, so no change is needed (§ 2.2).
3. **Which levels to list, and how to show the plan's numbers.** `##` only,
   with the plan's number in the `nnum` column (§ 2.3).
4. **Whether to mark the current section as you scroll.** Yes, on plan
   pages and summary pages both (§ 2.4).
5. **What a nested project's sidebar lists.** Its own pages, then its
   parent and siblings (§ 2.6).
6. **How the sections and the pages share the sidebar.** Inside the tree,
   with the readme's own block, folded on a narrow screen. A supplemental
   page lists its own sections (§ 2.7 and § 2.8).

## 4. Background

- **Heading counts.** On 2026-10-10, on `main` with `project-page-nav`, this
  repository's plans held 4 to 14 `##` lines each, and eight held `###`
  subsections, up to 8 each. This plan makes nine. One `##` line, in
  `agent-instructions`, sits in a fenced code block.
- **Ids today.** `renderMarkdown` in `site/src/site.ts` sets
  `h.id = slug(h.textContent)` on each rendered heading that has no id.
- **The summary's list.** `renderPage` in `site/src/summary.ts` draws the
  sections as `<ol>` items with the `nnum`, `ntitle`, and `ncheck` spans, and
  no script on the page marks a section as current while you scroll.

## 5. Acceptance criteria

Page tests run under node against the compiled `site.js` and `summary.js`,
as `project-page-nav`'s do, with a stub DOM whose rendered Markdown is the
elements each test names. In every row, the sidebar's markup holds exactly
one `aria-current="page"` and no other `aria-current`, because only the
watch marks a location.

| Do | Expect |
| --- | --- |
| Draw alpha's readme, whose body holds `## 1. Problem`, `### 1.1 Detail`, `## 2. Solution`, and `## Background` | Between the `.prblock` and the pages, a `navsections` block whose list links `#1-problem`, `#2-solution`, and `#background`, with the numbers `1`, `2`, and none, and the titles `Problem`, `Solution`, and `Background` |
| Draw headings `## 10. Outcome`, `## 2.1 Detail`, and `## 2026 roadmap` | The numbers `10`, `2.1`, and none, and the last title keeps its year |
| Draw `## 3. <code>project init</code> writes` and a heading with a link | The first keeps its code span beside the number `3`, and the second keeps the link's text without the link |
| Draw a body whose `<h2>` sits inside a quote | No section block |
| Draw `alpha/notes`, whose body holds two `##` headings | No block after the `.prblock`. The list follows the notes entry, inside its item in the pages. |
| Draw `alpha/demo.html` | No section block |
| Draw `alpha/beta`'s readme with the siblings `alpha/gamma` and `alpha/beta/zeta` | After the pages, a `navnear` block whose heading links `/projects/alpha/` and whose one entry links `/projects/alpha/gamma/`. Zeta is in beta's pages and not in the block, and beta is in neither. |
| Draw alpha's readme | No `navnear` block |
| Draw alpha's readme with stacks, sections, and pages | The `.prblock`, the sections, the pages, and the footer row, in that order |
| Draw any page with sections | The toggle reads `aria-expanded="false"` and names the list in `aria-controls`, and the list follows it |
| Draw a summary page with two groups | Its list is `sectionListHtml`'s with check dots: the markup it drew before, with `class="sections"` on the `<ol>` |

Watch tests run `watchSections` under node with a stub
`IntersectionObserver` and a window 1,000 pixels tall:

| Do | Expect |
| --- | --- |
| Report targets at -500, 200, and 600 pixels | The second link alone carries `aria-current="location"` |
| Report targets at 400 and 900 pixels | No link carries it |
| Report the end marker in view on a page scrolled from its top | The last section whose top shows is marked |
| Report the end marker in view on a page at its top | The 30% line still decides |
| Watch a list, then another, then `null` | Each new watch disconnects the earlier observers and removes their end marker |
| Read the observers' options | The targets' root margin is `0px 0px -70% 0px` |

Style tests read the stylesheets:

| Do | Expect |
| --- | --- |
| Read `site.css` outside its media queries | `.nav .navfold` is hidden |
| Read its `max-width: 980px` query | It shows `.nav .navfold` and hides the list that follows a toggle whose `aria-expanded` is `false` |
| Read `summary.css` | `.nav ol a[aria-current="location"]` draws the mark |
| Read the sidebar's scoping rules | `project-page-nav`'s scoping tests pass unchanged |

Every new test fails before the change.

In a browser, the implementing pull request runs `project site serve` on
this repository and on a checkout with nested projects, in the light and
dark themes:

- **Scrolling a plan.** On a long plan, the mark moves from section to
  section as each heading passes the top third of the window, and a click
  on the last section marks it.
- **Scrolling a summary.** On a summary page, the mark follows the groups,
  and the check dots still mark the reviewed ones.
- **At 375 pixels.** The list starts folded, the toggle opens and closes it,
  and `document.documentElement.scrollWidth` equals `clientWidth`.
- **A nested project.** Its sidebar links its parent and its siblings.

The pull request's Testing section records these steps.

## 6. Cost

The page reads the rendered body's top-level elements once each time it
draws a page, tens of elements on a long plan. The watch adds two
observers and no scroll listener. Each report reads the position of every
target, 4 to 14 on a plan and one per group on a summary, only when a
target crosses the line or the window's top, or when the end of the content
comes into view or leaves it. The build does not change, and `site.json`
does not grow.
