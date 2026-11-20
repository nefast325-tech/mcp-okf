"""Command canvas — Generate, layout and validate Obsidian .canvas files.

The OKF vault is a graph (concepts + typed edges). A .canvas is the same graph
drawn in 2D space. This command gives the system the ability to materialize
its own structure as visual maps — the "corteza visual" layer (Insight
canvas-como-corteza-visual-del-organismo).

Subcommands:
    validate <path>        Validate a .canvas (sensor: overlap, z-index, placeholders)
    layout   <path> <algo> Re-layout a .canvas (actuador: grid, dagre, radial, force, linear, auto)
    generate <slug>        Generate a .canvas from the vault graph (typed edges as labels)

Usage:
    python3 -m cli canvas validate mapas/Organismo-OKF.canvas
    python3 -m cli canvas validate mapas/Organismo-OKF.canvas --fix
    python3 -m cli canvas layout mapas/mi-mapa.canvas force
    python3 -m cli canvas layout mapas/mi-mapa.canvas auto --dry-run
    python3 -m cli canvas generate insights/canvas-como-corteza-visual-del-organismo-el-sistema-que-se-dibuja-a-si-mismo --depth 1
"""

import argparse
import json
import math
import sys
import time
from copy import deepcopy
from pathlib import Path

GRID = 20
H_GAP = 100   # horizontal gap between nodes
V_GAP = 80    # vertical gap between nodes
NODE_LIMIT_WARN = 100
NODE_LIMIT_ERROR = 200
VALID_NODE_TYPES = {"text", "file", "link", "group"}
VALID_COLORS = {"1", "2", "3", "4", "5", "6"}
FORBIDDEN_PLACEHOLDERS = [
    "Describe this", "YYYY-MM-DD", "Content goes here",
    "Value: 0", "Define this entity", "What happened",
    "(completar)", "(pendiente)",
]

# Color por type del frontmatter OKF (paleta Obsidian 1-6)
TYPE_COLOR = {
    "Decision": "4",
    "Insight": "1",
    "Plan": "3",
    "Project": "5",
    "Spec": "6",
    "Sistema": "6",
    "Sesion": "2",
    "Criterio": "3",
    "Research": "5",
    "Framework": "5",
    "Skill": "4",
    "Cliente": "2",
    "Historias": "2",
}


# ─────────────────────────────────────────────
# Utilidades de geometría
# ─────────────────────────────────────────────

def snap(value):
    """Snap a value to the nearest grid increment."""
    return round(value / GRID) * GRID


def get_center(node):
    """Get the center point of a node."""
    return (node["x"] + node["width"] / 2, node["y"] + node["height"] / 2)


def separate_groups_and_content(nodes):
    """Split nodes into groups (zones) and content nodes."""
    groups = [n for n in nodes if n.get("type") == "group"]
    content = [n for n in nodes if n.get("type") != "group"]
    return groups, content


def find_group_membership(groups, content):
    """Map each content node to the group it's inside (if any).

    Uses node center point for containment (matches Obsidian behavior).
    """
    membership = {}
    for node in content:
        ncx = node["x"] + node["width"] / 2
        ncy = node["y"] + node["height"] / 2
        for group in groups:
            gx, gy = group["x"], group["y"]
            if gx <= ncx <= gx + group["width"] and gy <= ncy <= gy + group["height"]:
                membership[node["id"]] = group["id"]
                break
    return membership


def _load_canvas(path):
    """Load a canvas file. Returns (data, error_str)."""
    if not path.exists():
        return None, f"File not found: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"


def _save_canvas(path, data):
    """Save canvas data with Obsidian-compatible formatting (tabs, no spaces)."""
    text = json.dumps(data, ensure_ascii=False, indent="\t")
    path.write_text(text + "\n", encoding="utf-8")


def _canvas_destino(vault, path) -> Path:
    """Resuelve un destino .canvas SIN salir del vault ni seguir symlinks."""
    vault = Path(vault).resolve()
    dest = Path(path)
    if not dest.is_absolute():
        dest = vault / dest
    # resolve() sigue symlinks: un .canvas que apunta fuera se rechaza.
    dest = dest.resolve()
    try:
        dest.relative_to(vault)
    except ValueError:
        raise PermissionError(f"canvas: destino fuera del vault: {path}") from None
    if dest.suffix != ".canvas":
        raise PermissionError(f"canvas: el destino debe ser .canvas: {path}")
    if dest.exists() and dest.is_symlink():
        raise PermissionError(f"canvas: el destino es un symlink: {path}")
    return dest


