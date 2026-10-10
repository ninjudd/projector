"""The Projector site: the static pages a repository serves from GitHub Pages.

The site is built from content Projector keeps in the repository: its
README, its `docs/` directory and the projects under it, and the pull request
summaries published to refs/projector/summaries, which the site calls
reviews. It has three sections, each at a real path under the site's base:
the projects at the root and under projects/, every file of a project at its
path inside the projects directory without `.md` or `.html`; the reviews
under reviews/<number>/<head>/, with the newest head also at
reviews/<number>/; and the docs under docs/, the README at docs/ itself and
every other document at its path without `.md` or `.html`. An HTML page is
shown as it is, in a frame inside the site, and the files beside it are
copied too, so a page that loads its own data or a WebAssembly module works
as it does from disk. GitHub Pages serves only files that exist, so each path
gets a small shell page that loads the one shared copy of the assets and
fetches its data.
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import urllib.parse
from importlib import resources
from pathlib import Path

from ..core import Project, ProjectorError, project_from_text, title_from_text
from ..review import identity, verdict_marker
from ..summary import (ATTRIBUTES_FILE, CONTEXT_MAX_BYTES, DIFF_FILE, HEAD_DIR, LEGACY_SUMMARY_FILE, SummaryError,
                       new_file_text, prepare_page, stored_attributes)



def assets_dir():
    """The site's assets: packaged beside this module in an install, and in the
    checkout's site/assets/, where they are built, when run from source."""
    packaged = resources.files(__package__) / "assets"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / "site" / "assets"


ASSETS = assets_dir()
HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1"
FONTS = "https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap"
MARKED = "https://cdnjs.cloudflare.com/ajax/libs/marked/18.0.13/lib/marked.umd.min.js"
PURIFY = "https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.4.16/purify.min.js"
CONTENT = "content"
MAX_FILE_BYTES = 20 * 1024 * 1024
PAGES = (".md", ".html")
FIXED_ROUTES = ("", "projects/", "reviews/", "docs/", "search/")
# Where the site serves the head's copy of each file a summary page can expand.
BLOBS = "reviews/blobs"


def escape(s: object) -> str:
    return html.escape(str(s), quote=True)


def icon_link() -> str:
    svg = (ASSETS / "icon.svg").read_text(encoding="utf-8")
    return f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{urllib.parse.quote(svg)}">'


def page(title: str, payload: dict, embed: bool = True, assets: str = "", src: str = "data.json",
         site_base: str | None = None) -> str:
    # A page opened from disk cannot fetch a file beside it, so a standalone page
    # embeds its data; a site page loads data.json when it opens, and carries the
    # site's menu bar above the summary.
    data = ""
    if embed:
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        data = f'<script id="summary-data" type="application/json">{blob}</script>\n'
    bar_style = bar = bar_script = ""
    if site_base is not None:
        bar_style = f'<link rel="stylesheet" href="{escape(assets)}site.css">\n'
        bar = f'<div id="sitebar" data-base="{escape(site_base)}"></div>\n'
        bar_script = f'<script src="{escape(assets)}site.js"></script>\n'
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(payload['pr']['repo'])}#{payload['pr']['number']}: {escape(payload['pr']['title'])}">
{icon_link()}
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{escape(assets)}summary.css">
{bar_style}{bar}<div id="summary"{"" if embed else f' data-src="{escape(src)}"'}></div>
{data}<script src="{HLJS}/highlight.min.js"></script>
<script src="{HLJS}/languages/protobuf.min.js"></script>
<script src="{escape(assets)}summary.js"></script>
{bar_script}"""


def copy_assets(out: Path) -> None:
    for asset in ("summary.js", "summary.css"):
        (out / asset).write_bytes((ASSETS / asset).read_bytes())


def pr_title(payload: dict) -> str:
    """The pull request's own title, which heads its summary page."""
    return payload["pr"]["title"]


