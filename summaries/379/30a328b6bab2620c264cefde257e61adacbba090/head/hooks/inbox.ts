import type { AgentInfo, FsEntry } from 'claude-code'

import type { Answerable, Item, Question, Request } from './types'
import type { Env } from './parse'

/** How long a delivered item stays in the panel. */
export const RESOLVED_MS = 30_000

/** How long a claim lasts without renewal, as `project inbox claim` counts it. */
export const CLAIM_MS = 60_000

const LIVE: ReadonlySet<AgentInfo['status']> = new Set(['pending', 'running', 'waiting', 'idle'])

export const letterAt = (index: number) => String.fromCharCode('a'.charCodeAt(0) + index)

/** The letter of a typed answer: the one after the last choice. */
export const typedLetter = (question: Question) => letterAt(question.choices.length)

/** Whether an item is snoozed at a time: put off until its `snoozedUntil`. */
export const isSnoozedAt = (item: Item, time: number) => item.snoozedUntil !== undefined && time < item.snoozedUntil

/** Whether the agent that posted an item withdrew it. */
const isWithdrawn = (item: Item) => item.answer !== undefined && 'withdrawn' in item.answer

/**
 * Whether the panel shows an item: not withdrawn, not snoozed or snoozed
 * within the last 30 seconds, and open, being delivered, or delivered within
 * the last 30 seconds.
 */
export const isShownAt = (item: Item, time: number) =>
  !isWithdrawn(item) &&
  (!isSnoozedAt(item, time) || (item.snoozedAt !== undefined && time - item.snoozedAt < RESOLVED_MS)) &&
  (item.deliveredAt === undefined || time - item.deliveredAt < RESOLVED_MS)

/** The open items whose snooze ended after `since` and by `time`, which bring a hidden panel back. */
export const wokeBetween = (items: readonly Item[], since: number, time: number) =>
  items.filter(
    item =>
      item.answer === undefined && item.snoozedUntil !== undefined && since < item.snoozedUntil && item.snoozedUntil <= time,
  )

/** Whether an open request's start waits for `STARTED` to reach its agent. */
export const isStarting = (request: Request) =>
  request.answer === undefined && request.started !== undefined && request.started.deliveredAt === undefined

/**
 * Whether a question's or request's line waits for a deliverer: an answer or
 * outcome recorded and undelivered, word of a restore, or a request's start,
 * and claimed by none that is live.
 */
export const needsDelivery = (item: Item, time: number): item is Answerable =>
  item.kind !== 'notice' &&
  (item.restoredAt !== undefined ||
    (item.answer !== undefined && item.deliveredAt === undefined) ||
    (item.kind === 'request' && isStarting(item))) &&
  (item.claim === undefined || time - item.claim.renewedAt >= CLAIM_MS)

/** What the panel puts in the prompt to discuss an item: its subject, in quotes. */
export const quoteOf = (item: Item) => `"${item.subject}"`

/** What the panel puts in the prompt for an answer or an outcome: the quoted item, a blank line, and the answer's text. */
export const proposalOf = (item: Answerable, answer: string) => `${quoteOf(item)}\n\n${answer}`

/** One answer a sent prompt gives: the letter of the option it picks. */
export type Given = { question: Question; letter: string }

/**
 * What a request's drawer offers after any start, in order: the options its
 * agent gave, or a single `Done` when it gave none. Each puts its words under
 * the request's quote and marks the request done, saying which. Declining is
 * one of the drawer's actions, since it needs no notes.
 */
export const choicesOf = (request: Request): readonly string[] => request.choices ?? ['Done']

/** One outcome a sent prompt gives a request: done or declined, and the words that said so. */
export type Finished = { request: Request; outcome: 'done' | 'declined'; word: string }

/**
 * The outcome a request's quote takes: the words on the first line after it,
 * so notes can follow. One of the request's options, or a plain `Done`, marks
 * it done, and a plain `Declined` declines it.
 */
function outcomeIn(request: Request, said: string | undefined): Omit<Finished, 'request'> | undefined {
  const word = said?.split('\n')[0]?.trim() ?? ''
  if (word === 'Declined') return { outcome: 'declined', word }
  return word === 'Done' || choicesOf(request).includes(word) ? { outcome: 'done', word } : undefined
}

