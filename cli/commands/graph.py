"""Command graph — Analyze the wikilink graph of the OKF vault.

Subcommands:
    stats, orphans, hubs, backlinks, deps, tags, bridges, cluster, path,
    dump, dirs, types, suggest-edge-types
"""

import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from cli.vault import find_md_files, EXCLUDE_FILES
from cli.wikilinks import extract_links, resolve_link
from cli.frontmatter import extract_tags, extract_typed_links, parse_frontmatter


def build_graph(vault, definitions=None):
    """Builds directed graph with typed and untyped edges, including scores.

    Args:
        vault: Path to vault root.
        definitions: Optional config-provided edge type definitions. If None,
                     uses the embedded defaults (cli.edge_types).

    Returns:
        {"relpath.md": {
            "out": [...], "in": [...],
            "typed_out": [{"target": "...", "type": "extiende", "score": 0.85}, ...],
            "typed_in": [{"target": "...", "type": "extiende", "score": 0.85}, ...],
        }}
    """
    from cli.edge_types import score_edge

    all_files = find_md_files(vault)
    name_index = {f.name: str(f.relative_to(vault)) for f in all_files}
    graph = {}

    for f in all_files:
        relpath = str(f.relative_to(vault))
        graph[relpath] = {"out": [], "in": [], "typed_out": [], "typed_in": [],
                          "leaf": False}

    # ── Cache de frontmatter por archivo ──
    fm_cache = {}
    for f in all_files:
        relpath = str(f.relative_to(vault))
        try:
            text = f.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
            fm_cache[relpath] = fm if fm else {}
            if isinstance(fm, dict) and fm.get("leaf") is True:
                graph[relpath]["leaf"] = True
        except Exception:
            fm_cache[relpath] = {}

    # ── Primera pasada: construir aristas ──
    typed_edges_raw = []  # (source, target, edge_type)

    for f in all_files:
        relpath = str(f.relative_to(vault))

        # --- Wikilinks (existente) ---
        links = extract_links(f)
        for target in links:
            resolved = resolve_link(target, vault, f.parent, name_index)
            if resolved and resolved in graph and resolved != relpath:
                graph[relpath]["out"].append(resolved)
                graph[resolved]["in"].append(relpath)

        # --- Aristas tipadas desde frontmatter ---
        typed_links = extract_typed_links(f, fm=fm_cache[relpath])
        for tl in typed_links:
            target_raw = tl["target"]
            edge_type = tl["type"]
            resolved = resolve_link(target_raw, vault, f.parent, name_index)
            if resolved and resolved in graph and resolved != relpath:
                typed_edges_raw.append((relpath, resolved, edge_type))

    # ── Calcular precedentes: count por (source_type, target_type, edge_type) ──
    precedent_counts = defaultdict(int)
    for src, tgt, etype in typed_edges_raw:
        src_type = str(fm_cache.get(src, {}).get("type", "?"))
        tgt_type = str(fm_cache.get(tgt, {}).get("type", "?"))
        precedent_counts[(src_type, tgt_type, etype)] += 1

    # ── Segunda pasada: insertar aristas con score ──
    for src, tgt, etype in typed_edges_raw:
        src_fm = fm_cache.get(src, {})
        tgt_fm = fm_cache.get(tgt, {})

        src_type = str(src_fm.get("type", "?"))
        tgt_type = str(tgt_fm.get("type", "?"))

        # Precedent ratio: (count - 1) / max(count, 1)  — sin contar esta arista
        total = precedent_counts.get((src_type, tgt_type, etype), 0)
        precedent = (total - 1) / max(total, 1) if total > 0 else 0.0

        # Tags: usar normalize_tags para manejar str/list
        src_tags = src_fm.get("tags", [])
        tgt_tags = tgt_fm.get("tags", [])
        if isinstance(src_tags, str):
            src_tags = [t.strip() for t in src_tags.strip("[]").split(",") if t.strip()]
        if isinstance(tgt_tags, str):
            tgt_tags = [t.strip() for t in tgt_tags.strip("[]").split(",") if t.strip()]
        if not isinstance(src_tags, list):
            src_tags = []
        if not isinstance(tgt_tags, list):
            tgt_tags = []

        edge_score = score_edge(
            source_type=src_type,
            target_type=tgt_type,
            edge_type=etype,
            source_tags=src_tags,
            target_tags=tgt_tags,
            source_desc=str(src_fm.get("description", "")),
            target_desc=str(tgt_fm.get("description", "")),
            precedent_ratio=precedent,
            definitions=definitions,
        )

        graph[src]["typed_out"].append({
            "target": tgt,
            "type": etype,
            "score": edge_score,
        })
        graph[tgt]["typed_in"].append({
            "target": src,
            "type": etype,
            "score": edge_score,
        })

    # Deduplicar
    for node in graph:
        graph[node]["out"] = sorted(set(graph[node]["out"]))
        graph[node]["in"] = sorted(set(graph[node]["in"]))

        seen_out = set()
        unique_out = []
        for entry in graph[node]["typed_out"]:
            key = (entry["target"], entry["type"])
            if key not in seen_out:
                seen_out.add(key)
                unique_out.append(entry)
        graph[node]["typed_out"] = sorted(
            unique_out, key=lambda x: (x["target"], x["type"])
        )

        seen_in = set()
        unique_in = []
        for entry in graph[node]["typed_in"]:
            key = (entry["target"], entry["type"])
            if key not in seen_in:
                seen_in.add(key)
                unique_in.append(entry)
        graph[node]["typed_in"] = sorted(
            unique_in, key=lambda x: (x["target"], x["type"])
        )

    return graph