def write_page(out: Path, payload: dict) -> None:
    """A standalone page, with its data embedded and the renderer beside it."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page(pr_title(payload), payload), encoding="utf-8")
    copy_assets(out)


def build_page(summary: dict, out: Path, diff: str | None = None, at_head: bool = False, extra: dict | None = None) -> dict:
    """A page for `summary` alone, with no site around it: a stack of one, headed as a site with no projects heads it."""
    payload = prepare_page(summary, diff=diff, at_head=at_head, extra=extra)
    number = payload["pr"]["number"]
    segments = stack_segments([number], {number: payload}, {}, {})
    payload.update(segments=segments, header=segments[0]["header"])
    write_page(out, payload)
    return payload


def summary_time(path: Path) -> int:
    """When the summary was committed, falling back to its modification time.

    A summary republished beside the spec.json an older release stored takes
    that file's time, when its head was first published, so the order heads
    are republished in cannot make an older head a pull request's newest.
    """
    original = path.with_name(LEGACY_SUMMARY_FILE)
    if original.is_file():
        path = original
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", path.name], cwd=path.parent,
                             capture_output=True, text=True, check=True).stdout.strip()
        if out:
            return int(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return int(path.stat().st_mtime)


# Anyone can review a public repository's pull request, and GitHub shows a
# marker line as nothing, so only a review by someone the repository let in
# can set a status.
REVIEWER_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")


def review_statuses(reviews: list[dict]) -> dict[str, dict]:
    """Each head's status among `reviews`, from the newest Projector review each model gave it.

    A review is Projector's when a line of its body is the marker a Projector
    review carries, which names the verdict, the head it reviewed, and the
    model, and its author owns the repository, belongs to its organization, or
    collaborates on it. Models are told apart by `identity`. A head's `models`
    lists each model's newest verdict on it, changes requested first and then
    by model. Its `status` is `changes-requested` when any of them is and
    `clean` otherwise, and its `url` is the newest review with that verdict.
    A model whose newest review is a clean incremental review carries
    `incrementalFrom`: the head of the full review it builds on, and that
    model's newest full review of that head as `url`, or None when none is
    among `reviews`. The status carries the same when one model reviewed the
    head.
    """
    newest: dict[str, dict[str, dict]] = {}
    full: dict[tuple[str, str], dict] = {}

    def keep(found_by: dict, key, found: dict) -> None:
        if key not in found_by or found["at"] >= found_by[key]["at"]:
            found_by[key] = found

    for review in reviews:
        if review.get("association") not in REVIEWER_ASSOCIATIONS:
            continue
        marker = verdict_marker(review.get("body") or "")
        if marker is None:
            continue
        model = identity(marker.model)
        found = {"model": model, "status": marker.verdict, "url": review["url"], "at": review["at"],
                 "from": marker.incremental_from}
        keep(newest.setdefault(marker.sha, {}), model, found)
        if marker.incremental_from is None:
            keep(full, (marker.sha, model), found)
    statuses = {}
    for head, by_model in newest.items():
        verdicts = sorted(by_model.values(), key=lambda found: (found["status"] != "changes-requested", found["model"]))
        models = []
        for found in verdicts:
            entry = {"model": found["model"], "status": found["status"], "url": found["url"]}
            if found["from"] is not None and found["status"] == "clean":
                entry["incrementalFrom"] = {"head": found["from"],
                                            "url": (full.get((found["from"], found["model"])) or {}).get("url")}
            models.append(entry)
        status = verdicts[0]["status"]
        deciding = max((found for found in verdicts if found["status"] == status), key=lambda found: found["at"])
        statuses[head] = {"status": status, "url": deciding["url"], "models": models}
        if len(models) == 1 and "incrementalFrom" in models[0]:
            statuses[head]["incrementalFrom"] = models[0]["incrementalFrom"]
    return statuses


def stored_head_file(folder: Path, path: str) -> bytes | None:
    """The head's copy of `path` that `publish` stored in `folder`, or None when it stored none.

    Whoever can push the summaries ref writes the diff that names `path`, so
    a path that leads out of `folder`, through `..` or a symlink, reads
    nothing, and neither does anything but a regular file of at most
    CONTEXT_MAX_BYTES. `folder` must be absolute and free of symlinks.
    """
    target = (folder / path).resolve()
    try:
        if not target.is_relative_to(folder) or not target.is_file() or target.stat().st_size > CONTEXT_MAX_BYTES:
            return None
        return target.read_bytes()
    except OSError:
        return None


def head_lines(text: str) -> list[str]:
    """The lines of `text`, with no empty line after a final newline."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def shows_diff(f: dict, lines: list[str]) -> bool:
    """Whether `lines`, a file at the head, hold each context and added line of the file's diff at its number."""
    return all(0 < line[2] <= len(lines) and lines[line[2] - 1] == line[3]
               for hunk in f["hunks"] for line in hunk["lines"] if line[0] in ("a", "c"))


def hunk_gaps(hunks: list[dict], total: int) -> list[dict]:
    """The runs of a file's `total` head lines that its hunks leave hidden, above, between, and below them.

    Each gap is new-side lines `start` to `end`. `before` is the index of the
    hunk it sits above, or the number of hunks for the gap below the last,
    and `oldStart` is the old-side number of `start`. A hunk with no lines on
    a side sits after its start line on that side. An empty gap is left out.
    """
    gaps = []
    new_next = old_next = 1
    for before, hunk in enumerate(hunks):
        new_start = hunk["newStart"] + (hunk["newLines"] == 0)
        old_start = hunk["oldStart"] + (hunk["oldLines"] == 0)
        gaps.append({"before": before, "start": new_next, "end": new_start - 1, "oldStart": old_next})
        new_next, old_next = new_start + hunk["newLines"], old_start + hunk["oldLines"]
    gaps.append({"before": len(hunks), "start": new_next, "end": total, "oldStart": old_next})
    return [gap for gap in gaps if gap["start"] <= gap["end"]]


def blob_id(data: bytes, head: str) -> str:
    """The id Git gives `data` as a blob: SHA-256 where commit `head` has 64 digits, as in a SHA-256 repository, else SHA-1."""
    digest = hashlib.sha256 if len(head) == 64 else hashlib.sha1
    return digest(b"blob %d\0" % len(data) + data).hexdigest()


def add_context(payload: dict, stored: Path, out: Path, base: str, written: dict[str, int]) -> None:
    """Give each file of `payload` whose head copy `publish` stored in `stored` the `context` its page expands.

    A copy that disagrees with the file's diff is dropped with a warning, so
    a page never shows a wrong line. The copy's text, decoded as UTF-8 with
    each CRLF a LF, as the diff reads, is written once per blob under BLOBS,
    and `written` maps each blob written to its size.
    """
    pr = payload["pr"]
    for f in payload["files"]:
        data = stored_head_file(stored, f["path"])
        if data is None:
            continue
        text = data.decode("utf-8", "replace").replace("\r\n", "\n")
        lines = head_lines(text)
        if not shows_diff(f, lines):
            print(f"::warning title=Context dropped::{pr['number']}/{pr['head']}: {f['path']} at the head does not "
                  "match its diff, so its page cannot expand the lines around its hunks")
            continue
        gaps = hunk_gaps(f["hunks"], len(lines))
        if not gaps:
            continue
        blob = blob_id(data, str(pr["head"]))
        if blob not in written:
            encoded = text.encode("utf-8")
            target = out / BLOBS / f"{blob}.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)
            written[blob] = len(encoded)
        f["context"] = {"url": f"{base}{BLOBS}/{blob}.txt", "lines": len(lines), "gaps": gaps}