# ─────────────────────────────────────────────
# VALIDATE — sensor
# ─────────────────────────────────────────────

def validate_canvas(canvas_path: Path, fix: bool = False) -> dict:
    """Validate a canvas file and return a report.

    Checks: JSON structure, required fields, ID collisions, node count limits,
    grid alignment, z-index ordering, node overlap, placeholders, paths.
    """
    result = {
        "valid": True,
        "path": str(canvas_path),
        "nodes": 0,
        "edges": 0,
        "groups": 0,
        "warnings": [],
        "errors": [],
    }

    data, err = _load_canvas(canvas_path)
    if data is None:
        result["valid"] = False
        result["errors"].append(err)
        return result

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if not isinstance(nodes, list):
        result["valid"] = False
        result["errors"].append("'nodes' must be an array")
        return result
    if not isinstance(edges, list):
        result["valid"] = False
        result["errors"].append("'edges' must be an array")
        return result

    result["nodes"] = len(nodes)
    result["edges"] = len(edges)
    modified = False

    # Node count limits
    if len(nodes) > NODE_LIMIT_ERROR:
        result["errors"].append(f"Node count {len(nodes)} exceeds limit ({NODE_LIMIT_ERROR})")
        result["valid"] = False
    elif len(nodes) > NODE_LIMIT_WARN:
        result["warnings"].append(
            f"Node count {len(nodes)} exceeds warning threshold ({NODE_LIMIT_WARN}). "
            f"Hard limit is {NODE_LIMIT_ERROR}."
        )

    # Per-node checks
    seen_ids = set()
    first_content_idx = None
    last_group_idx = None
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            result["errors"].append(f"Node at index {i} is not an object")
            result["valid"] = False
            continue

        node_id = node.get("id")
        if not node_id:
            result["errors"].append(f"Node at index {i} missing 'id'")
            result["valid"] = False
        elif node_id in seen_ids:
            result["errors"].append(f"Duplicate node ID: '{node_id}'")
            result["valid"] = False
        else:
            seen_ids.add(node_id)

        node_type = node.get("type")
        if not node_type:
            result["errors"].append(f"Node '{node_id}' missing 'type'")
            result["valid"] = False
        elif node_type not in VALID_NODE_TYPES:
            result["errors"].append(f"Node '{node_id}' has invalid type: '{node_type}'")
            result["valid"] = False

        # Z-index tracking
        if node_type == "group":
            last_group_idx = i
            result["groups"] += 1
        elif first_content_idx is None:
            first_content_idx = i

        # Position fields
        for field in ("x", "y", "width", "height"):
            val = node.get(field)
            if val is None:
                result["errors"].append(f"Node '{node_id}' missing '{field}'")
                result["valid"] = False
            elif not isinstance(val, (int, float)):
                result["errors"].append(f"Node '{node_id}' field '{field}' must be numeric")
                result["valid"] = False
            elif field in ("width", "height") and val <= 0:
                result["warnings"].append(f"Node '{node_id}' has non-positive {field}: {val}")

        # Grid alignment
        for field in ("x", "y", "width", "height"):
            val = node.get(field)
            if isinstance(val, (int, float)) and val % GRID != 0:
                if fix:
                    node[field] = round(val / GRID) * GRID
                    modified = True
                else:
                    result["warnings"].append(
                        f"Node '{node_id}' {field}={val} not aligned to {GRID}px grid"
                    )

        # Color
        color = node.get("color")
        if color is not None:
            if isinstance(color, int):
                result["warnings"].append(
                    f"Node '{node_id}' color is integer {color}, should be string \"{color}\""
                )
                if fix:
                    node["color"] = str(color)
                    modified = True
            elif isinstance(color, str) and not color.startswith("#") and color not in VALID_COLORS:
                result["warnings"].append(f"Node '{node_id}' has unknown color: '{color}'")

        # Type-specific
        if node_type == "text":
            text = node.get("text", "")
            if "text" not in node:
                result["errors"].append(f"Text node '{node_id}' missing 'text' field")
                result["valid"] = False
            elif text == "":
                result["warnings"].append(f"Text node '{node_id}' has empty 'text' field")
            else:
                for ph in FORBIDDEN_PLACEHOLDERS:
                    if ph in text:
                        result["warnings"].append(
                            f"Text node '{node_id}' contains placeholder: '{ph}'"
                        )
        elif node_type == "file":
            if "file" not in node:
                result["errors"].append(f"File node '{node_id}' missing 'file' field")
                result["valid"] = False
            else:
                fpath = node.get("file", "")
                if fpath.startswith("/") or fpath.startswith("~"):
                    result["errors"].append(
                        f"File node '{node_id}' uses absolute path: '{fpath}'. "
                        f"Must be vault-relative."
                    )
                    result["valid"] = False
        elif node_type == "link":
            url = node.get("url", "")
            if "url" not in node:
                result["errors"].append(f"Link node '{node_id}' missing 'url' field")
                result["valid"] = False
            elif url and not (url.startswith("http://") or url.startswith("https://")):
                result["warnings"].append(
                    f"Link node '{node_id}' url does not start with http(s)://: '{url}'"
                )

    # Z-index ordering
    if last_group_idx is not None and first_content_idx is not None and last_group_idx > first_content_idx:
        result["warnings"].append(
            "Z-index issue: some group nodes appear after content nodes. "
            "Groups should come first for proper background rendering."
        )

    # Edge checks
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            result["errors"].append(f"Edge at index {i} is not an object")
            result["valid"] = False
            continue
        eid = edge.get("id")
        if not eid:
            result["errors"].append(f"Edge at index {i} missing 'id'")
            result["valid"] = False
        elif eid in seen_ids:
            result["errors"].append(f"Duplicate edge ID: '{eid}'")
            result["valid"] = False
        else:
            seen_ids.add(eid)
        for side in ("fromNode", "toNode"):
            if edge.get(side) not in {n.get("id") for n in nodes if isinstance(n, dict)}:
                result["errors"].append(
                    f"Edge '{eid}' references unknown node '{edge.get(side)}' in '{side}'"
                )
                result["valid"] = False

    # Node overlap detection (content nodes only — groups are containers)
    content_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") != "group"]
    for i in range(len(content_nodes)):
        for j in range(i + 1, len(content_nodes)):
            a, b = content_nodes[i], content_nodes[j]
            a_right = a.get("x", 0) + a.get("width", 0)
            a_bottom = a.get("y", 0) + a.get("height", 0)
            b_right = b.get("x", 0) + b.get("width", 0)
            b_bottom = b.get("y", 0) + b.get("height", 0)
            if (a.get("x", 0) < b_right and a_right > b.get("x", 0)
                    and a.get("y", 0) < b_bottom and a_bottom > b.get("y", 0)):
                overlap_x = min(a_right, b_right) - max(a.get("x", 0), b.get("x", 0))
                overlap_y = min(a_bottom, b_bottom) - max(a.get("y", 0), b.get("y", 0))
                overlap_area = overlap_x * overlap_y
                min_area = min(a.get("width", 1) * a.get("height", 1),
                               b.get("width", 1) * b.get("height", 1))
                overlap_pct = round(overlap_area / max(min_area, 1) * 100)
                if overlap_pct >= 1:
                    result["warnings"].append(
                        f"Node overlap: '{a.get('id')}' and '{b.get('id')}' "
                        f"overlap ~{overlap_pct}%"
                    )

    if fix and modified:
        _save_canvas(canvas_path, data)

    return result


