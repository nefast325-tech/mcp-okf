"""Frontmatter YAML parsing.

Responsibilities:
- Extract and parse the YAML frontmatter block from a .md file
- Validate required fields (type, description)
- Extract normalized tags
- Increment reads counter
- Normalize string↔list tags

Recognized fields (OKF v0.1):
  type (required), description (required), title, tags, timestamp,
  resource, status, reads, leaf, cyber, okf_version
"""

import re
from pathlib import Path


def parse_frontmatter(text):
    """Extracts and parses YAML frontmatter. Tries PyYAML, fallback to regex.

    Args:
        text: Full content of the .md file.

    Returns:
        tuple[dict|None, str|None]: (fields_dict, raw_fm_text)
        - fields_dict: dict with parsed fields, or None if no frontmatter.
        - raw_fm_text: raw frontmatter text (without delimiters), or None.
    """
    if not text.startswith("---"):
        return None, None

    end = text.find("\n---\n", 3)
    if end == -1:
        end = text.find("\n---", 3)
    if end == -1:
        return None, None

    fm_text = text[3:end]

    # Intentar PyYAML primero (más preciso). Con libyaml el cargador en C
    # es ~10 veces más rápido que el de Python puro y da el mismo resultado;
    # con 10.131 notas el de Python era el 85 % del tiempo de un `search`.
    try:
        import yaml
        fm = yaml.load(fm_text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        if isinstance(fm, dict):
            # Filtrar claves espurias
            fm = {
                k: v for k, v in fm.items()
                if k and not str(k).startswith("#") and not str(k).startswith("- ")
            }
            return fm, fm_text
    except Exception:
        pass

    # Fallback: parser manual con regex
    fm = {}
    for line in fm_text.split("\n"):
        m = re.match(r'^(\w[\w-]*):\s*(.*)', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            fm[key] = val

    return fm if fm else None, fm_text


def quote_yaml_scalar(value: str) -> str:
    """Serializa un string como escalar YAML double-quoted válido.

    `json.dumps` produce escaping que es subconjunto válido del YAML
    double-quoted: cubre comillas dobles, backslashes y saltos de línea. Sin
    esto, un `title`/`description` con comillas internas genera frontmatter
    inválido: el archivo queda ilegible para `edit` ("Invalid or missing
    frontmatter") y para el health check.

    Mismo criterio que `cli/commands/edit.py::_quote` (la duplicación de esta
    primitiva en new/index/edit es deuda pendiente).
    """
    import json
    return json.dumps(value, ensure_ascii=False)


def validate_frontmatter(fields):
    """Validates that the frontmatter has the required fields.

    Args:
        fields: Parsed frontmatter dict.

    Returns:
        list[str]: List of errors (empty = all ok).
    """
    errors = []
    if not fields:
        return ["No frontmatter or invalid YAML"]

    if not fields.get("type"):
        errors.append("missing 'type' (OKF required)")

    desc = fields.get("description")
    if not desc or not str(desc).strip():
        errors.append("missing 'description' (vault policy)")
    elif isinstance(desc, str) and len(desc) > 2000:
        errors.append(f"description exceeds 2000 chars ({len(desc)}) — likely corruption")

    return errors


def extract_tags(md_path):
    """Extracts tags from a file's frontmatter. No PyYAML dependency.

    Args:
        md_path: Path to .md file.

    Returns:
        list[str]: Normalized tag list.
    """
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return []

    if not text.startswith("---"):
        return []

    end = text.find("\n---\n", 3)
    if end == -1:
        return []
    fm_text = text[3:end]

    tags = []
    # Formato inline: tags: [tag1, tag2]
    m = re.search(r'^tags:\s*\[([^\]]+)\]', fm_text, re.MULTILINE)
    if m:
        tags = [t.strip().strip('"').strip("'") for t in m.group(1).split(",")]
    else:
        # Formato multilínea: tags:\n  - tag1\n  - tag2
        in_tags = False
        for line in fm_text.split("\n"):
            if re.match(r'^tags:', line):
                in_tags = True
                continue
            if in_tags:
                m2 = re.match(r'\s*-\s+(.+)', line)
                if m2:
                    tags.append(m2.group(1).strip().strip('"').strip("'"))
                elif line.strip() and not line.startswith(" "):
                    break
    return [t for t in tags if t]


def normalize_tags(tags_value):
    """Converts any tag representation to a list of strings.

    Args:
        tags_value: Can be str ("tag1, tag2"), list, or None.

    Returns:
        list[str]
    """
    if tags_value is None:
        return []
    if isinstance(tags_value, list):
        return [str(t).strip() for t in tags_value if t]
    if isinstance(tags_value, str):
        # Puede ser formato "[a, b]" o "a, b"
        s = tags_value.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        return [t.strip() for t in s.split(",") if t.strip()]
    return []


def increment_reads(filepath, vault=None):
    """Increments the reads counter for a concept.

    Los counters viven en el store de telemetría local
    (<vault>/.okf/state/reads.jsonl, no versionado) — decisión 2026-08-27:
    escribirlos en el frontmatter versionaba ruido en cada lectura y
    generaba conflictos constantes en vaults multi-actor con sync
    automático. Si el archivo todavía tiene el campo viejo 'reads: N',
    se migra solo (baseline + limpieza).

    Args:
        filepath: Path to .md file.
        vault: Vault root. If None, derived by walking up looking for
            .okf.config.yaml.

    Returns:
        int: New total counter value (0 if failed).
    """
    if vault is None:
        from cli.reads_store import find_vault
        vault = find_vault(filepath)
    if vault is None:
        return 0
    from cli.reads_store import increment_reads as _inc
    return _inc(filepath, vault)


def extract_typed_links(md_path, fm=None):
    """Extracts typed edges from the 'links:' field in frontmatter.

    Args:
        md_path: Path to .md file.
        fm: Optional pre-parsed frontmatter dict. If provided, skips the
            file read + YAML parse (build_graph ya lo tiene cacheado).

    Returns:
        list[dict]: List of {"target": str, "type": str}.
        Empty list if there is no 'links:' field or frontmatter is malformed.
    """
    if fm is None:
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:
            return []

        fm, _ = parse_frontmatter(text)
        if fm is None:
            return []

    links = fm.get("links")
    if not isinstance(links, list):
        return []

    result = []
    for item in links:
        if isinstance(item, dict) and "target" in item and "type" in item:
            result.append({
                "target": str(item["target"]),
                "type": str(item["type"]),
            })
    return result


def validate_cross_type(source_type, source_path, links, vault, graph,
                        definitions=None):
    """Non-blocking cross-type validation for typed edges.

    Verifies that (source_type, target_type, edge_type) pairs are
    semantically valid per EDGE_TYPE_DEFINITIONS. Also detects
    mutual exclusion (extiende + refina same target) and corrige without
    deprecated target.

    Args:
        source_type: Type of the source node (e.g.: 'Insight').
        source_path: Relative path of the source node (e.g.: 'insights/foo.md').
        links: List of dicts {"target": relpath.md, "type": edge_type}.
        vault: Path to vault root.
        graph: Graph built with build_graph().
        definitions: Optional config-provided edge type definitions. If None,
                     uses the embedded defaults (cli.edge_types).

    Returns:
        list[str]: List of warnings (empty = all OK).
    """
    from cli.edge_types import validate_cross_type_pair

    warnings = []

    for link in links:
        target_path = link["target"]
        edge_type = link["type"]
        target_type = "?"

        # Resolver type del target
        target_file = vault / target_path
        if target_file.exists():
            try:
                text = target_file.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(text)
                if fm and fm.get("type"):
                    target_type = str(fm["type"])
            except Exception:
                pass

        # Validar par cross-type
        pair_warnings = validate_cross_type_pair(
            source_type, target_type, edge_type, definitions=definitions
        )
        for w in pair_warnings:
            warnings.append(
                f"links: {source_path} → {target_path}: {w}"
            )

        # Exclusión mutua: mismo target con extiende Y refina
        other_types = {
            l["type"] for l in links
            if l["target"] == target_path and l["type"] != edge_type
        }
        if edge_type == "extiende" and "refina" in other_types:
            warnings.append(
                f"links: {source_path} → {target_path}: mutual exclusion — "
                f"'extiende' and 'refina' on the same pair"
            )
        if edge_type == "refina" and "extiende" in other_types:
            warnings.append(
                f"links: {source_path} → {target_path}: mutual exclusion — "
                f"'extiende' and 'refina' on the same pair"
            )

        # corrige sin target deprecado ni corrected_by
        if edge_type == "corrige" and target_file.exists():
            try:
                text = target_file.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(text)
                if fm:
                    status = str(fm.get("status", ""))
                    cyber = fm.get("cyber")
                    has_corrected_by = (
                        isinstance(cyber, dict)
                        and bool(cyber.get("corrected_by"))
                    )
                    if "deprec" not in status.lower() and not has_corrected_by:
                        warnings.append(
                            f"links: {source_path} corrects '{target_path}' "
                            f"but the target is not marked as deprecated "
                            f"nor has cyber.corrected_by."
                        )
            except Exception:
                pass

    return warnings