def build_summaries(root: Path, out: Path, base: str = "/", described: list[dict] | None = None, trunk: str = "main",
                    lookup=None, repo_root: Path | None = None, status_lookup=None,
                    projects_dir: str | None = None, stacks_lookup=None) -> tuple[list[dict], list[str], list[dict]]:
    """Build every summary that can be built; report and skip the rest.

    The entries come in the order the Reviews index lists them, as
    `stack_groups` puts them, with each stack's members from `find_stacks`,
    given `trunk`, `lookup`, and `stacks_lookup`. Each carries `stack`, the
    number of the first review in its stack, or None when no other review is
    in it, and each page lists the pull requests in its stack. `described`
    holds the projects the site has pages for. Each summary is linked to the
    one it carries out, as `project_linker` decides, and each stack is split
    into segments by `stack_segments`. Every page and entry of a stack
    carries its `segments`, and every page and entry the `header` of the
    segment its pull request is in. A file is
    generated as the `attributes.json` that `publish` stored beside a summary
    says, or, for a summary without one, as the checkout at `repo_root` reads
    its .gitattributes. A file whose head copy `publish` stored beside its
    summary carries the `context` its page expands, as `add_context`
    describes. `status_lookup` returns a pull request's state and
    reviews, as `summary.pr_status` does, or None when it cannot ask. From
    them each page carries `pr.state`, `pr.currentBaseRef`, the base branch
    GitHub reports, or None once that branch is deleted, and `review`, its
    head's status across the models that reviewed it, as `review_statuses`
    gives it: `unreviewed` when no Projector review names the head. A page
    has none of them when the lookup is missing or cannot ask, and each
    entry carries its newest head's `pr.state` and `review` the same way.
    Each page also carries `defaultBranch`, `trunk`, where a merged pull
    request's files now are when the branch it merged into is gone.

    It asks `status_lookup` about each pull request before it links any
    summary, finds the projects that open pull requests propose under
    `projects_dir`, the projects directory's path in the repository, as
    `proposed_projects` does, and links each summary to those and to
    `described`, the checkout's projects. It returns the proposed projects
    beside the entries and the failures.
    """
    described = described or []
    summaries = sorted(root.glob("*/*/summary.json"))
    unread = [path for path in sorted(root.glob(f"*/*/{LEGACY_SUMMARY_FILE}"))
              if not path.with_name("summary.json").is_file()]
    own_repo = os.environ.get("GITHUB_REPOSITORY", "").lower()
    failures: list[str] = []

    def skip(path: Path, reason: object) -> None:
        failures.append(f"{path.relative_to(root)}: {reason}")
        print(f"::error title=Summary skipped::{path.relative_to(root)}: {str(reason).replace(chr(10), ' ')}")

    for path in unread:
        skip(path, f"the site reads summary.json, not {LEGACY_SUMMARY_FILE}; run `project upgrade`, then republish it "
                   "with `project summary publish`")

    by_pr: dict[str, list[tuple[int, dict, dict]]] = {}
    absolute = root.resolve()
    written: dict[str, int] = {}
    for path in summaries:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            pr = summary.get("pr") or {}
            number, head = path.parent.parent.name, path.parent.name
            if str(pr.get("number")) != number or pr.get("head") != head:
                raise SummaryError(f"it must sit at <pr.number>/<pr.head>/summary.json")
            if own_repo and str(pr.get("repo", "")).lower() != own_repo:
                raise SummaryError(f"it is for {pr.get('repo')}, not this repository")
            stored = path.with_name(DIFF_FILE)
            if not stored.is_file():
                raise SummaryError(f"it has no {DIFF_FILE} beside it; republish it with `project summary publish`")
            attributes = path.with_name(ATTRIBUTES_FILE)
            generated = stored_attributes(attributes) if attributes.is_file() else None
            payload = prepare_page(summary, diff=stored.read_text(encoding="utf-8"), at_head=True, root=repo_root,
                                   generated=generated)
        except (SummaryError, ValueError, KeyError, TypeError) as exc:
            skip(path, exc)
            continue
        add_context(payload, absolute / number / head / HEAD_DIR, out, base, written)
        by_pr.setdefault(number, []).append((summary_time(path), payload, summary))
    print(f"wrote {len(written)} head {'file' if len(written) == 1 else 'files'} for summary pages to expand, "
          f"{sum(written.values()):,} bytes, under {BLOBS}/")
    for versions in by_pr.values():
        versions.sort(key=lambda v: v[0], reverse=True)
    # The review skill publishes its review before the summary, and publishing
    # the summary starts this build, so the reviews include the verdict on the
    # head the summary describes.
    asked = {number: status_lookup(int(number)) if status_lookup is not None else None for number in by_pr}
    newest = {int(number): versions[0][1] for number, versions in by_pr.items()}
    proposed = proposed_projects(newest, {int(n): status for n, status in asked.items()}, described, repo_root,
                                 projects_dir, out) if repo_root is not None and projects_dir is not None else []
    link = project_linker(described + proposed, base)
    headers = {p["name"]: project_header(p, base) for p in described + proposed}
    owned = {}
    for number, versions in by_pr.items():
        projects = [link(summary, payload) for _, payload, summary in versions]
        owned[int(number)] = projects[0]
    prs = {number: payload["pr"] for number, payload in newest.items()}
    members = find_stacks(prs, trunk, lookup, stacks_lookup)
    segmented: dict[tuple[int, ...], list[dict]] = {}
    for stack in members.values():
        if tuple(stack) not in segmented:
            segmented[tuple(stack)] = stack_segments(stack, newest, owned, headers)
    entries = []
    for number, versions in sorted(by_pr.items(), key=lambda kv: -int(kv[0])):
        pull = int(number)
        heads = [{"head": p["pr"]["head"], "url": f"{base}reviews/{number}/{p['pr']['head']}/", "at": when}
                 for when, p, _ in versions]
        stack = stack_rows(pull, members[pull], prs, base)
        segments = segmented[tuple(members[pull])]
        header = next(s["header"] for s in segments if pull in s["members"])
        status = asked[number]
        statuses = review_statuses(status["reviews"]) if status is not None else None
        for _, payload, _ in versions:
            head = payload["pr"]["head"]
            payload.update(heads=[dict(h, current=h["head"] == head) for h in heads], stack=stack, segments=segments,
                           header=header, defaultBranch=trunk)
            if status is not None:
                payload["pr"]["state"] = status["state"]
                if "base" in status:
                    payload["pr"]["currentBaseRef"] = status["base"]
                payload["review"] = statuses.get(head, {"status": "unreviewed"})
            folder = out / "reviews" / number / head
            folder.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            (folder / "data.json").write_text(data, encoding="utf-8")
            (folder / "index.html").write_text(
                page(pr_title(payload), payload, embed=False, assets=f"{base}assets/", site_base=base,
                     src=f"{base}reviews/{number}/{head}/data.json"), encoding="utf-8")
            print(f"built {number}/{head[:9]}: {len(payload['groups'])} groups, {payload['stats']['files']} files")
        latest = versions[0][1]
        newest_head = latest["pr"]["head"]
        (out / "reviews" / number / "index.html").write_text(
            page(pr_title(latest), latest, embed=False, assets=f"{base}assets/", site_base=base,
                 src=f"{base}reviews/{number}/{newest_head}/data.json"), encoding="utf-8")
        entries.append({"number": pull, "header": header, "segments": segments, "pr": latest["pr"],
                        "review": latest.get("review"), "heads": len(versions), "updated": versions[0][0]})
    by_number = {e["number"]: e for e in entries}
    ordered = []
    for group in stack_groups(members, prs):
        for number in group:
            ordered.append(dict(by_number[number], stack=group[0] if len(group) > 1 else None))
    return ordered, failures, proposed


