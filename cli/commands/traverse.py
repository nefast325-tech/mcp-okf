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
                                    [--compact] [--budget N]

Semantics (Decision "Every traverse is an ontological search", 2026-08-01):
    --edge-type declares ontological intent (annotation). By default does NOT
    filter: returns the full labeled neighborhood, sorting edges of the declared
    type first. The superset is never lost.
    --filter turns edge_type into exclusion: only edges of that type
    (previous behavior, for queries asking for a single type).

Versión vigente (fase 4, cli/vigente.py): una nota en su estado de reemplazo
(`superada`, `reemplazada`) se salta por defecto hacia la que la `corrige`,
siguiendo la cadena hasta la vigente; el nodo visitado anota en `superseded`
las versiones que se saltaron. --all-versions recorre la cadena tal cual.

Costo de la llamada (--compact, --budget, recibo): un agente paga cada traverse
en tokens de contexto. --compact imprime una línea por nodo; --budget N corta
la salida en N nodos; y toda salida termina en un recibo (`📎 recibo:` en texto,
clave `receipt` en JSON) con nodos devueltos, nodos alcanzables, bytes y tokens
(bytes // 4). Los bytes del recibo cuentan la salida que lo precede, no al
recibo mismo.
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
    all_versions = getattr(args, "all_versions", False)
    compact = getattr(args, "compact", False)        # --compact: una línea por nodo
    budget = getattr(args, "budget", None)           # --budget N: la salida se corta en N nodos
    if budget is not None and budget < 1:
        print("Error: --budget must be >= 1.", file=sys.stderr)
        return 1
    if filter_mode and not edge_type_filter:
        print("⚠️  --filter without --edge-type: does not filter anything. Use --edge-type <type> --filter.",
              file=sys.stderr)

    # Construir grafo de wikilinks (ya resuelve todos los links)
    graph = build_graph(vault)
    name_index = build_name_index(vault)

    # Versión vigente de cada nota reemplazada (vacío con --all-versions o
    # sin esquema de estados): el recorrido salta a ella.
    from cli.vigente import build_superseded_map
    superseded_map = {} if all_versions else build_superseded_map(vault, graph, config)
    skipped_versions = {}  # vigente → [reemplazadas que se saltaron para llegar]

    def _vigente(path):
        cur = superseded_map.get(path)
        if cur is None:
            return path
        skipped_versions.setdefault(cur, [])
        if path not in skipped_versions[cur]:
            skipped_versions[cur].append(path)
        return cur

    # Resolver seeds. Las que no resuelven se ignoran con warning.
    resolved_seeds = []
    warnings = []
    for seed_candidate in seeds_candidates:
        resolved = _resolve_name(seed_candidate, graph)
        if resolved is not None and resolved in superseded_map:
            vigente = _vigente(resolved)
            warnings.append(f"Superseded: {resolved} → starting from its current "
                            f"version {vigente} (--all-versions to walk the chain)")
            resolved = vigente
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
        # Versiones reemplazadas que se saltaron para llegar a este nodo
        if current_path in skipped_versions:
            node["superseded"] = list(skipped_versions[current_path])

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
            # Una vecina reemplazada se cambia por su versión vigente (salvo
            # --all-versions). La arista `corrige` hacia la reemplazada se
            # omite: llevaría de la vigente a la vieja y de vuelta.
            if neighbor_path in superseded_map:
                if n_edge_type == "corrige" and superseded_map[neighbor_path] == current_path:
                    continue
                neighbor_path = _vigente(neighbor_path)
                if neighbor_path == current_path:
                    continue
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

    # ── Presupuesto (--budget) ──
    # El BFS corre completo (solo lee frontmatter: es barato) para saber cuántos
    # nodos eran alcanzables; después se recorta la lista. Como el recorte es un
    # prefijo del mismo BFS, el orden es idéntico al de la salida sin budget.
    reachable = len(nodes)
    truncated = budget is not None and reachable > budget
    if truncated:
        nodes = nodes[:budget]

    def _receipt(nbytes):
        """Recibo de la llamada: qué devolvió, qué había y cuánto cuesta leerlo."""
        return {
            "nodes": len(nodes), "reachable": reachable, "depth": depth,
            "bytes": nbytes, "tokens": nbytes // 4,
            "truncated": truncated, "budget": budget,
        }

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
        result["all_versions"] = bool(all_versions)
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
        # El recibo mide el JSON sin la clave receipt (no se cuenta a sí mismo).
        body = json.dumps(result, indent=2, ensure_ascii=False)
        result["receipt"] = _receipt(len(body.encode("utf-8")))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    # ── Output legible ──
    # Se acumula en `out` en vez de imprimir directo: el recibo del final
    # necesita saber cuántos bytes pesa lo que lo precede.
    out = []

    def _p(line=""):
        out.append(line)

    if len(resolved_seeds) == 1:
        _p(f"🔗 Traversal from: {resolved_seeds[0]}")
    else:
        _p(f"🔗 Traversal from {len(resolved_seeds)} seeds:")
        for s in resolved_seeds:
            _p(f"   📌 {s}")
    _p(f"   Depth: {depth} | Direction: {direction} | "
       f"Nodes: {len(nodes)}")

    if warnings:
        _p()
        for w in warnings:
            _p(f"   ⚠️  {w}")

    # ── Resumen ontológico (modo anotación) ──
    if edge_type_filter and not filter_mode:
        matched = [n for n in nodes
                   if n["from"] is not None and n["edge_type"] == edge_type_filter]
        edges_total = len([n for n in nodes if n["from"] is not None])
        _p()
        _p(f"   🎯 Annotation: {edge_type_filter} — {len(matched)}/{edges_total} "
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
        _p(f"   📊 Neighborhood: {_dominant} — declared {edge_type_filter} "
           f"= {_decl_pct:.0f}% of {edges_total} edges")
    elif not edge_type_filter and not compact:
        # ── Sugerencia ontológica (Nivel 2): si no se usó edge_type, sugerir ──
        # (en --compact se omite: es guía, no datos del grafo)
        typed_edge_types = {n["edge_type"] for n in nodes
                            if n["edge_type"] not in ("origin", "wikilink", "backlink",
                                                       "cyber.corrects", "cyber.corrected_by")}
        if typed_edge_types:
            et_list = ", ".join(sorted(typed_edge_types))
            typed_count = len([n for n in nodes if n["edge_type"] in typed_edge_types])
            _p()
            _p(f"   💡 Tip: this traversal includes {typed_count} "
               f"typed edges ({et_list}).")
            _p(f"   To annotate the explored type: --edge-type <type>")
            _p(f"   To filter (exclusion): --edge-type <type> --filter")
            _p(f"   Example: traverse <slug> --edge-type extiende")

    _p()

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

        if compact:
            # Una línea por nodo: sin título, sin tags, sin score y sin repetir
            # el `from` (el `via` basta para saber por qué arista se llegó).
            desc_c = desc[:80] + "…" if len(desc) > 80 else desc
            line = f"{prefix} {node['path']} [{ctype}]{status_str}"
            if desc_c:
                line += f" — {desc_c}"
            if node["from"]:
                line += f" · via {node['edge_type']}"
            if node.get("superseded"):
                # Abreviado: solo el nombre del archivo, sin carpeta ni .md
                line += " · ♻️ vigente de: " + ", ".join(
                    Path(p).stem for p in node["superseded"])
            if len(resolved_seeds) > 1 and node.get("seed"):
                line += f" · 🌰 seed: {node['seed']}"
            _p(line)
            continue

        _p(f"{prefix} {node['path']} [{ctype}]{status_str} {title}")
        if desc_short:
            _p(f"{indent}   {desc_short}")
        if tag_str:
            _p(f"{indent}   🏷️  {tag_str}")
        if node["from"]:
            score_str = f" ({node.get('score', 0):.2f})" if node.get("score", 0) > 0 else ""
            _p(f"{indent}   via {node['edge_type']}{score_str} ← {node['from']}")
        if node.get("superseded"):
            _p(f"{indent}   ♻️  vigente de: {', '.join(node['superseded'])}")
        if len(resolved_seeds) > 1 and node.get("seed"):
            _p(f"{indent}   🌰 seed: {node['seed']}")
        _p()

    # ── Recibo: siempre, en modo normal y compacto ──
    text = "\n".join(out) + "\n"
    r = _receipt(len(text.encode("utf-8")))
    sys.stdout.write(text)
    budget_str = f" (--budget {budget})" if budget is not None else ""
    print(f"📎 recibo: {r['nodes']} nodos de {r['reachable']} · depth {depth} · "
          f"{r['bytes']} bytes ≈ {r['tokens']} tokens · "
          f"truncado: {'sí' if truncated else 'no'}{budget_str}")

    return 0
