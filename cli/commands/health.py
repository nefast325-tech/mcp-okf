"""health command — Complete integrity check of the OKF vault.

9 checks:
    1. Valid frontmatter (type + description)
    2. Synced indexes
    3. Connected graph (orphans, density, tags)
    4. Broken links (wikilinks + markdown)
    5. Working scripts (smoke test via subprocess)
    6. Git hook present
    7. Cyber block: valid
    8. Plugin↔spec sync (plugin_hash vs repo HEAD)
    9. Timestamp vs git (creation date consistency)
"""

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from cli.vault import find_md_files, build_name_index
from cli.frontmatter import parse_frontmatter, extract_tags

EXCLUDED_CYBER_TYPES = {"MarcoTeorico", "LeccionAprendida", "Tool", "Spec"}


# ── Check 1: Frontmatter ──

def _check_frontmatter(vault):
    all_files = find_md_files(vault)
    ok, bad, warnings = 0, [], []

    for f in all_files:
        rel = str(f.relative_to(vault))
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            bad.append(f"{rel}: could not read")
            continue

        fm, _ = parse_frontmatter(text)
        if fm is None:
            bad.append(f"{rel}: missing frontmatter or invalid YAML")
            continue

        if not fm.get("type"):
            bad.append(f"{rel}: missing 'type' (OKF required)")
            continue

        desc = fm.get("description")
        if not desc or not str(desc).strip():
            bad.append(f"{rel}: missing 'description' (vault policy)")
            continue

        desc_str = str(desc).strip()
        if len(desc_str) < 15:
            warnings.append(f"{rel}: description too short ({len(desc_str)} chars)")

        ok += 1

    return ok, bad, warnings


# ── Check 2: Índices ──

def _check_indices(vault):
    ok, stale = 0, []

    from cli.vault import EXCLUDE_FILES as _EXCL, EXCLUDE_DIRS as _EXCL_DIRS
    for idx_file in sorted(vault.rglob("index.md")):
        parent = idx_file.parent
        rel_parts = parent.relative_to(vault).parts if parent != vault else ()
        if any(p in _EXCL_DIRS for p in rel_parts):
            continue  # carpeta fuera del régimen de índices (p.ej. .dsh-build)
        rel_dir = str(parent.relative_to(vault)) if parent != vault else "root"

        real_files = {f.name for f in parent.glob("*.md")
                      if f.name not in _EXCL}
        real_dirs = {d.name for d in parent.iterdir()
                     if d.is_dir() and (d / "index.md").exists()
                     and d.name not in _EXCL_DIRS}

        try:
            text = idx_file.read_text(encoding="utf-8")
        except Exception:
            stale.append(f"{rel_dir}/index.md: could not read")
            continue

        if not real_files and not real_dirs:
            body = text
            if body.startswith("---"):
                end = body.find("\n---\n", 3)
                if end != -1:
                    body = body[end + 5:]
            body = body.strip()
            if len(body) > 150:
                stale.append(f"{rel_dir}/index.md: no files but substantial content "
                             f"({len(body)} chars) — should this be a concept?")
            continue

        listed_files = set(re.findall(r'\[([^\]]+)\]\(([^)]+\.md)\)', text))
        listed_files = {m[1] for m in listed_files if not m[1].endswith("/index.md")}
        listed_dirs = {m[1].rstrip("/") for m in re.findall(r'\[([^\]]+)\]\(([^)]+/)\)', text)}
        for m in re.finditer(r'\[([^\]]+)\]\(([^)]+/index\.md)\)', text):
            listed_dirs.add(m.group(2).replace("/index.md", ""))

        phantom = listed_files - real_files
        missing_files = real_files - listed_files
        missing_dirs = real_dirs - listed_dirs

        issues = []
        if phantom:
            issues.append(f"lists nonexistent files: {', '.join(sorted(phantom))}")
        if missing_files:
            issues.append(f"missing from index: {', '.join(sorted(missing_files))}")
        if missing_dirs:
            issues.append(f"missing subdirectories: {', '.join(sorted(missing_dirs))}")

        if issues:
            stale.append(f"{rel_dir}/index.md: {'; '.join(issues)}")
        else:
            ok += 1

    return ok, stale


# ── Check 3: Grafo ──

