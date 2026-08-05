#!/usr/bin/env bash
set -euo pipefail
cd /d/Downloads/Trading_Bot 2>/dev/null || cd /mnt/d/Downloads/Trading_Bot || cd "D:/Downloads/Trading_Bot"
_REPO_ROOT=$(git rev-parse --show-toplevel)
PLAN="_workspace/plans/2026-07-30-profitability-improvement-loop.md"
OUT="_workspace/tmp_timing/codex_ceo_c2.md"
source ~/.claude/skills/gstack/bin/gstack-codex-probe
set +e
_gstack_codex_timeout_wrapper 600 codex exec "IMPORTANT: Do NOT read or execute any SKILL.md files or files in skill definition directories (paths containing skills/gstack). Stay focused on repository code only.

CEO review of REVISED plan (owner accepted 6-week stop/pivot + probe-first Track D). Be adversarial. Score the 6 CEO dimensions YES/NO/PARTIAL. File: $PLAN" -C "$_REPO_ROOT" -s read-only --enable web_search_cached < /dev/null > "$OUT" 2>&1
echo EXIT=$?
# extract final verdict-ish
tail -n 70 "$OUT"
