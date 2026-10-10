"""Pull request summaries: the data a summary page is built from.

A summary records a pull request's merge base and head and assigns every changed
file to a group with its explanation and checks. This module writes skeleton
summaries, checks a summary against its diff, publishes summaries to the hidden ref
refs/projector/summaries, and says whether a repository hosts a site that
serves them. Rendering summaries into pages is `projector.site`'s job.
"""

from __future__ import annotations

import datetime
import hashlib
import html
from html.parser import HTMLParser
import json
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .core import FileAction, ProjectorError

SUMMARY_VERSION = 1
CHECK_KINDS = ("context", "verify", "flag")
PAGES_REF = "refs/projector/summaries"
DISPATCH_EVENT = "projector-summaries"
PAGES_ROOT = "summaries"
DIFF_FILE = "diff.patch"
# What `publish` read from the head's .gitattributes, stored beside the diff.
ATTRIBUTES_FILE = "attributes.json"
# Publishes running at once, as a review loop's subagents run them, race to
# move the summaries ref, so a push that loses is rebuilt on the new tip and
# tried again, up to this many pushes in all.
PUSH_ATTEMPTS = 5
# What git prints when a push lost that race: the ref moved since the fetch, or
# the server could not lock or update it while another push held it. A hook or
# permission refusal says "rejected" too, so that word alone is not one of them.
PUSH_RACE_SIGNS = ("non-fast-forward", "fetch first", "stale info", "cannot lock ref", "failed to update ref")
# The name older releases stored each summary under. The site shows no summary
# from this file. It reports each one with no summary.json beside it, so the
# repository knows to republish it, and dates a republished summary by this file.
LEGACY_SUMMARY_FILE = "spec.json"
WORKFLOW_PATH = ".github/workflows/projector-site.yml"
LEGACY_WORKFLOW_PATHS = (".github/workflows/walkthroughs.yml",)
# The names summaries were published under when they were called walkthroughs.
# A repository that published under them keeps its summaries on the old ref, under
# the old root, until its next publish seeds the new ref from them, and its
# site workflow may still listen for the old event.
LEGACY_PAGES_REF = "refs/projector/walkthroughs"
LEGACY_PAGES_ROOT = "walkthroughs"
LEGACY_DISPATCH_EVENT = "projector-walkthroughs"

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
GENERATED_ATTRIBUTE = "linguist-generated"
FULL_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

WORKFLOW = """name: Projector site
on:
  repository_dispatch:
    types: [{event}]
  push:
    branches: [{branch}]
    paths: [{paths}]
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
      url: ${{{{ steps.site.outputs.page_url }}}}
    steps:
      - id: site
        uses: ninjudd/projector/actions/site@{ref}
"""
# Earlier shapes of the workflow Projector wrote. A file matching one of them, or
# the current one, is Projector's own and safe to rewrite; anything else has been
# edited by hand and is left for its owner.
PREVIOUS_WORKFLOWS = (
    # Before the site rebuilt on docs changes, when its step was named walkthroughs.
    """name: Projector site
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
""",
    # Rebuilding on docs changes, before the step was renamed site.
    """name: Projector site
on:
  repository_dispatch:
    types: [{event}]
  push:
    branches: [{branch}]
    paths: [{paths}]
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
""",
)


class SummaryError(ProjectorError):
    pass


class NeedsAdmin(SummaryError):
    """Setting up the site needs changes on GitHub that only an admin can make.

    `commands` are the `gh` commands that make them, in order, for the user
    to hand an admin; `workflow` is the site workflow `init` wrote beside them.
    """

    def __init__(self, repo: str, needed: str, commands: list[str]) -> None:
        super().__init__(repo, needed, commands)
        self.reason = f"you are not an admin of {repo}, so init cannot {needed}"
        self.commands = commands
        self.workflow: FileAction | None = None

    def steps(self) -> str:
        """What the user does next: the admin's commands, one per line."""
        return ("ask an admin to run these commands; the site deploys once they have and the site workflow is on "
                "the default branch:" + "".join(f"\n    {command}" for command in self.commands))

    def __str__(self) -> str:
        return f"{self.reason}; {self.steps()}"


# Diff parsing

def lang_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    if name == "dockerfile":
        return "dockerfile"
    if name == "makefile":
        return "makefile"
    dot = name.rfind(".")
    return LANGS.get(name[dot:], "") if dot >= 0 else ""


def kind_of(path: str, generated: bool | None = None) -> str:
    """What a summary shows `path` as: generated, test, docs, or '' for hand-written.

    `generated` is the file's `linguist-generated` value from .gitattributes,
    None when it has none. True makes the file generated and False keeps it
    out of that class, whatever its path says; with None, the path decides.
    """
    lower = path.lower()
    if generated or (generated is None and (lower.endswith(GENERATED_SUFFIXES)
                                            or any(part in "/" + lower for part in GENERATED_PARTS))):
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


def classify(files: list[dict], generated: dict[str, bool]) -> None:
    """Set each file's kind again, letting its `linguist-generated` value in `generated` say if it is generated."""
    for f in files:
        f["kind"] = kind_of(f["path"], generated.get(f["path"]))


# Attributes

