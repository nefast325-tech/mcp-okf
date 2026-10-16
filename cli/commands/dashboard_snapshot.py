"""Command dashboard-snapshot — Generates dashboard.json + daily snapshots.

Fase 1 del plan DashboardView (Cognitive Trace): un sensor que consolida
SQLite de eventos + estado del vault en un único JSON que un panel Obsidian
puede leer sin llamar tools MCP.

Escribe dos archivos (mismo contenido):
  1. <vault>/dashboard.json                    — último snapshot, para el plugin
  2. <vault>/sistema/dashboard-snapshots/YYYY-MM-DD.json — histórico (30 días)

Las tendencias de 7 días comparan contra el snapshot de hace 7 días:
  - health.trend_7d        ← health_score_trend (score)
  - graph.trend_7d         ← density (decisión de diseño: densidad es el KPI
                             del grafo que muestra el panel)
  - cibernetica.trend_7d   ← cyber_loops_abiertos_trend
  - actividad.trend_7d     ← eventos_semana_trend
  "up"/"down"/"flat"; null si no hay baseline.

Notas de implementación:
  - El check 5 de health (scripts smoke test) queda FUERA del score: son 8
    subprocesos (~30s) que convertirían un sensor rápido en un guard lento.
    `cli health` sigue siendo el check completo autoritativo.
  - Si la db de eventos no existe: los campos SQLite van null/vacíos.
  - stale_distribution usa las claves del plan (FRESCO) mapeando el FRESH
    que produce stale.py.
  - conceptos: detalle por nodo (type/status/timestamp/stale/cyber) para el
    panel. collect_stale corre UNA vez por snapshot y su resultado alimenta
    tanto stale_distribution como conceptos[].stale; status/timestamp/cyber
    salen de una pasada extra de find_md_files + parse_frontmatter.
  - --canvas combina con cli.commands.canvas.generate_canvas: genera un mapa
    de calor alrededor del nodo más visitado en sistema/mapas/.
"""

import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cli.frontmatter import parse_frontmatter
from cli.vault import find_md_files

SNAPSHOTS_DIR = Path("sistema") / "dashboard-snapshots"
# Histórico diario: el panel lee los últimos 30 y las tendencias, el de hace 7
# días. Lo anterior se borra en cada corrida (solo archivos YYYY-MM-DD.json).
SNAPSHOT_RETENTION_DAYS = 30
_SNAPSHOT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAPAS_DIR = Path("sistema") / "mapas"
TOP_VISITED_LIMIT = 10
TOP_NEGLECTED_LIMIT = 10
NEGLECTED_DAYS = 14

# Sesiones de sistema que no representan una sesión cognitiva de agente
# (cajón CLI sin id, comandos directos, watchdogs cron): se excluyen del
# default automático de session_diff, pero se pueden pedir explícitamente.
SYSTEM_SESSIONS_PREFIXES = ("cron_", "session-")
SYSTEM_SESSIONS_EXACT = ("local", "cli")
SESSION_DIFF_MIN_NODES = 2


# ── Helpers de fecha ──

def _domain_today():
    """'Hoy' en el dominio (Colombia, UTC-5) — igual que review.get_today_str."""
    from cli.commands.review import get_today_str
    return datetime.strptime(get_today_str(), "%Y-%m-%d").date()


def _baseline_path(vault, today):
    """Snapshot de hace 7 días, si existe."""
    baseline = today - timedelta(days=7)
    return vault / SNAPSHOTS_DIR / f"{baseline.isoformat()}.json"


def _trend(current, baseline):
    """Compara dos valores y devuelve 'up'/'down'/'flat' (None sin baseline)."""
    if current is None or baseline is None:
        return None
    if current > baseline:
        return "up"
    if current < baseline:
        return "down"
    return "flat"


# ── Consultas SQLite (Cognitive Trace) ──

