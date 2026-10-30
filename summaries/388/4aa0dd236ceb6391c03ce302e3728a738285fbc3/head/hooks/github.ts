import type { Checks, PullRequest, Thread } from './types'
import { isRecord, keyOf, type TrackedPr } from './parse'

/** Where one pull request's node sits in the response. */
type Slot = { pr: TrackedPr; repoAlias: string; prAlias: string }

export type Query = { text: string; slots: Slot[] }

export type Fetched = {
  prs: Map<string, PullRequest>
  /** The login of the account `gh` is signed in as; null when GitHub did not say. */
  viewer: string | null
  error: string | null
}

const FIELDS = `number title url state isDraft reviewDecision mergeable body
  author { login } headRefName additions deletions changedFiles updatedAt
  reviewThreads(first: 100) { nodes { isResolved } }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }`

// What an open drawer lists of its pull request's threads. GitHub counts
// `comments` under `reviewThreads(first: 100)` as 100 requests, a point of
// the account's quota, for each pull request. So the poll reads only
// `isResolved`, and the panel asks for these for the open drawer's alone.
const THREAD_FIELDS = `reviewThreads(first: 100) { nodes { isResolved isOutdated path line originalLine
  comments(first: 1) { nodes { url bodyText author { login } } } } }`

// The line `project summary publish` leaves at the bottom of a description.
const SUMMARY = /^<!-- projector-summary v=1 sha=[0-9a-f]{40} -->\r?\n\S* ?\*\*Projector summary\*\* [^\r\n]*?(https?:\/\/\S+)\s*$/m

const CHECKS: Record<string, Checks> = {
  SUCCESS: 'passing',
  FAILURE: 'failing',
  ERROR: 'failing',
  PENDING: 'pending',
  EXPECTED: 'pending',
}

/**
 * Builds one GraphQL query for every pull request, grouped by repository,
 * and for the signed-in account's login. Repository names come from
 * `parseTracked`, which admits only GitHub's name characters, so they are
 * safe inside a string literal.
 */
export function buildQuery(prs: readonly TrackedPr[]): Query {
  const query = queryOf(prs, FIELDS)
  return { ...query, text: `query { viewer { login } ${query.text} }` }
}

/** Builds the query for one pull request's unresolved threads, which its open drawer lists. */
export function buildThreadsQuery(pr: TrackedPr): Query {
  const query = queryOf([pr], THREAD_FIELDS)
  return { ...query, text: `query { ${query.text} }` }
}

/** The repositories' part of a query that asks for `fields` of each pull request. */
function queryOf(prs: readonly TrackedPr[], fields: string): Query {
  const byRepo = new Map<string, TrackedPr[]>()
  for (const pr of prs) {
    byRepo.set(pr.repo, [...(byRepo.get(pr.repo) ?? []), pr])
  }

  const slots: Slot[] = []
  const repos = [...byRepo.entries()].map(([repo, list], index) => {
    const [owner, name] = repo.split('/')
    const repoAlias = `r${index}`
    const pulls = list.map(pr => {
      const prAlias = `p${pr.number}`
      slots.push({ pr, repoAlias, prAlias })
      return `${prAlias}: pullRequest(number: ${pr.number}) { ${fields} }`
    })
    return `${repoAlias}: repository(owner: "${owner}", name: "${name}") { ${pulls.join(' ')} }`
  })

  return { text: repos.join(' '), slots }
}

const firstLine = (text: string) => text.trim().split('\n')[0] ?? ''

function messageOf(body: unknown): string | undefined {
  if (!isRecord(body) || !Array.isArray(body.errors)) return undefined
  const [first] = body.errors as unknown[]
  return isRecord(first) && typeof first.message === 'string' ? first.message : undefined
}

const nodesOf = (value: unknown): unknown[] =>
  isRecord(value) && Array.isArray(value.nodes) ? (value.nodes as unknown[]) : []

const numberOf = (value: unknown) => (typeof value === 'number' ? value : 0)

const stringOf = (value: unknown) => (typeof value === 'string' ? value : null)

/** An unresolved thread as the drawer lists it, or null for a resolved one. */
function threadOf(node: unknown): Thread | null {
  if (!isRecord(node) || node.isResolved !== false) return null
  const [first] = nodesOf(node.comments)
  const comment = isRecord(first) ? first : {}
  const line = typeof node.line === 'number' ? node.line : node.originalLine
  return {
    path: stringOf(node.path) ?? '',
    line: typeof line === 'number' ? line : null,
    isOutdated: node.isOutdated === true,
    author: isRecord(comment.author) ? stringOf(comment.author.login) : null,
    excerpt: firstLine(stringOf(comment.bodyText) ?? ''),
    url: stringOf(comment.url),
  }
}

