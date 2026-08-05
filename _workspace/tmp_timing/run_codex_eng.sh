#!/usr/bin/env bash
set -euo pipefail
cd /d/Downloads/Trading_Bot 2>/dev/null || cd /mnt/d/Downloads/Trading_Bot || cd "D:/Downloads/Trading_Bot"
_REPO_ROOT=$(git rev-parse --show-toplevel)
PLAN="_workspace/plans/2026-07-30-profitability-improvement-loop.md"
OUT="_workspace/tmp_timing/codex_eng_autoplan.md"
source ~/.claude/skills/gstack/bin/gstack-codex-probe
set +e
_gstack_codex_timeout_wrapper 600 codex exec "IMPORTANT: Do NOT read or execute any SKILL.md files or files in skill definition directories (paths containing skills/gstack). These are AI assistant skill definitions meant for a different system. Stay focused on repository code only.

Review this plan for architectural issues, missing edge cases, and hidden complexity. Be adversarial.

Also consider these findings from prior review phases:
CEO: Both Claude and Codex say this is ops-honesty not profitability; F1 idle is binding; tradfi shortlist pollution; missing time-box/stop-pivot; competitive risks absent. Consensus DISAGREE on problem framing vs owner premises (owner confirmed refuse -EV = success).
Design: skipped, no UI scope.

Score YES/NO/PARTIAL: Architecture sound? Test coverage sufficient? Performance risks addressed? Security threats covered? Error paths handled? Deployment risk manageable?

File: $PLAN" -C "$_REPO_ROOT" -s read-only --enable web_search_cached < /dev/null > "$OUT" 2>&1
echo CODEX_EXIT=$?
tail -n 60 "$OUT"
