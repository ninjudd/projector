import type { PullRequest } from './types'
import { keyOf, type TrackedPr } from './parse'

/** Where one pull request's node sits in the response. */
type Slot = { pr: TrackedPr; repoAlias: string; prAlias: string }

export type Query = { text: string; slots: Slot[] }

export type Fetched = {
  prs: Map<string, PullRequest>
  error: string | null
}

const FIELDS = `number title url state isDraft reviewDecision mergeable
  reviewThreads(first: 100) { nodes { isResolved } }`

/**
 * Builds one GraphQL query for every pull request, grouped by repository.
 * Repository names come from `parseTracked`, which admits only GitHub's
 * name characters, so they are safe inside a string literal.
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

  return { text: `query { ${repos.join(' ')} }`, slots }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const firstLine = (text: string) => text.trim().split('\n')[0] ?? ''

function messageOf(body: unknown): string | undefined {
  if (!isRecord(body) || !Array.isArray(body.errors)) return undefined
  const [first] = body.errors as unknown[]
  return isRecord(first) && typeof first.message === 'string' ? first.message : undefined
}

function toPullRequest(pr: TrackedPr, node: Record<string, unknown>): PullRequest {
  const threads = isRecord(node.reviewThreads) && Array.isArray(node.reviewThreads.nodes)
    ? (node.reviewThreads.nodes as unknown[])
    : []
  const state = node.state === 'MERGED' || node.state === 'CLOSED' ? node.state : 'OPEN'
  const decision = node.reviewDecision
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
    return { prs: new Map(), error: reason }
  }

  const data = isRecord(body) && isRecord(body.data) ? body.data : undefined
  if (data === undefined) {
    return { prs: new Map(), error: messageOf(body) ?? (firstLine(result.stderr) || 'GitHub returned no data') }
  }

  const prs = new Map<string, PullRequest>()
  for (const slot of query.slots) {
    const repo = data[slot.repoAlias]
    const node = isRecord(repo) ? repo[slot.prAlias] : undefined
    if (isRecord(node)) prs.set(keyOf(slot.pr), toPullRequest(slot.pr, node))
  }

  return { prs, error: null }
}