def build_tag_index(vault):
    """Builds tag → files index."""
    all_files = find_md_files(vault)
    tag_index = defaultdict(list)
    for f in all_files:
        relpath = str(f.relative_to(vault))
        for tag in extract_tags(f):
            tag_index[tag].append(relpath)
    return dict(tag_index)


def _resolve_name(filename, graph):
    """Resolves partial name to full name in the graph."""
    if filename in graph:
        return filename
    matches = [n for n in graph if filename in n]
    if len(matches) == 1:
        return matches[0]
    if matches:
        print(f"Matches for '{filename}':", file=sys.stderr)
        for m in matches[:10]:
            print(f"  {m}", file=sys.stderr)
    return None


def _cmd_stats(graph, tag_index):
    nodes = len(graph)
    wikilinks = sum(len(d["out"]) for d in graph.values())
    typed = sum(len(d["typed_out"]) for d in graph.values())

    lines = [
        f"Nodes: {nodes}",
        f"Edges (wikilinks): {wikilinks}",
        f"Edges (typed): {typed}",
        f"Total edges: {wikilinks + typed}",
    ]
    edges = wikilinks + typed
    density = edges / max(nodes * (nodes - 1), 1)
    lines.append(f"Density: {density:.3f}")

    max_out = max(graph.items(), key=lambda x: len(x[1]["out"]))
    max_in = max(graph.items(), key=lambda x: len(x[1]["in"]))
    max_typed_out = max(graph.items(), key=lambda x: len(x[1]["typed_out"]))
    max_typed_in = max(graph.items(), key=lambda x: len(x[1]["typed_in"]))

    lines.append(f"Max out-degree (wikilinks): {max_out[0]} ({len(max_out[1]['out'])})")
    lines.append(f"Max in-degree (wikilinks):  {max_in[0]} ({len(max_in[1]['in'])})")
    if typed > 0:
        lines.append(f"Max typed-out: {max_typed_out[0]} ({len(max_typed_out[1]['typed_out'])})")
        lines.append(f"Max typed-in:  {max_typed_in[0]} ({len(max_typed_in[1]['typed_in'])})")

    huérfanos = sum(1 for n, d in graph.items()
                    if not d["in"] and not d["out"]
                    and not d["typed_in"] and not d["typed_out"]
                    and not n.startswith("agentes/")
                    and not n.startswith("sesiones/"))
    lines.append(f"Orphans: {huérfanos}")

    if tag_index is not None:
        total_tags = len(tag_index)
        shared_tags = sum(1 for t, files in tag_index.items() if len(files) >= 2)
        lines.append(f"Total tags: {total_tags}")
        lines.append(f"Shared tags (≥2 files): {shared_tags}")
    return "\n".join(lines)


def _cmd_orphans(graph):
    orphans = [n for n, d in graph.items()
               if not d["in"] and not d["out"]
               and not d["typed_in"] and not d["typed_out"]
               and not n.startswith("agentes/")
               and not n.startswith("sesiones/")]
    if not orphans:
        return "No orphan concepts."
    lines = [f"{len(orphans)} concept(s) without links:"]
    for o in orphans:
        lines.append(f"  {o}")
    return "\n".join(lines)


def _cmd_hubs(graph):
    # Rank by total incoming (wikilinks + typed)
    ranked = sorted(
        graph.items(),
        key=lambda x: len(x[1]["in"]) + len(x[1]["typed_in"]),
        reverse=True,
    )
    lines = ["Top most referenced concepts:"]
    for node, data in ranked[:10]:
        count = len(data["in"]) + len(data["typed_in"])
        if count == 0:
            break
        lines.append(f"  [{count}] {node}")
    return "\n".join(lines)


