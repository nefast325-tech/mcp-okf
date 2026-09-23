"""Entry point for the OKF Vault CLI.

Usage:
    python3 -m cli <command> [options]

Commands:
    search      Search concepts and pending tasks
    read        Read a concept (+ auto-increment reads)
    traverse    Semantic traversal: concept frontmatter + neighborhood
    graph       Analyze the wikilink graph
    health      Complete integrity check
    index       Regenerate index.md and log.md
    new         Create a new concept
    touch       Read statistics
    dashboard   Generate dashboard.md
    dashboard-snapshot  Generate dashboard.json + daily snapshot
    stale       Semantic staleness detector
    review      Cybernetic review (expired review_on)
    audit       Audit frontmatter of all concepts
    validate    Strict YAML pre-commit validation (no regex fallback)
"""

import argparse
import sys
import os
import time
from pathlib import Path

from cli import __version__


def build_parser():
    """Builds the main parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="okf",
        description="Unified CLI for the OKF vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 -m cli search --todos\n"
               "  python3 -m cli read specs/okf-v01\n"
               "  python3 -m cli graph stats\n"
               "  python3 -m cli health --json\n"
               "  python3 -m cli new --type Decision --title \"...\" --description \"...\"",
    )
    parser.add_argument("--vault", type=str, default=None,
                        help="Path to vault (default: $OKF_VAULT or ~/OKF-Vault)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to .okf.config.yaml (default: <vault>/.okf.config.yaml)")
    parser.add_argument("--scope", type=str, default=None,
                        help="Subárbol legible (modo público). Default: $OKF_SCOPE o vault completo")
    parser.add_argument("--version", action="version",
                        version=f"okf {__version__}",
                        help="Show the installed version and exit")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # ── search ──
    sp_search = subparsers.add_parser("search", help="Search concepts and tasks")
    sp_search.add_argument("--query", type=str, default=None,
                           help="Filter by text in title, description and tags")
    sp_search.add_argument("--type", dest="filter_type", type=str, default=None,
                           help="Filter by type (Decision, Project, Spec, etc.)")
    sp_search.add_argument("--status", dest="filter_status", type=str, default=None,
                           help="Filter by status (propuesta, aplicada, etc.)")
    sp_search.add_argument("--todos", action="store_true", default=False,
                           help="Search pending - [ ] tasks")
    sp_search.add_argument("--all", action="store_true", default=False,
                           help="With --todos: include completed - [x]")
    sp_search.add_argument("--aging", action="store_true", default=False,
                           help="With --todos: show task age")
    sp_search.add_argument("--include-specs", action="store_true", default=False,
                           help="With --todos: include type=Spec checkboxes (acceptance criteria, not tasks)")
    sp_search.add_argument("--include-skills", action="store_true", default=False,
                           help="With --todos: include type=Skill checkboxes (self-audit checklists, not tasks)")
    sp_search.add_argument("--include-sessions", action="store_true", default=False,
                           help="With --todos: include type=Session checkboxes (that day's snapshot, not a live backlog)")
    sp_search.add_argument("--include-handoffs", action="store_true", default=False,
                           help="With --todos: include type=Handoff checkboxes (frozen session state, not a live backlog)")
    sp_search.add_argument("--with-graph", action="store_true", default=False,
                           help="Show typed edges between results at the end")
    sp_search.add_argument("--json", action="store_true", default=False,
                           help="JSON output")
    sp_search.add_argument("--cyber-field", type=str, default=None,
                           help="Filter by cyber field: (sensor, outcome, etc.)")
    sp_search.add_argument("--cyber-value", type=str, default=None,
                           help="Value for --cyber-field")
    sp_search.add_argument("--review-due", action="store_true", default=False,
                           help="Only concepts with cyber.review_on <= today")
    sp_search.add_argument("--since", type=str, default=None,
                           help="Filter by timestamp >= date (ISO 8601, inclusive)")
    sp_search.add_argument("--until", type=str, default=None,
                           help="Filter by timestamp <= date (ISO 8601, inclusive)")

    # ── read ──
    sp_read = subparsers.add_parser("read", help="Read a concept")
    sp_read.add_argument("target", type=str, nargs="?", help="Concept to read")
    sp_read.add_argument("--offset", type=int, default=1, help="Start line")
    sp_read.add_argument("--limit", type=int, default=500, help="Max lines")
    sp_read.add_argument("--no-touch", action="store_true", default=False,
                         help="Do not increment reads counter")

    # ── traverse ──
    sp_traverse = subparsers.add_parser("traverse",
                                        help="Semantic graph traversal")
    sp_traverse.add_argument("target", type=str, nargs="?",
                             help="Origin concept (optional if --seeds is used)")
    sp_traverse.add_argument("--seeds", type=str, nargs="+", default=None,
                             help="Multiple origin concepts (union + deduplication)")
    sp_traverse.add_argument("--depth", type=int, default=1,
                             help="Traversal depth (default: 1)")
    sp_traverse.add_argument("--direction", type=str, default="both",
                             choices=["both", "out", "in"],
                             help="Direction: both, out, in (default: both)")
    sp_traverse.add_argument("--no-cyber", action="store_true", default=False,
                             help="Do not follow cyber.corrects/corrected_by edges")
    sp_traverse.add_argument("--json", action="store_true", default=False,
                             help="JSON output")
    sp_traverse.add_argument("--edge-type", dest="edge_type", type=str, default=None,
                             choices=["extiende", "refina", "fundamenta",
                                      "aplica", "depende", "corrige"],
                             help="Declare the explored ontological type (annotation; does not filter)")
    sp_traverse.add_argument("--filter", action="store_true", default=False,
                             help="With --edge-type: exclude edges not of that type")

    # ── graph ──
    sp_graph = subparsers.add_parser("graph", help="Analyze the wikilink graph")
    sp_graph.add_argument("subcommand", nargs="?", type=str,
                          help="stats|orphans|hubs|backlinks|deps|tags|bridges|cluster|path|dump|dirs|types|impact|suggest-edge-types")
    sp_graph.add_argument("args", nargs="*", type=str,
                          help="Additional arguments for the subcommand")
    sp_graph.add_argument("--edge-type", dest="edge_type", type=str, default=None,
                          help="Filter backlinks/deps by edge type "
                               "(extiende, refina, fundamenta, aplica, depende, corrige)")
    sp_graph.add_argument("--apply", action="store_true", default=False,
                          help="With suggest-edge-types: apply HIGH confidence suggestions")
    sp_graph.add_argument("--dry-run", action="store_true", default=False,
                          help="With suggest-edge-types --apply: preview without writing")
    sp_graph.add_argument("--min-score", dest="min_score", type=float, default=None,
                          help="With suggest-edge-types: minimum score to apply "
                               "(semantic scoring 0.0-1.0; default: config graph.suggest_min_score)")

    # ── health ──
    sp_health = subparsers.add_parser("health", help="Complete health check")
    sp_health.add_argument("--strict", action="store_true", default=False,
                           help="Warnings cause exit 1")
    sp_health.add_argument("--json", action="store_true", default=False,
                           help="JSON output")

    # ── index ──
    sp_index = subparsers.add_parser("index", help="Regenerate index.md and log.md")

    # ── new ──
    sp_new = subparsers.add_parser("new", help="Create a new concept")
    sp_new.add_argument("--type", dest="concept_type", required=True,
                        help="Tipo: Decision, Plan, Project, Insight, MarcoTeorico, "
                             "LeccionAprendida, Tool, Spec")
    sp_new.add_argument("--title", default=None,
                        help="Descriptive title. Optional when --filename is "
                             "given; when omitted, no 'title:' field is written "
                             "(formats like the session summaries in "
                             "sesiones/ have no title)")
    sp_new.add_argument("--description", required=True, help="One-line summary")
    sp_new.add_argument("--filename", default=None,
                        help="Exact file name to use instead of the title slug "
                             "(e.g. --filename sesion-20260908_172301_5ef849fd.md, "
                             "underscores preserved). '.md' is appended if "
                             "missing. Must be a bare name, no path separators. "
                             "For type=Skill it names the skill directory "
                             "(SKILL.md stays fixed)")
    sp_new.add_argument("--field", dest="fields", action="append", default=None,
                        help="Extra frontmatter field 'key=value' (repeatable). "
                             "Top-level only; use `edit --field` for nested keys")
    sp_new.add_argument("--no-timestamps", dest="omit_timestamps",
                        action="store_true", default=False,
                        help="Do not write timestamp:/created: (formats that "
                             "don't carry them, like the session summaries)")
    sp_new.add_argument("--tags", default=None, help="Comma-separated tags")
    sp_new.add_argument("--status", default=None,
                        help="Status: propuesta, aplicada, activo, etc.")
    sp_new.add_argument("--resource", default=None, help="External canonical URI")
    sp_new.add_argument("--cyber", action="store_true", default=False,
                        help="Add cyber: block")
    sp_new.add_argument("--dry-run", action="store_true", default=False,
                        help="Preview without writing")
    sp_new.add_argument("--body", default=None,
                        help="Body content (replaces the default template)")
    sp_new.add_argument("--body-file", default=None,
                        help="File with body content")
    sp_new.add_argument("--link", dest="links", action="append", default=None,
                        help="Typed link: 'target:type' (repeatable). "
                             "e.g. --link frameworks/tp3:extiende")
    sp_new.add_argument("--entity", default=None,
                        help="Entity slug for by_entity types (e.g.: type=Cliente "
                             "entity=Lopcort → clientes/Lopcort/<slug>.md). "
                             "Required when the type groups by entity "
                             "(types.by_entity in config).")
    sp_new.add_argument("--force", action="store_true", default=False,
                        help="Overwrite the concept if the slug already exists")

    # ── edit ──
    sp_edit = subparsers.add_parser("edit", help="Update an existing concept (merge)")
    sp_edit.add_argument("slug", type=str,
                         help="Concept path (e.g. 'decisions/mi-decision', "
                              "'mi-decision.md', or bare basename found in the graph)")
    sp_edit.add_argument("--title", default=None,
                         help="New title (frontmatter only; does not rename the file)")
    sp_edit.add_argument("--description", default=None,
                         help="New one-line summary (cannot be emptied)")
    sp_edit.add_argument("--tags", default=None,
                         help="Comma-separated tags (empty string clears)")
    sp_edit.add_argument("--status", default=None,
                         help="New status (empty string clears)")
    sp_edit.add_argument("--resource", default=None,
                         help="New canonical URI (empty string clears)")
    sp_edit.add_argument("--body", default=None,
                         help="New body content (replaces the current body)")
    sp_edit.add_argument("--body-file", default=None,
                         help="File with body content")
    sp_edit.add_argument("--link", dest="links", action="append", default=None,
                         help="Typed link 'target:type' (repeatable). "
                              "REPLACES the full links list")
    sp_edit.add_argument("--clear-links", action="store_true", default=False,
                         help="Remove all typed links")
    sp_edit.add_argument("--field", dest="fields", action="append", default=None,
                         help="Custom frontmatter field 'key=value' (repeatable). "
                              "Dotted keys set nested blocks (repo_state.commit=abc). "
                              "JSON for lists/dicts; empty value deletes the field")
    sp_edit.add_argument("--dry-run", action="store_true", default=False,
                         help="Preview without writing")

    # ── touch ──
    sp_touch = subparsers.add_parser("touch", help="Read statistics")
    sp_touch.add_argument("target", nargs="?", type=str, help="Concept to increment")
    sp_touch.add_argument("--all", action="store_true", default=False,
                          help="Show read stats for the entire vault")

    # ── migrate-reads ──
    sp_migrate = subparsers.add_parser(
        "migrate-reads",
        help="Move read counters from frontmatter to the local telemetry store "
             "(.okf/state/reads.jsonl, not versioned)")

    # ── trace ──
    sp_trace = subparsers.add_parser("trace", help="Trace references across the OKF ecosystem")
    sp_trace.add_argument("query", type=str, help="Search term")
    sp_trace.add_argument("--layers", type=str, default="vault,code,hooks,cron,agents",
                          help="Layers to trace (default: all)")

    # ── analytics ──
    sp_analytics = subparsers.add_parser("analytics", help="Analytics queries on Cognitive Trace")
    sp_analytics.add_argument("--query", type=str, default="most_visited",
                              help="Query type (most_visited, session_heatmap, tool_usage, edge_type_usage, "
                                   "daily_activity, node_timeline, error_summary, co_visited, "
                                   "read_ratio, session_diff, depth_stats, entry_points, prompts)")
    sp_analytics.add_argument("--limit", type=int, default=10,
                              help="Result limit (default 10)")
    sp_analytics.add_argument("--arg", type=str, default="",
                              help="Additional argument (slug for node_timeline/co_visited, "
                                   "'sessionA,sessionB' para session_diff)")
    sp_analytics.add_argument("--session-id", type=str, default="",
                              help="Filter by session (empty = current via $OKF_SESSION_ID)")

    # ── dashboard ──
    sp_dash = subparsers.add_parser("dashboard", help="Generate dashboard.md")

    # ── dashboard-snapshot ──
    sp_snap = subparsers.add_parser(
        "dashboard-snapshot",
        help="Generate dashboard.json + daily snapshot (DashboardView sensor)")
    sp_snap.add_argument("--source", type=str, default="manual",
                         choices=["manual", "post-commit-hook", "cron"],
                         help="Who triggered the snapshot (default: manual)")
    sp_snap.add_argument("--canvas", action="store_true", default=False,
                         help="Also generate a heat canvas around the most "
                              "visited node (sistema/mapas/)")
    sp_snap.add_argument("--session-a", type=str, default="",
                         help="Session Diff: primer session_id a comparar")
    sp_snap.add_argument("--session-b", type=str, default="",
                         help="Session Diff: segundo session_id a comparar "
                              "(requiere --session-a)")
    sp_snap.add_argument("--db", type=str, default=None,
                         help="Override the Cognitive Trace SQLite path "
                              "(default: config features.trace_db_path or "
                              "~/.hermes/cognitive-trace.db)")

    # ── stale ──
    sp_stale = subparsers.add_parser("stale", help="Semantic staleness detector")
    sp_stale.add_argument("--json", action="store_true", default=False,
                          help="JSON output")

    # ── session-metrics ──
    sp_sm = subparsers.add_parser("session-metrics", help="Aggregated session metrics")
    sp_sm.add_argument("--json", action="store_true", default=False,
                       help="JSON output")

    # ── review ──
    sp_review = subparsers.add_parser("review", help="Cybernetic review")
    sp_review.add_argument("--json", action="store_true", default=False,
                           help="JSON output")
    sp_review.add_argument("--count", action="store_true", default=False,
                           help="Count only")

    # ── audit ──
    sp_audit = subparsers.add_parser("audit", help="Audit frontmatter")

    # ── validate ──
    sp_validate = subparsers.add_parser("validate",
                                        help="Strict YAML pre-commit validation")
    sp_validate.add_argument("target", type=str, nargs="?", default=None,
                             help="Specific file to validate (default: staged)")
    sp_validate.add_argument("--all", action="store_true", default=False,
                             help="Validate all concepts in the vault")

    # ── file-info ──
    sp_file_info = subparsers.add_parser("file-info", help="Date metadata for a concept")
    sp_file_info.add_argument("--slug", type=str, required=True,
                              help="Concept slug (e.g. 'frameworks/tp3-cibernetico')")
    sp_file_info.add_argument("--json", action="store_true", default=False,
                              help="JSON output")

    # ── canvas ──
    from cli.commands.canvas import build_subparser as build_canvas_subparser
    build_canvas_subparser(subparsers)

    return parser


def main(argv=None):
    """Main entry point.

    Args:
        argv: Argument list (default: sys.argv[1:]).
              Useful for programmatic invocation: main(["search", "--todos"])
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Resolver vault path
    from cli.vault import resolve_vault_path
    vault = resolve_vault_path(getattr(args, "vault", None))

    if not vault.exists():
        print(f"Error: vault not found at {vault}", file=sys.stderr)
        print("Use --vault or set $OKF_VAULT to specify another path.", file=sys.stderr)
        sys.exit(1)

    # Cargar configuración externalizada
    from cli.config import Config
    config = Config(vault, cli_config_arg=getattr(args, "config", None))

    # Propagar exclusiones a las variables globales de vault.py (backward compat)
    from cli.vault import apply_config
    apply_config(config)

    # ── Política de acceso: scope de subárbol y modo solo-lectura ──
    from cli.access import is_readonly, resolve_scope, set_scope, WRITE_COMMANDS
    try:
        set_scope(resolve_scope(getattr(args, "scope", None)))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Asegurar que el directorio del CLI esté en path para imports
    cli_dir = Path(__file__).resolve().parent
    if str(cli_dir.parent) not in sys.path:
        sys.path.insert(0, str(cli_dir.parent))

    # Inicializar telemetría Cognitive Trace
    from cli.telemetry import init as telemetry_init, record as telemetry_record
    telemetry_init(vault, config)

    # Extraer params del namespace para telemetría (sin campos internos)
    _params = {k: v for k, v in vars(args).items()
               if k not in ("vault", "config", "scope", "command", "func") and v is not None
               and v != False and v != ""}

    # Despachar al comando con captura de stdout/stderr para telemetría
    import io as _io
    command = args.command

    # Modo solo-lectura: control duro sobre los comandos que escriben.
    if is_readonly() and command in WRITE_COMMANDS:
        print(f"✗ Modo solo-lectura (OKF_READONLY): '{command}' es un comando "
              f"de escritura y está bloqueado.", file=sys.stderr)
        sys.exit(1)

    _t0 = time.monotonic()
    _exit = 1
    _stdout_buf = _io.StringIO()
    _stderr_buf = _io.StringIO()
    _old_stdout, _old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _stdout_buf, _stderr_buf

    try:
        if command == "search":
            from cli.commands.search import run
            _exit = run(args, vault, config) or 0

        elif command == "read":
            from cli.commands.read import run
            _exit = run(args, vault, config) or 0

        elif command == "traverse":
            from cli.commands.traverse import run
            _exit = run(args, vault, config) or 0

        elif command == "graph":
            from cli.commands.graph import run
            _exit = run(args, vault, config) or 0

        elif command == "health":
            from cli.commands.health import run
            _exit = run(args, vault, config) or 0

        elif command == "index":
            from cli.commands.index import run
            _exit = run(args, vault, config) or 0

        elif command == "new":
            from cli.commands.new import run
            _exit = run(args, vault, config) or 0

        elif command == "edit":
            from cli.commands.edit import run
            _exit = run(args, vault, config) or 0

        elif command == "touch":
            from cli.commands.touch import run
            _exit = run(args, vault, config) or 0
        elif command == "migrate-reads":
            from cli.commands.migrate_reads import run
            _exit = run(args, vault, config) or 0
        elif command == "trace":
            from cli.commands.trace import run
            _exit = run(args, vault, config) or 0

        elif command == "analytics":
            from cli.commands.analytics import run
            _exit = run(args, vault, config) or 0

        elif command == "dashboard":
            from cli.commands.dashboard import run
            _exit = run(args, vault, config) or 0

        elif command == "dashboard-snapshot":
            from cli.commands.dashboard_snapshot import run
            _exit = run(args, vault, config) or 0

        elif command == "stale":
            from cli.commands.stale import run
            _exit = run(args, vault, config) or 0

        elif command == "session-metrics":
            from cli.commands.session_metrics import run
            _exit = run(args, vault, config) or 0

        elif command == "review":
            from cli.commands.review import run
            _exit = run(args, vault, config) or 0

        elif command == "audit":
            from cli.commands.audit import run
            _exit = run(args, vault, config) or 0

        elif command == "validate":
            from cli.commands.validate import run
            _exit = run(args, vault, config) or 0

        elif command == "file-info":
            from cli.commands.file_info import run
            _exit = run(args, vault, config) or 0

        elif command == "canvas":
            from cli.commands.canvas import run
            _exit = run(args, vault, config) or 0

        else:
            print(f"Unknown command: {command}", file=sys.stderr)
            parser.print_help()
            sys.exit(1)
    finally:
        sys.stdout, sys.stderr = _old_stdout, _old_stderr
        _captured_out = _stdout_buf.getvalue()
        _captured_err = _stderr_buf.getvalue()
        # Re-emitir output a stdout/stderr real
        if _captured_out:
            _old_stdout.write(_captured_out)
        if _captured_err:
            _old_stderr.write(_captured_err)
        _duration_ms = int((time.monotonic() - _t0) * 1000)
        telemetry_record(f"okf_{command}", _params, _exit, _duration_ms, _captured_out, _captured_err)

    sys.exit(_exit)


if __name__ == "__main__":
    main()
