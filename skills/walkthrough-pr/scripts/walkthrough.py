#!/usr/bin/env python3
"""Build pull request walkthrough pages from specs and publish them.

    walkthrough.py init    --repo OWNER/NAME --pr N --spec SPEC [--diff FILE]
    walkthrough.py build   --spec SPEC --out DIR [--diff FILE] [--at-head]
    walkthrough.py site    --root DIR --out DIR
    walkthrough.py publish  --spec SPEC [--remote NAME] [--no-dispatch]
    walkthrough.py workflow [--action-ref REF] [--write]

`init` writes a skeleton spec: the pull request's metadata, its merge base
and head, and every changed file in one unassigned group.

`build` checks that every changed file sits in exactly one group and writes
DIR/index.html with the renderer beside it. It refuses when the pull request
has moved past the spec's head, unless --at-head asks for the recorded head
exactly, which is how the Pages site rebuilds older walkthroughs.

`site` builds every ROOT/<number>/<head>/spec.json into a Pages site with an
index and a stable ROOT/<number>/ link to each pull request's newest head.

`publish` commits a spec to the hidden ref refs/projector/walkthroughs
without touching the checkout, then sends the repository_dispatch event that
runs the walkthroughs workflow. A hidden ref is not a branch: GitHub lists no
branch and offers no pull request for it, and clones do not fetch it.

`workflow` prints the workflow file a repository adds to its default branch
once, or writes it with --write.

The diff comes from GitHub's compare API for the spec's merge base and head.
Pass --diff for a pull request too large for it, or to build offline.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SPEC_VERSION = 1
PAGES_REF = "refs/projector/walkthroughs"
DISPATCH_EVENT = "projector-walkthroughs"
PAGES_ROOT = "walkthroughs"
WORKFLOW_PATH = ".github/workflows/walkthroughs.yml"
HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1"
FONTS = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"

LANGS = {
    ".go": "go", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".py": "python", ".rb": "ruby", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".proto": "protobuf", ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".md": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "ini", ".cfg": "ini", ".sql": "sql", ".css": "css",
    ".scss": "scss", ".html": "xml", ".xml": "xml", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cs": "csharp", ".php": "php", ".tf": "ini",
}
GENERATED_SUFFIXES = (".pb.go", ".swagger.json", ".pb.ts", "_pb2.py", ".lock", "-lock.json", ".snap")
GENERATED_PARTS = ("/gen/", "/generated/", "/mocks/", "/__generated__/")

WORKFLOW = """name: Walkthroughs
on:
  repository_dispatch:
    types: [{event}]
  workflow_dispatch:
permissions:
  contents: read
  pull-requests: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  publish:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.walkthroughs.outputs.page_url }}}}
    steps:
      - id: walkthroughs
        uses: ninjudd/projector/actions/walkthroughs@{ref}
