"""Command stale — Semantic staleness detector.

Scans vault concepts for staleness signals:
concepts with valid format but disconnected from current reality.

Signals:
  1. timestamp > 90 days        → stale content
  2. reads = 0                  → nobody consults it
     (counter read from the telemetry store .okf/state/reads.jsonl —
     decisión 2026-08-27: el campo `reads` ya NO vive en el frontmatter.
     Solo cuenta si el concepto ya tiene antigüedad > reads_zero_min_days,
     para no marcar como stale un concepto recién creado y aún no leído)
  3. status: proposal > 30 days → phantom decision
  4. no backlinks               → orphan in the graph
  5. no commits > 6 months      → unmaintained
  6. type: Decision no status   → decision without closure
  7. description vs body        → description describes problems
     that the body already solved (checkboxes [x] > 70%)

Classification:
  🔴 STALE     — 3+ signals
  🟡 ATTENTION — 1-2 signals
  🟢 FRESH     — 0 signals
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli.frontmatter import parse_frontmatter
from cli.reads_store import get_reads
from cli.vault import find_md_files


def get_today():
    return datetime.now(timezone.utc)


def days_ago(date_str, today=None):
    """Calculates days elapsed since an ISO date."""
    if today is None:
        today = get_today()
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (today - dt).days
    except (ValueError, TypeError):
        return None


def git_last_commit_date(filepath, vault, git_index=None):
    """Date of the last commit that touched this file. None if no commits.

    Uses the batched git index (cli.gitutil) when provided; falls back to
    a single git log subprocess otherwise. The per-file subprocess pattern
    cost ~3s in ``stale``; the batch reduces it to one git call total.
    """
    if git_index is not None:
        entry = git_index.get(str(filepath.relative_to(vault)))
        if entry:
            return entry.get("last")
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "log", "-1", "--format=%aI", "--", str(filepath.relative_to(vault))],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception:
        return None


def count_checkboxes(text):
    """Counts [x] and [ ] checkboxes in the body (post-frontmatter)."""
    if text.startswith("---"):
        end = text.find("\n---\n", 3)
        if end == -1:
            end = text.find("\n---", 3)
        if end != -1:
            body = text[end:]
        else:
            body = text
    else:
        body = text

    completed = len(re.findall(r'^\s*- \[x\]', body, re.MULTILINE | re.IGNORECASE))
    pending = len(re.findall(r'^\s*- \[ \]', body, re.MULTILINE))
    total = completed + pending
    return completed, pending, total


def has_problem_language(description, patterns=None):
    """Detects whether the description talks about problems in present tense.

    Args:
        description: description text.
        patterns: list of regex patterns (case-insensitive). If None,
                  uses the embedded defaults (Spanish).
    """
    if patterns is None:
        patterns = [
            r'\bno\s+(existe|funciona|hay|tiene|está|implementado|conectado|almacena|notifica)\b',
            r'\broto[s]?\b',
            r'\bfalso[s]?\b',
            r'\bfalta[n]?\b',
            r'\bsin\s+(implementar|resolver|definir|conectar|backend)\b',
            r'\bpendiente[s]?\b',
            r'\bincompleto\b',
            r'\bplaceholder\b',
            r'\bclaims?\s+fals[oa]s?\b',
            r'\b404\b',
            r'\bCTAs?\s+rot[oa]s?\b',
            r'\bcero\s+métricas\b',
            r'\bno\s+está\b',
            r'\bse\s+pierden\b',
        ]
    desc_lower = description.lower()
    for pat in patterns:
        if re.search(pat, desc_lower):
            return True
    return False


def build_backlinks_index(vault):
    """Builds backlinks index: {target_relpath: [source_relpaths]}.

    Usa la misma resolución que `graph` (`build_graph`): wikilinks resueltos
    por ruta antes que por nombre de archivo, y aristas tipadas del
    frontmatter. Antes resolvía solo por nombre de archivo —un `[[a/x]]`
    caía en cualquier `x.md`— e ignoraba `links:`, así que marcaba
    «no backlinks» conceptos que sí los tienen.
    """
    from cli.commands.graph import build_graph

    index = {}
    for rel, node in build_graph(vault).items():
        sources = set(node["in"]) | {e["target"] for e in node["typed_in"]}
        if sources:
            index[rel] = sorted(sources)
    return index


def collect_stale(vault, timestamp_days=90, propuesta_days=30,
                  no_commits_days=180, checkbox_ratio=0.7,
                  problem_patterns=None, reads_zero_min_days=30):
    """Scans all concepts and evaluates staleness signals."""
    today = get_today()
    backlinks = build_backlinks_index(vault)
    reads_index = get_reads(vault)
    from cli.gitutil import build_git_dates_index
    git_index = build_git_dates_index(vault)
    results = []

    for f in find_md_files(vault):
        rel = str(f.relative_to(vault))
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        fm, _ = parse_frontmatter(text)
        if not fm:
            continue

        signals = []
        details = {}

        # Edades base: se calculan una sola vez (las usan las señales 1, 2, 3 y 5).
        ts = fm.get("timestamp")
        ts_age = days_ago(str(ts), today) if ts else None
        last_commit = git_last_commit_date(f, vault, git_index=git_index)
        commit_age = days_ago(last_commit, today) if last_commit else None
        # Edad efectiva: timestamp propio si lo hay, si no la del último commit.
        concept_age = ts_age if ts_age is not None else commit_age

        # ── Señal 1: timestamp antiguo ──
        if ts_age is not None and ts_age > timestamp_days:
            signals.append(f"timestamp {ts_age}d")
            details["age_days"] = ts_age

        # ── Señal 2: reads = 0 ──
        # El contador de lecturas vive en el store de telemetría
        # (.okf/state/reads.jsonl, decisión 2026-08-27) — NO en el frontmatter.
        # Ausencia de entrada = 0 lecturas. Solo cuenta como señal si el
        # concepto ya tiene antigüedad > reads_zero_min_days: un concepto
        # recién creado y aún no leído no está desconectado de la realidad.
        if reads_index.get(rel, 0) == 0:
            if concept_age is not None and concept_age > reads_zero_min_days:
                signals.append(f"reads=0 ({concept_age}d)")
                details["reads"] = 0

        # ── Señal 3: status: propuesta sin resolver ──
        status = fm.get("status", "")
        if str(status).lower() == "propuesta":
            if ts_age is not None:
                if ts_age > propuesta_days:
                    signals.append(f"proposal unresolved ({ts_age}d)")
            else:
                signals.append("proposal missing timestamp")

        # ── Señal 4: sin backlinks ──
        if rel not in backlinks or len(backlinks[rel]) == 0:
            # Excluir conceptos raíz que son naturalmente huérfanos
            concept_type = str(fm.get("type", ""))
            if concept_type not in ("MarcoTeorico", "Spec", "Tool", "Agente", "Sesion"):
                signals.append("no backlinks")

        # ── Señal 5: sin commits ──
        if commit_age is not None:
            if commit_age > no_commits_days:
                signals.append(f"no commits ({commit_age}d)")
                details["commit_age_days"] = commit_age
        else:
            signals.append("no commits (new?)")

        # ── Señal 6: type Decision sin status ──
        if str(fm.get("type", "")) == "Decision" and not fm.get("status"):
            signals.append("decision without status")

        # ── Señal 7: description vs body inconsistency ──
        description = str(fm.get("description", ""))
        if has_problem_language(description, patterns=problem_patterns):
            completed, pending, total = count_checkboxes(text)
            if total >= 3 and completed / total >= checkbox_ratio:
                signals.append(f"outdated desc ({completed}/{total} tasks ✓)")
                details["tasks_completed"] = f"{completed}/{total}"

        # ── Clasificar ──
        count = len(signals)
        if count >= 3:
            level = "STALE"
            icon = "🔴"
        elif count >= 1:
            level = "ATTENTION"
            icon = "🟡"
        else:
            level = "FRESH"
            icon = "🟢"

        results.append({
            "file": rel,
            "type": str(fm.get("type", "")),
            "title": str(fm.get("title", rel)),
            "level": level,
            "icon": icon,
            "signal_count": count,
            "signals": signals,
            "details": details,
        })

    # Ordenar: STALE primero, luego ATENCIÓN, luego FRESCO
    priority = {"STALE": 0, "ATTENTION": 1, "FRESH": 2}
    results.sort(key=lambda r: (priority.get(r["level"], 9), r["file"]))

    return results


def run(args, vault, config=None):
    """Runs the staleness detector."""
    json_out = getattr(args, "json", False)

    # Umbrales desde config o defaults
    timestamp_days = config.stale_timestamp_days if config else 90
    propuesta_days = config.stale_propuesta_days if config else 30
    no_commits_days = config.stale_no_commits_days if config else 180
    checkbox_ratio = config.stale_checkbox_ratio if config else 0.7
    problem_patterns = config.stale_problem_patterns if config else None
    reads_zero_min_days = config.stale_reads_zero_min_days if config else 30

    results = collect_stale(vault, timestamp_days, propuesta_days,
                            no_commits_days, checkbox_ratio, problem_patterns,
                            reads_zero_min_days)

    if json_out:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    stale = [r for r in results if r["level"] == "STALE"]
    atencion = [r for r in results if r["level"] == "ATTENTION"]
    fresco = [r for r in results if r["level"] == "FRESH"]

    total = len(results)

    print(f"🕵️ Staleness Detector — {get_today().strftime('%Y-%m-%d')}")
    print("═" * 60)

    if not stale and not atencion:
        print(f"\n✅ All FRESH — {total} concept(s) with no staleness signals.")
        print("\n── FRESH ──")
        for r in fresco:
            print(f"   🟢 [{r['type']}] {r['file']}")
        return 0

    # ── STALE ──
    if stale:
        print(f"\n🔴 STALE — {len(stale)} concept(s) with 3+ signals")
        print("─" * 60)
        for r in stale:
            print(f"\n   📄 [{r['type']}] {r['file']}")
            print(f"   {r['title']}")
            for s in r["signals"]:
                print(f"   ⚡ {s}")

    # ── ATENCIÓN ──
    if atencion:
        print(f"\n🟡 ATTENTION — {len(atencion)} concept(s) with 1-2 signals")
        print("─" * 60)
        for r in atencion:
            print(f"\n   📄 [{r['type']}] {r['file']}")
            print(f"   {r['title']}")
            for s in r["signals"]:
                print(f"   ⚡ {s}")

    # ── FRESCO ──
    if fresco:
        print(f"\n🟢 FRESH — {len(fresco)} concept(s) with no signals")
        for r in fresco:
            print(f"   🟢 [{r['type']}] {r['file']}")

    # ── Resumen ──
    print(f"\n{'═' * 60}")
    print(f"🔴{len(stale)} 🟡{len(atencion)} 🟢{len(fresco)}  — {total} concept(s) analyzed")

    return 1 if stale else 0