# ─────────────────────────────────────────────
# LAYOUT — actuador
# ─────────────────────────────────────────────

def layout_grid(content, columns=None, sort_by="type"):
    """Arrange content nodes in a grid pattern (galleries, boards)."""
    if not content:
        return content
    if columns is None:
        n = len(content)
        columns = max(2, min(6, math.ceil(math.sqrt(n))))
    if sort_by == "type":
        type_order = {"text": 0, "file": 1, "link": 2}
        content.sort(key=lambda n: (type_order.get(n.get("type"), 3), n.get("id", "")))
    elif sort_by == "size":
        content.sort(key=lambda n: n["width"] * n["height"], reverse=True)

    max_w = max(n["width"] for n in content)
    max_h = max(n["height"] for n in content)
    cell_w = max_w + H_GAP
    cell_h = max_h + V_GAP
    for i, node in enumerate(content):
        col = i % columns
        row = i // columns
        node["x"] = snap(col * cell_w + (cell_w - node["width"]) // 2)
        node["y"] = snap(row * cell_h + (cell_h - node["height"]) // 2)
    return content


def layout_dagre(content, edges, direction="TB"):
    """Hierarchical Sugiyama-style layout (flowcharts, process diagrams)."""
    if not content:
        return content
    node_map = {n["id"]: n for n in content}
    node_ids = set(node_map.keys())

    children = {nid: [] for nid in node_ids}
    parents = {nid: [] for nid in node_ids}
    for edge in edges:
        fn, tn = edge.get("fromNode"), edge.get("toNode")
        if fn in node_ids and tn in node_ids:
            children[fn].append(tn)
            parents[tn].append(fn)

    roots = [nid for nid in node_ids if not parents[nid]]
    if not roots:
        roots = [min(node_ids, key=lambda nid: len(parents[nid]))]

    layers = {}
    visited = set()
    queue = [(r, 0) for r in roots]
    for r, _ in queue:
        visited.add(r)
        layers[r] = 0
    while queue:
        current, layer = queue.pop(0)
        for child in children.get(current, []):
            new_layer = layer + 1
            if child not in visited or new_layer > layers.get(child, -1):
                layers[child] = new_layer
                if child not in visited:
                    visited.add(child)
                    queue.append((child, new_layer))

    max_layer = max(layers.values()) if layers else 0
    for nid in node_ids:
        if nid not in layers:
            layers[nid] = max_layer + 1

    layer_groups = {}
    for nid, layer in layers.items():
        layer_groups.setdefault(layer, []).append(nid)
    for layer in layer_groups:
        layer_groups[layer].sort()

    sorted_layers = sorted(layer_groups.keys())
    layer_dims = {}
    for li in sorted_layers:
        nids = layer_groups[li]
        layer_dims[li] = (
            max(node_map[n]["width"] for n in nids),
            max(node_map[n]["height"] for n in nids),
        )

    cumulative_y, cumulative_x = {}, {}
    cy, cx = 0, 0
    for li in sorted_layers:
        cumulative_y[li] = cy
        cumulative_x[li] = cx
        mw, mh = layer_dims[li]
        cy += mh + V_GAP * 2
        cx += mw + H_GAP * 2
    total_y, total_x = cy, cx

    for layer_idx in sorted_layers:
        nodes_in_layer = layer_groups[layer_idx]
        n_in_layer = len(nodes_in_layer)
        mw, mh = layer_dims[layer_idx]
        for pos, nid in enumerate(nodes_in_layer):
            node = node_map[nid]
            if direction == "TB":
                cell_w = mw + H_GAP
                start_x = -(n_in_layer * cell_w) // 2
                node["x"] = snap(start_x + pos * cell_w + (cell_w - node["width"]) // 2)
                node["y"] = snap(cumulative_y[layer_idx])
            elif direction == "LR":
                cell_h = mh + V_GAP
                start_y = -(n_in_layer * cell_h) // 2
                node["x"] = snap(cumulative_x[layer_idx])
                node["y"] = snap(start_y + pos * cell_h + (cell_h - node["height"]) // 2)
            elif direction == "BT":
                cell_w = mw + H_GAP
                start_x = -(n_in_layer * cell_w) // 2
                node["x"] = snap(start_x + pos * cell_w + (cell_w - node["width"]) // 2)
                node["y"] = snap(total_y - cumulative_y[layer_idx] - mh)
            elif direction == "RL":
                cell_h = mh + V_GAP
                start_y = -(n_in_layer * cell_h) // 2
                node["x"] = snap(total_x - cumulative_x[layer_idx] - mw)
                node["y"] = snap(start_y + pos * cell_h + (cell_h - node["height"]) // 2)
    return content


def layout_radial(content, edges, center_id=None):
    """Radial layout expanding from a center node (mind maps, hubs)."""
    if not content:
        return content
    node_map = {n["id"]: n for n in content}
    node_ids = set(node_map.keys())

    if center_id and center_id in node_map:
        center = center_id
    else:
        conn_count = {nid: 0 for nid in node_ids}
        for edge in edges:
            fn, tn = edge.get("fromNode"), edge.get("toNode")
            if fn in conn_count:
                conn_count[fn] += 1
            if tn in conn_count:
                conn_count[tn] += 1
        center = max(conn_count, key=lambda nid: conn_count[nid]) if conn_count else content[0]["id"]

    adj = {nid: set() for nid in node_ids}
    for edge in edges:
        fn, tn = edge.get("fromNode"), edge.get("toNode")
        if fn in adj and tn in adj:
            adj[fn].add(tn)
            adj[tn].add(fn)

    rings = {center: 0}
    visited = {center}
    queue = [center]
    while queue:
        current = queue.pop(0)
        for neighbor in adj.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                rings[neighbor] = rings[current] + 1
                queue.append(neighbor)

    max_ring = max(rings.values()) if rings else 0
    for nid in node_ids:
        if nid not in rings:
            rings[nid] = max_ring + 1

    ring_groups = {}
    for nid, ring in rings.items():
        ring_groups.setdefault(ring, []).append(nid)

    center_node = node_map[center]
    center_node["x"] = snap(-center_node["width"] // 2)
    center_node["y"] = snap(-center_node["height"] // 2)

    base_radius = 300
    max_ring_idx = max(ring_groups.keys()) if ring_groups else 0
    prev_radius = 0.0
    prev_max_dim = 0.0
    for ring_idx in sorted(ring_groups.keys()):
        if ring_idx == 0:
            continue
        nodes_in_ring = ring_groups[ring_idx]
        n_nodes = len(nodes_in_ring)
        # Radio mínimo considerando la DIMENSIÓN MAYOR del nodo (ancho o alto)
        # más factor de seguridad 1.2 para las esquinas (evita overlap en anillos densos)
        max_node_w = max(node_map[nid]["width"] for nid in nodes_in_ring)
        max_node_h = max(node_map[nid]["height"] for nid in nodes_in_ring)
        max_dim = max(max_node_w, max_node_h)
        min_arc = max_dim + H_GAP
        min_radius_own = math.ceil(n_nodes * min_arc * 1.2 / (2 * math.pi)) if n_nodes > 1 else 0
        # Separación acumulativa entre rings adyacentes: el ring actual debe estar
        # al menos a (dim_prev/2 + dim_cur/2 + gap) del radio del ring anterior
        min_radius_cum = prev_radius + prev_max_dim / 2 + max_dim / 2 + H_GAP
        radius = max(base_radius * ring_idx, min_radius_own, min_radius_cum)
        angle_step = 2 * math.pi / max(n_nodes, 1)
        # Fase por ring: cada anillo rota su inicio para que nodos de rings
        # distintos NO se apilen en el mismo ángulo (bugs de rings de 1-2 nodos)
        phase = ring_idx * (math.pi / max(max_ring_idx + 1, 2))
        for i, nid in enumerate(sorted(nodes_in_ring)):
            node = node_map[nid]
            angle = angle_step * i - math.pi / 2 + phase
            node["x"] = snap(radius * math.cos(angle) - node["width"] // 2)
            node["y"] = snap(radius * math.sin(angle) - node["height"] // 2)
        prev_radius = radius
        prev_max_dim = max_dim
    return content


def layout_force(content, edges, iterations=100):
    """Force-directed layout (Fruchterman-Reingold) for knowledge graphs."""
    if not content:
        return content
    if len(content) == 1:
        content[0]["x"] = 0
        content[0]["y"] = 0
        return content

    node_map = {n["id"]: n for n in content}
    node_ids = list(node_map.keys())
    n = len(node_ids)

    max_dim = max(max(nd["width"], nd["height"]) for nd in content)
    min_spacing = max_dim + H_GAP
    area = max(800 * 800, n * min_spacing * min_spacing)
    k = math.sqrt(area / max(n, 1))
    positions = {}
    for i, nid in enumerate(node_ids):
        angle = 2 * math.pi * i / n
        radius = k * 2
        positions[nid] = [radius * math.cos(angle), radius * math.sin(angle)]

    edge_set = set()
    for edge in edges:
        fn, tn = edge.get("fromNode"), edge.get("toNode")
        if fn in node_map and tn in node_map:
            edge_set.add((fn, tn))

    temp = k * 2
    for _ in range(iterations):
        displacements = {nid: [0.0, 0.0] for nid in node_ids}
        for i in range(n):
            for j in range(i + 1, n):
                ni, nj = node_ids[i], node_ids[j]
                dx = positions[ni][0] - positions[nj][0]
                dy = positions[ni][1] - positions[nj][1]
                dist = max(math.sqrt(dx * dx + dy * dy), 0.01)
                force = (k * k) / dist
                fx = (dx / dist) * force
                fy = (dy / dist) * force
                displacements[ni][0] += fx
                displacements[ni][1] += fy
                displacements[nj][0] -= fx
                displacements[nj][1] -= fy
        for fn, tn in edge_set:
            dx = positions[fn][0] - positions[tn][0]
            dy = positions[fn][1] - positions[tn][1]
            dist = max(math.sqrt(dx * dx + dy * dy), 0.01)
            force = (dist * dist) / k
            fx = (dx / dist) * force
            fy = (dy / dist) * force
            displacements[fn][0] -= fx
            displacements[fn][1] -= fy
            displacements[tn][0] += fx
            displacements[tn][1] += fy
        for nid in node_ids:
            dx, dy = displacements[nid]
            dist = max(math.sqrt(dx * dx + dy * dy), 0.01)
            scale = min(dist, temp) / dist
            positions[nid][0] += dx * scale
            positions[nid][1] += dy * scale
        temp *= 0.95

    for nid in node_ids:
        node = node_map[nid]
        node["x"] = snap(positions[nid][0] - node["width"] // 2)
        node["y"] = snap(positions[nid][1] - node["height"] // 2)
    return content


def layout_linear(content, axis="horizontal"):
    """Linear layout along a single axis (timelines, sequences)."""
    if not content:
        return content
    if axis == "horizontal":
        content.sort(key=lambda n: n["x"])
    else:
        content.sort(key=lambda n: n["y"])
    pos = 0
    for node in content:
        if axis == "horizontal":
            node["x"] = snap(pos)
            node["y"] = snap(-node["height"] // 2)
            pos += node["width"] + H_GAP
        else:
            node["x"] = snap(-node["width"] // 2)
            node["y"] = snap(pos)
            pos += node["height"] + V_GAP
    return content


def detect_algorithm(content, edges):
    """Auto-detect the best layout algorithm from canvas content."""
    n_nodes = len(content)
    if n_nodes == 0:
        return "grid"
    node_ids = {n["id"] for n in content}
    internal = [e for e in edges
                if e.get("fromNode") in node_ids and e.get("toNode") in node_ids]
    n_edges = len(internal)
    edge_ratio = n_edges / max(n_nodes, 1)
    file_ratio = sum(1 for n in content if n.get("type") == "file") / n_nodes

    if file_ratio > 0.6 and edge_ratio < 0.5:
        return "grid"
    if n_edges == 0:
        return "grid"

    conn_counts = {nid: 0 for nid in node_ids}
    for edge in internal:
        conn_counts[edge.get("fromNode")] += 1
        conn_counts[edge.get("toNode")] += 1
    if conn_counts:
        max_conn = max(conn_counts.values())
        if max_conn > n_nodes * 0.4 and n_edges > 3:
            return "radial"

    sources = {e.get("fromNode") for e in internal}
    sinks = {e.get("toNode") for e in internal}
    if len(sources - sinks) >= 1 and edge_ratio > 0.5:
        return "dagre"
    if edge_ratio > 1.0:
        return "force"
    return "dagre"


def layout_canvas(canvas_path, algorithm, dry_run=False, **kwargs):
    """Apply a layout algorithm to a canvas file. Returns report dict."""
    path = Path(canvas_path)
    data, err = _load_canvas(path)
    if data is None:
        return {"success": False, "error": err}

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    groups, content = separate_groups_and_content(nodes)
    original_positions = {n["id"]: (n.get("x"), n.get("y")) for n in content}

    actual_algorithm = algorithm
    if algorithm == "auto":
        actual_algorithm = detect_algorithm(content, edges)

    if actual_algorithm == "grid":
        content = layout_grid(content, columns=kwargs.get("columns"),
                              sort_by=kwargs.get("sort_by", "type"))
    elif actual_algorithm == "dagre":
        content = layout_dagre(content, edges, direction=kwargs.get("direction", "TB"))
    elif actual_algorithm == "radial":
        content = layout_radial(content, edges, center_id=kwargs.get("center"))
    elif actual_algorithm == "force":
        content = layout_force(content, edges, iterations=kwargs.get("iterations", 100))
    elif actual_algorithm == "linear":
        content = layout_linear(content, axis=kwargs.get("axis", "horizontal"))
    else:
        return {"success": False, "error": f"Unknown algorithm: {actual_algorithm}"}

    nodes_moved = sum(
        1 for n in content
        if (n["x"], n["y"]) != original_positions.get(n["id"], (None, None))
    )

    if not dry_run:
        data["nodes"] = groups + content
        _save_canvas(path, data)

    return {
        "success": True,
        "algorithm": actual_algorithm,
        "nodes": len(content),
        "nodes_moved": nodes_moved,
        "dry_run": dry_run,
    }


# ─────────────────────────────────────────────
# GENERATE — materializar el grafo del vault
# ─────────────────────────────────────────────

def _resolve_slug(root_slug, graph):
    """Resolve a concept slug to its relpath in the graph. Case-insensitive."""
    if not root_slug.endswith(".md"):
        root_slug = root_slug + ".md"
    if root_slug in graph:
        return root_slug
    # Coincidencia por sufijo o por nombre de archivo
    candidates = [p for p in graph if p.endswith("/" + root_slug) or p == root_slug]
    if candidates:
        return sorted(candidates)[0]
    name = Path(root_slug).name
    candidates = [p for p in graph if Path(p).name == name]
    if candidates:
        return sorted(candidates)[0]
    return None


def _slug_from_relpath(relpath):
    """Convert relpath 'insights/foo.md' to display slug 'insights/foo'."""
    return relpath[:-3] if relpath.endswith(".md") else relpath


def _node_text(relpath, fm):
    """Build the text for a canvas node from frontmatter + body first lines."""
    title = fm.get("title") or Path(relpath).stem.replace("-", " ").title()
    ctype = fm.get("type", "Concepto")
    desc = (fm.get("description") or "").strip()
    tags = fm.get("tags") or []
    tag_str = " · ".join(f"#{t}" for t in tags[:4]) if tags else ""
    lines = [f"# {title}", ""]
    if desc:
        lines.append(desc)
    if tag_str:
        lines.append("")
        lines.append(tag_str)
    lines.append("")
    lines.append(f"*{ctype}* · `{relpath}`")
    text = "\n".join(lines)
    # Estimación de alto según contenido, alineada al grid de 20px
    approx_lines = sum(max(1, len(line) // 62) for line in text.split("\n"))
    height = snap(min(400, max(120, 70 + approx_lines * 18)))
    return text, height


def generate_canvas(vault, root_slug, depth=1, layout="auto", output=None,
                    max_nodes=80, dry_run=False):
    """Generate a .canvas from the vault graph around a root concept.

    Uses the typed edge graph (cli.commands.graph.build_graph) so edges carry
    their ontological type as labels (extiende, refina, aplica...).
    """
    from cli.commands.graph import build_graph
    from cli.frontmatter import parse_frontmatter

    graph = build_graph(vault)
    root_rel = _resolve_slug(root_slug, graph)
    if root_rel is None:
        return {"success": False,
                "error": f"Slug not found in graph: {root_slug}. "
                         f"Try 'cli graph stats' to list concepts."}

    # BFS desde root a profundidad depth sobre aristas dirigidas
    visited = {root_rel}
    queue = [(root_rel, 0)]
    nodes: list = []
    edges: list = []

    while queue:
        current, d = queue.pop(0)
        info = graph.get(current, {})
        if d < depth:
            for target in list(info.get("out", [])) + list(info.get("in", [])):
                if target not in graph:
                    continue
                if target not in visited:
                    visited.add(target)
                    queue.append((target, d + 1))
                if len(visited) > max_nodes:
                    break
            # Aristas tipadas del frontmatter (typed_out/typed_in) también
            # expanden el BFS — un nodo enlazado SOLO por `links:` (sin
            # wikilink en el body) debe aparecer en el mapa.
            for te in (list(info.get("typed_out", []))
                       + list(info.get("typed_in", []))):
                target = te.get("target") if isinstance(te, dict) else te
                if target not in graph:
                    continue
                if target not in visited:
                    visited.add(target)
                    queue.append((target, d + 1))
                if len(visited) > max_nodes:
                    break
            if len(visited) > max_nodes:
                break

    # Construir nodos (root primero, luego por número de conexiones)
    def _conn_count(rel):
        return (len(graph.get(rel, {}).get("out", []))
                + len(graph.get(rel, {}).get("in", [])))

    ordered = sorted(visited, key=lambda r: (r != root_rel, -_conn_count(r)))
    for rel in ordered:
        try:
            text = (Path(vault) / rel).read_text(encoding="utf-8")
        except OSError:
            text = ""
        fm, _ = parse_frontmatter(text)
        body, height = _node_text(rel, fm)
        ctype = (fm or {}).get("type", "Concepto")
        nodes.append({
            "id": _slug_from_relpath(rel),
            "type": "text",
            "text": body,
            "x": 0,
            "y": 0,
            "width": 320,
            "height": height,
            "color": TYPE_COLOR.get(ctype, "2"),
        })

    # Aristas tipadas (solo entre nodos seleccionados)
    node_ids = {n["id"] for n in nodes}
    used_edges = set()
    for rel in visited:
        for te in graph.get(rel, {}).get("typed_out", []):
            src = _slug_from_relpath(rel)
            dst = _slug_from_relpath(te["target"])
            if src in node_ids and dst in node_ids and (src, dst) not in used_edges:
                used_edges.add((src, dst))
                edges.append({
                    "id": f"e{len(edges)}",
                    "fromNode": src,
                    "toNode": dst,
                    "fromSide": "right",
                    "toSide": "left",
                    "label": te.get("type", ""),
                })

    # Layout inicial en 0 → aplicar algoritmo (o auto-detect)
    algorithm = layout
    if algorithm == "auto":
        algorithm = detect_algorithm(nodes, edges)
    if algorithm == "force":
        layout_force(nodes, edges)
    elif algorithm == "radial":
        layout_radial(nodes, edges, center_id=_slug_from_relpath(root_rel))
    elif algorithm == "dagre":
        layout_dagre(nodes, edges)
    elif algorithm == "grid":
        layout_grid(nodes)

    data = {"nodes": nodes, "edges": edges}
    if output is None:
        output = str(Path(vault) / "mapas" / f"{Path(root_rel).stem}.canvas")
    out_path = _canvas_destino(vault, output)
    if dry_run:
        return {"success": True, "dry_run": True, "output": str(out_path),
                "nodes": len(nodes), "edges": len(edges), "layout": algorithm,
                "root": root_rel}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _save_canvas(out_path, data)

    return {
        "success": True,
        "output": str(out_path),
        "nodes": len(nodes),
        "edges": len(edges),
        "layout": algorithm,
        "root": root_rel,
    }


# ─────────────────────────────────────────────
# Run (argparse entry)
# ─────────────────────────────────────────────

def build_subparser(subparsers):
    """Add the 'canvas' subcommand to the main parser."""
    sp = subparsers.add_parser(
        "canvas",
        help="Generate, layout and validate Obsidian .canvas maps",
        description=("Materializa el grafo del vault como canvas (corteza visual). "
                     "Subcomandos: validate, layout, generate."),
    )
    sub = sp.add_subparsers(dest="canvas_sub", help="Canvas operation")

    # validate
    p_val = sub.add_parser("validate", help="Validate a .canvas file")
    p_val.add_argument("path", help="Path to .canvas file")
    p_val.add_argument("--fix", action="store_true", default=False,
                       help="Auto-fix grid alignment and color types")

    # layout
    p_lay = sub.add_parser("layout", help="Re-layout a .canvas file")
    p_lay.add_argument("path", help="Path to .canvas file")
    p_lay.add_argument("algorithm", choices=["grid", "dagre", "radial", "force", "linear", "auto"],
                       help="Layout algorithm")
    p_lay.add_argument("--direction", default="TB", choices=["TB", "BT", "LR", "RL"],
                       help="Direction for dagre (default: TB)")
    p_lay.add_argument("--columns", type=int, default=None, help="Columns for grid")
    p_lay.add_argument("--center", default=None, help="Center node id for radial")
    p_lay.add_argument("--iterations", type=int, default=100, help="Iterations for force")
    p_lay.add_argument("--dry-run", action="store_true", default=False,
                       help="Compute layout without writing")

    # generate
    p_gen = sub.add_parser("generate", help="Generate a canvas from the vault graph")
    p_gen.add_argument("slug", help="Root concept slug (e.g. insights/foo)")
    p_gen.add_argument("--depth", type=int, default=1, help="BFS depth (default: 1)")
    p_gen.add_argument("--layout", default="auto",
                       choices=["auto", "grid", "dagre", "radial", "force", "linear"],
                       help="Layout algorithm (default: auto-detect)")
    p_gen.add_argument("--max-nodes", type=int, default=80, help="Node cap (default: 80)")
    p_gen.add_argument("--output", default=None, help="Output path (default: mapas/<slug>.canvas)")
    p_gen.add_argument("--dry-run", action="store_true", default=False,
                       help="Build and resolve the destination without writing")

    return sp


def run(args, vault, config):
    """Execute the canvas subcommand."""
    if not args.canvas_sub:
        print("canvas: missing subcommand (validate | layout | generate)", file=sys.stderr)
        return 2

    if args.canvas_sub == "validate":
        try:
            _canvas_destino(vault, args.path)
        except PermissionError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        report = validate_canvas(Path(args.path), fix=args.fix)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["valid"] else 1

    if args.canvas_sub == "layout":
        try:
            _canvas_destino(vault, args.path)
        except PermissionError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        report = layout_canvas(args.path, args.algorithm, dry_run=args.dry_run,
                               direction=args.direction, columns=args.columns,
                               center=args.center, iterations=args.iterations)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    if args.canvas_sub == "generate":
        report = generate_canvas(vault, args.slug, depth=args.depth,
                                 layout=args.layout, output=args.output,
                                 max_nodes=args.max_nodes, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    print(f"canvas: unknown subcommand '{args.canvas_sub}'", file=sys.stderr)
    return 2
