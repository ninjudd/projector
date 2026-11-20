import type { Checks, Label, PullRequest, Thread } from './types'
import { isRecord, type TrackedPr } from './parse'

/** A query for one pull request, and the aliases its node sits under in the response. */
export type Query = { text: string; repoAlias: string; prAlias: string }

// What an open drawer lists of its pull request's threads. GitHub counts
// `comments` under `reviewThreads(first: 100)` as 100 requests, a point of
// the account's quota, for each pull request, so the panel asks for these for
// the open drawer's alone.
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

const COUNT_KEYS = ['checkRunCountsByState', 'statusContextCountsByState'] as const

/**
 * What each check run's and commit status's state counts as, with the
 * failures the watch daemon's CHECKS event counts. A state missing here,
 * such as `SKIPPED`, holds nothing back.
 */
const CONTEXT_CHECKS: Record<(typeof COUNT_KEYS)[number], Record<string, Checks>> = {
  checkRunCountsByState: {
    FAILURE: 'failing',
    TIMED_OUT: 'failing',
    STARTUP_FAILURE: 'failing',
    CANCELLED: 'cancelled',
    QUEUED: 'pending',
    IN_PROGRESS: 'pending',
    WAITING: 'pending',
    PENDING: 'pending',
    REQUESTED: 'pending',
  },
  statusContextCountsByState: { FAILURE: 'failing', ERROR: 'failing', PENDING: 'pending', EXPECTED: 'pending' },
}

/** Builds the query for one pull request's unresolved threads, which its open drawer lists. */
export function buildThreadsQuery(pr: TrackedPr): Query {
  const [owner, name] = pr.repo.split('/')
  const repoAlias = 'r0'
  const prAlias = `p${pr.number}`
  const pull = `${prAlias}: pullRequest(number: ${pr.number}) { ${THREAD_FIELDS} }`
  return { text: `query { ${repoAlias}: repository(owner: "${owner}", name: "${name}") { ${pull} } }`, repoAlias, prAlias }
}

const firstLine = (text: string) => text.trim().split('\n')[0] ?? ''

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

/** The pull request's head commit, as the query reads it. */
function headOf(node: Record<string, unknown>): Record<string, unknown> {
  const [last] = nodesOf(node.commits)
  return isRecord(last) && isRecord(last.commit) ? last.commit : {}
}

/** The suite of a check run the rollup lists, which says whether its workflow run was cancelled; none for a status. */
function suiteOf(context: unknown): Record<string, unknown> | undefined {
  return isRecord(context) && context.__typename === 'CheckRun' && isRecord(context.checkSuite)
    ? context.checkSuite
    : undefined
}

/** A check run's GitHub Actions run: its id, its workflow, its start, and its conclusion. */
interface Run {
  id: number | null
  workflow: number
  at: string
  conclusion: string | null
}

/** The workflow run a check run belongs to, or none for a check run that no workflow run made. */
function runOf(context: unknown): Run | undefined {
  const suite = suiteOf(context)
  const run = isRecord(suite?.workflowRun) ? suite.workflowRun : undefined
  const workflow = isRecord(run?.workflow) ? run.workflow.databaseId : undefined
  const at = stringOf(run?.createdAt)
  const id = typeof run?.databaseId === 'number' ? run.databaseId : null
  return typeof workflow === 'number' && at !== null
    ? { id, workflow, at, conclusion: stringOf(suite?.conclusion) }
    : undefined
}

/**
 * When each cancelled workflow run was cancelled: the earliest end of its
 * check runs that concluded `CANCELLED`. A cancelled run none of whose
 * cancelled check runs the rollup listed has no time here.
 */
function cancelTimesOf(contexts: unknown[]): Map<number, string> {
  const times = new Map<number, string>()
  for (const context of contexts) {
    const run = runOf(context)
    const ended = isRecord(context) ? stringOf(context.completedAt) : null
    if (run?.id == null || run.conclusion !== 'CANCELLED' || ended === null) continue
    if (isRecord(context) && context.conclusion === 'CANCELLED') {
      const seen = times.get(run.id)
      if (seen === undefined || ended < seen) times.set(run.id, ended)
    }
  }
  return times
}