def _cmd_backlinks(graph, filename, edge_type=None):
    resolved = _resolve_name(filename, graph)
    if resolved is None:
        return f"Not found: {filename}"

    incoming = graph[resolved]["in"]
    typed_incoming = [
        e["target"] for e in graph[resolved]["typed_in"]
        if edge_type is None or e["type"] == edge_type
    ]

    if edge_type:
        all_incoming = sorted(set(typed_incoming))
    else:
        all_incoming = sorted(set(incoming + typed_incoming))

    if not all_incoming:
        filter_msg = f" (filtered by type: {edge_type})" if edge_type else ""
        return f"No one references {resolved}{filter_msg}"

    filter_msg = f" [edge_type={edge_type}]" if edge_type else ""
    lines = [f"← Backlinks for {resolved}{filter_msg}:"]
    for src in all_incoming:
        # Anotar tipos de arista tipada con score
        typed_entries = [
            f"{e['type']}:{e.get('score', '?')}" for e in graph[resolved]["typed_in"]
            if e["target"] == src
        ]
        type_str = f" [{', '.join(typed_entries)}]" if typed_entries else ""
        lines.append(f"  {src}{type_str}")
    return "\n".join(lines)


def _cmd_deps(graph, filename, edge_type=None):
    resolved = _resolve_name(filename, graph)
    if resolved is None:
        return f"Not found: {filename}"

    outgoing = graph[resolved]["out"]
    typed_outgoing = [
        e["target"] for e in graph[resolved]["typed_out"]
        if edge_type is None or e["type"] == edge_type
    ]

    if edge_type:
        all_outgoing = sorted(set(typed_outgoing))
    else:
        all_outgoing = sorted(set(outgoing + typed_outgoing))

    if not all_outgoing:
        filter_msg = f" (filtered by type: {edge_type})" if edge_type else ""
        return f"{resolved} references no one{filter_msg}"

    filter_msg = f" [edge_type={edge_type}]" if edge_type else ""
    lines = [f"{resolved} → references{filter_msg}:"]
    for tgt in all_outgoing:
        typed_entries = [
            f"{e['type']}:{e.get('score', '?')}" for e in graph[resolved]["typed_out"]
            if e["target"] == tgt
        ]
        type_str = f" [{', '.join(typed_entries)}]" if typed_entries else ""
        lines.append(f"  {tgt}{type_str}")
    return "\n".join(lines)


def _cmd_path(graph, origin, dest):
    for name, target in [(origin, "origin"), (dest, "dest")]:
        resolved = _resolve_name(name, graph)
        if resolved is None:
            return f"Not found: {name}"
        if target == "origin":
            origin = resolved
        else:
            dest = resolved

    if origin not in graph or dest not in graph:
        return "Origin or destination not found."

    queue = deque([(origin, [origin])])
    visited = {origin}

    while queue:
        current, path = queue.popleft()
        if current == dest:
            return " → ".join(path)
        # Consider both wikilinks and typed edges
        neighbors = list(graph[current]["out"])
        neighbors += [e["target"] for e in graph[current]["typed_out"]]
        for neighbor in neighbors:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return f"No path from {origin} to {dest}"


def _cmd_cluster(graph):
    # Include both wikilinks and typed edges in undirected graph
    undirected = defaultdict(set)
    for node, data in graph.items():
        for tgt in data["out"]:
            undirected[node].add(tgt)
            undirected[tgt].add(node)
        for entry in data["typed_out"]:
            undirected[node].add(entry["target"])
            undirected[entry["target"]].add(node)

    visited = set()
    clusters = []

    for node in graph:
        if node in visited:
            continue
        component = []
        queue = deque([node])
        visited.add(node)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in undirected.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        clusters.append(sorted(component))

    clusters.sort(key=len, reverse=True)
    lines = [f"{len(clusters)} connected component(s):"]
    for i, cluster in enumerate(clusters, 1):
        lines.append(f"\n  Group {i} ({len(cluster)} concepts):")
        for node in cluster:
            out_deg = len(graph[node]["out"]) + len(graph[node]["typed_out"])
            in_deg = len(graph[node]["in"]) + len(graph[node]["typed_in"])
            lines.append(f"    {node}  (→{out_deg} ←{in_deg})")
    return "\n".join(lines)


def _cmd_tags(graph, tag_index, tag_name):
    if tag_name:
        if tag_name not in tag_index:
            return f"Tag not found: {tag_name}"
        files = tag_index[tag_name]
        lines = [f"🏷️  {tag_name} ({len(files)} file(s)):"]
        for f in files:
            other_tags = [t for t, fl in tag_index.items() if f in fl and t != tag_name]
            extra = f"  [+ {', '.join(other_tags)}]" if other_tags else ""
            lines.append(f"   - {f}{extra}")
        return "\n".join(lines)

    sorted_tags = sorted(tag_index.items(), key=lambda x: (-len(x[1]), x[0]))
    lines = [f"Tags in vault ({len(tag_index)} total):\n"]
    for tag, files in sorted_tags:
        count = len(files)
        if count >= 2:
            lines.append(f"🏷️  {tag} ({count} files):")
            for f in files:
                lines.append(f"   - {f}")
        else:
            lines.append(f"   {tag} → {files[0]}")
    return "\n".join(lines)


