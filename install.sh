#!/usr/bin/env bash
#
# Install Projector's CLI and host plugins without replacing host configuration.
#
#   ./install.sh all
#   ./install.sh cli
#   ./install.sh claude
#   ./install.sh codex
#   ./install.sh status

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECTOR_USER_ROOT="${PROJECTOR_USER_ROOT:-$HOME}"
CLAUDE_DIR="${PROJECTOR_CLAUDE_DIR:-${CLAUDE_CONFIG_DIR:-$PROJECTOR_USER_ROOT/.claude}}"
CODEX_DIR="${PROJECTOR_CODEX_DIR:-$PROJECTOR_USER_ROOT/.codex}"
CLAUDE_COMMAND="${PROJECTOR_CLAUDE_COMMAND:-claude}"
CODEX_COMMAND="${PROJECTOR_CODEX_COMMAND:-codex}"

legacy_links_for() {
  case "$1" in
    claude)
      printf '%s|%s\n' "$REPO/AGENTS.md" "$PROJECTOR_USER_ROOT/CLAUDE.md"
      printf '%s|%s\n' "$REPO/skills" "$CLAUDE_DIR/skills"
      printf '%s|%s\n' "$REPO/claude/agents" "$CLAUDE_DIR/agents"
      printf '%s|%s\n' "$REPO/claude/commands" "$CLAUDE_DIR/commands"
      ;;
    codex)
      printf '%s|%s\n' "$REPO/AGENTS.md" "$CODEX_DIR/AGENTS.md"
      printf '%s|%s\n' "$REPO/skills" "$CODEX_DIR/skills"
      printf '%s|%s\n' "$REPO/codex/prompts" "$CODEX_DIR/prompts"
      ;;
  esac
}

remove_legacy_links() {
  local host="$1" source target
  while IFS='|' read -r source target; do
    if [ -L "$target" ] && [ "$(readlink "$target")" = "$source" ]; then
      unlink "$target"
      printf '%-14s %s\n' "unlinked" "$target"
    fi
  done < <(legacy_links_for "$host")
}

# The interpreter an existing pipx venv was created with. pipx builds the
# package under its default Python to learn the package name, and some
# versions recreate the venv with it, so a default older than
# `python_requires` fails the reinstall even though the venv already holds a
# Python that satisfies it. The venv's own pyvenv.cfg names that Python, and
# any Python new enough for this package writes the `executable` line.
venv_python() {
  local venvs
  venvs="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)" || return 1
  [ -f "$venvs/projector-cli/pyvenv.cfg" ] || return 1
  awk -F' = ' '$1 == "executable" { print $2 }' "$venvs/projector-cli/pyvenv.cfg" | grep .
}

install_cli() {
  if command -v pipx >/dev/null 2>&1; then
    local python
    if python="$(venv_python)"; then
      PIPX_DEFAULT_PYTHON="$python" pipx install --force "$REPO"
    else
      pipx install --force "$REPO"
    fi
  else
    python3 -m pip install --user --upgrade "$REPO"
  fi
}

run_claude() { CLAUDE_CONFIG_DIR="$CLAUDE_DIR" "$CLAUDE_COMMAND" "$@"; }
run_codex() { CODEX_HOME="$CODEX_DIR" "$CODEX_COMMAND" "$@"; }

# The plugin version this checkout installs, from the manifest.
checkout_plugin_version() {
  sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO/.claude-plugin/plugin.json" | head -1
}

# What a host already has. Each host lists its marketplaces and plugins as
# JSON, which python3 -- required by the CLI anyway -- reads. A host that
# cannot answer reads as having nothing, and the first-install commands that
# follow from that are ones both hosts accept on a repeat.
host_marketplace() {
  case "$1" in
    claude) run_claude plugin marketplace list --json 2>/dev/null | python3 -c '
import json, sys
for entry in json.load(sys.stdin):
    if entry.get("name") == "projector":
        print(entry.get("source", "?"), entry.get("path") or entry.get("repo") or entry.get("url") or "")
' 2>/dev/null || true ;;
    codex) run_codex plugin marketplace list --json 2>/dev/null | python3 -c '
import json, sys
for entry in json.load(sys.stdin).get("marketplaces", []):
    if entry.get("name") == "projector":
        source = entry.get("marketplaceSource", {})
        print(source.get("sourceType", "?"), source.get("source", ""))
' 2>/dev/null || true ;;
  esac
}

host_plugin_version() {
  case "$1" in
    claude) run_claude plugin list --json 2>/dev/null | python3 -c '
import json, sys
for entry in json.load(sys.stdin):
    if entry.get("id") == "projector@projector":
        print(entry.get("version", "?"))
' 2>/dev/null || true ;;
    codex) run_codex plugin list --json 2>/dev/null | python3 -c '
import json, sys
for entry in json.load(sys.stdin).get("installed", []):
    if entry.get("pluginId") == "projector@projector":
        print(entry.get("version", "?"))
' 2>/dev/null || true ;;
  esac
}

