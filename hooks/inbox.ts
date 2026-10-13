import type { AgentInfo, FsEntry } from 'claude-code'

import type { Item, Question } from './types'
import type { Env } from './parse'

/** How long a delivered item stays in the band. */
export const RESOLVED_MS = 30_000

/** How long a claim lasts without renewal, as `project inbox claim` counts it. */
export const CLAIM_MS = 60_000

const LIVE: ReadonlySet<AgentInfo['status']> = new Set(['pending', 'running', 'waiting', 'idle'])

export const letterAt = (index: number) => String.fromCharCode('a'.charCodeAt(0) + index)

/** The letter of a typed answer: the one after the last choice. */
export const typedLetter = (question: Question) => letterAt(question.choices.length)

/** Whether an item is snoozed at a time: hidden until its `snoozedUntil`. */
const isSnoozedAt = (item: Item, time: number) => item.snoozedUntil !== undefined && time < item.snoozedUntil

/** Whether the agent that posted an item withdrew it. */
const isWithdrawn = (item: Item) => item.answer !== undefined && 'withdrawn' in item.answer

/**
 * Whether the band shows an item: not withdrawn, not snoozed, and open, being
 * delivered, or delivered within the last 30 seconds.
 */
export const isShownAt = (item: Item, time: number) =>
  !isWithdrawn(item) &&
  !isSnoozedAt(item, time) &&
  (item.deliveredAt === undefined || time - item.deliveredAt < RESOLVED_MS)

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

/** One answer a sent prompt gives: the letter of the option it picks. */
export type Given = { question: Question; letter: string }

/**
 * What a question's drawer put in the prompt: the question's quote as it read
 * then, and the option chosen, or an empty answer for discuss. `isStale`
 * marks one put there before the question was revised.
 */
export type Proposal = { ref: string; quote: string; answer: string; isStale?: boolean }

/** What a sent prompt says to the open questions: the answers it gives, and the questions it replies to. */
export type Read = { answers: Given[]; replies: Question[] }

/**
 * Reads a sent prompt as the band writes it, each quote speaking to one
 * question. A quoted open question takes an answer only when its quote ends
 * its line and the text after it, up to the next quoted item or the prompt's
 * end, is one of its options, whatever blank lines and spaces surround it:
 * what an option's number puts in the prompt. When open questions share the
 * text, the quote goes to the one whose drawer last put that answer, or the
 * quote alone, in the prompt, `proposed` holding them oldest first, and
 * otherwise to the oldest that offers it. Any other text after a quote
 * replies to one question: the one whose drawer last put that quote in the
 * prompt, and otherwise the oldest with that text. A quote and an answer
 * that a drawer put there before its question was revised answer nothing, so
 * a pick never falls through to another question with the old text: they
 * reply to the revised question. Only questions take answers and replies,
 * snoozed ones included.
 */
export function readPrompt(text: string, items: readonly Item[], proposed: readonly Proposal[] = []): Read {
  const open = items
    .filter((item): item is Question => item.kind === 'question' && item.answer === undefined)
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
  const answered = new Set<string>()
  const replied = new Set<string>()
  const isFree = (ref: string) => open.some(question => question.ref === ref) && !answered.has(ref) && !replied.has(ref)
  for (const { end, quote } of quotes) {
    const after = text.slice(end, quotes.find(next => next.at >= end)?.at)
    const said = /^[ \t]*\n/.test(after) ? after.trim() : undefined
    const stale = proposed.findLast(one => one.isStale === true && one.quote === quote && one.answer === said)
    if (stale !== undefined) {
      if (isFree(stale.ref)) replied.add(stale.ref)
      continue
    }
    const offering = open.filter(
      question =>
        quoteOf(question) === quote &&
        !answered.has(question.ref) &&
        said !== undefined &&
        question.choices.includes(said),
    )
    const drawer = proposed.findLast(
      one =>
        one.isStale !== true &&
        (one.answer === said || one.answer === '') &&
        offering.some(question => question.ref === one.ref),
    )
    const question = offering.find(one => one.ref === drawer?.ref) ?? offering[0]
    if (question !== undefined && said !== undefined) {
      answered.add(question.ref)
      answers.push({ question, letter: letterAt(question.choices.indexOf(said)) })
      continue
    }
    const intended = proposed.findLast(one => one.quote === quote && isFree(one.ref))
    const replying = intended?.ref ?? open.find(one => quoteOf(one) === quote && isFree(one.ref))?.ref
    if (replying !== undefined) replied.add(replying)
  }
  return { answers, replies: open.filter(question => replied.has(question.ref) && !answered.has(question.ref)) }
}

/** Names the subagent that asked, by its description and id when the session still lists it. */
function whoAsked(question: Question, agents: readonly AgentInfo[]): string {
  const agent = agents.find(one => one.id === question.agentId)
  return agent === undefined ? (question.agentId ?? '') : `${agent.description} (${agent.id})`
}

