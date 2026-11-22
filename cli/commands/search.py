"""Command search — Search concepts and pending tasks in the vault.

Versión vigente (fase 4, cli/vigente.py): las notas en su estado de reemplazo
(`superada`, `reemplazada`) no salen en los resultados salvo que se pida ese
status con --status o se pase --all-versions. Cada una tiene una vigente
(la que la `corrige`) y es esa la que responde la búsqueda.

Dónde coincidió el cuerpo (ronda 2, D3): con --body cada acierto de cuerpo
dice en qué línea está la primera mención (`line`, numerada como `read`),
qué dice esa línea (`snippet`, ≤ 160 chars), bajo qué encabezado cae
(`section`) y cuántas líneas del cuerpo mencionan el término (`matches`).
Sin eso el agente tenía que abrir cada nota para saber si la mención era
central o incidental. El cuerpo entero nunca se devuelve.
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from cli.vault import find_md_files, get_scope
from cli.frontmatter import parse_frontmatter, normalize_tags


def _tabla_plegado():
    import unicodedata
    tabla = {}
    for cp in range(0xC0, 0x250):
        ch = chr(cp)
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        if base != ch and base.isascii() and base:
            tabla[cp] = base.lower()
    # NFD latino usa marcas separadas. Quitarlas en translate evita normalizar
    # otra vez los cuerpos (habitualmente NFC) en cada búsqueda.
    tabla.update({cp: None for cp in range(0x300, 0x370)
                  if unicodedata.combining(chr(cp))})
    return tabla


_PLEGADO = _tabla_plegado()


def plegar(texto):
    """Minúsculas y sin tildes latinas (NFC/NFD): «Página» → «pagina», ñ → n."""
    return texto.lower().translate(_PLEGADO)


def _is_valid_frontmatter_key(key):
    return bool(key) and not str(key).startswith("#") and not str(key).startswith("- ")


def _extract_project(relative_parts):
    """Extracts the project/area from the file's relative path."""
    if len(relative_parts) <= 1:
        return "root"
    if relative_parts[0] == "clientes" and len(relative_parts) >= 3:
        return f"{relative_parts[0]}/{relative_parts[1]}"
    return relative_parts[0]


def _get_git_blame_cache(vault, relpath):
    """Runs git blame per file and returns {line_num: (age_days, date_iso)}."""
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "blame", "--line-porcelain", "--", relpath],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None

        cache = {}
        current_start = 0
        current_count = 0
        current_ts = None

        for line in result.stdout.split("\n"):
            m = re.match(r'^[0-9a-f]{40} (\d+) (\d+) (\d+)$', line)
            if m:
                if current_ts is not None and current_start > 0:
                    commit_dt = datetime.fromtimestamp(current_ts, tz=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - commit_dt).days
                    date_str = commit_dt.strftime("%Y-%m-%d")
                    for ln in range(current_start, current_start + current_count):
                        cache[ln] = (age_days, date_str)
                current_start = int(m.group(2))
                current_count = int(m.group(3))
                current_ts = None
                continue
            if line.startswith("committer-time "):
                current_ts = int(line.split()[1])

        if current_ts is not None and current_start > 0:
            commit_dt = datetime.fromtimestamp(current_ts, tz=timezone.utc)
            age_days = (datetime.now(timezone.utc) - commit_dt).days
            date_str = commit_dt.strftime("%Y-%m-%d")
            for ln in range(current_start, current_start + current_count):
                cache[ln] = (age_days, date_str)

        return cache if cache else None
    except Exception:
        return None


def find_todos(vault, include_done=False, with_aging=False, include_specs=False, include_skills=False, include_sessions=False, include_handoffs=False):
    """Finds all - [ ] and - [x] in vault files.

    By default EXCLUDES files with type: Spec — their checkboxes are
    design acceptance criteria (they define "done"), not executable
    tasks. Pass include_specs=True to include them explicitly.
    Also EXCLUDES files with type: Skill — their checkboxes are
    procedure self-audit checklists (quality criteria),
    not backlog items (decision 2026-08-06).
    Pass include_skills=True to include them explicitly.
    Also EXCLUDES files with type: Session — their checkboxes are the
    snapshot of what was pending on that date, not a live backlog: a past
    session keeps re-injecting its frozen list forever, and what it injects
    is mostly already done. Pass include_sessions=True to include them
    explicitly.
    Also EXCLUDES files with type: Handoff — their checkboxes are the
    frozen handoff state of a session, not a live backlog: they re-inject
    the same items every session until the handoff is updated. Pass
    include_handoffs=True to include them explicitly.
    """
    todos = []
    blame_cache = {}

    for md_file in find_md_files(vault):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        relpath = str(md_file.relative_to(vault))
        parts = md_file.relative_to(vault).parts
        fm, _ = parse_frontmatter(text)
        fm = fm or {}
        project = _extract_project(parts)

        # Criterios de aceptación de specs NO son tareas (decisión 2026-08-02)
        if not include_specs and fm.get("type") == "Spec":
            continue
        # Checklists de auto-auditoría de skills NO son tareas (decisión 2026-08-06)
        if not include_skills and fm.get("type") == "Skill":
            continue
        # Los checkboxes de una minuta son la foto de ese día, no backlog vivo
        if not include_sessions and fm.get("type") == "Session":
            continue
        # Los checkboxes de un handoff son estado congelado de una sesión
        if not include_handoffs and fm.get("type") == "Handoff":
            continue

        if with_aging and relpath not in blame_cache:
            blame_cache[relpath] = _get_git_blame_cache(vault, relpath)

        # Ignorar checkboxes dentro de bloques de código (```) — son ejemplos,
        # diagramas o capturas de estado, no tareas reales (fix 2026-08-02:
        # el insight embudo-invertido mostraba el checklist del plan CRM como
        # code block y el detector lo contaba como TODOs duplicados).
        in_code_block = False
        for i, line in enumerate(text.split("\n"), 1):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            m = re.match(r'^\s*- \[([ xX])\]\s+(.+)', line)
            if m:
                done = m.group(1) in ("x", "X")
                if done and not include_done:
                    continue
                entry = {
                    "file": relpath,
                    "project": project,
                    "line": i,
                    "done": done,
                    "text": m.group(2).strip(),
                    "context": fm.get("title", fm.get("description", relpath)),
                }
                if with_aging:
                    file_cache = blame_cache.get(relpath)
                    if file_cache and i in file_cache:
                        age_days, age_date = file_cache[i]
                        entry["age_days"] = age_days
                        entry["age_date"] = age_date
                        entry["age_status"] = "fresh" if age_days <= 3 else (
                            "aging" if age_days <= 7 else "stale")
                    else:
                        entry["age_days"] = None
                        entry["age_date"] = None
                        entry["age_status"] = "unknown"
                todos.append(entry)

    return todos


