"""Política de acceso al vault: modo solo-lectura y scope de subárbol.

Dos interruptores independientes, configurables por variable de entorno (o por
argumento del CLI):

- ``OKF_READONLY=true``   → los comandos de escritura se rechazan.
- ``OKF_SCOPE=<prefijo>`` → toda resolución de conceptos, búsqueda, grafo y
  descubrimiento de archivos queda acotada a ese subárbol del vault.
- ``OKF_SCOPE=<campo>=<valor>`` (fase 5) → la frontera es un CAMPO del
  frontmatter, no una ruta: solo se ven las notas cuyo ``<campo>`` (texto o
  lista) contiene ``<valor>``. Una nota sin el campo queda fuera (fail-closed),
  y también los índices. Es lo que acota una instancia a una iniciativa
  (``--scope iniciativa=caribe-tropical``) sin mover archivos de carpeta.

Motivación (2026-09-11): el agente de servicio al cliente de la cartera
inmobiliaria (perfil `atencion`, expuesto por webchat y WhatsApp) es un agente
PÚBLICO. Necesita leer una carpeta publicada del vault (fichas de proyecto,
portafolio, contacto) sin poder ver —ni enumerar, ni traversar, ni buscar— el
resto del vault (decisiones, insights, sesiones, personas, research).

El enforcement del scope vive en el CLI (``find_md_files`` + resolución de
slugs y wikilinks), no en el server MCP: el server solo propaga el prefijo.
Así vale igual para invocaciones directas del CLI y para el hook pre-commit, y
no se puede saltear agregando una tool nueva al server.
"""

import os
import re
from pathlib import Path

READONLY_ENV = "OKF_READONLY"
SCOPE_ENV = "OKF_SCOPE"

# Comandos que escriben en el vault. Se bloquean con OKF_READONLY=true.
# `touch` NO está: el incremento manual está deprecado y solo lee estadísticas.
# ASIMETRIA INTENCIONAL: 
# - En server.py: `touch` está en _WRITE_TOOLS (registrado solo si !readonly)
# - En access.py: `touch` NO está en WRITE_COMMANDS (siempre permitido en CLI)
# Esta diferencia es intencional: el servidor MCP puede restringir touch via readonly,
# pero el CLI lo deja disponible para diagnósticos rápidos (no altera contenido).
WRITE_COMMANDS = frozenset({
    "new", "edit", "index", "canvas", "dashboard-snapshot",
    "migrate-reads", "migrate",
})

_TRUTHY = {"1", "true", "yes", "on", "si", "sí"}

# Scope activo del proceso (lo fija main() desde --scope / $OKF_SCOPE).
_current_scope = None
# Scope por campo: (campo, valor), el vault contra el que se evalúa y el set
# de rutas permitidas, construido una vez por proceso.
_field_scope = None
_scope_vault = None
_field_allowed = None

_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _truthy(value):
    return str(value or "").strip().lower() in _TRUTHY


def is_readonly(env=None):
    """True si el proceso corre en modo solo-lectura."""
    env = os.environ if env is None else env
    return _truthy(env.get(READONLY_ENV))


def normalize_scope(prefix):
    """Normaliza el prefijo de scope a 'a/b' (sin barras extremas).

    Lanza ValueError si el prefijo intenta escapar del vault con '..' — un
    control de seguridad que falla en silencio (degradando a "vault completo")
    sería peor que no tenerlo.
    """
    if prefix is None:
        return None
    raw = str(prefix).strip().replace("\\", "/")
    if raw in ("", "/", "."):
        return None
    if "=" in raw:
        field, _, value = raw.partition("=")
        field, value = field.strip(), value.strip()
        if not _FIELD_NAME_RE.match(field) or not value or "/" in value or ".." in value:
            raise ValueError(
                f"{SCOPE_ENV} inválido: '{prefix}' no es 'campo=valor' (campo: letras, "
                f"dígitos, '_' o '-'; valor: no vacío, sin '/' ni '..')")
        return f"{field}={value}"
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(
            f"{SCOPE_ENV} inválido: '{prefix}' intenta salir del vault ('..')"
        )
    return "/".join(parts) or None


