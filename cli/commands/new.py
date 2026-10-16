"""Command new — Create a new concept in the OKF vault with consistent formatting."""

import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cli.frontmatter import quote_yaml_scalar

# Types MECE permitidos — defaults (pisan por Config si existe .okf.config.yaml)
VALID_TYPES = {
    "Sistema", "Agente", "Decision", "Plan", "Project", "Insight",
    "MarcoTeorico", "LeccionAprendida", "Tool", "Spec", "Skill", "Workflow", "Criterio",
    "Sesion", "Research", "Mapa", "Harness", "Handoff",
}

# Types que califican para bloque cyber: — defaults
CYBER_TYPES = {"Sistema", "Agente", "Decision", "Plan", "Project", "Insight", "Harness"}

# Mapeo type → directorio — defaults
TYPE_DIR = {
    "Sistema": "sistema",
    "Agente": "agentes",
    "Decision": "decisions",
    "Plan": "plans",
    "Project": "projects",
    "Insight": "insights",
    "MarcoTeorico": "frameworks",
    "LeccionAprendida": "historias",
    "Tool": "specs",
    "Spec": "specs",
    "Skill": "skills",
    "Workflow": "workflows",
    "Criterio": "criterios",
    "Sesion": "sesiones",
    "Research": "research",
    "Mapa": "mapas",
    "Harness": "harnesses",
    "Handoff": "handoffs",
}

BODY_TEMPLATES = {
    "Decision": """## Contexto

(¿Qué disparó esta decisión? ¿Qué problema resuelve?)

## Decisión

(¿Qué se decidió? ¿Qué alternativas se descartaron?)

## Impacto

(¿Qué cambia a partir de ahora?)
""",
    "Plan": """## Objetivo

(¿Qué se quiere lograr?)

## Fases

1.
2.
3.

## Métricas

(¿Cómo se mide el éxito?)
""",
    "Project": """## Visión

(¿Qué es este proyecto? ¿Por qué existe?)

## Estado

**Fase actual:** (planificación / desarrollo / producción / abandonado)

## Componentes

-
-
""",
    "Harness": """## Tool objetivo

(¿Qué tool envuelve este harness? bash, playwright, git, mcp-okf...)

## Script

(¿Ruta al script que implementa el harness? Ej: ~/.hermes/scripts/terminal_harness.py)

## Uso

```bash
python3 <script> "<comando>" [timeout]
```

## Protocolo de respuesta

| Campo | Tipo | Valores |
|-------|------|---------|
| status | enum | success \\| retry \\| needs_reasoning \\| fatal |
| category | string | ok, timeout, permission_denied, ... |
| exit_code | int | Código de salida real |
| suggestion | string | Acción recomendada para el LLM |

## Categorías de error

### RETRY

| Categoría | Patrón | Sugerencia |
|-----------|--------|------------|

### NEEDS_REASONING

| Categoría | Patrón | Sugerencia |
|-----------|--------|------------|

### FATAL

| Categoría | Patrón | Sugerencia |
|-----------|--------|------------|

## Relacionado

- [[agentes/]] — agentes que consumen este harness
- [[specs/harnesses-como-nodos-del-grafo]] — spec del type Harness
""",
    "Insight": """## Observación

(¿Qué patrón o conexión se detectó?)

## Implicación

(¿Qué significa esto? ¿Qué acción sugiere?)

## Verificación

(¿Cómo se confirma o descarta este insight?)
""",
    "Sistema": """## Qué hace

(¿Qué configuración o comportamiento controla en el sistema agéntico?)

## Dónde está

(¿Archivo de config, variable de entorno, skill?)

## Por qué

(¿Qué problema resuelve? ¿Qué pasaría si se quitara?)

## Relaciones

- 
""",
    "Agente": """## Rol

(¿Qué rol cumple este agente en el ecosistema?)

## Capacidades

- 
- 

## Configuración

(¿Modelo, tools, skills asignados?)

## AGENT.md / SOUL.md

(¿Dónde está su archivo de definición?)
""",
    "Skill": """## Identidad

| Campo | Valor |
|---|---|
| **Nombre** | (nombre de la skill) |
| **Repo** | (URL) |
| **Ruta Hermes** | `~/.hermes/skills/<categoria>/<nombre>/` |

## Qué hace

(¿Qué problema resuelve esta skill? ¿Cuándo debe cargarla el agente?)

## Dependencias

- 
- 

## Archivos

(¿SKILL.md, references/, scripts/?)
""",
    "Workflow": """## Qué problema resuelve

(¿Qué problema resuelve este workflow? ¿Cuándo debería un agente sugerirlo?)

## Arquitectura

(Componentes abstractos: sensor → criterio → clasificación → acción)

## Procedimiento

1. 
2. 
3. 

## Criterios de calidad

(¿Cómo saber si el workflow está funcionando?)

## Instanciación en OKF

(Referencia a la instancia concreta en nuestro ecosistema)
""",
    "Handoff": """## Contexto

(Qué se estaba haciendo, por qué — 2-3 frases)

## Completado

- [ ] (ítems con [x] y commit de referencia)

## Pendientes

- [ ] (ítems priorizados con archivos, líneas, causas probables)

## Estado verificable

(commit hash, test_pass_rate, archivos modificados — datos comprobables, no narrativa)

## Señales del ecosistema

(decisiones nuevas, health checks, watch dogs, insights relevantes al cierre)

## Para retomar

(instrucción de arranque para la próxima sesión: por dónde empezar, qué leer primero)
""",
}


