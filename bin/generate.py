#!/usr/bin/python3
"""Bake a snapshot of Orca projects + their open GitHub issues into plugin/panel.html.

The panel is sandboxed with connect-src 'none', so this is the only way data
reaches it. See specs/github-tasks-panel.md.
"""
import base64
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "plugin" / "panel.template.html"
OUTPUT = ROOT / "plugin" / "panel.html"
PLACEHOLDER = "/*__GITHUB_TASKS_DATA__*/null"
ISSUE_LIMIT = 50
ISSUE_PAGES = 3
BODY_LIMIT = 4000
THUMB_WIDTH = 720
IMAGE_MAX_BYTES = 15 * 1024 * 1024
FAILED_RETRY_SECONDS = 24 * 3600
# Orca refuses panel entries over 10 MB.
PANEL_BUDGET_BYTES = int(9.5 * 1024 * 1024)
MARKER = "\ue000"  # private-use char around image ids in bodies
BODY_IMG = re.compile(r"!\[[^\]]*\]\([^)\s]+(?:\s+\"[^\"]*\")?\)|<img\b[^>]*>", re.I)
HTML_IMG = re.compile(r"<img\b[^>]*?\bsrc=\"([^\"]+)\"", re.I)
HOME = Path.home()
CACHE = HOME / ".cache/orca-github-tasks"
# Not plain "orca": /usr/bin/orca is the GNOME screen reader.
ORCA_CLI = Path(os.environ.get("ORCA_CLI_BIN_DIR", HOME / ".config/orca/linux-orca-cli-shim")) / "orca"

