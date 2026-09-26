"""The Projector site: the static pages a repository serves from GitHub Pages.

The site is built from content Projector keeps in the repository: its
README, its `docs/` directory and the projects under it, and the pull request
walkthroughs published to refs/projector/walkthroughs, which the site calls
reviews. It has three sections, each at a real path under the site's base:
the projects at the root and under projects/, every file of a project at its
path inside the projects directory without `.md`; the reviews under
reviews/<number>/<head>/, with the newest head also at reviews/<number>/; and
the docs under docs/, the README at docs/ itself and every other document at
its path without `.md`. GitHub Pages serves only files that exist, so each
path gets a small shell page that loads the one shared copy of the assets and
fetches its data.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import re
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
MAX_FILE_BYTES = 20 * 1024 * 1024


def escape(s: object) -> str:
    return html.escape(str(s), quote=True)


def icon_link() -> str:
    svg = (ASSETS / "icon.svg").read_text(encoding="utf-8")
    return f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{urllib.parse.quote(svg)}">'


def page(title: str, payload: dict, embed: bool = True, assets: str = "", src: str = "data.json",
         site_base: str | None = None) -> str:
    # A page opened from disk cannot fetch a file beside it, so a standalone page
    # embeds its data; a site page loads data.json when it opens, and carries the
    # site's menu bar above the walkthrough.
    data = ""
    if embed:
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        data = f'<script id="walkthrough-data" type="application/json">{blob}</script>\n'
    bar_style = bar = bar_script = ""
    if site_base is not None:
        bar_style = f'<link rel="stylesheet" href="{escape(assets)}site.css">\n'
        bar = f'<div id="sitebar" data-base="{escape(site_base)}"></div>\n'
        bar_script = f'<script src="{escape(assets)}site.js"></script>\n'
    return f"""<title>{escape(title)}</title>
<meta charset="utf-8">
<meta name="description" content="{escape(payload['pr']['repo'])}#{payload['pr']['number']}: {escape(payload['pr']['title'])}">
{icon_link()}
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{escape(assets)}walkthrough.css">
{bar_style}{bar}<div id="walkthrough"{"" if embed else f' data-src="{escape(src)}"'}></div>
{data}<script src="{HLJS}/highlight.min.js"></script>
<script src="{HLJS}/languages/protobuf.min.js"></script>
<script src="{escape(assets)}walkthrough.js"></script>
{bar_script}"""


def copy_assets(out: Path) -> None:
    for asset in ("walkthrough.js", "walkthrough.css"):
        (out / asset).write_bytes((ASSETS / asset).read_bytes())


def write_page(out: Path, payload: dict) -> None:
    """A standalone page, with its data embedded and the renderer beside it."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page(payload["name"], payload), encoding="utf-8")
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


def build_walkthroughs(root: Path, out: Path, base: str = "/", link=None) -> tuple[list[dict], list[str]]:
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
            stored = path.with_name(DIFF_FILE)
            if not stored.is_file():
                raise SpecError(f"it has no {DIFF_FILE} beside it; republish it with `project walkthrough publish`")
            payload = prepare_page(spec, diff=stored.read_text(encoding="utf-8"), at_head=True)
            payload["projects"] = link(spec, payload) if link else []
        except (SpecError, ValueError, KeyError, TypeError) as exc:
            skip(path, exc)
            continue
        by_pr.setdefault(number, []).append((spec_time(path), payload))
    entries = []
    for number, versions in sorted(by_pr.items(), key=lambda kv: -int(kv[0])):
        versions.sort(key=lambda v: v[0], reverse=True)
        heads = [{"head": p["pr"]["head"], "url": f"{base}reviews/{number}/{p['pr']['head']}/", "at": when}
                 for when, p in versions]
        for _, payload in versions:
            head = payload["pr"]["head"]
            payload.update(indexUrl=f"{base}reviews/", heads=[dict(h, current=h["head"] == head) for h in heads])
            folder = out / "reviews" / number / head
            folder.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            (folder / "data.json").write_text(data, encoding="utf-8")
            (folder / "index.html").write_text(
                page(payload["name"], payload, embed=False, assets=f"{base}assets/", site_base=base,
                     src=f"{base}reviews/{number}/{head}/data.json"), encoding="utf-8")
            print(f"built {number}/{head[:9]}: {len(payload['groups'])} groups, {payload['stats']['files']} files")
        latest = versions[0][1]
        newest = latest["pr"]["head"]
        (out / "reviews" / number / "index.html").write_text(
            page(latest["name"], latest, embed=False, assets=f"{base}assets/", site_base=base,
                 src=f"{base}reviews/{number}/{newest}/data.json"), encoding="utf-8")
        entries.append({"number": int(number), "name": latest.get("name") or "", "pr": latest["pr"],
                        "heads": len(versions), "updated": versions[0][0],
                        "projects": [p["name"] for p in latest["projects"]]})
    return entries, failures


