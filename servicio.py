"""Modo servicio: el MCP de OKF por HTTP, con una llave por persona y commit automático.

Para que personas que no usan git (y agentes en otras máquinas) lean y escriban
el vault al instante, desde cualquier lugar, sin clon ni commits: un solo
servidor tiene el vault, todos le hablan por HTTP y él escribe con el motor de
siempre (validación, revisión base, tachado de secretos) y guarda el historial
en git por su cuenta.

    python server.py --vault <ruta> --http 127.0.0.1:8790 --llaves <archivo> [--autocommit]
    python server.py --llaves <archivo> --crear-llave "Ana" --correo ana@x --permiso escritura

- Llaves: un archivo JSON (modo 600) con el sha256 de cada llave y a quién
  pertenece: {"<sha256>": {"nombre", "correo", "permiso": "lectura"|"escritura"}}.
  La llave en claro se muestra una sola vez, al crearla; no se guarda.
- Sin llave válida, el servidor responde 401 antes de tocar el MCP.
- Con permiso «lectura», las herramientas que escriben responden que no.
- Las lecturas corren en paralelo; las escrituras, de a una (el commit de cada
  una queda con su autor).
- --autocommit: tras cada escritura que cambió algo, `git add -A` + commit con
  el autor de la llave. Subir a GitHub es aparte (política del despliegue).
"""

import asyncio
import contextvars
import functools
import hashlib
import json
import os
import secrets
import subprocess
import threading
from pathlib import Path

PERMISOS = ("lectura", "escritura")

# Quién hace la llamada en curso (lo pone el envoltorio de cada herramienta).
USUARIO = contextvars.ContextVar("okf_usuario", default=None)
# Una escritura a la vez: el commit queda con el autor correcto.
ESCRITURA = threading.Lock()


# ── Llaves ──────────────────────────────────────────────────────────────────

def _huella(llave: str) -> str:
    return hashlib.sha256(llave.encode("utf-8")).hexdigest()


class Llaves:
    def __init__(self, ruta):
        self.ruta = Path(ruta).expanduser()
        self._datos = {}
        self._mtime = None

    def _cargar(self):
        try:
            mtime = self.ruta.stat().st_mtime_ns
        except OSError:
            self._datos, self._mtime = {}, None
            return
        if mtime != self._mtime:  # se relee si cambió: alta o baja de llaves sin reiniciar
            try:
                self._datos = json.loads(self.ruta.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                self._datos = {}
            self._mtime = mtime

    def buscar(self, encabezado: str):
        """El usuario de un encabezado `Authorization: Bearer <llave>`, o None."""
        if not encabezado or not encabezado.lower().startswith("bearer "):
            return None
        llave = encabezado[7:].strip()
        if not llave:
            return None
        self._cargar()
        datos = self._datos.get(_huella(llave))
        if not isinstance(datos, dict) or datos.get("permiso") not in PERMISOS:
            return None
        return {"nombre": str(datos.get("nombre", "")), "correo": str(datos.get("correo", "")),
                "permiso": datos["permiso"]}

    def crear(self, nombre: str, correo: str, permiso: str) -> str:
        if permiso not in PERMISOS:
            raise ValueError(f"permiso debe ser {' o '.join(PERMISOS)}")
        self._cargar()
        llave = "okf_" + secrets.token_urlsafe(32)
        self._datos[_huella(llave)] = {"nombre": nombre, "correo": correo, "permiso": permiso}
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._datos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.ruta)
        return llave


# ── Guardia HTTP ────────────────────────────────────────────────────────────

class Guardia:
    """ASGI: sin llave válida, 401; con llave, deja el usuario en scope["state"]."""

    def __init__(self, app, llaves: Llaves):
        self.app = app
        self.llaves = llaves

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            encabezados = {k.lower(): v for k, v in scope.get("headers", [])}
            usuario = self.llaves.buscar(encabezados.get(b"authorization", b"").decode("latin-1"))
            if usuario is None:
                cuerpo = json.dumps({"error": "falta una llave válida (Authorization: Bearer okf_…)"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(cuerpo)).encode())]})
                await send({"type": "http.response.body", "body": cuerpo})
                return
            scope.setdefault("state", {})["okf_usuario"] = usuario
        await self.app(scope, receive, send)


