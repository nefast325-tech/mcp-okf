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
            responsable: {required: true, values: [rafael, jaime, claude, skynet]}
            vence: {format: date}

Cada campo admite `required` (bool), `values` (lista cerrada) y `format`
(`date` = ISO YYYY-MM-DD; sin format, texto libre). Sin esquema para un tipo,
nada cambia: los campos siguen sin validarse.
"""

from datetime import date, datetime


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


def check_fields(config, concept_type, fm):
    """Problemas de los campos de `fm` respecto al esquema del tipo.

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
                if values:
                    hint = f" (uno de: {', '.join(str(v) for v in values)})"
                problems.append(f"'{concept_type}' exige el campo '{name}'{hint}")
            continue
        values = rules.get("values")
        if values and str(value).strip() not in [str(v) for v in values]:
            problems.append(
                f"'{name}' = '{value}' no está en los valores de '{concept_type}' "
                f"({', '.join(str(v) for v in values)})")
        if rules.get("format") == "date" and not _is_date(value):
            problems.append(f"'{name}' = '{value}' no es una fecha ISO (YYYY-MM-DD)")
    return problems
