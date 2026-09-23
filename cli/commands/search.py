"""Command search — Search concepts and pending tasks in the vault."""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from cli.vault import find_md_files, get_scope
from cli.frontmatter import parse_frontmatter, normalize_tags


def _is_valid_frontmatter_key(key):
    return bool(key) and not str(key).startswith("#") and not str(key).startswith("- ")


def _extract_project(relative_parts):
    """Extracts the project/area from the file's relative path."""
    if len(relative_parts) <= 1:
        return "root"
    if relative_parts[0] == "clientes" and len(relative_parts) >= 3:
        return f"{relative_parts[0]}/{relative_parts[1]}"
    return relative_parts[0]


def _get_git_blame_cache(vault, relpath):
    """Runs git blame per file and returns {line_num: (age_days, date_iso)}."""
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "blame", "--line-porcelain", "--", relpath],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None

        cache = {}
        current_start = 0
        current_count = 0
        current_ts = None

        for line in result.stdout.split("\n"):
            m = re.match(r'^[0-9a-f]{40} (\d+) (\d+) (\d+)$', line)
            if m:
                if current_ts is not None and current_start > 0:
                    commit_dt = datetime.fromtimestamp(current_ts, tz=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - commit_dt).days
                    date_str = commit_dt.strftime("%Y-%m-%d")
                    for ln in range(current_start, current_start + current_count):
                        cache[ln] = (age_days, date_str)
                current_start = int(m.group(2))
                current_count = int(m.group(3))
                current_ts = None
                continue
            if line.startswith("committer-time "):
                current_ts = int(line.split()[1])

        if current_ts is not None and current_start > 0:
            commit_dt = datetime.fromtimestamp(current_ts, tz=timezone.utc)
            age_days = (datetime.now(timezone.utc) - commit_dt).days
            date_str = commit_dt.strftime("%Y-%m-%d")
            for ln in range(current_start, current_start + current_count):
                cache[ln] = (age_days, date_str)

        return cache if cache else None
    except Exception:
        return None


def find_todos(vault, include_done=False, with_aging=False, include_specs=False, include_skills=False, include_sessions=False, include_handoffs=False):
    """Finds all - [ ] and - [x] in vault files.

    By default EXCLUDES files with type: Spec — their checkboxes are
    design acceptance criteria (they define "done"), not executable
    tasks. Pass include_specs=True to include them explicitly.
    Also EXCLUDES files with type: Skill — their checkboxes are
    procedure self-audit checklists (quality criteria),
    not backlog items (decision 2026-08-06).
    Pass include_skills=True to include them explicitly.
    Also EXCLUDES files with type: Session — their checkboxes are the
    snapshot of what was pending on that date, not a live backlog: a past
    session keeps re-injecting its frozen list forever, and what it injects
    is mostly already done. Pass include_sessions=True to include them
    explicitly.
    Also EXCLUDES files with type: Handoff — their checkboxes are the
    frozen handoff state of a session, not a live backlog: they re-inject
    the same items every session until the handoff is updated. Pass
    include_handoffs=True to include them explicitly.
    """
    todos = []
    blame_cache = {}

    for md_file in find_md_files(vault):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        relpath = str(md_file.relative_to(vault))
        parts = md_file.relative_to(vault).parts
        fm, _ = parse_frontmatter(text)
        fm = fm or {}
        project = _extract_project(parts)

        # Criterios de aceptación de specs NO son tareas (decisión 2026-08-02)
        if not include_specs and fm.get("type") == "Spec":
            continue
        # Checklists de auto-auditoría de skills NO son tareas (decisión 2026-08-06)
        if not include_skills and fm.get("type") == "Skill":
            continue
        # Los checkboxes de una minuta son la foto de ese día, no backlog vivo
        if not include_sessions and fm.get("type") == "Session":
            continue
        # Los checkboxes de un handoff son estado congelado de una sesión
        if not include_handoffs and fm.get("type") == "Handoff":
            continue

        if with_aging and relpath not in blame_cache:
            blame_cache[relpath] = _get_git_blame_cache(vault, relpath)

        # Ignorar checkboxes dentro de bloques de código (```) — son ejemplos,
        # diagramas o capturas de estado, no tareas reales (fix 2026-08-02:
        # el insight embudo-invertido mostraba el checklist del plan CRM como
        # code block y el detector lo contaba como TODOs duplicados).
        in_code_block = False
        for i, line in enumerate(text.split("\n"), 1):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            m = re.match(r'^\s*- \[([ xX])\]\s+(.+)', line)
            if m:
                done = m.group(1) in ("x", "X")
                if done and not include_done:
                    continue
                entry = {
                    "file": relpath,
                    "project": project,
                    "line": i,
                    "done": done,
                    "text": m.group(2).strip(),
                    "context": fm.get("title", fm.get("description", relpath)),
                }
                if with_aging:
                    file_cache = blame_cache.get(relpath)
                    if file_cache and i in file_cache:
                        age_days, age_date = file_cache[i]
                        entry["age_days"] = age_days
                        entry["age_date"] = age_date
                        entry["age_status"] = "fresh" if age_days <= 3 else (
                            "aging" if age_days <= 7 else "stale")
                    else:
                        entry["age_days"] = None
                        entry["age_date"] = None
                        entry["age_status"] = "unknown"
                todos.append(entry)

    return todos