def _cmd_bridges(graph, tag_index):
    undirected = defaultdict(set)
    for node, data in graph.items():
        for tgt in data["out"]:
            undirected[node].add(tgt)
            undirected[tgt].add(node)
        for entry in data["typed_out"]:
            undirected[node].add(entry["target"])
            undirected[entry["target"]].add(node)

    visited = set()
    clusters = []
    cluster_map = {}

    for node in graph:
        if node in visited:
            continue
        component = set()
        queue = deque([node])
        visited.add(node)
        while queue:
            current = queue.popleft()
            component.add(current)
            for neighbor in undirected.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        cid = len(clusters)
        clusters.append(component)
        for n in component:
            cluster_map[n] = cid

    lines = []
    for tag, files in sorted(tag_index.items(), key=lambda x: -len(x[1])):
        if len(files) < 2:
            continue
        cluster_ids = {cluster_map[f] for f in files if f in cluster_map}
        if len(cluster_ids) >= 2:
            lines.append(f"\n🌉  {tag} ({len(files)} files) — spans {len(cluster_ids)} clusters:")
            for f in files:
                cid = cluster_map.get(f, "?")
                lines.append(f"   [{cid}] {f}")

    if not lines:
        return "No tags span clusters."
    return "Bridge tags between wikilink clusters:\n" + "\n".join(lines)


def _cmd_dirs(vault):
    """Directory tree with concept file counts."""
    from collections import defaultdict

    all_files = find_md_files(vault)
    dir_counts: dict[str, int] = defaultdict(int)
    for f in all_files:
        rel = f.relative_to(vault)
        parent = str(rel.parent) if str(rel.parent) != "." else "(root)"
        dir_counts[parent] += 1

    prefix_tree: dict[str, dict] = {}
    for d in sorted(dir_counts):
        parts = d.split("/")
        node = prefix_tree
        for part in parts:
            if part not in node:
                node[part] = {}
            node = node[part]

    lines = [f"Vault directories ({len(dir_counts)} folders with concepts):\n"]

    def _render(node, path_parts=(), depth=0):
        items = sorted(node.items())
        for i, (name, children) in enumerate(items):
            is_last = (i == len(items) - 1)
            connector = "└── " if is_last else "├── "
            full_path = "/".join(path_parts + (name,))

            count = dir_counts.get(full_path, 0)
            if count == 0 and depth == 0:
                count = dir_counts.get("(root)", 0)

            indent = "    " * depth
            lines.append(f"{indent}{connector}{name}/ ({count})")
            if children:
                _render(children, path_parts + (name,), depth + 1)

    _render(prefix_tree)
    return "\n".join(lines)


def _cmd_types(vault):
    """Distribution of concepts by type (frontmatter)."""
    from collections import Counter

    all_files = find_md_files(vault)
    type_counts: Counter[str] = Counter()
    missing: list[str] = []

    for f in all_files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            missing.append(str(f.relative_to(vault)))
            continue
        fields, _ = parse_frontmatter(text)
        if fields and fields.get("type"):
            type_counts[str(fields["type"])] += 1
        else:
            missing.append(str(f.relative_to(vault)))

    if not type_counts and not missing:
        return "No concepts with frontmatter found."

    lines = [f"Distribution by type ({sum(type_counts.values())} concepts with type, {len(missing)} without type):\n"]

    max_count = max(type_counts.values()) if type_counts else 1
    for t, count in type_counts.most_common():
        bar = "█" * max(1, int(20 * count / max_count))
        lines.append(f"  {t:<20} {count:>4}  {bar}")

    if missing:
        lines.append(f"\nWithout type ({len(missing)}):")
        for m in missing[:15]:
            lines.append(f"  - {m}")
        if len(missing) > 15:
            lines.append(f"  ... and {len(missing) - 15} more")

    return "\n".join(lines)


def _cmd_dump(graph):
    lines = []
    for node in sorted(graph.keys()):
        data = graph[node]
        out = ", ".join(data["out"]) or "—"
        incoming = ", ".join(data["in"]) or "—"
        typed_out_str = ", ".join(
            f"{e['target']}[{e['type']}:{e.get('score', '?')}]" for e in data["typed_out"]
        ) or "—"
        typed_in_str = ", ".join(
            f"{e['target']}[{e['type']}:{e.get('score', '?')}]" for e in data["typed_in"]
        ) or "—"
        lines.append(f"{node}")
        lines.append(f"  → {out}")
        lines.append(f"  ← {incoming}")
        lines.append(f"  ⇒ {typed_out_str}")
        lines.append(f"  ⇐ {typed_in_str}")
        lines.append("")
    return "\n".join(lines)


