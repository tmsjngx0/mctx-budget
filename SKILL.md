---
name: mctx-budget
description: Context budget X-ray for Claude Code. Audit plugins, skills, rules, memory, and token costs. Use when asked about token optimization, context budget, plugin management, or "what's eating my tokens".
---

Always run with `--format json` and render results as markdown tables.

## Commands

### list — Show installed plugins

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json list
```

Render as:
| # | Status | Plugin | Marketplace | Source |

### toggle — Enable/disable a plugin

1. Run `list` to show current state
2. Confirm number with user
3. Confirm scope (default: local)
4. Run: `--format json toggle <number> --scope <scope>`
5. Summarize: "name OFF → ON (saved to scope)"
6. **Always** remind: "Run `/reload-plugins` to apply."

### status — Detailed state per scope

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json status
```

| # | Plugin | Global | Project | Local | Effective | Source |
- `null` → `—`, `true` → `ON`, `false` → `OFF`

### skills — List active skills

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json skills
```

| Source | Skill | Description |

### agents — List active agents

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json agents
```

| Source | Agent |

### audit — Project-aware recommendations

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json audit
```

Full context audit across all layers. Reports:
1. **project_tags** — detected language/framework/tool tags
2. **skill_groups** — skills by source (user:gstack, user:dotfiles, project, plugin:*) with sizes
3. **rules** — active and excluded rules with sizes
4. **recommendations** — plugin enable/disable suggestions
5. **irrelevant_skills** — active but unnecessary for this project

Render as:

**Project tags**: python, django, beads, ...

**Token budget** (approximate):
| Source | Count | Size | ~Tokens |
| user:gstack | 27 | 884K | 221K (on invoke only) |
| plugin:mindcontext-core | 12 | 62K | 15K (on invoke only) |
| project | 7 | 164K | 41K (on invoke only) |
| Rules (active) | 8 | 25K | 6K (always loaded) |
| Rules (excluded) | 5 | 18K | — (saved) |

**Irrelevant skills**:
| Source | Skill | Required tags |

**Recommendations**: ...

After audit, guide user to `toggle` or `extract` to optimize.

### extract — Copy a plugin skill locally

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json extract <plugin> <skill> [--dest <name>]
```

Copies a skill from a plugin's cache to `.claude/skills/` so it works standalone without the plugin enabled.
Supports fuzzy matching on both plugin and skill names.

Typical workflow:
1. `audit` identifies needed skills from a heavy plugin
2. `extract` those skills locally
3. `toggle` the plugin OFF to save tokens
4. `/reload-plugins`

### remove — Remove a locally extracted skill

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json remove <skill>
```

Only removes from project `.claude/skills/`, never from plugin cache.

### profile save/load/list — Profile management

```bash
python3 {baseDir}/scripts/mctx_budget.py --format json profile save <name>
python3 {baseDir}/scripts/mctx_budget.py --format json profile load <name> --scope <scope>
python3 {baseDir}/scripts/mctx_budget.py --format json profile list
```

## Response style

- Never show raw JSON — always render as markdown tables or summaries
- After any state change, remind user to run `/reload-plugins`
- On errors, explain the `error` field clearly
