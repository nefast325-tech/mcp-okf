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

SUPPORT_EDGES = ("extiende", "fundamenta", "depende")


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


def _resolve_support_target(vault, rel, target):
    """Ruta (relativa al vault) del destino de una arista, o None.

    Primero el caso normal de este vault (`decisions/x.md` o `decisions/x`
    relativo a la raíz), sin tocar el disco más que con un `is_file`; solo si
    eso falla se usa `resolve_link`, la misma resolución que `graph` y
    `validate`, para targets escritos a mano (`../x`, nombre pelado).
    """
    target = str(target).strip()
    if not target:
        return None
    for cand in (target, target if target.endswith(".md") else target + ".md"):
        if (vault / cand).is_file():
            return cand
    try:
        from cli.wikilinks import resolve_link
        return resolve_link(target, vault, (vault / rel).parent)
    except Exception:
        return None


def grafo_de_corrige(vault, fm_cache):
    """Grafo mínimo con solo las aristas `corrige`, para `build_superseded_map`.

    Existe por costo: `build_graph` extrae los wikilinks del cuerpo y puntúa
    cada arista, y `build_superseded_map` volvía a parsear todos los
    frontmatters encima (0,10 s + 0,08 s en un vault de 132 notas). Para
    saber quién corrige a quién alcanza una pasada por el frontmatter, que
    queda en `fm_cache` y el mapa la reutiliza. La forma es la que el mapa
    lee: {rel: {"typed_in": [{"target": <fuente>, "type": "corrige"}]}}, con
    el mismo conjunto de nodos que `build_graph` (`find_md_files(vault)`).
    """
    from cli.vault import find_md_files
    rels = [str(f.relative_to(vault)) for f in find_md_files(vault)]
    graph = {rel: {"typed_in": []} for rel in rels}
    for rel in rels:
        if rel not in fm_cache:
            fm_cache[rel] = _frontmatter(vault, rel)
        links = fm_cache[rel].get("links")
        for link in (links if isinstance(links, list) else []):
            if not isinstance(link, dict) or link.get("type") != "corrige":
                continue
            target = _resolve_support_target(vault, rel, link.get("target", ""))
            if target and target in graph and target != rel:
                graph[target]["typed_in"].append({"target": rel, "type": "corrige"})
    return graph


def apoyos_rotos_de(vault, rel, config, fm=None, graph=None):
    """Aristas de apoyo de `rel` que apuntan a una nota reemplazada o muerta.

    Existe para que `read` avise ANTES del cuerpo cuando una nota vigente se
    apoya (`extiende`, `fundamenta`, `depende`, `aplica`) en algo derogado:
    `validate` ya lo detecta (fase 4), pero esa salida nadie la lee cuando
    razona sobre el cuerpo; `read` sí. El criterio es el mismo que en
    `validate._check_edge_type_consistency` y se mantiene a propósito:

    - la fuente tiene que estar viva (ni reemplazada ni muerta); una nota
      reemplazada es historia y `read` ya la anuncia con ♻️;
    - destino reemplazado (el `replaced` de su capa): aviso con su vigente
      (`current`) si la cadena de `corrige` la da, o `current=None`;
    - destino muerto sin sucesora (`dead` de su capa, p. ej. plan
      `cancelada`): solo para `extiende`/`fundamenta`/`depende`, igual que
      en `validate`; `aplica` hacia una muerta no avisa.

    Solo si algún destino está reemplazado se recorre el vault, y solo el
    frontmatter (`grafo_de_corrige`), para seguir `corrige` hasta la vigente;
    una nota limpia o con un apoyo muerto paga apenas el frontmatter de sus
    destinos. `graph` admite uno ya construido (p. ej. de `build_graph`).

    Devuelve una lista en el orden de `links:` con dicts
    {"edge", "target", "status", "kind": "reemplazada"|"muerta", "current"}.
    """
    rel = str(rel)
    if config is None or not getattr(config, "states_schemas", None):
        return []
    fm = fm if fm is not None else _frontmatter(vault, rel)
    src_type = str(fm.get("type", ""))
    src_status = fm.get("status")
    src_viva = (schema_for(config, src_type) is not None
                and not is_replaced_status(config, src_type, src_status)
                and not is_dead_status(config, src_type, src_status))
    if not src_viva:
        return []
    links = fm.get("links") if isinstance(fm.get("links"), list) else []

    rotos = []
    fm_cache = {rel: fm}
    for link in links:
        if not isinstance(link, dict):
            continue
        edge = str(link.get("type", ""))
        if edge not in SUPPORT_EDGES + ("aplica",):
            continue
        target = _resolve_support_target(vault, rel, link.get("target", ""))
        if not target:
            continue
        if target not in fm_cache:
            fm_cache[target] = _frontmatter(vault, target)
        t_fm = fm_cache[target]
        t_type = str(t_fm.get("type", ""))
        t_status = t_fm.get("status")
        if schema_for(config, t_type) is None:
            continue
        if is_replaced_status(config, t_type, t_status):
            rotos.append({"edge": edge, "target": target, "status": str(t_status),
                          "kind": "reemplazada", "current": None})
        elif edge in SUPPORT_EDGES and is_dead_status(config, t_type, t_status):
            rotos.append({"edge": edge, "target": target, "status": str(t_status),
                          "kind": "muerta", "current": None})

    if any(r["kind"] == "reemplazada" for r in rotos):
        if graph is None:
            graph = grafo_de_corrige(vault, fm_cache)
        superseded = build_superseded_map(vault, graph, config, fm_cache=fm_cache)
        for r in rotos:
            if r["kind"] == "reemplazada":
                r["current"] = superseded.get(r["target"])
    return rotos


def formato_apoyos_rotos(rotos):
    """Líneas del banner de `read` para `apoyos_rotos_de`, una por arista.

    Si la vigente repite la de la línea anterior se dice «la misma vigente»:
    cuando tres decisiones se corrigieron en una sola, repetir su slug tres
    veces costaba más bytes que todo el resto del aviso. Cierra con el conteo
    y qué hacer; esa línea no nombra estados a propósito, para que un conteo
    de avisos por línea no la cuente como uno más.
    """
    if not rotos:
        return []
    lines = []
    prev_current = None
    for r in rotos:
        head = f"⚠️  se apoya ({r['edge']}) en {slug_of(r['target'])}, {r['status']}"
        if r["kind"] == "muerta":
            lines.append(f"{head} (muerta, sin sucesora)")
            prev_current = None
        elif r["current"] is None:
            lines.append(f"{head} (sin vigente declarada)")
            prev_current = None
        elif r["current"] == prev_current:
            lines.append(f"{head} → la misma vigente")
        else:
            lines.append(f"{head} → vigente {slug_of(r['current'])}")
            prev_current = r["current"]
    n = len(rotos)
    lines.append(f"   → {n} apoyo{'s' if n != 1 else ''} roto{'s' if n != 1 else ''}: "
                 "re-anclá la arista o revisá el cuerpo.")
    return lines
