import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import * as lib from "../src/lib.js";

// The tests run from dist/test/, and the import graph is the source's.
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const IMPORT = /(?:^|\n)\s*(?:import|export)\b[^;]*?\bfrom\s*"([^"]+)"|\bimport\s*\(\s*"([^"]+)"\s*\)/g;

/** Every source file `entry` reaches through relative imports, relative to the service's root. */
function reached(entry: string): Set<string> {
  const seen = new Set<string>();
  const visit = (file: string) => {
    const name = relative(ROOT, file);
    if (seen.has(name)) return;
    seen.add(name);
    for (const match of readFileSync(file, "utf8").matchAll(IMPORT)) {
      const specifier = match[1] ?? match[2] ?? "";
      if (specifier.startsWith(".")) visit(resolve(dirname(file), specifier.replace(/\.js$/, ".ts")));
    }
  };
  visit(join(ROOT, entry));
  return seen;
}

test("the library exports the push ID's seal and open and the forwarder to Apple", () => {
  for (const name of ["sealPushId", "openPushId", "pushIdKey", "deviceToken", "createForwarder", "apnsConfigFromEnv", "pushIdKeyFromEnv"] as const) {
    assert.equal(typeof lib[name], "function", name);
  }
});

test("the library imports neither handler nor their rate limit, so a deployment can import it and mount neither", () => {
  assert.deepEqual([...reached("src/lib.ts")].sort(), ["src/apns.ts", "src/env.ts", "src/lib.ts", "src/push-id.ts"]);
});

test("the import walk follows imports, so the check above cannot pass by reading nothing", () => {
  const fromHandler = reached("api/v1/push.ts");
  assert.ok(fromHandler.has("src/handlers.ts"));
  assert.ok(fromHandler.has("src/limit.ts"));
  assert.ok(fromHandler.has("src/lib.ts"));
});
