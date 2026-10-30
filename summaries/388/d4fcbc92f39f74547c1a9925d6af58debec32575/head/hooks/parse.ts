import type { LoopKind } from './types'

/** Looks up an environment variable; undefined when it is unset or unknown. */
export type Env = (name: string) => string | undefined

/** A watcher found in a command line, and the tracked path it was given. */
export type Watcher = {
  kind: LoopKind
  /** The path, expanded; undefined when it could not be expanded. */
  tracked: string | undefined
  /** The `--tracked` argument as written, for messages. */
  written: string
}

/** One `owner/repo#number` entry of a tracked file. */
export type TrackedPr = { repo: string; number: number }

const SCRIPTS: Record<string, LoopKind> = {
  'watch-prs.sh': 'reviewing',
  'watch-threads.sh': 'watching',
}

const OPERATORS = ['&&', '||', ';', '&', '|', '(', ')', '\n']

type Part = { text: string; isLiteral: boolean }
type Token = { parts: Part[] } | { operator: string }

/**
 * Splits a shell command into words and operators, keeping which parts of a
 * word were quoted so that expansion leaves single-quoted text alone.
 */
export function tokenize(command: string): Token[] {
  const tokens: Token[] = []
  let parts: Part[] = []
  let isInWord = false

  const add = (text: string, isLiteral: boolean) => {
    isInWord = true
    const last = parts[parts.length - 1]
    if (last !== undefined && last.isLiteral === isLiteral) {
      last.text += text
    } else {
      parts.push({ text, isLiteral })
    }
  }
  const end = () => {
    if (isInWord) tokens.push({ parts })
    parts = []
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
      tokens.push({ operator })
      i += operator.length
    } else if (char === '#' && !isInWord) {
      while (i < command.length && command[i] !== '\n') i += 1
    } else if (char === "'") {
      const close = command.indexOf("'", i + 1)
      const stop = close === -1 ? command.length : close
      add(command.slice(i + 1, stop), true)
      i = stop + 1
    } else if (char === '"') {
      i += 1
      while (i < command.length && command[i] !== '"') {
        const inner = command[i] as string
        const next = command[i + 1]
        if (inner === '\\' && next !== undefined && '"\\$`'.includes(next)) {
          add(next, true)
          i += 2
        } else {
          add(inner, false)
          i += 1
        }
      }
      if (!isInWord) add('', true)
      i += 1
    } else if (char === '\\') {
      const next = command[i + 1]
      if (next !== undefined && next !== '\n') add(next, true)
      i += 2
    } else {
      add(char, false)
      i += 1
    }
  }
  end()

  return tokens
}

const VARIABLE = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g

/**
 * Expands a word the way the shell would for a path: a leading `~` and
 * `$NAME` or `${NAME}` outside single quotes. Undefined when the word holds
 * something only the running shell knows, such as an unset variable or a
 * command substitution.
 */
export function expand(parts: readonly Part[], env: Env): string | undefined {
  let out = ''
  for (const [index, part] of parts.entries()) {
    if (part.isLiteral) {
      out += part.text
      continue
    }
    let text = part.text
    if (text.includes('$(') || text.includes('`')) return undefined
    if (index === 0 && (text === '~' || text.startsWith('~/'))) {
      const home = env('HOME')
      if (home === undefined) return undefined
      text = home + text.slice(1)
    }
    let isKnown = true
    text = text.replace(VARIABLE, (_, braced: string | undefined, bare: string | undefined) => {
      const value = env(braced ?? bare ?? '')
      if (value === undefined) isKnown = false
      return value ?? ''
    })
    if (!isKnown || text.includes('$')) return undefined
    out += text
  }
  return out
}

const raw = (parts: readonly Part[]) => parts.map(part => part.text).join('')

const ASSIGNMENT = /^([A-Za-z_][A-Za-z0-9_]*)=/

/**
 * Finds each `watch-prs.sh` or `watch-threads.sh` invocation in a command
 * line and the `--tracked` path given to it. A variable the command assigns
 * before the watcher, as in `D=~/loops; watch-prs.sh --tracked $D/tracked`,
 * expands ahead of the environment.
 */
export function findWatchers(command: string, env: Env): Watcher[] {
  const tokens = tokenize(command)
  const found: Watcher[] = []
  const assigned = new Map<string, string | undefined>()
  const lookup: Env = name => (assigned.has(name) ? assigned.get(name) : env(name))
  let isCommandStart = true

  for (const [index, token] of tokens.entries()) {
    if (!('parts' in token)) {
      isCommandStart = true
      continue
    }
    const [first, ...rest] = token.parts
    const assignment =
      isCommandStart && first !== undefined && !first.isLiteral ? ASSIGNMENT.exec(first.text) : null
    if (assignment !== null && first !== undefined) {
      const [written, name] = assignment as unknown as [string, string]
      assigned.set(name, expand([{ ...first, text: first.text.slice(written.length) }, ...rest], lookup))
      continue
    }
    if (isCommandStart && raw(token.parts) === 'export') continue
    isCommandStart = false

    const name = raw(token.parts).split('/').pop() ?? ''
    const kind = SCRIPTS[name]
    if (kind === undefined) continue

    for (let at = index + 1; at < tokens.length; at += 1) {
      const arg = tokens[at] as Token
      if (!('parts' in arg)) break
      const text = raw(arg.parts)
      if (text === '--tracked') {
        const value = tokens[at + 1]
        if (value === undefined || !('parts' in value)) break
        found.push({ kind, tracked: expand(value.parts, lookup), written: raw(value.parts) })
        break
      }
      if (text.startsWith('--tracked=')) {
        const [first, ...rest] = arg.parts
        if (first === undefined) break
        const value = [{ ...first, text: first.text.slice('--tracked='.length) }, ...rest]
        found.push({ kind, tracked: expand(value, lookup), written: raw(value) })
        break
      }
    }
  }

  return found
}

