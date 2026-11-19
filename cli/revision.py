"""Revisión de una nota y candado de escritura.

`edit` reemplaza el cuerpo y la lista de links enteros. Si dos agentes leen la
misma versión de una nota y cada uno escribe su cuerpo, gana el último y el
cambio del otro se pierde sin que nadie se entere. La revisión cierra ese hueco:

  - `read` imprime «🔖 rev: <12 hex>», la huella del archivo tal como está;
  - `edit --base-revision <rev>` solo escribe si la nota sigue en esa revisión;
    si cambió, lo dice y no toca nada: se relee y se reaplica;
  - el candado hace que comprobar y escribir sea un solo paso entre procesos
    (cada agente es un `okf-mcp` propio que lanza su CLI).

La revisión es la huella del contenido, no el `timestamp`: dos ediciones en el
mismo segundo tienen el mismo timestamp, y un cambio de status no lo renueva.
Leer no cambia la huella: los contadores de lectura viven fuera del archivo.
"""

import contextlib
import hashlib
import os
import shutil
from pathlib import Path

try:                                    # POSIX; el store de lecturas ya depende de él
    import fcntl
except ImportError:                     # pragma: no cover
    fcntl = None

MODOS = ("optional", "required", "off")


def revision_de(contenido) -> str:
    """Huella corta (12 hex de SHA-256) del contenido de una nota."""
    data = contenido.encode("utf-8") if isinstance(contenido, str) else contenido
    return hashlib.sha256(data).hexdigest()[:12]


def revision_de_archivo(filepath) -> str:
    return revision_de(Path(filepath).read_text(encoding="utf-8"))


def normalizar(base) -> str:
    """La revisión como la pasa un agente: con espacios, comillas o el prefijo «rev:»."""
    base = str(base or "").strip().strip("`'\"").strip()
    if base.lower().startswith("rev"):
        base = base[3:].lstrip(":= ").strip()
    return base.lower()


@contextlib.contextmanager
def candado(vault, filepath):
    """Candado exclusivo por nota, entre procesos, mientras dura una escritura.

    Vive en <vault>/.okf/state/locks/ (el mismo árbol local, sin versionar, que
    el store de lecturas). Se toma sobre un archivo aparte y no sobre la nota:
    la nota se reemplaza al escribir y un candado sobre ella se iría con el
    archivo viejo. Si el proceso muere, el sistema lo suelta solo.
    """
    if fcntl is None:                   # pragma: no cover
        yield
        return
    try:
        rel = str(Path(filepath).resolve().relative_to(Path(vault).resolve()))
    except ValueError:
        rel = str(filepath)
    carpeta = Path(vault) / ".okf" / "state" / "locks"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / (hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16] + ".lock")
    with open(ruta, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def escribir_atomico(filepath, contenido: str) -> None:
    """Escribe a un temporal en la misma carpeta y lo renombra encima: un lector
    nunca ve la nota a medio escribir."""
    filepath = Path(filepath)
    tmp = filepath.with_name(f".{filepath.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(contenido, encoding="utf-8")
        if filepath.exists():
            shutil.copymode(filepath, tmp)
        os.replace(tmp, filepath)
    finally:
        if tmp.exists():
            tmp.unlink()


def modo(config) -> str:
    """`writes.base_revision` del vault: optional (por defecto), required u off."""
    valor = str(getattr(config, "writes_base_revision", "optional")) if config is not None else "optional"
    return valor if valor in MODOS else "optional"
