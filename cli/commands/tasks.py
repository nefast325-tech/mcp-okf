"""Command tasks — las tareas como notas (fase 3 del rediseño del vault).

Una casilla `- [ ]` es una lista de chequeo: vive dentro de la nota que la
contiene y no tiene dueño ni fecha. Lo que tiene responsable o vencimiento es
una tarea, y una tarea es una nota del tipo que el vault declare en
`types.task` (por defecto `Task`): con `status` de la capa de trabajo,
`responsable` y `vence` como campos (validados por `types.fields`,
cli/fields.py) y sus relaciones como aristas —`aplica` → el Plan o Project del
que forma parte, `depende` → lo que la bloquea.

Dos subcomandos:

  tasks list [--responsable X] [--iniciativa X] [--status X] [--vencidas] [--json]
      Las tareas del vault, vencidas primero y luego por fecha. La iniciativa
      es la del campo de la tarea o, si no lo lleva, la de la nota a la que
      `aplica`.

  tasks promote <nota> (--line N | --match TEXTO) --responsable X [--vence FECHA]
      Convierte una casilla abierta de <nota> en una nota de tarea: crea la
      tarea con `aplica` → <nota> y reemplaza la casilla por un enlace a la
      tarea, así la casilla deja de contarse dos veces (search --todos, board).

Crear, mover y asignar no necesitan comando propio: `new --type Task --field
responsable=…`, `edit --status …`, `edit --field responsable=…`.
"""

import json
import re
import sys
from datetime import date, datetime

from cli.frontmatter import parse_frontmatter
from cli.vault import find_md_files

_CHECKBOX_RE = re.compile(r"^(\s*)- \[ \]\s+(.+?)\s*$")


def _task_type(config):
    return config.types_task if config else "Task"


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _list_field(fm, key):
    value = fm.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _resolve_target(vault, target):
    for cand in (vault / str(target), vault / (str(target) + ".md")):
        if cand.exists():
            return cand
    return None


def _parent_of(vault, fm):
    """(rel, frontmatter) de la nota a la que la tarea `aplica`, o (None, {})."""
    for link in fm.get("links") or []:
        if isinstance(link, dict) and link.get("type") == "aplica":
            target = _resolve_target(vault, link.get("target", ""))
            if target is None:
                return str(link.get("target", "")), {}
            try:
                pfm, _ = parse_frontmatter(target.read_text(encoding="utf-8"))
            except Exception:
                pfm = {}
            return str(target.relative_to(vault)), (pfm or {})
    return None, {}


def _done_states(config, task_type):
    """Estados «terminado» de la capa del tipo (`done` en `types.states`): una
    tarea ahí no vence. Se lee de la config cruda y no de `schema_for`, que
    expone `done` solo desde el PR #34; sin `done`, ninguna se excluye."""
    schemas = getattr(config, "states_schemas", None) or {} if config else {}
    for schema in schemas.values():
        if isinstance(schema, dict) and task_type in (schema.get("types") or []):
            return {str(s) for s in (schema.get("done") or [])}
    return set()


def collect_tasks(vault, config=None, responsable=None, iniciativa=None,
                  status=None, vencidas=False, today=None):
    """Las notas de tarea del vault como dicts, filtradas y ordenadas."""
    task_type = _task_type(config)
    today = today or date.today()
    done_states = _done_states(config, task_type)

    tasks = []
    for f in find_md_files(vault):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if not fm or str(fm.get("type", "")) != task_type:
            continue
        rel = str(f.relative_to(vault))
        parent_rel, parent_fm = _parent_of(vault, fm)
        inis = _list_field(fm, "iniciativa") or _list_field(parent_fm, "iniciativa")
        st = str(fm.get("status") or "")
        vence = _as_date(fm.get("vence"))
        vencida = vence is not None and vence < today and st not in done_states
        resp = str(fm.get("responsable") or "")

        if responsable and resp != responsable:
            continue
        if iniciativa and iniciativa not in inis:
            continue
        if status and st != status:
            continue
        if vencidas and not vencida:
            continue
        tasks.append({
            "file": rel[:-3] if rel.endswith(".md") else rel,
            "title": str(fm.get("title") or rel),
            "status": st,
            "responsable": resp or None,
            "vence": vence.isoformat() if vence else None,
            "vencida": vencida,
            "iniciativa": inis or None,
            "aplica": (parent_rel[:-3] if parent_rel and parent_rel.endswith(".md") else parent_rel),
            "aplica_title": str(parent_fm.get("title")) if parent_fm.get("title") else None,
        })

    tasks.sort(key=lambda t: (
        not t["vencida"],
        t["vence"] or "9999-12-31",
        t["responsable"] or "~",
        t["file"],
    ))
    return tasks


