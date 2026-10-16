"""Command stamp — `timestamp` dice cuándo cambió el CONTENIDO, no los metadatos.

`timestamp` es «último cambio con sentido». Hasta ahora lo renovaba quien
escribía: `edit` lo ponía en cada cambio (también si solo tocaba el status), y
una edición en Obsidian no lo tocaba nunca. El resultado era un campo que no
medía nada fijo.

Este comando corre en el pre-commit, sobre los .md staged, y aplica una sola
regla contra HEAD:

  - Si cambió el contenido (cuerpo, title o description) y el timestamp quedó
    igual al de HEAD → se renueva a ahora.
  - Si NO cambió el contenido pero el timestamp sí → se restaura el de HEAD.
    Un cambio de status, de aristas o una migración de metadatos no lo renueva.
  - Archivos nuevos (sin versión en HEAD) no se tocan.

Con `--staged` (modo hook) vuelve a stagear lo que modificó. Con rutas
explícitas solo escribe el archivo.

Usage:
    python3 -m cli stamp --staged
    python3 -m cli stamp decisions/mi-decision.md
"""

import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from cli.frontmatter import parse_frontmatter

_CONTENT_KEYS = ("title", "description")
_TS_LINE = re.compile(r"^timestamp:[ \t]*(.*)$", re.MULTILINE)


def _now():
    """Hora local de Colombia (UTC-5), mismo criterio que new.py y edit.py."""
    return (datetime.now(timezone.utc) - timedelta(hours=5)).strftime(
        "%Y-%m-%dT%H:%M:%S-05:00")


def _split(text):
    """(frontmatter dict | None, raw fm, body). Body = todo después del cierre."""
    fm, raw = parse_frontmatter(text)
    if fm is None:
        return None, None, text
    end = text.find("\n---\n", 3)
    if end == -1:
        end = text.find("\n---", 3)
        body = text[end + 4:] if end != -1 else ""
    else:
        body = text[end + 5:]
    return fm, raw, body


def _content_signature(fm, body):
    """Lo que cuenta como contenido: cuerpo normalizado + title + description."""
    meta = tuple(str(fm.get(k, "")).strip() for k in _CONTENT_KEYS)
    return (body.strip(), meta)


def _head_text(vault, rel):
    """Versión en HEAD del archivo, o None si no existe en HEAD."""
    result = subprocess.run(
        ["git", "-C", str(vault), "show", f"HEAD:{rel}"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _raw_timestamp(text):
    """Valor textual de la línea `timestamp:` del frontmatter ('' si no hay)."""
    end = text.find("\n---", 3)
    m = _TS_LINE.search(text[:end] if end != -1 else text)
    return m.group(1).strip() if m else ""


def _set_timestamp(text, value):
    """Reescribe la línea `timestamp:` del frontmatter preservando el resto."""
    end = text.find("\n---", 3)
    head, tail = text[:end], text[end:]
    if not _TS_LINE.search(head):
        return text
    return _TS_LINE.sub(f"timestamp: {value}", head, count=1) + tail


def decide(work_text, head_text, now=None):
    """Decide el timestamp que corresponde al archivo de trabajo.

    Returns:
        tuple[str | None, str]: (nuevo texto o None si no hay que tocar nada,
        motivo legible).
    """
    fm, _, body = _split(work_text)
    if fm is None or "timestamp" not in fm:
        return None, "sin frontmatter o sin timestamp"
    if head_text is None:
        return None, "archivo nuevo"
    head_fm, _, head_body = _split(head_text)
    if head_fm is None:
        return None, "HEAD sin frontmatter"

    # El timestamp se compara y se restaura como TEXTO: YAML lo parsea a
    # datetime y su str() cambia el formato (pierde la «T»).
    work_ts = _raw_timestamp(work_text)
    head_ts = _raw_timestamp(head_text)
    content_changed = _content_signature(fm, body) != _content_signature(head_fm, head_body)

    if content_changed:
        if work_ts != head_ts:
            return None, "contenido cambió y el timestamp ya se renovó"
        return _set_timestamp(work_text, now or _now()), "contenido cambió: timestamp renovado"
    if work_ts != head_ts and head_ts:
        return _set_timestamp(work_text, head_ts), "solo metadatos: timestamp restaurado"
    return None, "sin cambios de contenido"


def _staged_md(vault):
    result = subprocess.run(
        ["git", "-C", str(vault), "diff", "--cached", "--name-only",
         "--diff-filter=M", "*.md"],
        capture_output=True, text=True, timeout=10,
    )
    return [f.strip() for f in result.stdout.split("\n") if f.strip()]


def run(args, vault, config=None):
    staged = bool(getattr(args, "staged", False))
    targets = list(getattr(args, "targets", None) or [])
    if staged:
        targets = _staged_md(vault)
    if not targets:
        print("✓ stamp: nada que revisar.")
        return 0

    touched = []
    for rel in targets:
        path = vault / rel
        if not path.exists() or path.name in ("index.md", "log.md", "dashboard.md"):
            continue
        try:
            work_text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        new_text, reason = decide(work_text, _head_text(vault, rel))
        if new_text is None or new_text == work_text:
            continue
        path.write_text(new_text, encoding="utf-8")
        touched.append(rel)
        print(f"🕒 stamp: {rel} — {reason}")

    if staged and touched:
        subprocess.run(["git", "-C", str(vault), "add", "--"] + touched,
                       capture_output=True, text=True, timeout=10)
    if not touched:
        print(f"✓ stamp: {len(targets)} archivo(s) revisados, ninguno ajustado.")
    return 0
