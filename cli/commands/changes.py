"""Command changes — qué cambió en el vault, leído del historial de git.

El vault guarda su historia en git, pero ninguna orden contestaba «¿qué cambió
desde el martes?», «¿cuándo se canceló tal plan?» o «¿qué decisiones se
reemplazaron este mes?»: había que deducirlo del cuerpo de las notas. `changes`
recorre los commits y, por cada nota tocada, compara su cabecera antes y
después:

  nueva       la nota aparece (con su tipo y su estado inicial)
  estado      el `status` cambió: «aceptada → reemplazada»
  reemplazo   la nota ganó una arista `corrige`: desde ese commit reemplaza a otra
  aristas     cambió alguna otra arista de `links:`
  cabecera    cambió el título o la descripción
  movida      el archivo cambió de ruta
  borrada     el archivo salió del vault
  texto       solo cambió el cuerpo (no se lista salvo que se pida: son la mayoría)

Es de solo lectura y no usa nada fuera de git: si el vault no es un repositorio
(o es una copia sin historia) lo dice y sale con error.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from cli.access import under_scope
from cli.frontmatter import extract_typed_links, parse_frontmatter
from cli.vault import _get_excludes

TIPOS = ("nueva", "estado", "reemplazo", "aristas", "cabecera", "movida", "borrada", "texto")
LIMITE = 50
SEP = "\x01"


def _git(vault, *args, entrada=None):
    return subprocess.run(["git", "-C", str(vault), *args], input=entrada, capture_output=True)


def _es_concepto(ruta, exclude_files, exclude_dirs):
    partes = tuple(ruta.split("/"))
    if not ruta.endswith(".md") or partes[-1] in exclude_files:
        return False
    if any(p in exclude_dirs for p in partes):
        return False
    return under_scope(partes)


def _commits(vault, since, until):
    """Commits (del más reciente al más viejo) con las notas que tocó cada uno."""
    orden = ["log", "--first-parent", "-m", "-M", "--name-status", "--diff-filter=AMDR",
             f"--format={SEP}%H%x09%aI%x09%an%x09%s"]
    if since:
        orden.append(f"--since={since}")
    if until:
        orden.append(f"--until={until}")
    r = _git(vault, *orden, "--", "*.md")
    if r.returncode != 0:
        return None, r.stderr.decode("utf-8", "replace").strip()
    commits = []
    for bloque in r.stdout.decode("utf-8", "replace").split(SEP)[1:]:
        lineas = [l for l in bloque.split("\n") if l.strip()]
        sha, fecha, autor, asunto = (lineas[0].split("\t", 3) + ["", "", ""])[:4]
        archivos = []
        for l in lineas[1:]:
            partes = l.split("\t")
            letra = partes[0][:1]
            if letra == "R" and len(partes) >= 3:
                archivos.append(("R", partes[2], partes[1]))
            elif len(partes) >= 2:
                archivos.append((letra, partes[1], None))
        commits.append({"sha": sha, "fecha": fecha, "autor": autor, "asunto": asunto, "archivos": archivos})
    return commits, ""


def _contenidos(vault, pedidos):
    """{«sha:ruta»: texto} con un solo `git cat-file --batch` (None si no existe)."""
    if not pedidos:
        return {}
    r = _git(vault, "cat-file", "--batch", entrada=("\n".join(pedidos) + "\n").encode("utf-8"))
    datos, salida, i = {}, r.stdout, 0
    for pedido in pedidos:
        fin = salida.find(b"\n", i)
        if fin == -1:
            break
        cab = salida[i:fin].decode("utf-8", "replace")
        i = fin + 1
        if cab.endswith(" missing") or " " not in cab:
            datos[pedido] = None
            continue
        largo = int(cab.rsplit(" ", 1)[1])
        datos[pedido] = salida[i:i + largo].decode("utf-8", "replace")
        i += largo + 1
    return datos


def _ficha(texto):
    """(tipo, estado, título, descripción, aristas, cuerpo) de una versión de la nota."""
    if texto is None:
        return None
    try:
        fm, _ = parse_frontmatter(texto)
    except Exception:
        fm = None
    fm = fm if isinstance(fm, dict) else {}
    aristas = {(l["type"], _slug(str(l["target"]))) for l in extract_typed_links(None, fm=fm)}
    fin = texto.find("\n---", 3)
    return {"type": str(fm.get("type") or ""), "status": str(fm.get("status") or ""),
            "title": str(fm.get("title") or ""), "description": str(fm.get("description") or ""),
            "aristas": aristas, "cuerpo": texto[fin + 4:] if fin != -1 else texto}


def _dia_local(fecha_iso):
    """El día del commit en la hora del dominio (Colombia, UTC-5), como `review`."""
    try:
        return datetime.fromisoformat(fecha_iso).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d")
    except ValueError:
        return fecha_iso[:10]


def _slug(ruta):
    return ruta[:-3] if ruta.endswith(".md") else ruta


def recolectar(vault, config=None, since=None, until=None):
    """Lista de cambios, del más reciente al más viejo. (None, error) si no hay git."""
    if _git(vault, "rev-parse", "--is-inside-work-tree").stdout.strip() != b"true":
        return None, "este vault no es un repositorio de git: no hay historia que leer"
    commits, error = _commits(vault, since, until)
    if commits is None:
        return None, error or "git log falló"
    exclude_files, exclude_dirs = _get_excludes(config)
    pedidos = []
    for c in commits:
        c["archivos"] = [a for a in c["archivos"] if _es_concepto(a[1], exclude_files, exclude_dirs)]
        for letra, ruta, antes in c["archivos"]:
            if letra != "D":
                pedidos.append(f"{c['sha']}:{ruta}")
            if letra != "A":
                pedidos.append(f"{c['sha']}^:{antes or ruta}")
    textos = _contenidos(vault, pedidos)
    cambios = []
    for c in commits:
        dia = _dia_local(c["fecha"])
        base = {"date": dia, "ts": c["fecha"], "author": c["autor"], "commit": c["sha"][:7], "subject": c["asunto"]}
        for letra, ruta, antes in c["archivos"]:
            nueva = _ficha(textos.get(f"{c['sha']}:{ruta}")) if letra != "D" else None
            vieja = _ficha(textos.get(f"{c['sha']}^:{antes or ruta}")) if letra != "A" else None
            ref = nueva or vieja or {"type": "", "status": ""}
            fila = dict(base, note=_slug(ruta), type=ref["type"], status=ref["status"])

            def anotar(kind, detalle, **extra):
                cambios.append(dict(fila, kind=kind, detail=detalle, **extra))

            if letra == "A" or (vieja is None and nueva is not None):
                anotar("nueva", f"nueva ({nueva['type'] or 'sin tipo'}"
                                + (f", {nueva['status']}" if nueva["status"] else "") + ")")
                for tipo, destino in sorted(nueva["aristas"]):
                    if tipo == "corrige":
                        anotar("reemplazo", f"reemplaza a {destino}", replaces=destino)
                continue
            if letra == "D" or nueva is None:
                anotar("borrada", "borrada")
                continue
            hubo = False
            if letra == "R":
                anotar("movida", f"movida desde {_slug(antes)}")
                hubo = True
            if vieja["status"] != nueva["status"]:
                anotar("estado", f"estado: {vieja['status'] or '(sin estado)'} → {nueva['status'] or '(sin estado)'}",
                       **{"from": vieja["status"], "to": nueva["status"]})
                hubo = True
            mas, menos = nueva["aristas"] - vieja["aristas"], vieja["aristas"] - nueva["aristas"]
            for tipo, destino in sorted(mas):
                if tipo == "corrige":
                    anotar("reemplazo", f"reemplaza a {destino}", replaces=destino)
                    hubo = True
            otras = [f"+{t} {d}" for t, d in sorted(mas) if t != "corrige"] + [f"−{t} {d}" for t, d in sorted(menos)]
            if otras:
                anotar("aristas", "aristas: " + ", ".join(otras))
                hubo = True
            if vieja["title"] != nueva["title"] or vieja["description"] != nueva["description"]:
                anotar("cabecera", "cabecera: cambió " + " y ".join(
                    n for n, k in (("el título", "title"), ("la descripción", "description")) if vieja[k] != nueva[k]))
                hubo = True
            if not hubo and vieja["cuerpo"] != nueva["cuerpo"]:
                anotar("texto", "texto: cambió el cuerpo")
    return cambios, ""


def _valores(filtro):
    return {v.strip().lower() for v in str(filtro or "").split(",") if v.strip()}


def _fecha_valida(valor):
    try:
        datetime.fromisoformat(valor)
        return True
    except ValueError:
        return False


def run(args, vault, config=None):
    since, until = getattr(args, "since", None), getattr(args, "until", None)
    for nombre, valor in (("--since", since), ("--until", until)):
        if valor and not _fecha_valida(valor):
            print(f"Error: {nombre} pide una fecha ISO 8601 (2026-10-29), no «{valor}»", file=sys.stderr)
            return 1
    kinds = _valores(getattr(args, "kind", None))
    desconocidos = sorted(kinds - set(TIPOS))
    if desconocidos:
        print(f"Error: --kind desconocido: {', '.join(desconocidos)}. Válidos: {', '.join(TIPOS)}", file=sys.stderr)
        return 1
    # A git se le pide una ventana un día más ancha por cada lado y con hora explícita
    # (un `--since=2026-10-29` pelado lo lee como «ese día a la hora de ahora»); el corte
    # exacto se hace después, por el día del commit en la hora del dominio. Así
    # `--since`/`--until` son inclusivos y dicen lo mismo que la columna FECHA.
    def _ventana(valor, dias, hora):
        if not valor:
            return None
        return (datetime.fromisoformat(valor[:10]) + timedelta(days=dias)).strftime("%Y-%m-%d") + hora

    cambios, error = recolectar(vault, config, _ventana(since, -1, "T00:00:00"), _ventana(until, 1, "T23:59:59"))
    if cambios is None:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if since:
        cambios = [c for c in cambios if c["date"] >= since[:10]]
    if until:
        cambios = [c for c in cambios if c["date"] <= until[:10]]

    tipos, hacia = _valores(getattr(args, "filter_type", None)), _valores(getattr(args, "to_status", None))
    nota = _slug((getattr(args, "note", None) or "").strip())
    solo_texto = sum(1 for c in cambios if c["kind"] == "texto")
    if tipos:
        cambios = [c for c in cambios if c["type"].lower() in tipos]
    if nota:
        cambios = [c for c in cambios if c["note"] == nota or c["note"].rsplit("/", 1)[-1] == nota]
    if hacia:
        cambios = [c for c in cambios if c["kind"] == "estado" and c.get("to", "").lower() in hacia]
    if kinds:
        cambios = [c for c in cambios if c["kind"] in kinds]
    elif not nota:
        # Las ediciones de solo texto son la mayoría y no dicen qué pasó: se cuentan al pie.
        cambios = [c for c in cambios if c["kind"] != "texto"]

    total = len(cambios)
    limite = getattr(args, "limit", None)
    limite = LIMITE if limite is None else limite
    if limite and limite > 0:
        cambios = cambios[:limite]

    if getattr(args, "json", False):
        print(json.dumps(cambios, ensure_ascii=False, indent=2))
        return 0
    if not cambios:
        print("(sin cambios con ese filtro)")
        return 0
    ancho = max(len(c["note"]) for c in cambios)
    print(f"{'FECHA':<10}  {'NOTA':<{ancho}}  CAMBIO  ·  AUTOR")
    for c in cambios:
        print(f"{c['date']}  {c['note']:<{ancho}}  {c['detail']}  ·  {c['author']}")
    notas = len({c["note"] for c in cambios})
    print(f"\n{len(cambios)} cambio(s) en {notas} nota(s), del {cambios[-1]['date']} al {cambios[0]['date']}"
          + (f" (los {len(cambios)} más recientes de {total}: --limit 0 para todos)" if total > len(cambios) else ""))
    if not kinds and not nota and solo_texto and not tipos and not hacia:
        print(f"({solo_texto} edición(es) de solo texto no se listan: --kind texto para verlas)")
    return 0
