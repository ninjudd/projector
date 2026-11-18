/** Looks up an environment variable; undefined when it is unset or unknown. */
export type Env = (name: string) => string | undefined

/**
 * A pull request as `owner/repo#number` names it. The parsers lowercase
 * `repo`, because GitHub ignores case in owner and repository names, so one
 * repository has one spelling however a file spells it.
 */
export type TrackedPr = { repo: string; number: number }

const OPERATORS = ['&&', '||', ';', '&', '|', '(', ')', '\n']

const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/

const GIT_OPTIONS_WITH_VALUES = new Set(['-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path', '--config-env'])
const WRAPPERS = new Set(['command', 'env', 'exec', 'nohup', 'time'])

/** The words of each simple command in a command line, as the shell would split and unquote them. */
function commandsOf(command: string): string[][] {
  const commands: string[][] = [[]]
  let word = ''
  let isInWord = false

  const add = (text: string) => {
    isInWord = true
    word += text
  }
  const end = () => {
    if (isInWord) commands[commands.length - 1]?.push(word)
    word = ''
    isInWord = false
  }

  let i = 0
  while (i < command.length) {
    const char = command[i] as string
    const operator = OPERATORS.find(op => command.startsWith(op, i))
    if (char === ' ' || char === '\t') {
      end()
      i += 1
    } else if (operator !== undefined) {
      end()
      commands.push([])
      i += operator.length
    } else if (char === '#' && !isInWord) {
      while (i < command.length && command[i] !== '\n') i += 1
    } else if (char === "'") {
      const close = command.indexOf("'", i + 1)
      const stop = close === -1 ? command.length : close
      add(command.slice(i + 1, stop))
      i = stop + 1
    } else if (char === '"') {
      i += 1
      add('')
      while (i < command.length && command[i] !== '"') {
        const inner = command[i] as string
        const next = command[i + 1]
        if (inner === '\\' && next !== undefined && '"\\$`'.includes(next)) {
          add(next)
          i += 2
        } else {
          add(inner)
          i += 1
        }
      }
      i += 1
    } else if (char === '\\') {
      const next = command[i + 1]
      if (next !== undefined && next !== '\n') add(next)
      i += 2
    } else {
      add(char)
      i += 1
    }
  }
  end()

  return commands.filter(words => words.length > 0)
}

/**
 * Whether a command line runs `git push`, `gh pr create`, or a `gh stack`
 * command, whose output can name a pull request the session opened or pushed
 * to. A command only mentioned, as in `echo git push`, does not count.
 */
export function isPushCommand(command: string): boolean {
  return commandsOf(command).some(words => {
    let at = 0
    while (at < words.length && (ASSIGNMENT.test(words[at] ?? '') || WRAPPERS.has(words[at] ?? ''))) at += 1
    const name = (words[at] ?? '').split('/').pop()
    const rest = words.slice(at + 1)
    if (name === 'gh') return rest[0] === 'stack' || (rest[0] === 'pr' && rest[1] === 'create')
    if (name !== 'git') return false
    for (let next = 0; next < rest.length; next += 1) {
      const word = rest[next] ?? ''
      if (GIT_OPTIONS_WITH_VALUES.has(word)) next += 1
      else if (!word.startsWith('-')) return word === 'push'
    }
    return false
  })
}

/** Names a pull request, its repository lowercased as the parsers leave it. */
export const keyOf = (pr: TrackedPr) => `${pr.repo}#${pr.number}`

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** What a subscription asks of the session, in the CLI's words: to fix the pull request, or to review it. */
export type SubscriptionRole = 'fix' | 'review'

/** One line of a session's `subscriptions` file. */
export type Subscription = {
  repo: string
  /** Null for a line that reviews every pull request of the repository. */
  number: number | null
  role: SubscriptionRole
  /** The model a `review` line reviews with; null for a `fix` line. */
  model: string | null
  isExcluded: boolean
}

/** A pull request the session subscribes to, and the roles it holds on it. */
export type Subscribed = { pr: TrackedPr; roles: SubscriptionRole[] }

