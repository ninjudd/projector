import type { AgentInfo } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import {
  answersIn,
  askArguments,
  forMainLoop,
  isShownAt,
  liveAgent,
  mayReference,
  needsDelivery,
  proposalOf,
  quoteOf,
  relayNote,
  signatureOf,
  span,
  storeOf,
  typedLetter,
} from './questions'
import type { Question } from './types'

const NOW = Date.UTC(2026, 9, 12, 12, 0)

const question = (fields: Partial<Question> = {}): Question => ({
  ref: '#7.1',
  number: 1,
  pr: 'o/r#7',
  askedAt: NOW - 60_000,
  question: 'Decline the finding?',
  detail: '',
  choices: ['Decline it', 'Add a budget'],
  session: 's1',
  ...fields,
})

const agent = (status: AgentInfo['status']): AgentInfo =>
  ({ id: 'agent-1', description: 'Fix #7', type: 'general-purpose', status }) as AgentInfo

const file = (name: string, mtimeMs: number) => ({ name, kind: 'file' as const, size: 10, mtimeMs, isLink: false })

test('only text shaped like a reference starts the CLI', () => {
  for (const text of ['go with #337.3.b', 'Let’s do q2.a.', 'o/r#12.1', 'q1']) expect(mayReference(text)).toBe(true)
  for (const text of ['rerun the 2 failing tests', 'see #337', 'eq2 holds', 'Q2']) expect(mayReference(text)).toBe(false)
})

test('letters run from the recommendation to the typed answer', () => {
  expect(typedLetter(question())).toBe('c')
})

test('the prompt carries a question in quotes, and an answer on a line of its own', () => {
  expect(quoteOf(question())).toBe('"Decline the finding?"')
  expect(proposalOf(question(), 'Add a budget')).toBe('"Decline the finding?"\n\nAdd a budget')
})

test('a sent prompt answers each question whose quote ends its line', () => {
  const open = [question(), question({ ref: '#7.2', question: 'Rebase first?', choices: ['Merge main', 'Rebase'] })]
  expect(answersIn('"Decline the finding?"\n\nAdd a budget\n', open)).toEqual([{ question: open[0], letter: 'b' }])
  expect(answersIn('Both:\n"Decline the finding?"\nDecline it\n"Rebase first?"\n\nkeep it', open)).toEqual([
    { question: open[0], letter: 'a' },
    { question: open[1], letter: 'c', text: 'keep it' },
  ])
  // Discussing a question, or leaving its answer empty, answers nothing.
  expect(answersIn('"Decline the finding?" what does the cap cover?', open)).toEqual([])
  expect(answersIn('"Decline the finding?"\n\n', open)).toEqual([])
  const answered = question({ answer: { letter: 'a', text: 'Decline it', answeredAt: NOW } })
  expect(answersIn('"Decline the finding?"\n\nAdd a budget', [answered])).toEqual([])
})

test('a quote two questions share answers one: the one whose drawer filled it, else the oldest', () => {
  const newer = question({ ref: '#8.1', pr: 'o/r#8' })
  const older = question({ askedAt: NOW - 120_000 })
  const once = '"Decline the finding?"\n\nAdd a budget'
  expect(answersIn(once, [newer, older])).toEqual([{ question: older, letter: 'b' }])
  expect(answersIn(once, [newer, older], ['#8.1'])).toEqual([{ question: newer, letter: 'b' }])
  // Quoted twice, each quote answers its own question, in the order the drawers filled the prompt.
  expect(answersIn(`${once}\n"Decline the finding?"\n\nDecline it`, [older, newer], ['#8.1', '#7.1'])).toEqual([
    { question: newer, letter: 'b' },
    { question: older, letter: 'a' },
  ])
})

test('the note beside an answer names the subagent that asked', () => {
  const asked = question({ agentId: 'agent-1' })
  expect(relayNote(asked, [agent('completed')])).toBe(
    'The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the answer this prompt gives.',
  )
  expect(relayNote(asked, [])).toBe('The subagent agent-1 asked "Decline the finding?"; send it the answer this prompt gives.')
})

test('a delivered question leaves the band after a minute', () => {
  expect(isShownAt(question(), NOW)).toBe(true)
  expect(isShownAt(question({ deliveredAt: NOW - 59_000 }), NOW)).toBe(true)
  expect(isShownAt(question({ deliveredAt: NOW - 60_000 }), NOW)).toBe(false)
})

