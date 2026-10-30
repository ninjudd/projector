/** What a loop does with its pull requests: `watch-threads.sh` watches, `watch-prs.sh` reviews. */
export type LoopKind = 'watching' | 'reviewing'

/** A tracked file this session started a watcher on. */
export type SeenLoop = { kind: LoopKind; tracked: string }

export type Checks = 'passing' | 'failing' | 'pending'

/** A review thread no one has resolved, as a pull request's drawer lists it. */
export type Thread = {
  path: string
  /** The line the thread is on, or was on once its line changed; null for a comment on the whole file. */
  line: number | null
  /** Whether a later push changed the lines the thread is on, which GitHub then folds away. */
  isOutdated: boolean
  /** Who opened the thread; null when GitHub did not say. */
  author: string | null
  /** The first line of the thread's first comment, as plain text. */
  excerpt: string
  /** The thread's first comment on GitHub; null when GitHub did not say. */
  url: string | null
}

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

/** One line of the panel: an open pull request and what the loops do with it. */
export type Row = { pr: PullRequest; kinds: LoopKind[] }

export type Snapshot = {
  rows: Row[]
  /** The login of the account the panel's `gh` is signed in as; null until GitHub says. */
  viewer: string | null
  fetchedAt: number | null
  error: string | null
}

/** A dismissal, `withdrawn` when the agent that posted the item took it back. */
export type Dismissal = { dismissed: true; withdrawn?: true; answeredAt: number }

/** How an item was resolved: dismissed, or a question answered with a lettered choice or typed text. */
export type Answer = { letter: string; text: string; answeredAt: number } | Dismissal

/**
 * How a request was resolved: done, declined, or withdrawn, with the person's
 * one line of `text` when they gave one. A decline is a dismissal.
 */
export type Outcome =
  | { done: true; text?: string; answeredAt: number }
  | { dismissed: true; declined: true; text?: string; answeredAt: number }
  | Dismissal

/** What every item has, as `project inbox list --json` gives it, with the session whose store holds it. */
export type Posted = {
  /** The item's number in its session, as a string such as `"3"`: what the CLI prints and takes. */
  ref: string
  number: number
  /** The pull request it is about, as `owner/repo#number`; null when none. */
  pr: string | null
  /** When the agent posted it, in milliseconds. */
  postedAt: number
  /** The question, the notice, or the request, in one line. */
  subject: string
  detail: string
  /** The subagent that posted it. */
  agentId?: string
  /** When a snoozed item returns, in milliseconds; absent when it is not snoozed. */
  snoozedUntil?: number
  /** When the item was last snoozed, in milliseconds; absent when it is not snoozed. */
  snoozedAt?: number
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
  /** When the person restored the question after its agent may have heard `DISMISSED`, until `RESTORED` is delivered. */
  restoredAt?: number
}

/** A notice, which you read and dismiss, and which sends nothing back; dismissing it delivers it. */
export type Notice = Posted & {
  kind: 'notice'
  answer?: Dismissal
}

/**
 * A request for work only you can do, whose `detail` holds its steps; its
 * outcome, done or declined, goes back to the agent that posted it.
 */
export type Request = Posted & {
  kind: 'request'
  /** What the agent does once you press start, in one line; absent when there is nothing to prepare. */
  start?: string
  /** The start action as it read when you pressed start or retry, and when; `deliveredAt` once `STARTED` reached the agent. */
  started?: { action: string; startedAt: number; deliveredAt?: number }
  /** What the agent made of the start: `ready`, or `blocked` with the reason you read before you retry. */
  progress?: { state: 'ready' | 'blocked'; reason?: string; at: number }
  answer?: Outcome
  /** A deliverer's hold on the line it sends: `line` says which, a `STARTED` or the outcome. */
  claim?: { token: string; renewedAt: number; line?: 'answer' | 'restored' | 'started' }
  /** When the person restored the request after its agent may have heard `DECLINED`, until `RESTORED` is delivered. */
  restoredAt?: number
}

export type Item = Question | Notice | Request

/** An item whose answer or outcome goes back to the agent that posted it. */
export type Answerable = Question | Request

/** What the panel's header counts, and can collapse to one line: the pull requests, or a kind of item. */
export type Section = 'pr' | Item['kind']

/**
 * An answer the panel claimed and is delivering: the claim's token, the route
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
      panelSnapshot: Snapshot
      panelNow: number
      isPanelShown: boolean
      /** The drawer that is open: a pull request's by `keyOf`, or `item:` and an item's reference. */
      panelDrawer: string | null
      /**
       * What an open drawer asks before it acts, in its actions' place: how
       * long to snooze, or whether to dismiss. `drawer` is the key
       * `panelDrawer` holds.
       */
      panelAsked: { drawer: string; about: 'snooze' | 'dismiss' } | null
      /**
       * The unresolved threads of the pull request whose drawer is open, as
       * GitHub last gave them. `drawer` is the key `panelDrawer` holds.
       */
      panelThreads: { drawer: string; threads: Thread[] } | null
      /** The session's inbox, as the panel last read it. */
      panelInbox: Item[]
      /** The pull requests and the kinds of item collapsed to one line, from the header's counts. */
      panelCollapsed: Section[]
      /** The answers the panel is delivering, by reference. */
      panelSending: Record<string, Sending>
      /** What the release before the rename kept as `panelSending`, which the panel takes over. */
      bandSending: Record<string, Sending>
      /** The panel's background for the session's Claude Code theme, which `panel.background = "auto"` takes. */
      panelThemeBackground: string
      /** `panel.background` as the panel last read it: `auto`, or the color it draws for the value. */
      panelBackground: string
      /** Whether the panel took the keyboard since the last keystroke in the prompt, so its hint names its own keys. */
      isPanelFocused: boolean
    }
  }
}
