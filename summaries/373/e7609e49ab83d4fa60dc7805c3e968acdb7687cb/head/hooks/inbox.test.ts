import type { AgentInfo } from 'claude-code'
import { expect, test } from 'claude-code/testing'

import {
  ASK_DESCRIPTION,
  askArguments,
  forMainLoop,
  isShownAt,
  isSnoozedAt,
  liveAgent,
  needsDelivery,
  proposalOf,
  quoteOf,
  readPrompt,
  relayNote,
  replyNote,
  reportArguments,
  REQUEST_DESCRIPTION,
  requestArguments,
  RESTORE_DESCRIPTION,
  RESTORE_SCHEMA,
  restoreArguments,
  REVISE_DESCRIPTION,
  reviseArguments,
  signatureOf,
  span,
  storeOf,
  TELL_DESCRIPTION,
  tellArguments,
  typedLetter,
  WITHDRAW_DESCRIPTION,
  WITHDRAW_SCHEMA,
  withdrawArguments,
  wokeBetween,
  type Proposal,
} from './inbox'
import type { Item, Notice, Question, Request } from './types'

const NOW = Date.UTC(2026, 9, 12, 12, 0)

const question = (fields: Partial<Question> = {}): Question => ({
  ref: '1',
  number: 1,
  pr: 'o/r#7',
  postedAt: NOW - 60_000,
  kind: 'question',
  subject: 'Decline the finding?',
  detail: '',
  choices: ['Decline it', 'Add a budget'],
  session: 's1',
  ...fields,
})

const notice = (fields: Partial<Notice> = {}): Notice => ({
  ref: '2',
  number: 2,
  pr: 'o/r#7',
  postedAt: NOW - 30_000,
  kind: 'notice',
  subject: 'Force-pushed #7 to drop a stray file',
  detail: '',
  session: 's1',
  ...fields,
})

const request = (fields: Partial<Request> = {}): Request => ({
  ref: '3',
  number: 3,
  pr: null,
  postedAt: NOW - 20_000,
  kind: 'request',
  subject: 'Test the gutters',
  detail: '1. Open Fyra.',
  session: 's1',
  ...fields,
})

const agent = (status: AgentInfo['status']): AgentInfo =>
  ({ id: 'agent-1', description: 'Fix #7', type: 'general-purpose', status }) as AgentInfo

/** An answer a drawer put in the prompt under the question `question()` asks. */
const pick = (ref: string, answer: string, fields: Partial<Proposal> = {}): Proposal => ({
  ref,
  quote: '"Decline the finding?"',
  answer,
  ...fields,
})

const answersIn = (text: string, items: readonly Item[], proposed: readonly Proposal[] = []) =>
  readPrompt(text, items, proposed).answers

const repliesIn = (text: string, items: readonly Item[]) => readPrompt(text, items).replies

const file = (name: string, mtimeMs: number) => ({ name, kind: 'file' as const, size: 10, mtimeMs, isLink: false })

test('the ask tool tells the agent that a pick arrives by number and a reply as the person’s prompt', () => {
  expect(ASK_DESCRIPTION).toContain(
    'The call returns the question’s number, such as 3. When the person picks an option, a message that starts `ANSWER 3.<letter>` arrives later, or `DISMISSED 3` when they dismiss it.',
  )
  expect(ASK_DESCRIPTION).toContain('the reply arrives as their prompt, quoting the question, and the question stays open')
  expect(ASK_DESCRIPTION).toMatch(/withdraw the question if the reply settles it, revise it if it was unclear/)
  expect(ASK_DESCRIPTION).toContain(
    'When they restore a question after you heard `DISMISSED 3`, `RESTORED 3` arrives, and the question is open again.',
  )
})

test('the restore tool is for a dismissal the person calls a mistake, never a question asked again', () => {
  expect(RESTORE_DESCRIPTION).toContain(
    'Use it only when the person says a dismissal was a mistake or asks for the item back, never on your own judgement, and never post a dismissed item again as a new one.',
  )
  expect(RESTORE_SCHEMA).toEqual(WITHDRAW_SCHEMA)
})

test('every inbox tool tells the agent to name an item to the person by its text, since the panel never shows its number', () => {
  for (const description of [
    ASK_DESCRIPTION,
    TELL_DESCRIPTION,
    REQUEST_DESCRIPTION,
    WITHDRAW_DESCRIPTION,
    REVISE_DESCRIPTION,
    RESTORE_DESCRIPTION,
  ]) {
    expect(description).toMatch(
      / The panel never shows an item’s number, so name the item to the person by what it asks, tells, or requests, never by its number\.$/,
    )
  }
})

