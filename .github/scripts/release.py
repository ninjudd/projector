#!/usr/bin/env python3
"""Set Projector's one version, or tag a release of it.

    .github/scripts/release.py bump patch|minor|major   raise one part of the version and write it
    .github/scripts/release.py set X.Y.Z                write an exact version instead
    .github/scripts/release.py released                 exit 0 if origin/main's version is tagged, 3 if not
    .github/scripts/release.py tag                      tag vX.Y.Z at origin/main, move the vX major tag, and publish the GitHub release

The Release workflow runs these. Started by hand with patch, minor, or major,
it runs `bump` on a release/vX.Y.Z branch and opens the release pull request.
When a change to setup.cfg reaches main, it runs `released`, and when that
exits 3 it runs `tag`: that pushes the immutable vX.Y.Z tag, force-moves the
major tag that marketplaces and the site action follow, and creates the
GitHub release for vX.Y.Z with generated notes; --no-release skips that step.
"""

from __future__ import annotations

import argparse
import configparser
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")
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
    config = configparser.ConfigParser()
    config.read_string(read(root / "setup.cfg"))
    found["setup.cfg"] = config["metadata"]["version"]
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
    setup = root / "setup.cfg"
    text, count = re.subn(r"(?m)^version\s*=.*$", f"version = {version}", setup.read_text(), count=1)
    if count != 1:
        raise ReleaseError("setup.cfg has no version line")
    setup.write_text(text)
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


def create_release(root: Path, remote: str, release: str) -> None:
    slug = repo_slug(git(root, "remote", "get-url", remote))
    if not slug:
        raise ReleaseError(f"{remote} is not a GitHub repository")
    try:
        subprocess.run(["gh", "release", "create", release, "--repo", slug, "--verify-tag", "--generate-notes"],
                       cwd=root, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ReleaseError("the gh CLI is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(f"gh release create failed: {exc.stderr.strip()}") from exc


NOT_RELEASED = 3


def merged(root: Path, remote: str, branch: str) -> tuple[str, str, bool]:
    """The branch's commit on the remote, the version it names, and whether that version is tagged."""
    git(root, "fetch", "--quiet", "--tags", remote, branch)
    commit = git(root, "rev-parse", f"refs/remotes/{remote}/{branch}")
    version = current(root, read=lambda path: git(root, "show", f"{commit}:{path.relative_to(root).as_posix()}"))
    return commit, version, bool(git(root, "tag", "--list", f"v{version}"))


def tag(root: Path, remote: str = "origin", branch: str = "main", github_release: bool = True) -> str:
    commit, version, tagged = merged(root, remote, branch)
    release, major = f"v{version}", f"v{parse(version)[0]}"
    if tagged:
        raise ReleaseError(f"{release} already exists; set a new version first")
    git(root, "tag", "-a", release, commit, "-m", f"Projector {version}")
    git(root, "tag", "-f", "-a", major, commit, "-m", f"Projector {version}")
    git(root, "push", "--quiet", remote, f"refs/tags/{release}")
    git(root, "push", "--quiet", "--force", remote, f"refs/tags/{major}")
    done = f"tagged {release} and moved {major} to {commit[:9]} on {remote}/{branch}"
    if not github_release:
        return done
    try:
        create_release(root, remote, release)
    except ReleaseError as exc:
        raise ReleaseError(f"{release} and {major} are pushed, but the GitHub release was not created ({exc}); "
                           f"finish with: gh release create {release} --verify-tag --generate-notes") from exc
    return f"{done}, and published the {release} GitHub release"


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("set", help="write a new version to every version file")
    s.add_argument("version")
    b = sub.add_parser("bump", help="raise the patch, minor, or major number and print the new version")
    b.add_argument("part", choices=("patch", "minor", "major"))
    r = sub.add_parser("released", help=f"exit 0 when the merged version is tagged, {NOT_RELEASED} when it is not")
    r.add_argument("--remote", default="origin")
    r.add_argument("--branch", default="main")
    t = sub.add_parser("tag", help="tag the merged version and move the major tag")
    t.add_argument("--remote", default="origin")
    t.add_argument("--branch", default="main")
    t.add_argument("--no-release", action="store_true", help="push the tags without creating the GitHub release")
    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            set_version(root, args.version)
            print(f"set {args.version} in setup.cfg and both plugin manifests")
        elif args.command == "bump":
            print(bump(root, args.part))
        elif args.command == "released":
            _, version, tagged = merged(root, args.remote, args.branch)
            print(f"v{version} is {'released' if tagged else 'not released yet'}")
            return 0 if tagged else NOT_RELEASED
        else:
            print(tag(root, args.remote, args.branch, github_release=not args.no_release))
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
