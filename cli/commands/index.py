"""Command index — Regenerate index.md and log.md for the OKF vault."""

import subprocess
import sys
from pathlib import Path


# Máximo permitido para cualquier campo de frontmatter (300 chars).
# Valores más largos son corrupción — probablemente el bug de inflación de comillas.
_MAX_FIELD_LENGTH = 600


def _extract_frontmatter_field(md_path, field):
    """Extracts a field from YAML frontmatter, with multiline support."""
    try:
        text = md_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return ""
        end = text.find("\n---\n", 3)
        if end == -1:
            end = text.find("\n---", 3)
        if end == -1:
            return ""
        fm_lines = text[3:end].split("\n")

        capturing = False
        value_lines = []
        indent = 0

        for line in fm_lines:
            stripped = line.strip()
            if not capturing:
                if stripped.startswith(f"{field}:"):
                    capturing = True
                    val = stripped[len(f"{field}:"):].strip()
                    if val in (">", "|", ">-", "|-", ">+", "|+"):
                        indent = len(line) - len(line.lstrip()) + 2
                        value_lines = []
                        continue
                    # Double-quoted YAML: "value"
                    if val.startswith('"') and val.endswith('"'):
                        result = val[1:-1]
                        if len(result) > _MAX_FIELD_LENGTH:
                            return ""
                        return result
                    # Single-quoted YAML: 'value' or 'value with ''escaped'' quotes'
                    if val.startswith("'") and val.endswith("'"):
                        inner = val[1:-1]
                        result = inner.replace("''", "'")
                        if len(result) > _MAX_FIELD_LENGTH:
                            return ""
                        return result
                    # Unquoted scalar — may have wrapping quotes from a previous
                    # buggy regeneration; strip them if present
                    if val:
                        if len(val) > _MAX_FIELD_LENGTH:
                            return ""
                        return val
            else:
                line_indent = len(line) - len(line.lstrip())
                if stripped and line_indent < indent and ":" in stripped:
                    break
                if stripped and not stripped.startswith("#"):
                    value_lines.append(stripped)

        if value_lines:
            result = " ".join(value_lines)
            if len(result) > _MAX_FIELD_LENGTH:
                return ""
            return result
    except Exception:
        pass
    return ""


def _extract_frontmatter_field_no_cap(md_path, field):
    """Extracts a YAML frontmatter field without length limit (for warnings)."""
    try:
        text = md_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return ""
        end = text.find("\n---\n", 3)
        if end == -1:
            end = text.find("\n---", 3)
        if end == -1:
            return ""
        fm_lines = text[3:end].split("\n")

        capturing = False
        value_lines = []
        indent = 0

        for line in fm_lines:
            stripped = line.strip()
            if not capturing:
                if stripped.startswith(f"{field}:"):
                    capturing = True
                    val = stripped[len(f"{field}:"):].strip()
                    if val in (">", "|", ">-", "|-", ">+", "|+"):
                        indent = len(line) - len(line.lstrip()) + 2
                        value_lines = []
                        continue
                    if val.startswith('"') and val.endswith('"'):
                        return val[1:-1]
                    if val.startswith("'") and val.endswith("'"):
                        inner = val[1:-1]
                        return inner.replace("''", "'")
                    if val:
                        return val
            else:
                line_indent = len(line) - len(line.lstrip())
                if stripped and line_indent < indent and ":" in stripped:
                    break
                if stripped and not stripped.startswith("#"):
                    value_lines.append(stripped)

        if value_lines:
            return " ".join(value_lines)
    except Exception:
        pass
    return ""


def _extract_body_description(md_path):
    """Extracts the first line after the title as description."""
    try:
        content = md_path.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("\n---\n", 3)
            if end != -1:
                content = content[end + 5:]
            else:
                end = content.find("\n---", 3)
                if end != -1:
                    content = content[end + 4:]
        lines = content.split("\n")
        found_title = False
        for line in lines:
            stripped = line.strip()
            if not found_title and stripped.startswith("# "):
                found_title = True
                continue
            if found_title:
                if stripped and not stripped.startswith("#"):
                    return stripped
    except Exception:
        pass
    return ""


def _get_dir_description(parent_dir, dirname):
    """Gets the description of a subdirectory from its index.md.

    Args:
        parent_dir: Path of the parent directory.
        dirname: Name of the subdirectory.

    Priority:
    1. 'description' field in index.md frontmatter
    2. First line of content after the title (body), if not a link
    3. \"\" if nothing useful
    """
    index_path = parent_dir / dirname / "index.md"
    if not index_path.exists():
        return ""
    desc = _extract_frontmatter_field(index_path, "description")
    if desc:
        return desc
    body_desc = _extract_body_description(index_path)
    # Solo usar body description si no es placeholder ni link markdown
    if body_desc and not body_desc.startswith("Contenido del directorio"):
        if "](" not in body_desc and not body_desc.startswith("* [") and not body_desc.startswith("- ["):
            return body_desc
    return ""


