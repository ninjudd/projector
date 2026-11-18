/**
 * What an earlier watcher did with the pull requests its tracked file lists:
 * `watch-threads.sh` watches, `watch-prs.sh` reviews. A session that started
 * one before the daemon keeps it until the session ends.
 */
export type LoopKind = 'watching' | 'reviewing'

/** A tracked file this session started an earlier watcher on. */
export type SeenLoop = { kind: LoopKind; tracked: string }

/** What the session does with a pull request, as its drawer names it. */
export type Role = 'fixing' | 'reviewing'

export type Checks = 'passing' | 'failing' | 'pending' | 'cancelled'

/** A label on a pull request, as GitHub spells and colors it. */
export type Label = {
  name: string
  /** GitHub's color for the label, as `#rrggbb`; null when the cache predates the panel reading it. */
  color: string | null
}

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
  /** Whether a workflow's newest run on the head was cancelled, so nothing has run its checks since. */
  isCancelled: boolean
  /** The pull request's labels, among them the `reviewing` and `clean` a review sets. */
  labels: Label[]
  /** When the pull request last changed, in milliseconds; null when GitHub did not say. */
  updatedAt: number | null
  /** The Projector summary the description links, when one was published. */
  summaryUrl: string | null
  /** Whether the daemon has yet to give a result for the pull request, which then holds only its reference. */
  isLoading?: boolean
}

/** One line of the panel: an open pull request and what the session does with it. */
export type Row = { pr: PullRequest; roles: Role[] }

export type Snapshot = {
  rows: Row[]
  /** The operator's login, from the daemon or else from GitHub; null until one says. */
  viewer: string | null
  fetchedAt: number | null
  error: string | null
}

/** A dismissal, `withdrawn` when the agent that posted the item took it back. */
export type Dismissal = { dismissed: true; withdrawn?: true; answeredAt: number }

/**
 * How an item was resolved: dismissed, or a question answered with a lettered
 * choice or typed text, and the `note` the person added in `project console`
 * or with `--note`.
 */
export type Answer = { letter: string; text: string; note?: string; answeredAt: number } | Dismissal

/**
 * How a request was resolved: done, declined, or withdrawn, with the person's
 * one line of `text` and their `note` when they gave them. A decline is a
 * dismissal.
 */
export type Outcome =
  | { done: true; text?: string; note?: string; answeredAt: number }
  | { dismissed: true; declined: true; text?: string; note?: string; answeredAt: number }
  | Dismissal

/** A message you sent the agent about an item, which reaches it as `DISCUSS` and has `deliveredAt` once it did. */
export type Message = { text: string; at: number; deliveredAt?: number }

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
  /**
   * When you sent the prompt I don't understand filled, or ran `project inbox
   * unclear`, until the agent revises the item; `deliveredAt` once `UNCLEAR`
   * reached the agent.
   */
  unclear?: { at: number; deliveredAt?: number }
  /**
   * A deliverer's hold on the line the item owes its agent: `line` says
   * which, the answer or outcome, `RESTORED`, `STARTED`, `UNCLEAR`, or
   * `DISCUSS`.
   */
  claim?: {
    token: string
    renewedAt: number
    line?: 'answer' | 'restored' | 'started' | 'unclear' | 'discuss'
    /** The message a `DISCUSS` claim delivers, by its index in `messages`. */
    entry?: number
  }
  /** The messages you sent the agent about the item, oldest first; absent until the first. */
  messages?: Message[]
  deliveredAt?: number
  session: string
}

/** A question, whose answer or dismissal goes back to the agent that asked. */
export type Question = Posted & {
  kind: 'question'
  /** The answers in letter order, the recommendation first; the letter after the last is a typed answer. */
  choices: string[]
  answer?: Answer
  /** When the person restored the question after its agent may have heard `DISMISSED`, until `RESTORED` is delivered. */
  restoredAt?: number
}