"""


class SpecError(Exception):
    pass


# Diff parsing

def lang_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    if name == "dockerfile":
        return "dockerfile"
    if name == "makefile":
        return "makefile"
    dot = name.rfind(".")
    return LANGS.get(name[dot:], "") if dot >= 0 else ""


def kind_of(path: str) -> str:
    lower = path.lower()
    if lower.endswith(GENERATED_SUFFIXES) or any(part in "/" + lower for part in GENERATED_PARTS):
        return "generated"
    name = lower.rsplit("/", 1)[-1]
    if (
        name.endswith(("_test.go", "_test.py", ".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js", "_spec.rb"))
        or name.startswith("test_")
        or "/tests/" in "/" + lower
        or "/__tests__/" in lower
    ):
        return "test"
    if lower.endswith((".md", ".mdx", ".rst", ".adoc")):
        return "docs"
    return ""


def parse_diff(text: str) -> list[dict]:
    files: list[dict] = []
    cur = hunk = None
    old_no = new_no = 0
    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            cur = {"path": raw.split(" b/", 1)[-1], "new": False, "deleted": False, "adds": 0, "dels": 0, "hunks": []}
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if raw.startswith("new file mode"):
            cur["new"] = True
        elif raw.startswith("deleted file mode"):
            cur["deleted"] = True
        elif raw.startswith("rename to "):
            cur["path"] = raw[len("rename to "):]
        elif raw.startswith("@@"):
            head = raw.split("@@")[1].split()
            old_no = int(head[0].lstrip("-").split(",")[0])
            new_no = int(head[1].lstrip("+").split(",")[0])
            hunk = {"header": raw, "lines": []}
            cur["hunks"].append(hunk)
        elif hunk is None:
            continue
        elif raw.startswith("\\"):
            hunk["lines"].append(["m", "", "", raw])
        elif raw.startswith("+"):
            hunk["lines"].append(["a", "", new_no, raw[1:]])
            new_no += 1
            cur["adds"] += 1
        elif raw.startswith("-"):
            hunk["lines"].append(["d", old_no, "", raw[1:]])
            old_no += 1
            cur["dels"] += 1
        elif raw.startswith(" "):
            hunk["lines"].append(["c", old_no, new_no, raw[1:]])
            old_no += 1
            new_no += 1
    for f in files:
        f["lang"] = lang_of(f["path"])
        f["kind"] = kind_of(f["path"])
        f["id"] = "f-" + hashlib.sha1(f["path"].encode()).hexdigest()[:10]
        f["anchor"] = "diff-" + hashlib.sha256(f["path"].encode()).hexdigest()
    return files


# GitHub

def gh(*args: str) -> str:
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    except FileNotFoundError as exc:
        raise SpecError("the gh CLI is not installed; pass --diff and fill the pr fields by hand") from exc
    except subprocess.CalledProcessError as exc:
        raise SpecError(f"gh {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def merge_base(repo: str, base_ref: str, head: str) -> str:
    return gh("api", f"repos/{repo}/compare/{base_ref}...{head}", "--jq", ".merge_base_commit.sha").strip()


def pr_metadata(repo: str, number: int) -> dict:
    raw = json.loads(gh("pr", "view", str(number), "--repo", repo, "--json", "title,headRefOid,baseRefName"))
    return {
        "repo": repo,
        "number": number,
        "title": raw["title"],
        "head": raw["headRefOid"],
        "base": merge_base(repo, raw["baseRefName"], raw["headRefOid"]),
        "baseRef": raw["baseRefName"],
    }


def fetch_diff(repo: str, base: str, head: str) -> str:
    return gh("api", "-H", "Accept: application/vnd.github.diff", f"repos/{repo}/compare/{base}...{head}")


# Specs and pages

def validate(spec: dict, files: list[dict]) -> None:
    if spec.get("version") != SPEC_VERSION:
        raise SpecError(f"spec version must be {SPEC_VERSION}")
    for key in ("repo", "number", "head", "title"):
        if not spec.get("pr", {}).get(key):
            raise SpecError(f"pr.{key} is required")
    groups = spec.get("groups") or []
    if not groups:
        raise SpecError("the spec has no groups")
    known = {f["path"] for f in files}
    seen: dict[str, str] = {}
    problems: list[str] = []
    ids: set[str] = set()
    for g in groups:
        gid = g.get("id")
        if not gid or not g.get("title"):
            problems.append("every group needs an id and a title")
            continue
        if gid in ids:
            problems.append(f"group id {gid!r} is used twice")
        ids.add(gid)
        for fs in g.get("files") or []:
            path = fs.get("path")
            if path not in known:
                problems.append(f"{gid}: {path} is not in the diff")
            elif path in seen:
                problems.append(f"{path} is in both {seen[path]} and {gid}")
            else:
                seen[path] = gid
        for c in g.get("checks") or []:
            if c.get("kind") not in ("verify", "flag") or not c.get("text"):
                problems.append(f"{gid}: each check needs kind verify or flag and a text")
    for path in sorted(known - set(seen)):
        problems.append(f"{path} is in no group")
    if problems:
        raise SpecError("the spec does not match the diff:\n  " + "\n  ".join(problems))


def stats(files: list[dict]) -> dict:
    out = {"files": len(files), "adds": 0, "dels": 0, "hand": 0, "test": 0, "generated": 0, "docs": 0}
    for f in files:
        out["adds"] += f["adds"]
        out["dels"] += f["dels"]
        out[f["kind"] or "hand"] += f["adds"] + f["dels"]
    return out


def escape(s: object) -> str:
    return html.escape(str(s), quote=True)


def page(title: str, payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<title>{escape(title)}</title>
<meta name="description" content="{escape(payload['pr']['repo'])}#{payload['pr']['number']}: {escape(payload['pr']['title'])}">
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="walkthrough.css">
<div id="walkthrough"></div>
<script id="walkthrough-data" type="application/json">{blob}</script>
<script src="{HLJS}/highlight.min.js"></script>
<script src="{HLJS}/languages/protobuf.min.js"></script>
<script src="walkthrough.js"></script>
"""