test('letters run from the recommendation to the typed answer', () => {
  expect(typedLetter(question())).toBe('c')
})

test('the prompt carries a question in quotes, and an answer on a line of its own', () => {
  expect(quoteOf(question())).toBe('"Decline the finding?"')
  expect(proposalOf(question(), 'Add a budget')).toBe('"Decline the finding?"\n\nAdd a budget')
})

test('a sent prompt answers each question whose quote an option follows', () => {
  const open = [question(), question({ ref: '2', number: 2, subject: 'Rebase first?', choices: ['Merge main', 'Rebase'] })]
  expect(answersIn('"Decline the finding?"\n\nAdd a budget\n', open)).toEqual([{ question: open[0], letter: 'b' }])
  expect(answersIn('Both:\n"Decline the finding?"\nDecline it\n"Rebase first?"\n\nRebase', open)).toEqual([
    { question: open[0], letter: 'a' },
    { question: open[1], letter: 'b' },
  ])
  // Spaces, tabs, and blank lines around the quote and the option change nothing.
  expect(answersIn('"Decline the finding?" \nAdd a budget', open)).toEqual([{ question: open[0], letter: 'b' }])
  expect(answersIn('"Rebase first?"\t\n\n\n  Rebase \t\n\n', open)).toEqual([{ question: open[1], letter: 'b' }])
  // A quoted notice ends the text that answers the question above it.
  const told = notice({ ref: '3', number: 3 })
  expect(answersIn(`"Decline the finding?"\n\nDecline it\n"${told.subject}"\n\nThanks`, [...open, told])).toEqual([
    { question: open[0], letter: 'a' },
  ])
  // Leaving the answer empty answers nothing.
  expect(answersIn('"Decline the finding?"\n\n', open)).toEqual([])
  const answered = question({ answer: { letter: 'a', text: 'Decline it', answeredAt: NOW } })
  expect(answersIn('"Decline the finding?"\n\nAdd a budget', [answered])).toEqual([])
})

test('anything after a quoted question but one of its options replies to it without answering', () => {
  const open = [question(), question({ ref: '2', number: 2, subject: 'Rebase first?', choices: ['Merge main', 'Rebase'] })]
  const replies = [
    // Free text, as discuss leaves room for.
    '"Rebase first?"\t\n\nkeep it',
    // An option with more text after it, on its line or on the next.
    '"Decline the finding?"\n\nAdd a budget of two per host',
    '"Decline the finding?"\n\nAdd a budget\n\nand log each retry',
    // A question back, under the quote or on its line.
    '"Decline the finding?"\n\nWhat does the cap cover?',
    '"Decline the finding?" what does the cap cover?',
    // Another question's option.
    '"Rebase first?"\n\nDecline it',
  ]
  for (const text of replies) {
    expect(answersIn(text, open)).toEqual([])
    expect(repliesIn(text, open)).toEqual(open.filter(one => text.includes(quoteOf(one))))
  }
  const text = '"Decline the finding?"\n\nDecline it\n"Rebase first?"\n\nwhy rebase?'
  expect(readPrompt(text, open)).toEqual({ answers: [{ question: open[0], letter: 'a' }], outcomes: [], replies: [open[1]] })
  // A notice or a resolved question takes no reply.
  const answered = question({ answer: { letter: 'a', text: 'Decline it', answeredAt: NOW } })
  expect(repliesIn(`"${notice().subject}"\n\nThanks`, [notice()])).toEqual([])
  expect(repliesIn('"Decline the finding?"\n\nwhy?', [answered])).toEqual([])
})

test('a quote two questions share goes to the oldest that offers the option it is followed by', () => {
  const older = question({ postedAt: NOW - 120_000, choices: ['Decline it', 'Add a budget'] })
  const newer = question({ ref: '2', number: 2, choices: ['Decline it', 'Add a budget per host'] })
  expect(answersIn('"Decline the finding?"\n\nAdd a budget per host', [older, newer])).toEqual([
    { question: newer, letter: 'b' },
  ])
  expect(answersIn('"Decline the finding?"\n\nDecline it', [older, newer])).toEqual([{ question: older, letter: 'a' }])
})

