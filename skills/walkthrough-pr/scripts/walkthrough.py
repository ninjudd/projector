#!/usr/bin/env python3
"""Build a pull request walkthrough page from a spec and the PR's diff.

    walkthrough.py init  --repo OWNER/NAME --pr N --spec SPEC [--diff FILE]
    walkthrough.py build --spec SPEC --out DIR [--diff FILE]

`init` writes a skeleton spec with the PR's metadata and every changed file
in one unassigned group. You then group the files and write the notes.
`build` checks that every changed file sits in exactly one group, that the
spec's head is still the PR's head, and writes DIR/index.html with the
renderer's walkthrough.js and walkthrough.css beside it.

Without --diff the diff comes from `gh pr diff`. Pass --diff for a PR too
large for GitHub's diff endpoint, or to build offline.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SPEC_VERSION = 1
HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1"
FONTS = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"

LANGS = {
    ".go": "go", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".py": "python", ".rb": "ruby", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".proto": "protobuf", ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".md": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "ini", ".sql": "sql", ".css": "css",
    ".scss": "scss", ".html": "xml", ".xml": "xml", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cs": "csharp", ".php": "php", ".tf": "ini", ".dockerfile": "dockerfile",
}
GENERATED_SUFFIXES = (".pb.go", "_grpc.pb.go", ".swagger.json", ".pb.ts", "_pb2.py", ".lock", "-lock.json", ".snap")
GENERATED_PARTS = ("/gen/", "/generated/", "/mocks/", "/__generated__/")


class SpecError(Exception):
    pass


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


C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote_path(token: str) -> str:
    """Decode a path git printed, C-quoted when it holds a special byte."""
    token = token.rstrip("\t")
    if not (len(token) >= 2 and token[0] == token[-1] == '"'):
        return token
    out = bytearray()
    body, i = token[1:-1], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567" and i + 3 < len(body) + 1 and all(c in "01234567" for c in body[i + 1:i + 4]):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            if nxt in C_ESCAPES:
                out.append(C_ESCAPES[nxt])
                i += 2
                continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def header_path(rest: str) -> str:
    """The b/ path of a `diff --git` line, used until the +++ line names it."""
    if rest.startswith('"'):
        end = 1
        while end < len(rest) and not (rest[end] == '"' and rest[end - 1] != "\\"):
            end += 1
        b_side = rest[end + 1:].strip()
        return unquote_path(b_side)[2:] if b_side else unquote_path(rest[:end + 1])[2:]
    half = (len(rest) - 1) // 2
    if len(rest) % 2 == 1 and rest[half] == " " and rest[:2] == "a/" and rest[half + 1:half + 3] == "b/" and rest[2:half] == rest[half + 3:]:
        return rest[half + 3:]
    b_side = rest.rsplit(" b/", 1)
    return unquote_path(b_side[-1]) if len(b_side) == 2 else rest


def parse_diff(text: str) -> list[dict]:
    files: list[dict] = []
    cur = hunk = None
    old_no = new_no = 0
    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            cur = {"path": header_path(raw[len("diff --git "):]), "new": False, "deleted": False, "adds": 0, "dels": 0, "hunks": []}
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
            cur["path"] = unquote_path(raw[len("rename to "):])
        elif hunk is None and raw.startswith("+++ "):
            target = unquote_path(raw[4:])
            if target.startswith("b/"):
                cur["path"] = target[2:]
        elif hunk is None and raw.startswith("--- "):
            source = unquote_path(raw[4:])
            if source.startswith("a/"):
                cur["path"] = source[2:]
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


def gh(*args: str) -> str:
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    except FileNotFoundError as exc:
        raise SpecError("the gh CLI is not installed; pass --diff and fill the PR fields by hand") from exc
    except subprocess.CalledProcessError as exc:
        raise SpecError(f"gh {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def pr_metadata(repo: str, number: int) -> dict:
    raw = json.loads(gh("pr", "view", str(number), "--repo", repo, "--json", "title,headRefOid,baseRefName,url"))
    return {"repo": repo, "number": number, "title": raw["title"], "head": raw["headRefOid"], "baseRef": raw["baseRefName"]}


def read_diff(args: argparse.Namespace, repo: str, number: int) -> str:
    if args.diff:
        return Path(args.diff).read_text(encoding="utf-8")
    return gh("pr", "diff", str(number), "--repo", repo)


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


def escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def cmd_init(args: argparse.Namespace) -> None:
    meta = pr_metadata(args.repo, args.pr)
    files = parse_diff(read_diff(args, args.repo, args.pr))
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
    pr = spec.get("pr") or {}
    if not args.diff:
        live = pr_metadata(pr["repo"], int(pr["number"]))
        if live["head"] != pr.get("head"):
            raise SpecError(f"the PR head moved from {str(pr.get('head'))[:9]} to {live['head'][:9]}; update the spec for the new head before building")
    files = parse_diff(read_diff(args, pr["repo"], int(pr["number"])))
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
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page(payload["name"], payload), encoding="utf-8")
    for asset in ("walkthrough.js", "walkthrough.css"):
        shutil.copyfile(ASSETS / asset, out / asset)
    s = payload["stats"]
    print(f"wrote {out}/index.html: {len(spec['groups'])} groups, {s['files']} files, +{s['adds']} -{s['dels']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="write a skeleton spec for a PR")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--spec", required=True)
    p.add_argument("--diff")
    p.set_defaults(func=cmd_init)
    b = sub.add_parser("build", help="build the page from a spec")
    b.add_argument("--spec", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--diff")
    b.set_defaults(func=cmd_build)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SpecError as exc:
        print(f"walkthrough: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
