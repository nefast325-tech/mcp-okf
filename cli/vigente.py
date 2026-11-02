"""La versión vigente de una nota reemplazada (fase 4 del rediseño).

Cuando una nota pasa a su estado de reemplazo (`superada`, `reemplazada`: el
`replaced` de su capa en `types.states`), la que la sustituye declara una
arista `corrige` hacia ella. Ese par (estado + arista entrante) es la memoria
de errores del grafo: `validate` exige que vayan juntos.

Este módulo la lee al revés: dado el grafo, para cada nota reemplazada dice
cuál es su versión vigente, siguiendo la cadena de `corrige` hasta una nota
que ya no está reemplazada. `traverse` y `search` saltan a esa versión por
defecto y muestran la cadena solo si se pide (`--all-versions`).

Sin esquema de estados no hay «estado de reemplazo» exacto y el mapa queda
vacío: nada cambia para un vault que no declara capas.
"""

from cli.frontmatter import parse_frontmatter
from cli.states import is_dead_status, is_replaced_status, schema_for
from cli.vault import find_md_files

SUPPORT_EDGES = ("extiende", "fundamenta", "depende")

# Aristas por las que una nota viva «se apoya» en otra. `aplica` entra aparte:
# hacia una reemplazada avisa (re-anclar), hacia una muerta no (el trabajo que
# aplicaba un plan cancelado es historia, no incoherencia).
APOYO_EDGES = SUPPORT_EDGES + ("aplica",)


