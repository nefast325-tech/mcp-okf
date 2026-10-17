"""Command edit — Update an existing concept in the OKF vault (merge semantics).

Unlike new (create-only), edit performs a merge: only the fields passed are
updated; everything else (type, created, cyber block, custom fields) is
preserved. The `timestamp` field represents the last meaningful edit (OKF
v0.1): it is refreshed when the CONTENT changes (body, title, description),
not when only metadata (status, tags, links, custom fields) does — the same
rule `okf stamp` enforces in the pre-commit hook.

`--status` is checked against the vault's state schema (cli/states.py) when
the type declares one: unknown states are rejected, and so are transitions
the schema does not allow, unless `--force` is passed (migrations).
"""

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cli.frontmatter import validate_cross_type


def _colombia_now():
    """Local time in Colombia (UTC-5), same criterion as new.py."""
    return (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S-05:00")


def _resolve(vault, slug):
    """Resolve a concept slug to a file path.

    Accepts 'decisions/mi-decision', 'decisions/mi-decision.md', 'mi-decision',
    or a bare basename found in the graph. Returns None if not found.
    """
    if slug.endswith(".md"):
        p = vault / slug
        if p.exists():
            return p
    p = vault / slug
    if p.exists():
        return p
    p = vault / (slug + ".md")
    if p.exists():
        return p
    # Fallback: buscar por basename en el grafo (como new.py resuelve targets)
    from cli.commands.graph import build_graph
    graph = build_graph(vault)
    needle = slug[:-3] if slug.endswith(".md") else slug
    for node in graph:
        try_target = node if node.endswith(".md") else node + ".md"
        if try_target == slug or try_target.endswith("/" + slug):
            return vault / try_target
        if Path(try_target).stem == needle:
            return vault / try_target
    return None


def _split(text):
    """Split a .md file into (fields_dict, body_str).

    Returns (None, None) if the file has no frontmatter or invalid YAML.
    """
    if not text.startswith("---"):
        return None, None
    lines = text.split("\n")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, None
    fm_lines = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])
    try:
        import yaml
        fields = yaml.safe_load(fm_lines)
    except Exception:
        return None, None
    if not isinstance(fields, dict):
        return None, None
    return fields, body


_SAFE_PLAIN = re.compile(r"^[A-Za-z0-9_\-./:]+$")
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def _quote(s):
    """JSON quoting — produces valid YAML double-quoted scalars."""
    import json
    return json.dumps(s, ensure_ascii=False)


def _fmt_scalar(value):
    if isinstance(value, str):
        if _NUMERIC.match(value) or not _SAFE_PLAIN.match(value):
            return _quote(value)
        return value
    if isinstance(value, (datetime, date)):
        # YAML 1.1 parsea timestamps ISO como datetime — re-serializar con
        # str() pierde la 'T' (2026-01-01 00:00:00). isoformat la preserva.
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _fmt_list(items):
    parts = []
    for item in items:
        if isinstance(item, str) and _SAFE_PLAIN.match(item) and not _NUMERIC.match(item):
            parts.append(item)
        else:
            parts.append(_fmt_scalar(item))
    return "[" + ", ".join(parts) + "]"


def _fmt_nested(d, indent):
    """Serialize a nested dict (e.g. cyber block) as an indented YAML block."""
    lines = []
    for k, v in d.items():
        if v is None:
            continue
        pad = " " * indent
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.extend(_fmt_nested(v, indent + 2))
        elif isinstance(v, list):
            lines.append(f"{pad}{k}: {_fmt_list(v)}")
        else:
            lines.append(f"{pad}{k}: {_fmt_scalar(v)}")
    return lines


