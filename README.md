# Orca GitHub Tasks panel (prototype)

Shows the selected Orca project's open GitHub issues in the right sidebar.
Design and limits: `specs/github-tasks-panel.md`.

## Setup

Needs `gh` (logged in) and Pillow for the system `python3` (`python3-pillow` on Fedora).

1. Orca → Settings → Plugins → Development → Add path:
   the `plugin` folder of this checkout
2. Review and enable "GitHub Tasks" (it asks for workspace read, terminal send and notifications).
3. Click the bug icon in the right sidebar.

`systemd/orca-github-tasks.timer` regenerates `plugin/panel.html` every
3 minutes. Install the units with `bin/install-units.sh` (it fills in this
checkout's path; rerun it after moving the checkout or changing a unit), then
`systemctl --user enable --now orca-github-tasks.timer`. Run
`bin/generate.py` by hand, or "GitHub Tasks: Refresh now" from Orca's command
palette, to refresh immediately.

The ⟳ button in the panel needs `systemd/orca-github-tasks-watch.service`
running (`systemctl --user enable --now orca-github-tasks-watch.service`). It shows a desktop
notification, which the watcher turns into a refresh.

## Remove

Remove the dev path in Orca, then
`systemctl --user disable --now orca-github-tasks.timer` and
`systemctl --user disable orca-github-tasks.service`, and
`systemctl --user disable --now orca-github-tasks-watch.service`.