def _cmd_impact(graph, filename, vault=None):
    """Ontological impact analysis: given a modified node, which other
    nodes should be reviewed according to typed edges.

    Impact direction depends on edge type:
    - X 'depende' B → B changed → X impacted (typed_in depends)
    - B 'fundamenta' X → B changed → X impacted (typed_out fundamenta)
    - X 'aplica' B → B changed → X impacted (typed_in applies)
    - X 'extiende' B → B changed → X consider review (typed_in extends)
    - X 'refina' B → B changed → possible minor impact (typed_in refines)
    - X 'corrige' B → B changed → verify the correction still applies
    """
    resolved = _resolve_name(filename, graph)
    if resolved is None:
        return f"Not found: {filename}"

    data = graph[resolved]

    # Categorizar impactos por score de arista
    blocking = []   # 🔴 score ≥ 0.7 — revisión obligatoria
    warning = []    # 🟡 score 0.4–0.69 — considerar revisión
    info = []       # 🔵 score < 0.4 — revisar si aplica

    # X depende B → typed_in con type=depende
    for entry in data.get("typed_in", []):
        source = entry["target"]
        etype = entry["type"]
        if etype != "depende":
            continue
        raw_score = entry.get("score", 0.5)  # default 0.5 si no hay score (retrocompat)
        reason = f"{source} depende de este nodo"
        if raw_score >= 0.7:
            blocking.append((source, f"{reason} [score: {raw_score}]"))
        elif raw_score >= 0.4:
            warning.append((source, f"{reason} [score: {raw_score}]"))
        else:
            info.append((source, f"{reason} [score: {raw_score}]"))

    # X aplica B → typed_in con type=aplica
    for entry in data.get("typed_in", []):
        source = entry["target"]
        etype = entry["type"]
        if etype != "aplica":
            continue
        raw_score = entry.get("score", 0.5)
        reason = f"{source} aplica este nodo"
        if raw_score >= 0.7:
            blocking.append((source, f"{reason} [score: {raw_score}]"))
        elif raw_score >= 0.4:
            warning.append((source, f"{reason} [score: {raw_score}]"))
        else:
            info.append((source, f"{reason} [score: {raw_score}]"))

    # X extiende B → typed_in con type=extiende
    for entry in data.get("typed_in", []):
        source = entry["target"]
        etype = entry["type"]
        if etype != "extiende":
            continue
        raw_score = entry.get("score", 0.5)
        reason = f"{source} extiende este nodo"
        if raw_score >= 0.7:
            warning.append((source, f"{reason} [score: {raw_score}]"))
        elif raw_score >= 0.4:
            info.append((source, f"{reason} [score: {raw_score}]"))
        else:
            info.append((source, f"{reason} (bajo score: {raw_score})"))

    # X refina B → typed_in con type=refina
    for entry in data.get("typed_in", []):
        source = entry["target"]
        etype = entry["type"]
        if etype != "refina":
            continue
        raw_score = entry.get("score", 0.5)
        reason = f"{source} refina este nodo"
        if raw_score >= 0.7:
            warning.append((source, f"{reason} [score: {raw_score}]"))
        elif raw_score >= 0.4:
            info.append((source, f"{reason} [score: {raw_score}]"))
        else:
            info.append((source, f"{reason} (bajo score: {raw_score})"))

    # X corrige B → typed_in con type=corrige
    for entry in data.get("typed_in", []):
        source = entry["target"]
        etype = entry["type"]
        if etype != "corrige":
            continue
        raw_score = entry.get("score", 0.5)
        reason = f"{source} corrects this node — is the correction still valid?"
        if raw_score >= 0.7:
            info.append((source, f"{reason} [score: {raw_score}]"))
        else:
            info.append((source, f"{reason} (bajo score: {raw_score})"))

    # B fundamenta X → typed_out con type=fundamenta
    for entry in data.get("typed_out", []):
        target = entry["target"]
        etype = entry["type"]
        if etype != "fundamenta":
            continue
        raw_score = entry.get("score", 0.5)
        reason = f"este nodo fundamenta a {target}"
        if raw_score >= 0.7:
            blocking.append((target, f"{reason} [score: {raw_score}]"))
        elif raw_score >= 0.4:
            warning.append((target, f"{reason} [score: {raw_score}]"))
        else:
            info.append((target, f"{reason} [score: {raw_score}]"))

    if not blocking and not warning and not info:
        return f"📋 {resolved}: no typed edges indicating impact. No one depends ontologically on this node."

    def _by_node(entries):
        """Agrupa (path, reason) por nodo, conservando todos sus motivos.

        Un nodo alcanzado por varias aristas es UN nodo impactado con varias
        razones, no varios nodos: el conteo cuenta nodos, no aristas.
        """
        grouped = {}
        for path, reason in entries:
            reasons = grouped.setdefault(path, [])
            if reason not in reasons:
                reasons.append(reason)
        return grouped

    lines = [f"📋 If you modify '{resolved}', also review:\n"]

    for icon, title, entries in (
        ("🔴", "MANDATORY REVIEW", blocking),
        ("🟡", "CONSIDER REVIEW", warning),
        ("🔵", "INFORMATIVE", info),
    ):
        grouped = _by_node(entries)
        if not grouped:
            continue
        lines.append(f"{icon} {title} ({len(grouped)}):")
        for path in sorted(grouped):
            lines.append(f"  {path}")
            for reason in grouped[path]:
                lines.append(f"     {reason}")
        lines.append("")

    total = len({path for path, _ in blocking + warning + info})
    lines.append(f"Total: {total} potentially impacted nodes.")

    return "\n".join(lines)