# The plugin worker gets a scrubbed env, so find the tools and the session bus
# (gh reads its token from the keyring over D-Bus) ourselves.
os.environ["PATH"] = os.pathsep.join(
    [
        os.environ.get("PATH", ""),
        str(HOME / ".local/share/mise/shims"),
        str(HOME / ".local/bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]
)
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


def run_json(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "failed")
    return json.loads(result.stdout)


def orca(*args):
    data = run_json([str(ORCA_CLI), *args, "--json"])
    if not data.get("ok"):
        raise RuntimeError(f"orca {' '.join(args)}: {data}")
    return data["result"]


def short_branch(branch):
    return (branch or "").removeprefix("refs/heads/")


def github_slug(repo):
    key = ((repo.get("gitRemoteIdentity") or {}).get("canonicalKey") or "").lower()
    if not key.startswith("github.com/"):
        return None
    # Keep the original casing for display.
    return repo["gitRemoteIdentity"]["canonicalKey"][len("github.com/"):]


def fetch_issues(slug):
    """Open issues (not PRs) with Markdown body plus rendered body_html."""
    # The issues endpoint also returns PRs, so page until there are enough real issues.
    issues = []
    try:
        for page in range(1, ISSUE_PAGES + 1):
            batch = run_json(
                [
                    shutil.which("gh") or "gh", "api", "-X", "GET", f"repos/{slug}/issues",
                    "-H", "Accept: application/vnd.github.full+json",
                    "-f", "state=open", "-f", "sort=updated", "-f", "per_page=100", "-f", f"page={page}",
                ]
            )
            issues += batch
            if len(batch) < 100 or sum(1 for i in issues if not i.get("pull_request")) >= ISSUE_LIMIT:
                break
    except Exception as error:  # noqa: BLE001 - report per repo, keep going
        return None, str(error)
    out = []
    for i in issues:
        if i.get("pull_request"):
            continue
        body = (i.get("body") or "")[:BODY_LIMIT]
        out.append(
            {
                "number": i["number"],
                "title": i["title"],
                "labels": [{"name": label["name"], "color": label.get("color") or "888888"} for label in i.get("labels") or []],
                "assignees": [a["login"] for a in i.get("assignees") or []],
                "author": (i.get("user") or {}).get("login", ""),
                "updatedAt": i.get("updated_at", ""),
                "url": i["html_url"],
                "body": body,
                # Dropped before writing; only used to download images.
                "_imageUrls": [html.unescape(u) for u in HTML_IMG.findall(i.get("body_html") or "")],
            }
        )
    return out[:ISSUE_LIMIT], None


def image_cache_path(url):
    key = urllib.parse.urlsplit(url)._replace(query="", fragment="").geturl()
    return CACHE / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".webp")


def thumbnail(url):
    """WebP bytes at most THUMB_WIDTH wide, cached on disk; None if unavailable."""
    path = image_cache_path(url)
    if path.exists():
        return path.read_bytes()
    failed = path.with_suffix(".failed")
    if failed.exists() and time.time() - failed.stat().st_mtime < FAILED_RETRY_SECONDS:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "orca-github-tasks"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(IMAGE_MAX_BYTES + 1)
        if len(raw) > IMAGE_MAX_BYTES:
            raise ValueError("image too large")
        image = Image.open(io.BytesIO(raw))
        image.seek(0)  # first frame of GIFs
        image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
        if image.width > THUMB_WIDTH:
            image = image.resize((THUMB_WIDTH, round(image.height * THUMB_WIDTH / image.width)), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "WEBP", quality=75)
    except Exception:  # noqa: BLE001 - a broken image must not break the run
        CACHE.mkdir(parents=True, exist_ok=True)
        failed.touch()
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def attach_images(issues_by_slug, slug_priority):
    """Swap image references in bodies for markers; return image id -> data URI or None.

    Markdown refs and rendered <img> tags appear in the same order, so they pair by index.
    """
    wanted = []  # (image id, url) in priority order
    ordered = sorted(issues_by_slug, key=lambda slug: slug_priority.get(slug, 0), reverse=True)
    for slug in ordered:
        issues = issues_by_slug[slug]["issues"] or []
        for issue in sorted(issues, key=lambda i: i["updatedAt"], reverse=True):
            urls = issue.pop("_imageUrls")
            refs = iter(range(len(urls)))

            def to_marker(match):
                index = next(refs, None)
                if index is None:
                    return match.group(0)
                image_id = image_cache_path(urls[index]).stem
                wanted.append((image_id, urls[index]))
                return f"{MARKER}{image_id}{MARKER}"

            issue["body"] = BODY_IMG.sub(to_marker, issue["body"])
        for issue in issues:
            issue.pop("_imageUrls", None)

    unique = list(dict(wanted).items())
    with ThreadPoolExecutor(max_workers=6) as pool:
        thumbs = list(pool.map(lambda item: thumbnail(item[1]), unique))
    return [(image_id, data) for (image_id, _), data in zip(unique, thumbs)]


def build_snapshot():
    repos = orca("repo", "list")["repos"]
    worktrees = orca("worktree", "list")["worktrees"]
    try:
        terminals = orca("terminal", "list")
        terminals = terminals.get("terminals", terminals)
    except Exception:  # noqa: BLE001 - terminal mapping is only a hint
        terminals = []

    slugs = {r["id"]: github_slug(r) for r in repos}
    unique_slugs = sorted({s for s in slugs.values() if s})
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict(zip(unique_slugs, pool.map(fetch_issues, unique_slugs)))
    issues_by_slug = {slug: {"issues": issues, "error": error} for slug, (issues, error) in fetched.items()}

    # Images go to the projects used most recently first, in case the budget runs out.
    slug_priority = {}
    for w in worktrees:
        slug = slugs.get(w["repoId"])
        if slug:
            slug_priority[slug] = max(slug_priority.get(slug, 0), w.get("lastActivityAt") or 0)
    thumbs = attach_images(issues_by_slug, slug_priority)

    worktree_repo = {w["id"]: w["repoId"] for w in worktrees}
    snapshot = {
        "generatedAt": int(time.time() * 1000),
        "repos": [{"id": r["id"], "name": r.get("displayName", ""), "slug": slugs[r["id"]]} for r in repos],
        "issuesBySlug": issues_by_slug,
        "images": {},
        "worktrees": [
            {"repoId": w["repoId"], "displayName": w.get("displayName", ""), "branch": short_branch(w.get("branch"))}
            for w in worktrees
            if not w.get("isArchived")
        ],
        "terminals": {
            t["handle"]: {
                "repoId": worktree_repo.get(t.get("worktreeId")),
                "agent": t.get("agentIdentity") or None,
            }
            for t in terminals
            if t.get("handle")
        },
    }

    # Fill the size budget in priority order. Absent id = not embedded; None = unavailable.
    used = len(render(snapshot).encode())
    for image_id, data in thumbs:
        if data is None:
            snapshot["images"][image_id] = None
            continue
        uri = "data:image/webp;base64," + base64.b64encode(data).decode()
        cost = len(uri) + len(image_id) + 8
        if used + cost <= PANEL_BUDGET_BYTES:
            snapshot["images"][image_id] = uri
            used += cost
    return snapshot


def render(snapshot):
    # Escape "<" so issue text can never close the inline <script>.
    blob = json.dumps(snapshot, ensure_ascii=False).replace("<", "\\u003c")
    blob = blob.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = TEMPLATE.read_text()
    if PLACEHOLDER not in template:
        sys.exit(f"placeholder missing from {TEMPLATE}")
    return template.replace(PLACEHOLDER, blob)


def same_content(old, new):
    # generatedAt changes every run; ignore it so the panel only reloads on real changes.
    pattern = r'"generatedAt": \d+'
    return re.sub(pattern, "", old) == re.sub(pattern, "", new)


def main():
    snapshot = build_snapshot()
    html = render(snapshot)
    old = OUTPUT.read_text() if OUTPUT.exists() else ""
    changed = not same_content(old, html)
    if changed:
        tmp = OUTPUT.with_name(".panel.html.tmp")
        tmp.write_text(html)
        os.replace(tmp, OUTPUT)

    github = snapshot["issuesBySlug"]
    issues = sum(len(entry["issues"] or []) for entry in github.values())
    errors = [f"{slug}: {entry['error']}" for slug, entry in github.items() if entry["error"]]
    images = snapshot["images"]
    embedded = sum(1 for uri in images.values() if uri)
    print(
        f"{len(github)} GitHub repos, {issues} open issues, {embedded} images embedded, "
        f"{sum(1 for uri in images.values() if uri is None)} unavailable, "
        f"{len(html.encode()) / 1e6:.1f} MB, {'written' if changed else 'unchanged'}"
    )
    for line in errors:
        print(f"error {line}")
    if "--check" in sys.argv and len(errors) == len(github) and github:
        sys.exit(1)


if __name__ == "__main__":
    main()