def checkout_root(start: Path | None = None) -> Path | None:
    """The top of the Git checkout holding `start`, the current directory by default, or None outside one."""
    try:
        found = subprocess.run(["git", "-C", str(start or Path.cwd()), "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True)
    except OSError:
        return None
    top = found.stdout.strip()
    return Path(top) if found.returncode == 0 and top else None


def names_repository(root: Path, repo: str) -> bool:
    """Whether one of the checkout's remotes is `repo`, OWNER/NAME on any host."""
    want = repo.strip("/").lower()
    if not want:
        return False
    found = subprocess.run(["git", "-C", str(root), "config", "--get-regexp", r"^remote\..*\.url$"],
                           capture_output=True, text=True)
    for line in found.stdout.splitlines():
        url = line.split(None, 1)[-1].strip().rstrip("/").lower().removesuffix(".git")
        if url == want or url.endswith(("/" + want, ":" + want)):
            return True
    return False


def check_attr(root: Path, paths: list[str], *options: str, env: dict | None = None) -> list[str] | None:
    """Each path's `linguist-generated` value as `git check-attr` prints it, in order, or None when git fails.

    The answer is GitHub's: only the repository's own .gitattributes files
    count, so the system's attributes file and the user's global one are left
    out, and patterns match a path's exact case even in a checkout on a
    case-insensitive file system, where Git would otherwise ignore case.
    """
    command = ["git", "-C", str(root), "-c", "core.attributesFile=", "-c", "core.ignoreCase=false",
               "check-attr", "-z", "--stdin", *options, GENERATED_ATTRIBUTE]
    try:
        done = subprocess.run(command, input=b"".join(p.encode("utf-8", "replace") + b"\0" for p in paths),
                              capture_output=True, env=dict(env or os.environ, GIT_ATTR_NOSYSTEM="1"))
    except OSError:
        return None
    # Each path prints as three fields: the path, the attribute, and its value.
    fields = done.stdout.split(b"\0")
    if done.returncode != 0 or len(fields) < 3 * len(paths):
        return None
    return [fields[3 * i + 2].decode("utf-8", "replace") for i in range(len(paths))]


def generated_values(paths: list[str], values: list[str]) -> dict[str, bool]:
    """Each path whose `linguist-generated` is specified, mapped to whether GitHub calls it generated.

    GitHub reads an unset attribute, `-linguist-generated`, or the value
    `false` as not generated, and any other setting as generated.
    """
    return {path: value not in ("unset", "false") for path, value in zip(paths, values) if value != "unspecified"}


def head_attributes(root: Path, head: str, paths: list[str]) -> dict[str, bool] | None:
    """`linguist-generated` for each of `paths` as the .gitattributes files of commit `head` set it.

    None when the checkout at `root` does not have that commit, or git cannot
    read it. Git 2.40 added `check-attr --source`; an older Git reads the
    commit's tree into a scratch index and checks the attributes there.
    """
    if not FULL_SHA.fullmatch(head or ""):
        return None
    if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{head}^{{commit}}"],
                      capture_output=True).returncode != 0:
        return None
    values = check_attr(root, paths, f"--source={head}")
    if values is None:
        with tempfile.TemporaryDirectory() as scratch:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(scratch) / "index"))
            read = subprocess.run(["git", "-C", str(root), "read-tree", head], capture_output=True, env=env)
            values = check_attr(root, paths, "--cached", env=env) if read.returncode == 0 else None
    return None if values is None else generated_values(paths, values)


def generated_attributes(paths: list[str], head: str, repo: str, root: Path | None = None) -> dict[str, bool]:
    """`linguist-generated` for each of `paths` at pull request head `head` of `repo`, read from a checkout.

    The checkout is the one holding `root`, the current directory by default.
    When it has the head commit, the head's own .gitattributes files answer.
    Otherwise a checkout of `repo` answers from its working tree, the
    nearest thing it has, which for a deploy is the default branch. Any other
    directory has no answer, so the path alone decides each file's kind.
    """
    top = checkout_root(root)
    if top is None or not paths:
        return {}
    found = head_attributes(top, head, paths)
    if found is not None:
        return found
    if not names_repository(top, repo):
        return {}
    values = check_attr(top, paths)
    return {} if values is None else generated_values(paths, values)


def stored_attributes(path: Path) -> dict[str, bool]:
    """The `linguist-generated` values `publish` stored in `path`, as `attributes.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get(GENERATED_ATTRIBUTE) if isinstance(data, dict) else None
    if not isinstance(values, dict) or not all(isinstance(v, bool) for v in values.values()):
        raise SummaryError(f"{path.name} must map each path to whether it is {GENERATED_ATTRIBUTE}")
    return values


# GitHub

def gh(*args: str, input: str | None = None) -> str:
    """Run gh with `input` on standard input. A failure names the arguments, never the input."""
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, input=input).stdout
    except FileNotFoundError as exc:
        raise SummaryError("the gh CLI is not installed; pass --diff and fill the pr fields by hand") from exc
    except subprocess.CalledProcessError as exc:
        raise SummaryError(f"gh {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def gh_lookup(*args: str) -> str | None:
    """Run gh, returning None when GitHub answers 404 and raising on any other failure."""
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    except FileNotFoundError as exc:
        raise SummaryError("the gh CLI is not installed") from exc
    except subprocess.CalledProcessError as exc:
        if "HTTP 404" in exc.stderr:
            return None
        raise SummaryError(f"gh {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def exposure(repo: str, pages: dict) -> str:
    """Why serving this repository's content on its Pages site would leak it, or ''.

    On a plan without private Pages a private repository's site is public, so
    everything the site copies from the repository would be too. A site with
    no `public` field is treated as public.
    """
    if pages.get("public", True) and (gh_lookup("api", f"repos/{repo}", "--jq", ".private") or "").strip() == "true":
        return f"{repo} is private but its GitHub Pages site is public"
    return ""


def require_private_site(repo: str) -> None:
    """Refuse to deploy when a private repository's Pages site is public."""
    raw = gh_lookup("api", f"repos/{repo}/pages")
    if raw is None:
        raise SummaryError(f"{repo} has no GitHub Pages site to deploy to")
    exposed = exposure(repo, json.loads(raw))
    if exposed:
        raise SummaryError(f"{exposed}; make the site private or the repository public before deploying")