test('a quote two questions share answers one: the one whose drawer put that answer there, else the oldest', () => {
  const newer = question({ ref: '2', number: 2, pr: 'o/r#8' })
  const older = question({ postedAt: NOW - 120_000 })
  const budget = '"Decline the finding?"\n\nAdd a budget'
  const decline = '"Decline the finding?"\n\nDecline it'
  expect(answersIn(budget, [newer, older])).toEqual([{ question: older, letter: 'b' }])
  expect(answersIn(budget, [newer, older], [pick('2', 'Add a budget')])).toEqual([
    { question: newer, letter: 'b' },
  ])
  // A proposal whose answer the prompt no longer holds, as when you cleared it, decides nothing.
  const both = [
    pick('2', 'Decline it'),
    pick('1', 'Add a budget'),
  ]
  expect(answersIn(budget, [newer, older], both)).toEqual([{ question: older, letter: 'b' }])
  expect(answersIn(decline, [newer, older], [pick('2', 'Decline it')])).toEqual([
    { question: newer, letter: 'a' },
  ])
  expect(answersIn(budget, [newer, older], [pick('2', 'Decline it')])).toEqual([
    { question: older, letter: 'b' },
  ])
  // Quoted twice, each quote answers its own question, in the order the quotes appear.
  expect(answersIn(`${budget}\n${decline}`, [older, newer], both)).toEqual([
    { question: older, letter: 'b' },
    { question: newer, letter: 'a' },
  ])
})

test('a quote two questions share speaks to one of them: an answer replies to neither, and a reply goes where its drawer put it', () => {
  const first = question({ postedAt: NOW - 120_000, agentId: 'agent-a', subject: 'Merge it?', choices: ['Yes', 'No'] })
  const second = question({ ref: '2', number: 2, agentId: 'agent-b', subject: 'Merge it?', choices: ['Yes', 'No'] })
  const both = [first, second]
  const merge = (ref: string, answer: string): Proposal => ({ ref, quote: '"Merge it?"', answer })
  // You pick No in the second question's drawer: it answers the second, and says nothing to the first.
  expect(readPrompt('"Merge it?"\n\nNo\n', both, [merge('2', 'No')])).toEqual({
    answers: [{ question: second, letter: 'b' }],
    outcomes: [],
    replies: [],
  })
  expect(readPrompt('"Merge it?"\n\nNo\n', both)).toEqual({ answers: [{ question: first, letter: 'b' }], outcomes: [], replies: [] })
  // A reply goes to the question whose drawer put the quote there, by discuss or by a pick you added to, else the oldest.
  expect(readPrompt('"Merge it?"\n\nwhy now?', both, [merge('2', '')])).toEqual({ answers: [], outcomes: [], replies: [second] })
  expect(readPrompt('"Merge it?"\n\nNo, not yet', both, [merge('2', 'No')])).toEqual({ answers: [], outcomes: [], replies: [second] })
  expect(readPrompt('"Merge it?"\n\nwhy now?', both)).toEqual({ answers: [], outcomes: [], replies: [first] })
  // An option typed under discuss answers the question you discussed.
  expect(readPrompt('"Merge it?"\n\nYes', both, [merge('2', '')])).toEqual({
    answers: [{ question: second, letter: 'a' }],
    outcomes: [],
    replies: [],
  })
  // The drawer you acted in last wins: a pick in the second, then a discuss in the first.
  expect(readPrompt('"Merge it?"\n\nYes', both, [merge('2', 'Yes'), merge('1', '')])).toEqual({
    answers: [{ question: first, letter: 'a' }],
    outcomes: [],
    replies: [],
  })
  expect(readPrompt('"Merge it?"\n\nYes', both, [merge('1', ''), merge('2', 'Yes')])).toEqual({
    answers: [{ question: second, letter: 'a' }],
    outcomes: [],
    replies: [],
  })
  // Quoted twice, each quote speaks to its own question.
  expect(readPrompt('"Merge it?"\n\nYes\n"Merge it?"\n\nwhy now?', both)).toEqual({
    answers: [{ question: first, letter: 'a' }],
    outcomes: [],
    replies: [second],
  })
})