def copy_content(repo_root: Path, path: Path, out: Path) -> str:
    """Copy one Markdown file under out/content, returning its repository path."""
    relative = path.relative_to(repo_root).as_posix()
    target = out / CONTENT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())
    return relative


def collect_docs(repo_root: Path, projects_dir: Path | None, out: Path) -> tuple[str | None, list[dict]]:
    """Copy the README and every Markdown file under docs/ outside the projects."""
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
    """Copy every project's readme and supplemental files, and describe each project."""
    if projects_dir is None or not projects_dir.is_dir():
        return []
    owners = {project.path.parent: project.name for project in projects}
    extras: dict[str, list[dict]] = {}
    for path in sorted(projects_dir.rglob("*.md")):
        relative = copy_content(repo_root, path, out)
        owner = next((owners[d] for d in (path.parent, *path.parent.parents) if d in owners), None)
        if owner is not None and path.name != "readme.md":
            title = title_from_text(path.read_text(encoding="utf-8"), path.stem)
            extras.setdefault(owner, []).append({"path": relative, "title": title})
    described = []
    for project in projects:
        entry = project.public(repo_root)
        entry["files"] = extras.get(project.name, [])
        described.append(entry)
    return described


def collect_files(repo_root: Path, out: Path) -> list[str]:
    """Copy every non-Markdown file under docs/, such as images, beside the Markdown."""
    docs_dir = repo_root / "docs"
    copied = []
    for path in sorted(docs_dir.rglob("*")) if docs_dir.is_dir() else []:
        relative = path.relative_to(repo_root).as_posix()
        if not path.is_file() or path.suffix.lower() == ".md" or any(p.startswith(".") for p in relative.split("/")):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            print(f"::warning title=File skipped::{relative} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
            continue
        copy_content(repo_root, path, out)
        copied.append(relative)
    return copied


def project_linker(described: list[dict], base: str):
    """Link a review to every project whose files its diff changes, or that its spec names."""
    by_name = {p["name"]: p for p in described}
    folders = sorted(((p["path"].rpartition("/")[0] + "/", p["name"]) for p in described), key=lambda f: -len(f[0]))

    def link(spec: dict, payload: dict) -> list[dict]:
        names = [n for n in spec.get("projects") or [] if n in by_name]
        for f in payload["files"]:
            owner = next((name for folder, name in folders if f["path"].startswith(folder)), None)
            if owner and owner not in names:
                names.append(owner)
        return [{"name": n, "title": by_name[n]["title"], "url": f"{base}projects/{n}/"} for n in names]

    return link


def plain_text(markdown: str) -> str:
    """The words of a Markdown document, for search: no frontmatter, markup, or link targets."""
    text = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", markdown, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_#>|\[\]]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def search_index(repo_root: Path, readme: str | None, docs: list[dict], projects: list[dict],
                 projects_readme: str | None) -> list[dict]:
    """One entry per served document: its site path, title, kind, and plain text."""
    entries = []

    def add(path: str, route: str, title: str, kind: str) -> None:
        text = plain_text((repo_root / path).read_text(encoding="utf-8"))
        entries.append({"route": route, "title": title, "kind": kind, "text": text})

    if readme:
        add(readme, "docs/", "README", "doc")
    for doc in docs:
        add(doc["path"], doc["route"], doc["title"], "doc")
    if projects_readme:
        add(projects_readme, "projects/", "How projects work", "project")
    for project in projects:
        add(project["path"], f"projects/{project['name']}/", project["title"], "project")
        for file in project["files"]:
            add(file["path"], file["route"], file["title"], "project")
    return entries


def local_route(path: str) -> str:
    """The route of a Markdown file under a section: its path without `.md`, a readme at its folder."""
    folder, _, name = path.rpartition("/")
    if name.lower() == "readme.md":
        return f"{folder}/" if folder else ""
    return f"{path[:-3]}/" if path.endswith(".md") else f"{path}/"


def doc_route(path: str) -> str:
    """The site path of a document under docs/. The README owns docs/ itself, so a
    readme at the top of docs/ moves aside to docs/readme/."""
    if path.lower() == "docs/readme.md":
        return "docs/readme/"
    return local_route(path)


