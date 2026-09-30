# Editor CLI skills

Agent skills that turn desktop editors into command-line tools, plus the Kash Crown video style built on them. Each skill is a `SKILL.md` plus Python scripts in its `scripts/` folder. The editor CLIs use only the standard library; `capcut-cli` beat sync uses librosa when it is installed, and `kc-vendetta-bounce` needs numpy and Pillow.

| Skill                                                 | Drives                                    | Headless?                                                      |
| ----------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------- |
| [`gimp-cli`](gimp-cli/SKILL.md)                       | GIMP 2.10 / 3.x via Script-Fu batch mode  | Yes                                                            |
| [`davinci-resolve-cli`](davinci-resolve-cli/SKILL.md) | DaVinci Resolve 18+ via its scripting API | Needs Resolve running; external scripting needs Resolve Studio |
| [`capcut-cli`](capcut-cli/SKILL.md)                   | CapCut drafts + ffmpeg edits + beat sync  | Yes. CapCut itself has no API, so edits render through ffmpeg  |
| [`kc-vendetta-bounce`](kc-vendetta-bounce/SKILL.md)   | Cinematic cracked-gold lyric visualizer   | Yes. numpy + Pillow + ffmpeg; beats via capcut-cli / librosa   |

## Install

These skills work with the open [`skills`](https://www.npmjs.com/package/skills) CLI:

```bash
npm install -g skills

# globally (user level) for Claude Code, straight from GitHub
skills add elias-hivemind/agents -g -a claude-code -s gimp-cli -s davinci-resolve-cli -s capcut-cli -s kc-vendetta-bounce -y

# or from a local checkout
skills add ./skills -g -a claude-code -y

skills list -g
```

### Cloud environment setup script

To have everything ready in every Claude Code cloud session, including the design skills this setup pairs with:

```bash
apt-get update -q
apt-get install -y -q --no-install-recommends ffmpeg gimp || true
pip install -q numpy pillow librosa
npm install -g skills @playwright/cli getdesign
skills add elias-hivemind/agents -g -a claude-code -s gimp-cli -s davinci-resolve-cli -s capcut-cli -s kc-vendetta-bounce -y
skills add leonxlnx/taste-skill -g -a claude-code -y
skills add pbakaus/impeccable -g -a claude-code -s impeccable -y
skills add microsoft/playwright-cli -g -a claude-code -s playwright-cli -y
skills add img2threejs/img2threejs -g -a claude-code -y
```

The skills work without the `skills` CLI too. Call the scripts directly, e.g. `python3 skills/gimp-cli/scripts/gimp_cli.py --help`.

## Tests

```bash
python3 -m unittest discover -s skills/tests -v
```

- The Resolve tests run against an in-memory fake of the scripting API (`tests/fake_resolve`).
- The GIMP and ffmpeg end-to-end tests run only when those tools are installed. The `kc-vendetta-bounce` render test needs numpy and Pillow.
- To test a specific GIMP build, set `GIMP_CONSOLE`.