def find_agent_bus_signals():
    """Scans signals/→default/ in Hermes profiles (if they exist)."""
    agent_bus_base = Path.home() / ".hermes" / "profiles"
    signals_dir_suffix = Path("agent-bus") / "signals" / "→default"
    signals = []

    if not agent_bus_base.exists():
        return signals

    for profile_dir in sorted(agent_bus_base.iterdir()):
        if not profile_dir.is_dir():
            continue
        sd = profile_dir / signals_dir_suffix
        if not sd.exists():
            continue

        profile_name = profile_dir.name
        for signal_file in sorted(sd.glob("*.md")):
            if signal_file.name == ".gitkeep":
                continue
            try:
                text = signal_file.read_text(encoding="utf-8")
            except Exception:
                continue

            fm, _ = parse_frontmatter(text)
            fm = fm or {}
            from_profile = fm.get("from", profile_name)
            ticket_ref = fm.get("ticket_ref", "")
            urgency = fm.get("urgency", "normal")
            signal_type = fm.get("type", "notification")

            ref_str = f" — {ticket_ref}" if ticket_ref else ""
            urgency_icon = "🔴" if urgency == "escalated" else "📬"

            signals.append({
                "file": f"profiles/{profile_name}/agent-bus/signals/→default/{signal_file.name}",
                "project": f"agent-bus/{profile_name}",
                "line": 0,
                "done": False,
                "text": f"{urgency_icon} [{from_profile}] {signal_type}{ref_str}",
                "context": f"[{profile_name}] {signal_file.name}",
            })

    return signals


def _sanitize_cyber(cyber):
    """Converts date/datetime objects to strings for JSON."""
    if cyber is None or not isinstance(cyber, dict):
        return None
    result = {}
    for k, v in cyber.items():
        if isinstance(v, (datetime,)):
            result[k] = v.isoformat() if hasattr(v, 'isoformat') else str(v)
        elif hasattr(v, 'isoformat'):
            result[k] = v.isoformat()
        elif isinstance(v, dict):
            result[k] = {
                sk: sv.isoformat() if hasattr(sv, 'isoformat') else sv
                for sk, sv in v.items()
            }
        elif isinstance(v, list):
            result[k] = [x.isoformat() if hasattr(x, 'isoformat') else x for x in v]
        else:
            result[k] = v
    return result


def find_concepts(vault):
    """Finds all concepts with frontmatter in the vault."""
    concepts = []
    for md_file in find_md_files(vault):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        fm, _ = parse_frontmatter(text)
        if not fm:
            continue

        relpath = str(md_file.relative_to(vault))
        parts = md_file.relative_to(vault).parts
        folder = _extract_project(parts)

        tags = normalize_tags(fm.get("tags", []))

        concepts.append({
            "file": relpath,
            "folder": folder,
            "type": str(fm.get("type", "")),
            "title": str(fm.get("title", "")),
            "status": str(fm.get("status", "")),
            "description": str(fm.get("description", "")),
            "tags": tags,
            "resource": str(fm.get("resource", "")),
            "timestamp": str(fm.get("timestamp", "")),
            "cyber": _sanitize_cyber(fm.get("cyber")),
        })

    return concepts