def _db_events(db_path):
    """Extrae las métricas de actividad del SQLite de eventos.

    Devuelve None si la db no existe o está rota — el sensor no debe crashear.
    """
    if not db_path or not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        now_utc = datetime.now(timezone.utc)
        cutoff_24h = (now_utc - timedelta(hours=24)).isoformat()
        cutoff_7d = (now_utc - timedelta(days=7)).isoformat()
        cutoff_neglected = (now_utc - timedelta(days=NEGLECTED_DAYS)).isoformat()

        eventos_24h = conn.execute(
            "SELECT COUNT(*) FROM events WHERE ts >= ?", (cutoff_24h,)
        ).fetchone()[0]
        eventos_7d = conn.execute(
            "SELECT COUNT(*) FROM events WHERE ts >= ?", (cutoff_7d,)
        ).fetchone()[0]
        sesiones_7d = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM events WHERE ts >= ?",
            (cutoff_7d,),
        ).fetchone()[0]

        # Tool distribution (7d) — misma normalización que analytics.tool_usage
        tools = {}
        for r in conn.execute(
            """SELECT replace(CASE WHEN tool LIKE 'okf_%' THEN substr(tool, 5)
                                   ELSE tool END, '-', '_') as tool_norm,
                      COUNT(*) as cnt
               FROM events WHERE ts >= ?
               GROUP BY tool_norm ORDER BY cnt DESC""",
            (cutoff_7d,),
        ):
            tools[r["tool_norm"]] = r["cnt"]

        # read ratio: promedio por nodo de reads/traverses (un nodo muy
        # transitado no debe enmascarar que el resto nunca se lee)
        ratios = conn.execute(
            """SELECT SUM(CASE WHEN tool_norm='read' THEN 1 ELSE 0 END) * 1.0
                       / NULLIF(SUM(CASE WHEN tool_norm='traverse' THEN 1 ELSE 0 END), 0) as ratio
               FROM v_node_events WHERE ts >= ? GROUP BY raw_node""",
            (cutoff_7d,),
        ).fetchall()
        if ratios:
            read_ratio_promedio = sum(r["ratio"] or 0 for r in ratios) / len(ratios)
        else:
            read_ratio_promedio = None

        # Top visitados con read vs traverse (slug completo = raw_node)
        top_visited = []
        for r in conn.execute(
            """SELECT raw_node AS slug,
                      SUM(CASE WHEN tool_norm='traverse' THEN 1 ELSE 0 END) as traverses,
                      SUM(CASE WHEN tool_norm='read' THEN 1 ELSE 0 END) as reads
               FROM v_node_events
               GROUP BY raw_node ORDER BY traverses DESC LIMIT ?""",
            (TOP_VISITED_LIMIT,),
        ):
            traverses = r["traverses"] or 0
            reads = r["reads"] or 0
            top_visited.append({
                "slug": r["slug"],
                "traverses": traverses,
                "reads": reads,
                "read_ratio": (reads / traverses) if traverses else None,
            })

        # Nodos descuidados: última visita traverse hace 14+ días
        top_neglected = []
        for r in conn.execute(
            """SELECT raw_node AS slug, MAX(ts) AS last_visit
               FROM v_node_events WHERE tool_norm = 'traverse'
               GROUP BY raw_node HAVING MAX(ts) < ?
               ORDER BY last_visit ASC LIMIT ?""",
            (cutoff_neglected, TOP_NEGLECTED_LIMIT),
        ):
            try:
                last = datetime.fromisoformat(r["last_visit"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                days = (now_utc - last).days
            except (ValueError, TypeError):
                days = None
            top_neglected.append({
                "slug": r["slug"],
                "days_since_last_visit": days,
            })

        # Entry points: traverses de entrada más frecuentes (misma semántica
        # que analytics._query_entry_points, pero con slug completo)
        entry_points = [
            r["entry"] for r in conn.execute(
                """SELECT raw_node AS entry, COUNT(*) as cnt
                   FROM v_node_events WHERE tool_norm = 'traverse'
                   GROUP BY raw_node ORDER BY cnt DESC LIMIT 3"""
            )
        ]

        conn.close()
        return {
            "eventos_24h": eventos_24h,
            "eventos_7d": eventos_7d,
            "sesiones_7d": sesiones_7d,
            "tools": tools,
            "read_ratio_promedio": read_ratio_promedio,
            "top_visited": top_visited,
            "top_neglected": top_neglected,
            "entry_points_top3": entry_points,
        }
    except Exception:
        return None


# ── Session Diff (capa del DashboardView) ──

def _is_system_session(session_id):
    """True si el id corresponde a un canal de sistema, no a una sesión de
    agente con identidad cognitiva (cajón CLI local, comandos directos,
    watchdogs cron, UUIDs legacy del harness viejo)."""
    if session_id in SYSTEM_SESSIONS_EXACT:
        return True
    return any(session_id.startswith(p) for p in SYSTEM_SESSIONS_PREFIXES)


def _candidate_sessions(conn, limit=10):
    """Sesiones de agente con navegación real (traverse/read con slug O
    target), ordenadas por última actividad. Excluye canales de sistema: son
    ruido para el diff (mezclan muchas sesiones o no tienen exploración
    cognitiva).

    Cuenta sobre events con COALESCE(slug, target) — dos formas de params
    conviven en la DB (ver _session_nodes). v_node_events aplica el mismo
    criterio (ver cli/telemetry.py).

    Devuelve lista de dicts {session_id, nodos, eventos, ultimo_ts}.
    """
    rows = conn.execute(
        """SELECT session_id,
                  COUNT(DISTINCT COALESCE(json_extract(params, '$.slug'),
                                          json_extract(params, '$.target'))) AS nodos,
                  COUNT(*) AS eventos,
                  MAX(ts) AS ultimo_ts
           FROM events
           WHERE tool IN ('okf_traverse','traverse','okf_read','read')
             AND (json_extract(params, '$.slug') IS NOT NULL
                  OR json_extract(params, '$.target') IS NOT NULL)
           GROUP BY session_id
           ORDER BY ultimo_ts DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {
            "session_id": r["session_id"],
            "nodos": r["nodos"],
            "eventos": r["eventos"],
            "ultimo_ts": r["ultimo_ts"],
        }
        for r in rows
        if not _is_system_session(r["session_id"])
    ]


def _session_nodes(conn, session_id):
    """Basenames de los nodos que la sesión atravesó o leyó.

    Normaliza dos formas de params en la misma DB: el server escribe
    params.slug ('frameworks/tp3-cibernetico'); el CLI (argumento target de
    traverse y read) y el harness dsh escriben params.target
    ('tp3-cibernetico'). Se reduce a basename (último
    segmento) — misma semántica que analytics session_diff — para que el
    diff funcione entre sesiones de cualquier generación y el grafo matchee
    por filename (GraphAnimator.nodeMatches).
    """
    rows = conn.execute(
        """SELECT COALESCE(json_extract(params, '$.slug'),
                          json_extract(params, '$.target')) AS raw
           FROM events
           WHERE session_id = ? AND tool IN ('okf_traverse','traverse',
                                             'okf_read','read')
             AND (json_extract(params, '$.slug') IS NOT NULL
                  OR json_extract(params, '$.target') IS NOT NULL)""",
        (session_id,),
    ).fetchall()
    nodos = set()
    for r in rows:
        raw = (r["raw"] or "").strip()
        if not raw:
            continue
        base = raw.rsplit("/", 1)[-1]
        if base.endswith(".md"):
            base = base[:-3]
        nodos.add(base)
    return nodos


def _session_diff_section(db_path, session_a=None, session_b=None):
    """Genera la sección session_diff del snapshot: nodos solo en A, solo en
    B, y en ambas (slugs completos, listas ordenadas).

    Selección de sesiones:
      - session_a/session_b explícitos (flags --session-a/--session-b):
        comparación pedida por el usuario. Acepta cualquier id, incluso de
        sistema (local/cli/cron_*), para debug o comparaciones puntuales.
      - sin flags: las 2 sesiones de agente más recientes con navegación
        (excluye canales de sistema). Si no hay 2, devuelve None → la capa
        Session Diff queda deshabilitada en el panel (sin datos, sin ruido).

    Formato (contrato con dashboard_view.ts):
      {session_a: {id, nodos}, session_b: {id, nodos},
       solo_a: [slug...], solo_b: [slug...], ambas: [slug...]}
    """
    if not db_path or not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        if session_a and session_b:
            a_id, b_id = session_a, session_b
        else:
            candidatos = _candidate_sessions(conn)
            if len(candidatos) < 2:
                conn.close()
                return None
            a_id = candidatos[0]["session_id"]
            b_id = candidatos[1]["session_id"]

        nodos_a = _session_nodes(conn, a_id)
        nodos_b = _session_nodes(conn, b_id)
        conn.close()

        if not nodos_a and not nodos_b:
            return None

        solo_a = sorted(nodos_a - nodos_b)
        solo_b = sorted(nodos_b - nodos_a)
        ambas = sorted(nodos_a & nodos_b)
        return {
            "session_a": {"id": a_id, "nodos": len(nodos_a)},
            "session_b": {"id": b_id, "nodos": len(nodos_b)},
            "solo_a": solo_a,
            "solo_b": solo_b,
            "ambas": ambas,
        }
    except Exception:
        return None


# ── Consultas del vault (imports directos, patrón dashboard.py) ──

def _health_section(vault, config):
    """Score de salud replicando health.run sin el check 5 (scripts, lento)."""
    from cli.commands.health import (
        _check_frontmatter, _check_indices, _check_graph,
        _check_broken_links, _check_git_hook, _check_cyber,
        _check_plugin_hash_sync, _check_timestamp_git,
    )

    fm_ok, fm_bad, fm_warn = _check_frontmatter(vault)
    idx_ok, idx_stale = _check_indices(vault)
    graph_data, graph_warn = _check_graph(vault)
    broken = _check_broken_links(vault)
    hook_ok, hook_err = _check_git_hook(vault)
    excluded_cyber = config.types_excluded_cyber if config else None
    cyber_ok, cyber_warn, cyber_err = _check_cyber(vault, excluded_cyber=excluded_cyber)

    plugin_hash_enabled = config.features_plugin_hash_sync if config else True
    plugin_stale = []
    if plugin_hash_enabled:
        _po, plugin_stale = _check_plugin_hash_sync(vault)
    ts_ok, ts_warn, ts_err = _check_timestamp_git(vault)

    errors = (len(fm_bad) + (0 if hook_ok else 1) + len(cyber_err) + len(ts_err)
              + (1 if graph_data is None else 0))
    warnings = (len(fm_warn) + len(idx_stale) + len(graph_warn) + len(broken)
                + len(cyber_warn) + len(plugin_stale) + len(ts_warn))

    checks_total = 8  # 1-4, 6-9 (sin check 5: scripts smoke test, ver docstring)
    score_items = [
        1 if len(fm_bad) == 0 else 0,
        1 if len(idx_stale) == 0 else 0,
        1 if graph_data and graph_data.get("orphans", 99) == 0 else 0,
        1 if len(broken) == 0 else 0,
        1 if hook_ok else 0,
        1 if len(cyber_err) == 0 else 0,
        1 if len(ts_err) == 0 and len(ts_warn) == 0 else 0,
    ]
    if plugin_hash_enabled:
        checks_total += 1
        score_items.append(1 if len(plugin_stale) == 0 else 0)

    warnings_detail = (broken[:5] + fm_warn[:3] + idx_stale[:3] + cyber_warn[:3]
                       + plugin_stale[:3] + ts_warn[:3])[:10]
    return {
        "score": sum(score_items),
        "max_score": checks_total,
        "errors": errors,
        "warnings": warnings,
        "warnings_detail": warnings_detail,
    }


def _graph_section(vault):
    """Estadísticas del grafo desde build_graph (cli.commands.graph).

    Una sola pasada de build_graph (health._check_graph vuelve a construir
    el grafo internamente; replicamos aquí su lógica de huérfanos/leaf para
    no parsear el vault dos veces extra).
    """
    from cli.commands.graph import build_graph
    try:
        g = build_graph(vault)
    except Exception:
        return {"total_nodes": None, "total_edges": None, "orphans": None,
                "hubs_top5": [], "density": None}

    # Misma exclusión de leaf que health._check_graph
    leaf_nodes = {n for n, d in g.items() if d.get("leaf")}
    leaf_nodes |= {n for n in g if n.startswith("agentes/") or n.startswith("sesiones/")}

    nodes = len(g)
    edges = sum(len(d["out"]) for d in g.values())
    orphans = sum(1 for n, d in g.items()
                  if not d["in"] and not d["out"]
                  and not d["typed_in"] and not d["typed_out"]
                  and n not in leaf_nodes)
    density = edges / max(nodes * (nodes - 1), 1)

    degree = {n: len(d.get("in", [])) + len(d.get("out", [])) for n, d in g.items()}
    hubs = sorted(degree.items(), key=lambda kv: -kv[1])[:5]
    return {
        "total_nodes": nodes,
        "total_edges": edges,
        "orphans": orphans,
        "hubs_top5": [n[:-3] if n.endswith(".md") else n for n, _deg in hubs],
        "density": density,
    }


def _cibernetica_section(vault):
    """Loops cibernéticos: bloques cyber, outcomes y review_on.

    review_on_vencidos cuenta SOLO los reviews ya vencidos (review_on < hoy):
    reusa review.collect_due y su campo ``vencido`` — la misma semántica que
    health (loop roto) y que conceptos[].cyber.vencido. Un review que vence
    hoy no está roto: sigue en la lista de review y se cuenta aparte en
    review_on_hoy. loops_abiertos = outcome pendiente o sin medir;
    loops_cerrados = outcome success/failure.

    Las listas ``*_nodes`` acompañan a cada contador con los nodos que lo
    componen, para que la capa Cyber del plugin coloree el grafo
    (dashboard_view.buildCyberNodes). Formato del identificador: la ruta
    relativa sin ``.md``, igual que conceptos[].file. Son opcionales para el
    plugin: si faltan, la capa queda vacía sin romper nada.
    """
    from cli.commands.review import collect_due
    due = collect_due(vault)
    vencidos = sum(1 for d in due if d["vencido"])
    hoy = sum(1 for d in due if not d["vencido"])

    total_blocks = 0
    outcome_pending = 0
    outcome_success = 0
    outcome_failure = 0
    proximos_7d = 0
    vencidos_nodes = []
    outcome_pending_nodes = []
    outcome_success_nodes = []
    outcome_failure_nodes = []

    from cli.commands.review import get_today_str
    today = get_today_str()
    limite = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")

    for f in find_md_files(vault):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if not fm:
            continue
        cyber = fm.get("cyber")
        if not isinstance(cyber, dict):
            continue
        total_blocks += 1
        rel = str(f.relative_to(vault))
        slug = rel[:-3] if rel.endswith(".md") else rel
        outcome = str(cyber.get("outcome", ""))
        if outcome == "pending":
            outcome_pending += 1
            outcome_pending_nodes.append(slug)
        elif outcome == "success":
            outcome_success += 1
            outcome_success_nodes.append(slug)
        elif outcome == "failure":
            outcome_failure += 1
            outcome_failure_nodes.append(slug)
        review_on = str(cyber.get("review_on", "") or "")
        if review_on and today < review_on <= limite:
            proximos_7d += 1
        # Mismo criterio que el agregado: vencido = review_on < hoy, sin
        # mirar el outcome (un loop cerrado con fecha pasada también venció).
        if review_on and review_on < today:
            vencidos_nodes.append(slug)

    loops_abiertos = outcome_pending + (total_blocks - outcome_pending
                                        - outcome_success - outcome_failure)
    loops_cerrados = outcome_success + outcome_failure
    return {
        "total_blocks": total_blocks,
        "loops_cerrados": loops_cerrados,
        "loops_abiertos": loops_abiertos,
        "review_on_vencidos": vencidos,
        "review_on_hoy": hoy,
        "review_on_proximos_7d": proximos_7d,
        "outcome_pending": outcome_pending,
        "outcome_success": outcome_success,
        "outcome_failure": outcome_failure,
        "review_on_vencidos_nodes": sorted(vencidos_nodes),
        "outcome_pending_nodes": sorted(outcome_pending_nodes),
        "outcome_success_nodes": sorted(outcome_success_nodes),
        "outcome_failure_nodes": sorted(outcome_failure_nodes),
    }


def _infracciones_7d(vault, config):
    """Infracciones MCP (uso directo de write_file/patch) de las minutas de
    sesión de los últimos 7 días — fuente vault, no SQLite."""
    from cli.commands.session_metrics import _sessions_dir, _parse_metrics_section
    sesiones_dir = _sessions_dir(vault, config)
    if not sesiones_dir.exists():
        return 0
    corte = datetime.now(timezone.utc) - timedelta(days=7)
    total = 0
    for f in sorted(sesiones_dir.glob("sesion-*.md")):
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(content)
        ts_raw = (fm or {}).get("timestamp")
        try:
            ts = datetime.fromisoformat(str(ts_raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < corte:
                continue
        except (ValueError, TypeError):
            continue
        metrics = _parse_metrics_section(content)
        total += metrics.get("infracciones", 0)
    return total


def _collect_stale_results(vault, config):
    """Una pasada de collect_stale compartida por _stale_distribution y
    _conceptos_section — el scan (≈570 nodos + git index) no se duplica."""
    from cli.commands.stale import collect_stale
    if config:
        return collect_stale(
            vault, config.stale_timestamp_days, config.stale_propuesta_days,
            config.stale_no_commits_days, config.stale_checkbox_ratio,
            config.stale_problem_patterns, config.stale_reads_zero_min_days,
            config.types_exempt_from_no_backlinks,
            config.stale_propuesta_days_insight,
            # La config COMPLETA tiene que llegar al detector, no solo sus
            # umbrales: sin `config=`, `collect_stale` cae en los literales
            # legacy (señal 3 = `propuesta`, señal 6 = Decision) mientras
            # `stale` y `board` leen el esquema del vault. Medido en OKF-Vault
            # con el esquema activo: 72 de 700 conceptos divergían —el panel
            # daba ATTENTION donde el CLI daba STALE—. Misma clase que el #21,
            # el #29 y el #30.
            config=config,
        )
    return collect_stale(vault)


def _stale_distribution(results):
    """Distribución FRESCO/ATENCION/STALE (stale.py produce FRESH en inglés)."""
    dist = {"FRESCO": 0, "ATENCION": 0, "STALE": 0}
    for r in results:
        level = r["level"]
        if level == "FRESH":
            dist["FRESCO"] += 1
        elif level == "ATTENTION":
            dist["ATENCION"] += 1
        else:
            dist["STALE"] += 1
    return dist


# Niveles de staleness en el schema del dashboard (inglés → español del plan)
_STALE_LEVEL_ES = {"FRESH": "FRESCO", "ATTENTION": "ATENCION", "STALE": "STALE"}


def _cyber_por_nodo(fm, today_iso):
    """Bloque cyber de un nodo normalizado para conceptos[].cyber.

    None si el frontmatter no tiene bloque cyber (o no es dict). vencido =
    review_on existe y es anterior a hoy (comparación ISO) — la misma
    semántica que review.collect_due()[].vencido y que el agregado
    cibernetica.review_on_vencidos; un review que vence hoy queda en False.
    target_metric se reduce al nombre de la métrica (el vault usa
    {name, target}).
    """
    cyber = fm.get("cyber")
    if not isinstance(cyber, dict):
        return None
    review_on = cyber.get("review_on")
    review_on_str = str(review_on) if review_on else None
    metric = cyber.get("target_metric")
    if isinstance(metric, dict):
        target_metric = str(metric["name"]) if metric.get("name") else None
    elif metric:
        target_metric = str(metric)
    else:
        target_metric = None
    return {
        "outcome": str(cyber.get("outcome", "") or ""),
        "review_on": review_on_str,
        "vencido": bool(review_on_str) and review_on_str < today_iso,
        "target_metric": target_metric,
    }


def _conceptos_section(vault, stale_results):
    """Detalle por concepto: una entrada por nodo con type en su frontmatter.

    Criterios:
      - Nodos con frontmatter pero sin type NO son conceptos: se omiten.
      - index.md/log.md/dashboard.md generados ya los excluye find_md_files.
      - stale se toma de los resultados compartidos de collect_stale; si un
        concepto no aparece allí (frontmatter ilegible en esa pasada),
        stale=null y queda al final del orden.

    Orden: STALE → ATENCION → FRESCO, dentro por file; sin stale al final.
    """
    stale_by_file = {r["file"]: r for r in stale_results}
    today_iso = _domain_today().isoformat()
    conceptos = []
    for f in find_md_files(vault):
        rel = str(f.relative_to(vault))
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if not fm or not fm.get("type"):
            continue
        ts = fm.get("timestamp")
        if isinstance(ts, datetime):
            timestamp = ts.isoformat()
        elif ts:
            timestamp = str(ts)
        else:
            timestamp = None
        stale_r = stale_by_file.get(rel)
        if stale_r is None:
            stale = None
        else:
            stale = {
                "level": _STALE_LEVEL_ES.get(stale_r["level"], stale_r["level"]),
                "signal_count": stale_r["signal_count"],
                "signals": stale_r["signals"],
            }
        conceptos.append({
            "file": rel[:-3] if rel.endswith(".md") else rel,
            "type": str(fm.get("type", "")),
            "title": str(fm.get("title", rel)),
            "status": str(fm.get("status", "") or ""),
            "timestamp": timestamp,
            "stale": stale,
            "cyber": _cyber_por_nodo(fm, today_iso),
        })

    orden = {"STALE": 0, "ATENCION": 1, "FRESCO": 2}
    conceptos.sort(key=lambda c: (
        orden.get((c["stale"] or {}).get("level"), 9), c["file"]))
    return conceptos


# ── Construcción del snapshot ──

def _build_snapshot(vault, config=None, db_path=None, source="manual",
                    session_a=None, session_b=None):
    """Construye el dict del snapshot con el schema del plan DashboardView."""
    from cli.commands.analytics import _resolve_db_path
    if db_path is None:
        db_path = _resolve_db_path(config, vault)
    db = _db_events(db_path)

    health = _health_section(vault, config)
    graph = _graph_section(vault)
    cibernetica = _cibernetica_section(vault)
    stale_results = _collect_stale_results(vault, config)
    dist = _stale_distribution(stale_results)
    conceptos = _conceptos_section(vault, stale_results)

    actividad = {
        "sesiones_7d": db["sesiones_7d"] if db else None,
        "eventos_7d": db["eventos_7d"] if db else None,
        "eventos_24h": db["eventos_24h"] if db else None,
        "tools_usadas": db["tools"] if db else {},
        "read_ratio_promedio": db["read_ratio_promedio"] if db else None,
        "infracciones_mcp_7d": _infracciones_7d(vault, config),
        "entry_points_top3": db["entry_points_top3"] if db else [],
    }

    calor_estructural = {
        "top_visited": db["top_visited"] if db else [],
        "top_neglected": db["top_neglected"] if db else [],
        "stale_distribution": dist,
    }

    # ── Tendencias vs snapshot de hace 7 días ──
    today = _domain_today()
    baseline = None
    baseline_path = _baseline_path(vault, today)
    if baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            baseline = None

    base_health = (baseline or {}).get("health") or {}
    base_act = (baseline or {}).get("actividad") or {}
    base_ciber = (baseline or {}).get("cibernetica") or {}
    base_graph = (baseline or {}).get("graph") or {}
    base_calor = (baseline or {}).get("calor_estructural") or {}
    base_stale_dist = base_calor.get("stale_distribution") or {}

    health_score_trend = _trend(health["score"], base_health.get("score"))
    eventos_semana_trend = _trend(actividad["eventos_7d"], base_act.get("eventos_7d"))
    stale_count_trend = _trend(dist["STALE"], base_stale_dist.get("STALE"))
    cyber_loops_abiertos_trend = _trend(cibernetica["loops_abiertos"],
                                        base_ciber.get("loops_abiertos"))
    read_ratio_trend = _trend(actividad["read_ratio_promedio"],
                              base_act.get("read_ratio_promedio"))

    health["trend_7d"] = health_score_trend
    graph["trend_7d"] = _trend(graph["density"], base_graph.get("density"))
    cibernetica["trend_7d"] = cyber_loops_abiertos_trend
    actividad["trend_7d"] = eventos_semana_trend

    # ── Session Diff: nodos solo en A / solo en B / en ambas ──
    # (capa del DashboardView; None sin 2 sesiones comparables → capa
    # deshabilitada en el panel, sin datos no hay ruido visual)
    session_diff = _session_diff_section(db_path, session_a=session_a,
                                         session_b=session_b)

    snapshot = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "generated_by": "cli dashboard-snapshot",
        "source": source,
        "health": health,
        "graph": graph,
        "cibernetica": cibernetica,
        "actividad": actividad,
        "calor_estructural": calor_estructural,
        "conceptos": conceptos,
        "negocio": None,  # fase 2 del plan: Umami API + D1, fuera del vault
        "session_diff": session_diff,
        "tendencias": {
            "health_score_trend": health_score_trend,
            "eventos_semana_trend": eventos_semana_trend,
            "stale_count_trend": stale_count_trend,
            "cyber_loops_abiertos_trend": cyber_loops_abiertos_trend,
            "read_ratio_trend": read_ratio_trend,
        },
    }
    return snapshot


def _write_snapshot(vault, snapshot, today):
    """Escribe dashboard.json y el snapshot diario (mismo contenido)."""
    payload = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"

    dashboard_path = vault / "dashboard.json"
    dashboard_path.write_text(payload, encoding="utf-8")

    snapshots_dir = vault / SNAPSHOTS_DIR
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    daily = snapshots_dir / f"{today.isoformat()}.json"
    daily.write_text(payload, encoding="utf-8")
    _prune_snapshots(snapshots_dir, today)
    return dashboard_path, daily


def _prune_snapshots(snapshots_dir, today, keep_days=SNAPSHOT_RETENTION_DAYS):
    """Borra los snapshots diarios de más de `keep_days` días (hoy incluido).

    Solo considera archivos YYYY-MM-DD.json con fecha válida: cualquier otro
    archivo de la carpeta queda intacto. Devuelve la lista de borrados.
    """
    limite = today - timedelta(days=keep_days - 1)
    borrados = []
    for f in sorted(snapshots_dir.glob("*.json")):
        if not _SNAPSHOT_NAME.match(f.stem):
            continue
        try:
            dia = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if dia < limite:
            try:
                f.unlink()
                borrados.append(f)
            except OSError:
                pass
    return borrados


def _generate_heat_canvas(vault, snapshot, today):
    """--canvas: mapa de calor alrededor del nodo más visitado.

    Combina el snapshot con cli.commands.canvas.generate_canvas (fase 4 del
    plan): el top_visited[0] es el "hottest node" y el mapa materializa su
    vecindario tipado en sistema/mapas/calor-YYYY-MM-DD.canvas.
    """
    from cli.commands.canvas import generate_canvas
    top = (snapshot.get("calor_estructural") or {}).get("top_visited") or []
    if not top:
        return None
    slug = top[0]["slug"]
    output = vault / MAPAS_DIR / f"calor-{today.isoformat()}.canvas"
    report = generate_canvas(vault, slug, depth=1, output=str(output))
    if not report.get("success"):
        return None
    return output


def run(args, vault, config=None):
    """Genera dashboard.json + snapshot diario (y opcionalmente el canvas)."""
    db_path = getattr(args, "db", None)
    source = getattr(args, "source", "manual")
    want_canvas = getattr(args, "canvas", False)
    session_a = getattr(args, "session_a", None) or None
    session_b = getattr(args, "session_b", None) or None

    snapshot = _build_snapshot(vault, config=config, db_path=db_path,
                               source=source, session_a=session_a,
                               session_b=session_b)
    today = _domain_today()
    dashboard_path, daily = _write_snapshot(vault, snapshot, today)
    print(f"  ✓ {dashboard_path.relative_to(vault)} "
          f"({len(json.dumps(snapshot))} bytes JSON)")
    print(f"  ✓ {daily.relative_to(vault)} (histórico)")

    if want_canvas:
        canvas_out = _generate_heat_canvas(vault, snapshot, today)
        if canvas_out:
            print(f"  ✓ {canvas_out.relative_to(vault)} (canvas de calor)")
        else:
            print("  - canvas omitido (sin top_visited o slug fuera del grafo)",
                  file=sys.stderr)
    return 0
