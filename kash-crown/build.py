#!/usr/bin/env python3
"""Build the KC VENDETTA BOUNCE backups from the one source of truth.

  release/kc-vendetta-bounce.zip      -> upload at claude.ai (Settings > Capabilities > Skills)
  obsidian/KC Vendetta Bounce.md      -> drop into your Obsidian vault

Run after any edit to .claude/skills/kc-vendetta-bounce/:  python3 kash-crown/build.py
"""
import datetime
import hashlib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / ".claude" / "skills" / "kc-vendetta-bounce"
ZIP = HERE / "release" / "kc-vendetta-bounce.zip"
NOTE = HERE / "obsidian" / "KC Vendetta Bounce.md"
FIXED_TS = (2026, 1, 1, 0, 0, 0)  # reproducible zip bytes


def files():
    """Every file in the skill folder except bytecode."""
    return sorted(p for p in SKILL.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def build_zip():
    """Write a reproducible zip with the skill folder at its root; return its sha256."""
    ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files():
            arcname = f"kc-vendetta-bounce/{p.relative_to(SKILL).as_posix()}"
            info = zipfile.ZipInfo(arcname, FIXED_TS)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if p.suffix in (".py", ".sh") else 0o644) << 16
            z.writestr(info, p.read_bytes())
    return hashlib.sha256(ZIP.read_bytes()).hexdigest()


def build_note(sha):
    """Write the Obsidian note: frontmatter, SKILL.md body, renderer source."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    body = text.split("---", 2)[2].strip() if text.startswith("---") else text
    script = (SKILL / "scripts" / "render.py").read_text(encoding="utf-8")
    today = datetime.date.today().isoformat()
    NOTE.parent.mkdir(parents=True, exist_ok=True)
    NOTE.write_text(f"""---
title: KC VENDETTA BOUNCE
aliases: [Vendetta Bounce, KC Bounce, Kash Crown visualizer]
tags: [kash-crown, skill, music-video, youtube, tiktok, visualizer]
type: claude-skill
skill_name: kc-vendetta-bounce
status: active
updated: {today}
zip_sha256: {sha}
---

> [!tip] How to use
> Upload a song to Claude and say **"Use KC VENDETTA BOUNCE for this"** (add the tagline if you have one).
> You get `TITLE_Youtube_Bars_HD.mp4` (full song, 16:9), `TITLE_TikTok_63s.mp4` (loudest 63s, 9:16) and `TITLE_Thumbnail.png`.

> [!info] Where the skill lives
> - Git backup: `elias-hivemind/agents` → `.claude/skills/kc-vendetta-bounce/`
> - claude.ai upload: `kash-crown/release/kc-vendetta-bounce.zip`
> - Claude Code (global): `~/.claude/skills/kc-vendetta-bounce/` (installed by `kash-crown/install.sh` or `install.ps1`)

![[vendetta-soul-reference.png]]

{body}

---

## Renderer source (backup copy)

> [!warning] Backup only
> Edit the file in the repo, then run `python3 kash-crown/build.py` to refresh this note and the zip.

```python
{script}```
""", encoding="utf-8")


def main():
    """Build the zip, then the note that records its checksum."""
    digest = build_zip()
    build_note(digest)
    ref = SKILL / "assets" / "reference" / "vendetta-soul-reference.png"
    (NOTE.parent / ref.name).write_bytes(ref.read_bytes())
    print(f"zip  : {ZIP} (sha256 {digest[:12]}…)")
    print(f"note : {NOTE}")


if __name__ == "__main__":
    main()
