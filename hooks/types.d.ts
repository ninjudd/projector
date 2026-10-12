/** What a loop does with its pull requests: `watch-threads.sh` watches, `watch-prs.sh` reviews. */
export type LoopKind = 'watching' | 'reviewing'

/** A tracked file this session started a watcher on, or named by hand. */
export type SeenLoop = { kind?: LoopKind; tracked: string }

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

/** The open pull requests the pane draws, by section. */
export type Snapshot = {
  watching: PullRequest[]
  reviewing: PullRequest[]
  fetchedAt: number | null
  error: string | null
}

declare module 'claude-code' {
  interface PluginState {
    projector: { seenLoops: SeenLoop[]; loopSnapshot: Snapshot; loopNow: number }
  }
}