/**
 * What a question's or request's drawer put in the prompt: the item's quote
 * as it read then, and the option or outcome chosen, or an empty answer for
 * discuss. `isStale` marks one put there before the item was revised.
 */
export type Proposal = { ref: string; quote: string; answer: string; isStale?: boolean }

/**
 * What a sent prompt says to the open questions and requests: the answers and
 * outcomes it gives, and the items it replies to.
 */
export type Read = { answers: Given[]; outcomes: Finished[]; replies: Answerable[] }

/**
 * Reads a sent prompt as the panel writes it, each quote speaking to one
 * question or request. A quoted open question takes an answer only when its
 * quote ends its line and the text after it, up to the next quoted item or
 * the prompt's end, is one of its options, whatever blank lines and spaces
 * surround it: what pressing an option puts in the prompt. A quoted open
 * request takes an outcome only when its quote ends its line and the first
 * line after it that is not blank is exactly one of its options, a plain
 * `Done`, or a plain `Declined`; the rest is the person's notes. A quote that
 * a drawer put in the prompt, with that answer or alone through discuss,
 * speaks to the item of the drawer that did so last, `proposed` holding them
 * oldest first: one of its options resolves it, and any other text replies to
 * it. A drawer's pick
 * from before its item was revised only replies, and one whose item was
 * resolved before the prompt was sent does nothing, so a drawer's pick never
 * falls through to another item with the same words.
 * A quote no drawer put there goes to the oldest item with that text that
 * takes what follows it, and otherwise replies to the one whose drawer last
 * put that quote in the prompt, or else to the oldest with that text. Only
 * questions and requests take answers and replies, snoozed ones included.
 */
export function readPrompt(text: string, items: readonly Item[], proposed: readonly Proposal[] = []): Read {
  const open = items
    .filter((item): item is Answerable => item.kind !== 'notice' && item.answer === undefined)
    .sort((one, other) => one.postedAt - other.postedAt)
  const quotes = [...new Set([...items.map(quoteOf), ...proposed.map(one => one.quote)])]
    .flatMap(quote => {
      const found: { at: number; end: number; quote: string }[] = []
      for (let at = text.indexOf(quote); at !== -1; at = text.indexOf(quote, at + 1)) {
        found.push({ at, end: at + quote.length, quote })
      }
      return found
    })
    .sort((one, other) => one.at - other.at)
  const answers: Given[] = []
  const outcomes: Finished[] = []
  const answered = new Set<string>()
  const replied = new Set<string>()
  const isOpen = (ref: string) => open.some(item => item.ref === ref) && !answered.has(ref)
  const isFree = (ref: string) => isOpen(ref) && !replied.has(ref)
  const isRequest = (ref: string) => items.some(item => item.ref === ref && item.kind === 'request')
  // Whether the text after an item's quote resolves it: one of a question's
  // options, or a request's outcome.
  const takes = (item: Answerable, said: string | undefined) =>
    item.kind === 'question' ? said !== undefined && item.choices.includes(said) : outcomeIn(item, said) !== undefined
  const resolve = (item: Answerable, said: string) => {
    answered.add(item.ref)
    if (item.kind === 'question') {
      answers.push({ question: item, letter: letterAt(item.choices.indexOf(said)) })
      return
    }
    const outcome = outcomeIn(item, said)
    if (outcome !== undefined) outcomes.push({ request: item, ...outcome })
  }
  for (const { end, quote } of quotes) {
    const after = text.slice(end, quotes.find(next => next.at >= end)?.at)
    const said = /^[ \t]*\n/.test(after) ? after.trim() : undefined
    // The drawer that put this text in the prompt: the last to put this
    // quote there with this answer or alone, through discuss, leaving out an
    // item an earlier quote in this prompt resolved. A request's drawer owns
    // its quote whatever follows it, since notes follow its outcome.
    const source = proposed.findLast(
      one =>
        one.quote === quote &&
        (isRequest(one.ref) || one.answer === said || one.answer === '') &&
        !answered.has(one.ref),
    )
    if (source !== undefined) {
      // An item resolved since its drawer put the quote there takes nothing,
      // and the quote falls through to no other item.
      const item = open.find(one => one.ref === source.ref)
      if (item === undefined) continue
      if (source.isStale !== true && said !== undefined && takes(item, said)) {
        resolve(item, said)
      } else if (isFree(source.ref)) {
        replied.add(source.ref)
      }
      continue
    }
    const offering = open.find(item => quoteOf(item) === quote && isOpen(item.ref) && takes(item, said))
    if (offering !== undefined && said !== undefined) {
      resolve(offering, said)
      continue
    }
    const intended = proposed.findLast(one => one.quote === quote && isFree(one.ref))
    const replying = intended?.ref ?? open.find(one => quoteOf(one) === quote && isFree(one.ref))?.ref
    if (replying !== undefined) replied.add(replying)
  }
  return { answers, outcomes, replies: open.filter(item => replied.has(item.ref) && !answered.has(item.ref)) }
}

