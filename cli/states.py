"""Máquina de estados por tipo, declarada en la config del vault.

El `status` de un concepto era texto libre: `type` se validaba, `status` no.
Con el tiempo el mismo campo acumuló tres preguntas distintas (¿sigue siendo
cierto?, ¿en qué quedó?, ¿cómo va?) y 13 valores con significado distinto
según el tipo (medición del 15-oct-2026 sobre el vault de cliente).

Este módulo lee el esquema de `types.states` en `.okf.config.yaml` y responde,
para un tipo dado: qué estados admite, cuál es el inicial, qué transiciones
permite, cuál es su estado de reemplazo (el que exige una arista `corrige`
entrante) y cuáles cuentan como "pendiente" para el detector `stale`.

Forma del esquema (agrupado por capa para no repetir cinco veces lo mismo):

    types:
      states:
        strict: false            # false = aviso, true = error (validate)
        schemas:
          conocimiento:
            types: [Research, Insight, Lesson, Criterion, Workflow]
            initial: vigente
            states: [vigente, en-revision, superada]
            transitions:
              vigente: [en-revision, superada]
              en-revision: [vigente, superada]
              superada: [en-revision]
            replaced: superada
            pending: [en-revision]
          registro:
            types: [Session, Persona]
            states: []           # sin ciclo: no llevan status

Sin esquema para un tipo, todo sigue como antes: el status no se valida y la
regla de `corrige` usa la heurística histórica (subcadena «deprec»/«superced»).
"""

_LEGACY_REPLACED_MARKERS = ("deprec", "superced")


def schema_for(config, concept_type):
    """Devuelve el esquema (dict) del tipo, o None si el vault no declara uno."""
    if config is None or not concept_type:
        return None
    schemas = getattr(config, "states_schemas", None) or {}
    for name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        types = schema.get("types") or []
        if concept_type in types:
            return {
                "layer": name,
                "types": list(types),
                "states": [str(s) for s in (schema.get("states") or [])],
                "initial": schema.get("initial"),
                "transitions": schema.get("transitions") or {},
                "replaced": schema.get("replaced"),
                "pending": [str(s) for s in (schema.get("pending") or [])],
                "done": [str(s) for s in (schema.get("done") or [])],
            }
    return None


def check_checkboxes(config, concept_type, status, checked, open_):
    """Las casillas del cuerpo acotan el estado (capa de trabajo).

    `done` en el esquema nombra los estados que significan «terminado»
    (p. ej. `hecha`; `cancelada` no, porque cancelar deja cosas sin hacer).

    - Terminado con casillas abiertas → problema (error con strict). Cerrar
      una nota es decisión de alguien, pero no puede contradecir su lista.
    - Inicial con casillas ya marcadas → aviso siempre: algo empezó y el
      estado no lo dice.

    Returns:
        tuple[list[str], list[str]]: (problemas, avisos).
    """
    schema = schema_for(config, concept_type)
    if schema is None or not schema["states"]:
        return [], []
    value = str(status).strip() if status is not None else ""
    problems, warnings = [], []
    if value in schema["done"] and open_ > 0:
        problems.append(f"'{concept_type}' en '{value}' con {open_} casilla(s) abierta(s): "
                        "marcalas, tachalas con su motivo o devolvé la nota a un estado activo")
    initial = str(schema["initial"] or schema["states"][0])
    if value == initial and checked > 0 and schema["done"]:
        warnings.append(f"'{concept_type}' en '{value}' con {checked} casilla(s) ya marcada(s): "
                        "parece que empezó")
    return problems, warnings


def requires_status(config, concept_type):
    """True si el tipo tiene esquema con al menos un estado."""
    schema = schema_for(config, concept_type)
    return bool(schema and schema["states"])


def initial_status(config, concept_type):
    """Estado inicial del tipo según el esquema, o None."""
    schema = schema_for(config, concept_type)
    if not schema or not schema["states"]:
        return None
    return str(schema["initial"] or schema["states"][0])


def check_status(config, concept_type, status):
    """Problemas de `status` respecto al esquema del tipo.

    Returns:
        list[str]: vacía si no hay esquema o el status cumple.
    """
    schema = schema_for(config, concept_type)
    if schema is None:
        return []
    value = str(status).strip() if status is not None else ""
    if not schema["states"]:
        if value:
            return [f"'{concept_type}' no lleva status (capa '{schema['layer']}', "
                    f"es un registro); tiene status='{value}'"]
        return []
    if not value:
        return [f"'{concept_type}' exige status; inicial: "
                f"'{initial_status(config, concept_type)}'"]
    if value not in schema["states"]:
        return [f"status '{value}' no está en el esquema de '{concept_type}' "
                f"({', '.join(schema['states'])})"]
    return []


def check_transition(config, concept_type, old, new):
    """Problema al pasar de `old` a `new`, o None si la transición es válida.

    Sin esquema, o sin `transitions` declaradas, cualquier cambio vale. Un
    concepto sin status previo puede tomar cualquier estado del esquema.
    """
    schema = schema_for(config, concept_type)
    if schema is None or not schema["states"]:
        return None
    new_value = str(new).strip()
    problems = check_status(config, concept_type, new_value)
    if problems:
        return problems[0]
    old_value = str(old).strip() if old else ""
    if not old_value or old_value == new_value:
        return None
    transitions = schema["transitions"]
    if not transitions:
        return None
    allowed = [str(s) for s in (transitions.get(old_value) or [])]
    if new_value not in allowed:
        return (f"transición no permitida para '{concept_type}': "
                f"'{old_value}' → '{new_value}' (desde '{old_value}' se puede ir a: "
                f"{', '.join(allowed) if allowed else 'ningún estado'})")
    return None


def is_replaced_status(config, concept_type, status):
    """True si `status` es el estado de reemplazo del tipo.

    Con esquema: igualdad exacta con `replaced`. Sin esquema: heurística
    histórica por subcadena (deprecada / supercedida), para no cambiar el
    comportamiento de vaults que no declaran estados.
    """
    value = str(status).strip().lower() if status is not None else ""
    schema = schema_for(config, concept_type)
    if schema is not None:
        replaced = schema["replaced"]
        return bool(replaced) and value == str(replaced).lower()
    return any(marker in value for marker in _LEGACY_REPLACED_MARKERS)


def pending_states(config, concept_type):
    """Estados que `stale` cuenta como pendientes (señal 3).

    Con esquema: los declarados en `pending`. Sin esquema: `propuesta`, el
    valor histórico.
    """
    schema = schema_for(config, concept_type)
    if schema is None:
        return {"propuesta"}
    return {s.lower() for s in schema["pending"]}


def states_strict(config):
    """True si el vault pide que el incumplimiento del esquema sea error."""
    return bool(getattr(config, "states_strict", False)) if config else False
