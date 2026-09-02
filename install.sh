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
      printf '%-12s %s\n' "unlinked" "$target"
    fi
  done < <(legacy_links_for "$host")
}

install_cli() {
  if command -v pipx >/dev/null 2>&1; then
    pipx install --force "$REPO"
  else
    python3 -m pip install --user --upgrade "$REPO"
  fi
}

install_claude() {
  command -v "$CLAUDE_COMMAND" >/dev/null 2>&1 || {
    echo "install.sh: Claude Code is not installed" >&2
    return 69
  }
  mkdir -p "$CLAUDE_DIR"
  remove_legacy_links claude
  CLAUDE_CONFIG_DIR="$CLAUDE_DIR" "$CLAUDE_COMMAND" plugin marketplace add "$REPO" --scope user
  CLAUDE_CONFIG_DIR="$CLAUDE_DIR" "$CLAUDE_COMMAND" plugin install projector@projector --scope user
}

install_codex() {
  command -v "$CODEX_COMMAND" >/dev/null 2>&1 || {
    echo "install.sh: Codex is not installed" >&2
    return 69
  }
  mkdir -p "$CODEX_DIR"
  remove_legacy_links codex
  CODEX_HOME="$CODEX_DIR" "$CODEX_COMMAND" plugin marketplace add "$REPO"
  CODEX_HOME="$CODEX_DIR" "$CODEX_COMMAND" plugin add projector@projector
}

repo_cli_version() {
  sed -n 's/^version[[:space:]]*=[[:space:]]*//p' "$REPO/setup.cfg" | head -1
}

# pipx and `pip install --user` both install a copy of the source, so a
# checkout that has moved on leaves the command behind with nothing saying so.
# The symlink still resolves and every old subcommand still works, while a
# subcommand added since the install reports itself as an invalid choice --
# which reads as a broken CLI rather than an old one.
report_cli() {
  local path installed repo
  if ! path="$(command -v project 2>/dev/null)"; then
    printf '%-12s %s\n' "cli-missing" "project"
    return
  fi
  printf '%-12s %s\n' "cli" "$path"

  repo="$(repo_cli_version)"
  installed="$(project --version 2>/dev/null | awk '{print $NF}')" || true
  if [ -z "$installed" ]; then
    # No --version at all: the install predates this reporting, which is
    # itself the staleness being reported.
    printf '%-12s %s\n' "cli-stale" "installed version unknown, repo $repo -- run ./install.sh cli"
  elif [ "$installed" = "$repo" ]; then
    printf '%-12s %s\n' "cli-version" "$installed"
  else
    printf '%-12s %s\n' "cli-stale" "installed $installed, repo $repo -- run ./install.sh cli"
  fi
}

show_status() {
  report_cli

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
      printf '%-12s %s\n' "$state" "$target"
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
      printf '%-12s %s\n' "skipped" "Claude Code is not installed"
    fi
    if command -v "$CODEX_COMMAND" >/dev/null 2>&1; then
      install_codex
      installed_hosts=$((installed_hosts + 1))
    else
      printf '%-12s %s\n' "skipped" "Codex is not installed"
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
