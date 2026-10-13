import type { AgentInfo, FsEntry } from 'claude-code'

import type { Question } from './types'
import type { Env } from './parse'

/** How long a delivered question stays in the band. */
export const RESOLVED_MS = 60_000

/** How long a claim lasts without renewal, as `project question claim` counts it. */
export const CLAIM_MS = 60_000

const LIVE: ReadonlySet<AgentInfo['status']> = new Set(['pending', 'running', 'waiting', 'idle'])

/**
 * Whether text holds anything shaped like a reference, `#337.3` or `q2`, so
 * that only such a prompt starts `project question context`, which decides.
 */
export const mayReference = (text: string) => /#\d+\.\d|\bq\d/.test(text)

export const letterAt = (index: number) => String.fromCharCode('a'.charCodeAt(0) + index)

/** The letter of a typed answer: the one after the last choice. */
export const typedLetter = (question: Question) => letterAt(question.choices.length)

/** Whether the band still shows a question: open, being delivered, or delivered under a minute ago. */
export const isShownAt = (question: Question, time: number) =>
  question.deliveredAt === undefined || time - question.deliveredAt < RESOLVED_MS

/** Whether an answer waits for a deliverer: recorded, undelivered, and claimed by none that is live. */
export const needsDelivery = (question: Question, time: number) =>
  question.answer !== undefined &&
  question.deliveredAt === undefined &&
  (question.claim === undefined || time - question.claim.renewedAt >= CLAIM_MS)

/** What the band puts in the prompt to discuss a question: its text, in quotes. */
export const quoteOf = (question: Question) => `"${question.question}"`

/** What the band puts in the prompt for an answer: the quoted question, a blank line, and the answer's text. */
export const proposalOf = (question: Question, answer: string) => `${quoteOf(question)}\n\n${answer}`

/** One answer a sent prompt gives: a choice's letter, or the typed letter and its text. */
export type Given = { question: Question; letter: string; text?: string }

/**
 * The answers a sent prompt gives, as the band wrote them. Each quoted
 * question that ends its line answers one open question with that text: the
 * choice whose text is the next line that is not blank, or else that line as
 * a typed answer. A quote goes first to a question whose drawer filled the
 * prompt, `proposed` naming them in the order they filled it, and otherwise
 * to the oldest open question with that text, so two questions that share
 * their text never take one answer. A quoted question with more on its line,
 * as discuss leaves it, answers nothing.
 */
export function answersIn(text: string, questions: readonly Question[], proposed: readonly string[] = []): Given[] {
  const rank = (question: Question) => {
    const at = proposed.indexOf(question.ref)
    return at === -1 ? proposed.length : at
  }
  const open = questions
    .filter(question => question.answer === undefined)
    .sort((one, other) => rank(one) - rank(other) || one.askedAt - other.askedAt)
  const quoted = new Set<number>()
  return open.flatMap(question => {
    const lead = `${quoteOf(question)}\n`
    let at = text.indexOf(lead)
    while (at !== -1 && quoted.has(at)) at = text.indexOf(lead, at + 1)
    if (at === -1) return []
    quoted.add(at)
    const said = (text.slice(at + lead.length).replace(/^\s*\n/, '').split('\n')[0] ?? '').trim()
    if (said === '') return []
    const index = question.choices.indexOf(said)
    return [index === -1 ? { question, letter: typedLetter(question), text: said } : { question, letter: letterAt(index) }]
  })
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

/** Where a session's questions live: `project question`'s store, found by the CLI's own rule. */
export function storeOf(env: Env, session: string): string | undefined {
  const home = env('HOME')
  const base = env('XDG_STATE_HOME') ?? (home === undefined ? undefined : `${home}/.local/state`)
  return base === undefined ? undefined : `${base}/projector/questions/${session}`
}

/**
 * What the store's directory holds: each question file's name, size and
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

/** The reference a question file is named for, the CLI having quoted it; empty for any other file. */
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
 * The arguments of `project question ask` for one call of the tool, or why
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
    'question',
    'ask',
    `--session=${session}`,
    `--recommend=${recommendation}`,
    ...options.map(option => `--option=${option}`),
    ...(detail === undefined || detail === '' ? [] : [`--detail=${detail}`]),
    ...(pr === undefined || pr === '' ? [] : [`--pr=${pr}`]),
    ...(agentId === undefined ? [] : [`--agent=${agentId}`]),
    '--',
    question,
  ]
}
