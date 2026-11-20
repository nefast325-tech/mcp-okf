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

If your vault is a git repository, ignore OKF's local state. It holds the note
cache, read counters and edit locks — per machine, never versioned:

```bash
echo ".okf/" >> ~/my-vault/.gitignore
```

### 5. Run the health check

```bash
python3 -m cli health --vault ~/my-vault
```

If every check passes (`Health: N/N — all clear`), your vault is ready.

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

These are the names the server registers. MCP clients usually show them with a
prefix (in Claude Code, `mcp__okf__traverse`). Tools marked ✏️ write to the
vault and are not registered with `--readonly`.

| Tool | Description |
|---|---|
| `traverse` | Semantic graph traversal — **primary query tool**. `edge_type` is required; capped at `traverse.budget` nodes (100) unless `budget` says otherwise |
| `read` | Read a concept (frontmatter + body); reports the note's revision for `edit` |
| `search` | Text search ranked by relevance, 20 results by default — **fallback**, prefer traverse. Also filters by type, status, dates and cyber fields |
| `todos` | Pending `- [ ]` tasks, grouped by project |
| `new` ✏️ | Create a concept with validated frontmatter (create-only) |
| `edit` ✏️ | Update a concept (merge semantics) — the only way to change an existing note |
| `graph` | Graph analysis: orphans, hubs, backlinks, deps, clusters, paths |
| `graph_impact` | Which nodes to review if this one changes |
| `graph_suggest_edge_types` ✏️ | Suggest (and optionally apply) types for untyped wikilinks |
| `health` | Vault health check |
| `index` ✏️ | Regenerate `index.md` and `log.md` |
| `stale` | Semantic staleness detector |
| `review` | Concepts with past-due `review_on` (cybernetic loop) |
| `touch` ✏️ | Read statistics (local read counters) |
| `file_info` | Concept file metadata (created/updated dates) |
| `canvas` ✏️ | Generate, lay out and validate Obsidian `.canvas` maps |
| `session_metrics` | Aggregated session metrics |
| `analytics` ✏️ | Analytics queries over trace events |
| `graph_command` ✏️ | Commands to the Cognitive Trace plugin in Obsidian |
| `trace` ✏️ | Reference trace across all ecosystem layers |

### How an agent should use them

1. **Find by structure first.** Read `index.md` files and use `traverse` with the
   relation you are exploring (`extiende`, `fundamenta`, `aplica`, `depende`,
   `corrige`…). Counting or filtering by type and status is `search` with
   `type`/`status` and no text: it lists every match.
2. **Search text as a fallback.** `search` ranks by relevance and shows 20; the
   footer says how many more matched (`limit=0` for all). Results marked
   «(parcial: k/n palabras)» lack some of your words — read them knowing that.
3. **Read before you write.** `read` ends with `🔖 rev: <hex>`. Pass it as
   `base_revision` to `edit` when you replace a body or links. If the note
   changed meanwhile, `edit` refuses and writes nothing: read again, re-apply your
   change on the current text, retry.
4. **Trust the refusals.** `new`/`edit` reject undeclared edge types, edge pairs
   outside `valid_pairs` (with `graph.strict_pairs: true`) and state transitions
   the vault does not declare; the message says what is allowed. Secrets are
   redacted before saving and the output says so.
5. **Superseded notes.** Replaced notes are skipped to their current version;
   an edge reached through a replaced note is marked «heredada» — do not count
   it as declared by the current version.

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

### Writes: base revision and secret redaction

`edit --body` and `edit --link` replace what the note has. `read` prints the
note's revision («🔖 rev: 3fa9c1d2e4b7», on stderr with the header); pass it to
`edit --base-revision` (`base_revision` in the MCP tool) and the write is
refused if the note changed since — re-read and re-apply. `writes.base_revision`
sets what happens when body or links are replaced without it: `optional`
(default) writes and warns, `required` refuses, `off` stays silent. Field,
status and tag edits merge and never need it. Every `edit` runs under a
per-note lock and writes atomically.

`links` and its subkeys are reserved in `edit --field` / MCP `fields`, just
as `new --field links=...` is reserved. Use `--link` / `links` to replace them
or `--clear-links` / `clear_links` to remove them. This keeps every link edit
on the same target/type validation and base-revision path; custom metadata
still merges without a base revision.

`new` and `edit` do not store secrets: API keys, tokens, private keys,
credentials in URLs and passwords they recognise are replaced by
«[secreto tachado]» in the note and in the trace, and the output says how many
and which rule. `writes.redact: false` turns off redaction in notes, not in
the trace. Trace body previews remain limited to 100 characters (plus `...`),
after detecting and redacting secrets in the complete body.

### Search: ranked, capped, accent-insensitive

`search` with text matches every word as a substring (AND) in title,
description and tags — and in the body with `body=True`. Then:

- **Order:** BM25F over title, description, tags and body. A word that starts a
  word in the note counts fully; inside another word it barely counts («cto» in
  «proyecto»). Body-only matches come after frontmatter matches.
- **Cap:** the table shows the 20 most relevant and a footer with how many
  more matched. `limit=N` changes it, `limit=0` shows all. JSON is exhaustive
  unless a limit is passed explicitly.
