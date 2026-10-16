"""Externalized configuration for MCP OKF.

Resolution chain:
    1. --config <path>               → explicit CLI argument
    2. $OKF_CONFIG             → environment variable
    3. <vault>/.okf.config.yaml       → next to the vault (95% of cases)
    4. ~/.config/okf/config.yaml → global user config (legacy: ~/.config/cli/)
    5. embedded defaults            → exact replica of Jaime's current configuration

Principle: hardcode fundamental domain rules, configure what changes.
The core contract (type + description + wikilinks) IS a fundamental rule → hardcoded.
Taxonomy, thresholds, feature flags → configurable.
"""

import os
from pathlib import Path
from typing import Any

from cli.edge_types import EDGE_TYPE_DEFINITIONS


def resolve_vault_path(value, vault):
    """Resuelve un path de features contra el vault: absoluto se usa tal cual,
    relativo se resuelve contra <vault> (configs portables, clonables)."""
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else Path(vault) / p


# ── Defaults embebidos ──────────────────────────────────────────────────────
# Réplica exacta de los hardcodeos actuales. Si no existe .okf.config.yaml,
# el comportamiento es idéntico al actual para Jaime.

DEFAULTS: dict[str, Any] = {
    "types": {
        "valid": [
            "Sistema", "Agente", "Decision", "Plan", "Project", "Insight",
            "MarcoTeorico", "LeccionAprendida", "Tool", "Spec", "Skill", "Workflow",
            "Mapa", "Criterio", "Sesion",
            "Research",
        ],
        "cyber": ["Sistema", "Agente", "Decision", "Plan", "Project", "Insight"],
        "excluded_cyber": ["MarcoTeorico", "LeccionAprendida", "Tool", "Spec"],
        # Tipos exentos de la señal 4 de `stale` («no backlinks»): conceptos
        # raíz naturalmente huérfanos. Es configurable porque el vocabulario
        # es del VAULT, no del código: OKF-Vault usa Sesion/Agente y el vault
        # del cliente usa Session (decisión 2026-10-15 — el mismo dato con dos
        # dialectos hacía que el sensor reportara de más). El default embebido
        # es el set del Criterio de obsolescencia (incluye Skill e Insight)
        # más los dialectos conocidos; cada vault declara el suyo en
        # `types.exempt_from_no_backlinks`.
        "exempt_from_no_backlinks": [
            "MarcoTeorico", "Spec", "Tool", "Agente", "Sesion", "Session",
            "Skill", "Insight",
        ],
        "by_entity": [],
        "directory": {
            "Sistema": "sistema",
            "Agente": "agentes",
            "Decision": "decisions",
            "Plan": "plans",
            "Project": "projects",
            "Insight": "insights",
            "MarcoTeorico": "frameworks",
            "LeccionAprendida": "historias",
            "Tool": "specs",
            "Spec": "specs",
            "Skill": "skills",
            "Workflow": "workflows",
            "Criterio": "criterios",
            "Sesion": "sesiones",
            "Research": "research",
            "Mapa": "mapas",
        },
    },
    "stale": {
        "timestamp_days": 90,
        "propuesta_days": 30,
        # Un `Insight` con status: propuesta no genera señal 3 hasta los 90
        # días: una hipótesis tiene más vida que una propuesta de acción
        # (decisión 2026-09-05, triaje automático del subgrafo dreaming).
        "propuesta_days_insight": 90,
        "no_commits_days": 180,
        "checkbox_ratio": 0.7,
        # Edad mínima (días) para que la señal "reads=0" cuente. El contador
        # sale del store de telemetría (.okf/state/reads.jsonl), no del
        # frontmatter: ausencia de entrada = 0 lecturas. Un concepto recién
        # creado y todavía no leído no está desconectado de la realidad.
        "reads_zero_min_days": 30,
        # Patrones de lenguaje de problema en description (regex, case-insensitive)
        # Señal 7 del detector: si la description habla de problemas y los
        # checkboxes del body están ≥70% completos, la description está obsoleta.
        "problem_patterns": [
            r"\bno\s+(existe|funciona|hay|tiene|está|implementado|conectado|almacena|notifica)\b",
            r"\broto[s]?\b",
            r"\bfalso[s]?\b",
            r"\bfalta[n]?\b",
            r"\bsin\s+(implementar|resolver|definir|conectar|backend)\b",
            r"\bpendiente[s]?\b",
            r"\bincompleto\b",
            r"\bplaceholder\b",
            r"\bclaims?\s+fals[oa]s?\b",
            r"\b404\b",
            r"\bCTAs?\s+rot[oa]s?\b",
            r"\bcero\s+métricas\b",
            r"\bno\s+está\b",
            r"\bse\s+pierden\b",
        ],
    },
    "cyber": {
        "review_days": 14,
    },
    "health": {
        "smoke_entry_point": "tp3-cibernetico",
    },
    "graph": {
        # Score semántico mínimo para aplicar sugerencias de aristas tipadas
        # (plan scoring-semantico, 2026-07-30). 0.0 = sin filtro.
        "suggest_min_score": 0.0,
    },
    "exclude": {
        "files": ["index.md", "log.md", "dashboard.md"],
        "dirs": [".git", ".obsidian", "node_modules", "Templates", "scripts", "references", "assets"],
    },
    "features": {
        "cognitive_trace": True,
        "plugin_hash_sync": True,
        # Paths para Cognitive Trace (solo relevantes si cognitive_trace: true)
        # - db_path: ruta al SQLite de eventos (default: ~/.hermes/cognitive-trace.db)
        # - jsonl_path: ruta al JSONL para el plugin de Obsidian
        #   (default: <vault>/.obsidian/plugins/cognitive-trace/event_log.jsonl)
        # Usar null para aceptar el default.
        "trace_db_path": None,
        "trace_jsonl_path": None,
    },
    "templates": {
        "Decision": (
            "## Contexto\n\n"
            "(¿Qué disparó esta decisión? ¿Qué problema resuelve?)\n\n"
            "## Decisión\n\n"
            "(¿Qué se decidió? ¿Qué alternativas se descartaron?)\n\n"
            "## Impacto\n\n"
            "(¿Qué cambia a partir de ahora?)\n"
        ),
        "Plan": (
            "## Objetivo\n\n"
            "(¿Qué se quiere lograr?)\n\n"
            "## Fases\n\n"
            "1.\n2.\n3.\n\n"
            "## Métricas\n\n"
            "(¿Cómo se mide el éxito?)\n"
        ),
        "Project": (
            "## Visión\n\n"
            "(¿Qué es este proyecto? ¿Por qué existe?)\n\n"
            "## Estado\n\n"
            "**Fase actual:** (planificación / desarrollo / producción / abandonado)\n\n"
            "## Componentes\n\n"
            "-\n-\n"
        ),
        "Insight": (
            "## Observación\n\n"
            "(¿Qué patrón o conexión se detectó?)\n\n"
            "## Implicación\n\n"
            "(¿Qué significa esto? ¿Qué acción sugiere?)\n\n"
            "## Verificación\n\n"
            "(¿Cómo se confirma o descarta este insight?)\n"
        ),
        "Sistema": (
            "## Qué hace\n\n"
            "(¿Qué configuración o comportamiento controla en el sistema agéntico?)\n\n"
            "## Dónde está\n\n"
            "(¿Archivo de config, variable de entorno, skill?)\n\n"
            "## Por qué\n\n"
            "(¿Qué problema resuelve? ¿Qué pasaría si se quitara?)\n\n"
            "## Relaciones\n\n"
            "- \n"
        ),
        "Agente": (
            "## Rol\n\n"
            "(¿Qué rol cumple este agente en el ecosistema?)\n\n"
            "## Capacidades\n\n"
            "- \n- \n\n"
            "## Configuración\n\n"
            "(¿Modelo, tools, skills asignados?)\n\n"
            "## AGENT.md / SOUL.md\n\n"
            "(¿Dónde está su archivo de definición?)\n"
        ),
        "Skill": (
            "## Identidad\n\n"
            "| Campo | Valor |\n"
            "|---|---|\n"
            "| **Nombre** | (nombre de la skill) |\n"
            "| **Repo** | (URL) |\n"
            "| **Ruta Hermes** | `~/.hermes/skills/<categoria>/<nombre>/` |\n\n"
            "## Qué hace\n\n"
            "(¿Qué problema resuelve esta skill? ¿Cuándo debe cargarla el agente?)\n\n"
            "## Dependencias\n\n"
            "- \n- \n\n"
            "## Archivos\n\n"
            "(¿SKILL.md, references/, scripts/?)\n"
        ),
        "Workflow": (
            "## Qué problema resuelve\n\n"
            "(¿Qué problema resuelve este workflow? ¿Cuándo debería un agente sugerirlo?)\n\n"
            "## Arquitectura\n\n"
            "(Componentes abstractos: sensor → criterio → clasificación → acción)\n\n"
            "## Procedimiento\n\n"
            "1. \n2. \n3. \n\n"
            "## Criterios de calidad\n\n"
            "(¿Cómo saber si el workflow está funcionando?)\n\n"
            "## Instanciación en OKF\n\n"
            "(Referencia a la instancia concreta en nuestro ecosistema)\n"
        ),
    },
    # Vocabulario de aristas tipadas (decisión 2026-08-10: configurable, no fijo).
    # Los defaults viven en cli.edge_types.EDGE_TYPE_DEFINITIONS. Aquí se
    # declaran SOLO los overrides del vault: valid_pairs adicionales se unen
    # a los defaults (no reemplazan), y un edge_type nuevo entra completo.
    "edge_types": {},
}