const GIT_OPTIONS_WITH_VALUES = new Set(['-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path', '--config-env'])
const WRAPPERS = new Set(['command', 'env', 'exec', 'nohup', 'time'])

/** The words of each simple command in a command line, as the shell would split them. */
function commandsOf(command: string): string[][] {
  const commands: string[][] = [[]]
  for (const token of tokenize(command)) {
    if ('parts' in token) commands[commands.length - 1]?.push(raw(token.parts))
    else commands.push([])
  }
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

const ENTRY = /^(!?)([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#([1-9][0-9]*)$/

/** Names a pull request without regard to case, as GitHub and the watchers do. */
export const keyOf = (pr: TrackedPr) => `${pr.repo}#${pr.number}`.toLowerCase()

/**
 * Reads a tracked file: one `owner/repo#number` per line, where a line
 * `!owner/repo#number` keeps that pull request out. Lines of any other
 * shape are skipped; the watcher reports them itself.
 */
export function parseTracked(text: string): TrackedPr[] {
  const kept = new Map<string, TrackedPr>()
  const excluded = new Set<string>()

  for (const line of text.split('\n')) {
    const match = ENTRY.exec(line.trim())
    if (match === null) continue
    const [, bang, repo, number] = match as unknown as [string, string, string, string]
    const pr = { repo, number: Number(number) }
    const key = keyOf(pr)
    if (bang === '!') {
      excluded.add(key)
    } else if (!kept.has(key)) {
      kept.set(key, pr)
    }
  }

  return [...kept.entries()]
    .filter(([key]) => !excluded.has(key))
    .map(([, pr]) => pr)
}

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
  /** The model a `review` line reviews with; null for a `fix` line, and for a `!` line that names none. */
  model: string | null
  isExcluded: boolean
}

/** A pull request the session subscribes to, and the roles it holds on it. */
export type Subscribed = { pr: TrackedPr; roles: SubscriptionRole[] }

const SUBSCRIPTION = /^(!?)([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)(?:#([1-9][0-9]*))?\s+(fix|review)(?=\s|$)(?:\s+(\S+))?/
const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

/**
 * Reads a session's `subscriptions` file: `owner/repo#N fix`, optionally
 * followed by `auto`, `owner/repo#N review <model> <reviewer>`,
 * `owner/repo review <model> <reviewer>`, and any of them behind `!` to keep
 * the pull request out of that role. Lines of any other shape are skipped.
 */
export function parseSubscriptions(text: string): Subscription[] {
  return text.split('\n').flatMap(line => {
    const match = SUBSCRIPTION.exec(line.trim())
    if (match === null) return []
    const [, bang, repo, number, role, word] = match as unknown as [
      string,
      string,
      string,
      string | undefined,
      SubscriptionRole,
      string | undefined,
    ]
    if (role === 'fix' && number === undefined) return []
    return [
      {
        repo,
        number: number === undefined ? null : Number(number),
        role,
        model: role === 'review' ? (word ?? null) : null,
        isExcluded: bang === '!',
      },
    ]
  })
}

/**
 * The pull requests a session subscribes to, in the order its lines name
 * them, then those its repository subscriptions adopted, each once with the
 * roles it holds. A `!` line takes the pull request out of that role, and for
 * `review` out of that model, wherever the line sits; one naming no model
 * takes it out of every model.
 */
export function subscribedOf(lines: readonly Subscription[], adopted: readonly TrackedPr[]): Subscribed[] {
  const keyOfLine = (line: Subscription) => `${line.repo}#${line.number ?? ''}`.toLowerCase()
  const isExcluded = (line: Subscription) =>
    lines.some(
      one =>
        one.isExcluded &&
        one.role === line.role &&
        keyOfLine(one) === keyOfLine(line) &&
        (one.model === null || one.model === line.model),
    )

  const found = new Map<string, Subscribed>()
  const add = (pr: TrackedPr, role: SubscriptionRole) => {
    const was = found.get(keyOf(pr)) ?? { pr, roles: [] }
    found.set(keyOf(pr), was.roles.includes(role) ? was : { ...was, roles: [...was.roles, role] })
  }
  for (const line of lines) {
    if (line.number !== null && !line.isExcluded && !isExcluded(line)) add({ repo: line.repo, number: line.number }, line.role)
  }
  for (const pr of adopted) add(pr, 'review')
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
      ? numbers.filter((number): number is number => Number.isInteger(number) && number > 0).map(number => ({ repo, number }))
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
export const cachePathOf = (root: string, pr: TrackedPr) =>
  `${root}/cache/${pr.repo.toLowerCase()}/pulls/${pr.number}.json`