test('a pick made before its question was revised answers no question, and replies to the revised one', () => {
  const older = question({ postedAt: NOW - 120_000 })
  const revised = question({ ref: '2', number: 2, detail: 'The cap is three.' })
  const stale = [pick('2', 'Decline it', { isStale: true })]
  const decline = '"Decline the finding?"\n\nDecline it\n'
  // The older question shares the text and offers the option, and still does not take the pick.
  expect(readPrompt(decline, [older, revised], stale)).toEqual({ answers: [], outcomes: [], replies: [revised] })
  // Revised to new words, the question is still the one the pick replies to.
  const reworded = question({ ref: '2', number: 2, subject: 'Decline the retry finding?' })
  expect(readPrompt(decline, [older, reworded], stale)).toEqual({ answers: [], outcomes: [], replies: [reworded] })
  // Text the stale pick does not hold answers as before.
  expect(readPrompt('"Decline the finding?"\n\nAdd a budget\n', [older, revised], stale)).toEqual({
    answers: [{ question: older, letter: 'b' }],
    outcomes: [],
    replies: [],
  })
  // A pick from the revised question's new drawer answers it.
  expect(readPrompt(decline, [older, revised], [pick('2', 'Decline it')])).toEqual({
    answers: [{ question: revised, letter: 'a' }],
    outcomes: [],
    replies: [],
  })
})

test('an option typed under a quote discussed before its question was revised answers no question, and replies to the revised one', () => {
  const older = question({ postedAt: NOW - 120_000 })
  const revised = question({ ref: '2', number: 2, detail: 'The cap is three.' })
  const reworded = question({ ref: '2', number: 2, subject: 'Decline the retry finding?' })
  const discussed = [pick('2', '', { isStale: true })]
  const decline = '"Decline the finding?"\n\nDecline it\n'
  expect(readPrompt(decline, [older, revised], discussed)).toEqual({ answers: [], outcomes: [], replies: [revised] })
  expect(readPrompt(decline, [older, reworded], discussed)).toEqual({ answers: [], outcomes: [], replies: [reworded] })
  // A pick you then make in the older question's drawer still answers it.
  const both = [...discussed, pick('1', 'Decline it')]
  expect(readPrompt(`"Decline the finding?"\n\nwhy?\n${decline}`, [older, revised], both)).toEqual({
    answers: [{ question: older, letter: 'a' }],
    outcomes: [],
    replies: [revised],
  })
  // Discussed again in the revised drawer, the quote answers the revised question.
  expect(readPrompt(decline, [older, revised], [pick('2', '')])).toEqual({
    answers: [{ question: revised, letter: 'a' }],
    outcomes: [],
    replies: [],
  })
})

test('a quote a drawer put in the prompt for a question resolved before you send speaks to no question', () => {
  const merge = { subject: 'Merge it?', choices: ['Yes', 'No'] }
  const open = question({ ...merge, postedAt: NOW - 120_000 })
  const dismissed = question({ ...merge, ref: '2', number: 2, answer: { dismissed: true, answeredAt: NOW } })
  const withdrawn = question({ ...merge, ref: '2', number: 2, answer: { dismissed: true, withdrawn: true, answeredAt: NOW } })
  const drawer = (answer: string, isStale: boolean): Proposal => ({ ref: '2', quote: '"Merge it?"', answer, isStale })
  const picks = [drawer('Yes', true), drawer('Yes', false), drawer('', true), drawer('', false)]
  for (const resolved of [[dismissed], [withdrawn], []]) {
    for (const proposal of picks) {
      expect(readPrompt('"Merge it?"\n\nYes\n', [open, ...resolved], [proposal])).toEqual({ answers: [], outcomes: [], replies: [] })
    }
  }
  // A quote no drawer put there still answers the open question.
  expect(readPrompt('"Merge it?"\n\nYes\n', [open, dismissed])).toEqual({
    answers: [{ question: open, letter: 'a' }],
    outcomes: [],
    replies: [],
  })
})

test('the note beside an answer or a reply names the subagent that asked', () => {
  const asked = question({ agentId: 'agent-1' })
  expect(relayNote(asked, [agent('completed')])).toBe(
    'The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the answer this prompt gives.',
  )
  expect(relayNote(asked, [])).toBe('The subagent agent-1 asked "Decline the finding?"; send it the answer this prompt gives.')
  expect(replyNote(asked, [agent('running')])).toBe(
    'The subagent Fix #7 (agent-1) asked "Decline the finding?"; send it the reply this prompt gives, which leaves the question open.',
  )
})

test('a delivered question leaves the panel after 30 seconds', () => {
  expect(isShownAt(question(), NOW)).toBe(true)
  expect(isShownAt(question({ deliveredAt: NOW - 29_000 }), NOW)).toBe(true)
  expect(isShownAt(question({ deliveredAt: NOW - 30_000 }), NOW)).toBe(false)
})