/** Names the subagent that posted an item, by its description and id when the session still lists it. */
function whoAsked(item: Answerable, agents: readonly AgentInfo[]): string {
  const agent = agents.find(one => one.id === item.agentId)
  return agent === undefined ? (item.agentId ?? '') : `${agent.description} (${agent.id})`
}

/** How a note names what the subagent did and what goes back: it asked for an answer, or requested work for an outcome. */
const wording = (item: Answerable) =>
  item.kind === 'question'
    ? { posted: 'asked', result: 'answer', noun: 'question' }
    : { posted: 'requested', result: 'outcome', noun: 'request' }

/** What the main loop reads beside a prompt that answers a subagent's item: whom to send it to. */
export const relayNote = (item: Answerable, agents: readonly AgentInfo[]) =>
  `The subagent ${whoAsked(item, agents)} ${wording(item).posted} ${quoteOf(item)}; send it the ${wording(item).result} this prompt gives.`

/** What the main loop reads beside a prompt that replies to a subagent's item: whom to send the reply to. */
export const replyNote = (item: Answerable, agents: readonly AgentInfo[]) =>
  `The subagent ${whoAsked(item, agents)} ${wording(item).posted} ${quoteOf(item)}; send it the reply this prompt gives, which leaves the ${wording(item).noun} open.`

/** The subagent that posted an item, while it can still read a message; undefined sends the line to the main loop. */
export function liveAgent(item: Answerable, agents: readonly AgentInfo[]): string | undefined {
  const agent = agents.find(one => one.id === item.agentId)
  return agent !== undefined && LIVE.has(agent.status) ? agent.id : undefined
}

/** What the main loop receives: the answer or outcome, and the subagent to relay it to when one posted the item. */
export function forMainLoop(text: string, item: Answerable, agents: readonly AgentInfo[]): string {
  if (item.agentId === undefined) return text
  const { posted, result } = wording(item)
  return `${text}\n\nThe subagent ${whoAsked(item, agents)} ${posted} this, and the ${result} could not reach it; send it this ${result}, or act on it yourself.`
}

/** Where a session's items live: `project inbox`'s store, found by the CLI's own rule. */
export function storeOf(env: Env, session: string): string | undefined {
  const home = env('HOME')
  const base = env('XDG_STATE_HOME') ?? (home === undefined ? undefined : `${home}/.local/state`)
  return base === undefined ? undefined : `${base}/projector/inbox/${session}`
}

/**
 * What the store's directory holds: each item file's name, size and
 * time, leaving out the lock and any file being written. A file whose claim
 * the panel itself renews counts by name alone, so the panel's own renewal
 * never makes it read the store again.
 */
export function signatureOf(entries: readonly FsEntry[], renewing: ReadonlySet<string>): string {
  return entries
    .filter(entry => entry.kind === 'file' && !entry.name.startsWith('.'))
    .map(entry => (renewing.has(refOf(entry.name)) ? entry.name : `${entry.name} ${entry.size} ${entry.mtimeMs}`))
    .sort()
    .join('\n')
}

/** The reference an item file is named for, the CLI having quoted it; empty for any other file. */
function refOf(name: string): string {
  if (!name.endsWith('.json')) return ''
  try {
    return decodeURIComponent(name.slice(0, -'.json'.length))
  } catch {
    return ''
  }
}

