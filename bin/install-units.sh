#!/bin/sh
# Install the systemd user units with @REPO_DIR@ replaced by this checkout's path.
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest"
for unit in "$repo"/systemd/*; do
    name=$(basename "$unit")
    rm -f "$dest/$name"
    sed "s#@REPO_DIR@#$repo#g" "$unit" > "$dest/$name"
done
systemctl --user daemon-reload
