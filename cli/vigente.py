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
from cli.states import is_replaced_status, schema_for

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
