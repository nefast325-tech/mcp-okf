"""Command validate — Strict pre-commit validation.

Validates that:
1. yaml.safe_load() parses the frontmatter without errors
2. Required fields (type, description, timestamp) are present and non-empty
3. Wikilinks are not wrapped in backticks (Obsidian breaks them)
4. Wikilinks are syntactically complete ([[ must have its closing ]])
5. Wikilinks with alias (|) inside tables escape the pipe with \\|
6. Wikilinks point to existing files (no broken links)
7. No stray `cyber:` block in the body (it belongs in the frontmatter —
   anywhere else it is inert: nothing reads it)
8. The body keeps its content: a body that loses most of its lines vs HEAD
   is rejected (catches an agent rewriting the whole body instead of
   appending)
9. `status` matches the vault's state schema (`types.states` in config, see
   cli/states.py): warning by default, error with `types.states.strict`

Unlike parse_frontmatter, it has NO regex fallback.
If the YAML is broken, it fails.

Usage:
    python3 -m cli validate              # validates git staged files
    python3 -m cli validate <file>       # validates a specific file
    python3 -m cli validate --all         # validates all concepts
"""

import re
import sys
from pathlib import Path
from cli.vault import find_md_files, EXCLUDE_FILES

# Guard de pérdida de contenido (validación 11): un body que conserva menos de
# esta fracción de sus líneas con contenido respecto a HEAD se rechaza.
_SHRINK_RATIO = 0.6
_SHRINK_MIN_LINES = 20


def _extract_body(text, fm_end):
    """Extracts the file body, excluding the frontmatter.

    Args:
        text: Full file content.
        fm_end: Index where the frontmatter ends (---).

    Returns:
        str: File body.
    """
    body = text[fm_end:]
    if body.startswith("\n---"):
        body = body[4:]
    return body


def _check_wikilinks_in_backticks(body, rel):
    """Detects wikilinks wrapped in backticks in the body.

    Detected patterns:
    - `[[concept]]` — wikilink inside inline code (NOT a link in Obsidian)
    - ```[[concept]]``` inside a code block (ignored)

    Returns:
        list[str]: List of lines with backtick-wikilinks found.
    """
    errors = []

    # Remove fenced code blocks (```...```) to avoid false positives
    clean = re.sub(r'```[\s\S]*?```', '', body)

    for i, line in enumerate(clean.split("\n"), 1):
        # Only detect exact pattern: `[[...]]` or `[[...|alias]]`
        # Backtick immediately BEFORE [[ and after ]]
        if re.search(r'`\[\[[^]]+\]\](?:\[[^]]+\]\])?`', line):
            # Show fragment
            match = re.search(r'(`\[\[[^`]+\]\]`)', line)
            fragment = match.group(1) if match else "?"
            errors.append(f"  line {i}: {fragment} → must be [[...]] without backticks")

    return errors


def _check_cyber_block_in_body(body, rel):
    """Detects a stray `cyber:` YAML block written in the body.

    The cyber block belongs in the FRONTMATTER (OKF v0.1): it is what
    `health`, `review` and `dashboard-snapshot` read to decide whether a
    loop is open, overdue or closed. A copy in the body is inert — nothing
    reads it — and it is how the failure looks in practice: the Cyber
    Review cron (job 90724426f80a) was told to update the cyber block "con
    patch", so it appended the report to the body and dumped the YAML at the
    end of the body, leaving the frontmatter's `outcome: pending` and an
    expired `review_on` untouched. The loop stayed "broken" forever while the
    report claimed success/failure.

    Detected pattern: a column-0 `cyber:` mapping line in the body. Fenced
    code blocks are stripped first, so a documented example inside ``` does
    not trip it.

    Returns:
        list[str]: List of offending lines.
    """
    errors = []

    clean = re.sub(r'```[\s\S]*?```', '', body)

    for i, line in enumerate(clean.split("\n"), 1):
        if re.match(r'^cyber:\s*$', line):
            errors.append(
                f"  line {i}: stray 'cyber:' block in the body → the cyber block "
                "lives in the FRONTMATTER; update it with "
                "mcp__okf__edit(fields=[\"cyber.outcome=...\"]) and delete it "
                "from the body (nothing reads it there)"
            )

    return errors