def _find_concept_dirs(vault):
    """Finds directories containing concepts or subdirectories with concepts."""
    from cli.vault import EXCLUDE_DIRS
    exclude_dirs = EXCLUDE_DIRS
    concept_dirs = []
    for d in sorted(vault.rglob("*")):
        if not d.is_dir() or d.name.startswith("."):
            continue
        parts = d.relative_to(vault).parts
        if any(p in exclude_dirs for p in parts):
            continue
        # Directorio con conceptos directos
        has_concepts = any(
            f.suffix == ".md" and f.stem not in ("index", "log")
            for f in d.iterdir()
        )
        # Directorio con subdirectorios que tienen conceptos
        has_subdirs_with_concepts = any(
            sub.is_dir() and any(
                f.suffix == ".md" and f.stem not in ("index", "log")
                for f in sub.rglob("*.md")
            )
            for sub in d.iterdir() if sub.is_dir()
        )
        if has_concepts or has_subdirs_with_concepts:
            concept_dirs.append(d)
    return concept_dirs


def _find_all_content_dirs(vault):
    """Finds directories with content for the root index."""
    dirs = []
    for d in sorted(vault.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and d.name not in (".git", ".obsidian"):
            dirs.append(d.name)
    return dirs


def _generate_root_index(vault):
    """Generates the root index.md, preserving existing custom sections."""
    all_dirs = _find_all_content_dirs(vault)
    lines = [
        "# OKF-Vault", "",
        "Memoria persistente de Hermes Agent. Vault de Obsidian que sigue el estándar OKF v0.1.",
        "", "## Sub-directorios", "",
    ]
    for dname in all_dirs:
        # Sin index.md no hay a dónde enlazar: `index` sólo genera índices para
        # carpetas con conceptos, y cuando llega acá ya los generó (paso 1 de run).
        if not (vault / dname / "index.md").exists():
            continue
        desc = _get_dir_description(vault, dname)
        if desc:
            lines.append(f"* [{dname}/]({dname}/index.md) - {desc}")
        else:
            lines.append(f"* [{dname}/]({dname}/index.md)")
            index_path = vault / dname / "index.md"
            if index_path.exists():
                print(f"  🚨 MISSING DESCRIPTION: {dname}/ — ADD description IN index.md", file=sys.stderr)

    content = "\n".join(lines) + "\n"

    # Preservar secciones personalizadas del index.md existente
    # (todo lo que está después de la última entrada de sub-directorio)
    existing = vault / "index.md"
    if existing.exists():
        try:
            old = existing.read_text()
            # Encontrar la última línea que es una entrada de sub-directorio
            last_entry_idx = -1
            old_lines = old.split("\n")
            for i, line in enumerate(old_lines):
                if line.startswith("* [") and "/](index.md)" in line or "/index.md)" in line:
                    last_entry_idx = i
            if last_entry_idx >= 0:
                custom = old_lines[last_entry_idx + 1:]
                # Saltar líneas vacías iniciales
                while custom and not custom[0].strip():
                    custom = custom[1:]
                if custom:
                    content = content.rstrip("\n") + "\n\n" + "\n".join(custom).rstrip() + "\n"
        except Exception:
            pass

    return content



def _generate_index(dir_path, vault):
    """Generates index.md for a directory."""
    name = dir_path.name
    # Título: usar el nombre capitalizado, pero si es subdirectorio, incluir contexto del padre
    title = name.capitalize()

    # Descripción del directorio: frontmatter > body preservado > vacío
    own_index = dir_path / "index.md"
    purpose = ""
    if own_index.exists():
        purpose = _extract_frontmatter_field(own_index, "description")
        if not purpose:
            body_desc = _extract_body_description(own_index)
            if body_desc and not body_desc.startswith("Contenido del directorio"):
                if "](" not in body_desc and not body_desc.startswith("* [") and not body_desc.startswith("- ["):
                    purpose = body_desc

    # Las skills no llevan prosa ni frontmatter en su index.md: sin esto, el
    # índice del directorio queda sin description y el index padre avisa
    # «no description in index.md» por cada skill (6 en OKF-Vault).
    if not purpose:
        skill_md = dir_path / "SKILL.md"
        if skill_md.exists():
            purpose = _extract_frontmatter_field(skill_md, "description")

    lines = []
    if purpose:
        # Safety: si la descripción es excesivamente larga, no la ponemos en frontmatter
        if len(purpose) > _MAX_FIELD_LENGTH:
            purpose = ""
    if purpose:
        # Usar comillas dobles (no simples) — _extract_frontmatter_field las maneja correctamente
        safe = purpose.replace('\\', '\\\\').replace('"', '\\"')
        # Doble safety: si el escaping duplica y excede el límite, skip
        if len(safe) > _MAX_FIELD_LENGTH * 2:
            purpose = ""
        else:
            lines = ["---", f'description: "{safe}"', "---", ""]
    lines.append(f"# {title}")
    lines.append("")

    from cli.vault import EXCLUDE_DIRS
    exclude_dirs = EXCLUDE_DIRS
    concepts = []
    for f in sorted(dir_path.iterdir()):
        if f.suffix == ".md" and f.stem not in ("index", "log"):
            desc = _extract_frontmatter_field(f, "description")
            status = _extract_frontmatter_field(f, "status")
            if not desc:
                # Check if description exists but was too long (silently dropped by cap)
                raw = _extract_frontmatter_field_no_cap(f, "description")
                if raw and len(raw) > _MAX_FIELD_LENGTH:
                    print(f"  🚨 LONG DESCRIPTION: {f.relative_to(vault)} — {len(raw)} chars (max {_MAX_FIELD_LENGTH}). TRUNCATE!", file=sys.stderr)
            concepts.append((f.name, desc, status))

    if concepts:
        lines.append("")
        lines.append("## Guías Disponibles")
        lines.append("")
        for fname, desc, status in concepts:
            prefix = "⚠️ " if status == "propuesta" else ""
            if desc:
                lines.append(f"* {prefix}[{fname}]({fname}) - {desc}")
            else:
                lines.append(f"* {prefix}[{fname}]({fname})")

    subdirs = []
    for d in sorted(dir_path.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and d.name not in exclude_dirs:
            has_content = any(
                f.suffix == ".md" and f.stem not in ("index", "log")
                for f in d.rglob("*.md")
            )
            if has_content or d.name in _find_all_content_dirs(vault) or (d / "index.md").exists():
                subdirs.append(d.name)

    if subdirs:
        lines.append("")
        lines.append("## Sub-directorios")
        lines.append("")
        for dname in subdirs:
            desc = _get_dir_description(dir_path, dname)
            if desc:
                lines.append(f"* [{dname}/]({dname}/index.md) - {desc}")
            else:
                lines.append(f"* [{dname}/]({dname}/index.md)")
                index_path = dir_path / dname / "index.md"
                if index_path.exists():
                    print(f"  ⚠️  {(dir_path / dname).relative_to(vault)}/ — no description in index.md", file=sys.stderr)

    return "\n".join(lines) + "\n"


def _generate_log(vault):
    """Generates log.md from git history."""
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "log", "--oneline", "--date=short",
             "--format=%h %ad %s", "-50"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return "# Update Log\n\n*Could not read git history.*\n"

    if result.returncode != 0:
        return "# Update Log\n\n*Could not read git history.*\n"

    lines = ["# Update Log", ""]
    current_date = None

    for entry in result.stdout.strip().split("\n"):
        if not entry.strip():
            continue
        parts = entry.split(" ", 2)
        if len(parts) < 3:
            continue
        commit_hash, date, message = parts
        date = date.strip()
        message = message.strip()

        if date != current_date:
            current_date = date
            lines.append(f"## {date}")
            lines.append("")
        lines.append(f"* `{commit_hash}` — {message}")

    if len(lines) == 2:
        lines.append("*No commits yet.*")

    return "\n".join(lines) + "\n"


def run(args, vault, config=None):
    """Regenerates index.md and log.md."""
    regenerated = []

    # 1. Índices de directorios con conceptos — ordenar por profundidad (hijos primero)
    #    para que los padres lean los index actualizados de los hijos
    concept_dirs = _find_concept_dirs(vault)
    concept_dirs.sort(key=lambda d: len(d.relative_to(vault).parts), reverse=True)

    for d in concept_dirs:
        index_path = d / "index.md"
        index_path.write_text(_generate_index(d, vault), encoding="utf-8")
        regenerated.append(str(index_path.relative_to(vault)))

    # 2. Index raíz — al final, para que lea los index de nivel 1 ya actualizados
    root_index = vault / "index.md"
    root_index.write_text(_generate_root_index(vault), encoding="utf-8")
    regenerated.append(str(root_index.relative_to(vault)))

    # 3. Log desde git
    log_path = vault / "log.md"
    log_path.write_text(_generate_log(vault), encoding="utf-8")
    regenerated.append(f"log.md ({log_path.stat().st_size} bytes)")

    for f in regenerated:
        print(f"  ✓ {f}")

    print(f"\n✅ {len(regenerated)} files regenerated.")
    return 0
