#!/usr/bin/env bash
#
# Install Projector's CLI and host plugins without replacing host configuration.
#
#   curl -fsSL https://projector.bot/install.sh | bash
#   curl -fsSL https://projector.bot/install.sh | bash -s -- status
#   ./install.sh [all|cli|claude|codex|status]      from a checkout
#
# Run from a checkout, the installer installs that checkout. Without one --
# piped from curl, or downloaded by `project upgrade` -- it installs a release
# from GitHub instead: the CLI from the source archive of PROJECTOR_REF
# (default v0, the tag every release moves) and the plugins from the
# PROJECTOR_REPO marketplace (default ninjudd/projector). Neither way needs
# git on this machine.

set -euo pipefail

# The directory holding this script, or nothing when bash reads it from stdin.
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
# A checkout is a script with the plugin manifest beside it. A copy of the
# script on its own, such as the one `project upgrade` downloads, is not one.
REPO=""
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/.claude-plugin/plugin.json" ]; then
  REPO="$SCRIPT_DIR"
fi
RELEASE_REPO="${PROJECTOR_REPO:-ninjudd/projector}"
RELEASE_REF="${PROJECTOR_REF:-v0}"
PROJECTOR_PYTHON="${PROJECTOR_PYTHON:-python3}"
VENV_DIR="${PROJECTOR_VENV:-${XDG_DATA_HOME:-$HOME/.local/share}/projector/venv}"
BIN_DIR="${PROJECTOR_BIN_DIR:-$HOME/.local/bin}"
PROJECTOR_USER_ROOT="${PROJECTOR_USER_ROOT:-$HOME}"
CLAUDE_DIR="${PROJECTOR_CLAUDE_DIR:-${CLAUDE_CONFIG_DIR:-$PROJECTOR_USER_ROOT/.claude}}"
CODEX_DIR="${PROJECTOR_CODEX_DIR:-$PROJECTOR_USER_ROOT/.codex}"
CLAUDE_COMMAND="${PROJECTOR_CLAUDE_COMMAND:-claude}"
CODEX_COMMAND="${PROJECTOR_CODEX_COMMAND:-codex}"
GH_COMMAND="${PROJECTOR_GH_COMMAND:-gh}"

