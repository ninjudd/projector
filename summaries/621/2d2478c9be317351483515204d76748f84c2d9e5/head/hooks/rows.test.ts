import { describe, expect, test } from 'claude-code/testing'

import fixtures from '../tests/fixtures/rows/index'
import { buildThreadsQuery, loadingOf, parseCache, parseThreads } from './github'
import { cachePathOf, parseAdopted, parseOperator, parseSubscriptions, subscribedOf, type TrackedPr } from './parse'
import { bySection, labeler, notesOf, ROLE_OF, ROLES, rowState, threadsByFile } from './register'
import type { PullRequest, Row, Thread } from './types'

/**
 * A case in `tests/fixtures/rows/`, which `tests/test_rows.py` runs through `src/projector/rows.py` too: the
 * files under the watch root and the rows they give, a pull request and the thread query the drawer sends
 * for it, or what `gh api graphql` printed and the threads the drawer lists.
 */
type Case = {
  name: string
  session?: string
  files?: Record<string, unknown>
  rows?: unknown
  pr?: TrackedPr
  query?: string
  stdout?: unknown
  threads?: unknown
}

const cases: Case[] = fixtures.flat()

const ROOT = '/state/projector/watch'

/** A fixture file's text: a string as it stands, any other value as its JSON. */
const textOf = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value))

const pullOf = (pr: PullRequest) => ({
  repo: pr.repo,
  number: pr.number,
  title: pr.title,
  url: pr.url,
  state: pr.state,
  is_draft: pr.isDraft,
  review_decision: pr.reviewDecision,
  is_conflicting: pr.isConflicting,
  open_threads: pr.openThreads,
  author: pr.author,
  branch: pr.branch,
  additions: pr.additions,
  deletions: pr.deletions,
  changed_files: pr.changedFiles,
  checks: pr.checks,
  is_cancelled: pr.isCancelled,
  labels: pr.labels.map(label => label.name),
  summary_url: pr.summaryUrl,
  is_loading: pr.isLoading === true,
})

const threadOf = (thread: Thread) => ({
  path: thread.path,
  line: thread.line,
  is_outdated: thread.isOutdated,
  author: thread.author,
  excerpt: thread.excerpt,
  url: thread.url,
})

/** The rows the panel lists for the case's session, read from its files as the panel's `load` reads them. */
function rowsOf(files: Record<string, unknown>, session: string) {
  const read = (path: string) => {
    const value = files[path.slice(ROOT.length + 1)]
    return value === undefined ? undefined : textOf(value)
  }
  const folder = `${ROOT}/sessions/${session}`
  const subscribed = subscribedOf(
    parseSubscriptions(read(`${folder}/subscriptions`) ?? ''),
    parseAdopted(read(`${folder}/state.json`) ?? ''),
  )
  const rows: Row[] = subscribed.flatMap(({ pr: entry, roles }) => {
    const text = read(cachePathOf(ROOT, entry))
    const pr = (text === undefined ? null : parseCache(entry, text)) ?? loadingOf(entry)
    const held = ROLES.filter(role => roles.some(one => ROLE_OF[one] === role))
    return pr.state === 'OPEN' ? [{ pr, roles: held }] : []
  })
  const label = labeler(rows.map(row => row.pr))
  const sections = bySection(rows, parseOperator(read(`${ROOT}/daemon.json`) ?? ''))
  return (['pr', 'review'] as const).flatMap(section =>
    sections[section].map(row => ({
      section,
      label: label(row.pr),
      state: rowState(row.pr).text,
      notes: notesOf(row.pr).map(note => note.text),
      roles: row.roles,
      pull: pullOf(row.pr),
    })),
  )
}

describe('the shared row fixtures', () => {
  test('hold cases of every kind', () => {
    expect(cases.filter(one => one.files !== undefined).length).toBeGreaterThan(0)
    expect(cases.filter(one => one.query !== undefined).length).toBeGreaterThan(0)
    expect(cases.filter(one => one.stdout !== undefined).length).toBeGreaterThan(0)
  })

  for (const one of cases) {
    test(one.name, () => {
      const pr = one.pr ?? { repo: '', number: 0 }
      if (one.files !== undefined) {
        expect(rowsOf(one.files, one.session ?? '')).toEqual(one.rows)
      } else if (one.query !== undefined) {
        expect(buildThreadsQuery(pr).text).toBe(one.query)
      } else {
        const found = parseThreads(buildThreadsQuery(pr), { exitCode: 0, stdout: textOf(one.stdout), stderr: '' })
        const grouped = found === null ? null : threadsByFile(found).map(([path, threads]) => [path, threads.map(threadOf)])
        expect(grouped).toEqual(one.threads)
      }
    })
  }
})
