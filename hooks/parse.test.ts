import { describe, expect, test } from 'claude-code/testing'

import { buildQuery, parseResponse } from './github'
import { findWatchers, parseTracked } from './parse'

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
      // Only unresolved threads, oldest first. An outdated one keeps the line it was on, and a comment on
      // the whole file has none.
      threads: [
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
      ],
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
})
