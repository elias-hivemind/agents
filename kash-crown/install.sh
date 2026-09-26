#!/usr/bin/env bash
# Install KC VENDETTA BOUNCE globally for Claude Code (macOS / Linux / Git-Bash).
#
#   ./kash-crown/install.sh                      # skill + python deps
#   ./kash-crown/install.sh "/path/to/Vault"     # + copy the note into Obsidian
#   OBSIDIAN_VAULT="/path/to/Vault" ./kash-crown/install.sh
#
# Installs to ~/.claude/skills/kc-vendetta-bounce (override with CLAUDE_SKILLS_DIR).
# A previous install is moved to ~/.claude/skill-backups/ (outside the skills dir so it never loads twice).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../.claude/skills/kc-vendetta-bounce"
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$SKILLS_DIR/kc-vendetta-bounce"
BACKUPS="$HOME/.claude/skill-backups"
VAULT="${1:-${OBSIDIAN_VAULT:-}}"

[[ -f "$SRC/SKILL.md" ]] || { echo "ERROR: skill source not found at $SRC" >&2; exit 1; }

if [[ -d "$DEST" ]]; then
  mkdir -p "$BACKUPS"
  bak="$BACKUPS/kc-vendetta-bounce-$(date +%Y%m%d-%H%M%S)"
  mv "$DEST" "$bak"
  echo "Previous install backed up -> $bak"
fi
mkdir -p "$SKILLS_DIR"
cp -R "$SRC" "$DEST"
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "Skill installed -> $DEST"

PY="$(command -v python3 || command -v python || true)"
if [[ -z "$PY" ]]; then
  echo "WARN: Python 3 not found. Install Python 3.9+, then: python3 -m pip install numpy pillow imageio-ffmpeg" >&2
else
  if ! "$PY" -c "import numpy, PIL, imageio_ffmpeg" 2>/dev/null; then
    "$PY" -m pip install --user --quiet numpy pillow imageio-ffmpeg \
      || echo "WARN: pip install failed (managed Python?). Run manually: $PY -m pip install numpy pillow imageio-ffmpeg" >&2
  fi
  "$PY" "$DEST/scripts/render.py" --help >/dev/null 2>&1 \
    && echo "Renderer check OK" \
    || echo "WARN: renderer self-check failed; see dependency warning above" >&2
fi

if [[ -n "$VAULT" ]]; then
  [[ -d "$VAULT" ]] || { echo "ERROR: Obsidian vault not found: $VAULT" >&2; exit 1; }
  NOTE_DIR="$VAULT/Kash Crown/Skills"
  mkdir -p "$NOTE_DIR"
  cp "$HERE/obsidian/KC Vendetta Bounce.md" "$HERE/obsidian/vendetta-soul-reference.png" "$NOTE_DIR/"
  echo "Obsidian note -> $NOTE_DIR/KC Vendetta Bounce.md"
fi

echo
echo "Done. In any Claude Code session: upload a song and say \"Use KC VENDETTA BOUNCE for this\"."
