# GitHub Tasks sidebar panel (prototype)

## Goal

Show the open GitHub issues of the project selected in Orca in Orca's right
sidebar, next to the session, so they can be read and handed to the agent
without switching to the full-page Tasks view.

## Constraints (Orca 1.4.x plugin API v1)

- A plugin panel is a sandboxed iframe with CSP `connect-src 'none'`,
  `img-src data:` and inline scripts only. It cannot fetch, load scripts, open
  links, or talk to the plugin worker.
- From the panel, only three host calls exist: `workspace.readContext`
  (focused worktree's `displayName`, `branch`, terminal handles),
  `terminal.sendText` (into a terminal of the focused worktree) and
  `notifications.show`.
- A dev-mode plugin (Settings → Plugins → developer folder) is watched; when
  its files change, Orca reloads the panel.

So the data has to be baked into `panel.html` by something outside the panel.

## Design

- `plugin/` is the dev plugin folder: `orca-plugin.json`, `panel.template.html`,
  `main.mjs`, and the generated `panel.html` (not committed).
- `bin/generate.py` (system `python3`; Pillow for images):
  1. Runs `orca repo list --json`, `orca worktree list --json` and
     `orca terminal list --json`.
  2. For every distinct `github.com/<owner>/<repo>` remote
     (`gitRemoteIdentity.canonicalKey`), fetches the 50 most recently updated
     open issues (pull requests excluded; up to 3 pages of 100, since the
     endpoint mixes in PRs) with `gh api repos/<owner>/<repo>/issues` and the
     `application/vnd.github.full+json` media type, which returns both the
     Markdown `body` and the rendered `body_html`.
  3. Images: the image references in `body` (`![..](..)` and `<img src>`) are
     paired in order with the `<img src>` URLs in `body_html`. For private
     repos those are short-lived signed URLs that download without auth.
     Each image is downloaded (max 15 MB, 20 s), shrunk to at most 720 px
     wide, re-encoded as WebP and embedded as a `data:` URI (the panel CSP
     only allows `img-src data:`). The reference in the body text is replaced
     by a marker the panel turns into an `<img>`. Thumbnails are cached in
     `~/.cache/orca-github-tasks/` by the URL without its query string, so
     each image is downloaded once; a failed download is retried after 24 h.
     (Loading images only when an issue is opened is not possible: the panel
     has no channel back to the generator.)
  4. Size budget: Orca refuses panel files over 10 MB, so the generator keeps
     `panel.html` under 9.5 MB. Images are added in priority order (repos by
     their most recently active Orca worktree, then issues by last update)
     until the budget is spent; later ones stay as a text marker
     "[image not embedded]". An image that cannot be downloaded or decoded
     stays as "[image unavailable]".
  5. Writes `plugin/panel.html` = template + one inline JSON blob: Orca repos
     (id, name, GitHub slug), issues and errors per slug (stored once even if
     several Orca projects share a slug), images by id, worktrees (repoId,
     displayName, branch), and terminal handles (handle → repoId, agent).
     JSON is escaped so it cannot close the `<script>` tag.
  6. Writes only when content changed (so the panel does not reload for
     nothing), atomically (temp file + rename).
  7. A repo whose fetch fails is kept with an error message; the run does
     not abort.
- Refresh: a systemd user timer runs the generator every 3 minutes. The
  plugin also contributes a command, "GitHub Tasks: Refresh now", whose worker
  runs the generator once.
- Refresh button in the panel. The panel's only outward call that leaves the
  app is `notifications.show`, which Orca turns into a desktop notification
  titled `github-tasks: <title>` (via the freedesktop Notifications D-Bus
  service; Orca also relays it to paired mobile clients). So:
  1. The button calls `notifications.show` with title
     `Refreshing GitHub issues` and shows "Refreshing…" until the panel
     reloads (or "Refresh did not arrive" after 60 s).
  2. `bin/refresh-watch.py` (systemd user service
     `orca-github-tasks-watch.service`) runs `dbus-monitor` on the session bus
     for `org.freedesktop.Notifications.Notify` and, when a summary equals
     `github-tasks: Refreshing GitHub issues`, runs `generate.py --force`.
     Requests arriving while a run is in progress are coalesced into one
     follow-up run.
  3. `--force` writes `panel.html` even when the data is unchanged, so the
     panel always reloads and the footer resets.
  The plugin therefore also requests the `notifications:show` capability.
- Panel behaviour:
  1. Polls `workspace.readContext` every 2 s.
  2. Picks the repo: first by any terminal handle that the snapshot maps to a
     repo; otherwise by `displayName` + `branch` (with `refs/heads/` stripped)
     when exactly one repo matches; otherwise shows a repo picker listing the
     candidates (or all GitHub repos when none match).
  3. Lists open issues: number, title, labels, assignee, age. A text filter
     narrows by number, title or label. Styling, with a palette for Orca's
     light and dark modes (the panel `<html>` has class `light` or `dark`):
     rows alternate background; each row has a left stripe and issue number
     coloured by kind (bug red, feature/enhancement blue, docs/question amber,
     anything else violet; from labels, else a `[Bug]`/`[Feature]` title
     prefix); labels use their GitHub colours; age is green under a day,
     amber under a week, muted after.
  4. Clicking an issue expands its body as plain text (no Markdown rendering,
     no HTML, so issue content cannot run script in the panel, which can type
     into terminals) with its images shown inline at panel width, and the URL
     as selectable text. Clicking an image toggles it between panel width and
     full size (scrolls horizontally).
  5. Links: the panel iframe is `sandbox="allow-scripts"` and Orca's shell
     cancels every link click, so nothing in the panel can open a browser.
     Each issue row, and the repo heading (its issues page), gets a "copy
     link" button instead. It tries `navigator.clipboard.writeText`, then
     `document.execCommand('copy')`; if both fail it shows the URL selected
     with "press Ctrl+C".
  6. "Send to terminal" types the single line
     `Work on GitHub issue #<n> in <owner>/<repo>: <title> (<url>)` into the
     chosen terminal of the focused worktree (one line, because a newline would
     submit an agent prompt early). Agent terminals are listed first.
     Enter is not pressed unless "Submit" is ticked.
  7. Shows when the data last changed, and an error line per repo when the
     fetch failed.

## Out of scope

Creating, editing, closing or commenting on issues; PRs; Linear/Jira/GitLab;
opening links in a browser (the panel cannot); a published, installable
plugin.

## Done when

- With the plugin folder added as a dev plugin and the timer running,
  selecting a GitHub-backed project shows its open issues in the right
  sidebar, and switching projects switches the list within a few seconds.
- "Send to terminal" types the issue reference into the chosen terminal.
- Every issue row and the repo heading have a copy-link button.
- The panel's refresh button reloads the panel with fresh data within a few
  seconds.
- A project without a GitHub remote shows "No GitHub remote for this project".
- Screenshots in issue bodies, including private repos, show inline.
- `plugin/panel.html` stays under 10 MB.
- `bin/generate.py --check` exits 0 and prints the repo/issue counts.
