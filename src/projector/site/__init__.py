"""The Projector site: the static pages a repository serves from GitHub Pages.

The site is built from content Projector keeps in the repository: its
README, its `docs/` directory and the project plans under it, and the pull
request walkthroughs published to refs/projector/walkthroughs. The root is
one page that renders the README by default and reaches everything else from
its menu. Each walkthrough spec becomes a page under /<number>/<head>/, with
a redirect to the newest head at /<number>/.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import subprocess
import urllib.parse
from importlib import resources
from pathlib import Path

from ..core import Project, title_from_text
from ..walkthrough import DIFF_FILE, SpecError, prepare_page

ASSETS = resources.files(__package__) / "assets"
HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1"
FONTS = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"
MARKED = "https://cdnjs.cloudflare.com/ajax/libs/marked/18.0.13/lib/marked.umd.min.js"
PURIFY = "https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.4.16/purify.min.js"
CONTENT = "content"


def escape(s: object) -> str:
    return html.escape(str(s), quote=True)


def icon_link() -> str:
    svg = (ASSETS / "icon.svg").read_text(encoding="utf-8")
    return f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{urllib.parse.quote(svg)}">'


def page(title: str, payload: dict, embed: bool = True) -> str:
    # A page opened from disk cannot fetch a file beside it, so a standalone page
    # embeds its data; a site page loads data.json when it opens.
    data = ""
    if embed:
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        data = f'<script id="walkthrough-data" type="application/json">{blob}</script>\n'
    return f"""<title>{escape(title)}</title>
<meta charset="utf-8">
<meta name="description" content="{escape(payload['pr']['repo'])}#{payload['pr']['number']}: {escape(payload['pr']['title'])}">
{icon_link()}
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="walkthrough.css">
<div id="walkthrough"></div>
{data}<script src="{HLJS}/highlight.min.js"></script>
<script src="{HLJS}/languages/protobuf.min.js"></script>
<script src="walkthrough.js"></script>
"""


def copy_assets(out: Path) -> None:
    for asset in ("walkthrough.js", "walkthrough.css"):
        (out / asset).write_bytes((ASSETS / asset).read_bytes())


def write_page(out: Path, payload: dict, embed: bool = True) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page(payload["name"], payload, embed=embed), encoding="utf-8")
    if not embed:
        (out / "data.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    copy_assets(out)


def build_page(spec: dict, out: Path, diff: str | None = None, at_head: bool = False, extra: dict | None = None) -> dict:
    payload = prepare_page(spec, diff=diff, at_head=at_head, extra=extra)
    write_page(out, payload)
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


def build_walkthroughs(root: Path, out: Path) -> tuple[list[dict], list[str]]:
    """Build every walkthrough spec that can be built; report and skip the rest."""
    specs = sorted(root.glob("*/*/spec.json"))
    if not specs:
        return [], []
    own_repo = os.environ.get("GITHUB_REPOSITORY", "").lower()
    failures: list[str] = []

    def skip(path: Path, reason: object) -> None:
        failures.append(f"{path.relative_to(root)}: {reason}")
        print(f"::error title=Walkthrough skipped::{path.relative_to(root)}: {str(reason).replace(chr(10), ' ')}")

    by_pr: dict[str, list[tuple[int, dict]]] = {}
    for path in specs:
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
            pr = spec.get("pr") or {}
            number, head = path.parent.parent.name, path.parent.name
            if str(pr.get("number")) != number or pr.get("head") != head:
                raise SpecError(f"it must sit at <pr.number>/<pr.head>/spec.json")
            if own_repo and str(pr.get("repo", "")).lower() != own_repo:
                raise SpecError(f"it is for {pr.get('repo')}, not this repository")
            # A spec published with its diff builds offline. One published before
            # diffs were stored falls back to fetching its diff from GitHub.
            stored = path.with_name(DIFF_FILE)
            diff = stored.read_text(encoding="utf-8") if stored.is_file() else None
            payload = prepare_page(spec, diff=diff, at_head=True)
        except (SpecError, ValueError, KeyError, TypeError) as exc:
            skip(path, exc)
            continue
        by_pr.setdefault(number, []).append((spec_time(path), payload))
    entries = []
    for number, versions in sorted(by_pr.items(), key=lambda kv: -int(kv[0])):
        versions.sort(key=lambda v: v[0], reverse=True)
        heads = [{"head": p["pr"]["head"], "url": f"../{p['pr']['head']}/", "at": when} for when, p in versions]
        for _, payload in versions:
            head = payload["pr"]["head"]
            payload.update(indexUrl="../../#/prs", heads=[dict(h, current=h["head"] == head) for h in heads])
            write_page(out / number / head, payload, embed=False)
            print(f"built {number}/{head[:9]}: {len(payload['groups'])} groups, {payload['stats']['files']} files")
        latest = versions[0][1]
        (out / number / "index.html").write_text(redirect(f"{latest['pr']['head']}/"), encoding="utf-8")
        entries.append({"number": int(number), "name": latest.get("name") or "", "pr": latest["pr"],
                        "heads": len(versions), "updated": versions[0][0]})
    return entries, failures


def copy_content(repo_root: Path, path: Path, out: Path) -> str:
    """Copy one Markdown file under out/content, returning its repository path."""
    relative = path.relative_to(repo_root).as_posix()
    target = out / CONTENT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())
    return relative


def collect_docs(repo_root: Path, projects_dir: Path | None, out: Path) -> tuple[str | None, list[dict]]:
    """Copy the README and every Markdown file under docs/ outside the plans."""
    readme = repo_root / "README.md"
    readme_path = copy_content(repo_root, readme, out) if readme.is_file() else None
    docs = []
    docs_dir = repo_root / "docs"
    for path in sorted(docs_dir.rglob("*.md")) if docs_dir.is_dir() else []:
        if projects_dir is not None and path.is_relative_to(projects_dir):
            continue
        relative = copy_content(repo_root, path, out)
        docs.append({"path": relative, "title": title_from_text(path.read_text(encoding="utf-8"), path.stem)})
    return readme_path, docs


def collect_projects(repo_root: Path, projects: list[Project], projects_dir: Path | None, out: Path) -> list[dict]:
    """Copy every plan and its supplemental files, and describe each project."""
    if projects_dir is None or not projects_dir.is_dir():
        return []
    owners = {project.path.parent: project.name for project in projects}
    extras: dict[str, list[str]] = {}
    for path in sorted(projects_dir.rglob("*.md")):
        relative = copy_content(repo_root, path, out)
        owner = next((owners[d] for d in (path.parent, *path.parent.parents) if d in owners), None)
        if owner is not None and path.name != "readme.md":
            extras.setdefault(owner, []).append(relative)
    described = []
    for project in projects:
        entry = project.public(repo_root)
        entry["files"] = extras.get(project.name, [])
        described.append(entry)
    return described


def home_page(repo: str) -> str:
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(repo or "Projector")}</title>
{icon_link()}
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="walkthrough.css">
<link rel="stylesheet" href="site.css">
<div id="site"></div>
<script src="{MARKED}"></script>
<script src="{PURIFY}"></script>
<script src="{HLJS}/highlight.min.js"></script>
<script src="site.js"></script>
"""