def _check_graph(vault):
    """Graph health via cli.commands.graph (direct import, not subprocess)."""
    try:
        from cli.commands.graph import build_graph, build_tag_index
        graph = build_graph(vault)
        tag_index = build_tag_index(vault)

        # Nodos leaf (frontmatter leaf: true, expuesto por build_graph) +
        # agentes/sesiones: prompts y logs, no documentos de referencia.
        # Antes esto re-leía y re-parseaba los 478 archivos; ahora viene
        # del grafo ya construido.
        leaf_nodes = {n for n, d in graph.items() if d.get("leaf")}
        leaf_nodes |= {n for n in graph if n.startswith("agentes/") or n.startswith("sesiones/")}

        nodes = len(graph)
        edges = sum(len(d["out"]) for d in graph.values())
        orphans = sum(1 for n, d in graph.items()
                      if not d["in"] and not d["out"]
                      and not d["typed_in"] and not d["typed_out"]
                      and n not in leaf_nodes)
        density = edges / max(nodes * (nodes - 1), 1)

        total_tags = len(tag_index)
        shared_tags = sum(1 for files in tag_index.values() if len(files) >= 2)

        warnings = []
        if orphans > 0:
            warnings.append(f"{orphans} orphan concept(s)")
        # La densidad NO es warning: es un KPI de forma del portafolio, no un
        # defecto del vault. Decae sola al crecer (el denominador va con n² y
        # las aristas con n), así que cualquier vault que crezca termina bajo
        # el umbral haga lo que haga — y con `health --strict` en el pre-commit
        # eso rechaza TODOS los commits, incluidos los de los agentes de sync.
        # Se sigue informando en el reporte y en `graph.density` del JSON.

        return {
            "nodes": nodes, "edges": edges, "orphans": orphans, "density": density,
            "tags": {"total": total_tags, "shared": shared_tags},
        }, warnings
    except Exception as e:
        return None, [f"graph analysis failed: {e}"]


# ── Check 4: Links rotos ──

def _resolve_simple(target, vault, current_dir, name_index):
    """Link resolution (same logic as wikilinks.resolve_link)."""
    if "#" in target:
        target = target.split("#")[0]
    if target.startswith("/"):
        target = target.lstrip("/")
        if not target.endswith(".md"):
            target += ".md"
        return target
    if target.startswith("."):
        try:
            resolved = (current_dir / target).resolve()
            result = str(resolved.relative_to(vault.resolve()))
            if not result.endswith(".md"):
                result += ".md"
            return result
        except ValueError:
            return None
    if "/" in target:
        if not target.endswith(".md"):
            target += ".md"
        return target
    name = target if target.endswith(".md") else target + ".md"
    if name in name_index:
        return name_index[name]
    candidate = current_dir / name
    if candidate.exists():
        return str(candidate.relative_to(vault))
    return None


def _check_broken_links(vault):
    all_files = find_md_files(vault)
    all_relpaths = {str(f.relative_to(vault)) for f in all_files}
    name_index = {f.name: str(f.relative_to(vault)) for f in all_files}
    broken = []

    for f in all_files:
        rel = str(f.relative_to(vault))
        if rel.startswith("sesiones/"):
            continue  # logs auto-generados, no documentos navegables
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        clean = text
        if clean.startswith("---"):
            end = clean.find("\n---\n", 3)
            if end != -1:
                clean = clean[end + 5:]
        clean = re.sub(r'```[\s\S]*?```', '', clean)
        clean = re.sub(r'`[^`]+`', '', clean)

        for m in re.finditer(r'\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]', clean):
            target = m.group(1).strip().rstrip('\\')
            if target.startswith(("http://", "https://")):
                continue  # URL externa, no wikilink local
            resolved = _resolve_simple(target, vault, f.parent, name_index)
            if resolved and resolved not in all_relpaths:
                candidate = vault / resolved
                if not candidate.exists():
                    broken.append(f"{rel} → [[{target}]]")

        for m in re.finditer(r'\[([^\]]*)\]\(([^)]+\.md)\)', clean):
            target = m.group(2).strip()
            if target.startswith(("http://", "https://")):
                continue  # URL externa terminada en .md, no ruta local
            resolved = _resolve_simple(target, vault, f.parent, name_index)
            if resolved and resolved not in all_relpaths:
                candidate = vault / resolved
                if not candidate.exists():
                    broken.append(f"{rel} → [{m.group(1)}]({target})")

    return broken


