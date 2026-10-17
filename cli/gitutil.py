"""Git date utilities for the OKF CLI.

Replaces the per-file ``git log -1`` subprocess pattern (which spawned
~2 subprocesses per concept — ~10s in ``health``) with a single batched
``git log`` pass that builds a {relpath: {first, last}} date index.
"""

import re
import subprocess
from datetime import datetime, timezone

_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)$"
)

# Caché por proceso: el CLI ejecuta un comando por proceso, y el índice
# git no cambia durante la ejecución de un comando.
_DATES_CACHE = {}
_UNCOMMITTED_CACHE = {}
_PREFIX_CACHE = {}


def _parse_iso(value):
    """Parses an ISO 8601 date string to an aware datetime, or None."""
    try:
        dt = datetime.fromisoformat(value.strip())
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt
    except (ValueError, TypeError):
        return None


def build_git_dates_index(vault):
    """Builds {relpath: {"first": iso, "last": iso}} from a single git log.

    One subprocess: ``git log --format=%aI --name-only`` (newest first).
    For each file, ``last`` is the date of the most recent commit that
    touched it and ``first`` the oldest.

    Args:
        vault: Path to the git repository root.

    Returns:
        dict[str, dict[str, str]] — relpath → {"first": ..., "last": ...}.
        Empty dict if the vault is not a git repo or git is unavailable.
    """
    cache_key = str(vault)
    cached = _DATES_CACHE.get(cache_key)
    if cached is not None:
        return cached

    index = {}
    try:
        result = subprocess.run(
            # --relative: paths relativos al VAULT y solo sus commits. Sin esto,
            # con el vault en un subdirectorio del repo, las rutas vienen con el
            # prefijo del subdir y ninguna coincide con un relpath del vault
            # (índice vacío → todas las fechas «desconocidas»).
            ["git", "-C", str(vault), "log", "--format=%aI", "--name-only",
             "--relative"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {}
    except Exception:
        return {}

    current_date = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if _ISO_DATE_RE.match(line):
            current_date = line
            continue
        if current_date is None:
            continue
        # Línea de archivo (puede venir con prefijo de rename "old => new")
        if " => " in line and line.endswith(".md"):
            line = line.split(" => ")[-1].strip()
        if not line.endswith(".md"):
            continue
        entry = index.setdefault(line, {"first": current_date, "last": current_date})
        # git log va de más nuevo a más viejo: la primera fecha vista es la
        # última edición ("last"); las fechas siguientes son más antiguas y
        # van reescribiendo "first" hasta quedar con la creación.
        entry["first"] = current_date

    _DATES_CACHE[cache_key] = index
    return index


def build_uncommitted_set(vault):
    """Builds the set of relpaths with uncommitted changes (index or worktree).

    A file that is being committed right now cannot have its timestamp "in the
    future" relative to its last commit: that commit does not exist yet. Any
    check that compares a frontmatter timestamp against ``git log`` has to
    exempt these files, or editing a concept that was last committed more than
    a day ago becomes impossible.

    One subprocess: ``git status --porcelain``.

    Args:
        vault: Path to the git repository root.

    Returns:
        set[str] — relpaths as git reports them. Empty set if the vault is not
        a git repo or git is unavailable.
    """
    cache_key = str(vault)
    cached = _UNCOMMITTED_CACHE.get(cache_key)
    if cached is not None:
        return cached

    paths = set()
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return set()
    except Exception:
        return set()

    prefix = vault_prefix(vault)
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        # Renombrados vienen como "old -> new": nos interesa el destino.
        if " -> " in path:
            path = path.split(" -> ")[-1].strip()
        path = path.strip('"')
        if prefix:
            # `git status` habla relativo a la raíz del repo: fuera del vault no
            # nos sirve, dentro hay que recortar el prefijo.
            if not path.startswith(prefix):
                continue
            path = path[len(prefix):]
        paths.add(path)

    _UNCOMMITTED_CACHE[cache_key] = paths
    return paths


def first_last_for(relpath, index):
    """Returns (first_date, last_date) datetimes for a relpath, or (None, None)."""
    entry = index.get(relpath)
    if not entry:
        return (None, None)
    return _parse_iso(entry.get("first")), _parse_iso(entry.get("last"))


def vault_prefix(vault):
    """Prefijo del vault dentro del repo git ('' si el vault ES la raíz).

    ``git show HEAD:<path>``, ``git status`` y ``git log --name-only`` hablan en
    paths relativos a la RAÍZ del repo. Con el vault en un subdirectorio
    (``monorepo/vault/``) un relpath del vault no resuelve: ``HEAD:planes/p.md``
    no existe y las comparaciones contra HEAD se vuelven «archivo nuevo».
    """
    key = str(vault)
    if key in _PREFIX_CACHE:
        return _PREFIX_CACHE[key]
    prefix = ""
    try:
        result = subprocess.run(
            ["git", "-C", key, "rev-parse", "--show-prefix"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            prefix = result.stdout.strip()
    except Exception:
        prefix = ""
    _PREFIX_CACHE[key] = prefix
    return prefix


def head_blob(vault, rel):
    """Contenido de ``rel`` (relpath del vault) en HEAD, o None si no está."""
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "show", f"HEAD:{vault_prefix(vault)}{rel}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    return result.stdout if result.returncode == 0 else None


def staged_paths(vault, pattern="*.md", diff_filter="ACMR"):
    """Archivos staged, con rutas relativas al VAULT (no a la raíz del repo).
    ``git diff --name-only`` emite rutas relativas a la raíz del repo git. Si el
    vault vive en un subdirectorio (``monorepo/vault/``), el llamador hacía
    ``vault / f`` → no existe → descarte silencioso: ``validate`` (modo staged,
    el que corre el hook) reportaba «0 file(s) valid» y salía 0, dejando pasar
    cualquier commit. ``--relative`` hace que git emita las rutas relativas al
    cwd, y con ``git -C vault`` el cwd es el vault; de paso excluye los staged
    que quedan fuera del vault.

    Returns:
        (paths, error): ``paths`` son str relativos al vault; ``error`` es el
        stderr de git cuando el comando falla (p. ej. el vault no es un repo),
        o None si todo fue bien.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "diff", "--cached", "--name-only",
             "--relative", f"--diff-filter={diff_filter}", pattern],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return [], str(e)
    if result.returncode != 0:
        err = (result.stderr or "").strip().splitlines()
        return [], err[0] if err else f"git exit {result.returncode}"
    return [f.strip() for f in result.stdout.split("\n") if f.strip()], None
