import { readFileSync } from "node:fs";
import { join } from "node:path";

// The site lives in web/ inside the Projector repository, so pyproject.toml,
// which names Projector's one version, sits one directory up. When it is not there,
// say nothing rather than show a number that could be stale.
const repoRoot = join(process.cwd(), "..");

function read(path: string): string | undefined {
  try {
    return readFileSync(join(repoRoot, path), "utf8");
  } catch {
    return undefined;
  }
}

export function projectorVersion(): string | undefined {
  const pyproject = read("pyproject.toml");
  return pyproject ? /^version\s*=\s*"([^"]+)"/m.exec(pyproject)?.[1] : undefined;
}