/** A short age: `40s`, `5m`, `2h`, `3d`. */
export const span = (seconds: number) =>
  seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m`
    : seconds < 86_400 ? `${Math.floor(seconds / 3600)}h` : `${Math.floor(seconds / 86_400)}d`

const UNSEEN_NUMBER =
  'The panel never shows an item’s number, so name the item to the person by what it asks, tells, or requests, never by its number.'

/** A tool's description: its own lines, then the sentence every inbox tool ends with about an item's number. */
const described = (lines: readonly string[]) => [...lines, UNSEEN_NUMBER].join(' ')

export const ASK_DESCRIPTION = described([
  'Ask the person a question without stopping: it appears in Projector’s panel above their prompt, and you keep working.',
  'Ask in one short line of at most 120 characters, and put what they need to decide in `detail`.',
  'Always recommend an answer, and write it and each option as a full answer they can pick as it stands.',
  'Name the pull request the question is about in `pr`, as owner/repo#number.',
  'Keep working on anything the answer does not block. The call returns the question’s number, such as 3.',
  'When the person picks an option, a message that starts `ANSWER 3.<letter>` arrives later, or `DISMISSED 3` when they dismiss it.',
  'When they restore a question after you heard `DISMISSED 3`, `RESTORED 3` arrives, and the question is open again.',
  'When they reply in their own words instead, the reply arrives as their prompt, quoting the question, and the question stays open:',
  'answer what they asked, then withdraw the question if the reply settles it, revise it if it was unclear or the choice has changed,',
  'or leave it open while it still needs them.',
  'When the person needs to do something only they can do, use `request` instead.',
])

export const ASK_SCHEMA = {
  type: 'object',
  properties: {
    question: { type: 'string', description: 'The question, in one line of at most 120 characters.' },
    recommendation: { type: 'string', description: 'The answer you recommend, written in full; it is answer a.' },
    options: {
      type: 'array',
      items: { type: 'string' },
      maxItems: 5,
      description: 'Up to five other answers, each written in full, lettered b, c, and on in this order.',
    },
    detail: {
      type: 'string',
      description: 'What the person needs to decide, as Markdown; leave it out when the question says it all.',
    },
    pr: { type: 'string', description: 'The pull request the question is about, as owner/repo#number.' },
  },
  required: ['question', 'recommendation'],
  additionalProperties: false,
}

const isText = (value: unknown): value is string => typeof value === 'string'

/**
 * The arguments of `project inbox ask` for one call of the tool, or why
 * the call cannot be one. Each value rides in its flag's `=` form and the
 * question after `--`, so text that starts with `-` stays text.
 */
export function askArguments(
  input: Readonly<Record<string, unknown>>,
  session: string,
  agentId: string | undefined,
): string[] | { error: string } {
  const { question, recommendation, options = [], detail, pr } = input
  if (!isText(question)) return { error: '`question` must be a string' }
  if (!isText(recommendation)) return { error: '`recommendation` must be a string' }
  if (!Array.isArray(options) || !options.every(isText)) return { error: '`options` must be a list of strings' }
  if (detail !== undefined && !isText(detail)) return { error: '`detail` must be a string' }
  if (pr !== undefined && !isText(pr)) return { error: '`pr` must be a string such as owner/repo#123' }
  return [
    'inbox',
    'ask',
    `--session=${session}`,
    `--recommend=${recommendation}`,
    ...options.map(option => `--option=${option}`),
    ...postedFlags(detail, pr, agentId),
    '--',
    question,
  ]
}

export const TELL_DESCRIPTION = described([
  'Tell the person something without stopping: it appears in Projector’s panel above their prompt until they dismiss it.',
  'Use it for the answer to a question they asked you, or for something you did that they should know about.',
  'Write one short line of at most 120 characters, and put the rest in `detail`.',
  'Name the pull request it is about in `pr`, as owner/repo#number.',
  'Nothing comes back to you; when you need the person to decide something, use `ask` instead,',
  'and when they need to do something only they can do, use `request`.',
])

export const TELL_SCHEMA = {
  type: 'object',
  properties: {
    notice: { type: 'string', description: 'What to tell the person, in one line of at most 120 characters.' },
    detail: {
      type: 'string',
      description: 'The rest of what they should read, as Markdown; leave it out when the line says it all.',
    },
    pr: { type: 'string', description: 'The pull request the notice is about, as owner/repo#number.' },
  },
  required: ['notice'],
  additionalProperties: false,
}

/** The arguments of `project inbox tell` for one call of the tool, or why the call cannot be one. */
export function tellArguments(
  input: Readonly<Record<string, unknown>>,
  session: string,
  agentId: string | undefined,
): string[] | { error: string } {
  const { notice, detail, pr } = input
  if (!isText(notice)) return { error: '`notice` must be a string' }
  if (detail !== undefined && !isText(detail)) return { error: '`detail` must be a string' }
  if (pr !== undefined && !isText(pr)) return { error: '`pr` must be a string such as owner/repo#123' }
  return ['inbox', 'tell', `--session=${session}`, ...postedFlags(detail, pr, agentId), '--', notice]
}

export const REQUEST_DESCRIPTION = described([
  'Ask the person to do something only they can do, such as running a command you may not run, signing in to a service,',
  'or checking a change on their own device: it appears in Projector’s panel above their prompt, and you keep working.',
  'Write the request in one short line of at most 120 characters, and put the steps they follow in `detail`.',
  'Name the pull request it is about in `pr`, as owner/repo#number.',
  'Give the choices they pick from when they finish in `options`, each a short answer that fits the work: for a test,',
  '`Looks good` and `Some feedback`. Each one marks the request done and tells you which they chose, and they can add',
  'notes under it. Leave `options` out to offer a single `Done`. They can always decline the request instead.',
  'Give `start` only when you must do something before they can begin, such as installing a build on their phone:',
  'one line saying what you will do. Leave it out otherwise, and the request goes straight to done or declined.',
  'Do the slow part before you post the request, such as building the app, so the start action only installs and',
  'launches it and the person is not left waiting once they press start.',
  'With `start`, the person presses start when they are ready, and a message that starts `STARTED 3` arrives quoting',
  'the start action. Do the start action only after it arrives, and do exactly what it quotes and nothing more.',
  'Then mark the request ready with `ready`, or, when you cannot finish, `block` it with what they should do first.',
  'Keep working on anything the result does not block. The call returns the request’s number, such as 3.',
  'When they finish, their outcome arrives later as their prompt, quoting the request with the option they chose and',
  'any notes under it, or as a message that starts `DONE 3` and names the option. When they decline it, a message that starts `DECLINED 3` arrives. When they restore a request after you heard',
  '`DECLINED 3`, `RESTORED 3` arrives, and the request is open again. When they reply in their own words instead, the',
  'reply arrives as their prompt, quoting the request, and the request stays open.',
  'When you need the person to decide something rather than do it, use `ask` instead.',
])

export const REQUEST_SCHEMA = {
  type: 'object',
  properties: {
    request: { type: 'string', description: 'What the person should do, in one line of at most 120 characters.' },
    detail: { type: 'string', description: 'The steps they follow, as Markdown.' },
    options: {
      type: 'array',
      items: { type: 'string' },
      maxItems: 5,
      description:
        'Up to five choices they pick from when they finish, each marking the request done, such as `Looks good` and `Some feedback` for a test; leave it out to offer a single `Done`.',
    },
    start: {
      type: 'string',
      description:
        'What you will do once they press start, in one line, such as installing a build; leave it out when there is nothing to prepare.',
    },
    pr: { type: 'string', description: 'The pull request the request is about, as owner/repo#number.' },
  },
  required: ['request'],
  additionalProperties: false,
}

/** The arguments of `project inbox request` for one call of the tool, or why the call cannot be one. */
export function requestArguments(
  input: Readonly<Record<string, unknown>>,
  session: string,
  agentId: string | undefined,
): string[] | { error: string } {
  const { request, detail, start, pr, options = [] } = input
  if (!isText(request)) return { error: '`request` must be a string' }
  if (detail !== undefined && !isText(detail)) return { error: '`detail` must be a string' }
  if (start !== undefined && !isText(start)) return { error: '`start` must be a string' }
  if (pr !== undefined && !isText(pr)) return { error: '`pr` must be a string such as owner/repo#123' }
  if (!Array.isArray(options) || !options.every(isText)) return { error: '`options` must be a list of strings' }
  return [
    'inbox',
    'request',
    `--session=${session}`,
    ...options.map(option => `--option=${option}`),
    ...(start === undefined || start === '' ? [] : [`--start=${start}`]),
    ...postedFlags(detail, pr, agentId),
    '--',
    request,
  ]
}

export const READY_DESCRIPTION = described([
  'Mark a request you posted ready once you have done its start action, after the message that starts `STARTED 3`',
  'arrived: Projector’s panel tells the person they can begin. Name it by the number `request` returned.',
  'Nothing comes back to you until they finish it, as a message that starts `DONE 3` or `DECLINED 3`.',
])

export const READY_SCHEMA = {
  type: 'object',
  properties: {
    number: { type: 'integer', minimum: 1, description: 'The request’s number, as `request` returned it, such as 3.' },
  },
  required: ['number'],
  additionalProperties: false,
}

export const BLOCK_DESCRIPTION = described([
  'Say you could not do a started request’s start action, and what the person should do before they press retry,',
  'such as “The phone is locked. Unlock it and press retry.” Name it by the number `request` returned.',
  'When they press retry, a new message that starts `STARTED 3` arrives, and you try the start action again.',
])

export const BLOCK_SCHEMA = {
  type: 'object',
  properties: {
    number: { type: 'integer', minimum: 1, description: 'The request’s number, as `request` returned it, such as 3.' },
    reason: {
      type: 'string',
      description: 'What the person should do before they press retry, in one line of at most 120 characters.',
    },
  },
  required: ['number', 'reason'],
  additionalProperties: false,
}

/** The arguments of `project inbox ready` or `block` for one call of its tool, or why the call cannot be one. */
export function reportArguments(
  input: Readonly<Record<string, unknown>>,
  session: string,
): string[] | { error: string } {
  const { number, reason } = input
  if (!isNumber(number)) return { error: '`number` must be the whole number `request` returned, such as 3' }
  if (reason !== undefined && !isText(reason)) return { error: '`reason` must be a string' }
  return reason === undefined
    ? ['inbox', 'ready', String(number), '--session', session, '--json']
    : ['inbox', 'block', String(number), `--reason=${reason}`, '--session', session, '--json']
}

export const WITHDRAW_DESCRIPTION = described([
  'Withdraw a question, notice, or request you posted that no longer needs the person,',
  'such as a question their reply settled or news that has gone stale.',
  'Name it by the number `ask`, `tell`, or `request` returned. It leaves Projector’s panel at once.',
  'Nothing comes back to you: a withdrawn question or request sends no `DISMISSED` or `DECLINED` message.',
])

/** The schema of a tool that takes one item, by the number `ask`, `tell`, or `request` returned. */
const ITEM_SCHEMA = {
  type: 'object',
  properties: {
    number: {
      type: 'integer',
      minimum: 1,
      description: 'The item’s number, as `ask`, `tell`, or `request` returned it, such as 3.',
    },
  },
  required: ['number'],
  additionalProperties: false,
}

export const WITHDRAW_SCHEMA = ITEM_SCHEMA

/** Whether a tool's `number` names an item: a whole number from 1. */
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 1

/** The arguments of a `project inbox` command on one item for one call of its tool, or why the call cannot be one. */
const itemArguments =
  (command: 'withdraw' | 'restore') =>
  (input: Readonly<Record<string, unknown>>, session: string): string[] | { error: string } => {
    const { number } = input
    if (!isNumber(number)) {
      return { error: '`number` must be the whole number `ask`, `tell`, or `request` returned, such as 3' }
    }
    return ['inbox', command, String(number), '--session', session, '--json']
  }

/** The arguments of `project inbox withdraw` for one call of the tool, or why the call cannot be one. */
export const withdrawArguments = itemArguments('withdraw')

export const RESTORE_DESCRIPTION = described([
  'Restore a question, notice, or request you posted that the person dismissed, declined, or snoozed: it reopens in',
  'Projector’s panel under the same number, with the same text. Use it only when the person says a dismissal was a',
  'mistake or asks for the item back, never on your own judgement, and never post a dismissed item again as a new one.',
  'Name it by the number `ask`, `tell`, or `request` returned. A restored question’s answer arrives as before, as a',
  'message that starts `ANSWER 3.<letter>` or `DISMISSED 3`, and a restored request’s outcome as one that starts',
  '`DONE 3` or `DECLINED 3`.',
])

export const RESTORE_SCHEMA = ITEM_SCHEMA

/** The arguments of `project inbox restore` for one call of the tool, or why the call cannot be one. */
export const restoreArguments = itemArguments('restore')

export const REVISE_DESCRIPTION = described([
  'Revise a question you asked or a request you posted that is still open, keeping its number, when the person’s reply',
  'shows it was unclear or the choice or the work has changed. Name it by the number `ask` or `request` returned, and',
  'give only what changes: for a question, the question, its detail, the recommendation, or the other options, which',
  '`options` replaces as a whole; for a request, the request, its detail, its options, which `options` replaces as a',
  'whole and an empty list removes, or its start action, where an empty `start` removes it. A revised request returns to open, so a start the person pressed for its old text needs a new press.',
  'Projector’s panel redraws it at once, and a question’s answer still arrives as a message that starts',
  '`ANSWER 3.<letter>` or `DISMISSED 3`, a request’s outcome as one that starts `DONE 3` or `DECLINED 3`.',
])

export const REVISE_SCHEMA = {
  type: 'object',
  properties: {
    number: {
      type: 'integer',
      minimum: 1,
      description: 'The item’s number, as `ask` or `request` returned it, such as 3.',
    },
    question: { type: 'string', description: 'The new question, in one line of at most 120 characters.' },
    request: { type: 'string', description: 'The new request, in one line of at most 120 characters.' },
    start: { type: 'string', description: 'The request’s new start action, in one line; an empty string removes it.' },
    recommendation: {
      type: 'string',
      description: 'The new answer you recommend, written in full; it is answer a.',
    },
    options: {
      type: 'array',
      items: { type: 'string' },
      maxItems: 5,
      description:
        'For a question, every other answer, up to five, each written in full and lettered b, c, and on in this order; an empty list leaves only the recommendation. For a request, every option, up to five, in order; an empty list removes them, so the request offers a single Done.',
    },
    detail: {
      type: 'string',
      description: 'The new detail, as Markdown; an empty string clears it.',
    },
  },
  required: ['number'],
  additionalProperties: false,
}

/**
 * The arguments of `project inbox revise` for one call of the tool, or why
 * the call cannot be one. Each value rides in its flag's `=` form, so text
 * that starts with `-` stays text, and an empty `detail` clears the detail.
 */
export function reviseArguments(
  input: Readonly<Record<string, unknown>>,
  session: string,
): string[] | { error: string } {
  const { number, question, request, start, recommendation, options, detail } = input
  if (!isNumber(number)) return { error: '`number` must be the whole number `ask` or `request` returned, such as 3' }
  if (question !== undefined && !isText(question)) return { error: '`question` must be a string' }
  if (request !== undefined && !isText(request)) return { error: '`request` must be a string' }
  if (start !== undefined && !isText(start)) return { error: '`start` must be a string' }
  if (recommendation !== undefined && !isText(recommendation)) return { error: '`recommendation` must be a string' }
  if (options !== undefined && (!Array.isArray(options) || !options.every(isText))) {
    return { error: '`options` must be a list of strings' }
  }
  if (detail !== undefined && !isText(detail)) return { error: '`detail` must be a string' }
  if ([question, request, start, recommendation, options, detail].every(value => value === undefined)) {
    return { error: 'give what changes: `question`, `request`, `start`, `detail`, `recommendation`, or `options`' }
  }
  return [
    'inbox',
    'revise',
    String(number),
    '--session',
    session,
    ...(question === undefined ? [] : [`--question=${question}`]),
    ...(request === undefined ? [] : [`--request=${request}`]),
    ...(start === undefined ? [] : [`--start=${start}`]),
    ...(recommendation === undefined ? [] : [`--recommend=${recommendation}`]),
    ...(options === undefined ? [] : options.length === 0 ? ['--no-options'] : options.map(option => `--option=${option}`)),
    ...(detail === undefined ? [] : [`--detail=${detail}`]),
    '--json',
  ]
}

/** The flags every posting tool passes when it has a value: the detail, the pull request, and the subagent. */
const postedFlags = (detail: string | undefined, pr: string | undefined, agentId: string | undefined) => [
  ...(detail === undefined || detail === '' ? [] : [`--detail=${detail}`]),
  ...(pr === undefined || pr === '' ? [] : [`--pr=${pr}`]),
  ...(agentId === undefined ? [] : [`--agent=${agentId}`]),
]
