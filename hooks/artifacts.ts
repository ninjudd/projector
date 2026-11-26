import type { Artifact } from './types'

export function discussionOf(row: Artifact): string {
  return [
    `Artifact ${row.id}@${row.version}: ${row.title}`,
    ...(row.description === '' ? [] : [`Description: ${row.description}`]),
    `Checkout: ${row.checkout}`,
    `Remote: ${row.remote}`,
    '', '',
  ].join('\n')
}

export function parseArtifacts(text: string): Artifact[] | null {
  try {
    const parsed: unknown = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || !('artifacts' in parsed) || !Array.isArray(parsed.artifacts)) return null
    return parsed.artifacts.filter((row: unknown): row is Artifact =>
      typeof row === 'object' && row !== null &&
      ['id', 'title', 'description', 'version', 'key', 'checkout', 'remote'].every(
        name => name in row && typeof (row as Record<string, unknown>)[name] === 'string',
      ) && 'preview' in row && typeof row.preview === 'boolean',
    )
  } catch {
    return null
  }
}