/** A notice, which you read and dismiss, and which sends back nothing but `UNCLEAR`; dismissing it delivers it. */
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
  /** The options the agent offers you when you finish, each marking the request done; absent offers a single `Done`. */
  choices?: string[]
  /** The start action as it read when you pressed start or retry, and when; `deliveredAt` once `STARTED` reached the agent. */
  started?: { action: string; startedAt: number; deliveredAt?: number }
  /** What the agent made of the start: `ready`, or `blocked` with the reason you read before you retry. */
  progress?: { state: 'ready' | 'blocked'; reason?: string; at: number }
  answer?: Outcome
  /** When the person restored the request after its agent may have heard `DECLINED`, until `RESTORED` is delivered. */
  restoredAt?: number
}

/**
 * A message you sent about a pull request or about the session itself. It
 * asks nothing of you, and dismissing it delivers it at once. It is resolved
 * as `delivered` once its message reached the agent, or as dismissed when the
 * message was taken back.
 */
export type MessageItem = Posted & {
  kind: 'message'
  answer?: Dismissal | { delivered: true; answeredAt: number }
}

export type Item = Question | Notice | Request | MessageItem

/**
 * An entry of the repository inbox, as `project inbox list --json` gives it
 * with `scope: "repo"`: an item a peer's agent posted to you, the copy of one
 * your agent sent a peer, or a peer's reply to it. Its `ref` is `r` and a
 * number, such as `"r3"`, and `line` is how it reads.
 */
export type Entry = {
  scope: 'repo'
  ref: string
  id: string
  role: 'incoming' | 'sent' | 'reply'
  /** An incoming or sent item's kind; a reply has none. */
  kind?: 'question' | 'request' | 'notice'
  subject: string
  detail?: string
  /** A question's answers, the recommendation first, or a request's options. */
  choices?: string[]
  follow_up?: string
  /** A reply's text. */
  text?: string
  line: string
  state: 'open' | 'snoozed' | 'closed' | 'replied'
  /** Your name for the peer it is from or to. */
  peer: string | null
  /** A sent item's replies, by reference. */
  replies?: string[]
  /** The sent item a reply answers, by ID. */
  answers?: string
  /** When this machine opened it, in milliseconds. */
  opened_at: number
}

/** An item whose answer or outcome goes back to the agent that posted it. */
export type Answerable = Question | Request

/**
 * What the panel's header counts, and can collapse to one line: your own pull
 * requests, the review requests someone else's pull requests make, or a kind
 * of item.
 */
export type Section = 'pr' | 'review' | Item['kind']

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
      /** The pull requests the session subscribed to when the panel last looked, by `keyOf`, so a new one shows the panel. */
      panelSubscribed: string[]
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
      /**
       * The files whose threads a drawer shows, by path. Every other file is
       * collapsed. `drawer` is the key `panelDrawer` holds.
       */
      panelOpenFiles: { drawer: string; paths: string[] } | null
      /** The session's inbox, as the panel last read it. */
      panelInbox: Item[]
      /** The repository inbox of the repository the session is registered in, as the panel last read it. */
      panelEntries: Entry[]
      /** The sections of pull requests and the kinds of item collapsed to one line, from the header's counts. */
      panelCollapsed: Section[]
      /** The answers the panel is delivering, by reference. */
      panelSending: Record<string, Sending>
      /** What the release before the rename kept as `panelSending`, which the panel takes over. */
      bandSending: Record<string, Sending>
      /** The panel's background for the session's Claude Code theme, which `panel.background = "auto"` takes. */
      panelThemeBackground: string
      /** The color of an open request's line for the session's Claude Code theme, which `paint` sets with the background. */
      panelThemeRequestColor: string
      /** `panel.background` as the panel last read it: `auto`, or the color it draws for the value. */
      panelBackground: string
      /** Whether the panel took the keyboard since the last keystroke in the prompt, so its hint names its own keys. */
      isPanelFocused: boolean
    }
  }
}