def _frontmatter(vault, rel):
    try:
        fm, _ = parse_frontmatter((vault / rel).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return fm or {}


def build_superseded_map(vault, graph, config=None, fm_cache=None):
    """{nota_reemplazada: nota_vigente} siguiendo `corrige` hasta el final.

    Una nota cuenta como reemplazada si su `status` es el `replaced` de su
    capa. Su sucesora directa es la fuente de un `corrige` que la apunta; si
    hay varias, la primera en orden de ruta. Si la sucesora también está
    reemplazada se sigue la cadena (con guarda de ciclos). Una nota
    reemplazada sin `corrige` entrante no tiene vigente y no entra al mapa.
    """
    if config is None or not getattr(config, "states_schemas", None):
        return {}
    fm_cache = fm_cache if fm_cache is not None else {}

    def fm_of(rel):
        if rel not in fm_cache:
            fm_cache[rel] = _frontmatter(vault, rel)
        return fm_cache[rel]

    def replaced(rel):
        fm = fm_of(rel)
        ctype = str(fm.get("type", ""))
        return schema_for(config, ctype) is not None and is_replaced_status(
            config, ctype, fm.get("status"))

    successor = {}
    for rel, node in graph.items():
        if not replaced(rel):
            continue
        sources = sorted(e["target"] for e in node.get("typed_in", [])
                         if e.get("type") == "corrige")
        if sources:
            successor[rel] = sources[0]

    current = {}
    for rel in successor:
        seen = {rel}
        cur = successor[rel]
        while cur in successor and cur not in seen:
            seen.add(cur)
            cur = successor[cur]
        if cur in seen:
            # Ciclo de corrige: no hay vigente que señalar
            continue
        current[rel] = cur
    return current


def current_version(rel, superseded_map):
    """La vigente de `rel`, o `rel` mismo si no está reemplazada."""
    return superseded_map.get(rel, rel)


def slug_of(rel):
    """`decisions/x.md` → `decisions/x`: lo que el agente pasa a `read`.

    Se usa en lo que `read` y `search` imprimen a stdout porque el trazador
    del server toma cualquier token terminado en `.md` como nodo resultado.
    """
    rel = str(rel)
    return rel[:-3] if rel.endswith(".md") else rel


def resolve_note(vault, rel, config, graph=None):
    """Vigencia de UNA nota, para que `read` la anuncie antes del cuerpo.

    Devuelve {"replaced", "status", "current", "current_title", "corrects"}:
    si la nota está en su estado de reemplazo, `current` es su vigente (o
    None si nadie la `corrige`); `corrects` son las notas que esta corrige.

    El grafo entero solo se construye cuando hace falta (la nota está
    reemplazada o declara `corrige`): una nota vigente común no paga nada.
    """
    rel = str(rel)
    fm = _frontmatter(vault, rel)
    ctype = str(fm.get("type", ""))
    status = str(fm.get("status") or "")
    info = {"replaced": False, "status": status, "current": None,
            "current_title": "", "corrects": []}
    if config is None or not getattr(config, "states_schemas", None):
        return info
    info["replaced"] = (schema_for(config, ctype) is not None
                        and is_replaced_status(config, ctype, status))
    links = fm.get("links") if isinstance(fm.get("links"), list) else []
    declares_corrige = any(isinstance(l, dict) and l.get("type") == "corrige" for l in links)
    if not info["replaced"] and not declares_corrige:
        return info
    if graph is None:
        from cli.commands.graph import build_graph  # import tardío: evita ciclo
        graph = build_graph(vault)
    node = graph.get(rel, {})
    info["corrects"] = [e["target"] for e in node.get("typed_out", [])
                        if e.get("type") == "corrige"]
    if info["replaced"]:
        current = build_superseded_map(vault, graph, config).get(rel)
        if current:
            info["current"] = current
            info["current_title"] = str(_frontmatter(vault, current).get("title", ""))
    return info


# ── Apoyos rotos: la coherencia que `validate` detecta, como dato reutilizable ──
#
# Hasta v0.4.32 la regla «una nota viva no se apoya en una reemplazada ni en
# una muerta sin sucesora» vivía dentro de `validate` como texto a stderr: la
# única salida que nadie lee en una sesión normal. Acá la regla devuelve datos
# para que `validate`, `stale` y `health` cuenten lo mismo desde un solo lugar.


def resolve_target_rel(vault, target, current_dir, name_index=None):
    """Ruta relativa (str) del destino de una arista, o None si no existe.

    Usa `resolve_link` —la MISMA resolución que `graph` y que el chequeo de
    wikilinks— y, si falla, el path pelado con y sin `.md`: un target escrito
    sin extensión (uso normal en los vaults) tiene que resolver igual que uno
    con ella.
    """
    try:
        from cli.wikilinks import resolve_link  # import tardío: evita ciclo
        rel = resolve_link(target, vault, current_dir, name_index)
    except Exception:
        rel = None
    if rel and (vault / rel).exists():
        return str(rel)
    for cand in (str(target), str(target) + ".md"):
        if (vault / cand).exists():
            return cand
    return None


def nota_viva(config, concept_type, status):
    """True si la nota tiene esquema y no está reemplazada ni muerta.

    Solo una nota viva puede ser incoherente: una reemplazada o cancelada que
    se apoya en otra reemplazada es historia, y la historia no se corrige.
    """
    return (schema_for(config, concept_type) is not None
            and not is_replaced_status(config, concept_type, status)
            and not is_dead_status(config, concept_type, status))


def apoyos_rotos_de(vault, rel, fm, config, name_index=None, fm_cache=None,
                    superseded_map=None):
    """Aristas de apoyo de UNA nota viva que caen en reemplazadas o muertas.

    Devuelve una lista, en el orden de `links:`, de dicts con:
      index          posición de la arista en `links:` (para que `validate`
                     intercale su aviso donde siempre)
      type           extiende / fundamenta / depende / aplica
      target_raw     el destino tal como está escrito en el frontmatter
      target         ruta relativa resuelta (`decisions/x.md`)
      target_status  `status` del destino, crudo
      clase          "reemplazada" (hay cadena `corrige` que seguir) o
                     "muerta" (estado `dead` de su capa, sin sucesora)
      vigente        versión vigente del destino según `superseded_map`,
                     o None si no se pasó el mapa o nadie la corrige

    Vacía si no hay esquema de estados, si la fuente no está viva, o si no
    declara `links:`. Las reglas son exactamente las de `validate`:
    - cualquier arista de `APOYO_EDGES` hacia una nota en su estado de
      reemplazo es «reemplazada»;
    - solo `SUPPORT_EDGES` hacia una nota en estado `dead` es «muerta»;
    - un destino sin esquema no se evalúa.
    """
    if not isinstance(fm, dict):
        return []
    links = fm.get("links")
    if not isinstance(links, list):
        return []
    src_type = str(fm.get("type", ""))
    if not nota_viva(config, src_type, fm.get("status")):
        return []
    fm_cache = fm_cache if fm_cache is not None else {}
    current_dir = (vault / rel).parent

    def fm_of(target_rel):
        if target_rel not in fm_cache:
            fm_cache[target_rel] = _frontmatter(vault, target_rel)
        return fm_cache[target_rel]

    rotas = []
    for i, link in enumerate(links):
        if not isinstance(link, dict):
            continue
        edge_type = str(link.get("type", ""))
        if edge_type not in APOYO_EDGES:
            continue
        target_raw = str(link.get("target", ""))
        target_rel = resolve_target_rel(vault, target_raw, current_dir, name_index)
        if target_rel is None:
            continue
        t_fm = fm_of(target_rel)
        if not t_fm:
            continue
        t_type = str(t_fm.get("type", ""))
        t_status = t_fm.get("status")
        if schema_for(config, t_type) is None:
            continue
        if is_replaced_status(config, t_type, t_status):
            clase = "reemplazada"
        elif edge_type in SUPPORT_EDGES and is_dead_status(config, t_type, t_status):
            clase = "muerta"
        else:
            continue
        rotas.append({
            "index": i,
            "type": edge_type,
            "target_raw": target_raw,
            "target": target_rel,
            "target_status": t_status,
            "clase": clase,
            "vigente": (superseded_map or {}).get(target_rel),
        })
    return rotas


def apoyos_rotos(vault, config, graph=None, name_index=None, fm_cache=None):
    """{nota_fuente: [aristas rotas]} para todo el vault.

    El grafo solo hace falta para decir cuál es la versión vigente de cada
    reemplazada (`build_superseded_map` sigue `corrige` por `typed_in`); quien
    ya lo construyó (`stale`, `health`) lo pasa y no se construye dos veces.
    Sin esquema de estados devuelve {} y nada cambia para ese vault.
    """
    if config is None or not getattr(config, "states_schemas", None):
        return {}
    files = find_md_files(vault, config=config)
    if name_index is None:
        name_index = {f.name: str(f.relative_to(vault)) for f in files}
    fm_cache = fm_cache if fm_cache is not None else {}

    def fm_of(rel):
        if rel not in fm_cache:
            fm_cache[rel] = _frontmatter(vault, rel)
        return fm_cache[rel]

    if graph is None:
        from cli.commands.graph import build_graph  # import tardío: evita ciclo
        graph = build_graph(vault)
    superseded = build_superseded_map(vault, graph, config, fm_cache)

    out = {}
    for f in files:
        rel = str(f.relative_to(vault))
        rotas = apoyos_rotos_de(vault, rel, fm_of(rel), config, name_index,
                                fm_cache, superseded)
        if rotas:
            out[rel] = rotas
    return out


def describe_apoyo(arista):
    """Texto corto de una arista rota, el mismo en `stale` y en `health`.

    `apoyo en reemplazada (fundamenta → decisions/x)` o
    `apoyo en muerta sin sucesora (extiende → plans/y)`. Lleva el slug (sin
    `.md`) porque es lo que el agente pasa a `read` para ir a mirar.
    """
    que = "reemplazada" if arista["clase"] == "reemplazada" else "muerta sin sucesora"
    return f"apoyo en {que} ({arista['type']} → {slug_of(arista['target'])})"
