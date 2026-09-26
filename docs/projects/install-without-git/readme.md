---
status: completed
priority: now
---

# Install and upgrade without git

## 1. Outcome

Anyone can install Projector with one command and keep it current with
another, and neither needs git or a clone:

```sh
curl -fsSL https://projector.bot/install.sh | bash
project upgrade
```

## 2. Acceptance criteria

- Piped from curl, `install.sh` installs the CLI from the newest release and
  the plugin for each host present, with the same targets and status rows as
  from a checkout. Run from a checkout, it behaves as before.
- `https://projector.bot/install.sh` always serves the newest release's
  installer.
- `project upgrade` upgrades an install that did not come from a checkout: one
  from the curl installer, a release archive, or a Git URL.
- A machine without `pipx` still gets an isolated `project` on its PATH.
- The website and the README lead with the one-command install.

## 3. Design

**One installer, two modes.** `install.sh` is a checkout's installer when the
plugin manifest sits beside it, and a release installer otherwise: piped from
curl, bash reads it from stdin with no file beside it, and the copy `project
upgrade` downloads sits alone in a temporary directory. A release install
takes the CLI from GitHub's source archive of `PROJECTOR_REF` and the plugins
from the `PROJECTOR_REPO` marketplace, so there is one installer to maintain,
not a second script beside it.

**Releases need no new artifact.** `PROJECTOR_REF` defaults to `v0`, the tag
every release already moves, and pip builds the package from GitHub's archive
of it, so the Release workflow publishes nothing new. The archive keeps its
URL while the tag moves, so the installer passes `--no-cache-dir` to pip, or a
cached copy would reinstall the old release. `PROJECTOR_REF` pins a release,
and `PROJECTOR_REPO` installs from a fork.

**projector.bot proxies the tagged installer.** The website rewrites
`/install.sh` to `install.sh` at the `v0` tag on
raw.githubusercontent.com. The URL therefore serves the installer of the
newest release rather than of `main`, which the website deploys from and
which can run ahead of the release the installer installs. The rewrite
proxies instead of redirecting, so the command reads as one request to
projector.bot. The script uses bash features, so the command pipes to `bash`,
not `sh`.

**`upgrade` reruns the released installer.** For an install pip recorded as a
checkout, `project upgrade` runs that checkout's `install.sh` as before. For
any other install it downloads the installer from projector.bot, or from
`install.sh` at `PROJECTOR_REF` in a GitHub fork it was installed from, with
`PROJECTOR_REPO` set to the fork, and runs it with the same targets.
Previously a Git URL install could not upgrade at all.

**Without pipx, a virtual environment of its own.** `pip install --user`
writes into the system Python, which Homebrew's and Debian's refuse
(PEP 668). The installer instead creates a virtual environment under
`~/.local/share/projector`, as pipx would, links `project` into
`~/.local/bin`, and warns when that directory is not on PATH. It checks for
Python 3.11 or newer first, and `PROJECTOR_PYTHON` names another interpreter.

**Status compares versions.** A release install has no source to diff, so
`status` compares the installed CLI and plugin versions with the release's
manifest, fetched from GitHub. Offline, it reports the versions without a
verdict.

## 4. Rollout

The website serves the installer at `v0`, and this change reaches `v0` only
with the next release. Until that release, the one-command install on the
website runs the previous installer, which cannot install without a checkout.
So a release follows this change's merge directly.

## 5. Outcome

Shipped, pending the release in section 4.

- `tests/test_install.py` pipes the installer into bash from a directory that
  is not a checkout. It covers installing the CLI from the release archive,
  a pinned ref and a fork, the plugins from the GitHub marketplace, moving a
  marketplace left reading a checkout, status against the release and
  offline, and the virtual-environment fallback with its Python check.
- `tests/test_projector.py` covers `upgrade` running the released installer
  for archive, Git, and fork installs, choosing projector.bot or the fork,
  and naming an installer it cannot fetch.
- With real pipx in isolated directories, the piped installer built 0.5.6
  from the `v0` archive.
- The local website served `/install.sh` through the rewrite as `text/plain`.
