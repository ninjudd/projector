import type { AgentInfo } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import {
  arrange,
  askArguments,
  chosenRef,
  forMainLoop,
  isShownAt,
  liveAgent,
  mayReference,
  needsDelivery,
  signatureOf,
  span,
  storeOf,
  typedLetter,
} from './questions'
import type { PullRequest, Question } from './types'

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

const pr = (repo: string, number: number) => ({ pr: { repo, number } as PullRequest, kinds: [] })

const file = (name: string, mtimeMs: number) => ({ name, kind: 'file' as const, size: 10, mtimeMs, isLink: false })

test('only text shaped like a reference starts the CLI', () => {
  for (const text of ['go with #337.3.b', 'Let’s do q2.a.', 'o/r#12.1', 'q1']) expect(mayReference(text)).toBe(true)
  for (const text of ['rerun the 2 failing tests', 'see #337', 'eq2 holds', 'Q2']) expect(mayReference(text)).toBe(false)
})

test('letters run from the recommendation to the typed answer', () => {
  expect(typedLetter(question())).toBe('c')
  expect(chosenRef(question({ answer: { letter: 'b', text: 'Add a budget', answeredAt: NOW } }))).toBe('#7.1.b')
  expect(chosenRef(question({ answer: { dismissed: true, answeredAt: NOW } }))).toBe('#7.1')
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

test('questions follow their pull request; others come first', () => {
  const rows = [pr('o/r', 7), pr('o/r', 9)]
  const { loose, byPr } = arrange(rows, [
    question({ ref: '#7.2', number: 2, askedAt: NOW - 1 }),
    question({ ref: 'q1', number: 1, pr: null, askedAt: NOW - 3 }),
    question({ ref: '#7.1', number: 1, pr: 'O/R#7', askedAt: NOW }),
    question({ ref: '#5.1', number: 1, pr: 'o/r#5', askedAt: NOW - 2 }),
  ])
  expect(loose.map(one => one.ref)).toEqual(['q1', '#5.1'])
  expect(byPr.get('o/r#7')?.map(one => one.ref)).toEqual(['#7.1', '#7.2'])
  expect(byPr.get('o/r#9')).toBeUndefined()
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
