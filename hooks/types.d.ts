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

/** How an item was resolved: dismissed, or a question answered with a lettered choice or typed text. */
export type Answer = { letter: string; text: string; answeredAt: number } | { dismissed: true; answeredAt: number }

/** What every item has, as `project inbox list --json` gives it, with the session whose store holds it. */
export type Posted = {
  ref: string
  number: number
  /** The pull request it is about, as `owner/repo#number`; null when none. */
  pr: string | null
  /** When the agent posted it, in milliseconds. */
  postedAt: number
  /** The question or the notice, in one line. */
  subject: string
  detail: string
  /** The subagent that posted it. */
  agentId?: string
  /** When a snoozed item returns, in milliseconds; absent when it is not snoozed. */
  snoozedUntil?: number
  deliveredAt?: number
  session: string
}

/** A question, whose answer or dismissal goes back to the agent that asked. */
export type Question = Posted & {
  kind: 'question'
  /** The answers in letter order, the recommendation first; the letter after the last is a typed answer. */
  choices: string[]
  answer?: Answer
  claim?: { token: string; renewedAt: number }
}

/** A notice, which you read and dismiss, and which sends nothing back; dismissing it delivers it. */
export type Notice = Posted & {
  kind: 'notice'
  answer?: { dismissed: true; answeredAt: number }
}

export type Item = Question | Notice

/**
 * An answer the band claimed and is delivering: the claim's token, the route
 * it took, and the text sent, so a later copy of the module keeps renewing
 * the claim and knows the prompt when it arrives.
 */
export type Sending = {
  ref: string
  session: string
  token: string
  route: 'send' | 'submit'
  text: string
}

declare module 'claude-code' {
  interface PluginState {
    projector: {
      seenLoops: SeenLoop[]
      bandSnapshot: Snapshot
      bandNow: number
      isBandShown: boolean
      /** The drawer that is open: a pull request's by `keyOf`, or `item:` and an item's reference. */
      bandDrawer: string | null
      /** The session's inbox, as the band last read it. */
      bandInbox: Item[]
      /** The answers the band is delivering, by reference. */
      bandSending: Record<string, Sending>
      /** What the band says when it has nothing to list: the model's quote, or null until it is asked. */
      bandQuote: string | null
    }
  }
}
