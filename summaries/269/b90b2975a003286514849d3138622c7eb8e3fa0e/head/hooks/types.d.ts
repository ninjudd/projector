/** What a loop does with its pull requests: `watch-threads.sh` watches, `watch-prs.sh` reviews. */
export type LoopKind = 'watching' | 'reviewing'

/** A tracked file this session started a watcher on, or named by hand. */
export type SeenLoop = { kind?: LoopKind; tracked: string }

export type Checks = 'passing' | 'failing' | 'pending'

export type PullRequest = {
  repo: string
  number: number
  title: string
  url: string
  state: 'OPEN' | 'MERGED' | 'CLOSED'
  isDraft: boolean
  reviewDecision: 'APPROVED' | 'CHANGES_REQUESTED' | 'REVIEW_REQUIRED' | null
  isConflicting: boolean
  openThreads: number
  author: string | null
  branch: string
  additions: number
  deletions: number
  changedFiles: number
  /** The head commit's combined check state; null when it has no checks. */
  checks: Checks | null
  /** When the pull request last changed, in milliseconds; null when GitHub did not say. */
  updatedAt: number | null
  /** The Projector summary the description links, when one was published. */
  summaryUrl: string | null
}

/** One line of the band: an open pull request and what the loops do with it. */
export type Row = { pr: PullRequest; kinds: LoopKind[] }

export type Snapshot = {
  rows: Row[]
  fetchedAt: number | null
  error: string | null
}

declare module 'claude-code' {
  interface PluginState {
    projector: {
      seenLoops: SeenLoop[]
      bandSnapshot: Snapshot
      bandNow: number
      isBandShown: boolean
      /** The pull request whose drawer is open, by `keyOf`. */
      bandDrawer: string | null
    }
  }
}