def _insert_link_entry(fm_text: str, target: str, edge_type: str) -> str:
    """Inserts a links: entry in the frontmatter without breaking nested blocks.

    Handles frontmatter with cyber: block or other nested blocks
    after links:. Inserts the entry within the existing links: block
    (if present) or creates the links: block BEFORE cyber: (if present) or at the end.

    Bug fixed 2026-08-03: naive insertion (fm_text.rstrip() + entry)
    added the entry INSIDE the cyber block when links: already existed,
    breaking the YAML (atypical pair 'expected block end, but found -').
    """
    entry = f"  - target: {target}\n    type: {edge_type}"

    # Buscar bloque links: a nivel raíz (línea que empieza con 'links:' sin indentar)
    lines = fm_text.split("\n")
    links_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^links:\s*$", line):
            links_idx = i
            break

    if links_idx is not None:
        # Encontrar el final del bloque links (próxima clave a nivel raíz)
        end_idx = len(lines)
        for j in range(links_idx + 1, len(lines)):
            # Línea no vacía y sin indentación = nueva clave raíz
            if lines[j].strip() and not lines[j].startswith((" ", "\t")):
                end_idx = j
                break
            # Línea vacía seguida de clave raíz → fin de bloque
            if not lines[j].strip():
                # Mirar si la siguiente no vacía es clave raíz
                for k in range(j + 1, len(lines)):
                    if not lines[k].strip():
                        continue
                    if not lines[k].startswith((" ", "\t")):
                        end_idx = k
                    break
                if end_idx != len(lines):
                    break
        # Insertar la entrada antes de end_idx, sin duplicar si ya existe
        for j in range(links_idx + 1, end_idx):
            if lines[j].strip() == f"- target: {target}":
                return fm_text  # ya existe — no duplicar
        lines.insert(end_idx, entry)
        return "\n".join(lines)

    # No hay links: — crear el bloque antes de cyber: (si existe) o al final
    cyber_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^cyber:\s*$", line):
            cyber_idx = i
            break
    if cyber_idx is not None:
        lines.insert(cyber_idx, "links:")
        lines.insert(cyber_idx + 1, entry)
    else:
        lines.append("links:")
        lines.append(entry)
    return "\n".join(lines)


