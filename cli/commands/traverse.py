"""Command traverse — Semantic traversal of the OKF vault graph.

Walks the graph from a source concept, following wikilinks (out + in),
typed edges (links: in frontmatter) and causal edges
(cyber.corrects / cyber.corrected_by), up to the indicated depth. At each visited node, returns only the frontmatter — not the body.

This enables efficient semantic navigation: in a single call you get
a concept's neighborhood without reading N complete files.

Usage:
    python3 -m cli traverse <concept> [--depth N] [--json]
                                    [--direction both|out|in] [--no-cyber]
                                    [--edge-type <type>] [--filter]

Semantics (Decision "Every traverse is an ontological search", 2026-08-01):
    --edge-type declares ontological intent (annotation). By default does NOT
    filter: returns the full labeled neighborhood, sorting edges of the declared
    type first. The superset is never lost.
    --filter turns edge_type into exclusion: only edges of that type
    (previous behavior, for queries asking for a single type).
"""

import json
import sys
from pathlib import Path
from cli.vault import build_name_index, under_scope
from cli.frontmatter import parse_frontmatter, normalize_tags
from cli.commands.graph import build_graph, _resolve_name


def _get_frontmatter_summary(filepath):
    """Extracts only the frontmatter of a .md file (without the body).

    Returns:
        dict|None: Key fields for semantic scanning, or None if no frontmatter.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None
    fm, _ = parse_frontmatter(text)
    if fm is None:
        return None
    return {
        "type": str(fm.get("type", "")),
        "title": str(fm.get("title", "")),
        "description": str(fm.get("description", "")),
        "tags": normalize_tags(fm.get("tags")),
        "status": str(fm.get("status", "")),
        "timestamp": str(fm.get("timestamp", "")),
    }


def _get_cyber_edges(filepath):
    """Extracts causal edges from the cyber: block of a concept.

    Returns:
        tuple[list[str], list[str]]: (corrects, corrected_by)
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return [], []
    fm, _ = parse_frontmatter(text)
    if fm is None:
        return [], []
    cyber = fm.get("cyber")
    if not isinstance(cyber, dict):
        return [], []

    corrects = cyber.get("corrects", [])
    corrected_by = cyber.get("corrected_by", [])

    if isinstance(corrects, str):
        corrects = [corrects]
    if isinstance(corrected_by, str):
        corrected_by = [corrected_by]

    return corrects or [], corrected_by or []


def _resolve_cyber_ref(ref, vault, name_index):
    """Resolves a cyber reference (filename or wikilink) to a relative path.

    cyber.corrects can contain:
    - Filenames without extension: "decision-0042"
    - Filenames with extension: "decision-0042.md"
    - Wikilinks: "[[decision-0042]]"
    - Relative paths: "decisions/decision-0042.md"
    """
    ref = ref.strip()
    if not ref:
        return None

    # Quitar [[...]] si viene como wikilink
    if ref.startswith("[["):
        ref = ref.strip("[]").split("|")[0].strip()

    # Quitar #anchor
    if "#" in ref:
        ref = ref.split("#")[0]

    # Ruta con directorio
    if "/" in ref:
        if not ref.endswith(".md"):
            ref += ".md"
        candidate = vault / ref
        if candidate.exists() and under_scope(ref):
            return ref
        return None

    # Nombre de archivo
    if not ref.endswith(".md"):
        ref += ".md"

    if ref in name_index:
        return name_index[ref]

    # Fallback: índice cacheado por proceso (reemplaza el rglob full-vault
    # que se disparaba por cada cyber ref no resuelto).
    from cli.wikilinks import _cached_name_index
    cached = _cached_name_index(vault)
    if ref in cached:
        return cached[ref]
    return None