def _slugify(text):
    """Converts title to slug for filename."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


def _validate_custom_filename(name):
    """Validates a user-provided --filename.

    Returns an error message (str), or None when the name is usable. The name
    must be a bare file name inside the directory that the type dictates: no
    path separators (the directory comes from --type, or from --entity /
    --filename for Skill), no traversal, no hidden files.
    """
    if not name:
        return "empty"
    if name in (".", ".."):
        return "the name cannot be '.' or '..'"
    if "/" in name or "\\" in name:
        return ("it must be a bare file name, without path separators "
                "(the directory is chosen by the type)")
    if name.startswith("."):
        return "hidden files are not allowed"
    if any(ord(c) < 32 for c in name):
        return "control characters are not allowed"
    return None


# Claves que --field no puede fijar: tienen opción dedicada y duplicarlas
# generaría frontmatter YAML inválido (dos veces la misma clave).
RESERVED_FIELD_KEYS = {
    "type", "title", "description", "tags", "status", "resource",
    "timestamp", "created", "links", "cyber",
}


def _parse_extra_fields(field_specs):
    """Parses repeatable --field 'key=value' specs into frontmatter lines.

    Reuses the value parsing/serialization of `edit --field` (JSON for
    containers, bool/int/float, plain string otherwise) so both commands speak
    the same dialect. Only top-level keys: nested blocks belong to `edit`.

    Returns:
        list[str] | None: formatted frontmatter lines, or None on error
        (the message is already printed to stderr).
    """
    if not field_specs:
        return []
    from cli.commands.edit import _fmt_list, _fmt_nested, _fmt_scalar, _parse_field_value

    lines = []
    seen = set()
    for spec in field_specs:
        if "=" not in spec:
            print(f"❌ Invalid --field: '{spec}'. Use 'key=value'", file=sys.stderr)
            return None
        raw_key, raw_value = spec.split("=", 1)
        key = raw_key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            print(
                f"❌ Invalid --field key: '{key}'. Use letters, digits, '_' or '-' "
                f"(starting with a letter or '_'); nested keys belong to "
                f"`edit --field parent.child=...`",
                file=sys.stderr,
            )
            return None
        if key in RESERVED_FIELD_KEYS:
            print(
                f"❌ --field '{key}' is reserved: use the dedicated option "
                f"(--type/--title/--description/--tags/--status/--resource)",
                file=sys.stderr,
            )
            return None
        if key in seen:
            print(f"❌ Duplicate --field: '{key}'", file=sys.stderr)
            return None
        seen.add(key)

        value = _parse_field_value(raw_value)
        if value is None:
            print(
                f"❌ --field '{key}': empty value. `new` is create-only; "
                f"use `edit --field {key}=` to clear a field",
                file=sys.stderr,
            )
            return None
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(_fmt_nested(value, 2))
        elif isinstance(value, list):
            lines.append(f"{key}: {_fmt_list(value)}")
        else:
            lines.append(f"{key}: {_fmt_scalar(value)}")
    return lines


def _parse_links(links):
    """Parse --link 'target:type' flags into a list of dicts.

    Args:
        links: list[str] | None — each element is "target:type".

    Returns:
        list[dict]: [{"target": "path.md", "type": "edge_type"}, ...]

    Raises:
        ValueError: If the format is invalid.
    """
    if not links:
        return []
    result = []
    for raw in links:
        if ":" not in raw:
            raise ValueError(
                f"Invalid format: '{raw}'. Use 'target:type' "
                f"(e.g.: frameworks/tp3-cibernetico:extiende)"
            )
        target, edge_type = raw.rsplit(":", 1)
        target = target.strip()
        edge_type = edge_type.strip()
        if not target:
            raise ValueError(f"Empty target in: '{raw}'")
        if not edge_type:
            raise ValueError(f"Empty type in: '{raw}'")
        result.append({"target": target, "type": edge_type})
    return result


def _build_frontmatter(concept_type, title, description, status, resource, tags,
                       cyber, links=None, config=None, omit_timestamps=False,
                       extra_lines=None):
    """Generate the frontmatter YAML block.

    Args:
        links: list[dict] | None — list of {"target": str, "type": str}.
        omit_timestamps: If True, skip `timestamp:`/`created:` (formats that
            don't carry them — e.g. the session summaries in sesiones/, whose
            format is part of the series contract; health check 9 already
            exempts sesiones/ from the missing-timestamp warning).
        extra_lines: list[str] | None — pre-formatted frontmatter lines from
            `--field key=value`, inserted before the timestamps.
    """
    # Hora local de Colombia (UTC-5). datetime.now(timezone.utc) devuelve la hora
    # UTC; sin restar el offset el sello quedaba 5 horas en el futuro, porque se
    # etiquetaba como -05:00 una hora que en realidad era UTC. Mismo criterio que
    # ya aplican review.py y health.py.
    now = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S-05:00")

    lines = ["---", f"type: {concept_type}"]
    # title es opcional: hay formatos del vault (resúmenes de sesión) que no lo
    # llevan. Sin title no se escribe la clave, en vez de escribir `title: ""`.
    if title and str(title).strip():
        lines.append(f"title: {quote_yaml_scalar(title.strip())}")
    lines.append(f"description: {quote_yaml_scalar(description.strip())}")

    if status:
        lines.append(f"status: {status}")
    elif concept_type == "Handoff":
        # Handoff: ciclo de vida propio (pending | completed | expired) — spec de handoff
        lines.append("status: pending")

    if resource:
        lines.append(f"resource: {quote_yaml_scalar(resource)}")

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        lines.append(f"tags: [{', '.join(tag_list)}]")

    if extra_lines:
        lines.extend(extra_lines)

    if not omit_timestamps:
        lines.append(f"timestamp: {now}")
        lines.append(f"created: {now}")

    # --- links: field (NUEVO) ---
    if links:
        lines.append("links:")
        for link in links:
            lines.append(f"  - target: {link['target']}")
            lines.append(f"    type: {link['type']}")

    # Resolver cyber_types desde config o fallback
    cyber_types = set(config.types_cyber) if config else CYBER_TYPES

    if cyber and concept_type in cyber_types:
        review_days = config.cyber_review_days if config else 14
        review_date = (date.today() + timedelta(days=review_days)).isoformat()
        lines.append("cyber:")
        lines.append("  sensor: (completar)")
        lines.append('  perception: ""')
        lines.append('  target_metric: {name: "", target: 0}')
        lines.append("  actuator: []")
        lines.append("  corrects: []")
        lines.append("  outcome: pending")
        lines.append(f"  review_on: {review_date}")
    elif cyber:
        print(f"⚠️  --cyber ignored: type '{concept_type}' does not qualify "
              f"(only {', '.join(sorted(cyber_types))})", file=sys.stderr)

    # Campos específicos de Handoff — spec de handoff (repo_state, graph_state)
    # + spec de ritmo multisession (state, next_session_at, interval, last_activity_at)
    if concept_type == "Handoff":
        lines.append('session_id: ""')
        lines.append("priority: media")
        lines.append('project: ""')
        lines.append("state: activo")
        lines.append(f"last_activity_at: {now}")
        lines.append('next_session_at: ""')
        lines.append("interval: daily")
        lines.append(f"checkpoint_at: {now}")
        lines.append("repo_state:")
        lines.append('  commit: ""')
        lines.append('  branch: ""')
        lines.append('  test_pass_rate: ""')
        lines.append("  files_modified: []")
        lines.append("graph_state:")
        lines.append('  vault_commit: ""')
        lines.append("  vecindarios: []")

    lines.append("---")
    return "\n".join(lines) + "\n"


def run(args, vault, config=None):
    """Create a new concept in the vault."""
    concept_type = getattr(args, "concept_type", None)
    title = getattr(args, "title", None)
    description = getattr(args, "description", None)
    tags = getattr(args, "tags", None)
    status = getattr(args, "status", None)
    resource = getattr(args, "resource", None)
    cyber = getattr(args, "cyber", False)
    dry_run = getattr(args, "dry_run", False)
    body_text = getattr(args, "body", None)
    body_file = getattr(args, "body_file", None)
    links_raw = getattr(args, "links", None)
    entity = getattr(args, "entity", None)
    filename_arg = (getattr(args, "filename", None) or "").strip()
    omit_timestamps = bool(getattr(args, "omit_timestamps", False))

    # Resolver desde config o fallback a defaults
    valid_types = set(config.types_valid) if config else VALID_TYPES
    type_dir = dict(config.types_directory) if config else TYPE_DIR
    by_entity = set(config.types_by_entity) if config else set()

    if concept_type not in valid_types:
        print(f"❌ Invalid type: '{concept_type}'", file=sys.stderr)
        print(f"   Valid: {', '.join(sorted(valid_types))}", file=sys.stderr)
        return 1

    # Máquina de estados del vault (cli/states.py): sin --status, el tipo
    # arranca en su estado inicial; con --status, tiene que ser uno del
    # esquema (y un tipo de registro no admite ninguno).
    from cli.states import check_status, initial_status, schema_for
    if schema_for(config, concept_type) is not None:
        if not status:
            status = initial_status(config, concept_type)
        else:
            for problem in check_status(config, concept_type, status):
                print(f"❌ {problem}", file=sys.stderr)
                return 1

    extra_lines = _parse_extra_fields(getattr(args, "fields", None))
    if extra_lines is None:
        return 1

    # Nombre del archivo: --filename lo fija exacto (sin slugificar, para no
    # romper convenciones con guiones bajos); si no, se deriva del slug del
    # título. Sin ninguno de los dos no hay nombre posible.
    title_clean = str(title).strip() if title else ""
    slug = _slugify(title_clean)
    stem = ""
    if filename_arg:
        err = _validate_custom_filename(filename_arg)
        if err:
            print(f"❌ Invalid --filename '{filename_arg}': {err}", file=sys.stderr)
            return 1
        stem = filename_arg[:-3] if filename_arg.lower().endswith(".md") else filename_arg
        if not stem:
            print(f"❌ Invalid --filename '{filename_arg}': missing name before '.md'",
                  file=sys.stderr)
            return 1
    elif slug:
        stem = slug
    else:
        print("❌ --title is required unless --filename is given.", file=sys.stderr)
        print("   (the file name is derived from the title slug; --filename pins "
              "the exact name, e.g. --filename sesion-20260908_172301_5ef849fd.md)",
              file=sys.stderr)
        return 1

    # Determinar directorio y nombre de archivo
    subdir = type_dir[concept_type]
    # Skill usa subdirectorio propio con SKILL.md (convención post-refactor);
    # ahí --filename nombra el directorio de la skill, no el archivo.
    if concept_type == "Skill":
        filename = "SKILL.md"
        filepath = vault / subdir / stem / filename
    elif concept_type in by_entity:
        # by_entity: un subdirectorio por instancia (decisión 2026-08-10).
        # Ej: type=Cliente entity=Lopcort → clientes/Lopcort/lopcort-com.md
        if not entity or not str(entity).strip():
            print(
                f"❌ Type '{concept_type}' groups by entity: "
                f"the 'entity' parameter is required "
                f"(e.g.: entity='Lopcort' → {subdir}/Lopcort/<slug>.md).",
                file=sys.stderr,
            )
            return 1
        entity_slug = _slugify(str(entity).strip())
        if not entity_slug:
            print(f"❌ Invalid entity: '{entity}'", file=sys.stderr)
            return 1
        filename = f"{stem}.md"
        filepath = vault / subdir / entity_slug / filename
    else:
        filename = f"{stem}.md"
        filepath = vault / subdir / filename

    if filepath.exists():
        if not getattr(args, "force", False):
            print(f"❌ Already exists: {filepath}", file=sys.stderr)
            print("   Use --force to overwrite, or `edit` to update it.", file=sys.stderr)
            return 1

    # --- Parsear y validar links (NUEVO) ---
    parsed_links = []
    if links_raw:
        try:
            parsed_links = _parse_links(links_raw)
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1

        # Validación bloqueante: targets, edge_types, duplicados
        from cli.edge_types import VALID_EDGE_TYPES
        from cli.commands.graph import build_graph
        from cli.frontmatter import validate_cross_type

        definitions = config.edge_type_definitions() if config else None
        valid_edge_types = set(definitions) if definitions else VALID_EDGE_TYPES

        graph = build_graph(vault)

        for link in parsed_links:
            target = link["target"]
            edge_type = link["type"]

            if edge_type not in valid_edge_types:
                print(
                    f"❌ Invalid edge type: '{edge_type}'. "
                    f"Use: {', '.join(sorted(valid_edge_types))}",
                    file=sys.stderr,
                )
                return 1

            target_file = vault / target
            resolved = None
            if target_file.exists():
                resolved = str(target_file.relative_to(vault))
            else:
                try_target = target if target.endswith(".md") else target + ".md"
                for node in graph:
                    if try_target in node or try_target == node:
                        resolved = node
                        break

            if resolved is None or resolved not in graph:
                print(
                    f"❌ Invalid link: '{target}' does not exist in the graph. "
                    f"The target must be an existing node.",
                    file=sys.stderr,
                )
                return 1

            link["target"] = resolved

        seen = set()
        for link in parsed_links:
            key = (link["target"], link["type"])
            if key in seen:
                print(
                    f"❌ Duplicate link: target='{link['target']}' "
                    f"type='{link['type']}'",
                    file=sys.stderr,
                )
                return 1
            seen.add(key)

        source_path = f"{subdir}/{filename}"
        if concept_type in by_entity and entity:
            source_path = f"{subdir}/{_slugify(str(entity).strip())}/{filename}"
        cross_warnings = validate_cross_type(
            concept_type, source_path, parsed_links, vault, graph,
            definitions=definitions,
        )
        if cross_warnings and config is not None and getattr(config, "graph_strict_pairs", False):
            # graph.strict_pairs: una arista fuera de los pares declarados
            # no se escribe (mismo criterio que `validate`).
            for w in cross_warnings:
                print(f"❌ {w}", file=sys.stderr)
            return 1
        for w in cross_warnings:
            print(f"⚠️  {w}", file=sys.stderr)

    frontmatter = _build_frontmatter(concept_type, title, description,
                                     status, resource, tags, cyber,
                                     links=parsed_links, config=config,
                                     omit_timestamps=omit_timestamps,
                                     extra_lines=extra_lines)
    if body_file:
        try:
            body_text = Path(body_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"❌ Error reading body-file: {e}", file=sys.stderr)
            return 1
    if body_text:
        body = f"\n{body_text.strip()}\n"
    else:
        template = config.get_template(concept_type) if config else BODY_TEMPLATES.get(concept_type, "## Contexto\n\n(Contenido)\n")
        body = f"\n{template}"
    content = frontmatter + body

    if dry_run:
        print(f"🔍 DRY-RUN — {filepath}")
        print("─" * 60)
        print(content)
        print("─" * 60)
        return 0

    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")

    print(f"✅ Created: {filepath}")
    print(f"   Type: {concept_type} | Status: {status or '(no status)'}")
    if cyber and concept_type in (set(config.types_cyber) if config else CYBER_TYPES):
        print(f"   🧠 Cyber block: included (outcome: pending, review_on: +{config.cyber_review_days if config else 14}d)")
    print(f"\n   Next step: edit body and commit.")
    return 0
