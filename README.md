# OKF MCP Server

MCP (Model Context Protocol) server for OKF vaults. Exposes 17 tools that let AI agents (Claude Code, Hermes, Cursor, etc.) query, create, and analyze concepts in a knowledge vault — no direct filesystem access needed.

## What is OKF?

OKF (Open Knowledge Format) is a convention for representing knowledge as markdown files with YAML frontmatter. The minimum contract is:

```markdown
---
type: Decision
title: "My first decision"
description: "One-line summary explaining what this concept is"
---

Free-form markdown content. Use [[wikilinks]] to
connect concepts together.
```

Each `.md` file is a **concept**. Concepts link to each other with `[[wikilinks]]`, forming a navigable knowledge graph. The OKF MCP exposes that graph as semantic tools for AI agents.

## Quick Start

### 1. Prerequisites

- Python 3.12+
- `mcp` (FastMCP)

```bash
pip install mcp
```

### 2. Installation

```bash
git clone https://github.com/syberloop/mcp-okf.git
cd mcp-okf
pip install .
```

This installs the `okf-mcp` command in your PATH.

> The installer can also seed a fresh demo vault (clones `syberloop/okf-demo`) when
> the target directory doesn't exist. The demo ships with the Cognitive Trace
> plugin versioned inside the tree — no extra clone needed.

### 3. Updating an existing installation

```bash
./install.sh --update
```