legacy_links_for() {
  # Only a checkout ever linked itself into a host's directory.
  [ -n "$REPO" ] || return 0
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

# What the CLI installs from: this checkout, or the release's source archive,
# which pip builds without git.
cli_source() {
  if [ -n "$REPO" ]; then
    printf '%s\n' "$REPO"
  else
    printf 'https://github.com/%s/archive/%s.tar.gz\n' "$RELEASE_REPO" "$RELEASE_REF"
  fi
}

# The command that runs a target again: this checkout's installer, or the
# upgrade, which fetches the released one.
rerun() {
  if [ -n "$REPO" ]; then printf './install.sh %s' "$1"; else printf 'project upgrade %s' "$1"; fi
}

install_cli() {
  local source python
  source="$(cli_source)"
  if command -v pipx >/dev/null 2>&1; then
    # pip caches a download by its URL, and the v0 archive keeps its URL while
    # each release moves the tag, so a cached copy would reinstall the old one.
    local args=(install --force "$source")
    [ -n "$REPO" ] || args+=(--pip-args=--no-cache-dir)
    if python="$(venv_python)"; then
      PIPX_DEFAULT_PYTHON="$python" pipx "${args[@]}"
    else
      pipx "${args[@]}"
    fi
  else
    install_venv "$source"
  fi
}

# Without pipx, the CLI gets a virtual environment of its own, as pipx would
# give it, and a link on PATH. `pip install --user` would write into the
# system Python, which Homebrew's and Debian's refuse (PEP 668).
install_venv() {
  local source="$1"
  if ! "$PROJECTOR_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo "install.sh: Projector needs Python 3.11 or newer as $PROJECTOR_PYTHON; set PROJECTOR_PYTHON to use another" >&2
    return 69
  fi
  [ -x "$VENV_DIR/bin/python" ] || "$PROJECTOR_PYTHON" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --quiet --upgrade --no-cache-dir "$source"
  mkdir -p "$BIN_DIR"
  ln -sf "$VENV_DIR/bin/project" "$BIN_DIR/project"
  printf '%-14s %s\n' "linked" "$BIN_DIR/project -> $VENV_DIR/bin/project"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn_row "not-on-path" "$BIN_DIR is not on PATH -- add it to your shell's PATH to run project" ;;
  esac
}

run_claude() { CLAUDE_CONFIG_DIR="$CLAUDE_DIR" "$CLAUDE_COMMAND" "$@"; }
run_codex() { CODEX_HOME="$CODEX_DIR" "$CODEX_COMMAND" "$@"; }

# The version this install should leave, from the plugin manifest, which
# carries the same version as the CLI: the checkout's, or the release's,
# fetched from GitHub. Offline, or when GitHub cannot answer, there is none to
# compare against.
expected_version() {
  local manifest
  if [ -n "$REPO" ]; then
    manifest="$(cat "$REPO/.claude-plugin/plugin.json")"
  elif [ -n "${PROJECTOR_OFFLINE:-}" ]; then
    return 0
  else
    manifest="$(curl -fsSL --max-time 15 \
      "https://raw.githubusercontent.com/$RELEASE_REPO/$RELEASE_REF/.claude-plugin/plugin.json" 2>/dev/null)" || return 0
  fi
  printf '%s\n' "$manifest" | sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
}

# The gh-stack release Projector pins, from the gh-stack entry in the Claude
# Code marketplace, the same file the host reads it from: the checkout's, or
# the release's, fetched from GitHub. Offline, or when GitHub cannot answer,
# there is none.
pinned_gh_stack() {
  local marketplace
  if [ -n "$REPO" ]; then
    marketplace="$(cat "$REPO/.claude-plugin/marketplace.json" 2>/dev/null)" || return 0
  elif [ -n "${PROJECTOR_OFFLINE:-}" ]; then
    return 0
  else
    marketplace="$(curl -fsSL --max-time 15 \
      "https://raw.githubusercontent.com/$RELEASE_REPO/$RELEASE_REF/.claude-plugin/marketplace.json" 2>/dev/null)" || return 0
  fi
  printf '%s\n' "$marketplace" | python3 -c '
import json, sys
for entry in json.load(sys.stdin).get("plugins", []):
    if entry.get("name") == "gh-stack":
        print(entry.get("source", {}).get("ref", "").lstrip("v"))
' 2>/dev/null || true
}

# Where the plugin installs from: the repository this checkout was cloned
# from, read off its `origin` remote, so a fork installs from the fork and a
# clone of the upstream installs from the upstream. A host refreshes a
# marketplace from its source, so one that reads this checkout installs
# whatever commit happens to be checked out and then reports itself current
# until someone pulls, while one that reads the repository moves with every
# release. The CLI still installs from the checkout, because pipx copies
# source and has no remote to read.
#
# A GitHub remote is reduced to owner/repo, which Claude Code records as a
# `github` marketplace and Codex takes as an HTTPS Git URL. Any other remote
# URL is handed to both hosts as it is. A checkout with no `origin` -- an
# unpacked archive, say -- has nothing but itself to install from.
plugin_repo() {
  local url
  # Without a checkout, the release's repository.
  [ -n "$REPO" ] || { printf '%s\n' "$RELEASE_REPO"; return 0; }
  url="$(git -C "$REPO" remote get-url origin 2>/dev/null)" || return 1
  [ -n "$url" ] || return 1
  case "$url" in
    git@github.com:*)       url="${url#git@github.com:}" ;;
    ssh://git@github.com/*) url="${url#ssh://git@github.com/}" ;;
    https://github.com/*)   url="${url#https://github.com/}" ;;
    http://github.com/*)    url="${url#http://github.com/}" ;;
    *) printf '%s\n' "$url"; return 0 ;;
  esac
  url="${url%/}"
  printf '%s\n' "${url%.git}"
}