PLAN_FILE = "readme.md"


def proposed_projects(newest: dict[int, dict], statuses: dict[int, dict | None], described: list[dict],
                      repo_root: Path, projects_dir: str, out: Path) -> list[dict]:
    """The projects open pull requests propose, described as `collect_projects` describes the checkout's, with `proposed`.

    `newest` is each pull request's newest summary page, and `statuses` its
    status from `summary.pr_status`, or None when GitHub could not be asked.
    A pull request proposes each `<projects_dir>/<name>/readme.md` its diff
    adds while it is open and its head branch is in this repository. One
    whose status is unknown proposes nothing, and the build says how many it
    left out. The checkout wins: a name it has as a project, or a path it
    holds, is never proposed. A plan loads as a checkout's does, and one that
    does not is skipped with a warning. When several pull requests propose
    one name, the lowest number's plan is the project, and the others are its
    `proposed.also`.

    A proposed project's `files` are the other Markdown files the same diff
    adds in its folder, outside hidden directories and deeper projects'
    folders. They are Markdown alone, because the site renders Markdown
    through DOMPurify but shows an HTML page as it is, on its own origin,
    and a proposal has not been merged. Each plan and file is written under
    out/content, beside the checkout's copies, and never over one.
    """
    plan_path = re.compile(re.escape(projects_dir.strip("/")) + r"/(.+)/" + re.escape(PLAN_FILE))
    taken = {p["name"] for p in described}
    found: dict[str, tuple[dict, dict, str]] = {}
    unknown = 0
    for number, payload in sorted(newest.items()):
        plans = [(f, match.group(1)) for f in payload["files"]
                 if f["new"] and (match := plan_path.fullmatch(f["path"])) is not None]
        if not plans:
            continue
        status = statuses.get(number)
        if status is None:
            unknown += 1
            continue
        if status["state"] != "open" or status.get("fork") is not False:
            continue
        for f, name in plans:
            if name in taken or (repo_root / f["path"]).exists():
                continue
            text = new_file_text(f) or ""
            try:
                project = project_from_text(text, name, repo_root / f["path"], Path(f["path"]))
            except ProjectorError as error:
                print(f"::warning title=Proposed project skipped::#{number} {error}")
                continue
            if name in found:
                found[name][0]["proposed"]["also"].append(number)
                continue
            entry = project.public(repo_root)
            entry["proposed"] = {"number": number, "head": payload["pr"]["head"], "also": []}
            found[name] = (entry, payload, text)
    if unknown:
        many = unknown != 1
        print(f"::warning title=Proposed projects left out::the status of {unknown} pull request{'s' if many else ''} "
              f"that add{'' if many else 's'} a plan is unknown, because GitHub could not be asked, so "
              f"{'their plans are' if many else 'its plan is'} not proposed")
    folders = sorted(((p["path"].rpartition("/")[0] + "/", p["name"])
                      for p in described + [entry for entry, _, _ in found.values()]), key=lambda f: -len(f[0]))
    proposed = []
    for name in sorted(found):
        entry, payload, text = found[name]
        write_content(out, entry["path"], text)
        entry["files"] = []
        for f in sorted(payload["files"], key=lambda f: f["path"]):
            path = f["path"]
            owner = next((owner for folder, owner in folders if path.startswith(folder)), None)
            if owner != name or not f["new"] or not path.lower().endswith(".md") or path.endswith(f"/{PLAN_FILE}"):
                continue
            if hidden(repo_root, repo_root / path) or (repo_root / path).exists():
                continue
            page = new_file_text(f)
            if page is None:
                continue
            write_content(out, path, page)
            entry["files"].append({"path": path, "title": page_title(Path(path), page)})
        proposed.append(entry)
    return proposed