def _head_body(filepath, vault, rel=None):
    """Body del archivo tal como está en HEAD. None si no existe o no hay git.

    Se usa para comparar el contenido del body antes/después de un cambio: un
    body que pierde la mayor parte de sus líneas casi siempre es contenido
    borrado, no una edición.
    """
    import subprocess
    if rel is None:
        try:
            rel = str(Path(filepath).resolve().relative_to(Path(vault).resolve()))
        except ValueError:
            return None
    try:
        r = subprocess.run(["git", "-C", str(vault), "show", f"HEAD:{rel}"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _body_line_count(text):
    """Líneas con contenido (ignora líneas vacías)."""
    return len([l for l in text.split("\n") if l.strip()])


def _strip_frontmatter(text):
    """Quita el bloque frontmatter inicial (si lo hay)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _check_body_shrink(filepath, vault, body, rel):
    """Rechaza un body que perdió la mayor parte de su contenido.

    Caso real (2026-09-29): el cron Cyber Review reescribió el body de un
    concepto con `mcp__okf__edit(body=...)` en vez de anexar su reporte, y
    borró 111 líneas (Contexto/Decisión/Impacto) dejando solo el reporte. El
    commit pasó: nada comparaba el body nuevo con el anterior, así que la
    pérdida solo se detectó minutos después, a mano.

    Un guard de prompt no alcanza para esto — la pérdida es irreversible desde
    el archivo, así que se ataja en el commit. Compara el body staged contra el
    de HEAD: si tenía >= _SHRINK_MIN_LINES líneas con contenido y el nuevo
    conserva menos de _SHRINK_RATIO, es error.

    Returns:
        str or None: mensaje de error, o None si el body está sano.
    """
    previous = _head_body(filepath, vault, rel=rel)
    if previous is None:
        return None  # archivo nuevo (o sin git): no hay con qué comparar

    prev_lines = _body_line_count(_strip_frontmatter(previous))
    if prev_lines < _SHRINK_MIN_LINES:
        return None

    new_lines = _body_line_count(body)
    if new_lines >= prev_lines * _SHRINK_RATIO:
        return None

    return (
        f"body shrink: {prev_lines} → {new_lines} líneas con contenido "
        f"({new_lines * 100 // max(prev_lines, 1)}% del anterior). Un cambio "
        f"normal no borra la mayoría del body: si esto es intencional, "
        f"reescribí el archivo a mano; si no, restaurá con "
        f"`git checkout HEAD -- {rel}` y volvé a aplicar el cambio anexando "
        f"(nunca `mcp__okf__edit(body=...)` para agregar texto al final)"
    )


def _check_malformed_wikilinks(body, rel):
    """Detects syntactically incomplete wikilinks in the body.

    Detected patterns:
    - [[concept — missing closing ]] on the same line
    - [[concept|alias — missing closing ]] on the same line

    Returns:
        list[str]: List of lines with malformed wikilinks.
    """
    errors = []

    # Remove fenced code blocks (```...```) and inline code (`...`) to avoid false positives
    clean = re.sub(r'```[\s\S]*?```', '', body)
    clean = re.sub(r'`[^`]+`', '', clean)

    for i, line in enumerate(clean.split("\n"), 1):
        # Find [[ that doesn't have ]] after it on the same line
        # Exclude lines that are normal markdown parts (e.g.: [[ in text)
        opens = [m.start() for m in re.finditer(r'\[\[', line)]
        closes = [m.start() for m in re.finditer(r'\]\]', line)]

        if len(opens) > len(closes):
            # More opens than closes — at least one ]] is missing
            errors.append(f"  line {i}: unclosed wikilink: {line.strip()[:80]}")

    return errors


def _check_wikilinks_in_tables(body, rel):
    """Detects wikilinks with alias (|) inside markdown table rows.

    In tables, the | of the wikilink [[target|alias]] collides with the cell
    separator, splitting the wikilink into two separate cells. The pipe must
    be escaped: [[target\\|alias]].

    Returns:
        list[str]: List of lines with conflicting wikilinks.
    """
    errors = []

    # Remove fenced code blocks
    clean = re.sub(r'```[\s\S]*?```', '', body)

    for i, line in enumerate(clean.split("\n"), 1):
        stripped = line.strip()
        # Only apply to table rows (starting with |)
        if not stripped.startswith('|'):
            continue
        # Find [[...|...]] where | is not escaped with \
        matches = re.finditer(r'\[\[([^]]*?[^\\])\|([^]]+)\]\]', stripped)
        for m in matches:
            fragment = m.group(0)
            errors.append(
                f"  line {i}: {fragment} → inside table, "
                f"escape pipe with \\| so Obsidian doesn't break the cell"
            )

    return errors


def _check_broken_wikilinks(body, rel, vault, fm=None, name_index=None):
    """Detects wikilinks pointing to nonexistent files.

    Also verifies targets in the frontmatter 'links:' field if fm
    is present.

    Args:
        body: Body content.
        rel: Relative path of the file being validated.
        vault: Path to vault root.
        fm: Parsed frontmatter (dict or None). If present and has
            'links:' field, its targets are verified.
        name_index: Shared {filename: relpath} index (built once per run).
            Avoids rebuilding it per file (O(n²) over the vault).

    Returns:
        list[str]: List of broken wikilinks found.
    """
    from cli.wikilinks import extract_links_from_text, resolve_link
    from pathlib import Path

    # Logs in sesiones/ are auto-generated records, not navigable
    # documents: their wikilinks are not validated (same policy as the
    # orphan count in the graph).
    if rel.startswith("sesiones/"):
        return []

    if name_index is None:
        from cli.vault import find_md_files
        try:
            all_files = find_md_files(vault)
            name_index = {f.name: str(f.relative_to(vault)) for f in all_files}
        except Exception:
            name_index = {}

    errors = []
    links = extract_links_from_text(body)

    if links:
        # ABSOLUTE directory of the file (resolve_link expects absolute paths
        # to resolve relative ./ ../ targets against the vault).
        current_dir = (vault / rel).parent if rel else vault
        for target in links:
            if target.startswith(('http://', 'https://', '#')):
                continue
            # Note: resolve_link(target, vault, current_dir, name_index).
            # It used to be called with (target, rel, vault) — swapped args —
            # and extract_links received the body as if it were a Path, so
            # this check never ran (always returned []).
            resolved = resolve_link(target, vault, current_dir, name_index)
            if resolved is None:
                errors.append(f"  [[{target}]] → file not found")

    # Verify targets in links: from frontmatter
    if fm and isinstance(fm.get("links"), list):
        for link in fm["links"]:
            if not isinstance(link, dict):
                continue
            target = str(link.get("target", ""))
            if not target:
                continue
            if target.startswith(('http://', 'https://', '#')):
                continue

            # Try to resolve
            target_file = vault / target
            resolved = None
            if target_file.exists():
                resolved = str(target_file.relative_to(vault))
            elif name_index:
                try_target = target if target.endswith(".md") else target + ".md"
                if try_target in name_index:
                    resolved = name_index[try_target]
                else:
                    for name, rp in name_index.items():
                        if try_target in rp:
                            resolved = rp
                            break

            if resolved is None:
                edge_type = link.get("type", "?")
                errors.append(
                    f"  links: target='{target}' type='{edge_type}' "
                    f"→ file not found"
                )

    return errors


def _resolve_edge_target(vault, target, current_dir, name_index=None):
    """Archivo destino de una arista, o None si no existe.

    Usa `resolve_link` —la MISMA resolución que `graph` y que el chequeo de
    wikilinks— en vez de `vault / target` a secas. Con el path pelado, un
    target escrito sin `.md` (que este vault usa de forma normal) no resolvía:
    el tipo del destino quedaba en `'?'` y el par era imposible de declarar en
    `valid_pairs`, así que `graph.strict_pairs: true` era inalcanzable.
    """
    try:
        from cli.wikilinks import resolve_link
        rel = resolve_link(target, vault, current_dir, name_index)
    except Exception:
        rel = None
    if rel:
        cand = vault / rel
        if cand.exists():
            return cand
    for cand in (vault / target, vault / (str(target) + ".md")):
        if cand.exists():
            return cand
    return None


def _check_edge_type_consistency(filepath, vault, definitions=None, config=None,
                                 name_index=None):
    """Detects inconsistencies in typed edges of the links: field.

    Verifies:
    - Unrecognized edge types (ERROR — blocking)
    - Mutual exclusion: same target with extiende AND refina (ERROR)
    - corrige whose target is not in its replaced state nor has
      cyber.corrected_by (ERROR). «Replaced state» comes from the vault's
      state schema (cli/states.py); without a schema, the legacy substring
      heuristic (deprecada / supercedida) applies.
    - Atypical pair (via validate_cross_type_pair) with semantic score
      (WARNING — informational, scoring-semantico plan: prioritized by score;
      ERROR when the vault sets `graph.strict_pairs: true`)

    Args:
        filepath: Path of the file to validate.
        vault: Path to vault root.
        definitions: Optional config-provided edge type definitions. If None,
                     uses the embedded defaults.
        config: Optional Config (state schema, strict_pairs).

    Returns:
        tuple[list[str], list[str]]: (errors, warnings).
    """
    from cli.edge_types import VALID_EDGE_TYPES, validate_cross_type_pair, score_edge
    from cli.frontmatter import parse_frontmatter
    from cli.states import is_replaced_status, schema_for, states_strict

    strict_pairs = bool(getattr(config, "graph_strict_pairs", False)) if config else False
    errors = []
    warnings = []
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return errors, warnings

    fm, _ = parse_frontmatter(text)
    if fm is None:
        return errors, warnings

    links = fm.get("links")
    if not isinstance(links, list):
        return errors, warnings

    valid_edge_types = set(definitions) if definitions else VALID_EDGE_TYPES
    rel = str(filepath.relative_to(vault))
    src_type = str(fm.get("type", ""))

    types_per_target = {}
    for link in links:
        if not isinstance(link, dict):
            continue
        target = str(link.get("target", ""))
        edge_type = str(link.get("type", ""))

        if edge_type not in valid_edge_types:
            errors.append(
                f"  links: unknown type '{edge_type}' in {rel} → {target}"
            )
            continue

        if target not in types_per_target:
            types_per_target[target] = set()
        types_per_target[target].add(edge_type)

        if edge_type == "corrige":
            target_file = _resolve_edge_target(vault, target, filepath.parent, name_index)
            if target_file is not None:
                try:
                    t_text = target_file.read_text(encoding="utf-8")
                    t_fm, _ = parse_frontmatter(t_text)
                    if t_fm:
                        status = str(t_fm.get("status", ""))
                        cyber = t_fm.get("cyber")
                        has_cb = (
                            isinstance(cyber, dict)
                            and bool(cyber.get("corrected_by"))
                        )
                        t_type = str(t_fm.get("type", ""))
                        if not is_replaced_status(config, t_type, status) and not has_cb:
                            # Con esquema en modo aviso (strict: false) el
                            # vault está migrando: no bloquea. Sin esquema,
                            # o con strict, es error como siempre.
                            blocking = (schema_for(config, t_type) is None
                                        or states_strict(config))
                            (errors if blocking else warnings).append(
                                f"  links: {rel} corrects '{target}' but the "
                                f"target is not in its replaced state (status='{status}')"
                            )
                except Exception:
                    pass

        # Atypical pair with semantic score: non-blocking WARNING.
        # Historical edges may have pairs outside valid_pairs without
        # breaking the commit; the score (0.0-1.0) prioritizes review.
        target_file = _resolve_edge_target(vault, target, filepath.parent, name_index)
        tgt_type = "?"
        tgt_fm = None
        try:
            if target_file is None:
                raise FileNotFoundError(target)
            t_text = target_file.read_text(encoding="utf-8")
            tgt_fm, _ = parse_frontmatter(t_text)
            if tgt_fm and tgt_fm.get("type"):
                tgt_type = str(tgt_fm["type"])
        except Exception:
            pass

        pair_warnings = validate_cross_type_pair(
            src_type, tgt_type, edge_type, definitions=definitions)
        if pair_warnings:
            src_tags = fm.get("tags", [])
            tgt_tags = tgt_fm.get("tags", []) if tgt_fm else []
            if isinstance(src_tags, str):
                src_tags = [t.strip() for t in src_tags.strip("[]").split(",") if t.strip()]
            if isinstance(tgt_tags, str):
                tgt_tags = [t.strip() for t in tgt_tags.strip("[]").split(",") if t.strip()]
            if not isinstance(src_tags, list):
                src_tags = []
            if not isinstance(tgt_tags, list):
                tgt_tags = []
            s = score_edge(
                source_type=src_type,
                target_type=tgt_type,
                edge_type=edge_type,
                source_tags=src_tags,
                target_tags=tgt_tags,
                source_desc=str(fm.get("description", "")),
                target_desc=str(tgt_fm.get("description", "")) if tgt_fm else "",
                precedent_ratio=0.0,
                definitions=definitions,
            )
            for w in pair_warnings:
                line = f"  {w} [score: {s:.2f}]"
                # graph.strict_pairs: el vault declaró todos sus pares y
                # quiere que uno fuera de la lista no entre al commit.
                (errors if strict_pairs else warnings).append(line)

    for target, types in types_per_target.items():
        if "extiende" in types and "refina" in types:
            errors.append(
                f"  links: mutual exclusion in {rel} → {target}: "
                f"'extiende' and 'refina' simultaneously"
            )

    return errors, warnings


def _check_states(fm, config):
    """Validation 12: `status` against the vault's state schema (cli/states.py).

    Returns:
        tuple[list[str], list[str]]: (errors, warnings). Problems are errors
        when `types.states.strict` is true, warnings otherwise. Without a
        schema for the type, nothing is checked (legacy behaviour).
    """
    from cli.states import check_status, states_strict

    problems = check_status(config, str(fm.get("type", "")), fm.get("status"))
    if not problems:
        return [], []
    if states_strict(config):
        return problems, []
    return [], problems


def _validate_file(filepath, vault, definitions=None, name_index=None, config=None):
    """Validates the frontmatter and wikilinks of a .md file.

    Args:
        filepath: Path of the file to validate.
        vault: Path to vault root.
        definitions: Optional config-provided edge type definitions. If None,
                     uses the embedded defaults.
        name_index: Shared {filename: relpath} index (built once per run).
        config: Optional Config (state schema, strict pairs).

    Returns:
        tuple[bool, str]: (ok, error_message). ok=True if valid.
    """
    rel = str(filepath.relative_to(vault))

    # Exclude files that are not concepts
    if filepath.name in EXCLUDE_FILES or filepath.name == "Untitled.md":
        return True, ""

    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"{rel}: could not read: {e}"

    # If it doesn't start with ---, it's not a concept (loose user note)
    if not text.startswith("---"):
        return True, ""

    # Extract YAML block and body
    end = text.find("\n---\n", 3)
    if end == -1:
        end = text.find("\n---", 3)
    if end == -1:
        return False, f"{rel}: unclosed frontmatter (missing final '---')"

    fm_text = text[3:end]
    body = _extract_body(text, end)

    all_errors = []

    # ── Validation 1: strict YAML ──
    try:
        import yaml
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        line = ""
        if hasattr(e, 'problem_mark') and e.problem_mark:
            line = f" (line {e.problem_mark.line + 1})"
        return False, f"{rel}: invalid YAML{line}: {e}"

    if fm is None:
        return False, f"{rel}: empty frontmatter"

    if not isinstance(fm, dict):
        return False, f"{rel}: frontmatter is not a valid YAML dict"

    # ── Validation 2: required fields ──
    if not fm.get("type"):
        all_errors.append("missing 'type'")
    desc = fm.get("description")
    if not desc or not str(desc).strip():
        all_errors.append("missing 'description'")
    # timestamp: obligatorio excepto en sesiones/ (auto-generadas)
    # y skills/ (skills de Hermes, no conceptos OKF)
    if not rel.startswith("sesiones/") and not rel.startswith("skills/"):
        ts = fm.get("timestamp")
        if not ts or not str(ts).strip():
            all_errors.append("missing 'timestamp'")

    # ── Validation 3: wikilinks in backticks ──
    wl_errors = _check_wikilinks_in_backticks(body, rel)
    if wl_errors:
        all_errors.append(f"wikilinks in backticks (Obsidian doesn't render them as links):\n" + "\n".join(wl_errors))

    # ── Validation 4: malformed wikilinks ──
    malformed = _check_malformed_wikilinks(body, rel)
    if malformed:
        all_errors.append(f"syntactically incomplete wikilinks (missing closing ]]):\n" + "\n".join(malformed))

    # ── Validation 5: wikilinks with alias in tables ──
    table_wl = _check_wikilinks_in_tables(body, rel)
    if table_wl:
        all_errors.append(f"wikilinks with unescaped pipe inside table:\n" + "\n".join(table_wl))

    # ── Validation 6: broken links ──
    broken = _check_broken_wikilinks(body, rel, vault, fm=fm,
                                     name_index=name_index)
    if broken:
        all_errors.append(f"wikilinks pointing to nonexistent files:\n" + "\n".join(broken))

    # ── Validation 10: stray cyber block in the body ──
    # Un bloque cyber en el cuerpo es inerte: health/review/dashboard leen el
    # frontmatter. Ver el docstring de _check_cyber_block_in_body para el caso
    # real (cron Cyber Review, 2026-09-25).
    stray_cyber = _check_cyber_block_in_body(body, rel)
    if stray_cyber:
        all_errors.append("stray 'cyber:' block in body (belongs in frontmatter):\n"
                          + "\n".join(stray_cyber))

    # ── Validation 11: el body conserva su contenido ──
    # Ataja la pérdida de contenido: un agente que reescribe el body entero
    # (mcp__okf__edit(body=...)) en vez de anexar. Caso real 2026-09-29.
    shrink = _check_body_shrink(filepath, vault, body, rel)
    if shrink:
        all_errors.append(shrink)

    # ── Validation 8: apoptosis — supercedida exige replaced_by ──
    if str(fm.get("status", "")).strip().lower() == "supercedida":
        rb = fm.get("replaced_by")
        if not rb or not str(rb).strip():
            all_errors.append(
                "status: supercedida sin 'replaced_by' (protocolo de apoptosis: "
                "criterios/protocolo-de-apoptosis-muerte-de-conceptos-en-el-vault-okf)")

    # ── Validation 12: status against the state schema (cli/states.py) ──
    state_errors, state_warnings = _check_states(fm, config)
    all_errors.extend(state_errors)

    # ── Validation 7: typed edge consistency ──
    edge_errors, edge_warnings = _check_edge_type_consistency(
        filepath, vault, definitions=definitions, config=config,
        name_index=name_index)
    if edge_errors:
        all_errors.append(f"inconsistencies in typed links:\n" + "\n".join(edge_errors))
    if edge_warnings or state_warnings:
        # Non-blocking warnings (semantic-scoring plan: atypical pair
        # prioritized by score; state schema in aviso mode) — reported to
        # stderr without aborting.
        print(f"⚠️  {rel}:", file=sys.stderr)
        for w in state_warnings:
            print(f"  {w}", file=sys.stderr)
        for w in edge_warnings:
            print(w, file=sys.stderr)

    if all_errors:
        return False, f"{rel}: " + "; ".join(all_errors)

    return True, ""


def _check_superseded_corrige(vault, config=None):
    """Validation 9 (grafo): toda entidad en su estado de reemplazo debe tener
    una arista tipada 'corrige' entrante declarada por su reemplazo.

    Implementa la memoria de errores tipada del grafo (P1-2 del backlog de
    instrumentación ontológica): el protocolo de apoptosis marca la muerte con
    replaced_by (wikilink), y la arista corrige la hace machine-readable para
    graph_impact/analytics. El reemplazo declara:
        links:
          - target: <predecesor.md>
            type: corrige

    Qué cuenta como «estado de reemplazo»: el literal histórico `supercedida`
    (siempre ERROR, como hasta ahora) y, si el vault declara un esquema de
    estados (cli/states.py), el `replaced` de la capa del tipo — ERROR con
    `types.states.strict`, WARNING si no.

    Returns:
        tuple[list[str], list[str]]: (errors, warnings).
    """
    import yaml
    from cli.states import is_replaced_status, schema_for, states_strict

    errors = []
    warnings = []
    superseded: list[tuple[str, bool]] = []  # (rel, blocking)
    corrige_targets: set[str] = set()
    for f in find_md_files(vault):
        if f.name in EXCLUDE_FILES or f.name == "Untitled.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---\n", 3)
        if end == -1:
            end = text.find("\n---", 3)
        if end == -1:
            continue
        rel = str(f.relative_to(vault))
        try:
            fm = yaml.safe_load(text[3:end])
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        status = str(fm.get("status", "")).strip().lower()
        c_type = str(fm.get("type", ""))
        if status == "supercedida":
            superseded.append((rel, True))
        elif schema_for(config, c_type) is not None and is_replaced_status(config, c_type, status):
            superseded.append((rel, states_strict(config)))
        links = fm.get("links") or []
        if not isinstance(links, list):
            continue
        for link in links:
            if isinstance(link, dict) and link.get("type") == "corrige":
                t = str(link.get("target", "")).strip()
                if t and not t.endswith(".md"):
                    t += ".md"
                corrige_targets.add(t)
    for rel, blocking in superseded:
        if rel not in corrige_targets:
            msg = (f"{rel}: en estado de reemplazo sin arista 'corrige' entrante "
                   "(el reemplazo debe declarar links: [{target: <este>, type: corrige}])")
            (errors if blocking else warnings).append(msg)
    return errors, warnings


def run(args, vault, config=None):
    """Runs frontmatter validation."""
    target = getattr(args, "target", None)
    validate_all = getattr(args, "all", False)

    if not vault.exists():
        print(f"Error: vault not found at {vault}", file=sys.stderr)
        return 1

    # Determine which files to validate
    if target:
        # Specific file
        filepath = vault / target
        if not filepath.exists():
            # Search by name
            for f in vault.rglob("*.md"):
                if f.name == target or target in str(f.relative_to(vault)):
                    filepath = f
                    break
        if not filepath.exists():
            print(f"✗ Not found: {target}", file=sys.stderr)
            return 1
        files = [filepath]
    elif validate_all:
        files = find_md_files(vault)
    else:
        # Default: .md files staged in git
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(vault), "diff", "--cached", "--name-only",
             "--diff-filter=ACMR", "*.md"],
            capture_output=True, text=True, timeout=10,
        )
        staged = [f.strip() for f in result.stdout.split("\n") if f.strip()]
        files = [vault / f for f in staged
                 if not f.endswith(("index.md", "log.md", "dashboard.md"))]
        if not files:
            print("✓ No .md files staged for validation.")
            return 0

    # Validate
    definitions = config.edge_type_definitions() if config else None
    # Shared index: built ONCE (previously _check_broken_wikilinks rebuilt
    # it per file with links: → O(n²) over the vault).
    name_index = {f.name: str(f.relative_to(vault)) for f in find_md_files(vault)}
    ok_count = 0
    errors = []

    for f in files:
        if not f.exists():
            continue
        is_ok, msg = _validate_file(f, vault, definitions=definitions,
                                    name_index=name_index, config=config)
        if is_ok:
            ok_count += 1
        else:
            errors.append(msg)

    # ── Validation 9 (grafo): reemplazadas exigen arista corrige entrante ──
    graph_errors, graph_warnings = _check_superseded_corrige(vault, config=config)
    errors.extend(graph_errors)
    for w in graph_warnings:
        print(f"⚠️  {w}", file=sys.stderr)

    # Reporte
    if errors:
        print(f"❌ {len(errors)} file(s) with errors:", file=sys.stderr)
        for e in errors:
            print(f"   {e}", file=sys.stderr)
        print(f"\n✅ {ok_count} valid, ❌ {len(errors)} invalid",
              file=sys.stderr)
        print("\nCommit aborted. Fix the errors and try again.",
              file=sys.stderr)
        return 1

    print(f"✅ {ok_count} file(s) valid.")
    return 0