def find_agent_bus_signals():
    """Scans signals/→default/ in Hermes profiles (if they exist)."""
    agent_bus_base = Path.home() / ".hermes" / "profiles"
    signals_dir_suffix = Path("agent-bus") / "signals" / "→default"
    signals = []

    if not agent_bus_base.exists():
        return signals

    for profile_dir in sorted(agent_bus_base.iterdir()):
        if not profile_dir.is_dir():
            continue
        sd = profile_dir / signals_dir_suffix
        if not sd.exists():
            continue

        profile_name = profile_dir.name
        for signal_file in sorted(sd.glob("*.md")):
            if signal_file.name == ".gitkeep":
                continue
            try:
                text = signal_file.read_text(encoding="utf-8")
            except Exception:
                continue

            fm, _ = parse_frontmatter(text)
            fm = fm or {}
            from_profile = fm.get("from", profile_name)
            ticket_ref = fm.get("ticket_ref", "")
            urgency = fm.get("urgency", "normal")
            signal_type = fm.get("type", "notification")

            ref_str = f" — {ticket_ref}" if ticket_ref else ""
            urgency_icon = "🔴" if urgency == "escalated" else "📬"

            signals.append({
                "file": f"profiles/{profile_name}/agent-bus/signals/→default/{signal_file.name}",
                "project": f"agent-bus/{profile_name}",
                "line": 0,
                "done": False,
                "text": f"{urgency_icon} [{from_profile}] {signal_type}{ref_str}",
                "context": f"[{profile_name}] {signal_file.name}",
            })

    return signals


def _sanitize_cyber(cyber):
    """Converts date/datetime objects to strings for JSON."""
    if cyber is None or not isinstance(cyber, dict):
        return None
    result = {}
    for k, v in cyber.items():
        if isinstance(v, (datetime,)):
            result[k] = v.isoformat() if hasattr(v, 'isoformat') else str(v)
        elif hasattr(v, 'isoformat'):
            result[k] = v.isoformat()
        elif isinstance(v, dict):
            result[k] = {
                sk: sv.isoformat() if hasattr(sv, 'isoformat') else sv
                for sk, sv in v.items()
            }
        elif isinstance(v, list):
            result[k] = [x.isoformat() if hasattr(x, 'isoformat') else x for x in v]
        else:
            result[k] = v
    return result


def _body_after_frontmatter(text, fm_text):
    """Devuelve el cuerpo del archivo: lo que sigue al `---` de cierre."""
    resto = text[3 + len(fm_text):]
    partes = resto.split("\n", 2)  # "", "---", cuerpo
    return partes[2] if len(partes) == 3 else ""


def _body_first_line(text, fm_text):
    """Número de línea (1-indexado, como numera `read`) de la primera línea del
    cuerpo: las del `---` de apertura, el frontmatter y el `---` de cierre
    se cuentan desde el texto, así la numeración no depende de cuántas
    líneas tenga el frontmatter."""
    return text[:3 + len(fm_text)].count("\n") + 3


# Encabezado ATX de Markdown (`# …` a `###### …`), con o sin `#` de cierre.
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")

SNIPPET_MAX = 160   # chars del fragmento; el cuerpo nunca se devuelve entero
SECTION_MAX = 60    # chars del encabezado en JSON
SECTION_TABLE_MAX = 40  # chars del encabezado en la tabla legible


def _recortar(texto, maximo):
    """Recorta a `maximo` chars con «…» final si hubo que cortar."""
    if len(texto) <= maximo:
        return texto
    return texto[:maximo - 1] + "…"