def enable_pages(repo: str, admin: bool = True, takeover: bool = False, url: str = "") -> dict:
    """Give the repository a Pages site that GitHub Actions deploys, private if the repository is.

    With `url`, a custom domain's address such as https://projects.example.com/,
    the site is served at that domain and reports `url` as its address.

    Returns what changed: `pages` is created, updated, or unchanged, and
    `visibility` is updated when a private repository's public site was made
    private. Raises when a private repository's site cannot be made private,
    because the site would publish the repository's README, plans and diffs,
    and first deletes a site this call created, so the refusal leaves the
    repository as it found it. Without `admin`, it changes nothing and raises
    when anything needs changing, so a site an admin already set up still
    counts as set up. Without `takeover`, it raises rather than switch a site
    that deploys from a branch, because that site is the repository's own.
    """
    raw = gh_lookup("api", f"repos/{repo}/pages")
    if raw is not None and not takeover and json.loads(raw).get("build_type") != "workflow":
        raise SummaryError(f"{repo} already serves its own GitHub Pages site from a branch; pass --site to replace it "
                        "with the Projector site")
    domain = urlparse(url).hostname or ""
    if not admin:
        pages = json.loads(raw) if raw is not None else None
        endpoint = f"repos/{repo}/pages"
        needs = []
        if pages is None:
            needs.append(("turn on its GitHub Pages site", f"gh api -X POST {endpoint} -f build_type=workflow"))
        elif pages.get("build_type") != "workflow":
            needs.append(("switch its GitHub Pages site to deploy from GitHub Actions",
                          f"gh api -X PUT {endpoint} -f build_type=workflow"))
        # A site GitHub is about to create counts as public, as it does below.
        if exposure(repo, pages or {}):
            needs.append(("make its public GitHub Pages site private", f"gh api -X PUT {endpoint} -F public=false"))
        if domain and (pages or {}).get("cname") != domain:
            needs.append((f"serve its GitHub Pages site at {domain}", f"gh api -X PUT {endpoint} -f cname={domain}"))
        if needs:
            raise NeedsAdmin(repo, needs[0][0], [command for _, command in needs])
        return {"pages": "unchanged", "visibility": "unchanged", "url": url or site_url(pages),
                "public": pages.get("public", True)}
    if raw is None:
        gh("api", "-X", "POST", f"repos/{repo}/pages", "-f", "build_type=workflow")
        action = "created"
    elif json.loads(raw).get("build_type") != "workflow":
        gh("api", "-X", "PUT", f"repos/{repo}/pages", "-f", "build_type=workflow")
        action = "updated"
    else:
        action = "unchanged"
    pages = json.loads(gh("api", f"repos/{repo}/pages")) if action != "unchanged" else json.loads(raw)
    visibility = "unchanged"
    if exposure(repo, pages):
        refusal = (f"{repo} is private but GitHub will not make its Pages site private, which needs private "
                   "Pages (GitHub Enterprise Cloud); make the repository public, or serve the site with "
                   "`project site serve` instead")
        try:
            gh("api", "-X", "PUT", f"repos/{repo}/pages", "-F", "public=false")
            pages = json.loads(gh("api", f"repos/{repo}/pages"))
            failure = refusal if exposure(repo, pages) else ""
        except SummaryError as exc:
            failure = f"{refusal}: {exc}"
        if failure:
            # A site this call created would stay public on a private repository.
            if action == "created":
                gh("api", "-X", "DELETE", f"repos/{repo}/pages")
                failure += "; the public Pages site init created was deleted"
            raise SummaryError(failure)
        visibility = "updated"
    # The domain is set once the site is private, so a failure leaves no public site behind.
    if domain and pages.get("cname") != domain:
        gh("api", "-X", "PUT", f"repos/{repo}/pages", "-f", f"cname={domain}")
        pages = json.loads(gh("api", f"repos/{repo}/pages"))
        action = "updated" if action == "unchanged" else action
    return {"pages": action, "visibility": visibility, "url": url or site_url(pages),
            "public": pages.get("public", True)}


def hosting(repo: str) -> tuple[str | None, str]:
    """Return the URL of the repository's site for summaries, or None and why it is not set up.

    Both halves are required. The workflow on the default branch is the reviewed
    decision to host summaries; a Pages site alone may serve something else.
    """
    if not any(gh_lookup("api", f"repos/{repo}/contents/{path}", "--jq", ".path") is not None
               for path in (WORKFLOW_PATH, *LEGACY_WORKFLOW_PATHS)):
        return None, f"{repo} has no {WORKFLOW_PATH} on its default branch"
    raw = gh_lookup("api", f"repos/{repo}/pages")
    if raw is None:
        return None, f"{repo} has the Projector site workflow but no GitHub Pages site"
    pages = json.loads(raw)
    exposed = exposure(repo, pages)
    if exposed:
        return None, exposed
    return site_url(pages), ""


def site_url(pages: dict) -> str:
    """The Pages site's URL, with a trailing slash."""
    site = pages.get("html_url") or ""
    if not site:
        return ""
    # GitHub reports an http:// URL for a custom domain that does not enforce
    # HTTPS, even once its certificate is issued and https:// serves the site.
    certified = (pages.get("https_certificate") or {}).get("state") == "approved"
    if site.startswith("http:") and (pages.get("https_enforced") or certified):
        site = "https:" + site.removeprefix("http:")
    return site.rstrip("/") + "/"


def homepage_url(site: str) -> str:
    """The site's URL as a website link, without the trailing slash `site_url` keeps for joining paths."""
    return site.rstrip("/")


def homepage_command(repo: str, url: str = "") -> str:
    """The `gh` command an admin runs to link the repository's website to its site.

    Without `url`, the command reads the address from the Pages site once the
    admin's earlier commands have run, since creating a site gives it an
    address and making it private moves it.
    """
    if url:
        return f"gh api -X PATCH repos/{repo} -f homepage={shlex.quote(homepage_url(url))}"
    return (f"gh api -X PATCH repos/{repo} -f homepage=\"$(gh api repos/{repo}/pages "
            f"--jq '.html_url | rtrimstr(\"/\")')\"")


def same_link(first: str, second: str) -> bool:
    """Whether two website links reach the same place, ignoring scheme, case, and a trailing slash."""

    def bare(link: str) -> str:
        return link.strip().split("://", 1)[-1].rstrip("/").lower()

    return bare(first) == bare(second)


