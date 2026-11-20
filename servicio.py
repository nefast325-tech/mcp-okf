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
- --autocommit: versiona solo notas/canvas producidos con el árbol limpio;
  los fallos del historial son explícitos. Subir a GitHub es aparte.
"""

import asyncio
import contextvars
import functools
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path

PERMISOS = ("lectura", "escritura")

# Quién hace la llamada en curso (lo pone el envoltorio de cada herramienta).
USUARIO = contextvars.ContextVar("okf_usuario", default=None)
SESION = contextvars.ContextVar("okf_sesion_http", default="")
# Una escritura a la vez: el commit queda con el autor correcto.
ESCRITURA = threading.Lock()


# ── Llaves ──────────────────────────────────────────────────────────────────

def _huella(llave: str) -> str:
    return hashlib.sha256(llave.encode("utf-8")).hexdigest()


class Llaves:
    def __init__(self, ruta):
        self.ruta = Path(ruta).expanduser()

    def _cargar(self, *, estricto=False):
        # Leer cada pedido: mtime no demuestra identidad ni contenido. Una
        # revocación por reemplazo atómico puede conservar el mtime anterior.
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
            if not isinstance(datos, dict):
                raise ValueError("el archivo de llaves debe ser un objeto JSON")
            return datos
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            if estricto:
                raise ValueError("no se puede leer el archivo de llaves; no se modificó") from exc
            return {}

    def buscar(self, encabezado: str):
        """El usuario de un encabezado `Authorization: Bearer <llave>`, o None."""
        if not encabezado or not encabezado.lower().startswith("bearer "):
            return None
        llave = encabezado[7:].strip()
        if not llave:
            return None
        datos = self._cargar().get(_huella(llave))
        if not isinstance(datos, dict) or datos.get("permiso") not in PERMISOS:
            return None
        return {"nombre": str(datos.get("nombre", "")), "correo": str(datos.get("correo", "")),
                "permiso": datos["permiso"]}

    def crear(self, nombre: str, correo: str, permiso: str) -> str:
        if permiso not in PERMISOS:
            raise ValueError(f"permiso debe ser {' o '.join(PERMISOS)}")
        if any(c in nombre + correo for c in "\r\n<>"):
            raise ValueError("nombre/correo no pueden contener saltos de línea ni < >")
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        # No tmp predecible, sin ventana de permisos 644 y sin altas perdidas
        # cuando dos administradores crean llaves al mismo tiempo.
        lock = self.ruta.with_name(self.ruta.name + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as guardia:
            fcntl.flock(guardia.fileno(), fcntl.LOCK_EX)
            datos = self._cargar(estricto=True)
            llave = "okf_" + secrets.token_urlsafe(32)
            datos[_huella(llave)] = {"nombre": nombre, "correo": correo, "permiso": permiso}
            fd, tmp = tempfile.mkstemp(prefix=".llaves-", dir=self.ruta.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as archivo:
                    json.dump(datos, archivo, ensure_ascii=False, indent=1)
                    archivo.write("\n")
                    archivo.flush()
                    os.fsync(archivo.fileno())
                os.replace(tmp, self.ruta)
            finally:
                Path(tmp).unlink(missing_ok=True)
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
            scope["state"]["okf_sesion"] = "http:" + _huella(encabezados[b"authorization"].decode("latin-1")[7:].strip())[:16]
        await self.app(scope, receive, send)


# ── Envoltorio de cada herramienta ──────────────────────────────────────────

def _usuario_de(mcp):
    try:
        pedido = mcp.get_context().request_context.request
    except Exception:
        return None
    estado = getattr(pedido, "state", None)
    return getattr(estado, "okf_usuario", None) if estado is not None else None


def envolver(fn, escribe: bool, mcp, *, vault=None, autocommit=False):
    """La herramienta pasa a ser asíncrona: comprueba el permiso de la llave y corre
    en un hilo (el bucle del servidor no se bloquea y las lecturas van en paralelo)."""

    def ejecutar(kwargs, usuario):
        if not escribe:
            return fn(**kwargs)
        # Cubre la herramienta entera, incluso fuera de _run o con otro nombre.
        with ESCRITURA:
            # Una acción "fix" de canvas escribe aunque dry_run llegue True en
            # otra acción: la simulación se decide por la mutación efectiva.
            # canvas: validate no escribe sin --fix; layout respeta --dry-run;
            # generate escribe salvo --dry-run.
            if fn.__name__ == "canvas":
                accion = kwargs.get("action")
                simula = (kwargs.get("dry_run", False) if accion == "layout"
                          else accion == "validate" and not kwargs.get("fix", False))
            else:
                simula = bool(kwargs.get("dry_run", False))
            versiona = autocommit and fn.__name__ in {
                "new", "edit", "index", "canvas", "graph_suggest_edge_types",
            } and not simula
            if versiona:
                try:
                    validar_historial(vault)
                except RuntimeError as exc:
                    return f"[historial bloqueado] No se ejecutó la escritura: {exc}"
            output = fn(**kwargs)
            if versiona:
                args = [fn.__name__, str(kwargs.get("slug", kwargs.get("title", "")))]
                try:
                    sha = confirmar(vault, usuario, fn.__name__, args)
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    return (f"[error de historial] La herramienta se ejecutó, pero NO se confirmó "
                            f"el commit: {exc}. Revisá el árbol antes de reintentar.\n{output}")
                if sha:
                    output += f"\n📝 guardado en el historial (commit {sha})"
            return output

    @functools.wraps(fn)
    async def herramienta(**kwargs):
        usuario = _usuario_de(mcp)
        if usuario is None:
            return "[sin llave] El servicio no identificó a quién pertenece la llave."
        if escribe and usuario["permiso"] != "escritura":
            return (f"[sin permiso] La llave de {usuario['nombre']} es de solo lectura: "
                    f"`{fn.__name__}` escribe en el vault.")
        marca = USUARIO.set(usuario)  # to_thread copia el contexto: el hilo ve al usuario
        pedido = mcp.get_context().request_context.request
        sesion = SESION.set(getattr(pedido.state, "okf_sesion", ""))
        try:
            return await asyncio.to_thread(ejecutar, kwargs, usuario)
        finally:
            SESION.reset(sesion)
            USUARIO.reset(marca)

    return herramienta


# ── Historial en git ────────────────────────────────────────────────────────

def _git(vault, *args):
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, timeout=30)


def _git_ok(vault, *args):
    r = _git(vault, *args)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout or "git falló").strip())
    return r.stdout


def _cambios(vault):
    records = iter(_git_ok(vault, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0"))
    paths = []
    for record in records:
        if not record:
            continue
        status, path = record[:2], record[3:]
        if "R" in status or "C" in status:
            next(records, None)
            raise RuntimeError("hay renombres/copias pendientes; resolver fuera del servicio")
        if Path(path).parts[0] not in {".okf", ".obsidian"}:
            paths.append(path)
    return paths


def validar_historial(vault):
    root = _git_ok(vault, "rev-parse", "--show-toplevel").strip()
    if Path(root).resolve() != Path(vault).resolve():
        raise RuntimeError("el vault debe ser la raíz de su propio repositorio git")
    if _cambios(vault):
        raise RuntimeError("el repositorio tiene cambios previos sin confirmar; no se atribuirán a otra persona")


def confirmar(vault, usuario, herramienta: str, args: list) -> str:
    """Commit de lo que cambió, con el autor de la llave. Devuelve el hash corto o ""."""
    if not (Path(vault) / ".git").exists():
        return ""
    paths = _cambios(vault)
    if not paths:
        return ""
    if any(Path(p).suffix not in {".md", ".canvas"} or (Path(vault) / p).is_symlink() for p in paths):
        raise RuntimeError("hay cambios ajenos a notas/canvas; no se incluyeron en el historial")
    _git_ok(vault, "add", "-A", "--", *paths)
    blanco = ""
    if len(args) == 2 and args[0] == "new":
        blanco = args[1]
    elif args and args[0] == "new" and "--title" in args[:-1]:
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
    _git_ok(vault, "-c", "user.name=Servicio OKF", "-c", "user.email=servicio@okf.local",
            "commit", "--only", "-q", "-m", mensaje, "--author", f"{nombre} <{correo}>", "--", *paths)
    return _git_ok(vault, "rev-parse", "--short", "HEAD").strip()


# ── Arranque ────────────────────────────────────────────────────────────────

def correr(mcp, direccion: str, llaves: Llaves, hosts=None):
    """Sirve el MCP por HTTP (streamable-http, ruta /mcp) detrás de la guardia."""
    import uvicorn
    host, _, puerto = direccion.rpartition(":")
    host = host or "127.0.0.1"
    mcp.settings.host, mcp.settings.port = host, int(puerto)
    # La identidad es por pedido; no compartir sesiones stateful entre llaves.
    mcp.settings.stateless_http = True
    if hosts:  # dominio público detrás de un proxy (protección contra DNS rebinding)
        ts = mcp.settings.transport_security
        ts.allowed_hosts = list(ts.allowed_hosts) + [f"{h}:*" if ":" not in h else h for h in hosts] + list(hosts)
        ts.allowed_origins = list(ts.allowed_origins) + [f"https://{h}" for h in hosts]
    app = Guardia(mcp.streamable_http_app(), llaves)
    uvicorn.run(app, host=host, port=int(puerto), log_level="warning")
