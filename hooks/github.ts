import type { Checks, PullRequest } from './types'
import { keyOf, type TrackedPr } from './parse'

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
      return `${prAlias}: pullRequest(number: ${pr.number}) { ${FIELDS} }`
    })
    return `${repoAlias}: repository(owner: "${owner}", name: "${name}") { ${pulls.join(' ')} }`
  })

  return { text: `query { viewer { login } ${repos.join(' ')} }`, slots }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const firstLine = (text: string) => text.trim().split('\n')[0] ?? ''

function messageOf(body: unknown): string | undefined {
  if (!isRecord(body) || !Array.isArray(body.errors)) return undefined
  const [first] = body.errors as unknown[]
  return isRecord(first) && typeof first.message === 'string' ? first.message : undefined
}

const nodesOf = (value: unknown): unknown[] =>
  isRecord(value) && Array.isArray(value.nodes) ? (value.nodes as unknown[]) : []

const numberOf = (value: unknown) => (typeof value === 'number' ? value : 0)

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
 * Reads what `gh api graphql` printed. A pull request that does not exist
 * leaves its slot null and adds an error, but the rest still answer, so
 * errors fail the fetch only when no data came back at all.
 */
export function parseResponse(
  query: Query,
  result: { exitCode: number; stdout: string; stderr: string },
): Fetched {
  let body: unknown
  try {
    body = JSON.parse(result.stdout)
  } catch {
    const reason = firstLine(result.stderr) || `gh exited with ${result.exitCode}`
    return { prs: new Map(), viewer: null, error: reason }
  }

  const data = isRecord(body) && isRecord(body.data) ? body.data : undefined
  if (data === undefined) {
    return {
      prs: new Map(),
      viewer: null,
      error: messageOf(body) ?? (firstLine(result.stderr) || 'GitHub returned no data'),
    }
  }

  const prs = new Map<string, PullRequest>()
  for (const slot of query.slots) {
    const repo = data[slot.repoAlias]
    const node = isRecord(repo) ? repo[slot.prAlias] : undefined
    if (isRecord(node)) prs.set(keyOf(slot.pr), toPullRequest(slot.pr, node))
  }

  const viewer = isRecord(data.viewer) && typeof data.viewer.login === 'string' ? data.viewer.login : null
  return { prs, viewer, error: null }
}