def _serialize(fields, links):
    """Serialize fields dict to a canonical OKF frontmatter block.

    Preserves field order as parsed (incl. custom/extra fields), emits the
    links: block in canonical form, refreshes timestamp.
    """
    lines = ["---"]
    for key, value in fields.items():
        if key == "links":
            continue
        if value is None:
            continue
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(_fmt_nested(value, 2))
        elif isinstance(value, list):
            lines.append(f"{key}: {_fmt_list(value)}")
        else:
            lines.append(f"{key}: {_fmt_scalar(value)}")
    if links:
        lines.append("links:")
        for link in links:
            lines.append(f"  - target: {link['target']}")
            lines.append(f"    type: {link['type']}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_field_value(raw: str):
    """Parse a --field value: JSON for containers, bools/ints, plain string otherwise.

    Empty string → None (deletes the field).
    """
    raw = raw.strip()
    if raw == "":
        return None
    if raw.startswith("[") or raw.startswith("{"):
        import json
        try:
            return json.loads(raw)
        except Exception:
            pass
    if raw in ("true", "false"):
        return raw == "true"
    if raw in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def _set_dotted(fields, key, value, changed):
    """Set a (possibly dotted) key in the fields dict; empty value removes it."""
    parts = key.split(".")
    target = fields
    for part in parts[:-1]:
        nxt = target.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            target[part] = nxt
        target = nxt
    leaf = parts[-1]
    if value is None:
        if leaf in target:
            target.pop(leaf, None)
            changed.append(key)
    elif target.get(leaf) != value:
        target[leaf] = value
        changed.append(key)


def _validate_links(parsed_links, vault, config, source_path, concept_type):
    """Validate typed links: edge types, existing targets, duplicates, cross-type.

    Same rules as new.py. Returns (warnings: list[str]) or raises ValueError.
    """
    from cli.edge_types import VALID_EDGE_TYPES
    from cli.commands.graph import build_graph

    definitions = config.edge_type_definitions() if config else None
    valid_edge_types = set(definitions) if definitions else VALID_EDGE_TYPES

    graph = build_graph(vault)

    for link in parsed_links:
        target = link["target"]
        edge_type = link["type"]

        if edge_type not in valid_edge_types:
            raise ValueError(
                f"Invalid edge type: '{edge_type}'. "
                f"Use: {', '.join(sorted(valid_edge_types))}"
            )

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
            raise ValueError(
                f"Invalid link: '{target}' does not exist in the graph. "
                f"The target must be an existing node."
            )
        link["target"] = resolved

    seen = set()
    for link in parsed_links:
        key = (link["target"], link["type"])
        if key in seen:
            raise ValueError(
                f"Duplicate link: target='{link['target']}' type='{link['type']}'"
            )
        seen.add(key)

    return validate_cross_type(
        concept_type, source_path, parsed_links, vault, graph,
        definitions=definitions,
    )


def run(args, vault, config=None):
    """Update an existing concept in the vault (merge semantics)."""
    slug = getattr(args, "slug", None)
    if not slug:
        print("❌ Missing slug", file=sys.stderr)
        return 1

    filepath = _resolve(vault, slug)
    if filepath is None:
        print(f"❌ Not found: {slug} (checked exact path and graph basename)", file=sys.stderr)
        return 1

    text = filepath.read_text(encoding="utf-8")
    fields, body = _split(text)
    if fields is None:
        print(f"❌ Invalid or missing frontmatter: {filepath}", file=sys.stderr)
        return 1

    concept_type = fields.get("type")
    changed = []

    # ── description (mandatory by vault policy) ──
    description = getattr(args, "description", None)
    if description is not None:
        if not str(description).strip():
            print("❌ description cannot be empty (vault policy)", file=sys.stderr)
            return 1
        if fields.get("description") != description.strip():
            fields["description"] = description.strip()
            changed.append("description")

    # ── title (frontmatter only; does not rename the file) ──
    title = getattr(args, "title", None)
    if title is not None and str(title).strip():
        if fields.get("title") != title.strip():
            fields["title"] = title.strip()
            changed.append("title")

    # ── status / resource: None = no tocar, "" = limpiar ──
    for field in ("status", "resource"):
        value = getattr(args, field, None)
        if value is not None:
            value = str(value).strip()
            old = fields.get(field)
            if field == "status" and value and not getattr(args, "force", False):
                # Máquina de estados del vault (cli/states.py): estado
                # válido para el tipo y transición permitida desde el actual.
                from cli.states import check_transition, check_checkboxes
                problem = check_transition(config, concept_type, old, value)
                if not problem:
                    # Terminar una nota con casillas abiertas contradice su lista.
                    from cli.commands.stale import count_checkboxes
                    checked, open_, _ = count_checkboxes(text)
                    problem = (check_checkboxes(config, concept_type, value, checked, open_)[0] or [None])[0]
                if problem:
                    print(f"❌ {problem} (usá --force para una migración)", file=sys.stderr)
                    return 1
            if value == "":
                if old is not None:
                    fields.pop(field, None)
                    changed.append(field)
            elif old != value:
                fields[field] = value
                changed.append(field)

    # ── tags ──
    tags = getattr(args, "tags", None)
    if tags is not None:
        new_tags = [t.strip() for t in tags.split(",") if t.strip()]
        old_tags = [str(t).strip() for t in (fields.get("tags") or [])]
        if old_tags != new_tags:
            if new_tags:
                fields["tags"] = new_tags
            else:
                fields.pop("tags", None)
            changed.append("tags")

    # ── custom fields: --field key=value (dotted keys = nested) ──
    # Necesario para el type Handoff: checkpoints incrementales actualizan
    # repo_state.commit, next_session_at, state, session_id, etc.
    field_specs = getattr(args, "fields", None)
    if field_specs:
        for spec in field_specs:
            if "=" not in spec:
                print(f"❌ Invalid --field: '{spec}'. Use 'key=value'", file=sys.stderr)
                return 1
            raw_key, raw_value = spec.split("=", 1)
            key = raw_key.strip()
            if not key:
                print(f"❌ Invalid --field: '{spec}'. Empty key", file=sys.stderr)
                return 1
            _set_dotted(fields, key, _parse_field_value(raw_value), changed)

        # Esquema de campos del vault (cli/fields.py): el resultado del merge
        # tiene que cumplirlo (campo exigido que se borra, valor fuera de la
        # lista, fecha mal escrita).
        from cli.fields import check_fields, fields_strict
        field_problems = check_fields(config, concept_type, fields)
        if field_problems and fields_strict(config):
            for problem in field_problems:
                print(f"❌ {problem}", file=sys.stderr)
            return 1
        for problem in field_problems:
            print(f"⚠️  {problem}", file=sys.stderr)

    # ── typed links: replace semantics ──
    links_raw = getattr(args, "links", None)
    clear_links = getattr(args, "clear_links", False)
    if clear_links:
        if fields.get("links"):
            changed.append("links")
        fields["links"] = []
    elif links_raw is not None:
        from cli.commands.new import _parse_links
        parsed_links = []
        try:
            parsed_links = _parse_links([l.strip() for l in links_raw if l.strip()])
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
        if not parsed_links:
            print("❌ --link requires at least one 'target:type'", file=sys.stderr)
            return 1
        source_path = str(filepath.relative_to(vault))
        try:
            warnings = _validate_links(parsed_links, vault, config, source_path, concept_type)
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1
        if warnings and config is not None and getattr(config, "graph_strict_pairs", False):
            # graph.strict_pairs: una arista fuera de los pares declarados
            # no se escribe (mismo criterio que `validate`).
            for w in warnings:
                print(f"❌ {w}", file=sys.stderr)
            return 1
        for w in warnings:
            print(f"⚠️  {w}", file=sys.stderr)
        if fields.get("links") != parsed_links:
            changed.append("links")
        fields["links"] = parsed_links

    # ── body ──
    body_original = body
    body_text = getattr(args, "body", None)
    body_file = getattr(args, "body_file", None)
    if body_file:
        try:
            body_text = Path(body_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"❌ Error reading body-file: {e}", file=sys.stderr)
            return 1
    if body_text is not None:
        body = f"\n{body_text.strip()}\n"
        changed.append("body")

    if not changed:
        print(f"ℹ️  No changes requested for {filepath}")
        return 0

    # ── timestamp: refresh only when CONTENT changed (last meaningful change) ──
    # Un cambio de status, tags, aristas o campos custom no renueva la fecha:
    # `timestamp` mide cuándo cambió lo que la nota dice, no sus metadatos.
    if any(c in ("body", "title", "description") for c in changed):
        fields["timestamp"] = _colombia_now()
        changed.append("timestamp")

    links_out = fields.get("links") or []
    frontmatter = _serialize(fields, links_out)
    # Normalize: keep a single blank line between the closing --- and the body
    body_clean = (body if body is not None else body_original) or ""
    body_clean = body_clean.strip("\n")
    content = frontmatter.rstrip("\n") + "\n" + (f"\n{body_clean}\n" if body_clean else "\n")

    if getattr(args, "dry_run", False):
        print(f"🔍 DRY-RUN — {filepath}")
        print(f"   Changes: {', '.join(changed)}")
        print("─" * 60)
        print(content)
        print("─" * 60)
        return 0

    filepath.write_text(content, encoding="utf-8")
    print(f"✅ Updated: {filepath}")
    print(f"   Changes: {', '.join(changed)}")
    return 0