def build_site(out: Path, walkthroughs: Path | None = None, repo_root: Path | None = None,
               projects: list[Project] | None = None, projects_dir: Path | None = None,
               repo: str = "", branch: str = "main") -> tuple[list[dict], list[str]]:
    """Build the whole site: the home page, its manifest, the docs, the plans, and every walkthrough."""
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    copy_assets(out)
    for asset in ("site.js", "site.css"):
        (out / asset).write_bytes((ASSETS / asset).read_bytes())
    entries, failures = build_walkthroughs(walkthroughs, out) if walkthroughs and walkthroughs.is_dir() else ([], [])
    readme, docs = collect_docs(repo_root, projects_dir, out) if repo_root else (None, [])
    described = collect_projects(repo_root, projects or [], projects_dir, out) if repo_root else []
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "") or (entries[0]["pr"]["repo"] if entries else "")
    manifest = {
        "repo": repo,
        "branch": branch,
        "content": CONTENT,
        "readme": readme,
        "docs": docs,
        "projectsDir": projects_dir.relative_to(repo_root).as_posix() if repo_root and projects_dir and projects_dir.is_dir() else None,
        "projects": described,
        "projectsReadme": (projects_dir / "README.md").relative_to(repo_root).as_posix()
        if repo_root and projects_dir and (projects_dir / "README.md").is_file() else None,
        "walkthroughs": [
            {"number": e["number"], "name": e["name"], "title": e["pr"]["title"], "head": e["pr"]["head"],
             "heads": e["heads"],
             "updated": datetime.datetime.fromtimestamp(e["updated"], datetime.timezone.utc).date().isoformat()}
            for e in entries
        ],
    }
    (out / "site.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "index.html").write_text(home_page(repo), encoding="utf-8")
    return entries, failures


def redirect(target: str) -> str:
    t = escape(target)
    return f'<!doctype html><meta charset="utf-8"><title>Redirecting</title>{icon_link()}<meta http-equiv="refresh" content="0; url={t}"><link rel="canonical" href="{t}"><a href="{t}">Newest walkthrough</a>\n'
