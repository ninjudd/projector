import { describe, expect, test } from 'claude-code/testing'

import { buildQuery, buildThreadsQuery, loadingOf, parseCache, parseResponse, parseThreads } from './github'
import {
  cachePathOf,
  findWatchers,
  isPushCommand,
  parseAdopted,
  parseOperator,
  parseSubscriptions,
  parseTracked,
  subscribedOf,
  watchRootOf,
} from './parse'

const VARIABLES: Record<string, string> = { HOME: '/home/ada' }
const env = (name: string) => VARIABLES[name]

describe('findWatchers', () => {
  test('reads the tracked path of a review watcher', () => {
    expect(
      findWatchers('scripts/watch-prs.sh --tracked /loops/tracked --state /loops/state --once', env),
    ).toEqual([{ kind: 'reviewing', tracked: '/loops/tracked', written: '/loops/tracked' }])
  })

  test('reads a fix watcher behind cd, quotes and a variable script path', () => {
    const command =
      'cd /repo && "$ROOT/skills/start-watch-loop/scripts/watch-threads.sh" --tracked "$HOME/watch loop/tracked" --state s'
    expect(findWatchers(command, env)).toEqual([
      { kind: 'watching', tracked: '/home/ada/watch loop/tracked', written: '$HOME/watch loop/tracked' },
    ])
  })

  test('expands ~ and leaves single-quoted text alone', () => {
    expect(findWatchers('watch-prs.sh --tracked ~/t', env)[0]?.tracked).toBe('/home/ada/t')
    expect(findWatchers("watch-prs.sh --tracked '$HOME/t'", env)[0]?.tracked).toBe('$HOME/t')
    expect(findWatchers('watch-prs.sh --tracked=${HOME}/t', env)[0]?.tracked).toBe('/home/ada/t')
  })

  test('leaves a path it cannot expand undefined', () => {
    expect(findWatchers('watch-prs.sh --tracked "$LOOP/tracked"', env)).toEqual([
      { kind: 'reviewing', tracked: undefined, written: '$LOOP/tracked' },
    ])
    expect(findWatchers('watch-prs.sh --tracked "$(pwd)/tracked"', env)[0]?.tracked).toBeUndefined()
  })

  test('expands a variable the command assigns before the watcher', () => {
    const command =
      'D=~/.local/state/projector; ID=projector-review-loop; bash /p/watch-prs.sh --tracked $D/$ID.tracked --state s'
    expect(findWatchers(command, env)[0]?.tracked).toBe('/home/ada/.local/state/projector/projector-review-loop.tracked')
    expect(findWatchers('export T="$HOME/t" && watch-threads.sh --tracked "$T"', env)[0]?.tracked).toBe('/home/ada/t')
    expect(findWatchers('watch-prs.sh --tracked $D/t; D=/late', env)[0]?.tracked).toBeUndefined()
    expect(findWatchers('D=$(pwd); watch-prs.sh --tracked $D/t', env)[0]?.tracked).toBeUndefined()
  })

  test('stops at the end of the watcher command', () => {
    expect(findWatchers('watch-prs.sh --once; echo --tracked /x', env)).toEqual([])
    expect(findWatchers('watch-prs.sh --once && other --tracked /x', env)).toEqual([])
  })

  test('ignores a command that only mentions a watcher', () => {
    expect(findWatchers("echo 'watch-prs.sh --tracked /x'", env)).toEqual([])
    expect(findWatchers('cat skills/start-review-loop/scripts/watch-prs.sh', env)).toEqual([])
    expect(findWatchers('my-watch-prs.sh --tracked /x', env)).toEqual([])
  })
})

describe('parseTracked', () => {
  test('keeps entries in order, once each, less the excluded ones', () => {
    const text = 'o/r#1\no/r#2\n!o/r#2\n\nnot an entry\no/r#1\n  other/re.po#30  \no/r#0\n'
    expect(parseTracked(text)).toEqual([
      { repo: 'o/r', number: 1 },
      { repo: 'other/re.po', number: 30 },
    ])
  })

  test('matches entries and exclusions without regard to case', () => {
    expect(parseTracked('Owner/Repo#5\nowner/repo#5\nowner/repo#6\n!OWNER/REPO#6\n')).toEqual([
      { repo: 'Owner/Repo', number: 5 },
    ])
  })
})

