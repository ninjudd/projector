import type { AgentInfo, FsEntry } from 'claude-code'

import type { Item, Question } from './types'
import type { Env } from './parse'

/** How long a delivered item stays in the band. */
export const RESOLVED_MS = 30_000

/** How long a claim lasts without renewal, as `project inbox claim` counts it. */
export const CLAIM_MS = 60_000

const LIVE: ReadonlySet<AgentInfo['status']> = new Set(['pending', 'running', 'waiting', 'idle'])

/**
 * Whether text holds anything shaped like a reference, `#337.3` or `q2`, so
 * that only such a prompt starts `project inbox context`, which decides.
 */
export const mayReference = (text: string) => /#\d+\.\d|\bq\d/.test(text)

export const letterAt = (index: number) => String.fromCharCode('a'.charCodeAt(0) + index)

/** The letter of a typed answer: the one after the last choice. */
export const typedLetter = (question: Question) => letterAt(question.choices.length)

/** Whether an item is snoozed at a time: hidden until its `snoozedUntil`. */
const isSnoozedAt = (item: Item, time: number) => item.snoozedUntil !== undefined && time < item.snoozedUntil

/**
 * Whether the band shows an item: not snoozed, and open, being delivered, or
 * delivered within the last 30 seconds.
 */
export const isShownAt = (item: Item, time: number) =>
  !isSnoozedAt(item, time) && (item.deliveredAt === undefined || time - item.deliveredAt < RESOLVED_MS)

/** The open items whose snooze ended after `since` and by `time`, which bring a hidden band back. */
export const wokeBetween = (items: readonly Item[], since: number, time: number) =>
  items.filter(
    item =>
      item.answer === undefined && item.snoozedUntil !== undefined && since < item.snoozedUntil && item.snoozedUntil <= time,
  )

/** Whether a question's answer waits for a deliverer: recorded, undelivered, and claimed by none that is live. */
export const needsDelivery = (item: Item, time: number): item is Question =>
  item.kind === 'question' &&
  item.answer !== undefined &&
  item.deliveredAt === undefined &&
  (item.claim === undefined || time - item.claim.renewedAt >= CLAIM_MS)

/** What the band puts in the prompt to discuss an item: its subject, in quotes. */
export const quoteOf = (item: Item) => `"${item.subject}"`

/** What the band puts in the prompt for an answer: the quoted question, a blank line, and the answer's text. */
export const proposalOf = (question: Question, answer: string) => `${quoteOf(question)}\n\n${answer}`

/** One answer a sent prompt gives: a choice's letter, or the typed letter and its text. */
export type Given = { question: Question; letter: string; text?: string }

/** An answer a question's drawer put in the prompt. */
export type Proposal = { ref: string; answer: string }

/**
 * The answers a sent prompt gives, as the band wrote them. Each quoted
 * question that ends its line, as an option and discuss both leave it,
 * answers one open question with that text: the choice whose text is the
 * next line that is not blank, or else that line as a typed answer. Spaces
 * or tabs after the quote still end its line; any other text after it on
 * that line answers nothing. When open questions share the text, the quote
 * goes to the one whose drawer last put that same answer in the prompt,
 * `proposed` holding them oldest first, and otherwise to the oldest, so two
 * questions never take one answer. Only questions take answers, snoozed ones
 * included, so a quoted notice answers nothing either.
 */
export function answersIn(text: string, items: readonly Item[], proposed: readonly Proposal[] = []): Given[] {
  const open = items
    .filter((item): item is Question => item.kind === 'question' && item.answer === undefined)
    .sort((one, other) => one.postedAt - other.postedAt)
  const found: { at: number; given: Given }[] = []
  const taken = new Set<string>()
  for (const quote of new Set(open.map(quoteOf))) {
    for (let at = text.indexOf(quote); at !== -1; at = text.indexOf(quote, at + 1)) {
      const after = text.slice(at + quote.length)
      if (!/^[ \t]*\n/.test(after)) continue
      const said = (after.replace(/^\s*\n/, '').split('\n')[0] ?? '').trim()
      const asking = open.filter(question => quoteOf(question) === quote && !taken.has(question.ref))
      if (said === '') continue
      const drawer = proposed.findLast(one => one.answer === said && asking.some(question => question.ref === one.ref))
      const question = asking.find(one => one.ref === drawer?.ref) ?? asking[0]
      if (question === undefined) continue
      taken.add(question.ref)
      const index = question.choices.indexOf(said)
      const letter = index === -1 ? typedLetter(question) : letterAt(index)
      found.push({ at, given: index === -1 ? { question, letter, text: said } : { question, letter } })
    }
  }
  return found.sort((one, other) => one.at - other.at).map(one => one.given)
}

/** Names the subagent that asked, by its description and id when the session still lists it. */
function whoAsked(question: Question, agents: readonly AgentInfo[]): string {
  const agent = agents.find(one => one.id === question.agentId)
  return agent === undefined ? (question.agentId ?? '') : `${agent.description} (${agent.id})`
}

/** What the main loop reads beside a prompt that answers a subagent's question: whom to send it to. */
export const relayNote = (question: Question, agents: readonly AgentInfo[]) =>
  `The subagent ${whoAsked(question, agents)} asked ${quoteOf(question)}; send it the answer this prompt gives.`

/** The subagent that asked, while it can still read a message; undefined sends the answer to the main loop. */
export function liveAgent(question: Question, agents: readonly AgentInfo[]): string | undefined {
  const agent = agents.find(one => one.id === question.agentId)
  return agent !== undefined && LIVE.has(agent.status) ? agent.id : undefined
}

/** What the main loop receives: the answer, and the subagent to relay it to when one asked. */
export function forMainLoop(text: string, question: Question, agents: readonly AgentInfo[]): string {
  if (question.agentId === undefined) return text
  return `${text}\n\nThe subagent ${whoAsked(question, agents)} asked this, and the answer could not reach it; send it this answer, or act on it yourself.`
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
 * the band itself renews counts by name alone, so the band's own renewal
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

export const ASK_DESCRIPTION = [
  'Ask the person a question without stopping: it appears in Projector’s band above their prompt, and you keep working.',
  'Ask in one short line of at most 120 characters, and put what they need to decide in `detail`.',
  'Always recommend an answer, and write it and each option as a full answer they can pick as it stands.',
  'Name the pull request the question is about in `pr`, as owner/repo#number.',
  'Keep working on anything the answer does not block. The call returns the question’s reference, such as #337.3,',
  'and the answer arrives later as a message that starts `ANSWER #337.3.<letter>` or `DISMISSED #337.3`.',
].join(' ')

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

export const TELL_DESCRIPTION = [
  'Tell the person something without stopping: it appears in Projector’s band above their prompt until they dismiss it.',
  'Use it for the answer to a question they asked you, or for something you did that they should know about.',
  'Write one short line of at most 120 characters, and put the rest in `detail`.',
  'Name the pull request it is about in `pr`, as owner/repo#number.',
  'Nothing comes back to you; when you need the person to decide something, use `ask` instead.',
].join(' ')

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

/** The flags both tools pass when they have a value: the detail, the pull request, and the subagent. */
const postedFlags = (detail: string | undefined, pr: string | undefined, agentId: string | undefined) => [
  ...(detail === undefined || detail === '' ? [] : [`--detail=${detail}`]),
  ...(pr === undefined || pr === '' ? [] : [`--pr=${pr}`]),
  ...(agentId === undefined ? [] : [`--agent=${agentId}`]),
]