# ── Envoltorio de cada herramienta ──────────────────────────────────────────

def _usuario_de(mcp):
    try:
        pedido = mcp.get_context().request_context.request
    except Exception:
        return None
    estado = getattr(pedido, "state", None)
    return getattr(estado, "okf_usuario", None) if estado is not None else None


def envolver(fn, escribe: bool, mcp):
    """La herramienta pasa a ser asíncrona: comprueba el permiso de la llave y corre
    en un hilo (el bucle del servidor no se bloquea y las lecturas van en paralelo)."""

    @functools.wraps(fn)
    async def herramienta(**kwargs):
        usuario = _usuario_de(mcp)
        if usuario is None:
            return "[sin llave] El servicio no identificó a quién pertenece la llave."
        if escribe and usuario["permiso"] != "escritura":
            return (f"[sin permiso] La llave de {usuario['nombre']} es de solo lectura: "
                    f"`{fn.__name__}` escribe en el vault.")
        marca = USUARIO.set(usuario)  # to_thread copia el contexto: el hilo ve al usuario
        try:
            return await asyncio.to_thread(functools.partial(fn, **kwargs))
        finally:
            USUARIO.reset(marca)

    return herramienta


# ── Historial en git ────────────────────────────────────────────────────────

def _git(vault, *args):
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, timeout=30)


def confirmar(vault, usuario, herramienta: str, args: list) -> str:
    """Commit de lo que cambió, con el autor de la llave. Devuelve el hash corto o ""."""
    if not (Path(vault) / ".git").exists():
        return ""
    if not _git(vault, "status", "--porcelain").stdout.strip():
        return ""
    _git(vault, "add", "-A")
    blanco = ""
    if args and args[0] == "new" and "--title" in args[:-1]:
        blanco = args[args.index("--title") + 1]
    elif len(args) > 1 and args[0] in ("edit", "touch"):
        blanco = str(args[1])
        for raiz in (str(vault), str(Path(vault).resolve())):
            if blanco.startswith(raiz + os.sep):
                blanco = blanco[len(raiz) + 1:]
        blanco = blanco[:-3] if blanco.endswith(".md") else blanco
    nombre = (usuario or {}).get("nombre") or "servicio"
    correo = (usuario or {}).get("correo") or "servicio@okf.local"
    mensaje = f"{herramienta.replace('okf_', '')}: {str(blanco)[:80]} (por {nombre}, servicio OKF)"
    r = _git(vault, "-c", "user.name=Servicio OKF", "-c", "user.email=servicio@okf.local",
             "commit", "-q", "-m", mensaje, "--author", f"{nombre} <{correo}>")
    if r.returncode != 0:
        return ""
    return _git(vault, "rev-parse", "--short", "HEAD").stdout.strip()


# ── Arranque ────────────────────────────────────────────────────────────────

def correr(mcp, direccion: str, llaves: Llaves, hosts=None):
    """Sirve el MCP por HTTP (streamable-http, ruta /mcp) detrás de la guardia."""
    import uvicorn
    host, _, puerto = direccion.rpartition(":")
    host = host or "127.0.0.1"
    mcp.settings.host, mcp.settings.port = host, int(puerto)
    if hosts:  # dominio público detrás de un proxy (protección contra DNS rebinding)
        ts = mcp.settings.transport_security
        ts.allowed_hosts = list(ts.allowed_hosts) + [f"{h}:*" if ":" not in h else h for h in hosts] + list(hosts)
        ts.allowed_origins = list(ts.allowed_origins) + [f"https://{h}" for h in hosts]
    app = Guardia(mcp.streamable_http_app(), llaves)
    uvicorn.run(app, host=host, port=int(puerto), log_level="warning")
