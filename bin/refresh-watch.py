#!/usr/bin/python3
"""Run generate.py --force when the panel's refresh button fires its notification.

The panel cannot reach anything but Orca's notifications.show, which becomes a
freedesktop desktop notification. See specs/github-tasks-panel.md.
"""
import subprocess
import sys
import threading
from pathlib import Path

GENERATOR = Path(__file__).resolve().parent / "generate.py"
REQUEST_SUFFIX = ": Refreshing GitHub issues"

lock = threading.Lock()
running = False
pending = False


def refresh():
    global running, pending
    while True:
        result = subprocess.run([sys.executable, str(GENERATOR), "--force"], capture_output=True, text=True)
        print((result.stdout or result.stderr).strip().splitlines()[:1], flush=True)
        with lock:
            if not pending:
                running = False
                return
            pending = False


def request():
    global running, pending
    with lock:
        if running:
            pending = True  # coalesce into one follow-up run
            return
        running = True
    threading.Thread(target=refresh, daemon=True).start()


def main():
    monitor = subprocess.Popen(
        ["dbus-monitor", "--session", "interface='org.freedesktop.Notifications',member='Notify'"],
        stdout=subprocess.PIPE,
        text=True,
    )
    strings = None  # string args of the Notify call being read
    for line in monitor.stdout:
        if "member=Notify" in line:
            strings = []
            continue
        if strings is None:
            continue
        stripped = line.strip()
        if stripped.startswith('string "'):
            strings.append(stripped[len('string "'):-1])
            # Notify(app_name, replaces_id, app_icon, summary, ...): summary is the 3rd string.
            if len(strings) == 3:
                summary = strings[2]
                strings = None
                if "github-tasks" in summary and summary.endswith(REQUEST_SUFFIX):
                    print(f"refresh requested: {summary}", flush=True)
                    request()
    sys.exit(monitor.wait())


if __name__ == "__main__":
    main()
