"""Pull request walkthrough specs: the data a walkthrough page is built from.

A spec records a pull request's merge base and head and assigns every changed
file to a group with its explanation and checks. This module writes skeleton
specs, checks a spec against its diff, publishes specs to the hidden ref
refs/projector/walkthroughs, and says whether a repository hosts a site that
serves them. Rendering specs into pages is `projector.site`'s job.
"""

from __future__ import annotations

import datetime
import hashlib
import html
from html.parser import HTMLParser
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import ProjectorError

SPEC_VERSION = 1
PAGES_REF = "refs/projector/walkthroughs"
DISPATCH_EVENT = "projector-walkthroughs"
PAGES_ROOT = "walkthroughs"
WORKFLOW_PATH = ".github/workflows/walkthroughs.yml"

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


class SpecError(ProjectorError):
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


# GitHub

def gh(*args: str) -> str:
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    except FileNotFoundError as exc:
        raise SpecError("the gh CLI is not installed; pass --diff and fill the pr fields by hand") from exc
    except subprocess.CalledProcessError as exc:
        raise SpecError(f"gh {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def gh_lookup(*args: str) -> str | None:
    """Run gh, returning None when GitHub answers 404 and raising on any other failure."""
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    except FileNotFoundError as exc:
        raise SpecError("the gh CLI is not installed") from exc
    except subprocess.CalledProcessError as exc:
        if "HTTP 404" in exc.stderr:
            return None
        raise SpecError(f"gh {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def hosting(repo: str) -> tuple[str | None, str]:
    """Return the repository's walkthroughs site URL, or None and why it is not set up.

    Both halves are required. The workflow on the default branch is the reviewed
    decision to host walkthroughs; a Pages site alone may serve something else.
    """
    if gh_lookup("api", f"repos/{repo}/contents/{WORKFLOW_PATH}", "--jq", ".path") is None:
        return None, f"{repo} has no {WORKFLOW_PATH} on its default branch"
    raw = gh_lookup("api", f"repos/{repo}/pages")
    if raw is None:
        return None, f"{repo} has the walkthroughs workflow but no GitHub Pages site"
    pages = json.loads(raw)
    if pages.get("public", True) and (gh_lookup("api", f"repos/{repo}", "--jq", ".private") or "").strip() == "true":
        return None, f"{repo} is private but its GitHub Pages site is public"
    site = pages["html_url"]
    # GitHub reports an http:// URL for a custom domain that does not enforce
    # HTTPS, even once its certificate is issued and https:// serves the site.
    certified = (pages.get("https_certificate") or {}).get("state") == "approved"
    if site.startswith("http:") and (pages.get("https_enforced") or certified):
        site = "https:" + site.removeprefix("http:")
    return site.rstrip("/") + "/", ""


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


# Sanitizing spec HTML

ALLOWED_TAGS = {"a", "b", "br", "code", "div", "em", "h3", "h4", "i", "li", "ol", "p", "pre", "span", "strong",
                "table", "tbody", "td", "th", "thead", "tr", "ul"}
VOID_TAGS = {"br"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math"}
ALLOWED_CLASSES = {"tblwrap", "tbl", "r", "note", "tight", "count", "mono"}


class Sanitizer(HTMLParser):
    """Rebuild spec HTML from the tags, classes and links spec.md documents."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.dropping = 0
        self.open: list[str] = []

    def attrs_for(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        kept = []
        for name, value in attrs:
            if value is None:
                continue
            if name == "class":
                classes = [c for c in value.split() if c in ALLOWED_CLASSES]
                if classes:
                    kept.append(f'class="{" ".join(classes)}"')
            elif name == "href" and tag == "a":
                target = html.unescape(value).strip()
                if target.lower().startswith(("https://", "http://")) or target.startswith("#"):
                    kept.append(f'href="{html.escape(target, quote=True)}"')
        return (" " + " ".join(kept)) if kept else ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_CONTENT:
            self.dropping += 1
        elif not self.dropping and tag in ALLOWED_TAGS:
            self.out.append(f"<{tag}{self.attrs_for(tag, attrs)}>")
            if tag not in VOID_TAGS:
                self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.dropping and tag in VOID_TAGS:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_CONTENT:
            self.dropping = max(0, self.dropping - 1)
        elif not self.dropping and tag in self.open:
            while self.open:
                top = self.open.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(html.unescape(f"&{name};"), quote=False))

    def handle_charref(self, name: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(html.unescape(f"&#{name};"), quote=False))

    def result(self) -> str:
        self.close()
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def sanitize_html(text: object) -> str:
    parser = Sanitizer()
    parser.feed(str(text))
    tail, parser.rawdata = parser.rawdata, ""
    if tail and not parser.dropping:
        parser.out.append(html.escape(tail, quote=False))
    return parser.result()


def sanitized(spec: dict) -> tuple[dict, list[dict]]:
    """The spec's overview and groups with every HTML field sanitized."""
    o = spec.get("overview") or {}
    overview = {
        "summary": [sanitize_html(p) for p in o.get("summary") or []],
        "cards": [{"id": str(c.get("id") or ""), "title": sanitize_html(c.get("title") or ""), "html": sanitize_html(c.get("html") or "")}
                  for c in o.get("cards") or []],
    }
    groups = []
    for g in spec["groups"]:
        groups.append({
            "id": str(g["id"]),
            "title": sanitize_html(g["title"]),
            "kicker": sanitize_html(g.get("kicker") or ""),
            "intro": [sanitize_html(p) for p in g.get("intro") or []],
            "concepts": [sanitize_html(c) for c in g.get("concepts") or []],
            "checks": [{"kind": c["kind"], "text": sanitize_html(c["text"])} for c in g.get("checks") or []],
            "files": [{k: (sanitize_html(v) if k == "note" else v) for k, v in f.items() if k in ("path", "note", "collapsed")}
                      for f in g.get("files") or []],
        })
    return overview, groups


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


def prepare_page(spec: dict, diff: str | None = None, at_head: bool = False, extra: dict | None = None) -> dict:
    pr = dict(spec.get("pr") or {})
    if not pr.get("repo") or not pr.get("number") or not pr.get("head"):
        raise SpecError("pr.repo, pr.number and pr.head are required")
    if not at_head:
        try:
            live = pr_metadata(pr["repo"], int(pr["number"]))
        except SpecError as exc:
            if diff is None:
                raise
            print(f"walkthrough: could not check whether the PR moved past {pr['head'][:9]} ({exc}); building from the given diff", file=sys.stderr)
        else:
            if live["head"] != pr["head"]:
                raise SpecError(f"the PR head moved from {pr['head'][:9]} to {live['head'][:9]}; update the spec for the new head before building")
    if diff is None:
        if not pr.get("base"):
            pr["base"] = merge_base(pr["repo"], pr.get("baseRef") or "main", pr["head"])
        diff = fetch_diff(pr["repo"], pr["base"], pr["head"])
    files = parse_diff(diff)
    validate(spec, files)
    overview, groups = sanitized(spec)
    payload = {
        "name": spec.get("name") or f"{pr['repo'].split('/')[-1]}#{pr['number']} walkthrough",
        "pr": pr,
        "overview": overview,
        "groups": groups,
        "files": files,
        "stats": stats(files),
        "generatedAt": datetime.date.today().isoformat(),
    }
    payload.update(extra or {})
    return payload


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
    prepare_page(spec, at_head=True)
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


def init(repo: str, number: int, spec_path: str, diff_path: str | None = None) -> None:
    meta = pr_metadata(repo, number)
    diff = Path(diff_path).read_text(encoding="utf-8") if diff_path else fetch_diff(repo, meta["base"], meta["head"])
    files = parse_diff(diff)
    spec = {
        "version": SPEC_VERSION,
        "name": "",
        "pr": meta,
        "overview": {"summary": [], "cards": []},
        "groups": [{"id": "unassigned", "title": "Unassigned", "kicker": "", "intro": [], "concepts": [], "checks": [],
                    "files": [{"path": f["path"]} for f in files]}],
    }
    Path(spec_path).write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {spec_path}: {meta['repo']}#{meta['number']} at {meta['head'][:9]}, {len(files)} files")
    for f in files:
        flag = f" [{f['kind']}]" if f["kind"] else ""
        print(f"  +{f['adds']:<5} -{f['dels']:<5} {f['path']}{flag}{' (new)' if f['new'] else ''}")