def matches_query(concept, query):
    """Checks if a concept matches the query (AND, case-insensitive)."""
    tokens = query.lower().split()
    if not tokens:
        return True

    searchable = " ".join([
        concept["title"],
        concept["description"],
        " ".join(concept["tags"]) if isinstance(concept["tags"], list) else "",
    ]).lower()

    for token in tokens:
        if ":" in token and not token.startswith("http"):
            field, value = token.split(":", 1)
            field = field.strip()
            value = value.strip()

            if "." in field:
                parent, child = field.split(".", 1)
                if parent == "cyber":
                    cyber = concept.get("cyber")
                    if not isinstance(cyber, dict):
                        return False
                    child_parts = child.split(".")
                    val = cyber
                    for part in child_parts:
                        if isinstance(val, dict):
                            val = val.get(part)
                        else:
                            val = None
                            break
                    if val is None:
                        return False
                    if str(val).lower() != value:
                        return False
                    continue
                return False

            if field in ("type", "status", "title"):
                if concept.get(field, "").lower() != value:
                    return False
                continue

        if token not in searchable:
            return False

    return True


def _print_todos(todos, with_aging=False):
    """Prints pending tasks grouped by project."""
    if not todos:
        print("(no pending tasks)")
        return

    by_project = {}
    for t in todos:
        by_project.setdefault(t.get("project", t["file"]), []).append(t)

    total = len(todos)
    stale_count = sum(1 for t in todos if t.get("age_status") == "stale")
    aging_count = sum(1 for t in todos if t.get("age_status") == "aging")

    status_line = f"📋 Pending tasks ({total} found)"
    if with_aging:
        parts = []
        if stale_count:
            parts.append(f"🔴 {stale_count} >7d")
        if aging_count:
            parts.append(f"🟡 {aging_count} 3-7d")
        if parts:
            status_line += " — " + ", ".join(parts)
    print(status_line)
    print("─" * 60)

    for project, items in sorted(by_project.items()):
        context = items[0]["context"]
        if context and context != items[0]["file"]:
            print(f"\n📁 {project}  — {context}")
        else:
            print(f"\n📁 {project}")

        for item in items:
            prefix = ""
            if with_aging and item.get("age_days") is not None:
                days = item["age_days"]
                if days <= 3:
                    prefix = "🟢 "
                elif days <= 7:
                    prefix = "🟡 "
                else:
                    prefix = "🔴 "
                age_info = f" ({days}d)" if days > 0 else " (hoy)"
            elif with_aging:
                prefix = "⚪ "
                age_info = " (?)"
            else:
                age_info = ""

            marker = "☑" if item.get("done") else "☐"
            print(f"  {prefix}{marker} {item['text']}{age_info}")

    print(f"\n─ {total} task(s) in {len(by_project)} project(s)")


def _print_table(concepts):
    """Prints a formatted text table."""
    if not concepts:
        print("(no results)")
        return

    cols = {
        "file": max(len(c["file"]) for c in concepts),
        "type": max(max(len(c["type"]) for c in concepts), 4),
        "status": max(max(len(c["status"]) for c in concepts), 6),
    }

    header = (
        f"{'FILE':<{cols['file']}}  "
        f"{'TYPE':<{cols['type']}}  "
        f"{'STATUS':<{cols['status']}}  "
        f"DESCRIPTION"
    )
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)

    for c in concepts:
        desc = c["description"][:80]
        prefix = "⚠ " if c["status"] == "propuesta" else "  "
        print(
            f"{prefix}{c['file']:<{cols['file']}}  "
            f"{c['type']:<{cols['type']}}  "
            f"{c['status']:<{cols['status']}}  "
            f"{desc}"
        )

    print(sep)
    print(f"{len(concepts)} concept(s)")


def _find_typed_edges(concepts, vault):
    """Finds typed edges between result concepts."""
    result_paths = {c["file"] for c in concepts}
    edges = []

    for c in concepts:
        filepath = vault / c["file"]
        try:
            text = filepath.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if not fm:
            continue
        links = fm.get("links")
        if not links or not isinstance(links, list):
            continue
        for link in links:
            if not isinstance(link, dict):
                continue
            target = link.get("target", "")
            # Normalizar: quitar .md si existe
            target = target.replace(".md", "")
            edge_type = link.get("type", "wikilink")
            # Ver si el target está en los resultados (con o sin .md)
            if target in result_paths or f"{target}.md" in result_paths:
                edges.append((c["file"], target, edge_type))

    return edges


