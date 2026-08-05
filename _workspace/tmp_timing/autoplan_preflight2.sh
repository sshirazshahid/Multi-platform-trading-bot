#!/usr/bin/env bash
set -euo pipefail
eval "$(~/.claude/skills/gstack/bin/gstack-slug 2>/dev/null)" || true
~/.claude/skills/gstack/bin/gstack-question-log '{"skill":"autoplan","question_id":"autoplan-premise-confirm","question_summary":"Confirm improvement-loop premises","category":"approval","door_type":"one-way","options_count":4,"user_choice":"A","recommended":"A","session_id":"autoplan-20260730"}' 2>/dev/null || true
source ~/.claude/skills/gstack/bin/gstack-codex-probe
_TEL=$(~/.claude/skills/gstack/bin/gstack-config get telemetry 2>/dev/null || echo off)
_CODEX_CFG=$(~/.claude/skills/gstack/bin/gstack-config get codex_reviews 2>/dev/null || echo enabled)
echo "TEL=$_TEL CODEX_CFG=$_CODEX_CFG"
if [ "$_CODEX_CFG" = "disabled" ]; then echo CODEX_AVAILABLE=false; exit 0; fi
if ! command -v codex >/dev/null 2>&1; then echo CODEX_AVAILABLE=false; exit 0; fi
if ! _gstack_codex_auth_probe >/dev/null; then echo CODEX_AVAILABLE=false AUTH_FAIL; exit 0; fi
_gstack_codex_version_check || true
echo CODEX_AVAILABLE=true
codex --version 2>/dev/null | head -1
