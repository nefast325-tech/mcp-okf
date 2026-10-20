"""Campos propios por tipo, declarados en la config del vault.

El frontmatter OKF tiene un contrato fijo (type, description, wikilinks) y, por
encima, campos libres que nadie validaba: un `responsable` mal escrito o un
`vence` que no es fecha pasaban el hook. Con la fase 3 del rediseño (las
tareas como notas) esos campos pasan a tener significado operativo —`okf tasks`
lista por responsable y por vencimiento— así que el vault puede declararlos:

    types:
      fields:
        strict: false            # false = aviso, true = error (validate)
        schemas:
          Task:
            responsable: {required: true, resolves: Persona, values: [skynet]}
            vence: {format: date}
          Decision:
            decidida_por: {required: true, resolves: Persona}
            decidida_el: {required: true, format: date}

Cada campo admite `required` (bool), `values` (lista cerrada), `format`
(`date` = ISO YYYY-MM-DD; sin format, texto libre) y `resolves` (fase 4: el
valor tiene que nombrar una nota existente de ese tipo —por el nombre del
archivo o de su carpeta de entidad— o estar en `values`; texto libre deriva
igual que derivó `activa`/`activo`). Sin esquema para un tipo, nada cambia:
los campos siguen sin validarse.
"""

from datetime import date, datetime
from pathlib import Path

# Nombres que resuelven a una nota, por (vault, tipo). Se construye una vez por
# proceso: validate --all lo consultaría una vez por nota.
_RESOLVE_CACHE: dict = {}


def fields_schema_for(config, concept_type):
    """Esquema de campos del tipo ({campo: reglas}), o None si no declara uno."""
    if config is None or not concept_type:
        return None
    schemas = getattr(config, "fields_schemas", None) or {}
    schema = schemas.get(concept_type)
    if not isinstance(schema, dict):
        return None
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in schema.items()}


def fields_strict(config):
    """True si el vault pide que incumplir el esquema de campos sea error."""
    return bool(getattr(config, "fields_strict", False)) if config else False


def _is_date(value):
    if isinstance(value, (date, datetime)):
        return True
    try:
        date.fromisoformat(str(value).strip())
        return True
    except ValueError:
        return False


def resolvable_names(vault, concept_type, config=None):
    """Nombres con los que una nota del tipo se puede nombrar desde un campo.

    Una `Persona` en `personas/rafael/rafael-hernandez.md` resuelve como
    `rafael` (carpeta de entidad) y como `rafael-hernandez` (archivo). La
    comparación es en minúsculas. Vacío si no hay vault o no hay notas.
    """
    if vault is None or not concept_type:
        return set()
    key = (str(Path(vault).resolve()), str(concept_type))
    cached = _RESOLVE_CACHE.get(key)
    if cached is not None:
        return cached
    from cli.frontmatter import parse_frontmatter
    from cli.vault import find_md_files
    names = set()
    vault = Path(vault)
    for f in find_md_files(vault, config=config):
        try:
            fm, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not fm or str(fm.get("type", "")) != str(concept_type):
            continue
        names.add(f.stem.lower())
        rel_parts = f.relative_to(vault).parts
        if len(rel_parts) >= 3:
            # by_entity: <dir>/<entidad>/<slug>.md → la entidad también nombra
            names.add(rel_parts[-2].lower())
    _RESOLVE_CACHE[key] = names
    return names


def reset_resolve_cache():
    """Olvida los nombres cacheados (tests, o tras crear una nota del tipo)."""
    _RESOLVE_CACHE.clear()


def check_fields(config, concept_type, fm, vault=None):
    """Problemas de los campos de `fm` respecto al esquema del tipo.

    `vault` hace falta solo para `resolves`: sin él, un campo que resuelve a
    un tipo se contrasta únicamente con `values`.

    Returns:
        list[str]: vacía si no hay esquema o los campos cumplen.
    """
    schema = fields_schema_for(config, concept_type)
    if not schema:
        return []
    fm = fm or {}
    problems = []
    for name, rules in schema.items():
        value = fm.get(name)
        empty = value is None or (isinstance(value, str) and not value.strip())
        if empty:
            if rules.get("required"):
                hint = ""
                values = rules.get("values")
                resolves = rules.get("resolves")
                if resolves:
                    hint = f" (una nota {resolves}"
                    if values:
                        hint += f" o uno de: {', '.join(str(v) for v in values)}"
                    hint += ")"
                elif values:
                    hint = f" (uno de: {', '.join(str(v) for v in values)})"
                problems.append(f"'{concept_type}' exige el campo '{name}'{hint}")
            continue
        values = [str(v) for v in (rules.get("values") or [])]
        resolves = rules.get("resolves")
        text = str(value).strip()
        if resolves:
            known = resolvable_names(vault, resolves, config=config)
            if text.lower() not in known and text not in values:
                hint = (f"ninguna nota {resolves} se llama así"
                        + (f"; valores admitidos además: {', '.join(values)}" if values else ""))
                problems.append(f"'{name}' = '{value}' no resuelve a una {resolves} ({hint})")
        elif values and text not in values:
            problems.append(
                f"'{name}' = '{value}' no está en los valores de '{concept_type}' "
                f"({', '.join(values)})")
        if rules.get("format") == "date" and not _is_date(value):
            problems.append(f"'{name}' = '{value}' no es una fecha ISO (YYYY-MM-DD)")
    return problems
