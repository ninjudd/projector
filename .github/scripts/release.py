#!/usr/bin/env python3
"""Set Projector's one version, or tag a release of it.

    .github/scripts/release.py bump patch|minor|major   raise one part of the version and write it
    .github/scripts/release.py set X.Y.Z                write an exact version instead
    .github/scripts/release.py released                 exit 0 if origin/main's version is fully released, 3 if not
    .github/scripts/release.py tag                      tag vX.Y.Z at origin/main, move the vX major tag, and publish the GitHub release

The Release workflow runs these. Started by hand with patch, minor, or major,
it runs `bump` on a release/vX.Y.Z branch and opens the release pull request.
When a change to pyproject.toml reaches main, it runs `released`, and when that
exits 3 it runs `tag`: that pushes the immutable vX.Y.Z tag, force-moves the
major tag that marketplaces and the site action follow, and creates the
GitHub release for vX.Y.Z with generated notes; --no-release skips that step.

A version is released once its tag and the major tag name the same commit and
its GitHub release exists. `tag` does only the steps the remote lacks, so
running it again finishes a release that stopped partway.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")
VERSION_FILES = ("pyproject.toml", *MANIFESTS)
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ReleaseError(Exception):
    pass


def parse(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if not match:
        raise ReleaseError(f"{version!r} is not X.Y.Z")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def versions(root: Path, read=lambda path: path.read_text()) -> dict[str, str]:
    found = {}
    found["pyproject.toml"] = tomllib.loads(read(root / "pyproject.toml"))["project"]["version"]
    for manifest in MANIFESTS:
        found[manifest] = json.loads(read(root / manifest))["version"]
    return found


def current(root: Path, read=lambda path: path.read_text()) -> str:
    found = versions(root, read)
    if len(set(found.values())) != 1:
        raise ReleaseError("the version files disagree: " + ", ".join(f"{k}={v}" for k, v in found.items()))
    return next(iter(found.values()))


def set_version(root: Path, version: str) -> None:
    if parse(version) <= parse(current(root)):
        raise ReleaseError(f"{version} is not above the current version {current(root)}")
    pyproject = root / "pyproject.toml"
    text, count = re.subn(r'(?m)^version\s*=\s*"[^"]*"', f'version = "{version}"', pyproject.read_text(), count=1)
    if count != 1:
        raise ReleaseError("pyproject.toml has no version line")
    pyproject.write_text(text)
    for manifest in MANIFESTS:
        path = root / manifest
        text, count = re.subn(r'("version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", path.read_text(), count=1)
        if count != 1:
            raise ReleaseError(f"{manifest} has no version field")
        path.write_text(text)


def bump(root: Path, part: str) -> str:
    """Raise the patch, minor, or major number, write it, and return the new version."""
    major, minor, patch = parse(current(root))
    version = {
        "patch": f"{major}.{minor}.{patch + 1}",
        "minor": f"{major}.{minor + 1}.0",
        "major": f"{major + 1}.0.0",
    }[part]
    set_version(root, version)
    return version


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def repo_slug(url: str) -> str:
    url = url.strip()
    for prefix in ("git@github.com:", "ssh://git@github.com/", "https://github.com/"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            return url[:-4] if url.endswith(".git") else url
    return ""


def gh(root: Path, remote: str, *args: str) -> subprocess.CompletedProcess[str]:
    slug = repo_slug(git(root, "remote", "get-url", remote))
    if not slug:
        raise ReleaseError(f"{remote} is not a GitHub repository")
    try:
        return subprocess.run(["gh", *args, "--repo", slug], cwd=root, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ReleaseError("the gh CLI is not installed") from exc


def published(root: Path, remote: str, release: str) -> bool:
    result = gh(root, remote, "release", "view", release, "--json", "tagName")
    if result.returncode == 0:
        return True
    if "release not found" in result.stderr:
        return False
    raise ReleaseError(f"gh release view failed: {result.stderr.strip()}")


def create_release(root: Path, remote: str, release: str) -> None:
    result = gh(root, remote, "release", "create", release, "--verify-tag", "--generate-notes")
    if result.returncode:
        raise ReleaseError(f"gh release create failed: {result.stderr.strip()}")


NOT_RELEASED = 3


class Merged(NamedTuple):
    """The version a branch names, the commit that releases it, and which of its tags the remote has."""

    version: str
    name: str
    major: str
    commit: str
    tagged: bool
    major_moved: bool


def version_at(root: Path, commit: str) -> str:
    return current(root, read=lambda path: git(root, "show", f"{commit}:{path.relative_to(root).as_posix()}"))


def remote_tags(root: Path, remote: str, *names: str) -> dict[str, str]:
    """The commit each named tag peels to on the remote, for the names the remote has."""
    patterns = [pattern for name in names for pattern in (f"refs/tags/{name}", f"refs/tags/{name}^{{}}")]
    commits: dict[str, str] = {}
    for line in git(root, "ls-remote", "--tags", remote, *patterns).splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            commits[name.removesuffix("^{}")] = sha
        else:
            commits.setdefault(name, sha)
    return commits


def check_tagged(root: Path, remote: str, branch: str, head: str, version: str, commit: str) -> None:
    """Refuse a version tag unless the branch has named that version from the tagged commit to its head."""
    if subprocess.run(["git", "merge-base", "--is-ancestor", commit, head], cwd=root, capture_output=True).returncode:
        raise ReleaseError(f"v{version} already names {commit[:9]}, which is not on {remote}/{branch}; "
                           "set a new version first")
    changes = git(root, "log", "--format=%H", f"{commit}..{head}", "--", *VERSION_FILES).split()
    for change in (commit, *changes):
        named = version_at(root, change)
        if named != version:
            raise ReleaseError(f"v{version} already names {commit[:9]}, but {remote}/{branch} names {named} "
                               f"at {change[:9]}, so that tag belongs to another release; set a new version first")


def merged(root: Path, remote: str, branch: str) -> Merged:
    """Read the branch's version on the remote and which of its tags the remote already has.

    Until the version tag exists, the release commit is the branch's head. Once
    it exists, the release commit is the tagged one, so a release that stopped
    partway finishes there even after the branch has moved past it.
    """
    # A failed push can leave this checkout with a tag the remote refused.
    # --force replaces it with the remote's rather than failing the fetch.
    git(root, "fetch", "--quiet", "--force", "--tags", remote, branch)
    head = git(root, "rev-parse", f"refs/remotes/{remote}/{branch}")
    version = version_at(root, head)
    name, major = f"v{version}", f"v{parse(version)[0]}"
    tags = remote_tags(root, remote, name, major)
    commit = tags.get(name, head)
    if name in tags:
        check_tagged(root, remote, branch, head, version, commit)
    return Merged(version, name, major, commit, tagged=name in tags, major_moved=tags.get(major) == commit)


def released(root: Path, remote: str = "origin", branch: str = "main") -> tuple[Merged, list[str]]:
    """The merged version and the parts of its release the remote still lacks."""
    state = merged(root, remote, branch)
    missing = [] if state.tagged else [f"the {state.name} tag"]
    if not state.major_moved:
        missing.append(f"{state.major} at {state.commit[:9]}")
    if not published(root, remote, state.name):
        missing.append("the GitHub release")
    return state, missing


def tag(root: Path, remote: str = "origin", branch: str = "main", github_release: bool = True) -> str:
    state = merged(root, remote, branch)
    did: list[str] = []
    found: list[str] = []
    try:
        if state.tagged:
            found.append(f"the {state.name} tag")
        else:
            # --force replaces only a local tag that a failed push left behind.
            # The push has no --force, so it never replaces a version tag.
            git(root, "tag", "--force", "-a", state.name, state.commit, "-m", f"Projector {state.version}")
            git(root, "push", "--quiet", remote, f"refs/tags/{state.name}")
            did.append(f"tagged {state.name}")
        if state.major_moved:
            found.append(state.major)
        else:
            git(root, "tag", "--force", "-a", state.major, state.commit, "-m", f"Projector {state.version}")
            git(root, "push", "--quiet", "--force", remote, f"refs/tags/{state.major}")
            did.append(f"moved {state.major}")
        if github_release:
            if published(root, remote, state.name):
                found.append("the GitHub release")
            else:
                create_release(root, remote, state.name)
                did.append("published the GitHub release")
    except ReleaseError as exc:
        before = f" Before that it {', '.join(did)}." if did else ""
        raise ReleaseError(f"{exc}.{before} Run tag again, or re-run the Release workflow, "
                           f"to finish {state.name}") from exc
    summary = f"{state.name} at {state.commit[:9]} on {remote}/{branch}: {', '.join(did) or 'nothing to do'}"
    return summary + (f" (already done: {', '.join(found)})" if found else "")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("set", help="write a new version to every version file")
    s.add_argument("version")
    b = sub.add_parser("bump", help="raise the patch, minor, or major number and print the new version")
    b.add_argument("part", choices=("patch", "minor", "major"))
    r = sub.add_parser("released", help=f"exit 0 when the merged version is fully released, {NOT_RELEASED} when it is not")
    r.add_argument("--remote", default="origin")
    r.add_argument("--branch", default="main")
    t = sub.add_parser("tag", help="do the release steps the remote lacks: the version tag, the major tag, the GitHub release")
    t.add_argument("--remote", default="origin")
    t.add_argument("--branch", default="main")
    t.add_argument("--no-release", action="store_true", help="push the tags without creating the GitHub release")
    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            set_version(root, args.version)
            print(f"set {args.version} in pyproject.toml and both plugin manifests")
        elif args.command == "bump":
            print(bump(root, args.part))
        elif args.command == "released":
            state, missing = released(root, args.remote, args.branch)
            if missing:
                print(f"{state.name} is not released yet; it still needs {', '.join(missing)}")
                return NOT_RELEASED
            print(f"{state.name} is released")
        else:
            print(tag(root, args.remote, args.branch, github_release=not args.no_release))
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