class Config:
    """Configuration for MCP OKF, resolved from file or defaults."""

    def __init__(self, vault: Path, cli_config_arg: str | None = None):
        self._vault = vault
        self._data: dict[str, Any] = {}
        self._load(cli_config_arg)

    def _load(self, cli_config_arg: str | None) -> None:
        """Resolves the config chain and merges over defaults."""
        import yaml

        config_path = self._resolve_path(cli_config_arg)
        if config_path:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                self._data = self._deep_merge(DEFAULTS, user_config)
            except Exception:
                # Broken YAML or unreadable file → use defaults
                self._data = DEFAULTS
        else:
            self._data = DEFAULTS

    def _resolve_path(self, cli_config_arg: str | None) -> Path | None:
        """Resolves the config file path in chain."""
        # 1. Argumento explícito del CLI
        if cli_config_arg:
            path = Path(cli_config_arg)
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.exists():
                return path.resolve()

        # 2. Variable de entorno
        env_config = os.environ.get("OKF_CONFIG")
        if env_config:
            path = Path(env_config).expanduser()
            if path.exists():
                return path.resolve()

        # 3. Raíz del vault (.okf.config.yaml, oculto)
        vault_config = self._vault / ".okf.config.yaml"
        if vault_config.exists():
            return vault_config

        # 4. Global del usuario. La ruta documentada es ~/.config/okf/;
        # ~/.config/cli/ se mantiene como legacy para no romper instalaciones
        # que hayan seguido el codigo en vez del docstring.
        for parent in ("okf", "cli"):
            global_config = Path.home() / ".config" / parent / "config.yaml"
            if global_config.exists():
                return global_config

        # 5. Sin archivo → defaults
        return None

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """Recursive merge: override only sets defined keys."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    # ── Acceso tipado ───────────────────────────────────────────────────────

    @property
    def types_valid(self) -> list[str]:
        return list(self._data["types"]["valid"])

    @property
    def types_cyber(self) -> list[str]:
        return list(self._data["types"]["cyber"])

    @property
    def types_excluded_cyber(self) -> set[str]:
        return set(self._data["types"]["excluded_cyber"])

    @property
    def types_exempt_from_no_backlinks(self) -> set[str]:
        """Tipos exentos de la señal 4 de `stale` («no backlinks»).

        Configurable por vault (config `types.exempt_from_no_backlinks`): el
        vocabulario de tipos es del vault, no del código. Si la config no lo
        declara, vale el default embebido.
        """
        return set(self._data["types"].get(
            "exempt_from_no_backlinks",
            DEFAULTS["types"]["exempt_from_no_backlinks"],
        ))

    @property
    def types_by_entity(self) -> set[str]:
        """Types that group by entity (one subdirectory per instance).

        Declared in config 'types.by_entity' (e.g.: ['Cliente'] → the CLI
        creates clientes/<entity>/<slug>.md instead of clientes/<slug>.md).
        Decision 2026-08-10.
        """
        by_entity = self._data["types"].get("by_entity", [])
        return set(by_entity)

    @property
    def types_directory(self) -> dict[str, str]:
        return dict(self._data["types"]["directory"])

    def edge_type_definitions(self) -> dict:
        """Effective edge type definitions: embedded defaults + config overrides.

        Resolution (decisión 2026-08-10 — el vocabulario de aristas es
        configurable, no fijo):
        - valid_pairs declarados en config se UNEN a los defaults del tipo
          (no reemplazan — permite agregar pares sin reescribir la lista).
        - Un edge_type nuevo en config entra completo (definición propia).
        - Propiedades escalares (description, transitive, inverse) se
          sobreescriben si el usuario las declara.

        Returns:
            dict: EDGE_TYPE_DEFINITIONS-compatible dict.
        """
        import copy

        result = copy.deepcopy(EDGE_TYPE_DEFINITIONS)
        overrides = self._data.get("edge_types", {}) or {}
        for etype, defn in overrides.items():
            if not isinstance(defn, dict):
                continue
            if etype in result:
                extra_pairs = defn.get("valid_pairs", [])
                if isinstance(extra_pairs, list) and extra_pairs:
                    existing = [
                        tuple(p) for p in result[etype].get("valid_pairs", [])
                    ]
                    for pair in extra_pairs:
                        t = tuple(pair)
                        if t not in existing:
                            existing.append(t)
                    result[etype]["valid_pairs"] = existing
                for key, value in defn.items():
                    if key != "valid_pairs":
                        result[etype][key] = value
            else:
                # Edge type nuevo: entra completo (valid_pairs normalizado
                # a tuplas para que el lookup de pares funcione — mismo
                # tratamiento que el branch de unión arriba).
                new_defn = copy.deepcopy(defn)
                pairs = new_defn.get("valid_pairs", [])
                if isinstance(pairs, list):
                    new_defn["valid_pairs"] = [tuple(p) for p in pairs]
                result[etype] = new_defn
        return result

    @property
    def stale_timestamp_days(self) -> int:
        return int(self._data["stale"]["timestamp_days"])

    @property
    def stale_propuesta_days(self) -> int:
        return int(self._data["stale"]["propuesta_days"])

    @property
    def stale_propuesta_days_insight(self) -> int:
        """Umbral de la señal 3 para `type: Insight` (default 90 días)."""
        return int(self._data["stale"].get("propuesta_days_insight", 90))

    @property
    def stale_no_commits_days(self) -> int:
        return int(self._data["stale"]["no_commits_days"])

    @property
    def stale_checkbox_ratio(self) -> float:
        return float(self._data["stale"]["checkbox_ratio"])

    @property
    def stale_reads_zero_min_days(self) -> int:
        return int(self._data["stale"].get("reads_zero_min_days", 30))

    @property
    def stale_problem_patterns(self) -> list[str]:
        return list(self._data["stale"].get("problem_patterns", []))

    @property
    def cyber_review_days(self) -> int:
        return int(self._data["cyber"]["review_days"])

    @property
    def health_smoke_entry_point(self) -> str:
        return str(self._data["health"]["smoke_entry_point"])

    @property
    def graph_suggest_min_score(self) -> float:
        return float(self._data["graph"].get("suggest_min_score", 0.0))

    @property
    def exclude_files(self) -> set[str]:
        return set(self._data["exclude"]["files"])

    @property
    def exclude_dirs(self) -> set[str]:
        return set(self._data["exclude"]["dirs"])

    @property
    def features_cognitive_trace(self) -> bool:
        return bool(self._data["features"]["cognitive_trace"])

    @property
    def features_plugin_hash_sync(self) -> bool:
        new_flag = self._data["features"].get("plugin_hash_sync")
        if new_flag is not None:
            return bool(new_flag)
        # backward compat: fallback al flag viejo si existe
        old_flag = self._data["features"].get("cognitive_trace")
        return bool(old_flag) if old_flag is not None else True

    def get_template(self, concept_type: str) -> str:
        """Returns the body template for a type, or a generic one if absent."""
        templates = self._data.get("templates", {})
        if isinstance(templates, dict):
            tmpl = templates.get(concept_type, "")
            if tmpl:
                return str(tmpl)
        return "## Contexto\n\n(Contenido)\n"
