/** Which watcher a loop runs: `watch-prs.sh` reviews, `watch-threads.sh` fixes. */
export type LoopKind = 'review' | 'fix' | 'unknown'

/** A tracked file this session started a watcher on, or named by hand. */
export type SeenLoop = { kind: LoopKind; tracked: string }

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
}

/** One loop as the pane draws it. */
export type LoopView = {
  kind: LoopKind
  tracked: string
  isOwn: boolean
  isRunning: boolean
  prs: PullRequest[]
  /** Tracked entries GitHub returned no pull request for. */
  missing: string[]
  error: string | null
}

export type Snapshot = {
  loops: LoopView[]
  fetchedAt: number | null
  error: string | null
}

declare module 'claude-code' {
  interface PluginState {
    projector: { seenLoops: SeenLoop[]; loopSnapshot: Snapshot }
  }
}