def _cmd_suggest_edge_types(vault, graph, apply=False, dry_run=False,
                            min_score=0.0, definitions=None):
    """Suggests edge types for existing untyped wikilinks.

    Algorithm:
    1. Iterates all out edges (wikilinks) of the graph.
    2. For each edge A→B without typed coverage, gets type_A and type_B
       from frontmatter and calls suggest_edge_type().
    3. Classifies by confidence (ALTA, MEDIA, BAJA).
    4. With --apply, writes only HIGH confidence ones to frontmatter.
    5. With --min-score N, only applies/writes edges with score >= N
       (semantic scoring 0.0-1.0, scoring-semantico plan).

    Args:
        vault: Path to vault root.
        graph: Graph built with build_graph().
        apply: If True, writes HIGH-confidence suggestions to frontmatter.
        dry_run: If True, previews without writing.
        min_score: Minimum semantic score to apply (0.0 = ALTA only).
        definitions: Optional config-provided edge type definitions. If None,
                     uses the embedded defaults.
    """
    from cli.edge_types import suggest_edge_type, score_edge
    from cli.frontmatter import parse_frontmatter

    suggestions = {"ALTA": [], "MEDIA": [], "BAJA": []}

    for source_path, data in graph.items():
        if not data["out"]:
            continue

        source_file = vault / source_path
        source_type = "?"
        source_fm = None
        try:
            text = source_file.read_text(encoding="utf-8")
            source_fm, _ = parse_frontmatter(text)
            if source_fm and source_fm.get("type"):
                source_type = str(source_fm["type"])
        except Exception:
            continue

        # Aristas ya cubiertas por links: existentes
        existing_typed = {
            tl["target"] for tl in extract_typed_links(source_file)
        }

        for target_path in data["out"]:
            if target_path in existing_typed:
                continue

            target_file = vault / target_path
            target_type = "?"
            target_fm = None
            try:
                t_text = target_file.read_text(encoding="utf-8")
                t_fm, _ = parse_frontmatter(t_text)
                target_fm = t_fm
                if t_fm and t_fm.get("type"):
                    target_type = str(t_fm["type"])
            except Exception:
                pass

            suggested_type, confidence = suggest_edge_type(
                source_type, target_type, definitions=definitions
            )

            # Si la arista inversa (target → source) ya está tipada, este
            # wikilink es la referencia de vuelta: tiparlo también crea un
            # ciclo (A refina B y B refina A). No es una sugerencia segura.
            reverse_typed = source_path in {
                tl.get("target")
                for tl in graph.get(target_path, {}).get("typed_out", [])
            }

            # Cyber override: si source tiene cyber.corrects → target,
            # sugiere 'refina' (corrige/mejora sin reemplazar).
            # 'corrige' se reserva para cuando el target está explícitamente
            # deprecado o supercedido (apoptosis).
            if source_fm and isinstance(source_fm.get("cyber"), dict):
                corrects = source_fm["cyber"].get("corrects", [])
                if isinstance(corrects, str):
                    corrects = [corrects]
                for ref in (corrects or []):
                    resolved_c = resolve_link(
                        ref.strip("[]"), vault, source_file.parent
                    )
                    if resolved_c == target_path:
                        # Verificar si el target está deprecado → corrige
                        target_f = vault / target_path
                        is_deprecated = False
                        try:
                            t_text = target_f.read_text(encoding="utf-8")
                            t_fm, _ = parse_frontmatter(t_text)
                            if t_fm:
                                t_status = str(t_fm.get("status", ""))
                                t_cyber = t_fm.get("cyber")
                                if "deprec" in t_status.lower() or "superced" in t_status.lower():
                                    is_deprecated = True
                                if isinstance(t_cyber, dict) and t_cyber.get("corrected_by"):
                                    is_deprecated = True
                        except Exception:
                            pass
                        if is_deprecated:
                            suggested_type = "corrige"
                        else:
                            suggested_type = "refina"
                        confidence = "ALTA"
                        break

            # Scoring semántico (plan scoring-semantico): score 0.0-1.0
            # con 4 señales. Se calcula con el frontmatter real de ambos nodos.
            # precedent_ratio=0.0: para aristas SUGERIDAS no hay precedente
            # tipado aún (el tipo se está proponiendo), build_graph lo calcula
            # para aristas ya existentes.
            src_tags = source_fm.get("tags", []) if source_fm else []
            tgt_tags = target_fm.get("tags", []) if target_fm else []
            if isinstance(src_tags, str):
                src_tags = [t.strip() for t in src_tags.strip("[]").split(",") if t.strip()]
            if isinstance(tgt_tags, str):
                tgt_tags = [t.strip() for t in tgt_tags.strip("[]").split(",") if t.strip()]
            if not isinstance(src_tags, list):
                src_tags = []
            if not isinstance(tgt_tags, list):
                tgt_tags = []

            edge_score = score_edge(
                source_type=source_type,
                target_type=target_type,
                edge_type=suggested_type,
                source_tags=src_tags,
                target_tags=tgt_tags,
                source_desc=str(source_fm.get("description", "")) if source_fm else "",
                target_desc=str(target_fm.get("description", "")) if target_fm else "",
                precedent_ratio=0.0,
                definitions=definitions,
            )

            if reverse_typed:
                confidence = "BAJA"

            suggestions[confidence].append({
                "source": source_path,
                "target": target_path,
                "suggested_type": suggested_type,
                "score": edge_score,
                "conflicto": reverse_typed,
            })

    # Un par sugerido ALTA en las dos direcciones se contradice: todos los
    # tipos son direccionales. Ninguna de las dos es segura de aplicar.
    alta_pairs = {(it["source"], it["target"]) for it in suggestions["ALTA"]}
    mutual = [
        it for it in suggestions["ALTA"]
        if (it["target"], it["source"]) in alta_pairs
    ]
    for it in mutual:
        it["conflicto"] = True
        suggestions["ALTA"].remove(it)
        suggestions["MEDIA"].append(it)

    # --- Output ---
    lines = []
    total = sum(len(v) for v in suggestions.values())
    total_wikilinks = sum(len(d["out"]) for d in graph.values())
    lines.append(f"Edges analyzed: {total} without type (of {total_wikilinks} total wikilinks)\n")

    for conf in ("ALTA", "MEDIA", "BAJA"):
        items = suggestions[conf]
        if not items:
            continue
        # Rankear por score descendente dentro de cada bucket
        items = sorted(items, key=lambda x: x["score"], reverse=True)
        lines.append(f"=== {conf} ({len(items)} edges) ===")
        for item in items:
            score_str = f" [score: {item['score']:.2f}]" if min_score > 0 else ""
            lines.append(
                f"  {item['source']} → {item['target']}: {item['suggested_type']}{score_str}"
            )
        lines.append("")

    if apply:
        apply_count = 0
        skipped_score = 0
        # Sin --min-score: solo ALTA (compat con contrato histórico).
        # Con --min-score: TODAS las sugerencias que pasen el umbral semántico
        # (plan scoring-semantico: rankear por score, aplicar por umbral).
        if min_score > 0:
            all_sug = (
                suggestions["ALTA"] + suggestions["MEDIA"] + suggestions["BAJA"]
            )
            candidates = [
                it for it in all_sug
                if it["score"] >= min_score and not it.get("conflicto")
            ]
            skipped_score = len(all_sug) - len(candidates)
        else:
            candidates = suggestions["ALTA"]
        for item in candidates:
            source_file = vault / item["source"]
            try:
                content = source_file.read_text(encoding="utf-8")
            except Exception:
                continue

            if not content.startswith("---"):
                continue

            end_fm = content.find("\n---\n", 3)
            if end_fm == -1:
                end_fm = content.find("\n---", 3)
            if end_fm == -1:
                continue

            fm_text = content[3:end_fm]
            after_fm = content[end_fm:]

            new_fm = _insert_link_entry(
                fm_text, item["target"], item["suggested_type"]
            )

            new_content = f"---\n{new_fm}{after_fm}"

            if not dry_run:
                source_file.write_text(new_content, encoding="utf-8")
            apply_count += 1

        if min_score > 0:
            lines.append(
                f"\n{'DRY-RUN: ' if dry_run else ''}Applied {apply_count} suggestions "
                f"with score >= {min_score:.2f}."
            )
        else:
            lines.append(
                f"\n{'DRY-RUN: ' if dry_run else ''}Applied {apply_count} HIGH suggestions."
            )
        if skipped_score and min_score > 0:
            lines.append(
                f"  (skipped {skipped_score} with score < {min_score:.2f})"
            )
        if not dry_run:
            lines.append("⚠️  Review changes with git diff before committing.")

    return "\n".join(lines)