def resolve_scope(cli_arg=None, env=None):
    """Scope efectivo: argumento CLI > $OKF_SCOPE > None (vault completo)."""
    if cli_arg is not None and str(cli_arg).strip() != "":
        return normalize_scope(cli_arg)
    env = os.environ if env is None else env
    return normalize_scope(env.get(SCOPE_ENV))


def set_scope(prefix, vault=None):
    """Fija el scope activo del proceso. Devuelve el prefijo normalizado.

    `vault` hace falta para un scope por campo (hay que leer el frontmatter);
    sin él, un scope por campo no deja ver nada (fail-closed).
    """
    global _current_scope, _field_scope, _scope_vault, _field_allowed
    _current_scope = normalize_scope(prefix)
    _field_scope = tuple(_current_scope.split("=", 1)) if is_field_scope(_current_scope) else None
    _scope_vault = Path(vault) if vault is not None else None
    _field_allowed = None
    return _current_scope


def is_field_scope(scope=None):
    """True si el scope (o el activo) es de la forma campo=valor."""
    sc = _current_scope if scope is None else scope
    return bool(sc) and "=" in str(sc)


def _field_matches(fm, field, value):
    raw = (fm or {}).get(field)
    if raw is None:
        return False
    items = raw if isinstance(raw, list) else [raw]
    return any(str(i).strip() == value for i in items)


def _allowed_paths():
    """Rutas (relativas, con '/') visibles bajo el scope por campo activo.

    Se recorre el vault UNA vez por proceso, sin pasar por find_md_files (que
    consulta este mismo scope). Las exclusiones son las del vault
    (cli.vault.EXCLUDE_*, que apply_config pisa desde la config). Sin vault
    conocido el set queda vacío: nada se ve.
    """
    global _field_allowed
    if _field_allowed is not None:
        return _field_allowed
    allowed = set()
    if _field_scope and _scope_vault is not None:
        from cli.frontmatter import parse_frontmatter
        from cli.vault import EXCLUDE_DIRS, EXCLUDE_FILES
        field, value = _field_scope
        for md in _scope_vault.rglob("*.md"):
            parts = md.relative_to(_scope_vault).parts
            if md.name in EXCLUDE_FILES or any(p in EXCLUDE_DIRS for p in parts):
                continue
            try:
                fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            except Exception:
                continue
            if fm and _field_matches(fm, field, value):
                allowed.add("/".join(parts))
    _field_allowed = allowed
    return allowed


def get_scope():
    """Scope activo, o None si el proceso ve el vault completo."""
    return _current_scope


def under_scope(relpath, scope=None):
    """True si un relpath (str o partes) cae dentro del scope.

    ``scope=None`` significa "el scope activo del proceso". Sin scope activo
    todo está dentro (comportamiento histórico del vault completo).
    """
    sc = _current_scope if scope is None else scope
    if not sc:
        return True
    if isinstance(relpath, (tuple, list, set)):
        rel = "/".join(str(p) for p in relpath)
    else:
        rel = str(relpath)
    rel = rel.replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if is_field_scope(sc):
        if sc != _current_scope:
            # Un scope por campo distinto del activo no tiene set construido:
            # fail-closed.
            return False
        if not rel.endswith(".md"):
            rel += ".md"
        return rel in _allowed_paths()
    return rel == sc or rel.startswith(sc + "/")


def in_scope_file(path, vault, scope=None):
    """True si un archivo (Path) del vault cae dentro del scope activo."""
    try:
        rel = str(Path(path).resolve().relative_to(Path(vault).resolve()))
    except ValueError:
        return False
    return under_scope(rel, scope)


def scope_root(vault, scope=None):
    """Directorio raíz de trabajo: <vault>/<scope>, o el vault si no hay scope."""
    sc = _current_scope if scope is None else scope
    vault = Path(vault)
    if not sc or is_field_scope(sc):
        # Un scope por campo no tiene carpeta propia: el índice de la raíz
        # no es visible (in_scope_file lo rechaza), así que no hay atajo.
        return vault
    return vault / sc
