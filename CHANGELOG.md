# Changelog

Todas las modificaciones notables al servidor MCP OKF. Formato basado en [Keep a Changelog](https://keepachangelog.com/).

---
## [Unreleased]

---
## [2026-10-22] — v0.4.30

El sensor de review contaba conceptos muertos como tareas pendientes: escaneaba `cyber.review_on` sin mirar `status`, así que un concepto que completó apoptosis aparecía vencido para siempre y `health`/el dashboard lo contaban como loop roto. Los dos únicos vencidos del vault OKF el 2026-10-22 eran una Decision `reemplazada` (dashboard.md) y un Plan `cancelada` (migración a A2A) — 100% falsos positivos.

### Estados muertos (`types.states.schemas.*.dead`)

- **`dead` en el esquema de estados** (cli/states.py): cada capa declara qué estados significan «este concepto ya no vive». `dead_states()` devuelve ese conjunto más el `replaced` de la capa; `is_dead_status()` responde por concepto. Sin esquema se mantiene la heurística histórica por subcadena (`deprec`, `superced`, `superad`, `obsolet`, `cancel`, `descart`, `rechaz`, `reemplaz`). `done`/`hecha` **no** son muerte: una nota terminada conserva su métrica re-verificable.
- **`review` filtra muertos** (cli/commands/review.py): `collect_due(vault, config=None, skipped=None)` omite los conceptos muertos y, si el llamador pasa `skipped`, los anexa; la salida humana cierra con una línea `(N concepto(s) muerto(s) omitido(s): …)`. `run()` propaga `config`, así que el mismo criterio aplica al CLI y al MCP, y el exit code deja de ser 1 por notas muertas.
- **El dashboard hereda el filtro** (cli/commands/dashboard_snapshot.py): `_cibernetica_section(vault, config=None)` pasa `config` a `collect_due` y no cuenta `review_on_vencidos`/`_nodes`/`proximos_7d` de un concepto muerto. Los outcomes (`loops_abiertos`/`loops_cerrados`) sí lo incluyen: al morir el concepto su loop cerró, el bloque queda como registro histórico.
- Tests: `tests/test_review_conceptos_muertos.py` (+12): `dead_states` con `replaced` incluido, `hecha` no es muerte, heurística sin esquema, Decision `reemplazada` / Plan `cancelada` omitidos, `aceptada` sigue siendo tarea, mezcla vivos/muertos, `skipped`, `review` sin pendientes con exit 0 más la línea de omitidos, y `review_on_vencidos_nodes` sin el muerto. Suite: **535**.
- Medido en el vault OKF (2026-10-22): antes `review` → 2 vencidos, exit 1; después → `✅ Nothing pending review` con los 2 muertos listados aparte, exit 0.

---
## [2026-10-21] — v0.4.29

Las fases 4 y 5 del rediseño del vault de cliente, en una etiqueta: la verdad tiene fecha y quien decide es una nota (#39), y cada nota dice a qué iniciativa pertenece con el alcance de una instancia del MCP por campo (#40). Las dos son opt-in por vault.

### Fase 5 — la iniciativa como campo y el alcance por campo

- **`types.fields.common`** (cli/fields.py): un esquema de campos que vale para todos los tipos salvo los de `except`; el esquema del tipo lo pisa campo a campo. Es como el vault exige `iniciativa` en toda nota menos en la capa de registro y en las tareas (que la heredan de la nota a la que aplican) sin repetir la regla diez veces.
- **Campos con lista** (cli/fields.py): un campo puede llevar lista (`iniciativa: [inmobiliaria, caribe-tropical]`) y cada ítem se valida contra `values`, `resolves` y `format`; una lista vacía en un campo exigido es problema.
- **`--scope campo=valor`** (cli/access.py): la frontera de una instancia puede ser un campo del frontmatter, no solo un prefijo de ruta. `find_md_files`, la resolución de wikilinks, `read` y `traverse` ven solo las notas cuyo campo (texto o lista) contiene el valor; una nota sin el campo y los índices quedan fuera (fail-closed); un scope mal formado (`campo=a/b`, `=x`) aborta. El set de rutas visibles se construye una vez por proceso leyendo el frontmatter, sin pasar por `find_md_files`. El scope por ruta no cambia. `set_scope(prefix, vault=)` recibe el vault desde `cli/main.py`; `server.py` propaga `--scope` sin cambios.
- Tests: `tests/test_fase5_iniciativa_scope_por_campo.py` (+13): esquema común y exentos, pisado campo a campo, listas (válida, con un ítem malo, vacía), `validate`; normalización del scope por campo y rechazo de formas inválidas; por subproceso, `search`/`read`/`traverse` acotados a una iniciativa (incluida una nota con lista), nota sin campo e índice invisibles, sin scope se ve todo, scope por ruta intacto. Sin el código, 12 de los 13 fallan. Suite: **523**.
- Medido (2026-10-19, master del vault de cliente, sin el campo todavía): `--scope iniciativa=caribe-tropical search` devuelve **0 notas** (fail-closed, como debe); la inferencia desde tags y carpetas cubre **160 notas**, **53** quedan exentas (Session, Persona, Task) y **78** necesitan una regla más o asignación a mano — ese es el tamaño de la migración, que va en su propio PR del vault.

### Fase 4 — la verdad tiene fecha, quien decide es una nota y una nota reemplazada señala su vigente

Fase 4 del rediseño del vault de cliente: la verdad tiene fecha, quien decide es una nota y una nota reemplazada señala su versión vigente. Las tres piezas son opt-in por vault: sin `resolves` en `types.fields`, sin `types.review` y sin esquema de estados, el comportamiento es el de v0.4.28.

### Added
- **`resolves: <Tipo>` en `types.fields`** (cli/fields.py): el valor de un campo tiene que nombrar una nota existente de ese tipo —por el nombre del archivo o de su carpeta de entidad (`personas/rafael/…` responde a `rafael`)— o estar en `values`. Es lo que hace que `decidida_por` y `responsable` resuelvan a una `Persona` en vez de a texto libre. `validate`, `new` y `edit` lo aplican con la misma severidad del resto del esquema de campos; sin vault a mano (uso de librería) solo cuenta `values`.
- **`verificado_el` / `revisar_el`** (cli/verificacion.py): fecha en que una nota se comprobó contra su fuente y cuándo volver a comprobarla. `revisar_el` es opcional: se deriva de `verificado_el` más el plazo del tipo (`types.review.days`) o del prefijo de ruta (`types.review.paths`, que gana sobre el tipo). `validate` (validación 14) exige fechas ISO y en orden (aviso; error con `types.review.strict`); `new` sella `verificado_el` con la fecha de hoy en los tipos con plazo; `stale` suma la señal 8 (`verification overdue (Nd)`); `review` lista lo vencido en una sección aparte y con `kind: verificacion` en el JSON (los ítems de `cyber.review_on` llevan `kind: cyber`).
- **Versión vigente** (cli/vigente.py): a partir del estado de reemplazo del esquema y de las aristas `corrige`, el mapa `{reemplazada: vigente}` sigue la cadena hasta la nota que no está reemplazada. `traverse` salta a la vigente por defecto —el nodo visitado lista en `superseded` lo que saltó, una semilla reemplazada arranca desde su vigente con aviso, y la arista `corrige` de vuelta no se recorre— y `--all-versions` recorre la cadena tal cual. `search` oculta las reemplazadas salvo `--status <ese estado>` o `--all-versions`, y dice cuántas ocultó. El servidor MCP expone `all_versions` en `traverse` y `search`.
- **`validate` avisa cuando una nota vigente se apoya en una reemplazada** (`extiende`, `fundamenta`, `depende`): nunca error, porque mover la arista a la versión vigente es decisión de quien edita.

### Fixed
- **`health` daba 7/8 en un vault cuyo `Decision` exige campos**: la prueba de humo `new --type Decision --dry-run` no pasaba ningún `--field`, así que «'Decision' exige el campo 'decidida_por'» la hacía fallar con el CLI sano. Medido 19-oct-2026 sobre la rama de migración del vault de cliente. Ahora `_smoke_fields` arma los `--field` desde el esquema del tipo (primer `values`, primer nombre que resuelve, hoy para una fecha, «smoke» para texto libre). Tests: +2 (`_smoke_fields` y el `new` de humo como subproceso, que falla sin los campos y pasa con ellos).

### Tests
- `tests/test_fase4_verificacion_persona_vigente.py` (+23): `resolves` por carpeta y por archivo, `values` junto a `resolves`, texto libre rechazado, mensaje del campo exigido, `validate`/`new` con Persona; plazo por tipo y por ruta, fecha derivada y explícita, días vencidos, formato y orden (aviso y error), `new` sella la fecha solo en tipos con plazo y respeta `--field`, `stale` señal 8 y `review` (JSON y humano); mapa de vigentes con cadena de dos saltos, vacío sin esquema, `traverse` salta / `--all-versions` / semilla reemplazada, `search` oculta y muestra, y el aviso de apoyo en reemplazada (sin aviso en la propia cadena de `corrige`).
- Suite: **510** (485 en v0.4.28).

### Medido (2026-10-19, master del vault de cliente, config actual)
- `validate --all`: **291 válidas, 0 errores, 1 aviso nuevo** — la decisión del bloque legal de la Circular 004 `extiende` el insight ya superado: el caso que la fase 4 nombra.
- `search`: **8 notas reemplazadas ocultas** (3 insights, 5 decisiones). `traverse` desde el insight superado de la Circular 004 arranca en su versión vigente y la anota en `superseded`.
- Con la config de la fase 4 cargada (Decision exige `decidida_por`/`decidida_el`, plazos por tipo): **59 decisiones** fallan por los dos campos que faltan, las **38 `Task`** fallan porque todavía no existe ninguna nota `Persona` a la que resolver, y hay **0 señales 8** porque ninguna nota tiene `verificado_el`. Es el tamaño de la migración, no un error del CLI.

### Verificado antes de mergear (2026-10-21)

- Suite en el worktree del PR y en un entorno limpio tipo CI (`env -i` + venv scratch instalado con `pip install -e`): **510** con el #39 y **523** con el #40. Los números del CHANGELOG eran correctos; los cuerpos de los PR decían 508 y 521 (arrastraban la base de v0.4.28: 485+23 y 510+13).
- Baseline RED: el archivo de tests del #39 falla en `master` con `ImportError: reset_resolve_cache`; los 12 de 13 del #40 fallan con `cli/` revertido.
- Medido de nuevo sobre un clon fresco del vault de cliente: `validate --all` 300 válidas + **1 aviso** (la decisión del bloque legal `extiende` el insight superado), `search` **292 + 8 ocultas = 300** — las 8 son 3 `superada` + 5 `reemplazada` según `board` — y `traverse` desde el insight superado arranca en su vigente.
- Scope por campo: fail-closed sin datos (**0** notas), scope por ruta intacto (**4**), mal formado aborta con **exit 1**, y `read`/`traverse`/`board`/`stale` no ven nada fuera del scope.
- **Efecto en este vault (no medido por los PR):** `OKF-Vault` oculta **6** notas reemplazadas en `search` y `validate` suma **2 avisos** (un Plan y un Research apoyados en decisiones reemplazadas). `health` sigue 9/9.

---
## [2026-10-19] — v0.4.28

El tablero dice de qué plan es parte una tarea y qué espera una nota bloqueada: cada tarjeta del `board` exporta sus aristas tipadas y cuántas le entran. Es el #38, que había quedado sin etiqueta.

### Added
- **`board`: cada tarjeta trae `relaciones` y `entrantes`.** `relaciones` son las aristas tipadas salientes de la nota (`{type, target, title}`, con el título del destino resuelto desde el vault y `null` si no existe; el target se acepta con o sin `.md`) y `entrantes` cuenta las aristas tipadas que apuntan a ella desde cualquier nota con frontmatter, incluida la capa de registro. Es lo que deja leer en un tablero «de qué plan es parte esta tarea» o «qué espera esta nota bloqueada» sin abrirla. Pedido del vault de cliente, 17-oct-2026. Test: +1 en `tests/test_board.py`.

### Tests
- `tests/test_board.py::test_tarjeta_trae_relaciones_y_entrantes` (+1): tres aristas (destino sin `.md`, con `.md`, inexistente), entrantes en los destinos y listas vacías donde no hay aristas. Sin el cambio falla con `KeyError: 'relaciones'`.
- Suite: **485** (484 en v0.4.27).

### Medido (2026-10-17, master del vault de cliente)
- **268 tarjetas, 218 con al menos una relación, 360 aristas, 0 destinos sin título**; las 37 `Task` traen su `aplica`. El JSON que genera la Action no trae estos campos hasta que su `OKF_VERSION` apunte a esta etiqueta.

---
## [2026-10-17] — v0.4.27

Las dos decisiones de modelo del vault de cliente, hechas cumplir por el CLI: las casillas acotan el estado (#34) y las tareas son notas con `responsable`/`vence` (#35), más el cierre de tres asimetrías de `validate` (#36, #37). Las dos reglas son opt-in por vault: sin `done` en `types.states` y sin `types.fields` en la config, el comportamiento es el de v0.4.26.

### Added
- **`done:` por capa en `types.states.schemas`** (validación 12): una nota en un estado «terminado» con casillas abiertas es problema (aviso por defecto, error con `strict`); una nota en el estado inicial con casillas ya marcadas es aviso siempre. `edit --status` rechaza pasar a un estado `done` con casillas abiertas (`--force` para migraciones). `cancelada` no va en `done` a propósito: cancelar deja cosas sin hacer. Sin `done` en el esquema no cambia nada (#34).
- **`types.fields`** (cli/fields.py): el vault declara, por tipo, campos exigidos (`required`), valores cerrados (`values`) y formato (`format: date`, ISO). `validate` lo comprueba (validación 13: aviso por defecto, error con `types.fields.strict`), `new` no crea un concepto al que le falte un campo exigido y `edit --field` no deja un valor fuera de la lista. Sin esquema para un tipo, los campos siguen sin validarse.
- **`okf tasks list [--responsable] [--iniciativa] [--status] [--vencidas] [--json]`**: las notas del tipo `types.task` (default `Task`) ordenadas con las vencidas primero y luego por `vence`; la iniciativa es la propia o la de la nota a la que `aplica`. Una tarea en un estado `done` de su capa no vence.
- **`okf tasks promote <nota> (--line N | --match TEXTO) --responsable X [--vence FECHA]`**: convierte una casilla abierta en una nota de tarea con `aplica` → la nota de origen, y reemplaza la casilla por un enlace a la tarea para que `search --todos` y `board` no la cuenten dos veces. Rechaza una línea que no es casilla abierta, un campo exigido ausente (con strict), una fecha mal escrita y un par `aplica` no declarado cuando `graph.strict_pairs` está activo. `--dry-run` muestra la tarea y el reemplazo sin escribir. Bloqueado en modo solo-lectura.
- `board`: cada tarjeta trae `responsable` y `vence` (null si la nota no los lleva).
- Plantilla embebida para `Task` («Qué hay que hacer» / «Hecho cuando»).

### Tests
- `tests/test_tasks_y_campos.py` (+16): esquema de campos (exigido, valores, fecha), validate estricto y en aviso, `new`/`edit` con esquema, `tasks list` (orden, filtros, iniciativa heredada, JSON), `promote` (crea, enlaza, reemplaza, rechazos, par no declarado, dry-run) y `board` con los campos nuevos.
- `tests/test_states_checkboxes.py` (+7): estado `done` con casillas abiertas (error con strict, aviso sin él), estado inicial con casillas marcadas y la transición rechazada desde `edit --status`.
- Suite: **484** (461 tras el #36 y el #37; +7 del #34, +16 del #35).

### Medido (2026-10-17, vault de cliente RHFGroup con las dos reglas activas)
- `validate --all` contra la config real del cliente: **283 válidas, 0 errores** — las 37 notas `Task` de `tasks/` ya cumplen `responsable`/`vence`.
- Las reglas disparan, no solo en los tests: `Task` sin `responsable`, `responsable: pepito` (fuera de `values`) y `vence: '31 de octubre'` → tres errores que nombran el campo y los valores admitidos; `Plan` en `hecha` con una casilla abierta → error; `Plan` en `pendiente` con una marcada → aviso; `edit --status hecha` con casilla abierta → rechazo.
- `tasks list`: **37 tareas** con responsable, vencimiento e iniciativa heredada del `aplica`. `tasks promote` creó la `Task`, la enlazó con `aplica` y reemplazó la casilla por `- → [[tasks/…]]`.
- `OKF-Vault`, que no declara ninguna de las dos: **703/703 válidas y 9/9 health** antes y después del cambio.

### Fixed
- **Con el vault en un subdirectorio del repo git, el modo staged validaba 0 archivos y salía 0**: `git diff --name-only` emite rutas relativas a la RAÍZ del repo, así que `vault / f` no existía, el bucle lo descartaba con un `continue` mudo y el hook de pre-commit no podía bloquear nada (issue #37). Ahora `cli/gitutil.staged_paths` pide las rutas con `--relative` (relativas al vault y sin los staged de fuera de él) y `validate` avisa en stderr cuando no puede listar los staged o cuando un path no existe bajo el vault, en vez de callar. Misma clase cerrada en los otros consumidores de git: `stamp --staged` (no renovaba ni restauraba el timestamp: `HEAD:<rel>` no resolvía), el guard de pérdida de contenido de `validate` (`git show HEAD:`), `git log --name-only` del índice de fechas de `health`/`stale` y `git status` de los archivos sin commitear (que ahora recorta el prefijo del vault y descarta lo de fuera). Tests: +5 en `tests/test_validate_staged_vault_en_subdirectorio.py` (461 en la suite); 4 fallan sin el cambio.
- **`validate` en modo staged (el hook) no respetaba `exclude.dirs`**: un `.md` staged dentro de un directorio excluido (`Templates/`, `scripts/`…) se validaba como concepto aunque `validate --all` lo saltara. Medido en el vault de cliente el 16-oct-2026: una plantilla de Obsidian en `Templates/Task.md` con `{{date}}` en el frontmatter bloqueaba el commit («invalid YAML»); `--all` daba 246/246. Ahora el modo staged aplica las mismas exclusiones que `find_md_files`. Test: `tests/test_validate_staged_respeta_exclude.py` (+2).
- **El modo staged filtraba solo tres nombres embebidos, y `--all` leía los excludes de un global mutable**: `exclude.files` del config se respetaba por accidente (dentro de `_validate_file`, que además contaba el archivo saltado como válido) y `--all`/el índice de wikilinks dependían de que `cli/main.py` hubiera pisado `EXCLUDE_FILES`/`EXCLUDE_DIRS` con `apply_config` — en uso de librería el criterio de «qué es un concepto» volvía a los defaults del módulo. Ahora las dos ramas reciben el `Config` y usan `_get_excludes`; sin `Config` el comportamiento es el anterior. Tests: +3 en `tests/test_validate_staged_respeta_exclude.py` (456 en la suite).

---
## [2026-10-16] — v0.4.26

`okf board` (PR #32) y el detector de obsolescencia con una sola vara.

### Added
- **`okf board [--out] [--iniciativa X] [--no-tasks]`**: el vault como JSON de tablero en una sola pasada — `columnas` (los estados de cada capa según `types.states`), `tarjetas` (un concepto tipado por tarjeta, fuera de la capa de registro, con capa, status, `iniciativa`, casillas hechas/total, edad y nivel de `stale`), `tareas` (casillas abiertas con antigüedad, reutiliza `search.find_todos`) y `salud` (conteo por capa y estado). `--iniciativa` filtra por el campo; sin esquema de estados sigue funcionando con una capa `sin-esquema` y los status observados (PR #32, Medardo Rafael Hernandez Franco; 7 tests).

### Fixed
- **`dashboard_snapshot` no le pasaba `config` a `collect_stale`**: el detector caía en los literales legacy (señal 3 = `propuesta`, señal 6 = Decision) mientras `stale` y `board` leían el esquema del vault. Medido en `OKF-Vault` con el esquema activo: **72 de 700 conceptos divergían** — el panel del DashboardView daba ATTENTION donde el CLI daba STALE—. Misma clase que el #21, el #29 y el #30.
- **`collect_stale(vault, config=cfg)` ignoraba la lista de exentos del vault** (`types.exempt_from_no_backlinks`) y usaba la embebida en silencio: el resultado dependía de CÓMO se llamaba a la función y no de la config. Ahora, con `config`, manda la lista del vault.

### Medido (2026-10-16, OKF-Vault con los dos strict activos)
- `board`: **565 tarjetas, 271 tareas, cuatro capas** (conocimiento 4 estados, compromiso 4, trabajo 5, infra 3).
- El desajuste `salud.por_estado` vs `columnas` **no puede ocurrir** con `types.states.strict: true`: toda tarjeta tiene un estado declarado por su capa (0 casos, 0 tarjetas sin status).
- `stale` y el panel: **0 divergencias** entre 700 conceptos (eran 72). STALE 6, ATTENTION 291.

### Tests
- **451** (443 en v0.4.25): +7 del board, +1 de paridad panel/CLI.

---
## [2026-10-16] — v0.4.25

El destino de una arista se resuelve como el resto del CLI (PR #33).

### Fixed
- **`validate` resolvía el destino de una arista con `vault / target` a secas**, sin el fallback de `.md`, mientras el resto del CLI acepta targets sin extensión (`resolve_link`). Un link `target: decisions/otra-nota` —como los escribe el vault OKF— no resolvía: el tipo del destino quedaba en `'?'` y **el par era imposible de declarar** en `valid_pairs`, así que `graph.strict_pairs: true` era inalcanzable en un vault que escribe targets sin extensión. Medido en `OKF-Vault`: **6 de 148** aristas atípicas estaban en ese estado (`Harness aplica ?`, `Plan aplica ?`, `Project aplica ?`, `Harness depende ?`, `Skill depende ?`) y ninguna se podía declarar.
- La misma resolución estaba mal en la rama de **`corrige`**: no leía el destino, así que no comprobaba que estuviera en su estado de reemplazo.

### Changed
- Las dos ramas usan `_resolve_edge_target`, que reusa `resolve_link`: la MISMA resolución que `graph` y que el chequeo de wikilinks (misma clase que el #29 y el #30 — una sola resolución por concepto), con fallback al path pelado y a `.md` para targets fuera de alcance.

### Tests
- `tests/test_validate_edge_target_sin_extension.py` (+4): par declarado con target sin `.md` no avisa; par atípico con target sin `.md` reporta el tipo real (no `'?'`); con `strict_pairs` es error; un target inexistente sigue siendo `'?'`.
- Suite: **443** (439 en v0.4.24).

**Verificado de punta a punta:** con este fix, `OKF-Vault` declaró sus 143 pares y activó `graph.strict_pairs: true` + `types.states.strict: true` con `validate --all` en **701/701, 0 avisos**.

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