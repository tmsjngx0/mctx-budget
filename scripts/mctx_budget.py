#!/usr/bin/env python3
"""mctx-budget: Context budget X-ray for Claude Code.

Audits plugins, skills, rules, memory, and token costs.
Manages enable/disable state across global, project, and local settings.

Commands: list, toggle, status, skills, agents, audit, extract, remove, profile
Supports --format json for machine-readable output (used by SKILL.md).
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ANSI colors
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

HOME = Path.home()
GLOBAL_SETTINGS = HOME / ".claude" / "settings.json"
INSTALLED_PLUGINS = HOME / ".claude" / "plugins" / "installed_plugins.json"
PLUGIN_CACHE = HOME / ".claude" / "plugins" / "cache"
USER_SKILLS = HOME / ".claude" / "skills"
PROJECT_SKILLS = Path(".claude/skills")
PROFILES_FILE = Path(__file__).resolve().parent.parent / "profiles.json"

PROJECT_SETTINGS = Path(".claude/settings.json")
LOCAL_SETTINGS = Path(".claude/settings.local.json")

_json_mode = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def emit(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def get_installed_plugins() -> list[str]:
    data = read_json(INSTALLED_PLUGINS)
    plugins_map = data.get("plugins", {})
    keys = []
    for key, versions in plugins_map.items():
        if isinstance(versions, list) and versions:
            sorted_versions = sorted(
                versions,
                key=lambda v: v.get("lastUpdated", ""),
                reverse=True,
            )
            if sorted_versions[0].get("installPath"):
                keys.append(key)
    return sorted(keys)


def get_plugin_install_path(key: str) -> Path | None:
    """Get the latest install path for a plugin key."""
    data = read_json(INSTALLED_PLUGINS)
    plugins_map = data.get("plugins", {})
    versions = plugins_map.get(key, [])
    if not isinstance(versions, list) or not versions:
        return None
    sorted_versions = sorted(
        versions, key=lambda v: v.get("lastUpdated", ""), reverse=True
    )
    p = sorted_versions[0].get("installPath", "")
    return Path(p) if p else None


def get_enabled_plugins(path: Path) -> dict[str, bool]:
    data = read_json(path)
    return data.get("enabledPlugins", {})


def get_effective_state(plugins: list[str]) -> dict[str, bool]:
    global_state = get_enabled_plugins(GLOBAL_SETTINGS)
    project_state = get_enabled_plugins(PROJECT_SETTINGS)
    local_state = get_enabled_plugins(LOCAL_SETTINGS)
    effective = {}
    for key in plugins:
        if key in local_state:
            effective[key] = local_state[key]
        elif key in project_state:
            effective[key] = project_state[key]
        elif key in global_state:
            effective[key] = global_state[key]
        else:
            effective[key] = True  # installed plugins default to enabled
    return effective


def get_source(key: str) -> str:
    local = get_enabled_plugins(LOCAL_SETTINGS)
    project = get_enabled_plugins(PROJECT_SETTINGS)
    global_ = get_enabled_plugins(GLOBAL_SETTINGS)
    if key in local:
        return "local"
    if key in project:
        return "project"
    if key in global_:
        return "global"
    return "none"


def set_plugin_state(path: Path, key: str, enabled: bool) -> None:
    data = read_json(path)
    if "enabledPlugins" not in data:
        data["enabledPlugins"] = {}
    data["enabledPlugins"][key] = enabled
    write_json(path, data)


def scope_to_path(scope: str) -> Path:
    return {
        "global": GLOBAL_SETTINGS,
        "project": PROJECT_SETTINGS,
        "local": LOCAL_SETTINGS,
    }[scope]


def ask_scope() -> str:
    print(f"\n{BOLD}Save to:{RESET}")
    print(f"  [1] local   {DIM}(.claude/settings.local.json){RESET}")
    print(f"  [2] project {DIM}(.claude/settings.json){RESET}")
    print(f"  [3] global  {DIM}(~/.claude/settings.json){RESET}")
    while True:
        choice = input("\nChoose (1-3): ").strip()
        if choice == "1":
            return "local"
        if choice == "2":
            return "project"
        if choice == "3":
            return "global"
        print(f"{RED}Enter 1, 2, or 3.{RESET}")


def parse_skill_frontmatter(skill_md: Path) -> dict:
    """Parse SKILL.md frontmatter for name and description."""
    if not skill_md.exists():
        return {}
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("'\"")
    return fm


# ---------------------------------------------------------------------------
# Skill / Agent scanning
# ---------------------------------------------------------------------------

def scan_plugin_skills(plugin_key: str) -> list[dict]:
    """Scan skills directory for a plugin."""
    install_path = get_plugin_install_path(plugin_key)
    if not install_path or not install_path.exists():
        return []
    skills_dir = install_path / "skills"
    if not skills_dir.is_dir():
        return []
    results = []
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        fm = parse_skill_frontmatter(d / "SKILL.md")
        results.append({
            "skill": fm.get("name", d.name),
            "description": fm.get("description", "")[:120],
            "dir": d.name,
        })
    return results


def scan_plugin_agents(plugin_key: str) -> list[dict]:
    """Scan agents directory for a plugin."""
    install_path = get_plugin_install_path(plugin_key)
    if not install_path or not install_path.exists():
        return []
    agents_dir = install_path / "agents"
    if not agents_dir.is_dir():
        return []
    results = []
    for f in sorted(agents_dir.iterdir()):
        name = f.stem if f.is_file() else f.name
        results.append({"agent": name})
    return results


def _classify_user_skill(d: Path) -> str:
    """Classify a user-level skill into a group based on symlink target.

    Uses path-segment matching (e.g. '/gstack/' not just 'gstack') to avoid
    false matches like '/my-gstack-fork/'.
    """
    if not d.is_symlink():
        return "user:standalone"
    try:
        target = str(d.resolve())
    except OSError:
        return "user:standalone"  # broken symlink
    if "/gstack/" in target:
        return "user:gstack"
    if "/dotfiles/" in target:
        return "user:dotfiles"
    if "/agents/" in target or "/.agents/" in target:
        return "user:agents"
    return "user:standalone"


def _measure_skill_dir(d: Path) -> int:
    """Measure SKILL.md + references/ only (skip .git, node_modules, etc)."""
    resolved = d.resolve() if d.is_symlink() else d
    total = 0
    if not resolved.is_dir():
        return 0
    # Only measure SKILL.md and references/ — the actual context cost
    skill_md = resolved / "SKILL.md"
    if skill_md.exists():
        total += skill_md.stat().st_size
    refs_dir = resolved / "references"
    if refs_dir.is_dir():
        for f in refs_dir.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    return total


def scan_local_skills() -> list[dict]:
    """Scan user-level and project-level custom skills with grouping."""
    results = []
    for base, level in [(USER_SKILLS, "user"), (PROJECT_SKILLS, "project")]:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            target = d.resolve() if d.is_symlink() else d
            fm = parse_skill_frontmatter(d / "SKILL.md")
            if not fm:
                # Try resolved path for symlinks
                fm = parse_skill_frontmatter(target / "SKILL.md")

            size = _measure_skill_dir(d)
            group = _classify_user_skill(d) if level == "user" else "project"

            results.append({
                "skill": fm.get("name", d.name),
                "description": fm.get("description", "")[:120],
                "source": group,
                "level": level,
                "size_bytes": size,
                "is_symlink": d.is_symlink(),
                "link_target": str(target) if d.is_symlink() else None,
            })
    return results


# ---------------------------------------------------------------------------
# Audit: project context detection
# ---------------------------------------------------------------------------

# File extension → language tags
LANG_EXTENSIONS = {
    ".py": "python",
    ".cs": "csharp",
    ".csproj": "dotnet",
    ".sln": "dotnet",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "react",
    ".jsx": "react",
    ".rb": "ruby",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".swift": "swift",
    ".kt": "kotlin",
    ".ex": "elixir",
    ".exs": "elixir",
}

# Marker files → framework/tool tags
MARKER_FILES = {
    "manage.py": "django",
    "pyproject.toml": "python",
    "Gemfile": "ruby",
    "Rakefile": "ruby",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "package.json": "node",
    "tsconfig.json": "typescript",
    "next.config.js": "nextjs",
    "next.config.ts": "nextjs",
    "tailwind.config.js": "tailwind",
    "tailwind.config.ts": "tailwind",
    ".beads": "beads",
    "openspec": "openspec",
    ".serena": "serena",
    "Dockerfile": "docker",
    "docker-compose.yml": "docker",
}

# Skill → required tags (skill is relevant only if ANY tag matches)
SKILL_TAGS: dict[str, set[str]] = {
    # Ruby-specific
    "dhh-rails-style": {"ruby"},
    "andrew-kane-gem-writer": {"ruby"},
    "dspy-ruby": {"ruby"},
    # iOS/mobile
    "test-xcode": {"swift", "ios"},
    # Style guides
    "every-style-editor": {"every-style"},
    # Frontend
    "frontend-design": {"react", "nextjs", "javascript", "typescript", "tailwind", "node"},
    # Generic (always relevant)
    "ce-plan": set(),
    "ce-brainstorm": set(),
    "ce-review": set(),
    "ce-work": set(),
    "ce-compound": set(),
    "ce-ideate": set(),
    "git-commit": set(),
    "git-commit-push-pr": set(),
    "git-worktree": set(),
    "git-clean-gone-branches": set(),
    "agent-browser": set(),
    "claude-permissions-optimizer": set(),
    "document-review": set(),
    "onboarding": set(),
    "reproduce-bug": set(),
    "todo-resolve": set(),
    "test-browser": {"javascript", "typescript", "react", "nextjs", "node"},
    "feature-video": set(),
    "gemini-imagegen": set(),
    "rclone": set(),
    "proof": set(),
    "agent-native-architecture": set(),
}

# Plugin-level tags (plugin is relevant if ANY tag matches)
PLUGIN_TAGS: dict[str, set[str]] = {
    "beads": {"beads"},
    "mindcontext-core": set(),  # always relevant
    "compound-engineering": set(),  # has mixed skills, audit at skill level
    "context7": set(),  # always relevant
    "serena": {"serena"},
    "worktrunk": set(),
    "feature-dev": set(),
    "code-review": set(),
    "code-simplifier": set(),
    "superpowers": set(),
    "csharp-lsp": {"csharp", "dotnet"},
    "explanatory-output-style": set(),
    "learning-output-style": set(),
    "security-guidance": set(),
    "flowforge-skills": {"flowforge"},
    "mindcontext-skills": set(),  # legacy
}


def detect_project_tags(root: Path = Path("."), max_depth: int = 3) -> set[str]:
    """Detect project language/framework/tool tags by scanning files."""
    tags: set[str] = set()

    # Check marker files
    for marker, tag in MARKER_FILES.items():
        if (root / marker).exists():
            tags.add(tag)

    # Scan file extensions (limited depth)
    ext_counts: dict[str, int] = {}
    for p in _walk_limited(root, max_depth):
        ext = p.suffix.lower()
        if ext in LANG_EXTENSIONS:
            lang = LANG_EXTENSIONS[ext]
            ext_counts[lang] = ext_counts.get(lang, 0) + 1

    # Only add languages with meaningful presence (>= 2 files)
    for lang, count in ext_counts.items():
        if count >= 2:
            tags.add(lang)

    return tags


def _walk_limited(root: Path, max_depth: int) -> list[Path]:
    """Walk directory tree with depth limit, skipping hidden/vendor dirs."""
    skip = {".git", ".beads", "node_modules", "__pycache__", ".venv", "venv", ".tox"}
    results = []

    def _walk(p: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            for child in p.iterdir():
                if child.name.startswith(".") or child.name in skip:
                    continue
                if child.is_file():
                    results.append(child)
                elif child.is_dir() and not child.is_symlink():
                    _walk(child, depth + 1)
        except PermissionError:
            pass

    _walk(root, 0)
    return results


def audit_skills(project_tags: set[str]) -> list[dict]:
    """Audit all skills from enabled plugins against project tags."""
    plugins = get_installed_plugins()
    effective = get_effective_state(plugins)
    findings = []

    for key in plugins:
        if not effective.get(key, False):
            continue
        plugin_name = key.split("@")[0]
        skills = scan_plugin_skills(key)
        for s in skills:
            skill_name = s["skill"]
            required = SKILL_TAGS.get(skill_name)
            if required is None:
                # Unknown skill — mark as unknown for review
                relevance = "unknown"
            elif len(required) == 0:
                relevance = "always"
            elif required & project_tags:
                relevance = "relevant"
            else:
                relevance = "irrelevant"

            findings.append({
                "plugin": plugin_name,
                "plugin_key": key,
                "skill": skill_name,
                "description": s["description"],
                "relevance": relevance,
                "required_tags": sorted(required) if required else [],
            })

    return findings


def audit_plugins(project_tags: set[str]) -> list[dict]:
    """Audit plugins against project tags."""
    plugins = get_installed_plugins()
    effective = get_effective_state(plugins)
    results = []

    for key in plugins:
        plugin_name = key.split("@")[0]
        enabled = effective.get(key, False)
        required = PLUGIN_TAGS.get(plugin_name, set())

        if len(required) == 0:
            relevance = "always"
        elif required & project_tags:
            relevance = "relevant"
        else:
            relevance = "irrelevant"

        skills = scan_plugin_skills(key) if enabled else []
        agents = scan_plugin_agents(key) if enabled else []

        results.append({
            "key": key,
            "name": plugin_name,
            "enabled": enabled,
            "relevance": relevance,
            "required_tags": sorted(required) if required else [],
            "skill_count": len(skills),
            "agent_count": len(agents),
        })

    return results


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(_args: argparse.Namespace) -> None:
    plugins = get_installed_plugins()
    effective = get_effective_state(plugins)

    if _json_mode:
        items = []
        for i, key in enumerate(plugins, 1):
            name, marketplace = key.split("@", 1) if "@" in key else (key, "")
            items.append({
                "num": i, "key": key, "name": name,
                "marketplace": marketplace,
                "enabled": effective.get(key, False),
                "source": get_source(key),
            })
        emit({"command": "list", "count": len(items), "plugins": items})
        return

    if not plugins:
        print(f"{YELLOW}No plugins installed.{RESET}")
        return
    print(f"\n{BOLD}Installed plugins ({len(plugins)}){RESET}\n")
    for i, key in enumerate(plugins, 1):
        icon = f"{GREEN}✅{RESET}" if effective.get(key, False) else f"{RED}❌{RESET}"
        print(f"  [{i:2d}] {icon} {key}")
    print()


def cmd_toggle(args: argparse.Namespace) -> None:
    plugins = get_installed_plugins()
    if not plugins:
        if _json_mode:
            emit({"command": "toggle", "error": "no plugins installed"})
        else:
            print(f"{YELLOW}No plugins installed.{RESET}")
        return

    try:
        idx = int(args.number) - 1
    except ValueError:
        if _json_mode:
            emit({"command": "toggle", "error": "invalid number"})
        else:
            print(f"{RED}Enter a number.{RESET}")
        sys.exit(1)

    if idx < 0 or idx >= len(plugins):
        msg = f"out of range: 1-{len(plugins)}"
        if _json_mode:
            emit({"command": "toggle", "error": msg})
        else:
            print(f"{RED}Out of range: enter 1-{len(plugins)}.{RESET}")
        sys.exit(1)

    key = plugins[idx]
    effective = get_effective_state(plugins)
    current = effective.get(key, False)
    new_state = not current

    scope = args.scope if args.scope else ("local" if _json_mode else ask_scope())
    path = scope_to_path(scope)
    set_plugin_state(path, key, new_state)

    if _json_mode:
        name, marketplace = key.split("@", 1) if "@" in key else (key, "")
        emit({
            "command": "toggle", "key": key, "name": name,
            "marketplace": marketplace, "previous": current,
            "enabled": new_state, "scope": scope,
        })
    else:
        state_str = f"{GREEN}ON{RESET}" if new_state else f"{RED}OFF{RESET}"
        print(f"\n{key} → {state_str} {DIM}(scope: {scope}){RESET}")
        print(f"\n{YELLOW}Run /reload-plugins to apply.{RESET}")


def cmd_status(_args: argparse.Namespace) -> None:
    plugins = get_installed_plugins()
    global_state = get_enabled_plugins(GLOBAL_SETTINGS)
    project_state = get_enabled_plugins(PROJECT_SETTINGS)
    local_state = get_enabled_plugins(LOCAL_SETTINGS)
    effective = get_effective_state(plugins)

    if _json_mode:
        items = []
        for i, key in enumerate(plugins, 1):
            name, marketplace = key.split("@", 1) if "@" in key else (key, "")

            def resolve(d: dict, k: str):
                return d[k] if k in d else None

            items.append({
                "num": i, "key": key, "name": name,
                "marketplace": marketplace,
                "global": resolve(global_state, key),
                "project": resolve(project_state, key),
                "local": resolve(local_state, key),
                "effective": effective.get(key, False),
                "source": get_source(key),
            })
        emit({"command": "status", "count": len(items), "plugins": items})
        return

    if not plugins:
        print(f"{YELLOW}No plugins installed.{RESET}")
        return
    print(f"\n{BOLD}{'Plugin':<45} {'Global':>8} {'Project':>8} {'Local':>8} {'Effect':>8}{RESET}")
    print(f"{'─' * 45} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8}")
    for key in plugins:
        name = key if len(key) <= 44 else key[:41] + "..."

        def fmt(state_dict: dict, k: str) -> str:
            if k not in state_dict:
                return f"{DIM}  —{RESET}"
            return f"{GREEN}  ON{RESET}" if state_dict[k] else f"{RED} OFF{RESET}"

        g = fmt(global_state, key)
        p = fmt(project_state, key)
        lo = fmt(local_state, key)
        eff = f"{GREEN}  ON{RESET}" if effective.get(key, False) else f"{RED} OFF{RESET}"
        print(f"  {name:<44} {g:>17} {p:>17} {lo:>17} {eff:>17}")
    print()


def cmd_skills(_args: argparse.Namespace) -> None:
    plugins = get_installed_plugins()
    effective = get_effective_state(plugins)
    all_skills = []

    # Plugin skills (measure size too)
    for key in plugins:
        if not effective.get(key, False):
            continue
        plugin_name = key.split("@")[0]
        install_path = get_plugin_install_path(key)
        for s in scan_plugin_skills(key):
            size = 0
            if install_path:
                skill_dir = install_path / "skills" / s["dir"]
                size = _measure_skill_dir(skill_dir)
            all_skills.append({
                "source": f"plugin:{plugin_name}",
                "level": "plugin",
                "plugin_key": key,
                "size_bytes": size,
                **s,
            })

    # User + project skills
    for s in scan_local_skills():
        all_skills.append(s)

    if _json_mode:
        # Group summary
        groups: dict[str, dict] = {}
        for s in all_skills:
            src = s.get("source", "unknown")
            g = groups.setdefault(src, {"count": 0, "total_bytes": 0})
            g["count"] += 1
            g["total_bytes"] += s.get("size_bytes", 0)

        emit({
            "command": "skills",
            "count": len(all_skills),
            "groups": {k: v for k, v in sorted(groups.items())},
            "skills": all_skills,
        })
        return

    print(f"\n{BOLD}Active skills ({len(all_skills)}){RESET}\n")
    for s in all_skills:
        src = s.get("source", "?")
        name = s.get("skill", "?")
        desc = s.get("description", "")[:60]
        print(f"  {CYAN}{src}{RESET} / {name}")
        if desc:
            print(f"    {DIM}{desc}{RESET}")
    print()


def cmd_agents(_args: argparse.Namespace) -> None:
    plugins = get_installed_plugins()
    effective = get_effective_state(plugins)
    all_agents = []

    for key in plugins:
        if not effective.get(key, False):
            continue
        plugin_name = key.split("@")[0]
        for a in scan_plugin_agents(key):
            all_agents.append({
                "source": f"plugin:{plugin_name}",
                "plugin_key": key,
                **a,
            })

    if _json_mode:
        emit({"command": "agents", "count": len(all_agents), "agents": all_agents})
        return

    print(f"\n{BOLD}Active agents ({len(all_agents)}){RESET}\n")
    for a in all_agents:
        src = a.get("source", "?")
        name = a.get("agent", "?")
        print(f"  {CYAN}{src}{RESET} / {name}")
    print()


def _measure_context() -> dict:
    """Measure all structural context loaded into a Claude Code session.

    Returns a structured dict with separate categories for the X-ray view:
    {
        "rules": [...],      # .claude/rules/ files
        "claude_md": [...],  # CLAUDE.md chain
        "memory": [...],     # auto-memory files
        "excludes": set(),   # claudeMdExcludes from all settings
    }
    """
    rules = []
    claude_md = []
    memory = []

    # Read claudeMdExcludes from all settings
    excludes: set[str] = set()
    for path in [GLOBAL_SETTINGS, PROJECT_SETTINGS, LOCAL_SETTINGS]:
        data = read_json(path)
        for e in data.get("claudeMdExcludes", []):
            excludes.add(e)

    # Global rules
    global_rules = HOME / ".claude" / "rules"
    if global_rules.is_dir():
        for f in sorted(global_rules.iterdir()):
            if f.is_file() and f.suffix == ".md":
                excluded = f"~/.claude/rules/{f.name}" in excludes
                size = f.stat().st_size
                rules.append({
                    "file": f.name,
                    "level": "global",
                    "path": f"~/.claude/rules/{f.name}",
                    "size_bytes": size,
                    "excluded": excluded,
                })

    # Project rules
    proj_rules = Path(".claude/rules")
    if proj_rules.is_dir():
        for f in sorted(proj_rules.iterdir()):
            if f.is_file() and f.suffix == ".md":
                excluded = f".claude/rules/{f.name}" in excludes
                size = f.stat().st_size
                rules.append({
                    "file": f.name,
                    "level": "project",
                    "path": f".claude/rules/{f.name}",
                    "size_bytes": size,
                    "excluded": excluded,
                })

    # CLAUDE.md chain
    for p, level in [(HOME / ".claude/CLAUDE.md", "global"), (Path("CLAUDE.md"), "project")]:
        if p.exists():
            claude_md.append({
                "file": p.name,
                "level": level,
                "path": str(p),
                "size_bytes": p.stat().st_size,
            })

    # AGENTS.md
    agents_md = Path("AGENTS.md")
    if agents_md.exists():
        claude_md.append({
            "file": "AGENTS.md",
            "level": "project",
            "path": "AGENTS.md",
            "size_bytes": agents_md.stat().st_size,
        })

    # Memory
    memory_dir = HOME / ".claude" / "projects"
    if memory_dir.is_dir():
        cwd_key = str(Path.cwd()).replace("/", "-").lstrip("-")
        mem_dir = memory_dir / cwd_key / "memory"
        if mem_dir.is_dir():
            for f in sorted(mem_dir.iterdir()):
                if f.is_file() and f.suffix == ".md":
                    memory.append({
                        "file": f.name,
                        "level": "memory",
                        "path": "auto-memory",
                        "size_bytes": f.stat().st_size,
                    })

    return {
        "rules": rules,
        "claude_md": claude_md,
        "memory": memory,
        "excludes": excludes,
    }


def _measure_rules() -> list[dict]:
    """Legacy wrapper: returns flat list for backward compat with audit command."""
    ctx = _measure_context()
    results = []
    for r in ctx["rules"]:
        results.append(r)
    for c in ctx["claude_md"]:
        results.append({**c, "excluded": False})
    for m in ctx["memory"]:
        results.append({**m, "excluded": False})
    return results


def cmd_audit(_args: argparse.Namespace) -> None:
    project_tags = detect_project_tags()
    plugin_findings = audit_plugins(project_tags)
    skill_findings = audit_skills(project_tags)

    irrelevant_skills = [s for s in skill_findings if s["relevance"] == "irrelevant"]
    unknown_skills = [s for s in skill_findings if s["relevance"] == "unknown"]
    relevant_skills = [s for s in skill_findings if s["relevance"] in ("relevant", "always")]

    # Plugin recommendations
    recommendations = []
    for p in plugin_findings:
        if p["enabled"] and p["relevance"] == "irrelevant":
            recommendations.append({
                "action": "disable",
                "key": p["key"],
                "name": p["name"],
                "reason": f"tags {p['required_tags']} not found in project",
            })
        elif not p["enabled"] and p["relevance"] in ("relevant", "always"):
            recommendations.append({
                "action": "enable",
                "key": p["key"],
                "name": p["name"],
                "reason": "project has matching tags" if p["required_tags"] else "generally useful",
            })

    # All skills (user + project + plugin)
    local_skills = scan_local_skills()

    # Group skills by source with totals
    skill_groups: dict[str, dict] = {}
    for s in local_skills:
        src = s.get("source", "unknown")
        g = skill_groups.setdefault(src, {"count": 0, "total_bytes": 0, "skills": []})
        g["count"] += 1
        g["total_bytes"] += s.get("size_bytes", 0)
        g["skills"].append({"name": s["skill"], "size_bytes": s.get("size_bytes", 0)})

    for s in skill_findings:
        src = f"plugin:{s['plugin']}"
        g = skill_groups.setdefault(src, {"count": 0, "total_bytes": 0, "skills": []})
        g["count"] += 1
        # plugin skills don't have size in audit findings, estimate from SKILL_TAGS
        g["skills"].append({"name": s["skill"]})

    # Rules measurement
    rules = _measure_rules()
    rules_active = [r for r in rules if not r["excluded"]]
    rules_excluded = [r for r in rules if r["excluded"]]
    active_bytes = sum(r["size_bytes"] for r in rules_active)
    excluded_bytes = sum(r["size_bytes"] for r in rules_excluded)

    if _json_mode:
        emit({
            "command": "audit",
            "project_tags": sorted(project_tags),
            "plugin_summary": {
                "total_skills": len(skill_findings),
                "relevant": len(relevant_skills),
                "irrelevant": len(irrelevant_skills),
                "unknown": len(unknown_skills),
            },
            "skill_groups": {k: v for k, v in sorted(skill_groups.items())},
            "rules": {
                "active": rules_active,
                "excluded": rules_excluded,
                "active_bytes": active_bytes,
                "excluded_bytes": excluded_bytes,
            },
            "recommendations": recommendations,
            "irrelevant_skills": irrelevant_skills,
            "plugins": plugin_findings,
        })
        return

    # Text output
    print(f"\n{BOLD}Project tags:{RESET} {', '.join(sorted(project_tags))}")
    print(f"\n{BOLD}Skill summary:{RESET} {len(relevant_skills)} relevant, {len(irrelevant_skills)} irrelevant, {len(unknown_skills)} unknown")

    if irrelevant_skills:
        print(f"\n{RED}Irrelevant skills:{RESET}")
        for s in irrelevant_skills:
            print(f"  {s['plugin']}/{s['skill']}  (needs: {', '.join(s['required_tags'])})")

    if recommendations:
        print(f"\n{YELLOW}Recommendations:{RESET}")
        for r in recommendations:
            icon = f"{GREEN}enable{RESET}" if r["action"] == "enable" else f"{RED}disable{RESET}"
            print(f"  {icon} {r['name']} — {r['reason']}")
    print()


def cmd_profile_save(args: argparse.Namespace) -> None:
    plugins = get_installed_plugins()
    effective = get_effective_state(plugins)
    profiles = read_json(PROFILES_FILE)
    profiles[args.name] = effective
    write_json(PROFILES_FILE, profiles)
    enabled_count = sum(1 for v in effective.values() if v)
    if _json_mode:
        emit({"command": "profile_save", "name": args.name,
              "enabled_count": enabled_count, "total": len(effective)})
    else:
        print(f"\n{GREEN}Profile '{args.name}' saved{RESET} ({enabled_count}/{len(effective)} active)")


def cmd_profile_load(args: argparse.Namespace) -> None:
    profiles = read_json(PROFILES_FILE)
    if args.name not in profiles:
        if _json_mode:
            emit({"command": "profile_load", "error": f"profile '{args.name}' not found",
                  "available": list(profiles.keys())})
        else:
            print(f"{RED}Profile '{args.name}' not found.{RESET}")
            available = list(profiles.keys())
            if available:
                print(f"Available: {', '.join(available)}")
        sys.exit(1)

    profile = profiles[args.name]
    scope = args.scope if args.scope else ("local" if _json_mode else ask_scope())
    path = scope_to_path(scope)
    data = read_json(path)
    data["enabledPlugins"] = profile
    write_json(path, data)
    enabled_count = sum(1 for v in profile.values() if v)
    if _json_mode:
        emit({"command": "profile_load", "name": args.name, "scope": scope,
              "enabled_count": enabled_count, "total": len(profile)})
    else:
        print(f"\n{GREEN}Profile '{args.name}' loaded{RESET} → {scope} ({enabled_count}/{len(profile)} active)")
        print(f"\n{YELLOW}Run /reload-plugins to apply.{RESET}")


def cmd_profile_list(_args: argparse.Namespace) -> None:
    profiles = read_json(PROFILES_FILE)
    if _json_mode:
        items = []
        for name, state in profiles.items():
            enabled = [k for k, v in state.items() if v]
            disabled = [k for k, v in state.items() if not v]
            items.append({"name": name, "enabled_count": len(enabled),
                          "disabled_count": len(disabled), "enabled": enabled})
        emit({"command": "profile_list", "profiles": items})
        return

    if not profiles:
        print(f"{YELLOW}No saved profiles.{RESET}")
        return
    print(f"\n{BOLD}Saved profiles{RESET}\n")
    for name, state in profiles.items():
        enabled = [k.split("@")[0] for k, v in state.items() if v]
        disabled_count = sum(1 for v in state.values() if not v)
        print(f"  {CYAN}{name}{RESET} ({len(enabled)} on, {disabled_count} off)")
        if enabled:
            print(f"    {DIM}{', '.join(enabled)}{RESET}")
    print()


def cmd_extract(args: argparse.Namespace) -> None:
    """Extract a skill from a plugin into the local project .claude/skills/."""
    plugin_key = args.plugin_key
    skill_name = args.skill
    dest_name = args.dest or skill_name

    install_path = get_plugin_install_path(plugin_key)
    if not install_path:
        # Try fuzzy match: find plugin key containing the given string
        all_keys = get_installed_plugins()
        matches = [k for k in all_keys if plugin_key in k]
        if len(matches) == 1:
            plugin_key = matches[0]
            install_path = get_plugin_install_path(plugin_key)
        elif len(matches) > 1:
            if _json_mode:
                emit({"command": "extract", "error": f"ambiguous plugin: {plugin_key}",
                      "matches": matches})
            else:
                print(f"{RED}Ambiguous plugin '{plugin_key}'. Matches: {', '.join(matches)}{RESET}")
            sys.exit(1)

    if not install_path or not install_path.exists():
        if _json_mode:
            emit({"command": "extract", "error": f"plugin not found: {plugin_key}"})
        else:
            print(f"{RED}Plugin not found: {plugin_key}{RESET}")
        sys.exit(1)

    src = install_path / "skills" / skill_name
    if not src.is_dir():
        # Try fuzzy match on skill dirs
        skills_dir = install_path / "skills"
        if skills_dir.is_dir():
            matches = [d.name for d in skills_dir.iterdir() if d.is_dir() and skill_name in d.name]
            if len(matches) == 1:
                skill_name = matches[0]
                src = skills_dir / skill_name
                if not args.dest:
                    dest_name = skill_name
            elif matches:
                if _json_mode:
                    emit({"command": "extract", "error": f"ambiguous skill: {skill_name}",
                          "matches": matches})
                else:
                    print(f"{RED}Ambiguous skill '{skill_name}'. Matches: {', '.join(matches)}{RESET}")
                sys.exit(1)

    if not src.is_dir():
        if _json_mode:
            emit({"command": "extract", "error": f"skill not found: {skill_name} in {plugin_key}"})
        else:
            print(f"{RED}Skill '{skill_name}' not found in {plugin_key}{RESET}")
        sys.exit(1)

    dest = PROJECT_SKILLS / dest_name
    if dest.exists():
        if _json_mode:
            emit({"command": "extract", "error": f"already exists: {dest}",
                  "hint": "use --dest to rename, or remove first"})
        else:
            print(f"{RED}Already exists: {dest}{RESET}")
            print(f"Use --dest <name> to rename, or remove the existing one first.")
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)

    # Measure size
    total_size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())

    if _json_mode:
        emit({
            "command": "extract",
            "plugin": plugin_key,
            "skill": skill_name,
            "dest": str(dest),
            "dest_name": dest_name,
            "size_bytes": total_size,
        })
    else:
        print(f"\n{GREEN}Extracted{RESET} {plugin_key}/{skill_name} → {dest}")
        print(f"  Size: {total_size // 1024}K")
        print(f"\n{YELLOW}Now you can disable the plugin and keep this skill locally.{RESET}")


def cmd_remove_skill(args: argparse.Namespace) -> None:
    """Remove a locally extracted skill."""
    skill_name = args.skill
    dest = PROJECT_SKILLS / skill_name

    if not dest.exists():
        if _json_mode:
            emit({"command": "remove", "error": f"not found: {dest}"})
        else:
            print(f"{RED}Not found: {dest}{RESET}")
        sys.exit(1)

    # Safety: only remove from .claude/skills/, never from plugin cache
    if not str(dest.resolve()).startswith(str(Path.cwd())):
        if _json_mode:
            emit({"command": "remove", "error": "can only remove project-local skills"})
        else:
            print(f"{RED}Can only remove project-local skills.{RESET}")
        sys.exit(1)

    total_size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    shutil.rmtree(dest)

    if _json_mode:
        emit({"command": "remove", "skill": skill_name, "removed": str(dest),
              "size_bytes": total_size})
    else:
        print(f"{GREEN}Removed{RESET} {dest} ({total_size // 1024}K)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _json_mode

    parser = argparse.ArgumentParser(
        description="mctx-budget: Context budget X-ray for Claude Code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List installed plugins")

    p_toggle = sub.add_parser("toggle", help="Toggle plugin on/off")
    p_toggle.add_argument("number", help="Plugin number")
    p_toggle.add_argument("--scope", choices=["local", "project", "global"])

    sub.add_parser("status", help="Full status view")
    sub.add_parser("skills", help="List active skills")
    sub.add_parser("agents", help="List active agents")
    sub.add_parser("audit", help="Project context recommendations")

    p_extract = sub.add_parser("extract", help="Extract a skill from a plugin locally")
    p_extract.add_argument("plugin_key", help="Plugin key or partial name (e.g. compound-engineering)")
    p_extract.add_argument("skill", help="Skill directory name (e.g. ce-plan)")
    p_extract.add_argument("--dest", help="Local skill name (default: same as source)")

    p_remove = sub.add_parser("remove", help="Remove a locally extracted skill")
    p_remove.add_argument("skill", help="Skill directory name")

    p_profile = sub.add_parser("profile", help="Profile management")
    profile_sub = p_profile.add_subparsers(dest="profile_command")
    p_save = profile_sub.add_parser("save", help="Save current state as profile")
    p_save.add_argument("name", help="Profile name")
    p_load = profile_sub.add_parser("load", help="Load a profile")
    p_load.add_argument("name", help="Profile name")
    p_load.add_argument("--scope", choices=["local", "project", "global"])
    profile_sub.add_parser("list", help="List profiles")

    args = parser.parse_args()
    _json_mode = args.format == "json"

    commands = {
        "list": cmd_list,
        "toggle": cmd_toggle,
        "status": cmd_status,
        "skills": cmd_skills,
        "agents": cmd_agents,
        "audit": cmd_audit,
        "extract": cmd_extract,
        "remove": cmd_remove_skill,
    }

    if args.command in commands:
        commands[args.command](args)
    elif args.command == "profile":
        profile_cmds = {
            "save": cmd_profile_save,
            "load": cmd_profile_load,
            "list": cmd_profile_list,
        }
        if args.profile_command in profile_cmds:
            profile_cmds[args.profile_command](args)
        else:
            p_profile.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
