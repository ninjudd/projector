#!/bin/sh
# Type-checks the hooks module against the declarations Claude Code writes to
# .claude-plugin/types/ when an interactive session loads this checkout with
# --plugin-dir. Nothing else provides them, so CI cannot run this check.
set -eu
cd "$(dirname "$0")/.."

if [ ! -f .claude-plugin/types/tsconfig.json ]; then
  echo "hooks/typecheck.sh: Claude Code's types are missing from .claude-plugin/types/." >&2
  echo "Start an interactive session with \`claude --plugin-dir .\` from the repository root" >&2
  echo "once, so Claude Code writes them, then run this again." >&2
  exit 1
fi
if [ ! -x site/node_modules/.bin/tsc ]; then
  echo "hooks/typecheck.sh: TypeScript is missing. Run \`npm ci\` in site/ first." >&2
  exit 1
fi
exec site/node_modules/.bin/tsc -p .
