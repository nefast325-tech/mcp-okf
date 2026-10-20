"""Command review — Cybernetic loop sensor.

Scans concepts with cyber.review_on <= today and classifies by severity.
Desde la fase 4 lista también las notas cuya verificación venció
(`revisar_el`, o `verificado_el` + el plazo de `types.review`; ver
cli/verificacion.py), en una sección aparte y con `kind: verificacion` en
el JSON.

Semántica de fechas (fijada 2026-09-10, tras el off-by-one de health):
- ``review_on < hoy`` → **vencido**: el review pasó sin cerrarse. Es lo que
  cuenta el dashboard (``review_on_vencidos``) y lo que health marca como
  loop roto. Se expone por ítem en el campo ``vencido``.
- ``review_on == hoy`` → **vence hoy**: sigue en la lista de tareas (hay que
  actuar hoy) pero todavía no está roto. ``collect_due`` lo devuelve con
  ``vencido: False``; el dashboard lo cuenta aparte (``review_on_hoy``).
"""

import json
import sys
from datetime import datetime, timezone
from cli.frontmatter import parse_frontmatter
from cli.vault import find_md_files


def get_today_str():
    """'Today' in the domain timezone (Colombia, UTC-5), not UTC.

    Fix 2026-08-02: comparing review_on against UTC advanced
    expirations between 19:00 and 24:00 local time (UTC was already on
    the next day) — a tomorrow review_on appeared expired today.
    """
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")


def collect_due(vault):
    """Finds concepts with cyber.review_on <= today.

    Cada ítem trae ``vencido`` (``review_on < hoy``): la lista incluye los que
    vencen hoy porque son tareas de hoy, pero ``vencido`` los distingue para
    quien necesite contar loops realmente rotos (dashboard, health).
    """
    today = get_today_str()
    due = []

    for f in find_md_files(vault):
        rel = str(f.relative_to(vault))
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

        review_on = cyber.get("review_on")
        if not review_on:
            continue

        review_str = str(review_on)
        if review_str <= today:
            outcome = str(cyber.get("outcome", ""))
            sensor = str(cyber.get("sensor", "?"))
            metric = cyber.get("target_metric", {})
            metric_name = metric.get("name", "?") if isinstance(metric, dict) else "?"

            if outcome in ("pending", "", "None"):
                severity = "required"
            else:
                severity = "verify"

            due.append({
                "file": rel,
                "type": str(fm.get("type", "")),
                "title": str(fm.get("title", rel)),
                "severity": severity,
                "outcome": outcome if outcome not in ("", "None") else "(unmeasured)",
                "review_on": review_str,
                "vencido": review_str < today,
                "sensor": sensor,
                "metric": metric_name,
            })

    return due


def collect_verification_due(vault, config=None, today=None):
    """Notas cuya verificación venció (fase 4, cli/verificacion.py).

    La fecha es `revisar_el` o `verificado_el` + el plazo del tipo
    (`types.review`). Misma forma que los ítems de `collect_due` para que un
    consumidor que itera la lista no se rompa: `kind: verificacion`,
    `review_on` = la fecha derivada, `severity: verify`.
    """
    from datetime import date as _date
    from cli.verificacion import DUE_FIELD, VERIFIED_FIELD, due_date

    if today is None:
        today = _date.fromisoformat(get_today_str())
    items = []
    for f in find_md_files(vault, config=config):
        rel = str(f.relative_to(vault))
        try:
            fm, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not fm:
            continue
        ctype = str(fm.get("type", ""))
        due = due_date(config, ctype, fm, rel)
        if due is None or due > today:
            continue
        items.append({
            "kind": "verificacion",
            "file": rel,
            "type": ctype,
            "title": str(fm.get("title", rel)),
            "severity": "verify",
            "outcome": "(unmeasured)",
            "review_on": due.isoformat(),
            "vencido": due < today,
            "sensor": VERIFIED_FIELD,
            "metric": DUE_FIELD,
            "verificado_el": str(fm.get(VERIFIED_FIELD, "")),
        })
    return items


def _marca_hoy(item):
    """Sufijo para la salida humana: distingue «vence hoy» de «vencido»."""
    return "" if item.get("vencido") else "  ⏰ vence hoy"


def run(args, vault, config=None):
    """Runs the cybernetic review."""
    json_out = getattr(args, "json", False)
    count_only = getattr(args, "count", False)

    due = collect_due(vault)
    for d in due:
        d.setdefault("kind", "cyber")
    verification = collect_verification_due(vault, config=config)

    if json_out:
        print(json.dumps(due + verification, ensure_ascii=False, indent=2))
    elif count_only:
        required = sum(1 for d in due if d["severity"] == "required")
        verify = sum(1 for d in due if d["severity"] == "verify")
        if due or verification:
            print(f"🔴{required} 🟡{verify + len(verification)}")
        else:
            print("✅ nothing pending")
    else:
        if not due and not verification:
            print("✅ Nothing pending review.")
        else:
            required = [d for d in due if d["severity"] == "required"]
            verify = [d for d in due if d["severity"] == "verify"]

            print(f"📡 Cyber review — {get_today_str()}")
            print("─" * 55)

            if required:
                print(f"\n🔴 Review required ({len(required)}):")
                for d in required:
                    print(f"   [{d['type']}] {d['file']}")
                    print(f"   Sensor: {d['sensor']} | Metric: {d['metric']}")
                    print(f"   Outcome: {d['outcome']} | Review era: {d['review_on']}"
                          f"{_marca_hoy(d)}")
                    print("")

            if verify:
                print(f"🟡 Re-verify validity ({len(verify)}):")
                for d in verify:
                    print(f"   [{d['type']}] {d['file']}")
                    print(f"   Outcome actual: {d['outcome']} | Review era: {d['review_on']}"
                          f"{_marca_hoy(d)}")
                    print(f"   Is this decision still valid?")
                    print("")

            if verification:
                print(f"📅 Verificación vencida ({len(verification)}):")
                for d in verification:
                    print(f"   [{d['type']}] {d['file']}")
                    print(f"   Verificada: {d['verificado_el'] or '(sin fecha)'} | "
                          f"Revisar era: {d['review_on']}{_marca_hoy(d)}")
                    print("   ¿Sigue siendo cierto? Comprobalo y actualizá verificado_el.")
                    print("")

            print(f"─\n{len(due) + len(verification)} concept(s) need attention.")

    return 1 if (due or verification) else 0