/**
 * Whether a failed check run of a cancelled workflow run started at or
 * after the cancel, which only a job that runs `if: always()` to gather the
 * jobs it needs does: it starts once they end, so in a cancelled run it
 * starts after the cancel and fails though nothing in the code did. A
 * failure that started before the cancel is the code's, and so is one in a
 * run whose cancel time is unknown.
 */
function causedByCancel(context: unknown, times: Map<number, string>): boolean {
  const run = runOf(context)
  const started = isRecord(context) ? stringOf(context.startedAt) : null
  const cancelled = run?.id == null ? undefined : times.get(run.id)
  return run?.conclusion === 'CANCELLED' && cancelled !== undefined && started !== null && started >= cancelled
}

/**
 * Whether a check run belongs to a cancelled workflow run that a newer run
 * of the same workflow on the head tests again: one still running, or one
 * whose check run of the same name ran rather than skipped or was
 * cancelled. The newer run's own result stands for the code. A cancelled
 * run with nothing newer, or whose newer run skipped the check, keeps each
 * failure that the cancel did not cause, since that check failed before the
 * cancel.
 */
function supersededOf(context: unknown, contexts: unknown[]): boolean {
  const run = runOf(context)
  if (run?.conclusion !== 'CANCELLED' || !isRecord(context)) return false
  return contexts.some(other => {
    const newer = runOf(other)
    if (newer === undefined || !isRecord(other) || newer.workflow !== run.workflow || newer.at <= run.at) return false
    const conclusion = stringOf(other.conclusion)
    return newer.conclusion === null || (other.name === context.name && conclusion !== 'SKIPPED' && conclusion !== 'CANCELLED')
  })
}

/** Each workflow's newest run on the head, among the check runs the rollup lists. */
function newestRuns(contexts: unknown[]): Run[] {
  const newest = new Map<number, Run>()
  for (const context of contexts) {
    const run = runOf(context)
    if (run === undefined) continue
    const seen = newest.get(run.workflow)
    if (seen === undefined || run.at > seen.at) newest.set(run.workflow, run)
  }
  return [...newest.values()]
}

/**
 * The head's checks from the rollup's counts, which cover every check run
 * and commit status, less two kinds of failure that say nothing about the
 * code: the `projector/review` status, which a review that requests changes
 * sets to `failure`, and a failed check run of a cancelled workflow run that
 * the cancel caused or that a newer run tests again. A newer run in the same
 * concurrency group cancels the one in progress, as a review's label does
 * where CI starts on `labeled`, and the cancelled run's `if: always()` job
 * fails though nothing in the code did. GitHub keeps the cancelled run's
 * checks on the head beside the newer run's. A head with nothing failing or
 * pending reads `cancelled` when every workflow's newest run on it was
 * cancelled. Without the counts the rollup's own state stands.
 */
function checksOf(commit: Record<string, unknown>): Checks | null {
  const rollup = commit.statusCheckRollup
  const state = isRecord(rollup) && typeof rollup.state === 'string' ? (CHECKS[rollup.state] ?? null) : null
  const contexts = isRecord(rollup) && isRecord(rollup.contexts) ? rollup.contexts : undefined
  if (contexts === undefined || !COUNT_KEYS.some(key => Array.isArray(contexts[key]))) return state

  const counts: Record<Checks, number> = { failing: 0, pending: 0, passing: 0, cancelled: 0 }
  for (const key of COUNT_KEYS) {
    const rows = Array.isArray(contexts[key]) ? (contexts[key] as unknown[]) : []
    for (const row of rows) {
      if (isRecord(row) && typeof row.state === 'string' && typeof row.count === 'number') {
        counts[CONTEXT_CHECKS[key][row.state] ?? 'passing'] += row.count
      }
    }
  }
  const status = commit.status
  const review = isRecord(status) && isRecord(status.context) ? stringOf(status.context.state) : null
  if (review !== null) counts[CONTEXT_CHECKS.statusContextCountsByState[review] ?? 'passing'] -= 1
  const nodes = nodesOf(contexts)
  const times = cancelTimesOf(nodes)
  for (const context of nodes) {
    const conclusion = isRecord(context) ? stringOf(context.conclusion) : null
    if (
      CONTEXT_CHECKS.checkRunCountsByState[conclusion ?? ''] === 'failing' &&
      (causedByCancel(context, times) || supersededOf(context, nodes))
    ) {
      counts.failing -= 1
      counts.cancelled += 1
    }
  }
  if (counts.failing > 0) return 'failing'
  if (counts.pending > 0) return 'pending'
  const newest = newestRuns(nodes)
  if (newest.length > 0 && newest.every(run => run.conclusion === 'CANCELLED')) return 'cancelled'
  return (['passing', 'cancelled'] as const).find(checks => counts[checks] > 0) ?? null
}