# A marketplace the host already has is refreshed from wherever it points,
# this checkout or the GitHub repository. Adding it again changes nothing
# when the source matches and is refused when it does not. Likewise
# `install` leaves an installed plugin exactly as it is; `update` is the
# command that moves it.
install_claude() {
  command -v "$CLAUDE_COMMAND" >/dev/null 2>&1 || {
    echo "install.sh: Claude Code is not installed" >&2
    return 69
  }
  mkdir -p "$CLAUDE_DIR"
  remove_legacy_links claude
  if [ -n "$(host_marketplace claude)" ]; then
    run_claude plugin marketplace update projector
  else
    run_claude plugin marketplace add "$REPO" --scope user
  fi
  if [ -n "$(host_plugin_version claude)" ]; then
    run_claude plugin update projector@projector
  else
    run_claude plugin install projector@projector --scope user
  fi
}

# Codex reads a local marketplace live and snapshots a Git one, so only a
# Git marketplace has anything to refresh. `add` installs the plugin, and
# installs it again from the marketplace when it is already there, so it is
# the upgrade as well.
install_codex() {
  command -v "$CODEX_COMMAND" >/dev/null 2>&1 || {
    echo "install.sh: Codex is not installed" >&2
    return 69
  }
  mkdir -p "$CODEX_DIR"
  remove_legacy_links codex
  case "$(host_marketplace codex)" in
    "") run_codex plugin marketplace add "$REPO" ;;
    git*) run_codex plugin marketplace upgrade projector ;;
  esac
  run_codex plugin add projector@projector
}

# pipx and `pip install --user` both install a copy of the source, so a
# checkout that has moved on leaves the command behind with nothing saying so.
# The symlink still resolves and every old subcommand still works, while a
# subcommand added since the install reports itself as an invalid choice --
# which reads as a broken CLI rather than an old one.
#
# Compare what is installed rather than what it calls itself. A version can
# only answer this if it is bumped on every CLI change, and a version nobody
# bumped reports a stale command as current -- the false reassurance this
# check exists to prevent. The installed copy is right there to diff.
report_cli() {
  local path installed_dir version
  if ! path="$(command -v project 2>/dev/null)"; then
    printf '%-14s %s\n' "cli-missing" "project"
    return
  fi
  printf '%-14s %s\n' "cli" "$path"

  installed_dir="$(project --package-dir 2>/dev/null)" || true
  if [ -z "$installed_dir" ] || [ ! -d "$installed_dir" ]; then
    # Too old to say where it lives, which is itself the staleness.
    printf '%-14s %s\n' "cli-stale" "installed command cannot report its source -- run ./install.sh cli"
  elif diff -rq --exclude=__pycache__ "$installed_dir" "$REPO/src/projector" >/dev/null 2>&1; then
    version="$(project --version 2>/dev/null | awk '{print $NF}')"
    printf '%-14s %s\n' "cli-current" "${version:-matches checkout}"
  else
    printf '%-14s %s\n' "cli-stale" "installed command differs from this checkout -- run ./install.sh cli"
  fi
}

# The plugin goes stale the same way the CLI does: a host caches the version
# it installed, and a checkout that moves on says nothing. Compare the
# installed version with the manifest here, and name the marketplace source,
# because an upgrade refreshes from that source rather than from this
# checkout when the two differ.
report_plugin() {
  local host="$1" command="$2" marketplace installed expected
  command -v "$command" >/dev/null 2>&1 || {
    printf '%-14s %s\n' "host-missing" "$host"
    return
  }
  marketplace="$(host_marketplace "$host")"
  [ -n "$marketplace" ] && printf '%-14s %s\n' "marketplace" "$host $marketplace"
  installed="$(host_plugin_version "$host")"
  expected="$(checkout_plugin_version)"
  if [ -z "$installed" ]; then
    printf '%-14s %s\n' "plugin-absent" "$host -- run ./install.sh $host"
  elif [ "$installed" = "$expected" ]; then
    printf '%-14s %s\n' "plugin-current" "$host $installed"
  else
    printf '%-14s %s\n' "plugin-stale" "$host installed $installed, checkout $expected -- run ./install.sh $host"
  fi
}

show_status() {
  report_cli
  report_plugin claude "$CLAUDE_COMMAND"
  report_plugin codex "$CODEX_COMMAND"

  local host source target state
  for host in claude codex; do
    while IFS='|' read -r source target; do
      state="absent"
      if [ -L "$target" ]; then
        state="other-link"
        [ "$(readlink "$target")" = "$source" ] && state="legacy-link"
      elif [ -e "$target" ]; then
        state="user-owned"
      fi
      printf '%-14s %s\n' "$state" "$target"
    done < <(legacy_links_for "$host")
  done
}

case "${1:-all}" in
  all)
    install_cli
    installed_hosts=0
    if command -v "$CLAUDE_COMMAND" >/dev/null 2>&1; then
      install_claude
      installed_hosts=$((installed_hosts + 1))
    else
      printf '%-14s %s\n' "skipped" "Claude Code is not installed"
    fi
    if command -v "$CODEX_COMMAND" >/dev/null 2>&1; then
      install_codex
      installed_hosts=$((installed_hosts + 1))
    else
      printf '%-14s %s\n' "skipped" "Codex is not installed"
    fi
    if [ "$installed_hosts" -eq 0 ]; then
      echo "install.sh: neither Claude Code nor Codex is installed" >&2
      exit 69
    fi
    ;;
  cli) install_cli ;;
  claude) install_claude ;;
  codex) install_codex ;;
  status) show_status ;;
  *)
    echo "usage: ./install.sh [all|cli|claude|codex|status]" >&2
    exit 64
    ;;
esac
