# Editor CLI skills

Agent skills that turn desktop editors into command-line tools. Each skill is a `SKILL.md` plus stdlib-only Python scripts in its `scripts/` folder.

| Skill                                                 | Drives                                    | Headless?                                                      |
| ----------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------- |
| [`gimp-cli`](gimp-cli/SKILL.md)                       | GIMP 2.10 / 3.x via Script-Fu batch mode  | Yes                                                            |
| [`davinci-resolve-cli`](davinci-resolve-cli/SKILL.md) | DaVinci Resolve 18+ via its scripting API | Needs Resolve running; external scripting needs Resolve Studio |
| [`capcut-cli`](capcut-cli/SKILL.md)                   | CapCut desktop drafts + ffmpeg edits      | Yes. CapCut itself has no API, so edits render through ffmpeg  |

## Install

These skills work with the open [`skills`](https://www.npmjs.com/package/skills) CLI:

```bash
npm install -g skills

# globally (user level) for Claude Code, straight from GitHub
skills add elias-hivemind/agents -g -a claude-code -s gimp-cli -s davinci-resolve-cli -s capcut-cli -y

# or from a local checkout
skills add ./skills -g -a claude-code -y

skills list -g
```

The skills work without the `skills` CLI too. Call the scripts directly, e.g. `python3 skills/gimp-cli/scripts/gimp_cli.py --help`.

## Tests

```bash
python3 -m unittest discover -s skills/tests -v
```

- The Resolve tests run against an in-memory fake of the scripting API (`tests/fake_resolve`).
- The GIMP and ffmpeg end-to-end tests run only when those tools are installed.
- To test a specific GIMP build, set `GIMP_CONSOLE`.