# The source to hand a host: owner/repo to Claude Code, the HTTPS URL to
# Codex, a non-GitHub remote as it came, or the checkout itself when the
# checkout has no remote to name.
plugin_source() {
  local repo
  repo="$(plugin_repo)" || { printf '%s\n' "$REPO"; return; }
  case "$repo" in
    *:*|*/*/*) printf '%s\n' "$repo" ;;
    *)
      case "$1" in
        codex) printf 'https://github.com/%s.git\n' "$repo" ;;
        *)     printf '%s\n' "$repo" ;;
      esac ;;
  esac
}

# True when a host's projector marketplace reads a local path -- Claude
# Code's `directory`, Codex's `local` -- which is the kind that installs
# whatever this checkout has checked out, and the one the installer moves.
marketplace_is_local() {
  case "$1" in directory\ *|local\ *) return 0 ;; esac
  return 1
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
  local plugin="${2:-projector@projector}"
  case "$1" in
    claude) run_claude plugin list --json 2>/dev/null | python3 -c '
import json, sys
for entry in json.load(sys.stdin):
    if entry.get("id") == sys.argv[1]:
        print(entry.get("version", "?"))
' "$plugin" 2>/dev/null || true ;;
    codex) run_codex plugin list --json 2>/dev/null | python3 -c '
import json, sys
for entry in json.load(sys.stdin).get("installed", []):
    if entry.get("pluginId") == sys.argv[1]:
        print(entry.get("version", "?"))
' "$plugin" 2>/dev/null || true ;;
  esac
}

# A marketplace that reads a local path is moved to the repository: removed,
# then added from the source above, with a row saying so. One that already
# reads a remote is refreshed from wherever it points, because a user who
# pointed it at a fork meant to. Adding a marketplace the host already has
# changes nothing when the source matches and is refused when it does not,
# which is why the local one is removed first. Likewise `install` leaves an
# installed plugin exactly as it is; `update` is the command that moves it.
install_claude() {
  command -v "$CLAUDE_COMMAND" >/dev/null 2>&1 || {
    echo "install.sh: Claude Code is not installed" >&2
    return 69
  }
  mkdir -p "$CLAUDE_DIR"
  remove_legacy_links claude
  local marketplace source
  marketplace="$(host_marketplace claude)"
  source="$(plugin_source claude)"
  if [ -z "$marketplace" ]; then
    run_claude plugin marketplace add "$source" --scope user
  elif marketplace_is_local "$marketplace" && [ "$source" != "$REPO" ]; then
    run_claude plugin marketplace remove projector
    run_claude plugin marketplace add "$source" --scope user
    printf '%-14s %s\n' "moved" "claude marketplace ${marketplace#* } -> $source"
  else
    run_claude plugin marketplace update projector
  fi
  if [ -n "$(host_plugin_version claude)" ]; then
    run_claude plugin update projector@projector
  else
    run_claude plugin install projector@projector --scope user
  fi
  install_claude_gh_stack
}

# Projector depends on the gh-stack plugin and fails to load without it, but
# `update` neither installs a dependency a plugin newly declares nor moves one
# already installed, so the installer does both by name. A marketplace from
# before Projector depended on gh-stack has no such plugin; that failure
# leaves Projector as it was and is reported rather than fatal.
install_claude_gh_stack() {
  if [ -n "$(host_plugin_version claude gh-stack@projector)" ]; then
    run_claude plugin update gh-stack@projector ||
      warn_row "gh-stack" "claude could not update gh-stack@projector"
  else
    run_claude plugin install gh-stack@projector --scope user ||
      warn_row "gh-stack" "claude could not install gh-stack@projector"
  fi
}

# Codex reads a local marketplace live and snapshots a Git one. A local
# marketplace is moved to the repository the same way, a Git one is
# refreshed, and a local one with nowhere to move to is left as it is, since
# asking Codex to upgrade it is an error rather than a no-op. `add` installs
# the plugin, and installs it again from the marketplace when it is already
# there, so it is the upgrade as well.
install_codex() {
  command -v "$CODEX_COMMAND" >/dev/null 2>&1 || {
    echo "install.sh: Codex is not installed" >&2
    return 69
  }
  mkdir -p "$CODEX_DIR"
  remove_legacy_links codex
  local marketplace source
  marketplace="$(host_marketplace codex)"
  source="$(plugin_source codex)"
  if [ -z "$marketplace" ]; then
    run_codex plugin marketplace add "$source"
  elif marketplace_is_local "$marketplace" && [ "$source" != "$REPO" ]; then
    run_codex plugin marketplace remove projector
    run_codex plugin marketplace add "$source"
    printf '%-14s %s\n' "moved" "codex marketplace ${marketplace#* } -> $source"
  else
    case "$marketplace" in git*) run_codex plugin marketplace upgrade projector ;; esac
  fi
  run_codex plugin add projector@projector
  # Codex has no plugin dependencies, so GitHub's gh-stack skill is a plugin
  # of its own here. Projector works without it, so a marketplace that does
  # not serve it yet is reported rather than fatal.
  run_codex plugin add gh-stack@projector ||
    warn_row "gh-stack" "codex could not install gh-stack@projector"
}

# The `gh stack` extension the stack workflow runs, at least at the release
# Projector pins. Installing it needs no login and is announced; nothing here
# is fatal, because Projector falls back to base branches without it. An
# extension that provides `gh stack` from another owner is someone's choice
# and is left alone.
install_gh_stack() {
  local installed pinned
  if ! command -v "$GH_COMMAND" >/dev/null 2>&1; then
    printf '%-14s %s\n' "skipped" "gh is not installed, so neither is gh stack"
    return 0
  fi
  installed="$(gh_stack_extension)"
  pinned="$(pinned_gh_stack)"
  case "$installed" in
    "")
      if "$GH_COMMAND" extension install github/gh-stack; then
        printf '%-14s %s\n' "installed" "gh stack (github/gh-stack)"
      else
        warn_row "gh-stack" "could not install gh stack -- run gh extension install github/gh-stack"
      fi ;;
    github/gh-stack\ *)
      if [ -n "$pinned" ] && version_older "${installed#* }" "$pinned"; then
        "$GH_COMMAND" extension upgrade stack ||
          warn_row "gh-stack" "could not upgrade gh stack -- run gh extension upgrade stack"
      fi ;;
    *)
      printf '%-14s %s\n' "skipped" "gh stack comes from ${installed%% *}, which the installer leaves alone" ;;
  esac
}

# The repository and version of the extension that provides `gh stack`, as
# `owner/repo version`, or nothing when no extension does.
gh_stack_extension() {
  "$GH_COMMAND" extension list 2>/dev/null | awk -F'\t' '
    $1 == "gh stack" { version = $3; sub(/^v/, "", version); print $2, version; exit }
  ' || true
}

# True when dotted version $1 is older than $2. A version that is not plain
# numbers is never older, so an unusual one is left alone.
version_older() {
  python3 -c '
import sys
def parts(version):
    return tuple(int(part) for part in version.split("."))
sys.exit(0 if parts(sys.argv[1]) < parts(sys.argv[2]) else 1)
' "$1" "$2" 2>/dev/null
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
  local path installed_dir version expected
  if ! path="$(command -v project 2>/dev/null)"; then
    printf '%-14s %s\n' "cli-missing" "project -- run $(rerun cli)"
    return
  fi
  printf '%-14s %s\n' "cli" "$path"

  # Without a checkout there is no source to diff, and a release bumps the
  # version on every change, so the version is the comparison.
  if [ -z "$REPO" ]; then
    version="$(project --version 2>/dev/null | awk '{print $NF}')"
    expected="$(expected_version)"
    if [ -z "$expected" ]; then
      printf '%-14s %s\n' "cli-installed" "${version:-unknown} (no release version to compare against)"
    elif [ "$version" = "$expected" ]; then
      printf '%-14s %s\n' "cli-current" "$version"
    else
      printf '%-14s %s\n' "cli-stale" "installed ${version:-unknown}, release $expected -- run $(rerun cli)"
    fi
    return
  fi

  installed_dir="$(project --package-dir 2>/dev/null)" || true
  if [ -z "$installed_dir" ] || [ ! -d "$installed_dir" ]; then
    # Too old to say where it lives, which is itself the staleness.
    printf '%-14s %s\n' "cli-stale" "installed command cannot report its source -- run ./install.sh cli"
  # The installed package carries the site's assets at site/assets/, which the
  # checkout builds in site/assets/ rather than under src/projector, so each
  # half is compared against its own source.
  elif diff -rq --exclude=__pycache__ --exclude=assets "$installed_dir" "$REPO/src/projector" >/dev/null 2>&1 &&
       diff -rq "$installed_dir/site/assets" "$REPO/site/assets" >/dev/null 2>&1; then
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
  if [ -n "$marketplace" ]; then
    printf '%-14s %s\n' "marketplace" "$host $marketplace"
    if marketplace_is_local "$marketplace" && [ "$(plugin_source "$host")" != "$REPO" ]; then
      warn_row "from-checkout" "$host installs from a checkout and goes stale with it -- run $(rerun "$host") to move it to $(plugin_source "$host")"
    fi
  fi
  installed="$(host_plugin_version "$host")"
  expected="$(expected_version)"
  if [ -z "$installed" ]; then
    printf '%-14s %s\n' "plugin-absent" "$host -- run $(rerun "$host")"
  elif [ -z "$expected" ]; then
    printf '%-14s %s\n' "plugin" "$host $installed (no release version to compare against)"
  elif [ "$installed" = "$expected" ]; then
    printf '%-14s %s\n' "plugin-current" "$host $installed"
  else
    local from="checkout"
    [ -n "$REPO" ] || from="release"
    printf '%-14s %s\n' "plugin-stale" "$host installed $installed, $from $expected -- run $(rerun "$host")"
  fi
}

# The `gh stack` extension against the release Projector pins. It is optional,
# so a missing or old one is a row to act on rather than a failure.
report_gh_stack() {
  local installed pinned version
  if ! command -v "$GH_COMMAND" >/dev/null 2>&1; then
    printf '%-14s %s\n' "gh-missing" "gh -- install the GitHub CLI to use gh stack"
    return
  fi
  installed="$(gh_stack_extension)"
  pinned="$(pinned_gh_stack)"
  version="${installed#* }"
  case "$installed" in
    "")
      printf '%-14s %s\n' "stack-absent" "gh stack -- run $(rerun all)" ;;
    github/gh-stack\ *)
      if [ -z "$pinned" ]; then
        printf '%-14s %s\n' "stack" "gh stack $version (no pinned version to compare against)"
      elif version_older "$version" "$pinned"; then
        printf '%-14s %s\n' "stack-stale" "gh stack $version, pinned $pinned -- run $(rerun all)"
      else
        printf '%-14s %s\n' "stack-current" "gh stack $version"
      fi ;;
    *)
      printf '%-14s %s\n' "stack-other" "gh stack comes from ${installed%% *}" ;;
  esac
}

# A row the reader must not scroll past: a marker always, because a captured
# log has no color, and bold yellow when stdout is a terminal.
warn_row() {
  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    printf '\033[1;33m%-14s ⚠️  %s\033[0m\n' "$1" "$2"
  else
    printf '%-14s ⚠️  %s\n' "$1" "$2"
  fi
}

# pipx copies the checkout, while a host's marketplace usually reads GitHub,
# so a checkout nobody pulled installs an old command beside a current plugin
# and the two version numbers then look like a bug. Fetch the checkout's
# upstream and say how far behind it is. PROJECTOR_OFFLINE=1 skips the fetch
# and compares against the last one. The fetch gives up after fifteen seconds
# of a stalled HTTP transfer, so a bad network delays the row rather than
# hanging the installer on it.
report_checkout() {
  local branch upstream remote behind commits hint note=""
  [ -n "$REPO" ] || return 0
  git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || return 0
  if ! branch="$(git -C "$REPO" symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
    printf '%-14s %s\n' "repo-untracked" "detached HEAD has no upstream to compare against"
    return 0
  fi
  if ! upstream="$(git -C "$REPO" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
    printf '%-14s %s\n' "repo-untracked" "$branch has no upstream to compare against"
    return 0
  fi
  remote="${upstream%%/*}"
  if [ -n "${PROJECTOR_OFFLINE:-}" ]; then
    note=" (offline; compared against the last fetch of $remote)"
  elif ! GIT_TERMINAL_PROMPT=0 git -C "$REPO" -c http.lowSpeedLimit=1 -c http.lowSpeedTime=15 \
      fetch --quiet "$remote" 2>/dev/null; then
    note=" (could not fetch $remote; compared against its last fetch)"
  fi
  if ! behind="$(git -C "$REPO" rev-list --count "HEAD..$upstream" 2>/dev/null)"; then
    printf '%-14s %s\n' "repo-untracked" "$branch tracks $upstream, which no longer exists$note"
    return 0
  fi
  if [ "$behind" -eq 0 ]; then
    printf '%-14s %s\n' "repo-current" "$branch matches $upstream$note"
    return 0
  fi
  if [ "$behind" -eq 1 ]; then commits="commit"; else commits="commits"; fi
  hint="run git pull in $REPO, then run the installer again"
  warn_row "repo-behind" "$branch is $behind $commits behind $upstream -- $hint$note"
}

show_status() {
  [ -n "$REPO" ] || printf '%-14s %s\n' "release" "$RELEASE_REPO $RELEASE_REF"
  report_checkout
  report_cli
  report_plugin claude "$CLAUDE_COMMAND"
  report_plugin codex "$CODEX_COMMAND"
  report_gh_stack

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
    install_gh_stack
    report_checkout
    if [ "$installed_hosts" -eq 0 ]; then
      echo "install.sh: neither Claude Code nor Codex is installed" >&2
      exit 69
    fi
    ;;
  cli) install_cli; report_checkout ;;
  claude) install_claude; report_checkout ;;
  codex) install_codex; report_checkout ;;
  status) show_status ;;
  *)
    echo "usage: install.sh [all|cli|claude|codex|status]" >&2
    exit 64
    ;;
esac