/** The daemon's `LINE` grammar: `fix`, optionally `auto`, or `review` with a model and a reviewer, and nothing after. */
const SUBSCRIPTION =
  /^(!?)([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)(?:#([1-9][0-9]*))?\s+(?:fix(?:\s+(auto))?|review\s+(\S+)\s+[A-Za-z0-9-]+)$/
const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

/**
 * The reviewer a model ID names, as the daemon compares models: the ID
 * lowercased, without a trailing bracketed variant such as `[1m]`, so one
 * model at another context window is the same reviewer.
 */
export const modelIdentityOf = (model: string) => model.toLowerCase().replace(/\[[^[\]]*\]$/, '')

/**
 * Reads a session's `subscriptions` file as the daemon reads it:
 * `owner/repo#N fix`, optionally followed by `auto`,
 * `owner/repo#N review <model> <reviewer>`, and
 * `owner/repo review <model> <reviewer>`, with a pull request's `fix` or
 * `review` line behind `!` to keep the pull request out of that role. The
 * daemon skips a line of any other shape, so the panel skips it too.
 */
export function parseSubscriptions(text: string): Subscription[] {
  return text.split('\n').flatMap(line => {
    const match = SUBSCRIPTION.exec(line.trim())
    if (match === null) return []
    const [, bang, repo, number, auto, model] = match as unknown as [
      string,
      string,
      string,
      string | undefined,
      string | undefined,
      string | undefined,
    ]
    const isExcluded = bang === '!'
    if (model === undefined && (number === undefined || (isExcluded && auto !== undefined))) return []
    if (model !== undefined && isExcluded && number === undefined) return []
    return [
      {
        repo: repo.toLowerCase(),
        number: number === undefined ? null : Number(number),
        role: model === undefined ? 'fix' : 'review',
        model: model ?? null,
        isExcluded,
      },
    ]
  })
}

/**
 * The pull requests a session subscribes to, in the order its lines name
 * them, then those its repository subscriptions adopted, each once with the
 * roles it holds. A `!` line takes the pull request out of that role, and for
 * `review` out of that model, wherever the line sits. An adopted pull request
 * stays only while a repository line of the session reviews it with a model no
 * `!` line names, as the daemon decides, so a dismissed one goes at once
 * rather than when the daemon's next pass rewrites `adopted`.
 */
export function subscribedOf(lines: readonly Subscription[], adopted: readonly TrackedPr[]): Subscribed[] {
  const roleOf = (line: Subscription) => (line.model === null ? line.role : `review ${modelIdentityOf(line.model)}`)
  const claimOf = (pr: TrackedPr, role: string) => `${keyOf(pr)} ${role}`
  const excluded = new Set(
    lines.flatMap(line =>
      line.isExcluded && line.number !== null ? [claimOf({ repo: line.repo, number: line.number }, roleOf(line))] : [],
    ),
  )
  const live = lines.filter(line => !line.isExcluded)

  const found = new Map<string, Subscribed>()
  const add = (pr: TrackedPr, role: SubscriptionRole) => {
    const was = found.get(keyOf(pr)) ?? { pr, roles: [] }
    found.set(keyOf(pr), was.roles.includes(role) ? was : { ...was, roles: [...was.roles, role] })
  }
  for (const line of live) {
    if (line.number === null) continue
    const pr = { repo: line.repo, number: line.number }
    if (!excluded.has(claimOf(pr, roleOf(line)))) add(pr, line.role)
  }
  const repositories = live.filter(line => line.number === null)
  for (const pr of adopted) {
    const isReviewed = repositories.some(
      line => line.repo === pr.repo && !excluded.has(claimOf(pr, roleOf(line))),
    )
    if (isReviewed) add(pr, 'review')
  }
  return [...found.values()]
}

/**
 * Reads the pull requests the daemon adopted for the session's repository
 * subscriptions from its `state.json`: `{"adopted": {"owner/repo": [12, 14]}}`.
 * The file holds other keys, which belong to the daemon.
 */
export function parseAdopted(text: string): TrackedPr[] {
  let state: unknown
  try {
    state = JSON.parse(text)
  } catch {
    return []
  }
  const adopted = isRecord(state) ? state.adopted : undefined
  if (!isRecord(adopted)) return []
  return Object.entries(adopted).flatMap(([repo, numbers]) =>
    REPO.test(repo) && Array.isArray(numbers)
      ? numbers.filter((number): number is number => Number.isInteger(number) && number > 0).map(number => ({ repo: repo.toLowerCase(), number }))
      : [],
  )
}

/** The login the daemon's `daemon.json` names as the operator, or null when it names none. */
export function parseOperator(text: string): string | null {
  try {
    const daemon: unknown = JSON.parse(text)
    return isRecord(daemon) && typeof daemon.operator === 'string' && daemon.operator !== '' ? daemon.operator : null
  } catch {
    return null
  }
}

/** Where the watch daemon keeps its files, as the CLI finds it: under `XDG_STATE_HOME`, else `~/.local/state`. */
export function watchRootOf(env: Env): string | undefined {
  const home = env('HOME')
  const base = env('XDG_STATE_HOME') ?? (home === undefined ? undefined : `${home}/.local/state`)
  return base === undefined ? undefined : `${base}/projector/watch`
}

/** The daemon's last result for a pull request, which it files under the repository's name in lowercase. */
export const cachePathOf = (root: string, pr: TrackedPr) => `${root}/cache/${pr.repo}/pulls/${pr.number}.json`