function checksOf(node: Record<string, unknown>): Checks | null {
  const [last] = nodesOf(node.commits)
  const commit = isRecord(last) ? last.commit : undefined
  const rollup = isRecord(commit) ? commit.statusCheckRollup : undefined
  return isRecord(rollup) && typeof rollup.state === 'string' ? (CHECKS[rollup.state] ?? null) : null
}

function toPullRequest(pr: TrackedPr, node: Record<string, unknown>): PullRequest {
  const threads = nodesOf(node.reviewThreads)
  const state = node.state === 'MERGED' || node.state === 'CLOSED' ? node.state : 'OPEN'
  const decision = node.reviewDecision
  const updatedAt = typeof node.updatedAt === 'string' ? Date.parse(node.updatedAt) : Number.NaN
  const summary = typeof node.body === 'string' ? SUMMARY.exec(node.body) : null
  return {
    repo: pr.repo,
    number: pr.number,
    title: typeof node.title === 'string' ? node.title : '',
    url: typeof node.url === 'string' ? node.url : `https://github.com/${pr.repo}/pull/${pr.number}`,
    state,
    isDraft: node.isDraft === true,
    reviewDecision:
      decision === 'APPROVED' || decision === 'CHANGES_REQUESTED' || decision === 'REVIEW_REQUIRED'
        ? decision
        : null,
    isConflicting: node.mergeable === 'CONFLICTING',
    openThreads: threads.filter(thread => isRecord(thread) && thread.isResolved === false).length,
    author: isRecord(node.author) && typeof node.author.login === 'string' ? node.author.login : null,
    branch: typeof node.headRefName === 'string' ? node.headRefName : '',
    additions: numberOf(node.additions),
    deletions: numberOf(node.deletions),
    changedFiles: numberOf(node.changedFiles),
    checks: checksOf(node),
    updatedAt: Number.isNaN(updatedAt) ? null : updatedAt,
    summaryUrl: summary?.[1] ?? null,
  }
}

/**
 * Reads the daemon's cache file for a pull request,
 * `{"repository": {"pullRequest": {...}}, ...}`, where `pullRequest` holds
 * every field `FIELDS` asks for. Null when the file holds no pull request.
 */
export function parseCache(pr: TrackedPr, text: string): PullRequest | null {
  let cached: unknown
  try {
    cached = JSON.parse(text)
  } catch {
    return null
  }
  const repository = isRecord(cached) ? cached.repository : undefined
  const node = isRecord(repository) ? repository.pullRequest : undefined
  return isRecord(node) ? toPullRequest(pr, node) : null
}

/** A pull request the session subscribes to that the daemon has no result for yet. */
export const loadingOf = (pr: TrackedPr): PullRequest => ({
  ...toPullRequest(pr, {}),
  isLoading: true,
})

type Result = { exitCode: number; stdout: string; stderr: string }

/**
 * The data in what `gh api graphql` printed. A pull request that does not
 * exist leaves its slot null and adds an error, but the rest still answer, so
 * errors fail the fetch only when no data came back at all.
 */
function dataOf(result: Result): { data: Record<string, unknown> } | { error: string } {
  let body: unknown
  try {
    body = JSON.parse(result.stdout)
  } catch {
    return { error: firstLine(result.stderr) || `gh exited with ${result.exitCode}` }
  }
  if (isRecord(body) && isRecord(body.data)) return { data: body.data }
  return { error: messageOf(body) ?? (firstLine(result.stderr) || 'GitHub returned no data') }
}

/** The node a slot names in a query's data, when GitHub gave one. */
function nodeAt(data: Record<string, unknown>, slot: Slot): Record<string, unknown> | undefined {
  const repo = data[slot.repoAlias]
  const node = isRecord(repo) ? repo[slot.prAlias] : undefined
  return isRecord(node) ? node : undefined
}

/** Reads what `gh api graphql` printed for `buildQuery`. */
export function parseResponse(query: Query, result: Result): Fetched {
  const read = dataOf(result)
  if ('error' in read) return { prs: new Map(), viewer: null, error: read.error }
  const { data } = read

  const prs = new Map<string, PullRequest>()
  for (const slot of query.slots) {
    const node = nodeAt(data, slot)
    if (node !== undefined) prs.set(keyOf(slot.pr), toPullRequest(slot.pr, node))
  }

  const viewer = isRecord(data.viewer) && typeof data.viewer.login === 'string' ? data.viewer.login : null
  return { prs, viewer, error: null }
}

/**
 * Reads what `gh api graphql` printed for `buildThreadsQuery`: the pull
 * request's unresolved threads, oldest first, or null when GitHub gave none
 * of it.
 */
export function parseThreads(query: Query, result: Result): Thread[] | null {
  const read = dataOf(result)
  const [slot] = query.slots
  const node = 'error' in read || slot === undefined ? undefined : nodeAt(read.data, slot)
  if (node === undefined) return null
  return nodesOf(node.reviewThreads)
    .map(threadOf)
    .filter(thread => thread !== null)
}
