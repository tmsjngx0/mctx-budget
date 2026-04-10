# mctx-budget

Context budget X-ray for Claude Code. See what's eating your tokens.

## What it does

Shows the structural overhead in your Claude Code sessions: plugins, skills, rules, CLAUDE.md chains, memory, and hooks. Like `htop` for your context window.

## Install

```bash
npx skills add tmsjngx0/mctx-budget
```

Or manually copy to `~/.claude/skills/mctx-budget/`.

## Commands

| Command | What |
|---------|------|
| `list` | Show installed plugins with on/off state |
| `toggle <n>` | Enable/disable plugin by number |
| `status` | Side-by-side Global/Project/Local/Effective state |
| `skills` | List all skills grouped by source with sizes |
| `agents` | List active agents from enabled plugins |
| `audit` | Detect project tags, recommend plugin changes, find irrelevant skills |
| `extract <plugin> <skill>` | Copy a skill from plugin cache to local `.claude/skills/` |
| `remove <skill>` | Delete locally extracted skill |
| `profile save/load/list` | Save and restore plugin configurations |

All commands support `--format json` for machine-readable output.

## How it works

The tool reads Claude Code's standard file structure:

- `~/.claude/plugins/installed_plugins.json` for plugin inventory
- `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json` for enable/disable state (local > project > global)
- `~/.claude/plugins/cache/` for plugin skill and agent definitions
- `~/.claude/skills/` and `.claude/skills/` for user and project skills
- `~/.claude/rules/` and `.claude/rules/` for rules files

No dependencies beyond Python 3.8+ stdlib.

## Requirements

- Python 3.8+
- Claude Code (or any AI agent that supports Vercel Skills)

## Part of

[mindcontext](https://github.com/tmsjngx0) ecosystem. Works independently, no other mindcontext tools required.
