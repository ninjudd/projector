import { readFileSync } from "node:fs";
import { join } from "node:path";

// The site lives in web/ inside the Projector repository, so setup.cfg, which
// names Projector's one version, sits one directory up. When it is not there,
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
  const cfg = read("setup.cfg");
  return cfg ? /^version\s*=\s*(\S+)/m.exec(cfg)?.[1] : undefined;
}