def _snippet(linea, pos, largo):
    """Ventana de ≤ SNIPPET_MAX chars de `linea` (ya sin saltos ni tabs)
    centrada en el término que empieza en `pos` y mide `largo`; «…» reemplaza
    el primer/último char de cada borde recortado para que el largo total no
    pase del máximo."""
    if len(linea) <= SNIPPET_MAX:
        return linea
    centro = pos + largo // 2
    inicio = max(0, centro - SNIPPET_MAX // 2)
    fin = inicio + SNIPPET_MAX
    if fin > len(linea):
        fin = len(linea)
        inicio = fin - SNIPPET_MAX
    ventana = linea[inicio:fin]
    if inicio > 0:
        ventana = "…" + ventana[1:]
    if fin < len(linea):
        ventana = ventana[:-1] + "…"
    return ventana


def _span_original(texto, pos, largo):
    """Pasa el span plegado a offsets del original, sin alterar el snippet.

    Solo se necesita para la primera línea coincidente, nunca para plegar
    los cuerpos completos. ASCII mantiene los offsets sin trabajo adicional.
    """
    if texto.isascii():
        return pos, largo
    offset = 0
    inicio = None
    for i, ch in enumerate(texto):
        offset += len(plegar(ch))
        if inicio is None and offset > pos:
            inicio = i
        if inicio is not None and offset >= pos + largo:
            fin = i + 1
            while fin < len(texto) and not plegar(texto[fin]):
                fin += 1
            return inicio, fin - inicio
    return pos, largo


def _es_filtro(token):
    """True si el token es un filtro de campo (type:/status:/title:/cyber.*:)
    y no texto a buscar. Es la misma regla que aplica `matches_query`."""
    if ":" not in token or token.startswith("http"):
        return False
    field = token.split(":", 1)[0].strip()
    return "." in field or field in ("type", "status", "title")


def _fm_searchable(concept):
    """Título, descripción y tags en minúsculas y sin tildes: lo que busca `search` sin --body."""
    if "_fm" not in concept:
        concept["_fm"] = plegar(" ".join([
            concept["title"],
            concept["description"],
            " ".join(concept["tags"]) if isinstance(concept["tags"], list) else "",
        ]))
    return concept["_fm"]


def locate_body_match(concept, query):
    """Dónde, cuánto y bajo qué título coincide el cuerpo con la consulta.

    Devuelve {line, snippet, section, matches}:
    - `line`: línea (1-indexada desde el inicio del archivo, igual que `read`)
      de la PRIMERA mención.
    - `snippet`: esa línea, recortada a ≤ 160 chars centrada en el término.
    - `section`: último encabezado Markdown que precede a la mención (o el de
      la misma línea, si la mención está en un título), recortado a 60; None
      si no hay encabezado antes.
    - `matches`: cuántas líneas del cuerpo mencionan el término; con varios
      términos AND, cuántas mencionan al menos uno.

    Los términos que se buscan son los que NO están en el frontmatter: son los
    que hicieron que la nota entrara como acierto de cuerpo. Si todos están en
    el frontmatter (no debería pasar en un acierto de cuerpo) se usan todos.
    Los `#` dentro de un bloque de código no son encabezados.
    """
    fm = _fm_searchable(concept)
    tokens = [plegar(t) for t in query.lower().split() if not _es_filtro(t)]
    terminos = [t for t in tokens if t not in fm] or tokens
    resultado = {"line": None, "snippet": "", "section": None, "matches": 0}
    if not terminos:
        return resultado

    seccion = None
    en_codigo = False
    for i, linea in enumerate(concept.get("_body_raw", "").split("\n"),
                              concept.get("_body_start", 1)):
        if linea.strip().startswith("```"):
            en_codigo = not en_codigo
            continue
        if not en_codigo:
            m = _HEADING_RE.match(linea)
            if m:
                seccion = m.group(2).strip() or None
        baja = plegar(linea)
        posiciones = [(baja.find(t), len(t)) for t in terminos if t in baja]
        if not posiciones:
            continue
        resultado["matches"] += 1
        if resultado["line"] is None:
            resultado["line"] = i
            # La posición se busca sobre la línea normalizada (sin tabs ni
            # dobles espacios) para que el centrado del fragmento sea exacto.
            plana = " ".join(linea.split())
            plana_plegada = plegar(plana)
            pos, largo = min((plana_plegada.find(t), len(t))
                             for t in terminos if t in plana_plegada)
            pos, largo = _span_original(plana, pos, largo)
            resultado["snippet"] = _snippet(plana, pos, largo)
            resultado["section"] = _recortar(seccion, SECTION_MAX) if seccion else None
    return resultado


def find_concepts(vault, plegar_cuerpo=True):
    """Finds all concepts with frontmatter in the vault.

    El cuerpo (texto tras el frontmatter) se conserva en `_body`, en
    minúsculas, para que la búsqueda por cuerpo reutilice esta misma lectura
    en vez de abrir cada archivo dos veces; `_body_raw` (tal cual, para el
    fragmento y el encabezado) y `_body_start` (línea del archivo donde
    empieza el cuerpo) sirven para decir dónde coincidió. Son claves
    privadas: `run` las quita antes de imprimir.
    """
    from cli.indice import notas, rel_de
    archivos = find_md_files(vault)
    datos = notas(vault, archivos)
    concepts = []
    for md_file in archivos:
        relpath = rel_de(md_file, vault)
        nota = datos.get(relpath)
        fm = nota["fm"] if nota else None
        if not fm:
            continue

        parts = tuple(relpath.split("/"))
        folder = _extract_project(parts)

        tags = normalize_tags(fm.get("tags", []))
        body = nota["cuerpo"]

        concepts.append({
            "file": relpath,
            "folder": folder,
            "type": str(fm.get("type", "")),
            "title": str(fm.get("title", "")),
            "status": str(fm.get("status", "")),
            "description": str(fm.get("description", "")),
            "tags": tags,
            "resource": str(fm.get("resource", "")),
            "timestamp": str(fm.get("timestamp", "")),
            "cyber": _sanitize_cyber(fm.get("cyber")),
            # Plegar 40 MB de cuerpos cuesta: solo si hay texto que buscar.
            "_body": plegar(body) if plegar_cuerpo else "",
            "_body_raw": body,
            "_body_start": nota["cuerpo_inicio"],
        })

    return concepts


def matches_query(concept, query, body=False):
    """Checks if a concept matches the query (AND, case-insensitive).

    Es substring sobre título, descripción y tags; con body=True cada token
    puede coincidir también en el cuerpo del archivo (`_body`).
    """
    tokens = query.lower().split()
    if not tokens:
        return True

    searchable = _fm_searchable(concept)
    if body:
        searchable += " " + concept.get("_body", "")

    for token in tokens:
        if ":" in token and not token.startswith("http"):
            field, value = token.split(":", 1)
            field = field.strip()
            value = value.strip()

            if "." in field:
                parent, child = field.split(".", 1)
                if parent == "cyber":
                    cyber = concept.get("cyber")
                    if not isinstance(cyber, dict):
                        return False
                    child_parts = child.split(".")
                    val = cyber
                    for part in child_parts:
                        if isinstance(val, dict):
                            val = val.get(part)
                        else:
                            val = None
                            break
                    if val is None:
                        return False
                    if str(val).lower() != value:
                        return False
                    continue
                return False

            if field in ("type", "status", "title"):
                if concept.get(field, "").lower() != value:
                    return False
                continue

        if plegar(token) not in searchable:
            return False

    return True


# ── Orden por relevancia y corte (19-nov-2026) ──
#
# Medido con 10.131 notas: las 28 búsquedas que los agentes hicieron de verdad
# devolvían en promedio 436 notas (30.000 tokens por pregunta) en orden de
# ruta, y la nota correcta llegó a salir en el lugar 482. Coincidir sigue
# siendo lo mismo (cada término es subcadena, AND); lo nuevo es el orden y el
# corte: las notas se ordenan por un BM25F sobre título, descripción, tags y
# cuerpo, y la tabla muestra las `limit` primeras con un pie que dice cuántas
# más hay.
#
# Un término cuenta entero cuando empieza una palabra y casi nada cuando cae
# dentro de otra: «cto» está dentro de «proyecto» y de «producto», y por
# subcadena empataba con «CTO fraccional».

LIMITE = 20
PESOS = {"title": 3.0, "description": 2.0, "tags": 2.0, "body": 1.0}
# Largo del campo: el cuerpo se normaliza (b estándar); el título no. Medido:
# normalizar el título sube las notas cuyo título es casi solo el término (una
# persona, un activo) pero hunde las de título largo (una búsqueda de una
# palabra con 10.131 notas: la correcta pasó del 5.º al 14.º); en total, peor.
NORMA = {"title": 0.0, "description": 0.3, "tags": 0.0, "body": 0.75}
K1 = 1.2
INFIJO = 0.1


def _terminos(query):
    return [plegar(t) for t in (query or "").lower().split() if not _es_filtro(t)]


def _campos(c):
    if "_campos" not in c:
        c["_campos"] = {
            "title": plegar(c["title"]),
            "description": plegar(c["description"]),
            "tags": plegar(" ".join(c["tags"])) if isinstance(c["tags"], list) else "",
            "body": c.get("_body", ""),
        }
    return c["_campos"]


def _frecuencia(texto, termino, patron):
    total = texto.count(termino)
    if not total:
        return 0.0
    al_inicio = len(patron.findall(texto))
    return al_inicio + INFIJO * (total - al_inicio)


def puntuar(concepts, universo, query):
    """Pone `_puntaje` (BM25F) a cada nota de `concepts`.

    Las frecuencias de documento y los largos medios salen de `universo` (todas
    las notas visibles), no solo de los resultados.
    """
    import math
    terminos = _terminos(query)
    if not terminos or not concepts:
        return
    patrones = {t: re.compile(r"(?<!\w)" + re.escape(t)) for t in terminos}
    n = max(len(universo), 1)
    df = {t: 0 for t in terminos}
    largos = {campo: 0 for campo in PESOS}
    for c in universo:
        campos = _campos(c)
        for campo, texto in campos.items():
            largos[campo] += len(texto)
        for t in terminos:
            if any(t in texto and patrones[t].search(texto) for texto in campos.values()):
                df[t] += 1
    medio = {campo: max(total / n, 1.0) for campo, total in largos.items()}
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in terminos}
    for c in concepts:
        campos = _campos(c)
        puntaje = 0.0
        for t in terminos:
            peso = 0.0
            for campo, texto in campos.items():
                tf = _frecuencia(texto, t, patrones[t])
                if tf:
                    b = NORMA[campo]
                    peso += PESOS[campo] * tf / (1 - b + b * len(texto) / medio[campo])
            puntaje += idf[t] * peso / (K1 + peso)
        c["_puntaje"] = puntaje