test('an answer waits for a deliverer until a live claim holds it', () => {
  const answered = question({ answer: { letter: 'a', text: 'Decline it', answeredAt: NOW } })
  expect(needsDelivery(question(), NOW)).toBe(false)
  expect(needsDelivery(answered, NOW)).toBe(true)
  expect(needsDelivery({ ...answered, claim: { token: 't', renewedAt: NOW - 5_000 } }, NOW)).toBe(false)
  expect(needsDelivery({ ...answered, claim: { token: 't', renewedAt: NOW - 60_000 } }, NOW)).toBe(true)
  expect(needsDelivery({ ...answered, deliveredAt: NOW }, NOW)).toBe(false)
})

test('the subagent’s status decides where an answer goes', () => {
  const asked = question({ agentId: 'agent-1' })
  for (const status of ['pending', 'running', 'waiting', 'idle'] as const) {
    expect(liveAgent(asked, [agent(status)])).toBe('agent-1')
  }
  for (const status of ['completed', 'failed', 'killed'] as const) {
    expect(liveAgent(asked, [agent(status)])).toBeUndefined()
  }
  expect(liveAgent(asked, [])).toBeUndefined()
  expect(liveAgent(question(), [agent('running')])).toBeUndefined()
})

test('the main loop is told which subagent asked', () => {
  expect(forMainLoop('ANSWER #7.1.a', question(), [])).toBe('ANSWER #7.1.a')
  expect(forMainLoop('ANSWER #7.1.a', question({ agentId: 'agent-1' }), [agent('completed')])).toContain(
    'The subagent Fix #7 (agent-1) asked this',
  )
  expect(forMainLoop('ANSWER #7.1.a', question({ agentId: 'gone' }), [])).toContain('The subagent gone asked this')
})

test('the store is where the CLI keeps it', () => {
  const env = (values: Record<string, string>) => (name: string) => values[name]
  expect(storeOf(env({ HOME: '/home/ada' }), 's1')).toBe('/home/ada/.local/state/projector/questions/s1')
  expect(storeOf(env({ HOME: '/home/ada', XDG_STATE_HOME: '/state' }), 's1')).toBe('/state/projector/questions/s1')
  expect(storeOf(env({}), 's1')).toBeUndefined()
})

test('the band’s own renewal leaves the store’s signature alone', () => {
  const before = [file('%237.1.json', 1), file('counts.json', 1), file('.lock', 1)]
  const renewed = [file('%237.1.json', 2), file('counts.json', 1)]
  const asked = [...renewed, file('q1.json', 3)]
  const renewing = new Set(['#7.1'])
  expect(signatureOf(renewed, renewing)).toBe(signatureOf(before, renewing))
  expect(signatureOf(renewed, new Set())).not.toBe(signatureOf(before, new Set()))
  expect(signatureOf(asked, renewing)).not.toBe(signatureOf(renewed, renewing))
  expect(signatureOf([file('.%237.1.json.abc', 1)], new Set())).toBe('')
  expect(signatureOf([file('100%.json', 1)], renewing)).toBe('100%.json 10 1')
})

test('ages are short', () => {
  expect([span(40), span(300), span(7200), span(3 * 86_400)]).toEqual(['40s', '5m', '2h', '3d'])
})

test('a call of the tool becomes `project question ask`, text kept as text', () => {
  expect(
    askArguments(
      { question: '-Ship it?', recommendation: '--yes', options: ['no'], detail: 'Why', pr: 'o/r#7' },
      's1',
      'agent-1',
    ),
  ).toEqual([
    'question',
    'ask',
    '--session=s1',
    '--recommend=--yes',
    '--option=no',
    '--detail=Why',
    '--pr=o/r#7',
    '--agent=agent-1',
    '--',
    '-Ship it?',
  ])
  expect(askArguments({ question: 'Ship it?', recommendation: 'Yes' }, 's1', undefined)).toEqual([
    'question',
    'ask',
    '--session=s1',
    '--recommend=Yes',
    '--',
    'Ship it?',
  ])
  expect(askArguments({ recommendation: 'Yes' }, 's1', undefined)).toEqual({ error: '`question` must be a string' })
  expect(askArguments({ question: 'Q', recommendation: 'Yes', options: 'no' }, 's1', undefined)).toEqual({
    error: '`options` must be a list of strings',
  })
})
