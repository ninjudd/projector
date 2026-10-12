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

/** Expands a path typed as one shell word, such as `/loop-prs ~/loop/tracked`. */
export function expandPath(text: string, env: Env): string | undefined {
  const tokens = tokenize(text.trim())
  const [word] = tokens
  if (tokens.length !== 1 || word === undefined || !('parts' in word)) return undefined
  return expand(word.parts, env)
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