def _parciales(universo, ya, terminos, body_mode, filter_type, filtros_inline):
    """Notas con al menos la mitad de las palabras (no todas), marcadas `partial`."""
    vistas = {c["file"] for c in ya}
    minimo = (len(terminos) + 1) // 2
    salida = []
    for c in universo:
        if c["file"] in vistas:
            continue
        if filter_type and c["type"].lower() not in _valores(filter_type):
            continue
        # Relajar el AND de palabras nunca relaja los predicados de campo.
        if not matches_query(c, filtros_inline):
            continue
        texto = _fm_searchable(c) + (" " + c.get("_body", "") if body_mode else "")
        tiene = sum(1 for t in terminos if t in texto)
        if minimo <= tiene < len(terminos):
            c["match"] = "partial"
            c["terms_matched"] = f"{tiene}/{len(terminos)}"
            salida.append(c)
    return salida


def ordenar(concepts):
    """Por puntaje, y las que coinciden solo en el cuerpo después (el contrato de --body).

    Medido: mezclar las dos por puntaje no cambió el resultado en las 26 búsquedas reales."""
    return sorted(concepts, key=lambda c: (c.get("match") == "body", -c.get("_puntaje", 0.0), c["file"]))


def _print_todos(todos, with_aging=False):
    """Prints pending tasks grouped by project."""
    if not todos:
        print("(no pending tasks)")
        return

    by_project = {}
    for t in todos:
        by_project.setdefault(t.get("project", t["file"]), []).append(t)

    total = len(todos)
    stale_count = sum(1 for t in todos if t.get("age_status") == "stale")
    aging_count = sum(1 for t in todos if t.get("age_status") == "aging")

    status_line = f"📋 Pending tasks ({total} found)"
    if with_aging:
        parts = []
        if stale_count:
            parts.append(f"🔴 {stale_count} >7d")
        if aging_count:
            parts.append(f"🟡 {aging_count} 3-7d")
        if parts:
            status_line += " — " + ", ".join(parts)
    print(status_line)
    print("─" * 60)

    for project, items in sorted(by_project.items()):
        context = items[0]["context"]
        if context and context != items[0]["file"]:
            print(f"\n📁 {project}  — {context}")
        else:
            print(f"\n📁 {project}")

        for item in items:
            prefix = ""
            if with_aging and item.get("age_days") is not None:
                days = item["age_days"]
                if days <= 3:
                    prefix = "🟢 "
                elif days <= 7:
                    prefix = "🟡 "
                else:
                    prefix = "🔴 "
                age_info = f" ({days}d)" if days > 0 else " (hoy)"
            elif with_aging:
                prefix = "⚪ "
                age_info = " (?)"
            else:
                age_info = ""

            marker = "☑" if item.get("done") else "☐"
            print(f"  {prefix}{marker} {item['text']}{age_info}")

    print(f"\n─ {total} task(s) in {len(by_project)} project(s)")


