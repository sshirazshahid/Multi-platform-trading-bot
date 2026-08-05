#!/usr/bin/env bash
set -euo pipefail
cd /d/Downloads/Trading_Bot 2>/dev/null || cd /mnt/d/Downloads/Trading_Bot || cd "D:/Downloads/Trading_Bot"
eval "$(~/.claude/skills/gstack/bin/gstack-slug 2>/dev/null)" || true
SLUG=${SLUG:-sshirazshahid-Multi-platform-trading-bot}
mkdir -p "$HOME/.gstack/projects/$SLUG"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | tr '/' '-')
DATETIME=$(date +%Y%m%d-%H%M%S)
COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
PLAN="_workspace/plans/2026-07-30-profitability-improvement-loop.md"
RESTORE="$HOME/.gstack/projects/$SLUG/${BRANCH}-autoplan-restore-${DATETIME}.md"
{
  echo "# /autoplan Restore Point"
  echo "Captured: $(date -u +%Y-%m-%dT%H:%M:%SZ) | Branch: $(git branch --show-current) | Commit: $COMMIT"
  echo
  echo "## Re-run Instructions"
  echo "1. Copy \"Original Plan State\" below back to your plan file"
  echo "2. Invoke /autoplan"
  echo
  echo "## Original Plan State"
  cat "$PLAN"
} > "$RESTORE"
echo "RESTORE_PATH=$RESTORE"
# prepend restore comment if missing
if ! grep -q '/autoplan restore point' "$PLAN"; then
  tmp=$(mktemp)
  echo "<!-- /autoplan restore point: $RESTORE -->" > "$tmp"
  cat "$PLAN" >> "$tmp"
  mv "$tmp" "$PLAN"
fi
touch ~/.gstack/.telemetry-prompted
# leave telemetry off as currently configured; mark prompted so we don't block ops
~/.claude/skills/gstack/bin/gstack-config set routing_declined true 2>/dev/null || true
touch ~/.gstack/.proactive-prompted
echo DONE
