"""Command read — Read a concept from the vault + auto-increment reads counter."""

import os
import sys
from pathlib import Path
from cli.frontmatter import increment_reads
from cli.access import in_scope_file, scope_root


def _find_file(target, vault):
    """Searches for a file by name or relative path in the vault.

    Con OKF_SCOPE activo la búsqueda queda acotada al subárbol: los slugs
    relativos se resuelven primero contra el scope (read("index.md") →
    <scope>/index.md) y nunca se devuelve un archivo de fuera del prefijo.
    """
    target = str(target).strip()
    roots = [scope_root(vault)]

    # 0) Ruta relativa al VAULT, si cae dentro del scope. Con scope, `read` y
    #    `traverse` deben aceptar el mismo slug: `read` imprime el path
    #    relativo al vault ("publico/x.md") y el agente lo vuelve a pasar tal
    #    cual — sin esto, el eco de una lectura no sirve como entrada.
    candidato_vault = vault / target
    if candidato_vault.is_file() and in_scope_file(candidato_vault, vault):
        return candidato_vault

    # 1) Coincidencia exacta por ruta relativa al scope
    for root in roots:
        for cand in (root / target,
                     root / (target if target.endswith(".md") else target + ".md")):
            if cand.is_file() and in_scope_file(cand, vault):
                return cand

    # 2) Por nombre de archivo
    for root in roots:
        for f in sorted(root.rglob("*.md")):
            if f.name == target and in_scope_file(f, vault):
                return f

    # 3) Por nombre parcial (solo candidatos dentro del scope)
    candidates = [
        f for root in roots for f in sorted(root.rglob("*.md"))
        if target in str(f.relative_to(vault)) and in_scope_file(f, vault)
    ]
    if len(candidates) == 1:
        return candidates[0]
    elif candidates:
        print(f"⚠ Ambiguo: {target}", file=sys.stderr)
        for c in candidates[:10]:
            print(f"  {c.relative_to(vault)}", file=sys.stderr)
        return None

    return None


def run(args, vault, config=None):
    """Reads a concept from the vault."""
    target = getattr(args, "target", None)
    if not target:
        print("Usage: python3 -m cli read <concept> [--offset N] [--limit N]",
              file=sys.stderr)
        return 1

    offset = getattr(args, "offset", 1)
    limit = getattr(args, "limit", 500)
    no_touch = getattr(args, "no_touch", False)
    test_session = os.environ.get("OKF_SESSION_PURPOSE", "").strip().lower() == "test"

    filepath = _find_file(target, vault)
    if filepath is None:
        print(f"✗ Not found: {target}", file=sys.stderr)
        return 1

    rel = filepath.relative_to(vault)

    # Touch (incrementar reads) — sesiones de test no contaminan la telemetría
    if not no_touch and not test_session:
        new_val = increment_reads(filepath, vault)
        if new_val:
            print(f"📖 {rel}  (reads: {new_val})", file=sys.stderr)
    else:
        label = "no touch" if no_touch else "test session — no touch"
        print(f"📖 {rel}  ({label})", file=sys.stderr)

    # Imprimir contenido
    print(f"─── {rel} ({filepath.stat().st_size} bytes) ───", file=sys.stderr)
    lines = filepath.read_text(encoding="utf-8").split("\n")
    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)

    for i in range(start, end):
        print(f"{i + 1}|{lines[i]}")

    if end < total:
        next_offset = end + 1
        print(f"\n─── truncated ({end}/{total} lines) — continue with --offset {next_offset} ───")

    return 0