def run(args, vault, config=None):
    """Runs semantic graph traversal from one or more origins."""
    target = getattr(args, "target", None)
    seeds_raw = getattr(args, "seeds", None)

    # Al menos uno de target o --seeds debe estar presente
    if not target and not seeds_raw:
        print("Usage: python3 -m cli traverse <concept> [--depth N] [--json] "
              "[--direction both|out|in] [--no-cyber]\n"
              "      python3 -m cli traverse --seeds <c1> <c2> [...] [--depth N] [...]",
              file=sys.stderr)
        return 1

    # Normalizar: si solo hay target, tratarlo como seed única
    if seeds_raw:
        seeds_candidates = seeds_raw
    else:
        seeds_candidates = [target]

    depth = getattr(args, "depth", 1)
    direction = getattr(args, "direction", "both")
    json_out = getattr(args, "json", False)
    no_cyber = getattr(args, "no_cyber", False)
    edge_type_filter = getattr(args, "edge_type", None)
    filter_mode = getattr(args, "filter", False)  # --filter: edge_type excluye (default: anotación)
    if filter_mode and not edge_type_filter:
        print("⚠️  --filter without --edge-type: does not filter anything. Use --edge-type <type> --filter.",
              file=sys.stderr)

    # Construir grafo de wikilinks (ya resuelve todos los links)
    graph = build_graph(vault)
    name_index = build_name_index(vault)

    # Resolver seeds. Las que no resuelven se ignoran con warning.
    resolved_seeds = []
    warnings = []
    for seed_candidate in seeds_candidates:
        resolved = _resolve_name(seed_candidate, graph)
        if resolved is None:
            warnings.append(f"Not found: {seed_candidate}")
        elif resolved in resolved_seeds:
            warnings.append(
                f"Duplicate seed ignored: {seed_candidate} -> {resolved}"
            )
        else:
            resolved_seeds.append(resolved)

    # Si TODAS las seeds fallaron, error
    if not resolved_seeds:
        for w in warnings:
            print(f"✗ {w}", file=sys.stderr)
        print("Error: no seed could be resolved.", file=sys.stderr)
        return 1

    # BFS con detección de ciclos y visited set compartido entre seeds
    visited = set()
    nodes = []
    # Queue entries: (path, depth, edge_type, from_path, seed_path, score)
    queue = []

    for seed_path in resolved_seeds:
        queue.append((seed_path, 0, "origin", None, seed_path, 0.0))

    while queue:
        current_path, current_depth, edge_type, from_path, seed_path, current_score = queue.pop(0)

        if current_path in visited:
            continue
        if current_depth > depth:
            continue

        visited.add(current_path)

        # Leer solo frontmatter (no body)
        filepath = vault / current_path
        fm_summary = _get_frontmatter_summary(filepath)

        node = {
            "path": current_path,
            "depth": current_depth,
            "edge_type": edge_type,
            "from": from_path,
            "frontmatter": fm_summary,
        }
        # Incluir score si la arista es tipada
        if current_score > 0:
            node["score"] = current_score
        # Incluir seed en multi-seed mode para trazabilidad
        if len(resolved_seeds) > 1:
            node["seed"] = seed_path

        nodes.append(node)

        if current_depth >= depth:
            continue

        # Recolectar vecinos según dirección
        neighbors = []

        if direction in ("out", "both"):
            for tgt in graph.get(current_path, {}).get("out", []):
                neighbors.append((tgt, "wikilink", 0.0))

            # Aristas tipadas salientes con score
            for entry in graph.get(current_path, {}).get("typed_out", []):
                neighbors.append((entry["target"], entry["type"], entry.get("score", 0.0)))

            if not no_cyber:
                corrects, _ = _get_cyber_edges(filepath)
                for ref in corrects:
                    resolved_ref = _resolve_cyber_ref(ref, vault, name_index)
                    if resolved_ref and resolved_ref != current_path:
                        neighbors.append((resolved_ref, "cyber.corrects", 0.0))

        if direction in ("in", "both"):
            for src in graph.get(current_path, {}).get("in", []):
                neighbors.append((src, "backlink", 0.0))

            # Aristas tipadas entrantes con score
            for entry in graph.get(current_path, {}).get("typed_in", []):
                neighbors.append((entry["target"], entry["type"], entry.get("score", 0.0)))

            if not no_cyber:
                _, corrected_by = _get_cyber_edges(filepath)
                for ref in corrected_by:
                    resolved_ref = _resolve_cyber_ref(ref, vault, name_index)
                    if resolved_ref and resolved_ref != current_path:
                        neighbors.append((resolved_ref, "cyber.corrected_by", 0.0))

        # Ordenar vecinos: en modo anotación, aristas del tipo declarado primero,
        # luego wikilinks (semántica débil), luego el resto por score.
        # En modo filtro, orden por score.
        if edge_type_filter and not filter_mode:
            neighbors.sort(key=lambda x: (x[1] != edge_type_filter, x[1] == "wikilink", -x[2]))
        else:
            neighbors.sort(key=lambda x: x[2], reverse=True)

        for neighbor_path, n_edge_type, n_score in neighbors:
            if neighbor_path not in visited:
                # Modo filtro (explícito): excluir aristas que no matchean el tipo
                # Modo anotación (default): el superset completo siempre entra
                if filter_mode and edge_type_filter:
                    if n_edge_type != edge_type_filter and n_edge_type not in ("origin",):
                        continue
                queue.append(
                    (neighbor_path, current_depth + 1, n_edge_type,
                     current_path, seed_path, n_score)
                )

    # ── Output JSON ──
    if json_out:
        result = {
            "depth": depth,
            "direction": direction,
            "total": len(nodes),
            "nodes": nodes,
        }
        if len(resolved_seeds) == 1:
            result["origin"] = resolved_seeds[0]
        else:
            result["seeds"] = resolved_seeds
        if edge_type_filter:
            result["edge_type"] = edge_type_filter
            result["filter"] = filter_mode
            if not filter_mode:
                result["matched"] = len([
                    n for n in nodes
                    if n["from"] is not None and n["edge_type"] == edge_type_filter
                ])
        # Aristas resultantes para Cognitive Trace
        result["result_edges"] = [
            {"from": n["from"], "to": n["path"], "type": n["edge_type"]}
            for n in nodes if n["from"] is not None
        ]
        if warnings:
            result["warnings"] = warnings
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    # ── Output legible ──
    if len(resolved_seeds) == 1:
        print(f"🔗 Traversal from: {resolved_seeds[0]}")
    else:
        print(f"🔗 Traversal from {len(resolved_seeds)} seeds:")
        for s in resolved_seeds:
            print(f"   📌 {s}")
    print(f"   Depth: {depth} | Direction: {direction} | "
          f"Nodes: {len(nodes)}")

    if warnings:
        print()
        for w in warnings:
            print(f"   ⚠️  {w}")

    # ── Resumen ontológico (modo anotación) ──
    if edge_type_filter and not filter_mode:
        matched = [n for n in nodes
                   if n["from"] is not None and n["edge_type"] == edge_type_filter]
        edges_total = len([n for n in nodes if n["from"] is not None])
        print()
        print(f"   🎯 Annotation: {edge_type_filter} — {len(matched)}/{edges_total} "
              f"edges of the declared type (full superset preserved)")
        # Contexto del vecindario: tipos dominantes (calibra la anotación contra
        # el anclaje de mostrar el tipo declarado primero — P2-2 del backlog).
        _type_counts: dict = {}
        for _n in nodes:
            if _n["from"] is not None:
                _t = _n["edge_type"] or "?"
                _type_counts[_t] = _type_counts.get(_t, 0) + 1
        _dominant = ", ".join(
            f"{t} ({c})" for t, c in sorted(_type_counts.items(), key=lambda kv: -kv[1])[:4]
        )
        _decl_pct = (len(matched) / edges_total * 100) if edges_total else 0
        print(f"   📊 Neighborhood: {_dominant} — declared {edge_type_filter} "
              f"= {_decl_pct:.0f}% of {edges_total} edges")
    elif not edge_type_filter:
        # ── Sugerencia ontológica (Nivel 2): si no se usó edge_type, sugerir ──
        typed_edge_types = {n["edge_type"] for n in nodes
                            if n["edge_type"] not in ("origin", "wikilink", "backlink",
                                                       "cyber.corrects", "cyber.corrected_by")}
        if typed_edge_types:
            et_list = ", ".join(sorted(typed_edge_types))
            typed_count = len([n for n in nodes if n["edge_type"] in typed_edge_types])
            print()
            print(f"   💡 Tip: this traversal includes {typed_count} "
                  f"typed edges ({et_list}).")
            print(f"   To annotate the explored type: --edge-type <type>")
            print(f"   To filter (exclusion): --edge-type <type> --filter")
            print(f"   Example: traverse <slug> --edge-type extiende")

    print()

    for node in nodes:
        indent = "  " * node["depth"]
        fm = node["frontmatter"] or {}

        if node["depth"] == 0:
            prefix = f"{indent}📍"
        else:
            arrow = "→" if node["edge_type"] in ("wikilink", "cyber.corrects") else "←"
            prefix = f"{indent}{arrow}"

        title = fm.get("title", "") or node["path"]
        desc = fm.get("description", "")
        tags = fm.get("tags", [])
        ctype = fm.get("type", "?")
        status = fm.get("status", "")

        # Truncar descripción
        desc_short = desc[:120] + "..." if len(desc) > 120 else desc
        tag_str = (
            f"[{', '.join(tags[:6])}{'...' if len(tags) > 6 else ''}]"
            if tags else ""
        )

        status_str = f" ({status})" if status else ""

        print(f"{prefix} {node['path']} [{ctype}]{status_str} {title}")
        if desc_short:
            print(f"{indent}   {desc_short}")
        if tag_str:
            print(f"{indent}   🏷️  {tag_str}")
        if node["from"]:
            score_str = f" ({node.get('score', 0):.2f})" if node.get("score", 0) > 0 else ""
            print(f"{indent}   via {node['edge_type']}{score_str} ← {node['from']}")
        if len(resolved_seeds) > 1 and node.get("seed"):
            print(f"{indent}   🌰 seed: {node['seed']}")
        print()

    return 0