def set_homepage(repo: str, url: str, current: str, admin: bool = True, replace: bool = False) -> tuple[str, str]:
    """Point the repository's website link at `url`, unless it already links elsewhere and not `replace`.

    The link is written without a trailing slash. Returns the action, updated,
    unchanged, or kept, and for kept, why. A link that differs only in scheme
    or trailing slash already reaches the site, as the one GitHub's "Use your
    GitHub Pages website" box writes does.
    """
    url = homepage_url(url)
    current = current.strip()
    if current and same_link(current, url):
        return "unchanged", ""
    if current and not replace:
        return "kept", f"{repo} already links its website to {current}; set it to {url} to link the site"
    if not admin:
        return "kept", (f"you are not an admin of {repo}, so its website does not link to {url}; "
                        f"ask an admin to run `{homepage_command(repo, url)}`")
    gh("api", "-X", "PATCH", f"repos/{repo}", "-f", f"homepage={url}")
    return "updated", ""


def merge_base(repo: str, base_ref: str, head: str) -> str:
    return gh("api", f"repos/{repo}/compare/{base_ref}...{head}", "--jq", ".merge_base_commit.sha").strip()


def base_pr(repo: str, base_ref: str) -> int | None:
    """The open pull request whose head is `base_ref`, which a pull request based on it is stacked on.

    Only an open one: a long-lived branch such as `develop` has merged release
    pull requests from it, and a pull request based on `develop` sits on none
    of them.
    """
    owner = repo.split("/", 1)[0]
    found = gh_lookup("api", "-X", "GET", f"repos/{repo}/pulls", "-f", f"head={owner}:{base_ref}",
                      "-f", "per_page=1", "--jq", ".[0].number // empty")
    return int(found) if found and found.strip().isdigit() else None


PR_STATUS_QUERY = """query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      state
      reviews(first: 100, after: $endCursor) {
        nodes { body url submittedAt authorAssociation }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}"""


def pr_status(repo: str, number: int) -> dict:
    """Pull request `number`'s `state`, `open`, `merged` or `closed`, and its submitted `reviews`.

    Each review has its `body`, its page as `url`, its time as `at`, and its
    author's relation to the repository as `association`, GitHub's
    `authorAssociation`. One query answers both, a page of reviews at a time.
    """
    owner, name = repo.split("/", 1)
    pages = gh("api", "graphql", "--paginate", "-f", f"query={PR_STATUS_QUERY}", "-f", f"owner={owner}",
               "-f", f"name={name}", "-F", f"number={number}",
               "--jq", ".data.repository.pullRequest | {state, reviews: [.reviews.nodes[] | select(.submittedAt != null) "
                       "| {body, url, at: .submittedAt, association: .authorAssociation}]} | @json")
    found = [json.loads(page) for page in pages.splitlines() if page.strip()]
    if not found or not found[0].get("state"):
        raise SummaryError(f"GitHub returned no state for {repo}#{number}")
    return {"state": str(found[0]["state"]).lower(), "reviews": [r for page in found for r in page["reviews"]]}


def pr_metadata(repo: str, number: int, stack: bool = True) -> dict:
    raw = json.loads(gh("pr", "view", str(number), "--repo", repo, "--json", "title,headRefOid,headRefName,baseRefName"))
    meta = {
        "repo": repo,
        "number": number,
        "title": raw["title"],
        "head": raw["headRefOid"],
        "headRef": raw["headRefName"],
        "base": merge_base(repo, raw["baseRefName"], raw["headRefOid"]),
        "baseRef": raw["baseRefName"],
    }
    # A pull request on the default branch is not stacked, even when an open
    # main -> production pull request has the default branch as its head.
    if stack and raw["baseRefName"] != gh("api", f"repos/{repo}", "--jq", ".default_branch").strip():
        stacked_on = base_pr(repo, raw["baseRefName"])
        if stacked_on is not None and stacked_on != number:
            meta["basePr"] = stacked_on
    return meta


def fetch_diff(repo: str, base: str, head: str) -> str:
    return gh("api", "-H", "Accept: application/vnd.github.diff", f"repos/{repo}/compare/{base}...{head}")


# Sanitizing summary HTML

ALLOWED_TAGS = {"a", "b", "br", "code", "div", "em", "h3", "h4", "i", "li", "ol", "p", "pre", "span", "strong",
                "table", "tbody", "td", "th", "thead", "tr", "ul"}
