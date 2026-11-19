"""Índice local de notas: lo ya leído de cada archivo, para no releer el vault en cada llamada.

Medido el 19-nov-2026 con un vault de 10.131 notas: cada `search` y cada
`traverse` releía y parseaba el frontmatter de todas las notas, y `traverse`
lo hacía dos veces (grafo + mapa de vigentes). Una llamada tardaba de 4 a 13 s
y el 85 % de ese tiempo era el parser de YAML.

El índice guarda, por archivo, el frontmatter ya parseado, los enlaces del
cuerpo y el cuerpo, y los reusa mientras el archivo no cambie (mismo
`mtime_ns` y mismo tamaño). Cada llamada mira la fecha y el tamaño de todos
los archivos —eso es barato— y vuelve a leer solo los que cambiaron.

Vive en `<vault>/.okf/state/indice.sqlite`: local, sin versionar, desechable
(borrarlo solo cuesta una reconstrucción). Si no se puede abrir o escribir
(vault de solo lectura, disco lleno, archivo dañado, otro proceso escribiendo)
se trabaja como antes, leyendo los archivos. `OKF_INDICE=0` lo apaga.
"""

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

ESQUEMA = 1
RUTA = Path(".okf") / "state" / "indice.sqlite"

# Por proceso: {vault: {rel: (mtime_ns, tamaño, nota)}}. Una llamada que
# construye el grafo y después el mapa de vigentes lee cada archivo una vez.
_MEMO = {}


def activo():
    return os.environ.get("OKF_INDICE", "1").strip().lower() not in ("0", "no", "false", "off")


# ── Frontmatter a JSON y de vuelta, sin perder fechas ──

def _a_json(o):
    if isinstance(o, datetime):
        return {"$momento": o.isoformat()}
    if isinstance(o, date):
        return {"$fecha": o.isoformat()}
    raise TypeError(type(o).__name__)


def _de_json(d):
    if len(d) == 1:
        if "$momento" in d:
            return datetime.fromisoformat(d["$momento"])
        if "$fecha" in d:
            return date.fromisoformat(d["$fecha"])
    return d


def _codificar(fm):
    """JSON del frontmatter, o None si no vuelve idéntico (claves que no son
    texto, tipos raros): esa nota se relee del archivo cada vez."""
    if fm is None:
        return "null"
    try:
        texto = json.dumps(fm, default=_a_json, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if json.loads(texto, object_hook=_de_json) != fm:
        return None
    return texto


# ── Lectura de un archivo (lo mismo que hacían search y build_graph) ──

def leer(path):
    """{fm, enlaces, cuerpo, cuerpo_inicio} de un archivo; None si no se puede leer."""
    from cli.frontmatter import parse_frontmatter
    from cli.wikilinks import extract_links_from_text
    try:
        texto = Path(path).read_text(encoding="utf-8")
    except Exception:
        return None
    fm, fm_texto = parse_frontmatter(texto)
    if fm:
        from cli.commands.search import _body_after_frontmatter, _body_first_line
        cuerpo = _body_after_frontmatter(texto, fm_texto)
        inicio = _body_first_line(texto, fm_texto)
    else:
        cuerpo, inicio = "", 1
    return {"fm": fm if fm else None, "enlaces": extract_links_from_text(texto),
            "cuerpo": cuerpo, "cuerpo_inicio": inicio}


# ── Base local ──

def _abrir(vault):
    ruta = Path(vault) / RUTA
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta), timeout=2)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)")
    fila = con.execute("SELECT valor FROM meta WHERE clave='esquema'").fetchone()
    if fila is None or fila[0] != str(ESQUEMA):
        con.execute("DROP TABLE IF EXISTS notas")
        con.execute("CREATE TABLE notas (ruta TEXT PRIMARY KEY, mtime_ns INTEGER, tam INTEGER, "
                    "fm TEXT, enlaces TEXT, cuerpo TEXT, cuerpo_inicio INTEGER)")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('esquema', ?)", (str(ESQUEMA),))
        con.commit()
    return con