Pulls the vault (if it's a git repo, e.g. a demo clone), rebuilds the Cognitive
Trace plugin if present, and reinstalls the MCP package. Safe on demo-seeded
installations. Use `--dry-run` to preview:

```bash
./install.sh --update --dry-run
```

After an update, restart your MCP client to load the new server code.

### 4. Configure your vault

Copy the example config to your vault root and edit it:

```bash
cp okf.config.example.yaml ~/my-vault/.okf.config.yaml
```

Edit the sections you need — at minimum, change `health.smoke_entry_point` to a concept that exists in your vault. All keys are optional; if something is missing, defaults are used.

Config resolution order:
1. `--config <path>` in the CLI
2. `$OKF_CONFIG` environment variable
3. `<vault>/.okf.config.yaml` (next to your `.md` files)
4. `~/.config/okf/config.yaml` (global)
5. Embedded defaults

Vault resolution order:
1. `--vault <path>` in the CLI
2. `$OKF_VAULT` environment variable
3. `~/OKF-Vault` (default)

### 5. Run the health check

```bash
python3 -m cli health --vault ~/my-vault
```

If you see `Health: 9/9`, your vault is ready.

### 6. Register the MCP with your agent

**Claude Code** (`~/.claude/.mcp.json`):

```json
{
  "mcpServers": {
    "okf": {
      "command": "okf-mcp",
      "args": []
    }
  }
}
```

**Hermes Agent** (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  okf:
    command: okf-mcp
    args: []
```

**Cursor** (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "okf": {
      "command": "okf-mcp",
      "args": []
    }
  }
}
```

> If using `python3 server.py` instead of `okf-mcp`, the path in `args` MUST be absolute. MCP clients do not resolve relative paths in `args`, even with the `cwd` field.

**OpenCode** (`~/.config/opencode/opencode.json`):

```json
{
  "mcp": {
    "okf": {
      "type": "local",
      "command": ["okf-mcp"]
    }
  }
}
```

> OpenCode uses a different config structure than Claude Code/Cursor. The server is configured directly under `mcp`, not inside `mcpServers`. Each entry requires `"type": "local"` and `command` as an array. Use `opencode mcp add okf -- okf-mcp` to add it automatically.

## Available Tools

| Tool | Description |
|---|---|
| `okf_traverse` | Semantic graph traversal — **primary query tool** |
| `okf_read` | Read a concept's full body |
| `okf_search` | FTS5 search — **fallback**, prefer traverse |
| `okf_graph` | Graph analysis: orphans, hubs, backlinks, clusters |
| `okf_new` | Create a new concept with validated OKF frontmatter |
| `okf_health` | Vault health check (9 checks) |
| `okf_index` | Regenerate index.md and log.md |
| `okf_touch` | Read statistics |
| `okf_review` | Concepts with past-due review_on (cybernetic loop) |
| `okf_stale` | Semantic staleness detector |
| `okf_session_metrics` | Aggregated session metrics |
| `okf_analytics` | Analytics queries over trace events |
| `okf_graph_command` | Commands to the Cognitive Trace plugin in Obsidian |
| `okf_graph_suggest_edge_types` | Suggest typed edge types for wikilinks |
| `okf_graph_impact` | Ontological impact analysis |
| `okf_file_info` | Concept file metadata (created/updated dates) |
| `okf_trace` | Reference trace across all ecosystem layers |

## The Semantic Graph

The primary tool is `okf_traverse`. Unlike keyword search, traversal follows the graph's wikilinks:

```
okf_traverse("my-concept", depth=2)
→ concept frontmatter
→ outgoing linked concepts (wikilinks)
→ incoming linked concepts (backlinks)
→ corrected concepts (cyber.corrects)
→ typed edges: extends, refines, grounds, applies, depends, corrects
```

This lets the agent **reason about knowledge structure**, not just match text.

## Concept Types

Types are predefined in the configuration file:

| Type | Purpose | Has cyber |
|---|---|---|
| `Decision` | Architectural or policy decision | Yes |
| `Plan` | Execution plan or roadmap | Yes |
| `Project` | Project with vision, status, and components | Yes |
| `Insight` | Observation, pattern detected, implication | Yes |
| `Framework` | Conceptual framework | No |
| `Lesson` | Lesson from experience | No |
| `Tool` | Tool or script | No |
| `Spec` | Technical specification | No |
| `System` | Ecosystem runtime configuration | Yes |
| `Agent` | AI agent definition | Yes |
| `Skill` | Hermes skill catalog | No |
| `Workflow` | Procedure or workflow | No |
| `Criterion` | Decision rule or criterion | No |
| `Session` | Session summary | No |
| `Research` | Research or paper | No |

The `cyber` block (sensor → target_metric → review_on) is optional and only applies to types marked with Yes.

## Typed Edges

Concepts can declare edges with explicit semantics in the `links:` frontmatter field:

| Type | Meaning |
|---|---|
| `extends` | A adds a dimension to B |
| `refines` | A narrows or clarifies B |
| `grounds` | A is the theoretical basis for B |
| `applies` | A implements B |
| `depends` | A requires B to exist |
| `corrects` | A replaces/invalidates part of B (only if B is deprecated) |

## Advanced Configuration

### Cybernetic Loop

If you use the `cyber` block in your concepts, the MCP can close the loop automatically:

1. `okf_new --cyber` assigns `review_on: +14d`
2. `okf_review` lists concepts with past-due reviews
3. An autonomous agent evaluates the outcome (success/failure) and updates

### Staleness Detection

`okf_stale` evaluates 7 semantic staleness signals:
1. Old timestamp (>90 days without meaningful changes)
2. Few reads (<2 in 90 days)
3. Ghost proposal (>30 days in a pending status — `propuesta`, or the
   schema's `pending` states when the type declares one)
4. Orphan (no incoming or outgoing wikilinks)
5. No commits (>180 days without commits touching the file)
6. Type that requires a status and has none (Decision, or any type with a
   state schema)
7. Outdated description (checkboxes complete but description still references problems)

Adjust thresholds in `stale.*` of your `.okf.config.yaml`.

### State schema per type

`status` was free text. A vault can declare a state machine per layer of
types in `types.states`; the CLI then checks it in `validate` (warning by
default, error with `strict: true`), seeds `new` with the initial state,
and makes `edit --status` refuse transitions the schema does not allow
(`--force` skips the check for migrations). Types with `states: []` are
records and must not carry a status.

```yaml
types:
  states:
    strict: false
    schemas:
      conocimiento:
        types: [Research, Insight, Lesson]
        initial: vigente
        states: [vigente, en-revision, superada]
        transitions:
          vigente: [en-revision, superada]
          en-revision: [vigente, superada]
          superada: [en-revision]
        replaced: superada        # a `corrige` edge must point to this state,
                                  # and a note in it needs an incoming `corrige`
        pending: [en-revision]    # what `stale` signal 3 counts as unresolved
      trabajo:
        types: [Plan, Project]
        initial: pendiente
        states: [pendiente, en-curso, hecha, cancelada]
        done: [hecha]             # a note here must have no open checkboxes
      registro:
        types: [Session]
        states: []
```

`done` names the states that mean "finished": a note in one of them with
open `- [ ]` checkboxes is a problem (warning, error with `strict`), and
`edit --status` refuses the move unless `--force`. A note still in the
initial state with checked boxes gets a warning. `cancelada` is deliberately
not in `done`: cancelling leaves things undone.

Without a schema nothing changes: the status is not checked and `corrige`
keeps its legacy substring heuristic (`deprecada` / `supercedida`).

### Edge pairs: strict and vault-declared

`graph.strict_pairs: true` turns an atypical (source type, target type) pair
into an error in `validate`, and `new`/`edit` refuse to write the edge.
`graph.inherit_default_pairs: false` drops the embedded `valid_pairs` (they
use one vault's vocabulary) so the vault declares every pair it uses in
`edge_types.<type>.valid_pairs`; an edge type with no declared pairs is
unrestricted, as `corrige` always was.

### `okf board` — the vault as a read-only board

`okf board` writes one JSON with the vault as a board: `columnas` (the
states of each layer from the state schema), `tarjetas` (one per typed
concept outside the record layer, with layer, status, `iniciativa`,
checkbox progress, age, `stale` level, `responsable`/`vence` when the note
carries them, its typed outgoing edges as `relaciones` with the target's title,
and `entrantes`, how many typed edges point at it), `tareas` (open checkboxes with
their age, as `search --todos --aging` computes them) and `salud` (counts
per layer and state). `--iniciativa X` keeps only cards and tasks whose
`iniciativa` field holds `X`; `--out` writes to a file. A GitHub Action can
run it on every push and publish the JSON for a Worker to serve — the page
never parses the vault.

### Field schema per type and `okf tasks`

A checkbox is a checklist item: it lives inside its note and has no owner or
due date. Work that has an owner or a date is a task, and a task is a note of
the type the vault declares in `types.task` (default `Task`), with a status
from the work layer and `responsable` / `vence` as fields. The vault declares
what those fields accept in `types.fields` (warning by default, error with
`strict: true`); `validate`, `new` and `edit` enforce it:

```yaml
types:
  task: Task
  fields:
    strict: true
    schemas:
      Task:
        responsable: {required: true, values: [rafael, jaime, claude, skynet]}
        vence: {format: date}          # ISO YYYY-MM-DD
```

Relations stay as edges: `aplica` → the Plan or Project the task belongs to,
`depende` → what blocks it (declare the pairs in `edge_types`).

```bash
okf tasks list [--responsable X] [--iniciativa X] [--status X] [--vencidas] [--json]
okf tasks promote plans/mi-plan --match "Firmar" --responsable rafael --vence 2026-11-01
```

`list` shows overdue tasks first; the initiative is the task's own
`iniciativa` or, failing that, the one of the note it applies to. `promote`
turns an open checkbox into a task note (`aplica` → the source note) and
replaces the checkbox with a link to the task, so `search --todos` and `board`
stop counting it twice. Creating, moving and assigning need no extra command:
`new --type Task --field responsable=…`, `edit --status …`, `edit --field …`.

### `okf stamp` — timestamp follows content

`timestamp` is the last meaningful change. `edit` refreshes it only when the
body, title or description change, and `okf stamp --staged` applies the same
rule in the pre-commit hook against HEAD: a body change with an untouched
timestamp gets today's date; a metadata-only change (status, tags, links)
with a refreshed timestamp gets HEAD's date back. Add it to your hook before
`validate`:

```bash
python3 -m cli --vault "$VAULT" stamp --staged
```

## Development

```bash
# Run tests
cd tests && python3 test_server.py

# Test the server manually (JSON-RPC stdio)
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | python3 server.py
```

## License

MIT

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed, date-versioned list of added features, fixes, and changes.