def project_route(path: str, project: dict) -> str:
    """The site path of a file in a project: projects/ and its path inside the projects directory."""
    folder = project["path"].rpartition("/")[0]
    inside = project["name"] + "/" + path[len(folder) + 1:]
    return "projects/" + local_route(inside)


def assign_routes(docs: list[dict], projects: list[dict]) -> None:
    """Give every document and project file a route of its own, as its `route`.

    A file and a folder of the same name would share a route: notes.md and
    notes/readme.md both want notes/, and in a project the folder may be a
    nested project. The folder keeps the plain route, since a readme is what a
    folder's path means, and the file keeps its `.md` so it stays reachable.
    """
    taken = {f"projects/{p['name']}/" for p in projects}
    entries = [(doc, doc_route(doc["path"])) for doc in docs]
    entries += [(file, project_route(file["path"], p)) for p in projects for file in p["files"]]
    entries.sort(key=lambda e: e[0]["path"].rpartition("/")[2].lower() != "readme.md")
    for entry, route in entries:
        if route in taken:
            route = route[:-1] + ".md/"
        taken.add(route)
        entry["route"] = route


def home_page(repo: str, base: str) -> str:
    assets = escape(f"{base}assets/")
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(repo or "Projector")}</title>
{icon_link()}
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{assets}walkthrough.css">
<link rel="stylesheet" href="{assets}site.css">
<div id="site" data-base="{escape(base)}"></div>
<script src="{MARKED}"></script>
<script src="{PURIFY}"></script>
<script src="{HLJS}/highlight.min.js"></script>
<script src="{assets}site.js"></script>
"""


def site_routes(docs: list[dict], projects: list[dict]) -> list[str]:
    """Every path the home page's script renders, each of which needs a shell."""
    routes = {"", "projects/", "reviews/", "docs/", "search/"}
    routes.update(doc["route"] for doc in docs)
    for project in projects:
        routes.add(f"projects/{project['name']}/")
        routes.update(file["route"] for file in project["files"])
    return sorted(routes)


def build_site(out: Path, walkthroughs: Path | None = None, repo_root: Path | None = None,
               projects: list[Project] | None = None, projects_dir: Path | None = None,
               repo: str = "", branch: str = "main", base: str = "/") -> tuple[list[dict], list[str]]:
    """Build the whole site: the shell pages, the manifest, the projects, the reviews, and the docs."""
    base = "/" + base.strip("/") + "/" if base.strip("/") else "/"
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    (out / "assets").mkdir(exist_ok=True)
    for asset in ("walkthrough.js", "walkthrough.css", "site.js", "site.css"):
        (out / "assets" / asset).write_bytes((ASSETS / asset).read_bytes())
    readme, docs = collect_docs(repo_root, projects_dir, out) if repo_root else (None, [])
    described = collect_projects(repo_root, projects or [], projects_dir, out) if repo_root else []
    files = collect_files(repo_root, out) if repo_root else []
    assign_routes(docs, described)
    link = project_linker(described, base)
    built = build_walkthroughs(walkthroughs, out, base, link) if walkthroughs and walkthroughs.is_dir() else ([], [])
    entries, failures = built
    for project in described:
        project["reviews"] = [e["number"] for e in entries if project["name"] in e["projects"]]
    projects_readme = (projects_dir / "README.md").relative_to(repo_root).as_posix() \
        if repo_root and projects_dir and (projects_dir / "README.md").is_file() else None
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "") or (entries[0]["pr"]["repo"] if entries else "")
    manifest = {
        "repo": repo,
        "base": base,
        "branch": branch,
        "content": CONTENT,
        "readme": readme,
        "docs": docs,
        "projectsDir": projects_dir.relative_to(repo_root).as_posix() if repo_root and projects_dir and projects_dir.is_dir() else None,
        "projects": described,
        "projectsReadme": projects_readme,
        "files": files,
        "reviews": [
            {"number": e["number"], "name": e["name"], "title": e["pr"]["title"], "head": e["pr"]["head"],
             "heads": e["heads"], "projects": e["projects"],
             "updated": datetime.datetime.fromtimestamp(e["updated"], datetime.timezone.utc).date().isoformat()}
            for e in entries
        ],
    }
    (out / "site.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    index = search_index(repo_root, readme, docs, described, projects_readme) if repo_root else []
    (out / "search.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shell = home_page(repo, base)
    for route in site_routes(docs, described):
        (out / route).mkdir(parents=True, exist_ok=True)
        (out / route / "index.html").write_text(shell, encoding="utf-8")
    (out / "404.html").write_text(shell, encoding="utf-8")
    return entries, failures
