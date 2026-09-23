"""Vault path resolution and file discovery.

Responsibilities:
- Determine vault location (CLI arg > $OKF_VAULT > ~/OKF-Vault)
- Find .md files in the vault with configured exclusions
- Build name→path index for wikilink resolution
"""

import os
from pathlib import Path

# Política de acceso (scope de subárbol + modo solo-lectura). Re-exportada
# acá porque los comandos ya importan de cli.vault.
from cli.access import (  # noqa: F401
    get_scope,
    in_scope_file,
    is_readonly,
    normalize_scope,
    resolve_scope,
    scope_root,
    set_scope,
    under_scope,
)

# Constantes unificadas — defaults embebidos (pisan por Config si existe .okf.config.yaml)
DEFAULT_EXCLUDE_FILES = {"index.md", "log.md", "dashboard.md", "AGENTS.md"}
DEFAULT_EXCLUDE_DIRS = {".git", ".obsidian", "Templates", "scripts", "references", "assets", ".dsh-build"}

# Referencia mutable: los comandos que instancian Config las pisan.
# Se mantienen para backward compat con código que no recibe Config aún.
EXCLUDE_FILES = DEFAULT_EXCLUDE_FILES.copy()
EXCLUDE_DIRS = DEFAULT_EXCLUDE_DIRS.copy()


def resolve_vault_path(cli_arg=None):
    """Determines the vault path.

    Priority:
        1. Explicit argument passed via CLI (Path or str)
        2. OKF_VAULT environment variable
        3. ~/OKF-Vault (default)

    Returns:
        Absolute path to vault.
    """
    if cli_arg is not None:
        path = Path(cli_arg)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve()

    env = os.environ.get("OKF_VAULT")
    if env:
        return Path(env).expanduser().resolve()

    return (Path.home() / "OKF-Vault").resolve()


def _get_excludes(config=None):
    """Gets exclusions from Config or falls back to module globals."""
    if config is not None:
        return config.exclude_files, config.exclude_dirs
    return EXCLUDE_FILES, EXCLUDE_DIRS


def apply_config(config):
    """Applies exclusions from a Config object to the module's global variables.

    Call once at startup, after instantiating Config.
    Maintains backward compat with code that imports EXCLUDE_FILES/EXCLUDE_DIRS directly.
    """
    global EXCLUDE_FILES, EXCLUDE_DIRS
    EXCLUDE_FILES = config.exclude_files
    EXCLUDE_DIRS = config.exclude_dirs


def find_md_files(vault, exclude_files=None, exclude_dirs=None, config=None):
    """Finds all .md files in the vault that are concepts.

    Args:
        vault: Path to vault.
        exclude_files: Set of filenames to exclude (deprecated: use config).
        exclude_dirs: Set of directory names to exclude (deprecated: use config).
        config: Config object (preferred over explicit exclude_files/dirs).

    Returns:
        List of absolute Paths, sorted.
    """
    if exclude_files is None and exclude_dirs is None:
        exclude_files, exclude_dirs = _get_excludes(config)
    elif exclude_files is None:
        exclude_files = EXCLUDE_FILES
    elif exclude_dirs is None:
        exclude_dirs = EXCLUDE_DIRS

    files = []
    for md_file in sorted(vault.rglob("*.md")):
        if md_file.name in exclude_files:
            continue
        parts = md_file.relative_to(vault).parts
        if any(p in exclude_dirs for p in parts):
            continue
        # Scope de subárbol (modo público): nada fuera del prefijo publicado.
        if not under_scope(parts):
            continue
        files.append(md_file)
    return files


def build_name_index(vault, config=None):
    """Builds filename → relpath index for global wikilink search.

    Args:
        vault: Path to vault.
        config: Config object (optional).

    Returns:
        dict[str, str] — {filename: relpath}
    """
    all_files = find_md_files(vault, config=config)
    return {f.name: str(f.relative_to(vault)) for f in all_files}