- **Partial matches:** if fewer than `limit` notes have all the words of a
  multi-word query, the table is completed with notes that have at least half
  of them, after the full matches and marked «(parcial: k/n palabras)»
  (`match: "partial"`, `terms_matched` in JSON). Not done when filtering by
  status, cyber fields or dates.
- **Accents and case don't matter** («pagina» finds «Página»). Exact filters
  (`type:`, `status:`, `title:`) are not folded.
- **Filters without text** (`type`, `status`, dates) list every match in path
  order, uncapped: a filter asks for the complete list.

### Traverse budget

Without `budget`, a traverse returns at most `traverse.budget` nodes (100 by
default, in `.okf.config.yaml`). When the cap cuts, a line «✂️ N nodo(s) más
quedaron fuera…» says how many were left out, and the receipt says
`truncado: sí (tope del vault: 100)` (`budget_default: true` in JSON).
`budget=0` removes the cap; `budget=N` replaces it; negative values are an
error. While the cap does not cut, the output is the same as without it.

### Local note cache (`.okf/state/indice.sqlite`)

`search`, `traverse` and the graph read notes through a local cache: parsed
frontmatter, body links and body per file, reused while the file's `mtime_ns`
and size do not change. Each call stats every file (cheap) and re-reads only
what changed. Measured on a synthetic vault of 10,131 notes: searches went from 6–21 s to under
1.1 s, traverse from 14 s to 0.9 s; building the cache the first time took
2.5 s (11 MB).

- It is disposable: deleting it only costs a rebuild. A damaged or unreadable
  cache, a read-only vault or a busy database fall back to reading the files.
- `OKF_INDICE=0` turns it off.
- It lives in `.okf/state/` — keep `.okf/` in your vault's `.gitignore`.

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

### Fields that resolve to a note (`resolves`)

A field can require that its value names an existing note of a type, instead
of a word from a list: who decided (`decidida_por`) and who does the work
(`responsable`) resolve to a `Persona`. The value matches the note's file name
or its entity folder (`personas/rafael/rafael-hernandez.md` answers to
`rafael` and `rafael-hernandez`); `values` still works alongside for names
that have no note (an agent, say):

```yaml
types:
  fields:
    schemas:
      Decision:
        decidida_por: {required: true, resolves: Persona}
        decidida_el:  {required: true, format: date}
      Task:
        responsable:  {required: true, resolves: Persona, values: [skynet]}
```

### Verification dates: `verificado_el` and `revisar_el`

Knowledge is true until it stops being true. `verificado_el` is the date the
content was last checked against its source; `revisar_el` is when to check
again, and it is optional: when absent it is derived from `verificado_el` plus
the type's period (one fact lives in one place). Periods are declared per type
or per path prefix, and a prefix wins over the type (a public price sheet
expires sooner than a lesson):

```yaml
types:
  review:
    strict: false          # dates must be ISO and in order; error with true
    days: {Research: 90, Insight: 90, Lesson: 180}
    paths: {publico/: 30}
```

`new` stamps `verificado_el` with today's date on types that have a period;
`stale` adds signal 8 (`verification overdue`) when the date has passed;
`review` lists the overdue notes next to the `cyber.review_on` ones (with
`kind: verificacion` in the JSON). Without `types.review`, nothing changes.

### The current version of a superseded note

A note in its replaced state (`superada`, `reemplazada`) has a successor: the
note that declares `corrige` towards it. `traverse` follows that chain and
jumps to the current version by default (the visited node lists what it
skipped under `superseded`); `search` hides replaced notes unless you ask for
their status or pass `--all-versions`. `validate` warns, never errors, when a
current note `extiende`/`fundamenta`/`depende` on a replaced one.

```bash
okf traverse insights/old-insight --edge-type extiende   # starts from the current version
okf traverse insights/old-insight --edge-type extiende --all-versions
okf search --type Insight --all-versions
```

### The initiative as a field, and scope by field

A note says which initiative it belongs to in `iniciativa` (one value or a
list, validated against the vault's closed list). The schema is declared once
for every type under `types.fields.common`, with the types that are exempt
(a session or a person belongs to no initiative; a task inherits the one of
the note it applies to):

```yaml
types:
  fields:
    common:
      fields:
        iniciativa: {required: true, values: [inmobiliaria, caribe-tropical, sistema]}
      except: [Session, Persona, Task]
```

With the field in place, an MCP instance can be bounded to one initiative
without moving files: `--scope iniciativa=caribe-tropical` (or
`OKF_SCOPE=iniciativa=caribe-tropical`) lets `search`, `read`, `traverse` and
the graph see only the notes whose field contains that value. A note without
the field is invisible, and so are the indexes (fail-closed). The path scope
(`--scope publico`) keeps working as before.

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

MIT — see [LICENSE](LICENSE) for the full text.

In short: use it, fork it, ship it commercially, embed it in a closed product.
The only obligation is keeping the copyright notice and the license text in
what you redistribute. No warranty.

Contributions are accepted under the same license (inbound = outbound): by
opening a PR you agree that your code ships under MIT.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed, date-versioned list of added features, fixes, and changes.
