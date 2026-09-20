# Open Design skill (Claude Code, user-level)

[Open Design](https://github.com/nexu-io/open-design) is an open-source,
local-first design workspace. Its full experience is a desktop app plus an MCP
server (`od mcp install claude`) that requires the local daemon. For headless
agent sessions there is a community `SKILL.md` wrapper,
[sugarforever/open-design-skill](https://github.com/sugarforever/open-design-skill),
which exposes the same catalogue (design systems, rendering templates,
functional skills, craft rules) without the daemon.

`scripts/install-open-design-skill.sh` installs that wrapper globally for
Claude Code and clones the catalogue it reads.

## Install

```bash
scripts/install-open-design-skill.sh
```

Requires `git` and `node >= 16`. Idempotent: re-running pulls updates for both
the skill and the catalogue.

| Path                           | Contents                                                            |
| ------------------------------ | ------------------------------------------------------------------- |
| `~/.claude/skills/open-design` | The skill (`SKILL.md` + `scripts/list-*.mjs`)                       |
| `~/.open-design-skill/repo`    | Sparse checkout: `design-systems/ design-templates/ skills/ craft/` |

Set `OPEN_DESIGN_ROOT` to point the skill at an existing Open Design checkout.
Pass `--full` for a non-sparse clone of the catalogue.

## Usage

Ask Claude Code for design work ("build a pitch deck", "landing page with the
Stripe design system", "use open design to ...") or invoke `/open-design`. The
skill writes a per-project `.open-design.json` binding after the first pick.

## Claude Code on the web

Remote containers start from a fresh image, so user-level skills are gone each
session. To re-install on every web session, add a SessionStart hook:

`.claude/hooks/session-start.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then exit 0; fi
"$CLAUDE_PROJECT_DIR/scripts/install-open-design-skill.sh" || exit 0
```

`.claude/settings.json`

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"
          }
        ]
      }
    ]
  }
}
```