# ── Check 5: Scripts funcionales ──

def _check_scripts(vault, smoke_entry_point="tp3-cibernetico"):
    """Smoke test: CLI commands work (via subprocess, self-contained).

    The smoke tests are independent subprocesses, so they run in parallel
    (serially they added ~30s of wall time to ``health``).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    cli_module = str((Path(__file__).resolve().parent.parent.parent))

    tests = [
        (["graph", "stats"], 15),
        (["search"], 10),
        (["traverse", smoke_entry_point, "--depth", "1"], 15),
        (["validate", "--all"], 15),
        (["touch", "--all"], 10),
        (["audit"], 10),
        (["review", "--count"], 10),
        (["new", "--type", "Decision", "--title", "Health Check Smoke Test",
          "--description", "Automatic health check test.", "--dry-run"], 10),
    ]

    def _run_one(cmd_args, timeout):
        name = f"cli {' '.join(cmd_args)}"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "cli"] + cmd_args,
                capture_output=True, text=True, timeout=timeout,
                cwd=str(vault),
                env={**os.environ,
                     # Smoke test: verificación, no uso. Sin esto cada health
                     # deja 8 eventos de telemetría y las métricas terminan
                     # describiendo al health check en vez del trabajo real.
                     "OKF_SUPPRESS_TELEMETRY": "1",
                     "PYTHONPATH": cli_module + ((":" + os.environ["PYTHONPATH"])
                                                 if os.environ.get("PYTHONPATH") else ""),
                     "OKF_VAULT": str(vault)},
            )
            if result.returncode != 0:
                if cmd_args == ["review", "--count"] and result.returncode == 1:
                    return name, True, None
                if cmd_args == ["validate", "--all"] and result.returncode == 1:
                    # exit 1 = encontró archivos inválidos (su trabajo), no un fallo del CLI.
                    return name, True, None
                err_output = (result.stderr or result.stdout).strip()
                err_lines = err_output.split("\n")
                err = "; ".join(line.strip() for line in err_lines[:5] if line.strip())
                if not err:
                    err = f"exit {result.returncode}"
                return name, False, f"{name}: {err}"
            return name, True, None
        except subprocess.TimeoutExpired:
            return name, False, f"{name}: timeout ({timeout}s)"
        except Exception as e:
            return name, False, f"{name}: {str(e)[:80]}"

    ok, failed = 0, []
    with ThreadPoolExecutor(max_workers=min(4, len(tests))) as pool:
        futures = [pool.submit(_run_one, args, tmo) for args, tmo in tests]
        for fut in as_completed(futures):
            _name, _ok, _err = fut.result()
            if _ok:
                ok += 1
            else:
                failed.append(_err)

    return ok, failed


# ── Check 6: Git hook ──

def _hooks_dir(vault):
    """Directorio de hooks que usa git de verdad.

    `git rev-parse --git-path hooks` respeta `core.hooksPath` y los worktrees
    (donde `.git` es un archivo). Sin git a mano, cae al lugar por defecto.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, timeout=5, cwd=str(vault),
        )
        if result.returncode == 0 and result.stdout.strip():
            path = Path(result.stdout.strip())
            return path if path.is_absolute() else vault / path
    except (OSError, subprocess.TimeoutExpired):
        pass
    return vault / ".git" / "hooks"


def _check_git_hook(vault):
    hooks_dir = _hooks_dir(vault)
    hook = hooks_dir / "pre-commit"
    if not hook.exists():
        # La ruta va en el mensaje: un 7/8 por el hook no debe obligar a leer
        # el código para saber dónde se buscó (sugerencia del PR #30).
        return False, f"pre-commit hook does not exist (looked in {hooks_dir})"
    if not os.access(hook, os.X_OK):
        return False, "pre-commit hook is not executable"
    return True, None


# ── Check 7: Bloque cyber ──