def stack_groups(members: dict[int, list[int]], prs: dict[int, dict]) -> list[list[int]]:
    """The pull requests in `prs`, which have reviews, in the order the Reviews index lists them, one list per stack.

    The index lists the newest pull request first, and a stack sits where its
    newest pull request would, its pull requests together in the order
    `find_stacks` gives them in `members`, from the bottom up. A pull request
    in a stack without a review here is left out, and one alone is a stack of
    its own.
    """
    placed: set[int] = set()
    groups = []
    for number in sorted(prs, reverse=True):
        if number in placed:
            continue
        group = [n for n in members[number] if n in prs and n not in placed]
        placed.update(group)
        groups.append(group)
    return groups


def stack_rows(number: int, stack: list[int], prs: dict[int, dict], base: str = "/") -> list[dict]:
    """A row for each pull request in `stack`, `number`'s, as `find_stacks` gives it, or [] when `number` stands alone.

    A pull request with a summary links to its review, and one without, such
    as a merged layer summarized nowhere, links to itself on GitHub and has no
    title here.
    """
    if len(stack) < 2:
        return []
    repo = prs[number]["repo"]
    return [{"number": n, "title": (prs.get(n) or {}).get("title") or "",
             "url": f"{base}reviews/{n}/" if n in prs else f"https://github.com/{repo}/pull/{n}",
             "current": n == number} for n in stack]


def find_stacks(prs: dict[int, dict], trunk: str, lookup=None, stacks_lookup=None) -> dict[int, list[int]]:
    """Every member of each pull request's stack, bottom first, for each pull request in `prs`: [number] alone for one in none.

    `stacks_lookup` returns GitHub's stacks and how many requests read them,
    as `summary.repo_stacks` does, or None when GitHub cannot answer, as when
    it is missing. Within a GitHub stack, each member sits on the member
    before it in GitHub's order, merged and closed members included, and its
    base branch does not count. Every other pull request in `prs` sits on the
    one `stack_bases` finds with `trunk` and `lookup`, so one chained by hand
    onto a GitHub stack's member joins that stack. A stack holds the pull
    requests beneath a pull request and every one above any of them. From
    each pull request the walk takes its successor in its own GitHub stack
    first, then its other branches, lowest number first, so a GitHub stack's
    members stay together in GitHub's order. A member without a summary stays
    in the stack.

    It prints one line saying how the stacks were found.
    """
    answer = stacks_lookup() if stacks_lookup is not None else None
    beneath: dict[int, int] = {}
    successor: dict[int, int] = {}
    settled: set[int] = set()
    if answer is None:
        print("stacks: GitHub's stacks could not be read, so every pull request sits on its base branch")
    else:
        listed, requests = answer
        stacks, note = github_stacks(listed, requests)
        print(f"stacks: {note}")
        for stack in stacks:
            settled.update(stack)
            for lower, upper in zip(stack, stack[1:]):
                beneath[upper] = lower
                successor[lower] = upper
    beneath.update(stack_bases([{"number": number, "pr": pr} for number, pr in prs.items()], trunk, lookup, settled))
    above: dict[int, list[int]] = {}
    for upper, lower in beneath.items():
        if successor.get(lower) != upper:
            above.setdefault(lower, []).append(upper)
    walks: dict[int, list[int]] = {}
    members = {}
    for number in prs:
        bottom, seen = number, {number}
        while beneath.get(bottom) is not None and beneath[bottom] not in seen:
            bottom = beneath[bottom]
            seen.add(bottom)
        if bottom not in walks:
            order: list[int] = []
            pending = [bottom]
            while pending:
                current = pending.pop()
                if current in order:
                    continue
                order.append(current)
                pending.extend(sorted(above.get(current, []), reverse=True))
                if current in successor:
                    pending.append(successor[current])
            walks[bottom] = order
        members[number] = walks[bottom]
    return members


def github_stacks(listed: list, requests: int) -> tuple[list[list[int]], str]:
    """The pull request numbers of each stack GitHub `listed`, bottom first, and a note on what was read.

    The build reads only a stack's `number` and its `pull_requests`' numbers,
    in their order. A stack without an integer number or a list of pull
    requests is left out, and so is a member without an integer number. A
    pull request in two stacks belongs to the one with the higher number, and
    the other closes the gap it leaves.
    """
    def number_of(item: object) -> int | None:
        value = item.get("number") if isinstance(item, dict) else None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    readable: list[tuple[int, list[int]]] = []
    unnumbered = unlisted = members_left = 0
    for stack in listed:
        pulls = stack.get("pull_requests") if isinstance(stack, dict) else None
        if number_of(stack) is None:
            unnumbered += 1
        elif not isinstance(pulls, list):
            unlisted += 1
        else:
            numbers = [number_of(pull) for pull in pulls]
            members_left += numbers.count(None)
            readable.append((number_of(stack), [n for n in numbers if n is not None]))
    claimed: set[int] = set()
    stacks = []
    for _, numbers in sorted(readable, key=lambda s: -s[0]):
        kept = []
        for n in numbers:
            if n not in claimed:
                claimed.add(n)
                kept.append(n)
        stacks.append(kept)
    parts = [f"read {counted(len(listed), 'GitHub stack')} in {counted(requests, 'request')}"]
    if unnumbered:
        parts.append(f"left out {counted(unnumbered, 'stack')} with no number, whose pull requests sit on their "
                     "base branches")
    if unlisted:
        parts.append(f"left out {counted(unlisted, 'stack')} with no list of pull requests")
    if members_left:
        parts.append(f"left out {counted(members_left, 'stack member')} with no number")
    return stacks, "; ".join(parts)


