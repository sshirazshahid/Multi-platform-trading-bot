#!/usr/bin/env bash
set -euo pipefail
cd /d/Downloads/Trading_Bot 2>/dev/null || cd /mnt/d/Downloads/Trading_Bot || cd "D:/Downloads/Trading_Bot"
COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
~/.claude/skills/gstack/bin/gstack-question-log '{"skill":"autoplan","question_id":"autoplan-final-gate-c2","question_summary":"Approve revised viability plan","category":"approval","door_type":"one-way","options_count":5,"user_choice":"A","recommended":"A","session_id":"autoplan-20260730-c2"}' 2>/dev/null || true
~/.claude/skills/gstack/bin/gstack-review-log '{"skill":"plan-ceo-review","timestamp":"'"$TIMESTAMP"'","status":"clean","unresolved":0,"critical_gaps":0,"mode":"SELECTIVE_EXPANSION","via":"autoplan","commit":"'"$COMMIT"'"}'
~/.claude/skills/gstack/bin/gstack-review-log '{"skill":"plan-eng-review","timestamp":"'"$TIMESTAMP"'","status":"issues_open","unresolved":3,"critical_gaps":2,"issues_found":8,"mode":"FULL_REVIEW","via":"autoplan","commit":"'"$COMMIT"'"}'
~/.claude/skills/gstack/bin/gstack-review-log '{"skill":"plan-devex-review","timestamp":"'"$TIMESTAMP"'","status":"issues_open","initial_score":5,"overall_score":6,"product_type":"ops-cli","tthw_current":"12min","tthw_target":"5min","unresolved":2,"via":"autoplan","commit":"'"$COMMIT"'"}'
~/.claude/skills/gstack/bin/gstack-review-log '{"skill":"autoplan-voices","timestamp":"'"$TIMESTAMP"'","status":"clean","source":"codex+subagent","phase":"ceo","via":"autoplan","consensus_confirmed":3,"consensus_disagree":1,"commit":"'"$COMMIT"'"}'
~/.claude/skills/gstack/bin/gstack-review-log '{"skill":"autoplan-voices","timestamp":"'"$TIMESTAMP"'","status":"issues_open","source":"codex+subagent","phase":"eng","via":"autoplan","consensus_confirmed":2,"consensus_disagree":1,"commit":"'"$COMMIT"'"}'
~/.claude/skills/gstack/bin/gstack-decision-log '{"decision":"approve-deploy-readiness-edge-viability-loop-rev2","rationale":"Owner approved autoplan A: 6w clock, Week-2 early exit, Track D unblock first, Track A measurement trust, default PIVOT=preservation","branch":"'"$(git branch --show-current)"'"}' 2>/dev/null || true
_TEL_END=$(date +%s)
echo '{"skill":"autoplan","event":"completed","branch":"'"$(git branch --show-current)"'","outcome":"success","session":"autoplan-20260730-c2"}' >> ~/.gstack/analytics/skill-usage.jsonl 2>/dev/null || true
~/.claude/skills/gstack/bin/gstack-timeline-log '{"skill":"autoplan","event":"completed","branch":"'"$(git branch --show-current)"'","outcome":"success","session":"autoplan-20260730-c2"}' 2>/dev/null || true
echo "LOGS_OK commit=$COMMIT ts=$TIMESTAMP"