def _marca_cuerpo(c):
    """«(cuerpo L<n> · <k> menc. · § <sección>)»; sin línea queda «(cuerpo)»."""
    if not c.get("line"):
        return "(cuerpo)"
    partes = [f"cuerpo L{c['line']}", f"{c.get('matches', 0)} menc."]
    if c.get("section"):
        partes.append(f"§ {_recortar(c['section'], SECTION_TABLE_MAX)}")
    return "(" + " · ".join(partes) + ")"


def _print_table(concepts):
    """Prints a formatted text table."""
    if not concepts:
        print("(no results)")
        return

    cols = {
        "file": max(len(c["file"]) for c in concepts),
        "type": max(max(len(c["type"]) for c in concepts), 4),
        "status": max(max(len(c["status"]) for c in concepts), 6),
    }

    header = (
        f"{'FILE':<{cols['file']}}  "
        f"{'TYPE':<{cols['type']}}  "
        f"{'STATUS':<{cols['status']}}  "
        f"DESCRIPTION"
    )
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)

    for c in concepts:
        desc = c["description"][:80]
        if c.get("match") == "partial":
            desc += f"  (parcial: {c['terms_matched']} palabras)"
        if c.get("match") == "body":
            # Coincidió solo en el cuerpo: decir dónde (línea como la numera
            # `read`), cuánto (líneas que mencionan) y bajo qué encabezado.
            desc += "  " + _marca_cuerpo(c)
        prefix = "⚠ " if c["status"] == "propuesta" else "  "
        print(
            f"{prefix}{c['file']:<{cols['file']}}  "
            f"{c['type']:<{cols['type']}}  "
            f"{c['status']:<{cols['status']}}  "
            f"{desc}"
        )

    print(sep)
    print(f"{len(concepts)} concept(s)")


def _find_typed_edges(concepts, vault):
    """Finds typed edges between result concepts."""
    result_paths = {c["file"] for c in concepts}
    edges = []

    for c in concepts:
        filepath = vault / c["file"]
        try:
            text = filepath.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        if not fm:
            continue
        links = fm.get("links")
        if not links or not isinstance(links, list):
            continue
        for link in links:
            if not isinstance(link, dict):
                continue
            target = link.get("target", "")
            # Normalizar: quitar .md si existe
            target = target.replace(".md", "")
            edge_type = link.get("type", "wikilink")
            # Ver si el target está en los resultados (con o sin .md)
            if target in result_paths or f"{target}.md" in result_paths:
                edges.append((c["file"], target, edge_type))

    return edges


