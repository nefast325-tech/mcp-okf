"""Command trace — Trace references across all layers of the OKF ecosystem.

Searches a query in:
    1. vault     — wikilinks (backlinks) + content (grep)
    2. code      — ~/.hermes/mcp-servers/okf/**/*.py
    3. hooks     — el directorio de hooks de git (con core.hooksPath y en worktrees)
    4. cron      — sistema/cron/* + sistema/hermes-cron-jobs/*
    5. agents    — AGENTS.md, CLAUDE.md, .claude/*.md

Usage: python3 -m cli trace <query> [--layers vault,code,hooks,cron,agents]
"""

import sys
from pathlib import Path

# Available layers
LAYERS = ["vault", "code", "hooks", "cron", "agents"]


def _grep_dir(directory: Path, pattern: str, glob: str = "*") -> list[tuple[str, int, str]]:
    """Searches pattern in files of a directory. Returns [(path, line_num, line), ...]."""
    results = []
    if not directory.exists():
        return results
    for f in sorted(directory.rglob(glob)):
        if not f.is_file() or f.name.endswith(".pyc"):
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.lower() in line.lower():
                    results.append((str(f), i, line.strip()))
        except Exception:
            continue
    return results


def _grep_file(filepath: Path, pattern: str) -> list[tuple[str, int, str]]:
    """Searches pattern in a file. Returns [(path, line_num, line), ...]."""
    results = []
    if not filepath.exists():
        return results
    try:
        for i, line in enumerate(filepath.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pattern.lower() in line.lower():
                results.append((str(filepath), i, line.strip()))
    except Exception:
        pass
    return results


def _search_vault_wikilinks(vault, query):
    """Searches wikilinks and backlinks containing query."""
    from cli.commands.graph import build_graph
    graph = build_graph(vault)
    results = []
    qlower = query.lower()
    for src, data in graph.items():
        # Wikilinks salientes
        for tgt in data.get("out", []):
            if qlower in tgt.lower():
                results.append(f"  {src} → [[{tgt}]]")
        # Backlinks entrantes
        for bl in data.get("in", []):
            if qlower in src.lower():
                results.append(f"  {bl} → [[{src}]]")
    return results


def _search_vault_content(vault, query):
    """Searches query in .md files of the vault (excluding index.md, log.md)."""
    results = []
    for f in sorted(vault.rglob("*.md")):
        if f.name in ("index.md", "log.md", "dashboard.md"):
            continue
        if ".git" in f.parts or ".obsidian" in f.parts:
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if query.lower() in line.lower():
                    rel = str(f.relative_to(vault))
                    results.append((rel, i, line.strip()))
        except Exception:
            continue
    return results


def run(args, vault, config=None):
    """Traces references to a query across the OKF ecosystem layers."""
    query = getattr(args, "query", None)
    if not query:
        print("Usage: python3 -m cli trace <query> [--layers vault,code,hooks,cron,agents]", file=sys.stderr)
        return 1

    layers_str = getattr(args, "layers", "vault,code,hooks,cron,agents")
    active_layers = [l.strip() for l in layers_str.split(",") if l.strip() in LAYERS]

    mcp_dir = Path(__file__).resolve().parent.parent.parent  # ~/.hermes/mcp-servers/okf/
    vault_root = vault

    for layer in active_layers:
        print(f"\n─── {layer} ───")

        if layer == "vault":
            # Wikilinks
            wl_results = _search_vault_wikilinks(vault_root, query)
            if wl_results:
                print("  [wikilinks]")
                for r in wl_results[:30]:
                    print(r)
                if len(wl_results) > 30:
                    print(f"  ... +{len(wl_results) - 30} more")

            # Contenido
            content_results = _search_vault_content(vault_root, query)
            if content_results:
                print("  [content]")
                for path, line_num, line in content_results[:30]:
                    print(f"  {path}:{line_num}  {line[:120]}")
                if len(content_results) > 30:
                    print(f"  ... +{len(content_results) - 30} more")

            if not wl_results and not content_results:
                print("  (no results)")

        elif layer == "code":
            results = _grep_dir(mcp_dir, query, "*.py")
            if results:
                for path, line_num, line in results[:30]:
                    # Mostrar path relativo al MCP dir
                    try:
                        rel = str(Path(path).relative_to(mcp_dir))
                    except ValueError:
                        rel = path
                    print(f"  {rel}:{line_num}  {line[:120]}")
                if len(results) > 30:
                    print(f"  ... +{len(results) - 30} more")
            else:
                print("  (no results)")

        elif layer == "hooks":
            # El directorio que git usa de verdad (core.hooksPath, worktrees) —
            # la misma resolución que health (fix #30). Hardcodear `.git/hooks`
            # dejaba este layer ciego en los vaults activados con hooksPath.
            from cli.commands.health import _hooks_dir
            hooks_dir = _hooks_dir(vault_root)
            results = _grep_dir(hooks_dir, query, "*")
            if results:
                for path, line_num, line in results:
                    print(f"  {Path(path).name}:{line_num}  {line[:120]}")
            else:
                print("  (no results)")

        elif layer == "cron":
            cron_dirs = [
                vault_root / "sistema" / "cron",
                vault_root / "sistema" / "hermes-cron-jobs",
            ]
            found = False
            for cron_dir in cron_dirs:
                results = _grep_dir(cron_dir, query, "*.md")
                for path, line_num, line in results:
                    found = True
                    try:
                        rel = str(Path(path).relative_to(vault_root))
                    except ValueError:
                        rel = path
                    print(f"  {rel}:{line_num}  {line[:120]}")
            if not found:
                print("  (no results)")

        elif layer == "agents":
            agent_files = [
                vault_root / "AGENTS.md",
                vault_root / "CLAUDE.md",
            ]
            # También ~/.claude/CLAUDE.md
            home_claude = Path.home() / ".claude" / "CLAUDE.md"
            if home_claude.exists():
                agent_files.append(home_claude)
            found = False
            for f in agent_files:
                results = _grep_file(f, query)
                for path, line_num, line in results:
                    found = True
                    print(f"  {Path(path).name}:{line_num}  {line[:120]}")
            if not found:
                print("  (no results)")

    print()
    return 0
