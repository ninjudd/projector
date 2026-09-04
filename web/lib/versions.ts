import { readFileSync } from "node:fs";
import { join } from "node:path";

// The site lives in web/ inside the Projector repository, so the two release
// manifests sit one directory up. When they are not there, say nothing rather
// than show a number that could be stale.
const repoRoot = join(process.cwd(), "..");

function read(path: string): string | undefined {
  try {
    return readFileSync(join(repoRoot, path), "utf8");
  } catch {
    return undefined;
  }
}

export function cliVersion(): string | undefined {
  const cfg = read("setup.cfg");
  return cfg ? /^version\s*=\s*(\S+)/m.exec(cfg)?.[1] : undefined;
}

export function pluginVersion(): string | undefined {
  const manifest = read(".claude-plugin/plugin.json");
  if (!manifest) return undefined;
  try {
    const parsed: unknown = JSON.parse(manifest);
    if (parsed && typeof parsed === "object" && "version" in parsed) {
      const version = (parsed as { version: unknown }).version;
      return typeof version === "string" ? version : undefined;
    }
  } catch {
    return undefined;
  }
  return undefined;
}
