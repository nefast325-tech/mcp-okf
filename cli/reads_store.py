"""Read counters store — telemetría de lecturas FUERA del frontmatter.

Decisión 2026-08-27: los read counters ya NO se escriben en el frontmatter
de los conceptos. El frontmatter es contenido; los counters son telemetría.
Mezclarlos versionaba ruido en cada lectura y generaba conflictos de merge
constantes en vaults multi-actor con sync automático (pull --rebase cada
pocos minutos).

Los counters viven en <vault>/.okf/state/reads.jsonl: append-only, NO
versionado, con lock por archivo (fcntl.flock) para escritura segura entre
actores del mismo filesystem (bind mounts compartidos).

Formato de línea (JSON):
    {"slug": "decisions/foo.md", "ts": "2026-08-27T16:40:00-0500", "baseline": 12}
    {"slug": "decisions/foo.md", "ts": "2026-08-27T16:41:00-0500"}
"baseline" suma N al contador (migración desde frontmatter); la ausencia
de "baseline" suma 1 (una lectura).
"""

import contextlib
import fcntl
import json
import os
import re
import time
from pathlib import Path

STORE_RELPATH = ".okf/state/reads.jsonl"
_MAX_VAULT_UP = 12

# Frontmatter completo: ---\n...\n---\n
_FM_RE = re.compile(r"^(---\n)(.*?)(\n---\n)", re.DOTALL)


def store_path(vault):
    """Ruta del store para un vault."""
    return Path(vault) / STORE_RELPATH


def find_vault(filepath):
    """Deriva el vault desde un archivo: sube buscando .okf.config.yaml.

    Returns:
        Path or None.
    """
    p = Path(filepath).resolve()
    for _ in range(_MAX_VAULT_UP):
        if (p / ".okf.config.yaml").exists():
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def _rel_slug(filepath, vault):
    try:
        rel = Path(filepath).resolve().relative_to(Path(vault).resolve())
    except ValueError:
        return None
    return str(rel).replace(os.sep, "/")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _fm_reads(content):
    """Extrae (valor, contenido_limpio) del campo reads del frontmatter.

    Opera SOLO dentro del bloque frontmatter (no toca el body). Devuelve
    (None, content) si no hay campo reads. El contenido limpio preserva
    TODO el archivo: frontmatter editado + body original.
    """
    m = _FM_RE.match(content)
    if not m:
        return None, content
    fm = m.group(2)
    rm = re.search(r"^reads:\s*(\d+)", fm, re.MULTILINE)
    if not rm:
        return None, content
    n = int(rm.group(1))
    new_fm = re.sub(r"^reads:\s*\d+\n?", "", fm, count=1, flags=re.MULTILINE)
    body = content[m.end():]
    return n, m.group(1) + new_fm + m.group(3) + body


@contextlib.contextmanager
def _locked(vault, mode="a"):
    """Abre el store con lock (exclusivo para escritura, compartido lectura)."""
    path = store_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, mode, encoding="utf-8")
    try:
        if mode.startswith("r"):
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield f
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def get_reads(vault):
    """{slug: total} — baselines de migración + eventos de lectura.

    Args:
        vault: Path al root del vault.

    Returns:
        dict[str, int]
    """
    counts = {}
    if not store_path(vault).exists():
        return counts
    try:
        with _locked(vault, mode="r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                slug = ev.get("slug")
                if not slug:
                    continue
                counts[slug] = counts.get(slug, 0) + int(ev.get("baseline", 1))
    except Exception:
        return counts
    return counts


def _migrate_file_if_needed(filepath, rel, vault):
    """Migración explícita: releer y limpiar bajo el mismo candado de edit."""
    from cli.access import is_readonly
    from cli.revision import candado, escribir_atomico
    if is_readonly():
        raise PermissionError("readonly no permite migrar frontmatter")
    with candado(vault, filepath):
        try:
            content = filepath.read_text(encoding="utf-8")
            n, new_content = _fm_reads(content)
            if n is None:
                return 0, 0
            with _locked(vault, mode="a+") as f:
                f.seek(0)
                seeded = False
                for line in f:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("slug") == rel and "baseline" in event:
                        seeded = True
                        break
                if not seeded:
                    f.seek(0, os.SEEK_END)
                    f.write(json.dumps({"slug": rel, "ts": _now(), "baseline": n},
                                       ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            if new_content != content:
                escribir_atomico(filepath, new_content)
            return int(not seeded), int(new_content != content)
        except OSError:
            return 0, 0


def increment_reads(filepath, vault):
    """Incrementa el contador del concepto en el store.

    Args:
        filepath: Path al archivo .md (absoluto o relativo al vault).
        vault: Path al root del vault.

    Returns:
        int: Total del concepto (baseline + lecturas), 0 si falla.
    """
    vault = Path(vault)
    rel = _rel_slug(filepath, vault)
    if rel is None:
        return 0
    # Una lectura no puede reescribir Markdown (ni siquiera para migrar un
    # contador viejo): puede ser readonly o competir con edit. La limpieza y
    # el baseline histórico pertenecen al comando explícito migrate-reads.
    event = {"slug": rel, "ts": _now()}
    try:
        with _locked(vault, mode="a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        return 0
    return get_reads(vault).get(rel, 0)


def migrate_frontmatter_reads(vault):
    """Migración en masa: siembra todos los counters del frontmatter en el
    store y limpia el campo de los archivos.

    Args:
        vault: Path al root del vault.

    Returns:
        tuple[int, int]: (contadores sembrados, frontmatters limpiados).
    """
    from cli.vault import find_md_files

    vault = Path(vault)
    seeded = cleaned = 0
    for md in find_md_files(vault):
        rel = str(md.relative_to(vault))
        added, removed = _migrate_file_if_needed(md, rel, vault)
        seeded += added
        cleaned += removed
    return seeded, cleaned
