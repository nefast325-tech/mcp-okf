"""Fechas de verificación por nota: `verificado_el` y `revisar_el` (fase 4).

Una nota de conocimiento (una investigación, un hallazgo, una ficha con
precios) es cierta hasta que deja de serlo, y hasta ahora la única pista de
cuándo se comprobó por última vez era la fecha metida en el título. Dos
campos la sacan de ahí:

- `verificado_el`: la fecha en que el contenido se comprobó contra su fuente.
- `revisar_el`: cuándo volver a comprobarlo. Opcional: si falta, se deriva de
  `verificado_el` más el plazo del tipo (un hecho vive en un solo lugar).

El plazo lo declara el vault, por tipo o por prefijo de ruta (una ficha
pública de precios caduca antes que una lección):

    types:
      review:
        strict: false              # false = aviso, true = error (validate)
        days:
          Research: 90
          Insight: 90
          Lesson: 180
        paths:
          publico/: 30             # el prefijo manda sobre el tipo

Qué hace cada comando con esto:
- `validate`: las dos fechas tienen que ser ISO y `revisar_el` no puede ser
  anterior a `verificado_el`.
- `new`: una nota de un tipo con plazo nace con `verificado_el` = hoy.
- `stale`: señal 8, «verificación vencida», cuando la fecha de revisión pasó.
- `review`: lista lo vencido junto con los `cyber.review_on`.

Sin `types.review` en la config, nada cambia.
"""

from datetime import date, datetime, timedelta

VERIFIED_FIELD = "verificado_el"
DUE_FIELD = "revisar_el"


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


def review_strict(config):
    return bool(getattr(config, "review_strict", False)) if config else False


def review_days_for(config, concept_type, rel=None):
    """Plazo (días) de la nota, o None si el vault no declara uno.

    Un prefijo de ruta en `paths` gana sobre el tipo: la misma `Project` en
    `publico/` caduca a los 30 días y en `projects/` no caduca.
    """
    if config is None:
        return None
    paths = getattr(config, "review_paths", None) or {}
    if rel:
        rel_norm = str(rel).replace("\\", "/")
        best = None
        for prefix, days in paths.items():
            p = str(prefix).strip("/") + "/"
            if rel_norm.startswith(p) and (best is None or len(p) > len(best[0])):
                best = (p, days)
        if best is not None:
            return int(best[1])
    days = getattr(config, "review_days", None) or {}
    value = days.get(str(concept_type)) if concept_type else None
    return int(value) if value is not None else None


def due_date(config, concept_type, fm, rel=None):
    """Fecha en que hay que volver a verificar la nota, o None.

    `revisar_el` explícito manda; si falta, `verificado_el` + plazo del tipo.
    Sin `verificado_el` legible no hay fecha (la nota nunca se verificó).
    """
    fm = fm or {}
    explicit = _as_date(fm.get(DUE_FIELD))
    if explicit is not None:
        return explicit
    verified = _as_date(fm.get(VERIFIED_FIELD))
    if verified is None:
        return None
    days = review_days_for(config, concept_type, rel)
    if days is None:
        return None
    return verified + timedelta(days=int(days))


def check_review_dates(config, concept_type, fm):
    """Problemas de formato y de orden entre las dos fechas.

    Returns:
        list[str]: vacía si no están o si cumplen.
    """
    fm = fm or {}
    problems = []
    raw_v = fm.get(VERIFIED_FIELD)
    raw_d = fm.get(DUE_FIELD)
    verified = _as_date(raw_v)
    due = _as_date(raw_d)
    if raw_v not in (None, "") and verified is None:
        problems.append(f"'{VERIFIED_FIELD}' = '{raw_v}' no es una fecha ISO (YYYY-MM-DD)")
    if raw_d not in (None, "") and due is None:
        problems.append(f"'{DUE_FIELD}' = '{raw_d}' no es una fecha ISO (YYYY-MM-DD)")
    if verified is not None and due is not None and due < verified:
        problems.append(f"'{DUE_FIELD}' ({due}) es anterior a '{VERIFIED_FIELD}' ({verified})")
    return problems


def overdue_days(config, concept_type, fm, rel=None, today=None):
    """Días que lleva vencida la verificación (>0), o None si no venció o no aplica."""
    due = due_date(config, concept_type, fm, rel)
    if due is None:
        return None
    today = today or date.today()
    if isinstance(today, datetime):
        today = today.date()
    delta = (today - due).days
    return delta if delta > 0 else None