test('a withdrawn item leaves the panel at once', () => {
  const dismissed = { dismissed: true as const, answeredAt: NOW }
  const withdrawn = { ...dismissed, withdrawn: true as const }
  expect(isShownAt(notice({ answer: dismissed, deliveredAt: NOW }), NOW)).toBe(true)
  expect(isShownAt(notice({ answer: withdrawn, deliveredAt: NOW }), NOW)).toBe(false)
  expect(isShownAt(question({ answer: withdrawn, deliveredAt: NOW }), NOW)).toBe(false)
})

test('a call of the withdraw tool becomes `project inbox withdraw`, and only a whole number names an item', () => {
  expect(withdrawArguments({ number: 3 }, 's1')).toEqual(['inbox', 'withdraw', '3', '--session', 's1', '--json'])
  const refusal = { error: '`number` must be the whole number `ask`, `tell`, or `request` returned, such as 3' }
  for (const number of [undefined, '3', 0, -1, 1.5, Number.NaN]) {
    expect(withdrawArguments({ number }, 's1')).toEqual(refusal)
  }
})

test('a call of the restore tool becomes `project inbox restore`, and only a whole number names an item', () => {
  expect(restoreArguments({ number: 3 }, 's1')).toEqual(['inbox', 'restore', '3', '--session', 's1', '--json'])
  const refusal = { error: '`number` must be the whole number `ask`, `tell`, or `request` returned, such as 3' }
  for (const number of [undefined, '3', 0, -1, 1.5, Number.NaN]) {
    expect(restoreArguments({ number }, 's1')).toEqual(refusal)
  }
})

test('a call of the revise tool becomes `project inbox revise` with only what changes, text kept as text', () => {
  expect(
    reviseArguments(
      { number: 3, question: '-Decline it?', recommendation: '--yes', options: ['no', 'later'], detail: '' },
      's1',
    ),
  ).toEqual([
    'inbox',
    'revise',
    '3',
    '--session',
    's1',
    '--question=-Decline it?',
    '--recommend=--yes',
    '--option=no',
    '--option=later',
    '--detail=',
    '--json',
  ])
  expect(reviseArguments({ number: 3, options: [] }, 's1')).toEqual([
    'inbox',
    'revise',
    '3',
    '--session',
    's1',
    '--no-options',
    '--json',
  ])
  expect(reviseArguments({ number: 3 }, 's1')).toEqual({
    error: 'give what changes: `question`, `request`, `start`, `detail`, `recommendation`, or `options`',
  })
  expect(reviseArguments({ number: '3', detail: 'x' }, 's1')).toEqual({
    error: '`number` must be the whole number `ask` or `request` returned, such as 3',
  })
  expect(reviseArguments({ number: 3, options: 'no' }, 's1')).toEqual({ error: '`options` must be a list of strings' })
  expect(reviseArguments({ number: 3, question: 7 }, 's1')).toEqual({ error: '`question` must be a string' })
})

test('an answer waits for a deliverer until a live claim holds it', () => {
  const answered = question({ answer: { letter: 'a', text: 'Decline it', answeredAt: NOW } })
  expect(needsDelivery(question(), NOW)).toBe(false)
  expect(needsDelivery(answered, NOW)).toBe(true)
  expect(needsDelivery({ ...answered, claim: { token: 't', renewedAt: NOW - 5_000 } }, NOW)).toBe(false)
  expect(needsDelivery({ ...answered, claim: { token: 't', renewedAt: NOW - 60_000 } }, NOW)).toBe(true)
  expect(needsDelivery({ ...answered, deliveredAt: NOW }, NOW)).toBe(false)
})

test('word that a question was restored waits for a deliverer until a live claim holds it', () => {
  const restored = question({ restoredAt: NOW - 1_000 })
  expect(needsDelivery(restored, NOW)).toBe(true)
  expect(needsDelivery({ ...restored, claim: { token: 't', renewedAt: NOW - 5_000 } }, NOW)).toBe(false)
  expect(needsDelivery({ ...restored, claim: { token: 't', renewedAt: NOW - 60_000 } }, NOW)).toBe(true)
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
  expect(forMainLoop('ANSWER 1.a', question(), [])).toBe('ANSWER 1.a')
  expect(forMainLoop('ANSWER 1.a', question({ agentId: 'agent-1' }), [agent('completed')])).toContain(
    'The subagent Fix #7 (agent-1) asked this',
  )
  expect(forMainLoop('ANSWER 1.a', question({ agentId: 'gone' }), [])).toContain('The subagent gone asked this')
})