def counted(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def stack_bases(entries: list[dict], trunk: str, lookup=None,
                settled: set[int] | frozenset[int] = frozenset()) -> dict[int, int]:
    """For each stacked pull request, the number of the pull request beneath it.

    A pull request is stacked only when its base branch is another pull
    request's head branch, so a base branch no pull request uses, the default
    branch among them, marks nothing. The pull request beneath is a review's
    whose summary records that branch as its `headRef`, else the `basePr` the
    summary recorded, else what `lookup` finds for the branch, asked once per
    branch. Skipping the default branch only saves a lookup that would find
    nothing. A pull request in `settled`, which GitHub's stacks already
    place, gets no base and costs no lookup.
    """
    by_branch = {e["pr"]["headRef"]: e["number"] for e in entries if e["pr"].get("headRef")}
    looked_up: dict[str, int | None] = {}
    bases = {}
    for entry in entries:
        base_ref = entry["pr"].get("baseRef") or ""
        if not base_ref or base_ref == trunk or entry["number"] in settled:
            continue
        beneath = by_branch.get(base_ref) or entry["pr"].get("basePr")
        if beneath is None and lookup is not None:
            if base_ref not in looked_up:
                looked_up[base_ref] = lookup(base_ref)
            beneath = looked_up[base_ref]
        if beneath is not None and beneath != entry["number"]:
            bases[entry["number"]] = int(beneath)
    return bases


def stack_segments(members: list[int], newest: dict[int, dict], owned: dict[int, str | None],
                   headers: dict[str, dict]) -> list[dict]:
    """A stack's segments, bottom first: each a run of `members` that carry one project, with that project's header.

    `members` is the whole stack, bottom first, as `find_stacks` gives it,
    members without a summary included. `newest` holds each summarized
    member's newest page, `owned` each one's own project from
    `project_linker`, and `headers` each project's header, as
    `project_header` makes it. The first segment starts at the bottom, under
    the first project any member names. A member that names another project
    starts a segment, and one that names the same project, or none, joins the
    segment of the member before it. A stack where no member names a project
    is one segment, headed by the first `name` a member's summary gives, or
    else the title of its first member with a summary.
    """
    first = next((owned[n] for n in members if owned.get(n)), None)
    if first is None:
        named = [newest[n]["name"] for n in members if n in newest and newest[n]["name"]]
        title = named[0] if named else next(newest[n]["pr"]["title"] for n in members if n in newest)
        return [{"header": {"title": title}, "members": list(members)}]
    segments = [{"header": headers[first], "members": []}]
    current = first
    for number in members:
        project = owned.get(number)
        if project and project != current:
            current = project
            segments.append({"header": headers[project], "members": []})
        segments[-1]["members"].append(number)
    return segments


def project_stacks(entries: list[dict]) -> dict[str, list[list[dict]]]:
    """Each project's stacks, in the order of `entries`: the `segments` of every stack that holds a segment it heads.

    `entries` come in the Reviews index's order, as `build_summaries` returns
    them, so each project's stacks come newest first. A stack is taken once,
    at its first entry, whose `stack` is None or its own number, and is filed
    once under each project its segments' headers name, however many of its
    segments name it. A stack whose every header is a title is filed under
    none.
    """
    stacks: dict[str, list[list[dict]]] = {}
    for entry in entries:
        if entry["stack"] not in (None, entry["number"]):
            continue
        named = dict.fromkeys(s["header"]["project"] for s in entry["segments"] if "project" in s["header"])
        for name in named:
            stacks.setdefault(name, []).append(entry["segments"])
    return stacks


def copy_content(repo_root: Path, path: Path, out: Path) -> str:
    """Copy one file under out/content, returning its repository path."""
    relative = path.relative_to(repo_root).as_posix()
    write_content(out, relative, path.read_bytes())
    return relative


def write_content(out: Path, relative: str, content: str | bytes) -> None:
    """Write the file at `relative` in the repository under out/content."""
    target = out / CONTENT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def is_page(path: Path) -> bool:
    return path.suffix.lower() in PAGES


def page_title(path: Path, text: str | None = None) -> str:
    """A Markdown file's first heading, or an HTML page's <title>; else its name.
    `text` is the file's text, read from `path` when not given."""
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    fallback = path.parent.name if path.stem.lower() == "index" else path.stem
    if path.suffix.lower() == ".md":
        return title_from_text(text, fallback)
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.S | re.I)
    title = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip() if match else ""
    return title or fallback


def hidden(repo_root: Path, path: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(repo_root).parts)


def pages_under(repo_root: Path, folder: Path) -> list[Path]:
    """Every Markdown file and HTML page under `folder`, outside hidden directories."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file() and is_page(p) and not hidden(repo_root, p))


def collect_docs(repo_root: Path, projects_dir: Path | None, out: Path) -> tuple[str | None, list[dict]]:
    """Copy the README and every Markdown file and HTML page under docs/ outside the projects."""
    readme = repo_root / "README.md"
    readme_path = copy_content(repo_root, readme, out) if readme.is_file() else None
    docs = []
    for path in pages_under(repo_root, repo_root / "docs"):
        if projects_dir is not None and path.is_relative_to(projects_dir):
            continue
        relative = copy_content(repo_root, path, out)
        docs.append({"path": relative, "title": page_title(path)})
    return readme_path, docs


def collect_projects(repo_root: Path, projects: list[Project], projects_dir: Path | None, out: Path) -> list[dict]:
    """Copy every project's readme and supplemental files, and describe each project."""
    if projects_dir is None or not projects_dir.is_dir():
        return []
    owners = {project.path.parent: project.name for project in projects}
    extras: dict[str, list[dict]] = {}
    for path in pages_under(repo_root, projects_dir):
        relative = copy_content(repo_root, path, out)
        owner = next((owners[d] for d in (path.parent, *path.parent.parents) if d in owners), None)
        if owner is not None and path.name != "readme.md":
            extras.setdefault(owner, []).append({"path": relative, "title": page_title(path)})
    described = []
    for project in projects:
        entry = project.public(repo_root)
        entry["files"] = extras.get(project.name, [])
        described.append(entry)
    return described