def _print_typed_edges(edges):
    """Prints detected relationships section."""
    if not edges:
        return
    print()
    print("## Detected relationships")
    for source, target, etype in edges:
        print(f"  {source}")
        print(f"    └─ {etype} → {target}")


def _valores(filtro):
    """«pendiente, en-curso» -> {"pendiente", "en-curso"}; vacío = sin filtro."""
    return {v.strip().lower() for v in str(filtro or "").split(",") if v.strip()}


def _sin_md(rel):
    return rel[:-3] if rel.endswith(".md") else rel


def _resolver_notas(pedidas, rels):
    """Rutas (con .md) de las notas nombradas en `pedidas` (slugs separados por coma).

    Acepta la ruta con o sin `.md`, o solo el nombre del archivo si no es
    ambiguo. Devuelve (rutas, errores): un slug que no existe o que nombra a
    varias notas es un error que se dice, no un filtro que calla.
    """
    por_slug = {_sin_md(r): r for r in rels}
    rutas, errores = set(), []
    for crudo in str(pedidas or "").split(","):
        pedido = _sin_md(crudo.strip().strip("[]"))
        if not pedido:
            continue
        if pedido in por_slug:
            rutas.add(por_slug[pedido])
            continue
        candidatas = [r for slug, r in por_slug.items() if slug.rsplit("/", 1)[-1] == pedido]
        if len(candidatas) == 1:
            rutas.add(candidatas[0])
        elif candidatas:
            errores.append(f"«{pedido}» nombra a varias notas: {', '.join(sorted(_sin_md(c) for c in candidatas))}")
        else:
            errores.append(f"no hay ninguna nota «{pedido}»")
    return rutas, errores


def filtrar_por_arista(concepts, todas, graph, edge=None, edge_dir="out",
                       linked=None, linked_type=None, linked_status=None):
    """Deja las notas con al menos una arista tipada que cumple el filtro.

    Es un salto exacto sobre `links:`, tal como está declarado: una arista hacia
    una nota reemplazada cuenta hacia ESA nota, no hacia su versión vigente
    (al revés que `traverse`, que la sustituye). Sirve para lo que antes pedía
    armar el conjunto a mano: «planes que aplican decisiones reemplazadas»,
    «tareas canceladas que aplicaban tal plan», «qué depende de tal sistema».

    edge_dir="out": la nota del resultado declara la arista hacia la enlazada.
    edge_dir="in":  la enlazada declara la arista hacia la nota del resultado.

    Cada nota que queda lleva en `edges` las aristas que cumplieron, con el
    tipo y el estado de la nota del otro extremo.
    """
    tipos_arista = _valores(edge)
    tipos_nota, estados = _valores(linked_type), _valores(linked_status)
    ficha = {c["file"]: c for c in todas}
    clave = "typed_out" if edge_dir != "in" else "typed_in"
    quedan = []
    for c in concepts:
        cumplen = []
        for arista in graph.get(c["file"], {}).get(clave, []):
            otra = ficha.get(arista["target"])
            if otra is None:
                continue
            if tipos_arista and arista["type"].lower() not in tipos_arista:
                continue
            if linked is not None and arista["target"] not in linked:
                continue
            if tipos_nota and otra["type"].lower() not in tipos_nota:
                continue
            if estados and otra["status"].lower() not in estados:
                continue
            cumplen.append({"type": arista["type"], "dir": "in" if edge_dir == "in" else "out",
                            "linked": _sin_md(arista["target"]), "linked_type": otra["type"],
                            "linked_status": otra["status"]})
        if cumplen:
            c["edges"] = cumplen
            quedan.append(c)
    return quedan


def _print_aristas(concepts, edge_dir):
    """Las aristas que cumplieron el filtro, por nota: es la justificación de cada fila."""
    print()
    if edge_dir == "in":
        print("## Aristas que cumplen el filtro (la nota enlazada la declara hacia la del resultado)")
    else:
        print("## Aristas que cumplen el filtro (la nota del resultado la declara)")
    for c in concepts:
        print(f"  {_sin_md(c['file'])}  [{c['status'] or 'sin estado'}]")
        for a in c.get("edges", []):
            flecha = "←" if edge_dir == "in" else "→"
            print(f"    └─ {a['type']} {flecha} {a['linked']}  ({a['linked_type']} · {a['linked_status'] or 'sin estado'})")