describe('GitHub', () => {
  const query = buildQuery([
    { repo: 'o/r', number: 1 },
    { repo: 'o/r', number: 2 },
    { repo: 'x/y', number: 3 },
  ])

  test('asks for every pull request in one query, grouped by repository, with the signed-in login', () => {
    expect(query.text).toMatch(/^query \{ viewer \{ login \} r0: /)
    expect(query.text).toContain('r0: repository(owner: "o", name: "r") { p1: pullRequest(number: 1)')
    expect(query.text).toContain('p2: pullRequest(number: 2)')
    expect(query.text).toContain('r1: repository(owner: "x", name: "y") { p3: pullRequest(number: 3)')
  })

  test('asks the poll only whether each thread is resolved, which costs no point per pull request', () => {
    // A connection under `reviewThreads(first: 100)`, such as each thread's comments, makes GitHub count 100 more
    // requests, a point more, for each pull request. The poll reads every tracked pull request every 30 seconds.
    expect(query.text).toContain('reviewThreads(first: 100) { nodes { isResolved } }')
    expect(query.text).not.toContain('comments(')
  })

  test('reads what came back even when one pull request is missing', () => {
    const body = {
      data: {
        viewer: { login: 'ada' },
        r0: {
          p1: {
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
            commits: { nodes: [{ commit: { statusCheckRollup: { state: 'FAILURE' } } }] },
            body:
              'Why the change exists.\n\n<!-- projector-summary v=1 sha=' +
              'a'.repeat(40) +
              ' -->\n📽️ **Projector summary** of aaaaaaa: https://example.com/site/reviews/1\n',
          },
          p2: null,
        },
        r1: { p3: { number: 3, title: 'Third', state: 'MERGED', isDraft: false, mergeable: 'UNKNOWN' } },
      },
      errors: [{ message: 'Could not resolve to a PullRequest with the number of 2.' }],
    }
    const fetched = parseResponse(query, { exitCode: 1, stdout: JSON.stringify(body), stderr: '' })

    expect(fetched.error).toBeNull()
    expect(fetched.viewer).toBe('ada')
    expect([...fetched.prs.keys()]).toEqual(['o/r#1', 'x/y#3'])
    expect(fetched.prs.get('o/r#1')).toEqual({
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
      updatedAt: Date.UTC(2026, 9, 12, 12, 0),
      summaryUrl: 'https://example.com/site/reviews/1',
    })
    expect(fetched.prs.get('x/y#3')?.state).toBe('MERGED')
    expect(fetched.prs.get('x/y#3')).toMatchObject({ author: null, checks: null, updatedAt: null, summaryUrl: null })
    expect(fetched.prs.get('x/y#3')?.url).toBe('https://github.com/x/y/pull/3')
  })

  test('reports why gh failed', () => {
    const offline = parseResponse(query, {
      exitCode: 1,
      stdout: '',
      stderr: 'error connecting to api.github.com\ncheck your internet connection\n',
    })
    expect(offline).toEqual({ prs: new Map(), viewer: null, error: 'error connecting to api.github.com' })

    const refused = parseResponse(query, {
      exitCode: 1,
      stdout: JSON.stringify({ errors: [{ message: 'Bad credentials' }] }),
      stderr: '',
    })
    expect(refused).toEqual({ prs: new Map(), viewer: null, error: 'Bad credentials' })
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
      'gh stack submit',
      'npm test; gh stack push',
      '(cd sub && git push)',
    ]) {
      expect([command, isPushCommand(command)]).toEqual([command, true])
    }
  })

  test('leaves out a command that only mentions one, or runs another subcommand', () => {
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

  test('file a pull request’s result under its repository in lowercase', () => {
    expect(cachePathOf('/w', { repo: 'Acme/App', number: 12 })).toBe('/w/cache/acme/app/pulls/12.json')
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