def copy_assets(out: Path) -> None:
    for asset in ("walkthrough.js", "walkthrough.css"):
        shutil.copyfile(ASSETS / asset, out / asset)


def build_page(spec: dict, out: Path, diff: str | None = None, at_head: bool = False, extra: dict | None = None) -> dict:
    pr = dict(spec.get("pr") or {})
    if not pr.get("repo") or not pr.get("number") or not pr.get("head"):
        raise SpecError("pr.repo, pr.number and pr.head are required")
    if diff is None:
        if not at_head:
            live = pr_metadata(pr["repo"], int(pr["number"]))
            if live["head"] != pr["head"]:
                raise SpecError(f"the PR head moved from {pr['head'][:9]} to {live['head'][:9]}; update the spec for the new head before building")
        if not pr.get("base"):
            pr["base"] = merge_base(pr["repo"], pr.get("baseRef") or "main", pr["head"])
        diff = fetch_diff(pr["repo"], pr["base"], pr["head"])
    files = parse_diff(diff)
    validate(spec, files)
    payload = {
        "name": spec.get("name") or f"{pr['repo'].split('/')[-1]}#{pr['number']} walkthrough",
        "pr": pr,
        "overview": spec.get("overview") or {},
        "groups": spec["groups"],
        "files": files,
        "stats": stats(files),
        "generatedAt": datetime.date.today().isoformat(),
    }
    payload.update(extra or {})
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page(payload["name"], payload), encoding="utf-8")
    copy_assets(out)
    return payload