/** What the main loop reads beside a prompt that answers a subagent's question: whom to send it to. */
export const relayNote = (question: Question, agents: readonly AgentInfo[]) =>
  `The subagent ${whoAsked(question, agents)} asked ${quoteOf(question)}; send it the answer this prompt gives.`

/** What the main loop reads beside a prompt that replies to a subagent's question: whom to send the reply to. */
export const replyNote = (question: Question, agents: readonly AgentInfo[]) =>
  `The subagent ${whoAsked(question, agents)} asked ${quoteOf(question)}; send it the reply this prompt gives, which leaves the question open.`

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
  'Keep working on anything the answer does not block. The call returns the question’s number, such as 3.',
  'When the person picks an option, a message that starts `ANSWER 3.<letter>` arrives later, or `DISMISSED 3` when they dismiss it.',
  'When they reply in their own words instead, the reply arrives as their prompt, quoting the question, and the question stays open:',
  'answer what they asked, then withdraw the question if the reply settles it, revise it if it was unclear or the choice has changed,',
  'or leave it open while it still needs them.',
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

export const WITHDRAW_DESCRIPTION = [
  'Withdraw a question or notice you posted that no longer needs the person,',
  'such as a question their reply settled or news that has gone stale.',
  'Name it by the number `ask` or `tell` returned. It leaves Projector’s band at once.',
  'Nothing comes back to you: a withdrawn question sends no `DISMISSED` message.',
].join(' ')

export const WITHDRAW_SCHEMA = {
  type: 'object',
  properties: {
    number: {
      type: 'integer',
      minimum: 1,
      description: 'The item’s number, as `ask` or `tell` returned it, such as 3.',
    },
  },
  required: ['number'],
  additionalProperties: false,
}

/** Whether a tool's `number` names an item: a whole number from 1. */
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 1

/** The arguments of `project inbox withdraw` for one call of the tool, or why the call cannot be one. */
export function withdrawArguments(
  input: Readonly<Record<string, unknown>>,
  session: string,
): string[] | { error: string } {
  const { number } = input
  if (!isNumber(number)) return { error: '`number` must be the whole number `ask` or `tell` returned, such as 3' }
  return ['inbox', 'withdraw', String(number), '--session', session, '--json']
}

export const REVISE_DESCRIPTION = [
  'Revise a question you asked that is still open, keeping its number, when the person’s reply shows it was unclear',
  'or the choice has changed. Name it by the number `ask` returned, and give only what changes:',
  'the question, its detail, the recommendation, or the other options, which `options` replaces as a whole.',
  'Projector’s band redraws it at once, and its answer still arrives as a message that starts',
  '`ANSWER 3.<letter>` or `DISMISSED 3`.',
].join(' ')

export const REVISE_SCHEMA = {
  type: 'object',
  properties: {
    number: { type: 'integer', minimum: 1, description: 'The question’s number, as `ask` returned it, such as 3.' },
    question: { type: 'string', description: 'The new question, in one line of at most 120 characters.' },
    recommendation: {
      type: 'string',
      description: 'The new answer you recommend, written in full; it is answer a.',
    },
    options: {
      type: 'array',
      items: { type: 'string' },
      maxItems: 5,
      description:
        'Every other answer, up to five, each written in full and lettered b, c, and on in this order; an empty list leaves only the recommendation.',
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
  const { number, question, recommendation, options, detail } = input
  if (!isNumber(number)) return { error: '`number` must be the whole number `ask` returned, such as 3' }
  if (question !== undefined && !isText(question)) return { error: '`question` must be a string' }
  if (recommendation !== undefined && !isText(recommendation)) return { error: '`recommendation` must be a string' }
  if (options !== undefined && (!Array.isArray(options) || !options.every(isText))) {
    return { error: '`options` must be a list of strings' }
  }
  if (detail !== undefined && !isText(detail)) return { error: '`detail` must be a string' }
  if ([question, recommendation, options, detail].every(value => value === undefined)) {
    return { error: 'give what changes: `question`, `detail`, `recommendation`, or `options`' }
  }
  return [
    'inbox',
    'revise',
    String(number),
    '--session',
    session,
    ...(question === undefined ? [] : [`--question=${question}`]),
    ...(recommendation === undefined ? [] : [`--recommend=${recommendation}`]),
    ...(options === undefined ? [] : options.length === 0 ? ['--no-options'] : options.map(option => `--option=${option}`)),
    ...(detail === undefined ? [] : [`--detail=${detail}`]),
    '--json',
  ]
}

/** The flags both posting tools pass when they have a value: the detail, the pull request, and the subagent. */
const postedFlags = (detail: string | undefined, pr: string | undefined, agentId: string | undefined) => [
  ...(detail === undefined || detail === '' ? [] : [`--detail=${detail}`]),
  ...(pr === undefined || pr === '' ? [] : [`--pr=${pr}`]),
  ...(agentId === undefined ? [] : [`--agent=${agentId}`]),
]
