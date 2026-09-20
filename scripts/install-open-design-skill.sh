#!/usr/bin/env bash
# Install the Open Design skill for Claude Code at user (global) scope.
#
# What this does (idempotent, non-interactive):
#   1. Installs/updates the SKILL.md wrapper from sugarforever/open-design-skill
#      into ~/.claude/skills/open-design (the user-level Claude Code skills dir).
#   2. Clones/updates the Open Design catalogue (nexu-io/open-design) that the
#      wrapper reads, into $OPEN_DESIGN_ROOT (default ~/.open-design-skill/repo).
#      Uses a sparse checkout of the four directories the skill needs, which
#      keeps it at ~130MB instead of the full monorepo.
#   3. Smoke-tests the skill's list scripts against the catalogue.
#
# Requirements: git, node >= 16, network access to github.com.
#
# Usage:
#   scripts/install-open-design-skill.sh            # install or update
#   scripts/install-open-design-skill.sh --full     # full (non-sparse) catalogue
#   OPEN_DESIGN_ROOT=/path scripts/install-open-design-skill.sh
set -euo pipefail

SKILL_REPO="https://github.com/sugarforever/open-design-skill.git"
CONTENT_REPO="https://github.com/nexu-io/open-design.git"
SKILL_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/open-design"
ROOT="${OPEN_DESIGN_ROOT:-$HOME/.open-design-skill/repo}"
SPARSE_DIRS=(design-systems design-templates skills craft)
FULL=0

for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

for bin in git node; do
  command -v "$bin" >/dev/null 2>&1 || { echo "error: '$bin' not found on PATH" >&2; exit 1; }
done

log() { printf '[open-design] %s\n' "$*"; }

# --- 1. skill wrapper -------------------------------------------------------
if [ -d "$SKILL_DIR/.git" ]; then
  log "updating skill at $SKILL_DIR"
  git -C "$SKILL_DIR" pull --ff-only --quiet || log "warn: skill update failed, keeping existing copy"
elif [ -f "$SKILL_DIR/SKILL.md" ]; then
  log "skill already present at $SKILL_DIR (not a git checkout, leaving as is)"
else
  log "installing skill to $SKILL_DIR"
  mkdir -p "$(dirname "$SKILL_DIR")"
  git clone --depth 1 --quiet "$SKILL_REPO" "$SKILL_DIR"
fi

# --- 2. catalogue -----------------------------------------------------------
if [ -d "$ROOT/.git" ]; then
  log "updating catalogue at $ROOT"
  git -C "$ROOT" pull --ff-only --quiet || log "warn: catalogue update failed, keeping existing copy"
elif [ -d "$ROOT" ] && [ -n "$(ls -A "$ROOT" 2>/dev/null)" ]; then
  log "catalogue dir $ROOT exists and is non-empty but not a git checkout; leaving as is"
else
  mkdir -p "$(dirname "$ROOT")"
  if [ "$FULL" = 1 ]; then
    log "cloning full catalogue to $ROOT"
    git clone --depth 1 --quiet "$CONTENT_REPO" "$ROOT"
  else
    log "cloning sparse catalogue (${SPARSE_DIRS[*]}) to $ROOT"
    git clone --depth 1 --filter=blob:none --sparse --quiet "$CONTENT_REPO" "$ROOT"
    git -C "$ROOT" sparse-checkout set "${SPARSE_DIRS[@]}"
  fi
fi

# --- 3. smoke test ----------------------------------------------------------
for d in "${SPARSE_DIRS[@]}"; do
  [ -d "$ROOT/$d" ] || { echo "error: expected $ROOT/$d to exist" >&2; exit 1; }
done

export OPEN_DESIGN_ROOT="$ROOT"
ds=$(node "$SKILL_DIR/scripts/list-design-systems.mjs" | tail -n +2 | wc -l | tr -d ' ')
tp=$(node "$SKILL_DIR/scripts/list-design-templates.mjs" | tail -n +2 | wc -l | tr -d ' ')
sk=$(node "$SKILL_DIR/scripts/list-skills.mjs" | tail -n +2 | wc -l | tr -d ' ')
cr=$(node "$SKILL_DIR/scripts/list-craft.mjs" | tail -n +2 | wc -l | tr -d ' ')
log "ok: skill=$SKILL_DIR root=$ROOT design-systems=$ds templates=$tp skills=$sk craft=$cr"

# Persist OPEN_DESIGN_ROOT for the current Claude Code session when run as a hook.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export OPEN_DESIGN_ROOT=\"$ROOT\"" >> "$CLAUDE_ENV_FILE"
fi
