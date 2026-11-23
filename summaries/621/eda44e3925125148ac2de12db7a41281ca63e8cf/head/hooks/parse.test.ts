import { describe, expect, test } from 'claude-code/testing'

import { buildThreadsQuery, loadingOf, parseCache, parseThreads } from './github'
import {
  cachePathOf,
  isPushCommand,
  parseAdopted,
  parseOperator,
  parseSubscriptions,
  subscribedOf,
  watchRootOf,
} from './parse'

const VARIABLES: Record<string, string> = { HOME: '/home/ada' }
const env = (name: string) => VARIABLES[name]

describe('GitHub', () => {
  test('reads each field of a cached pull request, and a default for each one it leaves out', () => {
    const cacheOf = (pullRequest: Record<string, unknown>) => JSON.stringify({ repository: { pullRequest } })
    const first = cacheOf({
      number: 1,
      title: 'First',
      url: 'https://github.com/o/r/pull/1',
      state: 'OPEN',
      isDraft: true,
      reviewDecision: 'CHANGES_REQUESTED',
      mergeable: 'CONFLICTING',
      reviewThreads: { nodes: [{ isResolved: false }, { isResolved: true }, { isResolved: false }] },
      author: { login: 'ada' },
      headRefName: 'first-change',
      additions: 120,
      deletions: 7,
      changedFiles: 4,
      updatedAt: '2026-10-12T12:00:00Z',
      labels: { nodes: [{ name: 'Reviewing', color: 'FBCA04' }, { name: 'bug', color: 'not a color' }] },
      commits: { nodes: [{ commit: { statusCheckRollup: { state: 'FAILURE' } } }] },
      body:
        'Why the change exists.\n\n<!-- projector-summary v=1 sha=' +
        'a'.repeat(40) +
        ' -->\n📽️ **Projector summary** of aaaaaaa: https://example.com/site/reviews/1\n',
    })
    const third = cacheOf({ number: 3, title: 'Third', state: 'MERGED', isDraft: false, mergeable: 'UNKNOWN' })

    expect(parseCache({ repo: 'o/r', number: 1 }, first)).toEqual({
      repo: 'o/r',
      number: 1,
      title: 'First',
      url: 'https://github.com/o/r/pull/1',
      state: 'OPEN',
      isDraft: true,
      reviewDecision: 'CHANGES_REQUESTED',
      isConflicting: true,
      openThreads: 2,
      author: 'ada',
      branch: 'first-change',
      additions: 120,
      deletions: 7,
      changedFiles: 4,
      checks: 'failing',
      isCancelled: false,
      // Spelled as GitHub spells them, with GitHub's color as `#rrggbb`, and none for a color the panel cannot draw.
      labels: [
        { name: 'Reviewing', color: '#fbca04' },
        { name: 'bug', color: null },
      ],
      updatedAt: Date.UTC(2026, 9, 12, 12, 0),
      summaryUrl: 'https://example.com/site/reviews/1',
    })
    expect(parseCache({ repo: 'x/y', number: 3 }, third)).toMatchObject({
      state: 'MERGED',
      author: null,
      checks: null,
      updatedAt: null,
      summaryUrl: null,
      url: 'https://github.com/x/y/pull/3',
    })
  })

  test('reads a head’s checks without the projector/review status a review posts', () => {
    type Counts = { checkRuns?: Record<string, number>; statuses?: Record<string, number> }
    const checksOf = (rollup: string, review: string | null, { checkRuns = {}, statuses = {} }: Counts) => {
      const rows = (counts: Record<string, number>) => Object.entries(counts).map(([state, count]) => ({ state, count }))
      const commit = {
        status: review === null ? null : { context: { state: review } },
        statusCheckRollup: {
          state: rollup,
          contexts: { checkRunCountsByState: rows(checkRuns), statusContextCountsByState: rows(statuses) },
        },
      }
      const node = { number: 1, commits: { nodes: [{ commit }] } }
      return parseCache({ repo: 'o/r', number: 1 }, JSON.stringify({ repository: { pullRequest: node } }))?.checks
    }

    expect({
      // A review that requests changes fails its status, which fails the rollup when nothing else does.
      'only the review failed': checksOf('FAILURE', 'FAILURE', { statuses: { FAILURE: 1 } }),
      'the review failed, the rest passed': checksOf('FAILURE', 'FAILURE', {
        checkRuns: { SUCCESS: 3, SKIPPED: 1 },
        statuses: { FAILURE: 1 },
      }),
      'the review failed, and so did a check run': checksOf('FAILURE', 'FAILURE', {
        checkRuns: { FAILURE: 1, SUCCESS: 2 },
        statuses: { FAILURE: 1 },
      }),
      'the review failed, and another status errored': checksOf('FAILURE', 'FAILURE', {
        checkRuns: { SUCCESS: 1 },
        statuses: { FAILURE: 1, ERROR: 1 },
      }),
      'the review is running, the rest still run': checksOf('PENDING', 'PENDING', {
        checkRuns: { IN_PROGRESS: 1, SUCCESS: 2 },
        statuses: { PENDING: 1 },
      }),
      'the review is running, the rest passed': checksOf('PENDING', 'PENDING', {
        checkRuns: { SUCCESS: 2 },
        statuses: { PENDING: 1 },
      }),
      'the review passed, a check run failed': checksOf('FAILURE', 'SUCCESS', {
        checkRuns: { FAILURE: 1 },
        statuses: { SUCCESS: 1 },
      }),
      'no review status, a check run failed': checksOf('FAILURE', null, { checkRuns: { FAILURE: 1, SUCCESS: 1 } }),
      'no review status, every check passed': checksOf('SUCCESS', null, { checkRuns: { SUCCESS: 2 } }),
    }).toEqual({
      'only the review failed': null,
      'the review failed, the rest passed': 'passing',
      'the review failed, and so did a check run': 'failing',
      'the review failed, and another status errored': 'failing',
      'the review is running, the rest still run': 'pending',
      'the review is running, the rest passed': 'passing',
      'the review passed, a check run failed': 'failing',
      'no review status, a check run failed': 'failing',
      'no review status, every check passed': 'passing',
    })
  })

  test('reads a cancelled run’s failed checks as cancelled when the cancel caused them or a newer run tests them again, and notes a workflow whose newest run was cancelled', () => {
    // A check run as the rollup lists it: its name and conclusion, its workflow run's conclusion, workflow, and
    // start, which give the run its id, and its own start and end. A check run with no workflow belongs to no
    // workflow run.
    type Run = [
      name: string,
      conclusion: string | null,
      suite: string | null,
      workflow: number | null,
      at: string,
      started?: string,
      completed?: string,
    ]
    const FIRST = '2026-11-16T22:00:00Z'
    const LATER = '2026-11-16T22:01:00Z'
    // When a cancelled run's jobs ran: a job that failed on its own, then the cancel, then the gathering job that
    // runs `if: always()` once the jobs it needs end.
    const STARTED = '2026-11-16T22:00:05Z'
    const FAILED = '2026-11-16T22:00:20Z'
    const CANCELLED = '2026-11-16T22:00:40Z'
    const GATHERED = '2026-11-16T22:00:42Z'
    const read = (rollup: string, runs: Run[]) => {
      const counts = new Map<string, number>()
      for (const [, conclusion] of runs) counts.set(conclusion ?? 'IN_PROGRESS', (counts.get(conclusion ?? 'IN_PROGRESS') ?? 0) + 1)
      const nodes = runs.map(([name, conclusion, suite, workflow, at, started, completed]) => ({
        __typename: 'CheckRun',
        name,
        conclusion,
        startedAt: started ?? null,
        completedAt: completed ?? null,
        checkSuite: {
          conclusion: suite,
          workflowRun:
            workflow === null
              ? null
              : { databaseId: Date.parse(at) / 1000 + workflow, createdAt: at, workflow: { databaseId: workflow } },
        },
      }))
      const contexts = { checkRunCountsByState: [...counts].map(([state, count]) => ({ state, count })), nodes }
      const node = { number: 1, commits: { nodes: [{ commit: { statusCheckRollup: { state: rollup, contexts } } }] } }
      const found = parseCache({ repo: 'o/r', number: 1 }, JSON.stringify({ repository: { pullRequest: node } }))
      return [found?.checks, found?.isCancelled]
    }
    // A label starts a run that cancels the one in progress, whose gathering job then starts and fails.
    const slow: Run = ['slow', 'CANCELLED', 'CANCELLED', 1, FIRST, STARTED, CANCELLED]
    const gathering: Run = ['complete', 'FAILURE', 'CANCELLED', 1, FIRST, GATHERED]
    // The run's `lint` failed on its own before the cancel.
    const lint: Run = ['lint', 'FAILURE', 'CANCELLED', 1, FIRST, STARTED, FAILED]

    expect({
      'nothing ran since': read('FAILURE', [slow, gathering]),
      'the newer run passed': read('FAILURE', [
        slow,
        gathering,
        ['slow', 'SUCCESS', 'SUCCESS', 1, LATER],
        ['complete', 'SUCCESS', 'SUCCESS', 1, LATER],
      ]),
      'the newer run still runs': read('FAILURE', [slow, gathering, ['slow', null, null, 1, LATER]]),
      // Without a cancelled check run of its own run, nothing says when the cancel came.
      'the cancel time is unknown': read('FAILURE', [gathering]),
      'a check failed before the cancel, nothing ran since': read('FAILURE', [lint, slow, gathering]),
      'a check failed before the cancel, the newer run passed it': read('FAILURE', [
        lint,
        slow,
        ['lint', 'SUCCESS', 'SUCCESS', 1, LATER],
        ['slow', 'SUCCESS', 'SUCCESS', 1, LATER],
      ]),
      // A review's label starts a run whose job-level `if` skips every job.
      'a check failed before the cancel, the newer run skipped it': read('FAILURE', [
        lint,
        slow,
        ['lint', 'SKIPPED', 'SUCCESS', 1, LATER],
        ['slow', 'SKIPPED', 'SUCCESS', 1, LATER],
      ]),
      'only a cancelled run, with a passed job': read('FAILURE', [
        ['lint', 'SUCCESS', 'CANCELLED', 1, FIRST, STARTED, FAILED],
        slow,
      ]),
      'another workflow failed': read('FAILURE', [slow, gathering, ['lint', 'FAILURE', 'FAILURE', 2, FIRST]]),
      'a run failed and nothing cancelled it': read('FAILURE', [['lint', 'FAILURE', 'FAILURE', 1, FIRST]]),
      'a check run that no workflow run made': read('FAILURE', [
        ['other', 'CANCELLED', 'CANCELLED', null, FIRST, STARTED, CANCELLED],
        ['external', 'FAILURE', 'CANCELLED', null, FIRST, GATHERED],
      ]),
    }).toEqual({
      'nothing ran since': ['cancelled', true],
      'the newer run passed': ['passing', false],
      'the newer run still runs': ['pending', false],
      'the cancel time is unknown': ['failing', true],
      'a check failed before the cancel, nothing ran since': ['failing', true],
      'a check failed before the cancel, the newer run passed it': ['passing', false],
      'a check failed before the cancel, the newer run skipped it': ['failing', false],
      'only a cancelled run, with a passed job': ['cancelled', true],
      'another workflow failed': ['failing', true],
      'a run failed and nothing cancelled it': ['failing', false],
      'a check run that no workflow run made': ['failing', false],
    })
  })

  describe('an open drawer’s threads', () => {
    const threadsQuery = buildThreadsQuery({ repo: 'o/r', number: 1 })

    test('asks for one pull request’s threads and each one’s first comment', () => {
      expect(threadsQuery.text).toMatch(
        /^query \{ r0: repository\(owner: "o", name: "r"\) \{ p1: pullRequest\(number: 1\) \{ reviewThreads/,
      )
      expect(threadsQuery.text).toContain('comments(first: 1) { nodes { url bodyText author { login } } }')
      expect(threadsQuery.text).not.toContain('viewer')
    })

    test('reads the unresolved threads, oldest first', () => {
      const body = {
        data: {
          r0: {
            p1: {
              reviewThreads: {
                nodes: [
                  {
                    isResolved: false,
                    isOutdated: false,
                    path: 'src/app.ts',
                    line: 12,
                    originalLine: 10,
                    comments: {
                      nodes: [
                        {
                          url: 'https://github.com/o/r/pull/1#discussion_r1',
                          bodyText: 'P2 · Retry the fetch\nIt gives up after one try.',
                          author: { login: 'grace' },
                        },
                      ],
                    },
                  },
                  { isResolved: true, path: 'src/done.ts', line: 3 },
                  { isResolved: false, isOutdated: true, path: 'README.md', line: null, originalLine: 7 },
                  { isResolved: false, path: 'docs/guide.md', line: null, originalLine: null },
                ],
              },
            },
          },
        },
      }
      const threads = parseThreads(threadsQuery, { exitCode: 0, stdout: JSON.stringify(body), stderr: '' })
      // An outdated thread keeps the line it was on, and a comment on the whole file has none.
      expect(threads).toEqual([
        {
          path: 'src/app.ts',
          line: 12,
          isOutdated: false,
          author: 'grace',
          excerpt: 'P2 · Retry the fetch',
          url: 'https://github.com/o/r/pull/1#discussion_r1',
        },
        { path: 'README.md', line: 7, isOutdated: true, author: null, excerpt: '', url: null },
        { path: 'docs/guide.md', line: null, isOutdated: false, author: null, excerpt: '', url: null },
      ])
    })

    test('reads nothing when GitHub gave no threads', () => {
      const missing = { data: { r0: { p1: null } }, errors: [{ message: 'Could not resolve to a PullRequest' }] }
      expect(parseThreads(threadsQuery, { exitCode: 1, stdout: JSON.stringify(missing), stderr: '' })).toBeNull()
      const offline = { exitCode: 1, stdout: '', stderr: 'error connecting to api.github.com\n' }
      expect(parseThreads(threadsQuery, offline)).toBeNull()
    })
  })
})

describe('isPushCommand', () => {
  test('finds a push, a new pull request, or a stack command, wherever it sits in the line', () => {
    for (const command of [
      'git push',
      'git push -u origin HEAD',
      'cd /w && git -C sub --no-pager push origin feature',
      'GIT_TRACE=1 git push',
      'env GH_TOKEN=x gh pr create --draft --title "Add a thing" --body-file b.md',
      '/usr/bin/git push --force-with-lease',
      'gh stack submit --auto',
      'npm test; gh stack push',
      'gh stack sync',
      'gh stack link 12 13',
      '(cd sub && git push)',
    ]) {
      expect([command, isPushCommand(command)]).toEqual([command, true])
    }
  })

  test('leaves out a command that only mentions one, runs another subcommand, or asks for help', () => {
    for (const command of [
      'echo git push',
      "git commit -m 'git push later'",
      'git status',
      'git log --grep push',
      'git -C push status',
      'gh pr view 12',
      'gh pr list',
      'grep -r "gh pr create" skills',
      'my-git push',
      '# git push',
      'gh stack view',
      'gh stack checkout 12',
      'gh stack add next-layer',
      'gh stack checkout --help',
      'gh stack submit -h',
      'gh stack help submit',
      'git push --help',
      'gh pr create --help',
    ]) {
      expect([command, isPushCommand(command)]).toEqual([command, false])
    }
  })
})

describe('subscriptions', () => {
  test('reads each line of the grammar and skips any other, as the daemon does', () => {
    const text = [
      'acme/app#12 fix',
      'acme/app#13 fix auto',
      'acme/app#14 review claude-opus-5-5 octocat',
      'acme/app review claude-opus-5-5 octocat',
      '!acme/app#9 review claude-opus-5-5 octocat',
      '!acme/app#12 fix',
      'acme/app fix',
      'acme/app#16 fixer',
      'acme/app#16 fix later',
      'acme/app#16 fix auto now',
      '!acme/app#16 fix auto',
      'acme/app#17 review',
      'acme/app#17 review claude-opus-5-5',
      'acme/app#17 review claude-opus-5-5 octo_cat',
      'acme/app#17 review claude-opus-5-5 octocat now',
      '!acme/app#17 review',
      '!acme/app review claude-opus-5-5 octocat',
      'not a line',
      '',
    ].join('\n')
    expect(parseSubscriptions(text)).toEqual([
      { repo: 'acme/app', number: 12, role: 'fix', model: null, isExcluded: false },
      { repo: 'acme/app', number: 13, role: 'fix', model: null, isExcluded: false },
      { repo: 'acme/app', number: 14, role: 'review', model: 'claude-opus-5-5', isExcluded: false },
      { repo: 'acme/app', number: null, role: 'review', model: 'claude-opus-5-5', isExcluded: false },
      { repo: 'acme/app', number: 9, role: 'review', model: 'claude-opus-5-5', isExcluded: true },
      { repo: 'acme/app', number: 12, role: 'fix', model: null, isExcluded: true },
    ])
  })

  test('lists each pull request once with its roles, less the excluded ones, then the adopted ones', () => {
    const lines = parseSubscriptions(
      [
        'acme/app#13 fix auto',
        'Acme/App#13 review claude-opus-5-5 octocat',
        'acme/app#12 fix',
        '!ACME/APP#12 fix',
        'acme/app#14 review claude-opus-5-5 octocat',
        'acme/app#14 review claude-sonnet-5 octocat',
        '!acme/app#14 review claude-opus-5-5 octocat',
        'acme/app#15 review claude-opus-5-5 octocat',
        '!acme/app#15 review Claude-Opus-5-5[1m] octocat',
        'acme/app review claude-opus-5-5[1m] octocat',
        '!acme/app#21 review claude-opus-5-5 octocat',
      ].join('\n'),
    )
    const adopted = [
      { repo: 'acme/app', number: 13 },
      { repo: 'acme/app', number: 20 },
      { repo: 'acme/app', number: 21 },
      { repo: 'x/y', number: 3 },
    ]
    expect(subscribedOf(lines, adopted)).toEqual([
      { pr: { repo: 'acme/app', number: 13 }, roles: ['fix', 'review'] },
      // One model's exclusion leaves another model's review. Models compare as the daemon compares them, in
      // lowercase and without a context-window variant, so #15's `!` line takes it out.
      { pr: { repo: 'acme/app', number: 14 }, roles: ['review'] },
      // A `!` line takes an adopted pull request out before the daemon's next pass drops it from `adopted`, and
      // one adopted for a repository no line reviews any longer goes too.
      { pr: { repo: 'acme/app', number: 20 }, roles: ['review'] },
    ])
  })

  test('keeps an adopted pull request that one model of the repository still reviews', () => {
    const lines = parseSubscriptions(
      [
        'acme/app review claude-opus-5-5 octocat',
        'acme/app review claude-sonnet-5 octocat',
        '!acme/app#20 review claude-opus-5-5 octocat',
      ].join('\n'),
    )
    expect(subscribedOf(lines, [{ repo: 'acme/app', number: 20 }])).toEqual([
      { pr: { repo: 'acme/app', number: 20 }, roles: ['review'] },
    ])
  })

  test('reads the adopted pull requests from the daemon’s state, and nothing else of it', () => {
    const state = JSON.stringify({
      adopted: { 'acme/app': [12, 14], 'x/y': [3, 0, 'four', 2.5], 'not a repository': [1] },
      announced: { 'acme/app#12': {} },
    })
    expect(parseAdopted(state)).toEqual([
      { repo: 'acme/app', number: 12 },
      { repo: 'acme/app', number: 14 },
      { repo: 'x/y', number: 3 },
    ])
    expect(parseAdopted('')).toEqual([])
    expect(parseAdopted('{"adopted": []}')).toEqual([])
  })

  test('reads the operator from daemon.json', () => {
    const daemon = { pid: 12, version: '0.9.0', started_at: '2026-10-30T12:00:00Z', operator: 'octocat' }
    expect(parseOperator(JSON.stringify(daemon))).toBe('octocat')
    expect(parseOperator(JSON.stringify({ ...daemon, operator: '' }))).toBeNull()
    expect(parseOperator('{')).toBeNull()
  })
})

describe('the daemon’s files', () => {
  test('live under XDG_STATE_HOME, else ~/.local/state, as the CLI finds them', () => {
    expect(watchRootOf(env)).toBe('/home/ada/.local/state/projector/watch')
    const variables: Record<string, string> = { HOME: '/home/ada', XDG_STATE_HOME: '/state' }
    expect(watchRootOf(name => variables[name])).toBe('/state/projector/watch')
    expect(watchRootOf(() => undefined)).toBeUndefined()
  })

  test('lowercase each repository they read, and file its pull request’s result under that name', () => {
    const [line] = parseSubscriptions('Acme/App#12 fix\n')
    expect(line?.repo).toBe('acme/app')
    expect(parseAdopted('{"adopted": {"Acme/App": [12]}}')).toEqual([{ repo: 'acme/app', number: 12 }])
    expect(cachePathOf('/w', { repo: line?.repo ?? '', number: 12 })).toBe('/w/cache/acme/app/pulls/12.json')
  })

  test('give a pull request’s row from its cached result', () => {
    // Every field the daemon's query names, as the cache holds them.
    const pullRequest = {
      number: 12,
      title: 'Add a thing',
      url: 'https://github.com/acme/app/pull/12',
      body: '',
      state: 'OPEN',
      isDraft: true,
      reviewDecision: null,
      mergeable: 'CONFLICTING',
      updatedAt: '2026-10-30T11:00:00Z',
      headRefOid: 'a'.repeat(40),
      headRefName: 'add-a-thing',
      baseRefName: 'main',
      author: { login: 'octocat' },
      additions: 30,
      deletions: 4,
      changedFiles: 3,
      labels: { nodes: [{ name: 'clean' }] },
      commits: { nodes: [{ commit: { statusCheckRollup: { state: 'PENDING' } } }] },
      comments: { nodes: [] },
      reviews: { nodes: [] },
      changesRequested: { nodes: [] },
      reviewThreads: {
        nodes: [
          { id: 'T1', isResolved: false, path: 'a.py', line: 3, first: { nodes: [] }, last: { nodes: [] } },
          { id: 'T2', isResolved: true, path: 'b.py', line: 9, first: { nodes: [] }, last: { nodes: [] } },
        ],
      },
    }
    const cached = JSON.stringify({
      fetched_at: '2026-10-30T11:59:00Z',
      etags: { pull: 'W/"1"' },
      repository: { visibility: 'PUBLIC', pullRequest },
      reviews: [],
    })
    expect(parseCache({ repo: 'Acme/App', number: 12 }, cached)).toEqual({
      repo: 'Acme/App',
      number: 12,
      title: 'Add a thing',
      url: 'https://github.com/acme/app/pull/12',
      state: 'OPEN',
      isDraft: true,
      reviewDecision: null,
      isConflicting: true,
      openThreads: 1,
      author: 'octocat',
      branch: 'add-a-thing',
      additions: 30,
      deletions: 4,
      changedFiles: 3,
      checks: 'pending',
      isCancelled: false,
      labels: [{ name: 'clean', color: null }],
      updatedAt: Date.UTC(2026, 9, 30, 11),
      summaryUrl: null,
    })
    expect(parseCache({ repo: 'acme/app', number: 12 }, '')).toBeNull()
    expect(parseCache({ repo: 'acme/app', number: 12 }, '{"repository": {"pullRequest": null}}')).toBeNull()
  })

  test('give a pull request with no result yet a loading row that holds only its reference', () => {
    expect(loadingOf({ repo: 'acme/app', number: 12 })).toMatchObject({
      repo: 'acme/app',
      number: 12,
      url: 'https://github.com/acme/app/pull/12',
      state: 'OPEN',
      isLoading: true,
    })
  })
})