def _print_typed_edges(edges):
    """Prints detected relationships section."""
    if not edges:
        return
    print()
    print("## Detected relationships")
    for source, target, etype in edges:
        print(f"  {source}")
        print(f"    └─ {etype} → {target}")


def run(args, vault, config=None):
    """Runs concept or task search."""
    json_out = getattr(args, "json", False)
    todos_mode = getattr(args, "todos", False)
    include_all = getattr(args, "all", False)
    with_aging = getattr(args, "aging", False)
    query = getattr(args, "query", None)
    filter_type = getattr(args, "filter_type", None)
    filter_status = getattr(args, "filter_status", None)
    cyber_field = getattr(args, "cyber_field", None)
    cyber_value = getattr(args, "cyber_value", None)
    review_due = getattr(args, "review_due", False)
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)

    with_graph = getattr(args, "with_graph", False)

    if with_aging and not todos_mode:
        print("Error: --aging only works with --todos", file=sys.stderr)
        return 1
    if include_all and not todos_mode:
        print("Error: --all only works with --todos", file=sys.stderr)
        return 1
    if cyber_value and not cyber_field:
        print("Error: --cyber-value requires --cyber-field", file=sys.stderr)
        return 1

    if todos_mode:
        include_specs = getattr(args, "include_specs", False)
        include_skills = getattr(args, "include_skills", False)
        include_sessions = getattr(args, "include_sessions", False)
        include_handoffs = getattr(args, "include_handoffs", False)
        todos = find_todos(vault, include_done=include_all, with_aging=with_aging, include_specs=include_specs, include_skills=include_skills, include_sessions=include_sessions, include_handoffs=include_handoffs)
        # El agent-bus vive fuera del vault (~/.hermes/profiles/…): nunca en una
        # instancia acotada a un subárbol (agente público).
        if not get_scope():
            todos.extend(find_agent_bus_signals())
        if json_out:
            print(json.dumps(todos, ensure_ascii=False, indent=2))
        else:
            _print_todos(todos, with_aging=with_aging)
        return 0

    concepts = find_concepts(vault)

    if query and query.strip():
        concepts = [c for c in concepts if matches_query(c, query)]
    if filter_type:
        concepts = [c for c in concepts if c["type"].lower() == filter_type.lower()]
    if filter_status:
        concepts = [c for c in concepts if c["status"].lower() == filter_status.lower()]

    if cyber_field:
        field_path = cyber_field.split(".")
        value = (cyber_value or "").lower()
        filtered = []
        for c in concepts:
            cyber = c.get("cyber")
            if not isinstance(cyber, dict):
                continue
            val = cyber
            for part in field_path:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break
            if val is not None and str(val).lower() == value:
                filtered.append(c)
        concepts = filtered

    if review_due:
        from datetime import timedelta
        today_str = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")  # Colombia UTC-5
        filtered = []
        for c in concepts:
            cyber = c.get("cyber")
            if not isinstance(cyber, dict):
                continue
            review_on = cyber.get("review_on")
            if review_on and str(review_on) <= today_str:
                filtered.append(c)
        concepts = filtered

    if since or until:
        try:
            since_dt = datetime.fromisoformat(since) if since else None
            until_dt = datetime.fromisoformat(until) if until else None
            # Asegurar timezone-aware: si la entrada es naive, asumir UTC
            if since_dt and since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            if until_dt and until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
        except ValueError as e:
            print(f"Error: invalid date — {e}", file=sys.stderr)
            return 1
        filtered = []
        for c in concepts:
            ts_raw = c.get("timestamp", "")
            if not ts_raw:
                continue
            try:
                ts_raw = ts_raw.strip().strip('"').strip("'")
                ts_dt = datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            # Normalizar a UTC para comparación
            if ts_dt.tzinfo is not None:
                ts_dt = ts_dt.astimezone(timezone.utc)
            else:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            if since_dt and ts_dt < since_dt:
                continue
            if until_dt and ts_dt > until_dt:
                continue
            filtered.append(c)
        concepts = filtered

    if json_out:
        print(json.dumps(concepts, ensure_ascii=False, indent=2))
    else:
        _print_table(concepts)
        if with_graph and not todos_mode:
            edges = _find_typed_edges(concepts, vault)
            if edges:
                _print_typed_edges(edges)
            else:
                print("\n(no typed edges among results)")

    return 0