def run(args, vault, config=None):
    """Runs concept or task search."""
    json_out = getattr(args, "json", False)
    todos_mode = getattr(args, "todos", False)
    include_all = getattr(args, "all", False)
    with_aging = getattr(args, "aging", False)
    query = getattr(args, "query", None)
    filter_type = getattr(args, "filter_type", None)
    filter_status = getattr(args, "filter_status", None)
    cyber_field = getattr(args, "cyber_field", None)
    cyber_value = getattr(args, "cyber_value", None)
    review_due = getattr(args, "review_due", False)
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)

    with_graph = getattr(args, "with_graph", False)
    all_versions = getattr(args, "all_versions", False)
    body_mode = getattr(args, "body", False)
    edge = getattr(args, "edge", None)
    edge_dir = (getattr(args, "edge_dir", None) or "out").lower()
    linked = getattr(args, "linked", None)
    linked_type = getattr(args, "linked_type", None)
    linked_status = getattr(args, "linked_status", None)
    por_arista = bool(edge or linked or linked_type or linked_status)

    if with_aging and not todos_mode:
        print("Error: --aging only works with --todos", file=sys.stderr)
        return 1
    if include_all and not todos_mode:
        print("Error: --all only works with --todos", file=sys.stderr)
        return 1
    if cyber_value and not cyber_field:
        print("Error: --cyber-value requires --cyber-field", file=sys.stderr)
        return 1
    if edge_dir not in ("out", "in"):
        print("Error: --edge-dir es out (la nota declara la arista) o in (la recibe)", file=sys.stderr)
        return 1
    if por_arista and todos_mode:
        print("Error: los filtros de arista no se combinan con --todos", file=sys.stderr)
        return 1

    if todos_mode:
        include_specs = getattr(args, "include_specs", False)
        include_skills = getattr(args, "include_skills", False)
        include_sessions = getattr(args, "include_sessions", False)
        include_handoffs = getattr(args, "include_handoffs", False)
        todos = find_todos(vault, include_done=include_all, with_aging=with_aging, include_specs=include_specs, include_skills=include_skills, include_sessions=include_sessions, include_handoffs=include_handoffs)
        # El agent-bus vive fuera del vault (~/.hermes/profiles/…): nunca en una
        # instancia acotada a un subárbol (agente público).
        if not get_scope():
            todos.extend(find_agent_bus_signals())
        if json_out:
            print(json.dumps(todos, ensure_ascii=False, indent=2))
        else:
            _print_todos(todos, with_aging=with_aging)
        return 0

    concepts = find_concepts(vault, plegar_cuerpo=bool(query and query.strip()))
    limit = getattr(args, "limit", None)
    todas = concepts
    tipos, estados = _valores(filter_type), _valores(filter_status)

    graph = notas_enlazadas = None
    if por_arista:
        from cli.commands.graph import build_graph
        from cli.edge_types import resolve_definitions
        definiciones = config.edge_type_definitions() if config else None
        graph = build_graph(vault, definitions=definiciones)
        validos = {str(k).lower() for k in resolve_definitions(definiciones)}
        desconocidos = sorted(_valores(edge) - validos)
        if desconocidos:
            print(f"Error: tipo de arista desconocido: {', '.join(desconocidos)}. "
                  f"Válidos: {', '.join(sorted(validos))}", file=sys.stderr)
            return 1
        if linked:
            notas_enlazadas, errores = _resolver_notas(linked, [c["file"] for c in todas])
            if errores:
                print("Error: --linked: " + "; ".join(errores), file=sys.stderr)
                return 1

    # Las reemplazadas se ocultan salvo --all-versions o --status <ese estado>.
    # Se guardan aparte para contar al pie solo las que coinciden con la
    # consulta y el tipo: el total del vault no le dice nada al agente.
    hidden = []
    if not all_versions and not filter_status:
        from cli.states import is_replaced_status, schema_for
        kept = []
        for c in concepts:
            if (schema_for(config, c["type"]) is not None
                    and is_replaced_status(config, c["type"], c["status"])):
                hidden.append(c)
                continue
            kept.append(c)
        concepts = kept
    universo = concepts
    if query and query.strip():
        # Con --body, una reemplazada que coincide solo en su cuerpo también
        # cuenta: es la misma regla de recall que para las vivas.
        hidden = [c for c in hidden if matches_query(c, query, body=body_mode)]
    if filter_type:
        hidden = [c for c in hidden if c["type"].lower() in tipos]
    if por_arista:
        hidden = filtrar_por_arista(hidden, todas, graph, edge, edge_dir, notas_enlazadas,
                                    linked_type, linked_status)

    if query and query.strip():
        fm_hits = [c for c in concepts if matches_query(c, query)]
        # Las que coinciden solo en el cuerpo van después de las de frontmatter.
        # Sin --body solo se cuentan para el pie, y únicamente cuando el
        # frontmatter devolvió poco (<5): ahí es donde un agente concluye
        # «no existe» sobre una muestra silenciosa.
        body_hits = []
        if body_mode or len(fm_hits) < 5:
            vistos = {c["file"] for c in fm_hits}
            body_hits = [c for c in concepts
                         if c["file"] not in vistos and matches_query(c, query, body=True)]
        for c in fm_hits:
            c["match"] = "frontmatter"
        for c in body_hits:
            c["match"] = "body"
            if body_mode:
                # Solo con --body: sin él estas notas no se muestran y el
                # trabajo sería en vano.
                c.update(locate_body_match(c, query))
        concepts = fm_hits + body_hits
    if filter_type:
        concepts = [c for c in concepts if c["type"].lower() in tipos]
    if filter_status:
        concepts = [c for c in concepts if c["status"].lower() in estados]
    if por_arista:
        concepts = filtrar_por_arista(concepts, todas, graph, edge, edge_dir, notas_enlazadas,
                                      linked_type, linked_status)

    if cyber_field:
        field_path = cyber_field.split(".")
        value = (cyber_value or "").lower()
        filtered = []
        for c in concepts:
            cyber = c.get("cyber")
            if not isinstance(cyber, dict):
                continue
            val = cyber
            for part in field_path:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break
            if val is not None and str(val).lower() == value:
                filtered.append(c)
        concepts = filtered

    if review_due:
        from datetime import timedelta
        today_str = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")  # Colombia UTC-5
        filtered = []
        for c in concepts:
            cyber = c.get("cyber")
            if not isinstance(cyber, dict):
                continue
            review_on = cyber.get("review_on")
            if review_on and str(review_on) <= today_str:
                filtered.append(c)
        concepts = filtered

    if since or until:
        try:
            since_dt = datetime.fromisoformat(since) if since else None
            until_dt = datetime.fromisoformat(until) if until else None
            # Asegurar timezone-aware: si la entrada es naive, asumir UTC
            if since_dt and since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            if until_dt and until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
        except ValueError as e:
            print(f"Error: invalid date — {e}", file=sys.stderr)
            return 1
        filtered = []
        for c in concepts:
            ts_raw = c.get("timestamp", "")
            if not ts_raw:
                continue
            try:
                ts_raw = ts_raw.strip().strip('"').strip("'")
                ts_dt = datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            # Normalizar a UTC para comparación
            if ts_dt.tzinfo is not None:
                ts_dt = ts_dt.astimezone(timezone.utc)
            else:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            if since_dt and ts_dt < since_dt:
                continue
            if until_dt and ts_dt > until_dt:
                continue
            filtered.append(c)
        concepts = filtered

    # Sin --body las coincidencias de cuerpo no se muestran ni cambian la
    # salida (tampoco el campo `match` en JSON): solo se anuncian al pie.
    solo_cuerpo = set()
    if not body_mode:
        solo_cuerpo = {c["file"] for c in concepts if c.get("match") == "body"}
        concepts = [c for c in concepts if c.get("match") != "body"]

    # Con texto en la consulta: por relevancia y cortadas a `limit` (la tabla;
    # el JSON solo se corta si se pide --limit). Sin texto, orden de ruta y
    # todas: un filtro por tipo o estado pide la lista completa.
    total = len(concepts)
    ordenadas = bool(_terminos(query))
    parciales = []
    if ordenadas:
        if limit is None and not json_out:
            limit = LIMITE
        puntuar(concepts, universo, query)
        concepts = ordenar(concepts)
        terminos = _terminos(query)
        filtros_inline = " ".join(t for t in (query or "").lower().split() if _es_filtro(t))
        # Igual que --status/--cyber-field: estas consultas piden coincidencias
        # completas, no relleno. Los filtros type/title sí admiten parciales.
        filtros_estrictos = any(t.split(":", 1)[0] not in ("type", "title")
                               for t in filtros_inline.split())
        if (len(terminos) > 1 and limit and limit > 0 and len(concepts) < limit
                and not (filter_status or cyber_field or review_due or since or until
                         or filtros_estrictos or por_arista)):
            parciales = _parciales(universo, concepts, terminos, body_mode, filter_type,
                                   filtros_inline)
            puntuar(parciales, universo, query)
            parciales = ordenar(parciales)[:limit - len(concepts)]
            concepts = concepts + parciales
        if limit and limit > 0:
            concepts = concepts[:limit]
    # Algunas coincidencias completas con --body ya se mostraron como parcial
    # sin --body. Contar solo notas adicionales tras el corte real de salida.
    solo_cuerpo.difference_update(c["file"] for c in concepts)
    for c in concepts:
        for k in ("_body", "_body_raw", "_body_start", "_campos", "_puntaje", "_fm"):
            c.pop(k, None)
        if not body_mode and c.get("match") != "partial":
            c.pop("match", None)

    if json_out:
        print(json.dumps(concepts, ensure_ascii=False, indent=2))
    else:
        _print_table(concepts)
        if por_arista and concepts:
            _print_aristas(concepts, edge_dir)
        if parciales:
            print(f"\n({total} nota(s) con todas las palabras; se completó con {len(parciales)} "
                  f"que tienen algunas, marcadas «parcial»)")
        if ordenadas and len(concepts) - len(parciales) < total:
            print(f"\n(las {len(concepts)} más relevantes de {total}; hay {total - len(concepts)} más: "
                  f"afina la consulta o pide limit=0 para verlas todas)")
        if solo_cuerpo:
            print(f"\n({len(solo_cuerpo)} nota(s) más mencionan \"{(query or '').strip()}\" "
                  f"en el cuerpo; --body para verlas)")
        if hidden:
            # Cada oculta con su vigente (slugs sin .md: el trazador del
            # server tomaría un token terminado en .md como nodo resultado).
            # Con --json no se agrega nada: la salida es una lista.
            # Para nombrar la vigente alcanza el grafo de `corrige` (el mismo
            # que usa `read`); el grafo entero costaba medio segundo con 10.000 notas.
            from cli.vigente import build_superseded_map, grafo_de_corrige, slug_of
            fm_cache = {}
            vigentes = build_superseded_map(vault, grafo_de_corrige(vault, fm_cache), config, fm_cache)
            pares = []
            for c in hidden:
                vigente = vigentes.get(c["file"])
                destino = (f"vigente {slug_of(vigente)}" if vigente
                           else "sin vigente declarada")
                pares.append(f"{slug_of(c['file'])} → {destino}")
            print(f"\n({len(hidden)} nota(s) reemplazada(s) coinciden y se ocultan: "
                  f"{'; '.join(pares)}; --all-versions para verlas)")
        if with_graph and not todos_mode:
            edges = _find_typed_edges(concepts, vault)
            if edges:
                _print_typed_edges(edges)
            else:
                print("\n(no typed edges among results)")

    return 0
