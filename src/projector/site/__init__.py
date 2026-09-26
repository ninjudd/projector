"""The Projector site: the static pages a repository serves from GitHub Pages.

The site is built from content Projector keeps in the repository. Today that
is the pull request walkthroughs published to refs/projector/walkthroughs;
each spec becomes a page under /<number>/<head>/, with a redirect to the
newest head at /<number>/ and an index at the root.
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

from ..walkthrough import SpecError, prepare_page

ASSETS = resources.files(__package__) / "assets"
HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1"
FONTS = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"


def escape(s: object) -> str:
    return html.escape(str(s), quote=True)


def icon_link() -> str:
    svg = (ASSETS / "icon.svg").read_text(encoding="utf-8")
    return f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{urllib.parse.quote(svg)}">'


def page(title: str, payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<title>{escape(title)}</title>
<meta name="description" content="{escape(payload['pr']['repo'])}#{payload['pr']['number']}: {escape(payload['pr']['title'])}">
{icon_link()}
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
        (out / asset).write_bytes((ASSETS / asset).read_bytes())


def write_page(out: Path, payload: dict) -> None:
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


def build_site(root: Path, out: Path) -> tuple[list[dict], list[str]]:
    """Build every spec that can be built; report and skip the rest."""
    specs = sorted(root.glob("*/*/spec.json"))
    if not specs:
        raise SpecError(f"no walkthroughs under {root}: expected <number>/<head>/spec.json")
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
            payload = prepare_page(spec, at_head=True)
        except (SpecError, ValueError, KeyError, TypeError) as exc:
            skip(path, exc)
            continue
        by_pr.setdefault(number, []).append((spec_time(path), payload))
    if not by_pr:
        raise SpecError(f"no walkthrough built; {len(failures)} skipped")
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    copy_assets(out)
    entries = []
    for number, versions in sorted(by_pr.items(), key=lambda kv: -int(kv[0])):
        versions.sort(key=lambda v: v[0], reverse=True)
        heads = [{"head": p["pr"]["head"], "url": f"../{p['pr']['head']}/", "at": when} for when, p in versions]
        for _, payload in versions:
            head = payload["pr"]["head"]
            payload.update(indexUrl="../../", heads=[dict(h, current=h["head"] == head) for h in heads])
            write_page(out / number / head, payload)
            print(f"built {number}/{head[:9]}: {len(payload['groups'])} groups, {payload['stats']['files']} files")
        latest = versions[0][1]
        (out / number / "index.html").write_text(redirect(f"{latest['pr']['head']}/"), encoding="utf-8")
        entries.append({"number": int(number), "name": latest.get("name") or "", "pr": latest["pr"],
                        "heads": len(versions), "updated": versions[0][0]})
    (out / "index.html").write_text(index_page(entries), encoding="utf-8")
    return entries, failures


def redirect(target: str) -> str:
    t = escape(target)
    return f'<!doctype html><meta charset="utf-8"><title>Redirecting</title>{icon_link()}<meta http-equiv="refresh" content="0; url={t}"><link rel="canonical" href="{t}"><a href="{t}">Newest walkthrough</a>\n'


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
{icon_link()}
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="walkthrough.css">
<div class="wrap"><header class="top"><div><div class="eyebrow"><a href="https://github.com/{escape(repo)}">{escape(repo)}</a></div><h1>Pull request walkthroughs</h1></div></header>
<div class="card"><div class="tblwrap"><table class="tbl"><tr><th>PR</th><th>Walkthrough</th><th>Head</th><th>Versions</th><th>Updated</th></tr>{rows}</table></div>
<p class="note">Built by Projector's <span class="mono">walkthrough-pr</span> skill. Each link opens the newest version; older heads are listed in its sidebar.</p></div></div>
"""