def collect_files(repo_root: Path, projects_dir: Path | None, out: Path) -> list[str]:
    """Copy every other file under docs/ and the projects directory beside the pages:
    images, and the data, scripts, and WebAssembly modules an HTML page loads."""
    folders = [repo_root / "docs"]
    if projects_dir is not None and not projects_dir.is_relative_to(folders[0]):
        folders.append(projects_dir)
    copied = []
    for path in sorted(p for folder in folders if folder.is_dir() for p in folder.rglob("*")):
        relative = path.relative_to(repo_root).as_posix()
        if not path.is_file() or is_page(path) or hidden(repo_root, path):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            print(f"::warning title=File skipped::{relative} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
            continue
        copy_content(repo_root, path, out)
        copied.append(relative)
    return copied


def project_header(project: dict, base: str) -> dict:
    """The header of a stack segment that carries out `project`, linked to its page on the site."""
    return {"project": project["name"], "title": project["title"], "url": f"{base}projects/{project['name']}/"}


def project_linker(described: list[dict], base: str):
    """Find the one project a summary carries out, among the projects in `described`, which have pages on the site.

    A summary's `project` names it when the site has that project, and a
    `project` of "" or one the site lacks names none. A summary published
    before the field existed, with no `project` or a null one, takes the
    first project its `projects` list names that the site has, or else the
    one project that owns files its diff changes, when exactly one does.
    Each changed file in a project's folder also gets that project as its
    `project`: the deepest one, so a nested project owns its own files. A
    file in any other project's folder, such as one the pull request adds,
    gets none.
    """
    by_name = {p["name"]: p for p in described}
    folders = sorted(((p["path"].rpartition("/")[0] + "/", p["name"]) for p in described), key=lambda f: -len(f[0]))

    def page_of(name: str) -> dict:
        return {"name": name, "title": by_name[name]["title"], "url": f"{base}projects/{name}/"}

    def link(summary: dict, payload: dict) -> str | None:
        owners = set()
        for f in payload["files"]:
            owner = next((name for folder, name in folders if f["path"].startswith(folder)), None)
            if owner:
                f["project"] = page_of(owner)
                owners.add(owner)
        chosen = summary.get("project")
        if isinstance(chosen, str):
            return chosen if chosen in by_name else None
        named = summary.get("projects")
        known = [n for n in named if isinstance(n, str) and n in by_name] if isinstance(named, list) else []
        if known:
            return known[0]
        return owners.pop() if len(owners) == 1 else None

    return link


def html_text(page: str) -> str:
    """The visible words of an HTML page, for search: no scripts, styles, or tags."""
    text = re.sub(r"<(script|style|template)\b.*?</\1\s*>", " ", page, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->|<[^>]+>", " ", text, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:20000]


def plain_text(markdown: str) -> str:
    """The words of a Markdown document, for search: no frontmatter, markup, or link targets."""
    text = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", markdown, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_#>|\[\]]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def search_index(content: Path, readme: str | None, docs: list[dict], projects: list[dict],
                 projects_readme: str | None) -> list[dict]:
    """One entry per served document: its site path, title, kind, and plain text, read from its copy under
    `content`, and the number of the pull request that proposes it as `proposed`, for a proposed project's.
    A document with no copy, such as a plan under a hidden folder, gets none, since the page cannot load it."""
    entries = []

    def add(path: str, route: str, title: str, kind: str, proposed: dict | None = None) -> None:
        copy = content / path
        if not copy.is_file():
            return
        text = copy.read_text(encoding="utf-8", errors="replace")
        text = html_text(text) if path.lower().endswith(".html") else plain_text(text)
        entry = {"route": route, "title": title, "kind": kind, "text": text}
        if proposed is not None:
            entry["proposed"] = proposed["number"]
        entries.append(entry)

    if readme:
        add(readme, "docs/", "README", "doc")
    for doc in docs:
        add(doc["path"], doc["route"], doc["title"], "doc")
    if projects_readme:
        add(projects_readme, "projects/", "How projects work", "project")
    for project in projects:
        add(project["path"], f"projects/{project['name']}/", project["title"], "project", project.get("proposed"))
        for file in project["files"]:
            add(file["path"], file["route"], file["title"], "project", project.get("proposed"))
    return entries


def local_route(path: str) -> str:
    """The route of a page under a section: its path without `.md` or `.html`, and a
    readme or an index.html at its folder."""
    folder, _, name = path.rpartition("/")
    if name.lower() in ("readme.md", "index.html"):
        return f"{folder}/" if folder else ""
    stem, dot, suffix = path.rpartition(".")
    return f"{stem}/" if dot and f".{suffix.lower()}" in PAGES else f"{path}/"


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

    Two pages can want one route: notes.md and notes/readme.md both want
    notes/, a project's folder may be a nested project, and an index.html
    wants its folder's route as a readme does. A readme claims first, since a
    readme is what a folder's path means, then an index.html. A page that
    finds its route taken keeps its whole file name, notes.md/, so it stays
    reachable; an index.html keeps index/ instead, since index.html/ would be
    a folder beside the shell page the folder's own route already wrote.
    """
    taken = set(FIXED_ROUTES) | {f"projects/{p['name']}/" for p in projects}
    entries = [(doc, doc_route(doc["path"]), doc["path"]) for doc in docs]
    for p in projects:
        folder = p["path"].rpartition("/")[0]
        for file in p["files"]:
            entries.append((file, project_route(file["path"], p), f"projects/{p['name']}/{file['path'][len(folder) + 1:]}"))
    claim = {"readme.md": 0, "index.html": 1}
    entries.sort(key=lambda e: claim.get(e[0]["path"].rpartition("/")[2].lower(), 2))
    for entry, route, whole in entries:
        if route in taken:
            route = f"{whole[:-5]}/" if whole.lower().endswith("/index.html") else f"{whole}/"
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
<link rel="stylesheet" href="{assets}summary.css">
<link rel="stylesheet" href="{assets}site.css">
<div id="site" data-base="{escape(base)}"></div>
<script src="{MARKED}"></script>
<script src="{PURIFY}"></script>
<script src="{HLJS}/highlight.min.js"></script>
<script src="{assets}summary.js"></script>
<script src="{assets}site.js"></script>
"""


def site_routes(docs: list[dict], projects: list[dict]) -> list[str]:
    """Every path the home page's script renders, each of which needs a shell."""
    routes = set(FIXED_ROUTES)
    routes.update(doc["route"] for doc in docs)
    for project in projects:
        routes.add(f"projects/{project['name']}/")
        routes.update(file["route"] for file in project["files"])
    return sorted(routes)


def review_row(entry: dict) -> dict:
    """A review's row on the Reviews index, in site.json.

    `title` is the one its page has, `pr_title`'s, and `header` the header
    of its stack segment. `updated` is when its newest head was published, in
    UTC. `state` and `review` are its pull request's and its newest head's,
    as its page carries them, and are left out when the build could not ask
    GitHub.
    """
    row = {"number": entry["number"], "title": pr_title(entry), "header": entry["header"],
           "heads": entry["heads"], "stack": entry["stack"],
           "updated": datetime.datetime.fromtimestamp(entry["updated"], datetime.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ")}
    if entry["pr"].get("state") is not None:
        row["state"] = entry["pr"]["state"]
    if entry["review"] is not None:
        row["review"] = entry["review"]
    return row


def build_site(out: Path, summaries: Path | None = None, repo_root: Path | None = None,
               projects: list[Project] | None = None, projects_dir: Path | None = None,
               repo: str = "", branch: str = "main", base: str = "/",
               trunk: str | None = None, lookup=None, status_lookup=None,
               stacks_lookup=None) -> tuple[list[dict], list[str], list[dict]]:
    """Build the whole site: the shell pages, the manifest, the projects, the reviews, and the docs.

    `branch` is the checked-out branch the site's GitHub links point at, and
    `trunk` the default branch a pull request that is not stacked is based on.
    `stacks_lookup` returns GitHub's stacks, or None when it cannot ask, and
    `lookup` finds the pull request whose head is a branch, for a summary in
    no GitHub stack that did not record the one it is stacked on.
    `status_lookup` returns a pull request's state, reviews, and whether it
    is a fork's, or None when it cannot ask, for each summary's file links and
    review status and for the projects open pull requests propose.
    `projects_dir` is the configured projects directory, which a pull request
    can propose the first plan of. Each project, proposed ones included,
    carries the `stacks` that `project_stacks` files under it. Returns the
    reviews, the summaries skipped, and the proposed projects.
    """
    trunk = trunk or branch
    base = "/" + base.strip("/") + "/" if base.strip("/") else "/"
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    (out / "assets").mkdir(exist_ok=True)
    for asset in ("summary.js", "summary.css", "site.js", "site.css"):
        (out / "assets" / asset).write_bytes((ASSETS / asset).read_bytes())
    readme, docs = collect_docs(repo_root, projects_dir, out) if repo_root else (None, [])
    described = collect_projects(repo_root, projects or [], projects_dir, out) if repo_root else []
    files = collect_files(repo_root, projects_dir, out) if repo_root else []
    projects_path = projects_dir.relative_to(repo_root).as_posix() \
        if repo_root and projects_dir and projects_dir.is_relative_to(repo_root) else None
    built = build_summaries(summaries, out, base, described, trunk=trunk, lookup=lookup, repo_root=repo_root,
                            status_lookup=status_lookup, projects_dir=projects_path, stacks_lookup=stacks_lookup) \
        if summaries and summaries.is_dir() else ([], [], [])
    entries, failures, proposed = built
    described = sorted(described + proposed, key=lambda p: p["name"])
    assign_routes(docs, described)
    stacks = project_stacks(entries)
    for project in described:
        project["stacks"] = stacks.get(project["name"], [])
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
        "projectsDir": projects_path if described or (projects_dir and projects_dir.is_dir()) else None,
        "projects": described,
        "projectsReadme": projects_readme,
        "files": files,
        "reviews": [review_row(e) for e in entries],
    }
    (out / "site.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    index = search_index(out / CONTENT, readme, docs, described, projects_readme) if repo_root else []
    (out / "search.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shell = home_page(repo, base)
    for route in site_routes(docs, described):
        (out / route).mkdir(parents=True, exist_ok=True)
        (out / route / "index.html").write_text(shell, encoding="utf-8")
    (out / "404.html").write_text(shell, encoding="utf-8")
    return entries, failures, proposed