test('the store is where the CLI keeps it', () => {
  const env = (values: Record<string, string>) => (name: string) => values[name]
  expect(storeOf(env({ HOME: '/home/ada' }), 's1')).toBe('/home/ada/.local/state/projector/inbox/s1')
  expect(storeOf(env({ HOME: '/home/ada', XDG_STATE_HOME: '/state' }), 's1')).toBe('/state/projector/inbox/s1')
  expect(storeOf(env({}), 's1')).toBeUndefined()
})

test('the panel’s own renewal leaves the store’s signature alone', () => {
  const before = [file('1.json', 1), file('count.json', 1), file('.lock', 1)]
  const renewed = [file('1.json', 2), file('count.json', 1)]
  const asked = [...renewed, file('2.json', 3)]
  const renewing = new Set(['1'])
  expect(signatureOf(renewed, renewing)).toBe(signatureOf(before, renewing))
  expect(signatureOf(renewed, new Set())).not.toBe(signatureOf(before, new Set()))
  expect(signatureOf(asked, renewing)).not.toBe(signatureOf(renewed, renewing))
  expect(signatureOf([file('.1.json.abc', 1)], new Set())).toBe('')
  expect(signatureOf([file('100%.json', 1)], renewing)).toBe('100%.json 10 1')
})

test('ages are short', () => {
  expect([span(40), span(300), span(7200), span(3 * 86_400)]).toEqual(['40s', '5m', '2h', '3d'])
})

