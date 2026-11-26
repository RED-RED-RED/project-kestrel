#!/usr/bin/env bash
# resurvey.sh - clear a KESTREL node's saved GPS survey and re-run survey-in.
#
# Run this ON the node (needs root). It deletes the saved fixed-position
# survey and restarts the node so the GPS re-runs survey-in from scratch.
# The node is OFFLINE for the survey-in window (several minutes) while it
# re-establishes its fixed position, so run it during a maintenance window.
#
# Usage:
#   sudo ./resurvey.sh        # prompts for confirmation
#   sudo ./resurvey.sh -y     # skip the prompt (scripted use)

set -euo pipefail

SURVEY="/var/lib/kestrel-node/survey.json"
SERVICE="kestrel-node"
ASSUME_YES=0
[[ "${1:-}" == "-y" || "${1:-}" == "--yes" ]] && ASSUME_YES=1

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0 $*" >&2
  exit 1
fi

echo "This clears the saved survey ($SURVEY) and restarts $SERVICE."
echo "The node will be OFFLINE for the survey-in window (a few minutes) while"
echo "it re-establishes its fixed position."
if [[ $ASSUME_YES -eq 0 ]]; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

if [[ -f "$SURVEY" ]]; then
  rm -f "$SURVEY"
  echo "Removed $SURVEY"
else
  echo "No survey file at $SURVEY (already clear)."
fi

systemctl restart "$SERVICE"
echo "Restarted $SERVICE. Following the log (Ctrl-C to stop)..."
echo
exec journalctl -u "$SERVICE" -f --since "10 seconds ago"