def _check_cyber(vault, excluded_cyber=None):
    from datetime import timedelta
    today = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")  # Colombia UTC-5, no UTC
    if excluded_cyber is None:
        excluded_cyber = EXCLUDED_CYBER_TYPES
    ok, warnings, errors = 0, [], []

    for f in find_md_files(vault):
        rel = str(f.relative_to(vault))
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        fm, _ = parse_frontmatter(text)
        if fm is None:
            continue

        cyber = fm.get("cyber")
        if not isinstance(cyber, dict):
            ok += 1
            continue

        concept_type = str(fm.get("type", ""))
        outcome = str(cyber.get("outcome", ""))
        review_on = str(cyber.get("review_on", "")) if cyber.get("review_on") else ""

        if concept_type in excluded_cyber:
            errors.append(f"{rel}: cyber block: in type '{concept_type}' "
                          f"(excluded — only Decision/Plan/Project/Insight)")
            continue

        if not outcome or outcome in ("", "None"):
            warnings.append(f"{rel}: cyber block: missing 'outcome'")

        if outcome == "pending" and review_on and review_on < today:
            errors.append(f"{rel}: cyber.outcome=pending with review_on={review_on} "
                          f"expired — broken loop")

        ok += 1

    return ok, warnings, errors


# ── Check 8: Sincronización plugin↔spec ──

def _check_plugin_hash_sync(vault):
    """Verifies that plugin_hash in specs matches the plugin repo HEAD."""
    specs_dir = vault / "specs"
    plugins_dir = Path.home() / ".hermes" / "plugins"
    ok, stale = 0, []

    if not specs_dir.is_dir():
        return 0, ["specs/ does not exist"]

    for spec_file in sorted(specs_dir.glob("*.md")):
        try:
            text = spec_file.read_text(encoding="utf-8")
        except Exception:
            continue

        fm, _ = parse_frontmatter(text)
        if fm is None:
            continue

        plugin_hash = fm.get("plugin_hash")
        if not plugin_hash:
            continue

        spec_name = spec_file.stem

        plugin_name = spec_name
        for prefix in ("plugin-", "hermes-"):
            if plugin_name.startswith(prefix):
                plugin_name = plugin_name[len(prefix):]
                break

        plugin_dir = plugins_dir / plugin_name
        if not (plugin_dir / ".git").exists():
            found = False
            if plugins_dir.is_dir():
                for d in plugins_dir.iterdir():
                    if not d.is_dir() or not (d / ".git").exists():
                        continue
                    if d.name in plugin_name or plugin_name in d.name:
                        plugin_dir = d
                        found = True
                        break
                    base = plugin_name.split("-")[0]
                    if base and d.name.startswith(base):
                        plugin_dir = d
                        found = True
                        break
            if not found:
                stale.append(f"{spec_file.relative_to(vault)}: plugin_hash={plugin_hash[:7]} "
                             f"but no local repo found in ~/.hermes/plugins/")
                continue

        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=str(plugin_dir),
            )
            if result.returncode != 0:
                stale.append(f"{spec_file.relative_to(vault)}: could not read HEAD "
                             f"of {plugin_dir}")
                continue

            head = result.stdout.strip()
            if head.startswith(plugin_hash):
                ok += 1
            else:
                stale.append(f"{spec_file.relative_to(vault)}: plugin_hash={plugin_hash[:7]} "
                             f"≠ HEAD={head[:7]} en {plugin_dir.name} — spec desincronizada")
        except Exception as e:
            stale.append(f"{spec_file.relative_to(vault)}: error leyendo git: {e}")

    return ok, stale


# ── Check 9: Timestamp vs git ──

