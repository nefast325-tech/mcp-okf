"""Command board — el vault como tablero: JSON para un Kanban de solo lectura.

Qué exporta (una sola pasada sobre el vault):

  - `columnas`: los estados de cada capa, tal como los declara el esquema
    de `types.states` (cli/states.py). Sin esquema, los status que existan.
  - `tarjetas`: una por concepto tipado que no sea de la capa de registro,
    con su capa, status, iniciativa, casillas hechas/total, edad y nivel de
    `stale`. La capa de trabajo ES el Kanban; conocimiento y compromiso
    alimentan el panel de salud.
  - `tareas`: las casillas abiertas con su antigüedad (`search --todos
    --aging`), para la vista de tareas por nota.
  - `salud`: cuántas tarjetas hay por capa y estado, y cuántas marca `stale`.

La iniciativa sale del campo `iniciativa` del frontmatter (string o lista);
sin campo, `null`. `--iniciativa X` filtra tarjetas y tareas por ese valor.
El filtro por iniciativa como FRONTERA (lo que recibe cada socio) lo aplica
quien publica el JSON, por iniciativa, no la página.

Usage:
    python3 -m cli board                      # JSON por stdout
    python3 -m cli board --out tablero.json
    python3 -m cli board --iniciativa inmobiliaria
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli.frontmatter import parse_frontmatter, normalize_tags
from cli.vault import find_md_files


def _git_head(vault):
    try:
        r = subprocess.run(["git", "-C", str(vault), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def _iniciativas(fm):
    value = fm.get("iniciativa")
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _layer_columns(config, types_seen_status):
    """{capa: [estados]} desde el esquema; sin esquema, una capa 'sin-esquema'
    con los status observados."""
    schemas = getattr(config, "states_schemas", None) or {} if config else {}
    columns = {}
    for name, schema in schemas.items():
        states = [str(s) for s in (schema.get("states") or [])]
        if states:
            columns[name] = states
    if not columns:
        columns["sin-esquema"] = sorted({s for s in types_seen_status if s})
    return columns


def build_board(vault, config=None, iniciativa=None, with_tasks=True):
    from cli.states import schema_for
    from cli.commands.stale import collect_stale, count_checkboxes, days_ago, get_today

    today = get_today()
    exempt = config.types_exempt_from_no_backlinks if config else None
    stale_by_file = {r["file"]: r for r in collect_stale(
        vault, exempt_no_backlinks=exempt, config=config)}

    cards = []
    statuses_seen = set()
    for f in find_md_files(vault):
        rel = str(f.relative_to(vault))
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if not fm or not fm.get("type"):
            continue
        ctype = str(fm["type"])
        schema = schema_for(config, ctype)
        if schema is not None and not schema["states"]:
            continue  # capa de registro: no va al tablero
        layer = schema["layer"] if schema else "sin-esquema"
        status = str(fm.get("status") or "")
        statuses_seen.add(status)
        inis = _iniciativas(fm)
        if iniciativa and iniciativa not in inis:
            continue
        done, pending, total = count_checkboxes(text)
        ts = fm.get("timestamp")
        ts_str = ts.isoformat() if isinstance(ts, datetime) else (str(ts) if ts else None)
        stale_r = stale_by_file.get(rel)
        cards.append({
            "file": rel[:-3] if rel.endswith(".md") else rel,
            "carpeta": rel.split("/", 1)[0] if "/" in rel else "",
            "type": ctype,
            "capa": layer,
            "title": str(fm.get("title") or rel),
            "description": str(fm.get("description") or ""),
            "status": status,
            "timestamp": ts_str,
            "edad_dias": days_ago(ts_str, today) if ts_str else None,
            "tags": normalize_tags(fm.get("tags")),
            "iniciativa": inis or None,
            "casillas": {"hechas": done, "total": total},
            "stale": ({"level": stale_r["level"], "signals": stale_r["signals"]}
                      if stale_r else None),
        })

    tasks = []
    if with_tasks:
        from cli.commands.search import find_todos
        allowed = {c["file"] + ".md" for c in cards} if iniciativa else None
        for t in find_todos(vault, with_aging=True):
            rel = t.get("file") or t.get("relpath") or ""
            if allowed is not None and rel not in allowed:
                continue
            tasks.append({
                "file": rel[:-3] if rel.endswith(".md") else rel,
                "nota": str(t.get("context") or rel),
                "line": t.get("line"),
                "text": t.get("text") or "",
                "age_days": t.get("age_days"),
                "age_status": t.get("age_status"),
            })

    columns = _layer_columns(config, statuses_seen)
    salud = {}
    for layer, states in columns.items():
        per_state = {s: 0 for s in states}
        stale_count = 0
        for c in cards:
            if c["capa"] != layer and not (layer == "sin-esquema" and c["capa"] == "sin-esquema"):
                continue
            per_state[c["status"]] = per_state.get(c["status"], 0) + 1
            if (c["stale"] or {}).get("level") == "STALE":
                stale_count += 1
        salud[layer] = {"por_estado": per_state, "stale": stale_count}

    cards.sort(key=lambda c: (c["capa"], c["status"], c["file"]))
    return {
        "generado": today.replace(microsecond=0).isoformat(),
        "commit": _git_head(vault),
        "iniciativa": iniciativa,
        "columnas": columns,
        "tarjetas": cards,
        "tareas": tasks,
        "salud": salud,
    }


def run(args, vault, config=None):
    board = build_board(
        vault, config,
        iniciativa=getattr(args, "iniciativa", None),
        with_tasks=not getattr(args, "no_tasks", False),
    )
    payload = json.dumps(board, indent=2, ensure_ascii=False) + "\n"
    out = getattr(args, "out", None)
    if out:
        Path(out).write_text(payload, encoding="utf-8")
        print(f"✅ board: {len(board['tarjetas'])} tarjetas, {len(board['tareas'])} tareas → {out}")
    else:
        sys.stdout.write(payload)
    return 0
