---
status: completed
priority: next
---

# Write the site's scripts in TypeScript

## 1. Outcome

The site's two scripts, `site.js` and `walkthrough.js`, are written in
TypeScript and type-checked in strict mode. The site, every repository's
deploy, and every CLI install serve the same JavaScript as before, compiled
from that TypeScript.

## 2. Acceptance criteria

- The sources live in `src/projector/site/ts/` and `npm run build` compiles
  them to `src/projector/site/assets/`, one JavaScript file per source, with
  no bundler and no module loader.
- The compiled files are committed, and a test fails when they differ from
  what the TypeScript compiles to.
- CI runs the test suite with the compiler installed, so a stale compiled file
  cannot reach `main`.
- The Python CLI still has no runtime dependencies, and `site build` and
  `site serve` need no Node.

## 3. Where the compiled JavaScript lives

Every consumer reads the scripts from a Git tree at some ref: the composite
action checks out `@v0` or `@main` and runs `site build` from it, and pipx and
the plugin install from a commit. So the JavaScript must be in the tree at
every ref anyone uses.

**Committed beside the TypeScript, chosen.** Every ref works everywhere with
no extra machinery, offline, and with no Node at run time. `.gitattributes`
marks the two files `linguist-generated`, so GitHub collapses them in pull
request diffs. `CompiledScriptTests` compiles the TypeScript and compares the
result with the committed files; it skips where `npm ci` has not run, and the
new `Check` workflow always runs it. A conflict in a compiled file is resolved
by taking either side and running `npm run build`, and the freshness test
catches a wrong resolution. Compiling each source to its own file keeps those
conflicts to changes that would conflict in the TypeScript anyway.
polyacrx2 commits its generated code the same way and regenerates it to
resolve conflicts.

**Hidden refs per commit, rejected.** CI would publish each commit's build to
a ref such as `refs/projector/assets/<sha>`, and consumers would fetch the
build for the commit they run. The deploy at `@main` races the publish right
after a merge, every branch push needs a build for `@branch` to work, and the
local CLI would have to fetch from GitHub, which ends offline use and the
no-dependency promise; a pipx install does not even record its commit.

**Compiled output only on tags, rejected.** `@main`, which this repository's
own site deploys from, and a pipx install from `main` would have no
JavaScript, unless the action compiled at run time, which the local CLI
cannot.

## 4. Conversion

The TypeScript keeps each script's structure and every function name, so the
compiled output reads like the scripts it replaces and the node tests that
load individual functions still find them. `globals.d.ts` declares the
libraries the pages load from cdnjs and the shapes of `site.json`,
`search.json`, and a walkthrough's data, which the scripts are now checked
against. The compiler, TypeScript 7.0.2, is pinned exactly in
`package.json`, and `package-lock.json` pins its native binaries.

## 5. Outcome

Shipped. Both scripts type-check under `strict`, `noUnusedLocals`, and
`noImplicitReturns`. The test suite passes against the compiled output,
including the node tests that run `highlight` and `routeFor`, and the
freshness test fails on a hand-edited compiled file. In Chrome, against this
repository's site and the HTML demo, the projects, docs, search, and review
pages rendered from the compiled scripts, the HTML frame kept its deep link
and sidebar toggle, and a review page collapsed a viewed file, counted a
reviewed group, and highlighted its syntax, with no console errors.