def run(args, vault, config=None):
    """Runs graph analysis."""
    subcommand = getattr(args, "subcommand", None)
    sub_args = getattr(args, "args", [])
    edge_type = getattr(args, "edge_type", None)
    apply_flag = getattr(args, "apply", False)
    dry_run_flag = getattr(args, "dry_run", False)
    min_score = getattr(args, "min_score", None)
    if min_score is None:
        # Default desde config (plan scoring-semantico: umbral configurable)
        min_score = (
            config.graph_suggest_min_score if config is not None else 0.0
        )

    definitions = config.edge_type_definitions() if config else None

    if not subcommand:
        # Default: dump
        graph = build_graph(vault, definitions=definitions)
        print(_cmd_dump(graph))
        return 0

    # suggest-edge-types solo necesita build_graph, no tag_index
    if subcommand == "suggest-edge-types":
        graph = build_graph(vault, definitions=definitions)
        print(_cmd_suggest_edge_types(
            vault, graph, apply=apply_flag, dry_run=dry_run_flag,
            min_score=min_score, definitions=definitions
        ))
        return 0

    graph = build_graph(vault, definitions=definitions)
    tag_index = build_tag_index(vault)

    if subcommand == "stats":
        print(_cmd_stats(graph, tag_index))
    elif subcommand == "orphans":
        print(_cmd_orphans(graph))
    elif subcommand == "hubs":
        print(_cmd_hubs(graph))
    elif subcommand == "backlinks":
        if not sub_args:
            print("Usage: python3 -m cli graph backlinks <file.md>", file=sys.stderr)
            return 1
        print(_cmd_backlinks(graph, sub_args[0], edge_type=edge_type))
    elif subcommand == "deps":
        if not sub_args:
            print("Usage: python3 -m cli graph deps <file.md>", file=sys.stderr)
            return 1
        print(_cmd_deps(graph, sub_args[0], edge_type=edge_type))
    elif subcommand == "path":
        if len(sub_args) < 2:
            print("Usage: python3 -m cli graph path <origin> <destination>", file=sys.stderr)
            return 1
        print(_cmd_path(graph, sub_args[0], sub_args[1]))
    elif subcommand == "cluster":
        print(_cmd_cluster(graph))
    elif subcommand == "tags":
        print(_cmd_tags(graph, tag_index, sub_args[0] if sub_args else None))
    elif subcommand == "bridges":
        print(_cmd_bridges(graph, tag_index))
    elif subcommand == "dirs":
        print(_cmd_dirs(vault))
    elif subcommand == "types":
        print(_cmd_types(vault))
    elif subcommand == "dump":
        print(_cmd_dump(graph))
    elif subcommand == "impact-batch":
        if not sub_args:
            print("Usage: python3 -m cli graph impact-batch <file1> <file2> ...", file=sys.stderr)
            return 1
        for slug in sub_args:
            print(_cmd_impact(graph, slug, vault=vault))
            print("---")
    elif subcommand == "impact":
        if not sub_args:
            print("Usage: python3 -m cli graph impact <file.md>", file=sys.stderr)
            return 1
        print(_cmd_impact(graph, sub_args[0], vault=vault))
    else:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        print("Commands: stats, orphans, hubs, backlinks, deps, tags, bridges, "
              "dirs, types, cluster, path, dump, impact, suggest-edge-types", file=sys.stderr)
        return 1

    return 0
