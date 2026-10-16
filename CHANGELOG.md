# Changelog

Todas las modificaciones notables al servidor MCP OKF. Formato basado en [Keep a Changelog](https://keepachangelog.com/).

---
## [2026-10-16] — v0.4.24

Estado de los conceptos declarado en el vault (PR #31, Medardo Rafael Hernandez Franco).

### Added
- **Máquina de estados por tipo, declarada en la config** (`types.states.schemas`, agrupada por capa): `types`, `states`, `initial`, `transitions`, `replaced` (el estado que exige un `corrige` entrante) y `pending` (lo que `stale` cuenta como sin resolver). `states: []` = capa de registro, no lleva `status`. `types.states.strict` (default `false`) decide aviso o error.
- `new` sin `--status` arranca en el estado inicial del tipo; con `--status`, el valor tiene que estar en el esquema. Un tipo de registro no admite ninguno.
- `edit --status` valida el estado y la transición desde el actual; `--force` salta la transición (migraciones).
- `graph.inherit_default_pairs: false` deja fuera los `valid_pairs` embebidos —usan el vocabulario de **un** vault (`LeccionAprendida`, no `Lesson`)— para que cada vault declare los suyos con sus propios nombres de tipo, conservando descripción, transitividad e inversa.
- `graph.strict_pairs: true`: un par (tipo origen, tipo destino) fuera de los declarados pasa de aviso a error en `validate`, y `new`/`edit` no escriben la arista.
- **`okf stamp [--staged]`**: fija `timestamp` desde el diff de contenido contra `HEAD` — si el cuerpo, el `title` o la `description` cambiaron y el timestamp quedó igual al de `HEAD`, lo renueva; si no cambió el contenido y el timestamp sí, restaura el de `HEAD`; los archivos nuevos no se tocan. Con `--staged` vuelve a stagear lo que ajustó, para el pre-commit.

### Changed
- **`timestamp` mide el contenido, no los metadatos**: `edit` lo renueva solo si cambian `body`, `title` o `description`. Un cambio solo de `status`, tags, aristas o campos custom ya no lo renueva. Aplica **también sin esquema** — es un cambio global, no una rama opt-in (verificado sobre un vault sin `types.states`: antes `Changes: status, timestamp`, ahora `Changes: status` con la fecha intacta).
- **`stale` señal 3** (`ghost proposal`) lee `pending` del esquema del tipo; sin esquema, el literal histórico `propuesta`. **Señal 6** exige status a cualquier tipo con estados declarados; sin esquema, a `Decision`.
- **La regla de `corrige` mira el estado `replaced`** de la capa del destino en vez de la subcadena; sin esquema conserva la heurística histórica (`deprec`/`superced`). Una nota en estado de reemplazo sin `corrige` entrante avisa (error con `strict`); el literal `supercedida` sigue siendo error, como hasta ahora. Con esquema en modo aviso no bloquea, porque el vault está migrando.
- La validación 12 de `validate` (status contra el esquema) se suma a las existentes; sin esquema no comprueba nada.

### Medido (2026-10-16)
- Suite: **396 → 439** (43 nuevos). Revirtiendo `cli/` a `master`, los nuevos dan 24 fallos + 3 errores: no son vacíos.
- `validate --all` y `stale --json` sobre `OKF-Vault` (699 archivos, sin `types.states`): salida **idéntica** a v0.4.23. La rama opt-in no cambia nada sin esquema.
- Con esquema en un vault de prueba: `new` siembra el inicial, `edit --status` rechaza estado fuera del esquema y transición no declarada (`superada → vigente` bloquea, `--force` pasa), `validate` avisa con `strict: false` y bloquea con `true`, y la capa de registro avisa si lleva status.
- Los tres casos de `stamp` verificados en un repo de prueba (contenido cambió, solo metadatos, archivo nuevo).
- El check `Timestamp↔git` de `health` no se rompe: solo alerta si el timestamp va en el futuro respecto al último commit o más de 30 días antes del primero, y no renovar en cambios de metadatos va en la dirección segura.

### Known follow-up
- `dashboard_snapshot._collect_stale_results` llama a `collect_stale(...)` **sin** el nuevo `config=`, así que un vault con esquema haría divergir el panel DashboardView de `stale` y `board` (mismo vault, `ATTENTION` ↔ `STALE`). Inerte sin esquema. Repro en el comentario de revisión del PR #32.

---
## [2026-10-15] — v0.4.23

### Changed
- **Los tipos exentos de la señal 4 de `stale` («no backlinks») salen de config: `types.exempt_from_no_backlinks`** (con default embebido). La lista estaba hardcodeada con el vocabulario de **un** vault —`("MarcoTeorico", "Spec", "Tool", "Agente", "Sesion")`— mientras `types.valid` sí es configurable: en el vault del cliente, donde el tipo es `Session` y no `Sesion`, 9 sesiones entraban como señal 4 y el mismo número significaba cosas distintas en cada vault. El default embebido es el set del Criterio de obsolescencia más los dialectos conocidos: `MarcoTeorico, Spec, Tool, Agente, Sesion, Session, Skill, Insight`.
- **Señal 3: el umbral de `status: propuesta` para `type: Insight` sube a 90 días** (`stale.propuesta_days_insight`, default 90) y el resto de los tipos mantiene 30. Es la decisión del triaje automático del subgrafo dreaming (2026-09-05): una hipótesis tiene más vida que una propuesta de acción, su ciclo es integrarse o archivarse, no «aplicarse». Cierra N1 y N2 del plan del ajuste del detector, en propuesta desde el 2026-09-05.
- `dashboard_snapshot` comparte ahora la config completa del detector: antes omitía `stale.reads_zero_min_days` y no podía ver los exentos, así que su distribución podía contradecir a `stale` (misma clase que el #21 y el #29).

### Fixed
- Dos tests de `test_stale_reads_store.py` usaban `type: Insight` para fabricar el caso de 3 señales. Con `Insight` exento de la señal 4 ese camino ya no existe: el caso se prueba con un tipo no exento (`Research`). El contrato del store de lecturas no cambia.

### Medido (2026-10-15, sobre los vaults reales)
| Vault | Señal 4 | STALE | ATTENTION | FRESCO |
|---|---|---|---|---|
| `OKF-Vault` (698 conceptos) | 110 → **67** | 9 → **2** | 357 → **317** | 332 → **379** |
| `skynet` (238 conceptos) | 43 → **26** | 0 → 0 | 62 → **44** | 176 → **194** |

Tests: 396 (11 nuevos; RED 5 fallos + 2 errores contra el código anterior), verdes también en entorno limpio tipo CI (`env -i` + venv scratch).

---
## [2026-10-15] — v0.4.22

### Fixed
- **`health` buscaba el hook siempre en `<vault>/.git/hooks/pre-commit`** (#30). Con `core.hooksPath hooks` —la activación que recomienda el `CLAUDE.md` de `skynet-vault`— o dentro de un worktree (donde `.git` es un archivo), un vault sano daba **7/8** con «pre-commit hook does not exist» aunque el hook existiera y corriera: como ese hook termina en `health --strict`, **el commit quedaba abortado** y no había forma de commitear con la configuración recomendada. Repro medido en un clon nuevo del vault: 7/8 sin el fix, 8/8 con él. `_hooks_dir(vault)` le pregunta a git con `git rev-parse --git-path hooks`; `dashboard_snapshot` hereda el arreglo porque importa la misma función. `tests/test_health_git_hook_hookspath.py` (+5).
- **`trace --layers hooks` seguía leyendo `.git/hooks` a mano** (misma clase que el #30, cerrada en el mismo lote): en un vault con `core.hooksPath` respondía «(no results)» en silencio aunque el hook existiera — demostrado en vivo sobre `skynet` antes y después. Ahora usa `_hooks_dir` de `health`: **un solo lugar decide dónde vive el hook**. `tests/test_trace_hooks_dir.py` (+4, RED 3/4 contra el código anterior).
- **La señal 4 de `stale` («no backlinks») resolvía los wikilinks por nombre de archivo e ignoraba las aristas tipadas** (#29), así que contradecía a `graph`. Medido sobre `skynet` (239 conceptos, 2026-10-15): 51 conceptos marcados antes, 34 después — **17 falsos positivos corregidos (33 %)**, 12 por aristas tipadas y 5 por basenames repetidos (`[[projects/x/x]]` caía en cualquier `x.md` y la ficha real quedaba huérfana). `build_backlinks_index` usa ahora `build_graph`, la misma resolución que `graph`. Costo: `stale` pasa de ~0.5 s a ~0.68 s en 239 conceptos, porque construye el grafo completo (aceptable a esta escala). `tests/test_stale_backlinks.py` (+3).

### Changed
- El check del hook de `health` incluye en el mensaje **la ruta donde buscó** («looked in …»): un 7/8 se diagnostica sin leer el código (sugerencia del autor del #30).

Tests: 385.

### Fuera de alcance (verificado, no incluido)
- La tupla de tipos «naturalmente huérfanos» de la señal 4 lista `Sesion`, pero el vault usa `type: Session` (15 conceptos, 9 sin backlinks): esos siguen contando como «no backlinks». Es la diferencia entre los 60 que reporta el CLI y los 51 del conteo sin sesiones. `tests/test_dashboard_snapshot_conceptos.py:73` fija la premisa con el typo. Decisión de producto pendiente (¿una sesión sin backlinks es señal de staleness?), fuera de este release.

---
## [2026-10-01] — v0.4.21

### Fixed
- **El hook versionado (`hooks/pre-commit`) dependía del `python3` del PATH y bloqueaba el commit si ese intérprete no traía PyYAML**. Hallado en el boot sequence del handoff DSH del 2026-10-01: el toolchain de Hermes se instaló el 2026-09-30 en `~/.hermes/tools/python-3.14.7*/bin`, quedó primero en el PATH de las sesiones de Hermes, y **no incluye PyYAML**. Desde ese momento `python3 -m cli validate` moría con `ModuleNotFoundError: No module named 'yaml'` y el hook reportaba «COMMIT BLOQUEADO — VALIDACIÓN FALLIDA»: el mensaje culpaba a la validación en vez de al intérprete.
- El hook ahora **resuelve un intérprete que pueda importar `yaml`** (`/usr/bin/python3` primero, luego el `python3` del PATH), lo usa en todas sus invocaciones del CLI (`validate`, `index`, `dashboard-snapshot`, `health`, `graph impact-batch`, `regenerar-prefill.py`), admite override explícito con `OKF_PYTHON` y **aborta con `exit 1` y un mensaje accionable** si ninguno sirve — un hook que no puede validar no debe aprobar el commit en silencio.
- `tests/test_hooks_template_coherente.py` (+4 tests, 9 en total): toda invocación del CLI en el template debe pasar por el intérprete resuelto; el orden de candidatos empieza por `/usr/bin/python3`; el override está documentado; y sin intérprete válido el hook sale `1`.

### Por qué pasó (misma clase que el detector)
El 2026-10-01 también se encontró que `~/.hermes/scripts/detector-ritmo.py` respondía «0 handoffs / IDLE» en silencio cuando el intérprete no tenía PyYAML (ver `dsh-rhythm`, commit `9d8459b`). Las dos veces el patrón es el mismo: **una dependencia dura que falta se degrada en una respuesta plausible en vez de fallar**. Los sensores que no pueden leer no deben reportar «todo tranquilo».

Tests: 373.

---

## [2026-09-29] — v0.4.20

### Fixed
- **`install.sh --with-hooks` no generaba el snapshot del DashboardView** (#24). La config que escribía (`.pre-commit-config.yaml`) corría `validate → index → health` y nada más, así que un vault instalado con `--with-cognitive-trace --with-hooks` mostraba el panel con «Snapshot no generado aún — se regenera en cada commit»: la promesa era falsa, porque ningún hook lo generaba.

- La sección 9 de `install.sh` agrega un cuarto hook (`okf-dashboard-snapshot`) con `|| true` — nunca bloquea el commit — y el vault entre comillas dentro del `bash -c`. Añade además `/dashboard.json` y `/sistema/dashboard-snapshots/` al `.gitignore` del vault, sin duplicar reglas y respetando un `.gitignore` sin salto final.

- `tests/test_install_hooks_snapshot.py` corre el bloque real de la sección 9 contra un vault temporal.

Nota: en el vault del sistema este hueco ya lo cubre el cron de 30 min; el fix sirve sobre todo a instalaciones nuevas.

Tests: 341.

---

## [2026-09-29] — v0.4.19

### Fixed
- **El hook versionado (`hooks/pre-commit`) seguía invocando y stageando el `dashboard.md` retirado** — regresión del retiro de v0.4.17 en la capa que este mismo CHANGELOG declara fuente de verdad. Un `git add` fallaba con `fatal: pathspec` en cada commit que tocara el vault.

- El template del repo (`hooks/pre-commit`) se actualizó para dejar de llamar a `cli dashboard` y de stagear `dashboard.md`. Además se añadió `tests/test_hooks_template_coherente.py` que guarda el template frente a futuros cambios del CLI: falla si el hook menciona un subcomando que no existe o si intenta stagear un archivo desconocido. Se verificó RED sobre el template viejo (detecta `dashboard` en la línea 49 y `dashboard.md` en el `git add`).

Tests: 339.

---

## [2026-09-29] — v0.4.18

### Added
- **Modo público de solo-lectura con alcance de subárbol** (#25). Dos interruptores independientes:
  - `OKF_SCOPE=<subpath>` (p.e. `publico` o `research/2026`) acota todas las lecturas (`read`, `search`, `traverse`, `graph*`, `touch`, `index`, `file_info`, `health`, `audit`, `review`, `stale`, `todos`) al subárbol indicado, resolviendo rutas relativas dentro de él y bloqueando el acceso a todo lo que está fuera.
  - `OKF_READONLY=true` bloquea las escrituras (`new`, `edit`, `index`, `canvas`, `dashboard-snapshot`, `migrate-reads`, `migrate`) a nivel de CLI y, a nivel de servidor MCP, deja de registrar las herramientas de escritura en el `_WRITE_TOOLS` expuesto.

- Cambios en el código:
  - `cli/access.py`: nuevas funciones `scope_root`, `in_scope_file`, `normalize_scope`, y constante `WRITE_COMMANDS` (sin `touch`: el incremento manual está deprecado y solo lee estadísticas).
  - `cli/vault.py`: `resolve_slug` y `find_md_files` usan el scope cuando está activo.
  - `cli/commands/*.py`: todos los comandos de lectura llaman a `access.set_scope` al inicio si la variable de entorno está definida.
  - `server.py`: respeta ambos interruptores; bajo `OKF_READONLY` deja de registrar `_WRITE_TOOLS` y, bajo `OKF_SCOPE`, envuelve cada herramienta de lectura con un filtro que devuelve error si el resultado está fuera del subárbol.
  - Nuevas pruebas en `tests/test_scope_readonly.py` (372 líneas) que cubren:
    - acotación de lecturas a subárboles válidos,
    - bloqueo de lectura fuera de scope (por ruta y por nombre de archivo),
    - traversal que no sigue wikilinks que salen del scope,
    - aborto explícito ante scopes que intentan salir del vault (`../..`),
    - comportamiento por defecto sin variables de entorno (visto completo),
    - registro condicional de herramientas en el servidor MCP,
    - precedencia CLI > ENV > default para ambos interruptores,
    - y la asimetría intencional: `touch` está en `_WRITE_TOOLS` del servidor (así responde a readonly) pero **no** en `WRITE_COMMANDS` del CLI (siempre disponible para diagnósticos rápidos que no alteran contenido).

- **Nota de confidencialidad**: el scope por sí solo **no** es un límite de confidencialidad. Si se usa sin `OKF_READONLY=true`, las herramientas de análisis (`analytics`, `trace`, `session-metrics`) siguen registradas y leen la telemetría *global* (contadores, lecturas, etc.), aunque filtran los *nombres* de los nodos devueltos. El aislamiento estricto requiere **ambos** interruptores: `OKF_SCOPE` + `OKF_READONLY=true`.

Tests: 369.

---

## [2026-09-28] — v0.4.17