VOID_TAGS = {"br"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math"}
ALLOWED_CLASSES = {"tblwrap", "tbl", "r", "note", "tight", "count", "mono"}


class Sanitizer(HTMLParser):
    """Rebuild summary HTML from the tags, classes and links format.md documents."""

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


def sanitized(summary: dict) -> tuple[dict, list[dict]]:
    """The summary's overview and groups with every HTML field sanitized."""
    o = summary.get("overview") or {}
    overview = {
        "summary": [sanitize_html(p) for p in o.get("summary") or []],
        "cards": [{"id": str(c.get("id") or ""), "title": sanitize_html(c.get("title") or ""), "html": sanitize_html(c.get("html") or "")}
                  for c in o.get("cards") or []],
    }
    groups = []
    for g in summary["groups"]:
        context = [{"kind": "context", "text": c} for c in g.get("concepts") or []]
        groups.append({
            "id": str(g["id"]),
            "title": sanitize_html(g["title"]),
            "kicker": sanitize_html(g.get("kicker") or ""),
            "intro": [sanitize_html(p) for p in g.get("intro") or []],
            "checks": [sanitized_check(c) for c in context + list(g.get("checks") or [])],
            "files": [sanitized_file(f) for f in g.get("files") or []],
        })
    return overview, groups


def sanitized_check(c: dict) -> dict:
    out = {"kind": c["kind"], "text": sanitize_html(c["text"])}
    if c.get("line") is not None:
        out["line"] = int(c["line"])
        out["side"] = "old" if c.get("side") == "old" else "new"
    return out


def sanitized_file(f: dict) -> dict:
    out = {k: (sanitize_html(v) if k == "note" else v) for k, v in f.items() if k in ("path", "note", "collapsed")}
    if f.get("checks"):
        out["checks"] = [sanitized_check(c) for c in f["checks"]]
    return out


# Summaries and pages

def validate(summary: dict, files: list[dict]) -> None:
    if summary.get("version") != SUMMARY_VERSION:
        raise SummaryError(f"summary version must be {SUMMARY_VERSION}")
    for key in ("repo", "number", "head", "title"):
        if not summary.get("pr", {}).get(key):
            raise SummaryError(f"pr.{key} is required")
    groups = summary.get("groups") or []
    if not groups:
        raise SummaryError("the summary has no groups")
    by_path = {f["path"]: f for f in files}
    seen: dict[str, str] = {}
    problems: list[str] = []
    ids: set[str] = set()
    kinds = ", ".join(CHECK_KINDS)
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
            if path not in by_path:
                problems.append(f"{gid}: {path} is not in the diff")
                continue
            if path in seen:
                problems.append(f"{path} is in both {seen[path]} and {gid}")
            else:
                seen[path] = gid
            lines = {"new": diff_lines(by_path[path], "new"), "old": diff_lines(by_path[path], "old")}
            for c in fs.get("checks") or []:
                if c.get("kind") not in CHECK_KINDS or not c.get("text"):
                    problems.append(f"{path}: each check needs a kind ({kinds}) and a text")
                elif c.get("line") is not None:
                    side = "old" if c.get("side") == "old" else "new"
                    if not isinstance(c["line"], int) or c["line"] not in lines[side]:
                        problems.append(f"{path}: line {c['line']} ({side} side) is not in its diff")
        for c in g.get("checks") or []:
            if c.get("kind") not in CHECK_KINDS or not c.get("text"):
                problems.append(f"{gid}: each check needs a kind ({kinds}) and a text")
            elif c.get("line") is not None:
                problems.append(f"{gid}: a check with a line belongs on its file")
    for path in sorted(set(by_path) - set(seen)):
        problems.append(f"{path} is in no group")
    if problems:
        raise SummaryError("the summary does not match the diff:\n  " + "\n  ".join(problems))


def diff_lines(f: dict, side: str) -> set[int]:
    """The line numbers a check can point at on one side of a file's diff: on
    the new side the added and context lines, on the old side the removed and
    context lines."""
    kinds, index = (("a", "c"), 2) if side == "new" else (("d", "c"), 1)
    return {line[index] for h in f["hunks"] for line in h["lines"] if line[0] in kinds and isinstance(line[index], int)}


def stats(files: list[dict]) -> dict:
    out = {"files": len(files), "adds": 0, "dels": 0, "hand": 0, "test": 0, "generated": 0, "docs": 0}
    for f in files:
        out["adds"] += f["adds"]
        out["dels"] += f["dels"]
        out[f["kind"] or "hand"] += f["adds"] + f["dels"]
    return out


def summary_diff(summary: dict) -> str:
    pr = summary["pr"]
    base = pr.get("base") or merge_base(pr["repo"], pr.get("baseRef") or "main", pr["head"])
    return fetch_diff(pr["repo"], base, pr["head"])


def prepare_page(summary: dict, diff: str | None = None, at_head: bool = False, extra: dict | None = None,
                 root: Path | None = None, generated: dict[str, bool] | None = None) -> dict:
    """The data a summary page renders: the summary, sanitized, with its diff's files and counts.

    `generated` holds the `linguist-generated` values `publish` stored for the
    head. Without it they are read from the checkout holding `root`, as
    `generated_attributes` describes.
    """
    pr = dict(summary.get("pr") or {})
    if not pr.get("repo") or not pr.get("number") or not pr.get("head"):
        raise SummaryError("pr.repo, pr.number and pr.head are required")
    if not at_head:
        try:
            live = pr_metadata(pr["repo"], int(pr["number"]), stack=False)
        except SummaryError as exc:
            if diff is None:
                raise
            print(f"summary: could not check whether the PR moved past {pr['head'][:9]} ({exc}); building from the given diff", file=sys.stderr)
        else:
            if live["head"] != pr["head"]:
                raise SummaryError(f"the PR head moved from {pr['head'][:9]} to {live['head'][:9]}; update the summary for the new head before building")
    if diff is None:
        diff = summary_diff(summary)
    files = parse_diff(diff)
    validate(summary, files)
    if generated is None:
        generated = generated_attributes([f["path"] for f in files], str(pr["head"]), str(pr["repo"]), root)
    classify(files, generated)
    overview, groups = sanitized(summary)
    payload = {
        "name": summary.get("name") or f"{pr['repo'].split('/')[-1]}#{pr['number']} summary",
        "pr": pr,
        "overview": overview,
        "groups": groups,
        "files": files,
        "stats": stats(files),
        "generatedAt": datetime.date.today().isoformat(),
    }
    payload.update(extra or {})
    return payload


# Publishing to the summaries ref

def git(*args: str, env: dict | None = None, input: str | None = None) -> str:
    try:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=env, input=input).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise SummaryError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def repo_slug(remote_url: str) -> str:
    url = remote_url.strip()
    for prefix in ("git@github.com:", "ssh://git@github.com/", "https://github.com/"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    else:
        return ""
    return url[:-4] if url.endswith(".git") else url


def tracking_ref(remote: str, ref: str) -> str:
    """Where this checkout keeps its copy of the remote's `ref`."""
    return ref.replace("refs/projector/", f"refs/projector/remotes/{remote}/", 1)


def fetch_ref(remote: str, ref: str) -> str:
    """Fetch the remote's `ref` into its tracking ref; return its commit, or '' when the fetch fails."""
    local = tracking_ref(remote, ref)
    fetched = subprocess.run(["git", "fetch", "--quiet", remote, f"+{ref}:{local}"], capture_output=True, text=True)
    return git("rev-parse", "--verify", "--quiet", local) if fetched.returncode == 0 else ""


def seed_from_legacy(remote: str) -> str:
    """The head of a history for the summaries ref that carries the old walkthroughs ref's summaries, or ''.

    The site dates each summary by the last commit that touched its path, and a
    path-limited log does not follow a move, so one commit moving every summary
    to summaries/ would date them all at the move and let an older head of a
    pull request tie with, or outrank, its newest. Each old commit is replayed
    instead, with its walkthroughs/ folder at summaries/ and its author,
    committer, dates, and message kept, so every summary keeps the date it was
    published. Nothing is pushed here; the publish that asked for the seed
    pushes it with its own summary on top. The old ref stays on the remote, and
    the repository can delete it once the new ref exists.
    """
    old = fetch_ref(remote, LEGACY_PAGES_REF)
    if not old:
        return ""
    head = ""
    for commit in git("rev-list", "--reverse", "--first-parent", old).split():
        try:
            summaries = git("rev-parse", "--verify", "--quiet", f"{commit}:{LEGACY_PAGES_ROOT}")
        except SummaryError:
            continue  # A commit from before the folder held anything.
        tree = git("mktree", input=f"040000 tree {summaries}\t{PAGES_ROOT}\n")
        fields = git("show", "-s", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%B", commit).split("\0", 6)
        env = {**os.environ,
               "GIT_AUTHOR_NAME": fields[0], "GIT_AUTHOR_EMAIL": fields[1], "GIT_AUTHOR_DATE": fields[2],
               "GIT_COMMITTER_NAME": fields[3], "GIT_COMMITTER_EMAIL": fields[4], "GIT_COMMITTER_DATE": fields[5]}
        parents = ["-p", head] if head else []
        head = git("commit-tree", tree, *parents, "-m", fields[6] or f"Carry over {commit[:9]}", env=env)
    return head


def dispatch(repo: str) -> None:
    gh("api", f"repos/{repo}/dispatches", "-f", f"event_type={DISPATCH_EVENT}")
    # A site workflow generated before the rename listens only for the old
    # event, so send it too; a workflow generated since listens only for the
    # new one, so an updated repository still builds once. A later release
    # stops sending the old event.
    gh("api", f"repos/{repo}/dispatches", "-f", f"event_type={LEGACY_DISPATCH_EVENT}")


def ref_exists(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                          capture_output=True).returncode == 0


def summary_tree(parent: str, folder: str, summary: dict, diff: str, generated: dict[str, bool] | None = None) -> str:
    """The tree of `parent`, or an empty one, with the summary and its diff written under `folder`.

    With `generated`, the head's `linguist-generated` values go beside them.
    Without, an attributes file an earlier publish of this head left stays.
    """
    files = [("summary.json", json.dumps(summary, indent=1, ensure_ascii=False) + "\n"), (DIFF_FILE, diff)]
    if generated is not None:
        record = {GENERATED_ATTRIBUTE: dict(sorted(generated.items()))}
        files.append((ATTRIBUTES_FILE, json.dumps(record, indent=1, ensure_ascii=False) + "\n"))
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        if parent:
            git("read-tree", parent, env=env)
        for name, content in files:
            blob = git("hash-object", "-w", "--stdin", input=content)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{folder}/{name}", env=env)
        return git("write-tree", env=env)


def summary_pr(summary: dict) -> dict:
    """The summary's pull request, refusing a summary that cannot say where it is published."""
    pr = summary.get("pr") or {}
    if summary.get("version") != SUMMARY_VERSION or not pr.get("repo") or not pr.get("number") or not pr.get("head"):
        raise SummaryError("the summary needs version, pr.repo, pr.number and pr.head")
    return pr


def push_decision(repo: str) -> tuple[str | None, str]:
    """The URL of the site `publish` pushes a summary to `repo` for, or None and why the summary stays local.

    Only a repository that hosts its Projector site reads the pushed ref, so
    one without a site gets its summary in this checkout alone, for `site serve`
    to preview, and nothing is written to the shared repository. The answer
    is `hosting`'s own, so a hosted site whose Pages answer has no URL still
    counts as hosted, as `site status` counts it.
    """
    try:
        return hosting(repo)
    except SummaryError as error:
        return None, f"could not tell whether {repo} hosts its Projector site: {error}"


def publish_attributes(remote: str, head: str, paths: list[str]) -> dict[str, bool] | None:
    """The head's `linguist-generated` values for `paths`, for `publish` to store, or None when it cannot read them.

    A deploy builds from a checkout of the default branch, which lacks the
    head, so they are read here. A head this checkout lacks is fetched from
    `remote` first, into its objects and FETCH_HEAD alone, as `git fetch
    REMOTE HEAD` would; no branch or tag changes.
    """
    top = checkout_root()
    if top is None or not paths:
        return None
    found = head_attributes(top, head, paths)
    if found is None and FULL_SHA.fullmatch(head):
        fetched = subprocess.run(["git", "-C", str(top), "fetch", "--quiet", "--no-tags", remote, head],
                                 capture_output=True, stdin=subprocess.DEVNULL)
        if fetched.returncode == 0:
            found = head_attributes(top, head, paths)
    if found is None:
        print(f"could not read the .gitattributes of {head[:9]}, which {remote} did not serve; the site will mark "
              "generated files by the .gitattributes of the checkout it builds from", file=sys.stderr)
    return found


def publish(summary_path: Path, remote: str, send_dispatch: bool = True, ref: str = PAGES_REF, root: str = PAGES_ROOT,
            diff_path: Path | None = None, push: bool = True, reason: str = "") -> str | None:
    """Commit the summary and its diff to the summaries ref, and push them unless `push` is false.

    The head's `linguist-generated` values go beside them when this checkout
    has the head or can fetch it, so a deploy classifies files as the head does.
    Without `push`, the commit goes on this checkout's own `ref` and nothing
    leaves it, no dispatch included; `reason` says why, for the report.
    """
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    pr = summary_pr(summary)
    slug = repo_slug(git("remote", "get-url", remote))
    if slug and slug.lower() != pr["repo"].lower():
        raise SummaryError(f"{remote} is {slug}, but the summary is for {pr['repo']}")
    diff = diff_path.read_text(encoding="utf-8") if diff_path else summary_diff(summary)
    paths = [f["path"] for f in prepare_page(summary, diff=diff, at_head=True, generated={})["files"]]
    generated = publish_attributes(remote, str(pr["head"]), paths)
    folder = f"{root}/{pr['number']}/{pr['head']}"
    written = (f"{folder}/summary.json, {DIFF_FILE} and {ATTRIBUTES_FILE}" if generated is not None
               else f"{folder}/summary.json and {DIFF_FILE}")
    message = f"Publish the summary of #{pr['number']} at {pr['head'][:9]}"
    if not push:
        parent = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") if ref_exists(ref) else ""
        tree = summary_tree(parent, folder, summary, diff, generated)
        if parent and tree == git("rev-parse", f"{parent}^{{tree}}"):
            print(f"{ref} in this checkout already has this summary; nothing to publish")
            return None
        commit = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
        git("update-ref", ref, commit, parent)
        print(f"committed {commit[:9]} to {ref} in this checkout: {written}; nothing was "
              f"pushed{f', because {reason}' if reason else ''}; preview it with `project site serve`")
        return commit
    local = tracking_ref(remote, ref)
    tip = fetch_ref(remote, ref)
    seed = None
    for attempt in range(PUSH_ATTEMPTS):
        parent = tip
        if not parent and (ref, root) == (PAGES_REF, PAGES_ROOT):
            if seed is None:
                seed = seed_from_legacy(remote)
            parent = seed
        tree = summary_tree(parent, folder, summary, diff, generated)
        if parent and tree == git("rev-parse", f"{parent}^{{tree}}"):
            print(f"{ref} already has this summary; nothing to publish")
            return None
        commit = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
        try:
            git("push", "--quiet", remote, f"{commit}:{ref}")
            break
        except SummaryError as error:
            if attempt + 1 == PUSH_ATTEMPTS:
                raise
            raced = any(sign in str(error) for sign in PUSH_RACE_SIGNS)
            if raced:
                time.sleep(0.5 * 2 ** attempt + random.uniform(0, 0.25))
            moved = fetch_ref(remote, ref)
            # While the ref has not moved, the push lost no race unless git says
            # so, so a refused login or a dropped connection fails now rather
            # than after every attempt. A failed refetch leaves nothing to
            # build on.
            if (tip and not moved) or (moved == tip and not raced):
                raise
            tip = moved
    seeded = bool(seed) and parent == seed
    git("update-ref", local, commit)
    retried = f" after {attempt} {'retry' if attempt == 1 else 'retries'} because another publish moved the ref" if attempt else ""
    print(f"pushed {commit[:9]} to {remote} {ref}: {written}{retried}")
    if seeded:
        print(f"carried the summaries on {LEGACY_PAGES_REF} over to {ref}; delete {LEGACY_PAGES_REF} from {remote} "
              "once every site reads the new ref")
    if send_dispatch:
        dispatch(pr["repo"])
        print(f"sent {DISPATCH_EVENT} and {LEGACY_DISPATCH_EVENT} to {pr['repo']}; its Projector site workflow "
              "builds and deploys the site")
    return commit


def review_page(site: str, number: int) -> str:
    """The link to pull request `number`'s summary on the site at `site`, which ends in a slash.

    The link itself ends without one. GitHub Pages and `site serve` both
    redirect it to the directory, so the page still loads.
    """
    return f"{site}reviews/{number}"


SUMMARY_MARKER = "projector-summary"
# The link block a publish leaves in the description, as whole lines, CRLF
# included because GitHub stores a description edited in the browser that way.
SUMMARY_BLOCK = re.compile(r"^<!-- " + SUMMARY_MARKER + r" v=1 sha=[0-9a-f]{40} -->\r?\n"
                           r"\S* ?\*\*Projector summary\*\* [^\r\n]*(?:\r?\n|\Z)", re.M)


def summary_link(head: str, page: str) -> str:
    """The marker and link line a summary comment and the description both carry."""
    return f"<!-- {SUMMARY_MARKER} v=1 sha={head} -->\n📽️ **Projector summary** of {head[:7]}: {page}\n"


def describe_summary(repo: str, number: int, head: str, site: str) -> str:
    """Link the summary at the very bottom of the pull request's description.

    A publish removes the link block an earlier one left, wherever it now
    sits, and appends this head's after a blank line, so the description keeps
    one, last. A description that already ends with it is left alone. A
    failed edit, such as on a pull request the account cannot edit, is
    reported rather than raised: the comment already links the summary.
    `site` is the hosted site's URL. Returns what it did.
    """
    if not site:
        return f"not linking the summary from the description: GitHub's Pages answer for {repo} has no URL"
    block = summary_link(head, review_page(site, number))
    try:
        current = json.loads(gh("api", f"repos/{repo}/pulls/{number}", "--jq", '.body // "" | @json'))
        described = SUMMARY_BLOCK.sub("", current).rstrip()
        updated = f"{described}\n\n{block}" if described else block
        if updated == current:
            return f"the description already links the summary of {head[:7]}"
        # The description goes on standard input, so a refusal does not quote it back.
        gh("api", "-X", "PATCH", f"repos/{repo}/pulls/{number}", "--input", "-", input=json.dumps({"body": updated}))
    except SummaryError as exc:
        return f"not linking the summary from the description: {exc}"
    return f"linked the summary of {head[:7]} at the end of the description"


# Timeline events GitHub does not render on the pull request's conversation, so
# they do not bury the summary comment: subscriptions and mentions are
# notification bookkeeping, and a publish that deletes its own older summary
# comment leaves a `comment_deleted` behind.
INVISIBLE_EVENTS = frozenset({"subscribed", "unsubscribed", "mentioned", "comment_deleted"})


def comment_summary(repo: str, number: int, head: str, site: str | None = None) -> str:
    """Link the summary from a comment on the pull request, so a reader on GitHub finds it.

    The pull request keeps one such comment per account, naming the head it
    summarizes, and keeps it the pull request's last message: each publish
    posts the link afresh at the end and then deletes the account's older
    summary comments, so the pull request is never without one. A comment that
    already names this head and is still the last visible item on the pull
    request's timeline is left alone, and only the account's older summary
    comments are deleted. Any later comment, submitted review, or other event
    GitHub shows on the conversation, such as a ready-for-review or a review
    request, buries it; a pending review and the events in `INVISIBLE_EVENTS`
    do not. A repository whose site is not hosted gets no comment. `site` is
    the site's URL when the caller already looked it up. Returns what it did.
    """
    if site is None:
        url, reason = hosting(repo)
        if url is None:
            return f"not linking the summary from a comment: {reason}"
        site = url
    if not site:
        return f"not linking the summary from a comment: GitHub's Pages answer for {repo} has no URL"
    page = review_page(site, number)
    body = summary_link(head, page)
    login = gh("api", "user", "--jq", ".login").strip()
    # Every timeline item, oldest first, as its event, its id, a review's state,
    # and its body only when it is this account's summary comment, whose id is
    # the issue comment's: the last visible item decides whether one is at the end.
    rows = gh("api", "--paginate", f"repos/{repo}/issues/{number}/timeline", "--jq",
              f'.[] | [.event, .id, ((.state // "") | ascii_downcase), '
              f'if .event == "commented" and ((.user.login // "") | ascii_downcase) == "{login.lower()}" '
              f'and ((.body // "") | test("<!-- {SUMMARY_MARKER} v=1 ")) then .body else null end] | @json')
    items = [json.loads(row) for row in rows.splitlines() if row.strip()]
    visible = [item for item in items
               if item[0] not in INVISIBLE_EVENTS and not (item[0] == "reviewed" and item[2] == "pending")]
    comments = [item[1] for item in items if item[3] is not None]

    def delete(stale: list[int]) -> str:
        """Delete the account's summary comments `stale`, and name them."""
        for comment_id in stale:
            gh("api", "-X", "DELETE", f"repos/{repo}/issues/comments/{comment_id}")
        return ", ".join(f"comment {comment_id}" for comment_id in stale)

    if visible and visible[-1][3] is not None and visible[-1][3].strip() == body.strip():
        # A delete that failed on an earlier run can have left an older one behind.
        last = visible[-1][1]
        deleted = delete([comment_id for comment_id in comments if comment_id != last])
        return (f"comment {last} already links the summary of {head[:7]}"
                f"{f', deleting {deleted}' if deleted else ''}: {page}")
    posted = json.loads(gh("api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}") or "{}")
    deleted = delete(comments)
    if deleted:
        return (f"moved the link to the summary of {head[:7]} to a new comment at the end, deleting "
                f"{deleted}: {posted.get('html_url') or page}")
    return f"commented a link to the summary of {head[:7]}: {posted.get('html_url') or page}"


def watched_paths(projects_dir: str | None = None) -> list[str]:
    """The paths a push to the default branch rebuilds the site for: the README
    and docs/, a projects directory outside docs/, and .projector.toml and the
    workflow itself, so merging the workflow deploys the site the first time and
    a new site.prepare takes effect."""
    paths = ["README.md", "'docs/**'"]
    if projects_dir and projects_dir.strip("/") != "docs" and not projects_dir.strip("/").startswith("docs/"):
        paths.append(f"'{projects_dir.strip('/')}/**'")
    return paths + [".projector.toml", WORKFLOW_PATH]


def workflow_text(action_ref: str, branch: str = "main", projects_dir: str | None = None) -> str:
    """The site workflow."""
    return WORKFLOW.format(event=DISPATCH_EVENT, ref=action_ref, branch=branch,
                           paths=", ".join(watched_paths(projects_dir)))


def generated_workflow(text: str, projects_dir: str | None = None) -> bool:
    """Whether `text` is a workflow Projector wrote for this repository, in its
    current shape or an earlier one, with any action ref.

    Only what Projector itself varies is free: the action ref, one branch, its
    dispatch event, and the watched paths it lists for `projects_dir`, with or
    without the two it added later. A path or branch added on the same line is
    a hand edit, and so is any other change.
    """
    current = watched_paths(projects_dir)
    lists = {", ".join(current), ", ".join(current[:-2])}
    slots = {
        "event": ("@@EVENT@@", "(?:" + "|".join(map(re.escape, (DISPATCH_EVENT, LEGACY_DISPATCH_EVENT))) + ")"),
        "ref": ("@@REF@@", r"[^\s]+"),
        "branch": ("@@BRANCH@@", r"[^\s,\]]+"),
        "paths": ("@@PATHS@@", "(?:" + "|".join(map(re.escape, sorted(lists))) + ")"),
    }
    for template in (WORKFLOW, *PREVIOUS_WORKFLOWS):
        pattern = re.escape(template.format(**{name: mark for name, (mark, _) in slots.items()}))
        for mark, allowed in slots.values():
            pattern = pattern.replace(re.escape(mark), allowed)
        if re.fullmatch(pattern, text):
            return True
    return False


def default_branch(remote: str = "origin", root: Path | None = None) -> str:
    """The remote's default branch as the checkout recorded it, or main."""
    within = ["-C", str(root)] if root is not None else []
    try:
        head = git(*within, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    except SummaryError:
        return "main"
    return head.split("/", 1)[1] if "/" in head else head


def init(repo: str, number: int, summary_path: str, diff_path: str | None = None) -> None:
    meta = pr_metadata(repo, number)
    diff = Path(diff_path).read_text(encoding="utf-8") if diff_path else fetch_diff(repo, meta["base"], meta["head"])
    files = parse_diff(diff)
    classify(files, generated_attributes([f["path"] for f in files], meta["head"], repo))
    summary = {
        "version": SUMMARY_VERSION,
        "name": "",
        "pr": meta,
        "overview": {"summary": [], "cards": []},
        "groups": [{"id": "unassigned", "title": "Unassigned", "kicker": "", "intro": [], "checks": [],
                    "files": [{"path": f["path"]} for f in files]}],
    }
    Path(summary_path).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {summary_path}: {meta['repo']}#{meta['number']} at {meta['head'][:9]}, {len(files)} files")
    for f in files:
        flag = f" [{f['kind']}]" if f["kind"] else ""
        print(f"  +{f['adds']:<5} -{f['dels']:<5} {f['path']}{flag}{' (new)' if f['new'] else ''}")