/**
 * Whether a workflow's newest run on the head was cancelled, so nothing has
 * run its checks since. A cancelled run that a newer one replaced says
 * nothing, since a newer run in the same concurrency group cancels the one
 * before it whenever, for one, a label starts it.
 */
function cancelledOf(commit: Record<string, unknown>): boolean {
  const rollup = commit.statusCheckRollup
  const contexts = isRecord(rollup) ? nodesOf(rollup.contexts) : []
  return newestRuns(contexts).some(run => run.conclusion === 'CANCELLED')
}

const PREVIEW_URL = /^https?:\/\/\S+$/

/**
 * The head's newest Vercel preview: the URL of the last deployment the
 * `vercel` bot made whose latest status succeeded. GitHub lists a commit's
 * deployments oldest first.
 */
function previewOf(commit: Record<string, unknown>): string | null {
  const urls = nodesOf(commit.deployments).flatMap(node => {
    if (!isRecord(node) || !isRecord(node.creator) || node.creator.login !== 'vercel') return []
    const status = isRecord(node.latestStatus) ? node.latestStatus : {}
    const url = stringOf(status.environmentUrl)
    return status.state === 'SUCCESS' && url !== null && PREVIEW_URL.test(url) ? [url] : []
  })
  return urls.at(-1) ?? null
}

const LABEL_COLOR = /^[0-9a-f]{6}$/i

function labelOf(node: unknown): Label[] {
  const name = isRecord(node) ? stringOf(node.name) : null
  if (name === null) return []
  const color = isRecord(node) ? stringOf(node.color) : null
  return [{ name, color: color !== null && LABEL_COLOR.test(color) ? `#${color.toLowerCase()}` : null }]
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
    checks: checksOf(headOf(node)),
    isCancelled: cancelledOf(headOf(node)),
    labels: nodesOf(node.labels).flatMap(labelOf),
    updatedAt: Number.isNaN(updatedAt) ? null : updatedAt,
    summaryUrl: summary?.[1] ?? null,
    previewUrl: previewOf(headOf(node)),
  }
}

/**
 * Reads the daemon's cache file for a pull request,
 * `{"repository": {"pullRequest": {...}}, ...}`, where `pullRequest` holds
 * the pull request as the daemon last asked GitHub for it, with its labels,
 * the `isResolved` of each review thread, and its head commit's checks. Null
 * when the file holds no pull request.
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

/** The pull request's node in what `gh api graphql` printed for a query, when GitHub gave one. */
function nodeOf(query: Query, result: Result): Record<string, unknown> | undefined {
  let body: unknown
  try {
    body = JSON.parse(result.stdout)
  } catch {
    return undefined
  }
  const data = isRecord(body) ? body.data : undefined
  const repo = isRecord(data) ? data[query.repoAlias] : undefined
  const node = isRecord(repo) ? repo[query.prAlias] : undefined
  return isRecord(node) ? node : undefined
}

/**
 * Reads what `gh api graphql` printed for `buildThreadsQuery`: the pull
 * request's unresolved threads, oldest first, or null when GitHub gave none
 * of it.
 */
export function parseThreads(query: Query, result: Result): Thread[] | null {
  const node = nodeOf(query, result)
  if (node === undefined) return null
  return nodesOf(node.reviewThreads)
    .map(threadOf)
    .filter(thread => thread !== null)
}