def _clean_item(text):
    """Texto de una casilla sin marcas de énfasis ni enlaces, para el título."""
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\*\*|__|`", "", text)
    return text.strip()


def promote(vault, config, source, line=None, match=None, responsable=None,
            vence=None, title=None, description=None, dry_run=False):
    """Convierte la casilla abierta `line`/`match` de `source` en una nota de tarea."""
    from cli.commands.edit import _resolve
    from cli.commands.new import _build_frontmatter, _slugify
    from cli.edge_types import validate_cross_type_pair
    from cli.fields import check_fields, fields_strict
    from cli.states import initial_status

    task_type = _task_type(config)
    valid_types = set(config.types_valid) if config else set()
    if valid_types and task_type not in valid_types:
        print(f"❌ El vault no declara el tipo '{task_type}' en types.valid "
              f"(es el tipo de tarea, `types.task`)", file=sys.stderr)
        return 1

    src = _resolve(vault, source)
    if src is None:
        print(f"❌ Not found: {source}", file=sys.stderr)
        return 1
    src_rel = str(src.relative_to(vault))
    text = src.read_text(encoding="utf-8")
    src_fm, _ = parse_frontmatter(text)
    src_fm = src_fm or {}
    lines = text.split("\n")

    idx = None
    if line is not None:
        if not (1 <= int(line) <= len(lines)):
            print(f"❌ {src_rel} tiene {len(lines)} líneas; no hay línea {line}", file=sys.stderr)
            return 1
        idx = int(line) - 1
    elif match:
        for i, l in enumerate(lines):
            if match in l and _CHECKBOX_RE.match(l):
                idx = i
                break
        if idx is None:
            print(f"❌ Ninguna casilla abierta de {src_rel} contiene «{match}»", file=sys.stderr)
            return 1
    else:
        print("❌ Indicá la casilla con --line N o --match TEXTO", file=sys.stderr)
        return 1

    m = _CHECKBOX_RE.match(lines[idx])
    if not m:
        print(f"❌ La línea {idx + 1} de {src_rel} no es una casilla abierta (`- [ ] …`): "
              f"{lines[idx].strip()[:80]}", file=sys.stderr)
        return 1
    indent, item_text = m.group(1), m.group(2)

    title = (title or _clean_item(item_text)).strip()
    if not title:
        print("❌ La casilla no deja título; pasá --title", file=sys.stderr)
        return 1
    parent_title = str(src_fm.get("title") or src_rel)
    description = (description or f"{title}. Tarea sacada de «{parent_title}».").strip()

    type_dir = (dict(config.types_directory) if config else {}).get(task_type, "tasks")
    slug = _slugify(title)[:90].strip("-")
    if not slug:
        print("❌ El título no produce un nombre de archivo; pasá --title", file=sys.stderr)
        return 1
    task_path = vault / type_dir / f"{slug}.md"
    if task_path.exists():
        print(f"❌ Ya existe: {task_path.relative_to(vault)} (pasá otro --title)", file=sys.stderr)
        return 1

    extra = {}
    if responsable:
        extra["responsable"] = str(responsable).strip()
    if vence:
        if _as_date(vence) is None:
            print(f"❌ --vence '{vence}' no es una fecha ISO (YYYY-MM-DD)", file=sys.stderr)
            return 1
        extra["vence"] = _as_date(vence).isoformat()
    problems = check_fields(config, task_type, extra)
    if problems and fields_strict(config):
        for p in problems:
            print(f"❌ {p} (pasalo con --responsable / --vence)", file=sys.stderr)
        return 1
    for p in problems:
        print(f"⚠️  {p}", file=sys.stderr)

    # El par (Task aplica <tipo de la nota>) tiene que estar declarado si el
    # vault es estricto con los pares: mejor avisarlo acá que en el hook.
    definitions = config.edge_type_definitions() if config else None
    pair_warnings = validate_cross_type_pair(
        task_type, str(src_fm.get("type", "?")), "aplica", definitions=definitions)
    if pair_warnings and config is not None and getattr(config, "graph_strict_pairs", False):
        for w in pair_warnings:
            print(f"❌ {w}", file=sys.stderr)
        print(f"   Declará el par en .okf.config.yaml: edge_types.aplica.valid_pairs "
              f"← [{task_type}, {src_fm.get('type', '?')}]", file=sys.stderr)
        return 1
    for w in pair_warnings:
        print(f"⚠️  {w}", file=sys.stderr)

    status = initial_status(config, task_type)
    extra_lines = [f"{k}: {v}" for k, v in extra.items()]
    frontmatter = _build_frontmatter(
        task_type, title, description, status, None, None, False,
        links=[{"target": src_rel, "type": "aplica"}], config=config,
        extra_lines=extra_lines)
    parent_stem = src_rel[:-3] if src_rel.endswith(".md") else src_rel
    body = (
        "\n## Qué hay que hacer\n\n"
        f"{item_text}\n\n"
        f"Sale de [[{parent_stem}|{parent_title}]] (línea {idx + 1}).\n\n"
        "## Hecho cuando\n\n"
        "(¿Qué tiene que ser cierto para marcarla `hecha`?)\n"
    )
    task_rel = f"{type_dir}/{slug}"
    new_line = f"{indent}- → [[{task_rel}|{item_text}]]"

    if dry_run:
        print(f"🔍 DRY-RUN — {task_rel}.md")
        print("─" * 60)
        print(frontmatter + body)
        print("─" * 60)
        print(f"{src_rel}:{idx + 1}  {lines[idx].strip()}  →  {new_line.strip()}")
        return 0

    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(frontmatter + body, encoding="utf-8")
    lines[idx] = new_line
    src.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Tarea: {task_rel}.md  (status: {status or '(sin status)'}"
          + (f", responsable: {extra['responsable']}" if extra.get("responsable") else "")
          + (f", vence: {extra['vence']}" if extra.get("vence") else "") + ")")
    print(f"   {src_rel}:{idx + 1} ahora enlaza a la tarea en vez de la casilla.")
    return 0


def _print_tasks(tasks):
    if not tasks:
        print("Sin tareas que cumplan el filtro.")
        return
    for t in tasks:
        flag = "⚠️ " if t["vencida"] else "   "
        vence = t["vence"] or "sin fecha"
        resp = t["responsable"] or "sin responsable"
        parent = f"  ↳ {t['aplica_title'] or t['aplica']}" if t["aplica"] else ""
        print(f"{flag}[{t['status'] or '-'}] {vence}  {resp:<10} {t['title']}{parent}")
        print(f"      {t['file']}")
    vencidas = sum(1 for t in tasks if t["vencida"])
    print(f"\n{len(tasks)} tarea(s)" + (f", {vencidas} vencida(s)" if vencidas else ""))


def run(args, vault, config=None):
    sub = getattr(args, "tasks_command", None)
    if sub == "promote":
        from cli.access import is_readonly
        if is_readonly():
            print("✗ Modo solo-lectura (OKF_READONLY): 'tasks promote' escribe y está bloqueado.",
                  file=sys.stderr)
            return 1
        return promote(
            vault, config, getattr(args, "source", None),
            line=getattr(args, "line", None), match=getattr(args, "match", None),
            responsable=getattr(args, "responsable", None), vence=getattr(args, "vence", None),
            title=getattr(args, "title", None), description=getattr(args, "description", None),
            dry_run=getattr(args, "dry_run", False))

    tasks = collect_tasks(
        vault, config,
        responsable=getattr(args, "responsable", None),
        iniciativa=getattr(args, "iniciativa", None),
        status=getattr(args, "status", None),
        vencidas=getattr(args, "vencidas", False))
    if getattr(args, "as_json", False):
        sys.stdout.write(json.dumps(tasks, indent=2, ensure_ascii=False) + "\n")
    else:
        _print_tasks(tasks)
    return 0