def spec_time(path: Path) -> int:
    """When the spec was committed, falling back to its modification time."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", path.name], cwd=path.parent,
                             capture_output=True, text=True, check=True).stdout.strip()
        if out:
            return int(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return int(path.stat().st_mtime)


def build_site(root: Path, out: Path) -> list[dict]:
    specs = sorted(root.glob("*/*/spec.json"))
    if not specs:
        raise SpecError(f"no walkthroughs under {root}: expected <number>/<head>/spec.json")
    by_pr: dict[str, list[tuple[int, Path, dict]]] = {}
    for path in specs:
        spec = json.loads(path.read_text(encoding="utf-8"))
        number, head = path.parent.parent.name, path.parent.name
        if str(spec.get("pr", {}).get("number")) != number or spec.get("pr", {}).get("head") != head:
            raise SpecError(f"{path} must sit at <pr.number>/<pr.head>/spec.json")
        by_pr.setdefault(number, []).append((spec_time(path), path, spec))
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    copy_assets(out)
    entries = []
    for number, versions in sorted(by_pr.items(), key=lambda kv: -int(kv[0])):
        versions.sort(key=lambda v: v[0], reverse=True)
        heads = [{"head": v[2]["pr"]["head"], "url": f"../{v[2]['pr']['head']}/", "at": v[0]} for v in versions]
        for when, path, spec in versions:
            head = spec["pr"]["head"]
            listed = [dict(h, current=h["head"] == head) for h in heads]
            payload = build_page(spec, out / number / head, at_head=True, extra={"indexUrl": "../../", "heads": listed})
            print(f"built {number}/{head[:9]}: {len(spec['groups'])} groups, {payload['stats']['files']} files")
        latest = versions[0][2]
        (out / number / "index.html").write_text(redirect(f"{latest['pr']['head']}/"), encoding="utf-8")
        entries.append({"number": int(number), "name": latest.get("name") or "", "pr": latest["pr"],
                        "heads": len(versions), "updated": versions[0][0]})
    (out / "index.html").write_text(index_page(entries), encoding="utf-8")
    return entries


def redirect(target: str) -> str:
    t = escape(target)
    return f'<!doctype html><meta charset="utf-8"><title>Redirecting</title><meta http-equiv="refresh" content="0; url={t}"><link rel="canonical" href="{t}"><a href="{t}">Newest walkthrough</a>\n'


def index_page(entries: list[dict]) -> str:
    repo = entries[0]["pr"]["repo"] if entries else ""
    rows = "".join(
        f'<tr><td><a href="{e["number"]}/">#{e["number"]}</a></td><td><a href="{e["number"]}/">{escape(e["name"] or e["pr"]["title"])}</a>'
        f'<div class="note">{escape(e["pr"]["title"])}</div></td><td class="mono">{escape(e["pr"]["head"][:9])}</td>'
        f'<td>{e["heads"]}</td><td>{datetime.datetime.fromtimestamp(e["updated"], datetime.timezone.utc).date().isoformat()}</td></tr>'
        for e in entries
    )
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(repo)} walkthroughs</title>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="walkthrough.css">
<div class="wrap"><header class="top"><div><div class="eyebrow"><a href="https://github.com/{escape(repo)}">{escape(repo)}</a></div><h1>Pull request walkthroughs</h1></div></header>
<div class="card"><div class="tblwrap"><table class="tbl"><tr><th>PR</th><th>Walkthrough</th><th>Head</th><th>Versions</th><th>Updated</th></tr>{rows}</table></div>
<p class="note">Built by Projector's <span class="mono">walkthrough-pr</span> skill. Each link opens the newest version; older heads are listed in its sidebar.</p></div></div>
"""


# Publishing to the walkthroughs branch