test('a call of the tool becomes `project inbox ask`, text kept as text', () => {
  expect(
    askArguments(
      { question: '-Ship it?', recommendation: '--yes', options: ['no'], detail: 'Why', pr: 'o/r#7' },
      's1',
      'agent-1',
    ),
  ).toEqual([
    'inbox',
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
    'inbox',
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

test('a snoozed item leaves the panel until its time, and wakes once between two ticks', () => {
  const snoozed = notice({ snoozedUntil: NOW + 3_600_000 })
  expect(isShownAt(snoozed, NOW)).toBe(false)
  expect(isShownAt(snoozed, NOW + 3_600_000)).toBe(true)
  expect(wokeBetween([snoozed, question()], NOW + 3_595_000, NOW + 3_600_000)).toEqual([snoozed])
  expect(wokeBetween([snoozed], NOW + 3_600_000, NOW + 3_605_000)).toEqual([])
  expect(wokeBetween([snoozed], NOW, NOW + 3_595_000)).toEqual([])
  const dismissed = notice({ snoozedUntil: NOW + 3_600_000, answer: { dismissed: true, answeredAt: NOW } })
  expect(wokeBetween([dismissed], NOW, NOW + 3_600_000)).toEqual([])
})

test('a snoozed item stays in the panel for 30 seconds after it is snoozed, then leaves until its time', () => {
  const snoozed = notice({ snoozedAt: NOW, snoozedUntil: NOW + 3_600_000 })
  expect(isSnoozedAt(snoozed, NOW + 29_000)).toBe(true)
  expect(isShownAt(snoozed, NOW + 29_000)).toBe(true)
  expect(isShownAt(snoozed, NOW + 30_000)).toBe(false)
  expect(isShownAt(snoozed, NOW + 3_600_000)).toBe(true)
  expect(isSnoozedAt(snoozed, NOW + 3_600_000)).toBe(false)
})

test('only a question’s answer and a request’s outcome wait for a deliverer', () => {
  const dismissed = { dismissed: true as const, answeredAt: NOW }
  expect(needsDelivery(question({ answer: dismissed }), NOW)).toBe(true)
  expect(needsDelivery(notice({ answer: dismissed }), NOW)).toBe(false)
  expect(needsDelivery(request({ answer: { done: true, answeredAt: NOW } }), NOW)).toBe(true)
  expect(needsDelivery(request({ answer: { done: true, answeredAt: NOW }, deliveredAt: NOW }), NOW)).toBe(false)
  expect(needsDelivery(request({ restoredAt: NOW }), NOW)).toBe(true)
  expect(needsDelivery(request(), NOW)).toBe(false)
})

test('a sent prompt answers snoozed questions and never a notice', () => {
  const snoozed = question({ snoozedUntil: NOW + 3_600_000 })
  const told = notice()
  expect(answersIn('"Decline the finding?"\n\nAdd a budget\n', [snoozed, told])).toEqual([
    { question: snoozed, letter: 'b' },
  ])
  expect(answersIn('"Force-pushed #7 to drop a stray file"\n\nThanks\n', [snoozed, told])).toEqual([])
})

test('a call of the tell tool becomes `project inbox tell`, text kept as text', () => {
  expect(tellArguments({ notice: '-Dropped a file', detail: 'Why', pr: 'o/r#7' }, 's1', 'agent-1')).toEqual([
    'inbox',
    'tell',
    '--session=s1',
    '--detail=Why',
    '--pr=o/r#7',
    '--agent=agent-1',
    '--',
    '-Dropped a file',
  ])
  expect(tellArguments({ notice: 'Dropped a file' }, 's1', undefined)).toEqual([
    'inbox',
    'tell',
    '--session=s1',
    '--',
    'Dropped a file',
  ])
  expect(tellArguments({ detail: 'Why' }, 's1', undefined)).toEqual({ error: '`notice` must be a string' })
  expect(tellArguments({ notice: 'N', pr: 7 }, 's1', undefined)).toEqual({
    error: '`pr` must be a string such as owner/repo#123',
  })
})

test('the request tool asks for work only the person can do, its steps in `detail`, and names what comes back', () => {
  expect(REQUEST_DESCRIPTION).toContain('Ask the person to do something only they can do')
  expect(REQUEST_DESCRIPTION).toContain('put the steps they follow in `detail`')
  expect(REQUEST_DESCRIPTION).toContain('`DONE 3` or `DECLINED 3`')
  expect(REQUEST_DESCRIPTION).toContain('When you need the person to decide something rather than do it, use `ask` instead.')
})

test('a call of the request tool becomes `project inbox request`, text kept as text', () => {
  expect(requestArguments({ request: '-Test the gutters', detail: '1. Open Fyra.', pr: 'o/r#7' }, 's1', 'agent-1')).toEqual([
    'inbox',
    'request',
    '--session=s1',
    '--detail=1. Open Fyra.',
    '--pr=o/r#7',
    '--agent=agent-1',
    '--',
    '-Test the gutters',
  ])
  expect(requestArguments({ request: 'Sign in' }, 's1', undefined)).toEqual(['inbox', 'request', '--session=s1', '--', 'Sign in'])
  expect(requestArguments({ detail: 'Why' }, 's1', undefined)).toEqual({ error: '`request` must be a string' })
  expect(requestArguments({ request: 'R', detail: 7 }, 's1', undefined)).toEqual({ error: '`detail` must be a string' })
})

test('Done or Declined on the line under a quoted request is its outcome, and notes may follow', () => {
  const open = request()
  const outcomesIn = (text: string) => readPrompt(text, [open]).outcomes
  expect(outcomesIn('"Test the gutters"\n\nDone\n')).toEqual([{ request: open, outcome: 'done' }])
  expect(outcomesIn('"Test the gutters"\n\nDone\n\n4 didn’t work\nthe rest did')).toEqual([{ request: open, outcome: 'done' }])
  expect(outcomesIn('"Test the gutters"\n\n  Declined \n\nNo phone today')).toEqual([{ request: open, outcome: 'declined' }])
  expect(readPrompt('"Test the gutters"\n\nDone', [open])).toEqual({
    answers: [],
    outcomes: [{ request: open, outcome: 'done' }],
    replies: [],
  })
})

test('anything else under a quoted request replies to it and leaves it open', () => {
  const open = request()
  for (const text of [
    '"Test the gutters"\n\ndone?',
    '"Test the gutters"\n\nDone, mostly',
    '"Test the gutters"\n\nWhich build?',
    '"Test the gutters" Done',
    '"Test the gutters"\n\n',
  ]) {
    expect(readPrompt(text, [open])).toEqual({ answers: [], outcomes: [], replies: [open] })
  }
  const done = request({ answer: { done: true, answeredAt: NOW } })
  expect(readPrompt('"Test the gutters"\n\nDone', [done])).toEqual({ answers: [], outcomes: [], replies: [] })
})

test('a request’s drawer owns its quote whatever follows, and a pick from before a revise only replies', () => {
  const open = request()
  const shared = request({ ref: '4', number: 4, postedAt: NOW - 10_000 })
  const done: Proposal = { ref: '4', quote: '"Test the gutters"', answer: 'Done' }
  expect(readPrompt('"Test the gutters"\n\nDone\n\nall good', [open, shared], [done]).outcomes).toEqual([
    { request: shared, outcome: 'done' },
  ])
  expect(readPrompt('"Test the gutters"\n\nDeclined', [open, shared], [done]).outcomes).toEqual([
    { request: shared, outcome: 'declined' },
  ])
  expect(readPrompt('"Test the gutters"\n\nDone', [open, shared])).toEqual({
    answers: [],
    outcomes: [{ request: open, outcome: 'done' }],
    replies: [],
  })
  expect(readPrompt('"Test the gutters"\n\nDone', [open, shared], [{ ...done, isStale: true }])).toEqual({
    answers: [],
    outcomes: [],
    replies: [shared],
  })
})

test('a quoted request and a quoted question in one prompt each take their own', () => {
  const asked = question()
  const open = request()
  expect(readPrompt('"Decline the finding?"\n\nDecline it\n"Test the gutters"\n\nDone\n\nfine', [asked, open])).toEqual({
    answers: [{ question: asked, letter: 'a' }],
    outcomes: [{ request: open, outcome: 'done' }],
    replies: [],
  })
})

test('the notes beside a subagent’s request name it and say an outcome goes back', () => {
  const posted = request({ agentId: 'agent-1' })
  expect(relayNote(posted, [agent('completed')])).toBe(
    'The subagent Fix #7 (agent-1) requested "Test the gutters"; send it the outcome this prompt gives.',
  )
  expect(replyNote(posted, [])).toBe(
    'The subagent agent-1 requested "Test the gutters"; send it the reply this prompt gives, which leaves the request open.',
  )
  expect(forMainLoop('DONE 3 Test the gutters', posted, [])).toBe(
    'DONE 3 Test the gutters\n\nThe subagent agent-1 requested this, and the outcome could not reach it; send it this outcome, or act on it yourself.',
  )
})

test('a call of the revise tool revises a request with `request`', () => {
  expect(reviseArguments({ number: 3, request: 'Test the new gutters', detail: '1. Open the build.' }, 's1')).toEqual([
    'inbox',
    'revise',
    '3',
    '--session',
    's1',
    '--request=Test the new gutters',
    '--detail=1. Open the build.',
    '--json',
  ])
  expect(reviseArguments({ number: 3, request: 7 }, 's1')).toEqual({ error: '`request` must be a string' })
  expect(reviseArguments({ number: 3, start: '' }, 's1')).toEqual(['inbox', 'revise', '3', '--session', 's1', '--start=', '--json'])
})

test('the request tool tells the agent to give a start action only when it must prepare, and to wait for STARTED', () => {
  expect(REQUEST_DESCRIPTION).toContain('Give `start` only when you must do something before they can begin')
  expect(REQUEST_DESCRIPTION).toContain('Do the start action only after it arrives, and do exactly what it quotes and nothing more.')
  expect(requestArguments({ request: 'Test the gutters', start: 'Install the build' }, 's1', undefined)).toEqual([
    'inbox',
    'request',
    '--session=s1',
    '--start=Install the build',
    '--',
    'Test the gutters',
  ])
  expect(requestArguments({ request: 'R', start: 7 }, 's1', undefined)).toEqual({ error: '`start` must be a string' })
})

test('a call of the ready or block tool becomes `project inbox ready` or `block`', () => {
  expect(reportArguments({ number: 3 }, 's1')).toEqual(['inbox', 'ready', '3', '--session', 's1', '--json'])
  expect(reportArguments({ number: 3, reason: '-Unlock the phone' }, 's1')).toEqual([
    'inbox',
    'block',
    '3',
    '--reason=-Unlock the phone',
    '--session',
    's1',
    '--json',
  ])
  expect(reportArguments({ number: 0 }, 's1')).toEqual({ error: '`number` must be the whole number `request` returned, such as 3' })
})

test('a request’s start waits for a deliverer until STARTED is delivered, and only while the request is open', () => {
  const started = { action: 'Install the build', startedAt: NOW }
  expect(needsDelivery(request({ start: 'Install the build', started }), NOW)).toBe(true)
  expect(needsDelivery(request({ started: { ...started, deliveredAt: NOW } }), NOW)).toBe(false)
  expect(needsDelivery(request({ started, claim: { token: 't', renewedAt: NOW, line: 'started' } }), NOW)).toBe(false)
  expect(needsDelivery(request({ started, answer: { done: true, answeredAt: NOW }, deliveredAt: NOW }), NOW)).toBe(false)
})
