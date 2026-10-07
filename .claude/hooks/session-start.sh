#!/usr/bin/env bash
# SessionStart hook for Claude Code on the web.
# Remote containers start from a fresh image, so user-level skills are not
# present. Re-install the Open Design skill globally on every web session.
# Local sessions are untouched.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

"$CLAUDE_PROJECT_DIR/scripts/install-open-design-skill.sh" || {
  echo "[open-design] install failed; session continues without the skill" >&2
  exit 0
}