def git(*args: str, env: dict | None = None, input: str | None = None) -> str:
    try:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=env, input=input).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise SpecError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def repo_slug(remote_url: str) -> str:
    url = remote_url.strip()
    for prefix in ("git@github.com:", "ssh://git@github.com/", "https://github.com/"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    else:
        return ""
    return url[:-4] if url.endswith(".git") else url


def dispatch(repo: str) -> None:
    gh("api", f"repos/{repo}/dispatches", "-f", f"event_type={DISPATCH_EVENT}")


def publish(spec_path: Path, remote: str, send_dispatch: bool = True, ref: str = PAGES_REF, root: str = PAGES_ROOT) -> str | None:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    pr = spec.get("pr") or {}
    if spec.get("version") != SPEC_VERSION or not pr.get("repo") or not pr.get("number") or not pr.get("head"):
        raise SpecError("the spec needs version, pr.repo, pr.number and pr.head")
    slug = repo_slug(git("remote", "get-url", remote))
    if slug and slug.lower() != pr["repo"].lower():
        raise SpecError(f"{remote} is {slug}, but the spec is for {pr['repo']}")
    local = ref.replace("refs/projector/", f"refs/projector/remotes/{remote}/", 1)
    fetched = subprocess.run(["git", "fetch", "--quiet", remote, f"+{ref}:{local}"], capture_output=True, text=True)
    parent = git("rev-parse", "--verify", "--quiet", local) if fetched.returncode == 0 else ""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        if parent:
            git("read-tree", parent, env=env)
        blob = git("hash-object", "-w", "--stdin", input=json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{root}/{pr['number']}/{pr['head']}/spec.json", env=env)
        tree = git("write-tree", env=env)
    if parent and tree == git("rev-parse", f"{parent}^{{tree}}"):
        print(f"{ref} already has this spec; nothing to publish")
        return None
    message = f"Publish the walkthrough of #{pr['number']} at {pr['head'][:9]}"
    commit = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
    git("push", "--quiet", remote, f"{commit}:{ref}")
    git("update-ref", local, commit)
    print(f"pushed {commit[:9]} to {remote} {ref}: {root}/{pr['number']}/{pr['head']}/spec.json")
    if send_dispatch:
        dispatch(pr["repo"])
        print(f"sent {DISPATCH_EVENT} to {pr['repo']}; its walkthroughs workflow builds and deploys the site")
    return commit


def workflow_text(action_ref: str) -> str:
    return WORKFLOW.format(event=DISPATCH_EVENT, ref=action_ref)


# Commands

def cmd_init(args: argparse.Namespace) -> None:
    meta = pr_metadata(args.repo, args.pr)
    diff = Path(args.diff).read_text(encoding="utf-8") if args.diff else fetch_diff(args.repo, meta["base"], meta["head"])
    files = parse_diff(diff)
    spec = {
        "version": SPEC_VERSION,
        "name": "",
        "pr": meta,
        "overview": {"summary": [], "cards": []},
        "groups": [{"id": "unassigned", "title": "Unassigned", "kicker": "", "intro": [], "concepts": [], "checks": [],
                    "files": [{"path": f["path"]} for f in files]}],
    }
    Path(args.spec).write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.spec}: {meta['repo']}#{meta['number']} at {meta['head'][:9]}, {len(files)} files")
    for f in files:
        flag = f" [{f['kind']}]" if f["kind"] else ""
        print(f"  +{f['adds']:<5} -{f['dels']:<5} {f['path']}{flag}{' (new)' if f['new'] else ''}")


def cmd_build(args: argparse.Namespace) -> None:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    diff = Path(args.diff).read_text(encoding="utf-8") if args.diff else None
    payload = build_page(spec, Path(args.out), diff=diff, at_head=args.at_head)
    s = payload["stats"]
    print(f"wrote {args.out}/index.html: {len(spec['groups'])} groups, {s['files']} files, +{s['adds']} -{s['dels']}")


def cmd_site(args: argparse.Namespace) -> None:
    entries = build_site(Path(args.root), Path(args.out))
    print(f"wrote {args.out}/index.html: {len(entries)} pull requests")


def cmd_publish(args: argparse.Namespace) -> None:
    publish(Path(args.spec), args.remote, send_dispatch=not args.no_dispatch)


def cmd_workflow(args: argparse.Namespace) -> None:
    text = workflow_text(args.action_ref)
    if not args.write:
        print(text, end="")
        return
    path = Path(git("rev-parse", "--show-toplevel")) / WORKFLOW_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}; commit it to the default branch, where GitHub runs dispatched workflows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="write a skeleton spec for a PR")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--spec", required=True)
    p.add_argument("--diff")
    p.set_defaults(func=cmd_init)
    b = sub.add_parser("build", help="build one page from a spec")
    b.add_argument("--spec", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--diff")
    b.add_argument("--at-head", action="store_true", help="build the spec's recorded head even if the PR has moved")
    b.set_defaults(func=cmd_build)
    s = sub.add_parser("site", help="build every spec under a root into a Pages site")
    s.add_argument("--root", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_site)
    u = sub.add_parser("publish", help="commit a spec to the hidden walkthroughs ref and start a build")
    u.add_argument("--spec", required=True)
    u.add_argument("--remote", default="origin")
    u.add_argument("--no-dispatch", action="store_true", help="push the spec without starting the workflow")
    u.set_defaults(func=cmd_publish)
    w = sub.add_parser("workflow", help="print or write the workflow file for the default branch")
    w.add_argument("--action-ref", default="v0", help="the projector tag or commit the workflow runs")
    w.add_argument("--write", action="store_true", help="write .github/workflows/walkthroughs.yml in this checkout")
    w.set_defaults(func=cmd_workflow)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SpecError as exc:
        print(f"walkthrough: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
