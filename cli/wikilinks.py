"""Extraction and resolution of wikilinks and markdown links.

Responsibilities:
- Extract targets from [[wikilinks]] and [text](path.md)
- Resolve targets to vault-relative paths
"""

import re
from pathlib import Path

from cli.access import under_scope

# Regex para wikilinks [[concepto]] y [[concepto|alias]]
WIKILINK_RE = re.compile(r'\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]')
# Regex para markdown links [text](path.md)
MDLINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+\.md)\)')

# Caché de {nombre_archivo: relpath} por proceso (una construcción por vault).
# El CLI ejecuta un comando por proceso; find_md_files respeta las exclusiones
# del Config ya aplicadas, así que esto reemplaza los rglob() full-vault que
# se disparaban por cada link no resuelto (194k scandir en graph stats).
_NAME_INDEX_CACHE = {}


def _cached_name_index(vault):
    """Builds (once per process) the filename → relpath index for a vault."""
    cache_key = str(vault)
    idx = _NAME_INDEX_CACHE.get(cache_key)
    if idx is None:
        from cli.vault import find_md_files, EXCLUDE_FILES, EXCLUDE_DIRS
        try:
            all_files = find_md_files(vault)
            idx = {f.name: str(f.relative_to(vault)) for f in all_files}
            # Extender a archivos no-concepto con extensión (.canvas, .png…)
            # para que [[Organismo-OKF.canvas]] resuelva como en Obsidian.
            for f in vault.rglob("*"):
                if f.is_file() and f.suffix not in ("", ".md") and f.name not in EXCLUDE_FILES:
                    parts = f.relative_to(vault).parts
                    if any(p in EXCLUDE_DIRS for p in parts):
                        continue
                    # Scope de subárbol: un agente público no resuelve assets
                    # (ni links) de fuera de la carpeta publicada.
                    if not under_scope(parts):
                        continue
                    idx.setdefault(f.name, str(f.relative_to(vault)))
        except Exception:
            idx = {}
        _NAME_INDEX_CACHE[cache_key] = idx
    return idx


def extract_links_from_text(text):
    """Extracts all link targets (wikilinks + markdown) from raw file text.

    Args:
        text: Full content of the .md file (including frontmatter).

    Returns:
        list[str]: List of unique targets.
    """
    # Quitar frontmatter y code blocks para evitar falsos positivos
    clean = text
    if clean.startswith("---"):
        end = clean.find("\n---\n", 3)
        if end != -1:
            clean = clean[end + 5:]
    clean = re.sub(r'```[\s\S]*?```', '', clean)
    clean = re.sub(r'`[^`]+`', '', clean)

    targets = set()

    # Wikilinks [[target]]
    for match in WIKILINK_RE.finditer(clean):
        # rstrip('\\') : en tablas Obsidian el alias se escapa como \|, y
        # el backslash queda pegado al target ([[x\|alias]]).
        target = match.group(1).strip().rstrip("\\")
        targets.add(target)

    # Markdown links [text](target.md)
    for match in MDLINK_RE.finditer(clean):
        target = match.group(2).strip()
        targets.add(target)

    return list(targets)


def extract_links(md_path, exclude_files=None):
    """Extracts all link targets (wikilinks + markdown) from a file.

    Args:
        md_path: Path to .md file.
        exclude_files: Set of filenames to exclude.

    Returns:
        list[str]: List of unique targets.
    """
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return []
    return extract_links_from_text(text)


def resolve_link(target, vault, current_dir, name_index=None):
    """Resolves a link target to a path relative to the vault.

    Args:
        target: Link text (e.g. "[[okf-v01]]" → "okf-v01").
        vault: Path to the vault.
        current_dir: Path to the directory of the file containing the link.
        name_index: dict {filename: relpath} for global lookup (optional,
                    avoids redundant rglob).

    Returns:
        str|None: Path relative to the vault, or None if not found.
    """
    from cli.vault import EXCLUDE_FILES

    # Quitar anchor (#section)
    if "#" in target:
        target = target.split("#")[0]

    # Ruta absoluta dentro del vault (/frameworks/algo.md)
    if target.startswith("/"):
        target = target.lstrip("/")
        if not target.endswith(".md"):
            target += ".md"
        candidate = vault / target
        if candidate.exists() and under_scope(target):
            return target
        return None

    # Ruta relativa con ../ o ./
    if target.startswith("."):
        candidate = (current_dir / target).resolve()
        try:
            result = str(candidate.relative_to(vault.resolve()))
            if not result.endswith(".md"):
                result += ".md"
            # Verificar existencia sobre la ruta final (con .md ya agregado):
            # antes devolvía la ruta resuelta aunque el archivo no existiera
            # (validate daba por buenos links a archivos inexistentes dentro
            # del vault; health los marcaba).
            if not (vault / result).exists():
                return None
            if not under_scope(result):
                return None
            return result
        except ValueError:
            return None

    # Ruta con path pero sin ./ (ej: frameworks/algo.md)
    if "/" in target:
        if not target.endswith(".md"):
            target += ".md"
        candidate = vault / target
        if candidate.exists() and under_scope(target):
            return target
        return None

    # Wikilink sin path — buscar nombre de archivo.
    # Si el target ya tiene extensión (.canvas, .png…), no agregar .md.
    if not target.endswith(".md") and not re.search(r"\.\w+$", target):
        target += ".md"
    name = target

    # Buscar en el mismo directorio primero
    candidate = current_dir / name
    if candidate.exists() and candidate.name not in EXCLUDE_FILES:
        rel_same = str(candidate.relative_to(vault))
        if under_scope(rel_same):
            return rel_same

    # Buscar por índice global (explícito o caché de proceso).
    # Antes había un rglob() full-vault como fallback: se disparaba por cada
    # link no resuelto (miles de escaneos recursivos → 4.6s en graph stats).
    # El índice contiene exactamente los archivos que find_md_files considera
    # conceptos (respeta exclusiones); un archivo fuera de él no es un target
    # válido del grafo, así que el fallback era puro desperdicio.
    if name_index is None:
        name_index = _cached_name_index(vault)
    if name in name_index:
        return name_index[name]

    # El índice explícito (md-only) puede no tener archivos no-concepto
    # (.canvas, .png…): consultar también la caché de proceso, que sí los
    # incluye. Resuelve [[Organismo-OKF.canvas]] como lo haría Obsidian.
    cached = _cached_name_index(vault)
    if name in cached:
        return cached[name]

    return None
