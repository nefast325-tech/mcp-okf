"""Política de acceso al vault: modo solo-lectura y scope de subárbol.

Dos interruptores independientes, configurables por variable de entorno (o por
argumento del CLI):

- ``OKF_READONLY=true``   → los comandos de escritura se rechazan.
- ``OKF_SCOPE=<prefijo>`` → toda resolución de conceptos, búsqueda, grafo y
  descubrimiento de archivos queda acotada a ese subárbol del vault.

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
from pathlib import Path

READONLY_ENV = "OKF_READONLY"
SCOPE_ENV = "OKF_SCOPE"

# Comandos que escriben en el vault. Se bloquean con OKF_READONLY=true.
# `touch` NO está: el incremento manual está deprecado y solo lee estadísticas.
WRITE_COMMANDS = frozenset({
    "new", "edit", "index", "canvas", "dashboard", "dashboard-snapshot",
    "migrate-reads", "migrate",
})

_TRUTHY = {"1", "true", "yes", "on", "si", "sí"}

# Scope activo del proceso (lo fija main() desde --scope / $OKF_SCOPE).
_current_scope = None


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


def set_scope(prefix):
    """Fija el scope activo del proceso. Devuelve el prefijo normalizado."""
    global _current_scope
    _current_scope = normalize_scope(prefix)
    return _current_scope


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
    return (vault / sc) if sc else vault