def _check_timestamp_git(vault):
    """Verifies that the frontmatter timestamp is consistent with git.

    The timestamp in OKF is 'last meaningful change'. In practice, okf_new
    sets it at creation and it's rarely updated. This check verifies two things:
    1. The file HAS a timestamp (warning if missing, except index/log/sesiones)
    2. The timestamp is not before the first commit nor far after the last
       (signal of invented or desynchronized timestamp)
    """
    ok, warnings, errors = 0, [], []

    # Un solo git log batchado (antes: ~2 subprocesos por archivo ≈ 10s).
    from cli.gitutil import build_git_dates_index, build_uncommitted_set
    git_index = build_git_dates_index(vault)
    # Un archivo con cambios sin commitear no puede tener el timestamp
    # "en el futuro": el commit contra el que se compara todavia no existe.
    uncommitted = build_uncommitted_set(vault)

    for f in find_md_files(vault):
        rel = str(f.relative_to(vault))
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        fm, _ = parse_frontmatter(text)
        if fm is None:
            continue

        ts_raw = fm.get("timestamp")
        if not ts_raw:
            if rel.endswith("/index.md") or rel.endswith("/log.md"):
                continue
            parts = f.relative_to(vault).parts
            if parts and parts[0] == "sesiones":
                continue
            warnings.append(f"{rel}: missing timestamp")
            continue

        ts_str = str(ts_raw).strip().strip('"').strip("'")
        try:
            ts_dt = datetime.fromisoformat(ts_str)
        except ValueError:
            errors.append(f"{rel}: invalid timestamp: '{ts_raw}'")
            continue

        ts_date = ts_dt.astimezone(timezone.utc).date() if ts_dt.tzinfo else ts_dt.date()

        entry = git_index.get(rel)
        if entry is None:
            ok += 1
            continue

        try:
            last_dt = datetime.fromisoformat(entry["last"].strip())
            last_date = last_dt.astimezone(timezone.utc).date() if last_dt.tzinfo else last_dt.date()
            # Timestamp >1 día en el futuro respecto al último commit
            if (ts_date - last_date).days > 1 and rel not in uncommitted:
                warnings.append(
                    f"{rel}: timestamp={ts_date} es futuro vs git last-edit={last_date}"
                )
                continue

            first_dt = datetime.fromisoformat(entry["first"].strip())
            first_date = first_dt.astimezone(timezone.utc).date() if first_dt.tzinfo else first_dt.date()
            # Timestamp >30 días anterior al primer commit (probablemente inventado)
            if (first_date - ts_date).days > 30:
                warnings.append(
                    f"{rel}: timestamp={ts_date} es muy anterior a git created={first_date}"
                )
                continue

            ok += 1
        except Exception:
            ok += 1

    return ok, warnings, errors


def _check_version(vault):
    """Checks the installed CLI version against the latest release of our repo.

    Fuente de verdad: https://github.com/syberloop/mcp-okf (releases). NO PyPI —
    el nombre 'okf-mcp' en PyPI pertenece a un proyecto de terceros (ver
    Decision en el vault). Si no hay red o el repo no tiene releases, reporta
    'no verificable' sin romper el health.

    OJO: que la version local sea MAYOR que la ultima release publicada no
    significa "al dia" — significa que el feed de releases quedo atras y no
    puede verificar nada. Son dos estados distintos y se reportan distinto
    (`ahead`), para que la linea no afirme una verificacion que no hizo.

    Returns:
        dict: {"local": str, "latest": str|None, "up_to_date": bool|None,
               "ahead": bool|None, "source": str}
    """
    from cli import __version__
    local = __version__
    latest = None
    up_to_date = None
    ahead = None

    try:
        import json as _json
        import urllib.request as _ur

        req = _ur.Request(
            "https://api.github.com/repos/syberloop/mcp-okf/releases/latest",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "okf-mcp-health"},
        )
        with _ur.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        tag = (data.get("tag_name") or "").lstrip("v")
        latest = tag or None
        if latest:
            def _key(v):
                return tuple(int(x) for x in re.findall(r"\d+", v)[:3] or [0])
            up_to_date = _key(local) == _key(latest)
            ahead = _key(local) > _key(latest)
    except Exception:
        latest, up_to_date, ahead = None, None, None

    return {"local": local, "latest": latest, "up_to_date": up_to_date,
            "ahead": ahead, "source": "github.com/syberloop/mcp-okf"}


