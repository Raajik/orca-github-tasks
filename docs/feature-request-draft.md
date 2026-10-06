Title: [Feature]: Show the selected project's GitHub Tasks in the right sidebar

### Problem or use case

The Tasks view already lists the selected project's GitHub issues, but it is a full page that replaces the workspace. To act on an issue I open Tasks, read the issue, switch back to the session, and repeat whenever I need to re-read it. Our design issues lean heavily on screenshots, so I end up flipping between the two views constantly while an agent works on the fix.

The right sidebar is where workspace context already lives (Explorer, Source Control, Checks, Ports, Workspaces), and it stays visible next to the terminal. The open issues for the current project would fit there.

Related: #12693 asks for the *linked* Linear issue in the right sidebar. This request is for the project's open GitHub issue list (the Tasks data), and #21046 (Tasks page state lost when switching views) is a symptom of the same back-and-forth.

### Proposed solution

Add a "Tasks" tab to the right sidebar that shows the Tasks list scoped to the selected project's repo:

- open issues for the focused worktree's repo, following project switches
- the same filter/search as the Tasks page
- expanding an issue shows its body with images inline
- an action to hand the issue to the workspace's agent (as the Tasks page's "Use" does), plus "Open in Tasks" for the full view

### Alternatives or additional context

I prototyped this as a dev plugin to check it is useful: https://github.com/Raajik/orca-github-tasks (a right-sidebar panel listing the selected project's open issues with screenshots inline, and a button that types the issue reference into the agent's terminal). It works, but only through workarounds, because plugin API v1 gives a panel no way to get data:

- the panel CSP is `connect-src 'none'; img-src data:` and the panel can only call `workspace.readContext`, `terminal.sendText` and `notifications.show`
- there is no panel ↔ worker channel (#15638)
- the panel iframe is `sandbox="allow-scripts"` and the shell cancels link clicks, so a panel cannot open an issue in the browser (the prototype offers copy-link buttons instead)
- `workspace.readContext` returns `displayName` and `branch` but no repo identity, so a panel cannot tell which repo is focused when several projects share a name like `main`

So the prototype runs an external script on a timer that calls `gh` and bakes the issues and images (as `data:` URIs) into `panel.html`, which the dev-plugin watcher then reloads. That costs a full panel reload on every change, a 10 MB `panel.html` cap, and images fetched ahead of time because the panel cannot request one when an issue is opened.

If a built-in tab is not planned, either of these would make the plugin route viable:

1. a panel ↔ worker message channel (#15638), plus a scoped network capability (`net:fetch` hosts, already mentioned as a later phase in `plugin-capabilities.ts`)
2. a read-only host method exposing the Tasks data Orca already fetches for the focused project (e.g. `tasks.list` / `tasks.get`), and the focused repo's identity (`gitRemoteIdentity.canonicalKey`) in `workspace.readContext`
