#!/bin/sh
# Type-checks the hooks module against the declarations Claude Code writes to
# .claude-plugin/types/ when an interactive session loads a checkout with
# --plugin-dir. Nothing else provides them, so CI cannot run this check. They
# depend mainly on the Claude Code version, so a worktree that has none
# borrows the main checkout's. A worktree with no tsc of its own borrows the
# main checkout's too, which works because tsc reads its libraries from beside
# itself.
set -eu
cd "$(dirname "$0")/.."

types=.claude-plugin/types/tsconfig.json
tsc=site/node_modules/.bin/tsc
main=$(git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')

if [ ! -x "$tsc" ]; then
  if [ -z "$main" ] || [ ! -x "$main/$tsc" ]; then
    echo "hooks/typecheck.sh: TypeScript is missing from site/node_modules/." >&2
    echo "Run \`npm ci\` in site/ once, in this checkout or in the main checkout," >&2
    echo "then run this again." >&2
    exit 1
  fi
  echo "hooks/typecheck.sh: using TypeScript from $main" >&2
  tsc=$main/$tsc
fi
if [ -f "$types" ]; then
  exec "$tsc" -p .
fi

if [ -z "$main" ] || [ ! -f "$main/$types" ]; then
  echo "hooks/typecheck.sh: Claude Code's types are missing from .claude-plugin/types/." >&2
  echo "Start an interactive session with \`claude --plugin-dir .\` once, from the root of" >&2
  echo "this checkout or of the main checkout, so Claude Code writes them, then run this again." >&2
  exit 1
fi

echo "hooks/typecheck.sh: using Claude Code's types from $main" >&2
config=$(mktemp -d)
trap 'rm -rf "$config"' EXIT
trap 'exit 1' HUP INT TERM
json() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
cat > "$config/tsconfig.json" <<EOF
{
  "extends": "$(json "$main/$types")",
  "include": ["$(json "$(pwd -P)/hooks")"]
}
EOF
"$tsc" -p "$config"
