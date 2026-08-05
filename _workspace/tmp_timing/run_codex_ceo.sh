#!/usr/bin/env bash
set -euo pipefail
cd /d/Downloads/Trading_Bot 2>/dev/null || cd /mnt/d/Downloads/Trading_Bot || cd "D:/Downloads/Trading_Bot"
_REPO_ROOT=$(git rev-parse --show-toplevel)
PLAN="_workspace/plans/2026-07-30-profitability-improvement-loop.md"
OUT="_workspace/tmp_timing/codex_ceo_autoplan.md"
source ~/.claude/skills/gstack/bin/gstack-codex-probe
# 10 min wrapper
set +e
_gstack_codex_timeout_wrapper 600 codex exec "IMPORTANT: Do NOT read or execute any SKILL.md files or files in skill definition directories (paths containing skills/gstack). These are AI assistant skill definitions meant for a different system. Stay focused on repository code only.

You are a CEO/founder advisor reviewing a development plan.
Challenge the strategic foundations: Are the premises valid or assumed? Is this the
right problem to solve, or is there a reframing that would be 10x more impactful?
What alternatives were dismissed too quickly? What competitive or market risks are
unaddressed? What scope decisions will look foolish in 6 months? Be adversarial.
No compliments. Just the strategic blind spots.

Also score YES/NO/PARTIAL with one line each:
1. Premises valid?
2. Right problem to solve?
3. Scope calibration correct?
4. Alternatives sufficiently explored?
5. Competitive/market risks covered?
6. 6-month trajectory sound?

File: $PLAN" -C "$_REPO_ROOT" -s read-only --enable web_search_cached < /dev/null > "$OUT" 2>&1
_CODEX_EXIT=$?
set -e
echo "CODEX_EXIT=$_CODEX_EXIT"
if [ "$_CODEX_EXIT" = "124" ]; then
  echo "[codex stalled]"
  _gstack_codex_log_event "codex_timeout" "600" || true
fi
wc -l "$OUT"
tail -n 80 "$OUT"