def rel_de(f, vault):
    """Ruta relativa al vault como texto, sin `Path.relative_to` (caro por miles)."""
    s, raiz = str(f), str(vault)
    if s.startswith(raiz + os.sep):
        return s[len(raiz) + 1:].replace(os.sep, "/")
    return str(Path(f).relative_to(vault))


def _sello(archivos, vault):
    sellos = {}
    for f in archivos:
        try:
            st = os.stat(f)
        except OSError:
            continue
        sellos[rel_de(f, vault)] = (f, st.st_mtime_ns, st.st_size)
    return sellos


def notas(vault, archivos=None, cuerpo=True):
    """{rel: {fm, enlaces, cuerpo, cuerpo_inicio}} de `archivos` (por defecto
    `find_md_files(vault)`), al día con lo que hay en disco.

    Con `cuerpo=False` no se trae el cuerpo de la base (el grafo no lo usa) y
    la clave `cuerpo` puede faltar.
    """
    vault = Path(vault)
    if archivos is None:
        from cli.vault import find_md_files
        archivos = find_md_files(vault)
    sellos = _sello(archivos, vault)
    memo = _MEMO.setdefault(str(vault), {})

    # Lo que el proceso ya tiene al día no se vuelve a pedir.
    faltan = {rel for rel, (_, m, t) in sellos.items()
              if rel not in memo or memo[rel][:2] != (m, t)
              or (cuerpo and "cuerpo" not in memo[rel][2])}
    if faltan:
        _completar(vault, sellos, faltan, memo, cuerpo)
    return {rel: memo[rel][2] for rel in sellos if rel in memo and memo[rel][2] is not None}


def _completar(vault, sellos, faltan, memo, cuerpo):
    con = None
    if activo():
        try:
            con = _abrir(vault)
        except (OSError, sqlite3.Error):
            con = None
    guardadas = {}
    if con is not None:
        try:
            cols = "ruta, mtime_ns, tam, fm, enlaces, cuerpo_inicio" + (", cuerpo" if cuerpo else "")
            for fila in con.execute(f"SELECT {cols} FROM notas"):
                if fila[0] in faltan:
                    guardadas[fila[0]] = fila
        except sqlite3.Error:
            guardadas = {}

    nuevas = []
    for rel in faltan:
        f, m, t = sellos[rel]
        fila = guardadas.get(rel)
        nota = None
        if fila is not None and (fila[1], fila[2]) == (m, t) and fila[3] is not None:
            nota = {"fm": json.loads(fila[3], object_hook=_de_json), "enlaces": json.loads(fila[4]),
                    "cuerpo_inicio": fila[5]}
            if cuerpo:
                nota["cuerpo"] = fila[6]
        else:
            nota = leer(f)
            if nota is not None:
                nuevas.append((rel, m, t, _codificar(nota["fm"]), json.dumps(nota["enlaces"], ensure_ascii=False),
                               nota["cuerpo"], nota["cuerpo_inicio"]))
        memo[rel] = (m, t, nota)

    if con is None:
        return
    try:
        if nuevas:
            con.executemany("INSERT OR REPLACE INTO notas VALUES (?,?,?,?,?,?,?)", nuevas)
        # En una pasada completa, las filas de archivos que ya no existen se
        # van (no las que quedan fuera del listado por alcance o exclusión:
        # siguen en disco y otra instancia las puede pedir).
        idas = []
        if len(sellos) == len(faltan):
            idas = [(r,) for (r,) in con.execute("SELECT ruta FROM notas")
                    if r not in sellos and not (Path(vault) / r).exists()]
        if idas:
            con.executemany("DELETE FROM notas WHERE ruta=?", idas)
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


def frontmatter(vault, rel):
    """Frontmatter de UNA nota: del memo del proceso si está al día, si no del archivo."""
    rel = str(rel)
    path = os.path.join(str(vault), rel)
    entrada = _MEMO.get(str(Path(vault)), {}).get(rel)
    if entrada is not None:
        try:
            st = os.stat(path)
            if (st.st_mtime_ns, st.st_size) == entrada[:2] and entrada[2] is not None:
                return entrada[2]["fm"]
        except OSError:
            pass
    nota = leer(path)
    return nota["fm"] if nota else None
