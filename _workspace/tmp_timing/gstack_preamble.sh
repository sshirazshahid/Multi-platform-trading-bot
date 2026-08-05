#!/usr/bin/env bash
set -euo pipefail
cd /d/Downloads/Trading_Bot 2>/dev/null || cd /mnt/d/Downloads/Trading_Bot 2>/dev/null || cd "D:/Downloads/Trading_Bot"
eval "$(~/.claude/skills/gstack/bin/gstack-slug 2>/dev/null)" || true
echo "SLUG=${SLUG:-unknown}"
mkdir -p "${GSTACK_HOME:-$HOME/.gstack}/projects/${SLUG:-unknown}"
echo "BRANCH=$(git branch --show-current 2>/dev/null || echo unknown)"
echo "TEL=$(~/.claude/skills/gstack/bin/gstack-config get telemetry 2>/dev/null || echo off)"
echo "CODEX_CFG=$(~/.claude/skills/gstack/bin/gstack-config get codex_reviews 2>/dev/null || echo enabled)"
echo "CHECKPOINT=$(~/.claude/skills/gstack/bin/gstack-config get checkpoint_mode 2>/dev/null || echo explicit)"
echo "PROACTIVE=$(~/.claude/skills/gstack/bin/gstack-config get proactive 2>/dev/null || echo true)"
echo "TEL_PROMPTED=$([ -f ~/.gstack/.telemetry-prompted ] && echo yes || echo no)"
echo "LAKE_INTRO=$([ -f ~/.gstack/.completeness-intro-seen ] && echo yes || echo no)"
echo "PROACTIVE_PROMPTED=$([ -f ~/.gstack/.proactive-prompted ] && echo yes || echo no)"
echo "HAS_ROUTING=$([ -f CLAUDE.md ] && grep -q '## Skill routing' CLAUDE.md && echo yes || echo no)"
command -v codex >/dev/null && echo CODEX_BIN=yes || echo CODEX_BIN=no
ls -t ~/.gstack/projects/${SLUG:-unknown}/*-design-*.md 2>/dev/null | head -1 || echo "No design doc found"
git log --oneline -15
git diff --stat origin/main...HEAD 2>/dev/null | tail -20