def run(args, vault, config=None):
    strict = getattr(args, "strict", False)
    json_out = getattr(args, "json", False)

    smoke_entry = config.health_smoke_entry_point if config else "tp3-cibernetico"
    plugin_hash_enabled = config.features_plugin_hash_sync if config else True

    if not vault.exists():
        msg = f"Error: vault not found at {vault}"
        if json_out:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    results = {}
    all_warnings = []
    all_errors = []

    # 1. Frontmatter
    fm_ok, fm_bad, fm_warn = _check_frontmatter(vault)
    results["frontmatter"] = {"ok": fm_ok, "bad": len(fm_bad), "details": fm_bad[:20]}
    all_errors.extend(fm_bad)
    all_warnings.extend(fm_warn)

    # 2. Índices
    idx_ok, idx_stale = _check_indices(vault)
    results["indices"] = {"ok": idx_ok, "stale": len(idx_stale), "details": idx_stale[:10]}
    all_warnings.extend(idx_stale)

    # 3. Grafo
    graph_data, graph_warn = _check_graph(vault)
    results["graph"] = graph_data
    if graph_data is None:
        all_errors.append("graph: could not be analyzed")
    all_warnings.extend(graph_warn)

    # 4. Links rotos
    broken = _check_broken_links(vault)
    results["broken_links"] = {"count": len(broken), "details": broken[:15]}
    all_warnings.extend(broken[:15])

    # 5. Scripts
    scripts_ok, scripts_failed = _check_scripts(vault, smoke_entry_point=smoke_entry)
    results["scripts"] = {"ok": scripts_ok, "failed": len(scripts_failed), "details": scripts_failed}
    all_errors.extend(scripts_failed)

    # 6. Git hook
    hook_ok, hook_err = _check_git_hook(vault)
    results["git_hook"] = hook_ok
    if hook_err:
        all_errors.append(hook_err)

    # 7. Bloque cyber
    excluded_cyber = config.types_excluded_cyber if config else EXCLUDED_CYBER_TYPES
    cyber_ok, cyber_warn, cyber_err = _check_cyber(vault, excluded_cyber=excluded_cyber)
    results["cyber"] = {"ok": cyber_ok, "warnings": len(cyber_warn),
                        "errors": len(cyber_err),
                        "details": cyber_err[:10] + cyber_warn[:10]}
    all_warnings.extend(cyber_warn)
    all_errors.extend(cyber_err)

    # 8. Sincronización plugin↔spec
    checks_total = 7
    plugin_ok, plugin_stale = 0, []  # init: el check 8 puede estar desactivado
    if plugin_hash_enabled:
        checks_total = 8
        plugin_ok, plugin_stale = _check_plugin_hash_sync(vault)
        results["plugin_hash_sync"] = {"ok": plugin_ok, "stale": len(plugin_stale),
                                       "details": plugin_stale}
        all_warnings.extend(plugin_stale)
    else:
        results["plugin_hash_sync"] = {"ok": 0, "stale": 0,
                                       "details": ["desactivado (features.plugin_hash_sync: false)"]}

    # 9. Timestamp vs git
    ts_ok, ts_warn, ts_err = _check_timestamp_git(vault)
    results["timestamp_git"] = {"ok": ts_ok, "warnings": len(ts_warn),
                                "errors": len(ts_err),
                                "details": ts_warn[:10] + ts_err[:5]}
    all_warnings.extend(ts_warn)
    all_errors.extend(ts_err)

    # 10. Versión instalada vs última release del repo (no PyPI)
    # Informativo, NO bloquea el health: un tercero con versión vieja debe poder
    # commitear igual; el reporte le dice que actualice. Solo se muestra.
    version_info = _check_version(vault)
    results["version"] = version_info

    # Score
    checks_total = 8  # base 8 (1-8)
    score_items = [
        1 if len(fm_bad) == 0 else 0,
        1 if len(idx_stale) == 0 else 0,
        1 if graph_data and graph_data.get("orphans", 99) == 0 else 0,
        1 if len(broken) == 0 else 0,
        1 if len(scripts_failed) == 0 else 0,
        1 if hook_ok else 0,
        1 if len(cyber_err) == 0 else 0,
        1 if len(ts_err) == 0 and len(ts_warn) == 0 else 0,  # check 9
    ]
    if plugin_hash_enabled:
        checks_total += 1
        score_items.append(1 if len(plugin_stale) == 0 else 0)
    checks_ok = sum(score_items)

    if json_out:
        print(json.dumps({
            "score": f"{checks_ok}/{checks_total}",
            "errors": len(all_errors),
            "warnings": len(all_warnings),
            "error_details": all_errors[:20],
            "warning_details": all_warnings[:20],
            "results": results,
        }, indent=2, ensure_ascii=False))
        return 0 if not strict or (not all_errors and not all_warnings) else 1

    # Reporte legible
    print("🔍 OKF Vault Health Check")
    print("─" * 54)

    if fm_bad:
        print(f"❌ Frontmatter: {fm_ok} ok, {len(fm_bad)} errors")
        for e in fm_bad[:5]:
            print(f"   - {e}")
    else:
        print(f"✅ Frontmatter: {fm_ok}/{fm_ok} files compliant")

    total_idx = idx_ok + len(idx_stale)
    if idx_stale:
        print(f"⚠️  Indexes: {idx_ok}/{total_idx} in sync, {len(idx_stale)} outdated")
        for e in idx_stale[:3]:
            print(f"   - {e}")
    else:
        print(f"✅ Indexes: {total_idx}/{total_idx} in sync")

    if graph_data:
        g = graph_data
        gt = g.get("tags", {})
        tag_str = f", {gt.get('total', 0)} tags ({gt.get('shared', 0)} shared)"
        if g["orphans"] == 0:
            print(f"✅ Graph: {g['nodes']} nodes, {g['edges']} edges, 0 orphans, "
                  f"density {g['density']:.3f}{tag_str}")
        else:
            print(f"⚠️  Graph: {g['nodes']} nodes, {g['edges']} edges, {g['orphans']} orphans, "
                  f"density {g['density']:.3f}{tag_str}")
    else:
        print("❌ Graph: could not be analyzed")

    if broken:
        print(f"⚠️  Broken links: {len(broken)}")
        for b in broken[:3]:
            print(f"   - {b}")
    else:
        print("✅ Links: no broken links detected")

    total_scr = scripts_ok + len(scripts_failed)
    if scripts_failed:
        print(f"❌ Scripts: {scripts_ok}/{total_scr} functional")
        for f in scripts_failed:
            print(f"   - {f}")
    else:
        print(f"✅ Scripts: {total_scr}/{total_scr} functional")

    if hook_ok:
        print("✅ Git hook: pre-commit present and executable")
    else:
        print(f"❌ Git hook: {hook_err}")

    if cyber_err:
        print(f"❌ Cyber block: {cyber_ok} ok, {len(cyber_err)} errors")
        for e in cyber_err[:3]:
            print(f"   - {e}")
    elif cyber_warn:
        print(f"⚠️  Cyber block: {cyber_ok} ok, {len(cyber_warn)} warnings")
    else:
        print(f"✅ Cyber block: {cyber_ok}/{cyber_ok} compliant")

    total_plugins = plugin_ok + len(plugin_stale)
    if plugin_stale:
        print(f"⚠️  Plugin↔spec: {plugin_ok}/{total_plugins} in sync")
        for e in plugin_stale[:3]:
            print(f"   - {e}")
    elif total_plugins > 0:
        print(f"✅ Plugin↔spec: {total_plugins}/{total_plugins} in sync")

    # Timestamp vs git
    total_ts = ts_ok + len(ts_warn) + len(ts_err)
    if ts_err:
        print(f"❌ Timestamp↔git: {ts_ok}/{total_ts} ok, {len(ts_err)} errors")
        for e in ts_err[:3]:
            print(f"   - {e}")
    elif ts_warn:
        print(f"⚠️  Timestamp↔git: {ts_ok}/{total_ts} ok, {len(ts_warn)} warnings")
        for w in ts_warn[:3]:
            print(f"   - {w}")
    else:
        print(f"✅ Timestamp↔git: {ts_ok}/{total_ts} match")

    # Versión instalada vs última release (informativo, no afecta el score)
    v = version_info
    if v.get("latest") and v.get("up_to_date") is not None:
        if v.get("ahead"):
            estado = (f"adelantada a la última release publicada (v{v['latest']}) "
                      f"— sin verificar")
        elif v["up_to_date"]:
            estado = "actualizada"
        else:
            estado = f"desactualizada — última: {v['latest']}"
        print(f"ℹ️  Versión: {v['local']} ({estado}) · {v['source']}")
    else:
        print(f"ℹ️  Versión: {v['local']} (última release no verificable sin red) · {v['source']}")

    print("─" * 54)
    if all_errors:
        print(f"🔴 Health: {checks_ok}/{checks_total} — {len(all_errors)} errors, "
              f"{len(all_warnings)} warnings")
    elif all_warnings:
        print(f"🟡 Health: {checks_ok}/{checks_total} — {len(all_warnings)} warnings")
    else:
        print(f"🟢 Health: {checks_ok}/{checks_total} — all clear")

    if strict and (all_errors or all_warnings):
        return 1
    return 0
