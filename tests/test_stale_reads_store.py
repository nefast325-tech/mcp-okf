"""Señal 2 del detector de stale: reads=0 leído del store de telemetría.

Bug que estos tests fijan: `collect_stale` leía el contador con
`fm.get("reads")`, pero desde la decisión 2026-08-27 los read counters ya NO
se escriben en el frontmatter — viven en `<vault>/.okf/state/reads.jsonl`
(`cli/reads_store.py`). La señal era código muerto: cero disparos sobre 660
conceptos reales.

Contrato nuevo:
  - El contador sale del store; ausencia de entrada = 0 lecturas.
  - La señal solo cuenta si el concepto ya tiene antigüedad
    (`timestamp` propio, o último commit) > `reads_zero_min_days`
    (default 30, configurable en `.okf.config.yaml` → `stale.reads_zero_min_days`).
    Un concepto recién creado y todavía no leído no está desconectado de la
    realidad.
  - Un `reads:` legacy en el frontmatter se ignora.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.commands.stale import collect_stale, run
from cli.config import Config
from cli.reads_store import store_path


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _concept(path, days_old, extra_fm="", ctype="Insight"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"type: {ctype}\n"
        'title: "Test"\n'
        'description: "test"\n'
        f'timestamp: "{_iso(days_old)}"\n'
        f"{extra_fm}"
        "---\n\nBody.\n",
        encoding="utf-8",
    )


def _signals(results, rel):
    for r in results:
        if r["file"] == rel:
            return r["signals"]
    raise AssertionError(f"{rel} no está en los resultados")


def _reads_signals(signals):
    return [s for s in signals if s.startswith("reads=0")]


class StaleReadsSignalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _add_read(self, rel, n=1):
        p = store_path(self.vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for _ in range(n):
                f.write(json.dumps({"slug": rel, "ts": "2026-09-01T00:00:00-0500"}) + "\n")

    # ── El contador sale del store ──────────────────────────────────────

    def test_old_concept_without_store_entry_fires(self):
        rel = "insights/viejo.md"
        _concept(self.vault / rel, days_old=60)
        results = collect_stale(self.vault)
        sig = _reads_signals(_signals(results, rel))
        self.assertEqual(len(sig), 1)
        self.assertIn("60d", sig[0])

    def test_store_entry_suppresses_signal(self):
        rel = "insights/viejo.md"
        _concept(self.vault / rel, days_old=60)
        self._add_read(rel)
        results = collect_stale(self.vault)
        self.assertEqual(_reads_signals(_signals(results, rel)), [])

    def test_baseline_zero_in_store_counts_as_zero_reads(self):
        # migrate-reads siembra `baseline: N`; un baseline 0 sigue siendo 0.
        rel = "insights/viejo.md"
        _concept(self.vault / rel, days_old=60)
        p = store_path(self.vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"slug": rel, "ts": "x", "baseline": 0}) + "\n",
                     encoding="utf-8")
        results = collect_stale(self.vault)
        self.assertEqual(len(_reads_signals(_signals(results, rel))), 1)

    # ── Gate de antigüedad ──────────────────────────────────────────────

    def test_young_concept_is_not_flagged(self):
        rel = "insights/nuevo.md"
        _concept(self.vault / rel, days_old=2)
        results = collect_stale(self.vault)
        self.assertEqual(_reads_signals(_signals(results, rel)), [])

    def test_threshold_is_configurable(self):
        rel = "insights/nuevo.md"
        _concept(self.vault / rel, days_old=2)
        results = collect_stale(self.vault, reads_zero_min_days=0)
        self.assertEqual(len(_reads_signals(_signals(results, rel))), 1)

    def test_threshold_above_age_suppresses(self):
        rel = "insights/viejo.md"
        _concept(self.vault / rel, days_old=60)
        results = collect_stale(self.vault, reads_zero_min_days=365)
        self.assertEqual(_reads_signals(_signals(results, rel)), [])

    # ── El frontmatter ya no participa ──────────────────────────────────

    def test_legacy_frontmatter_reads_is_ignored(self):
        rel = "insights/legacy.md"
        _concept(self.vault / rel, days_old=2, extra_fm="reads: 0\n")
        results = collect_stale(self.vault)
        self.assertEqual(_reads_signals(_signals(results, rel)), [])

    def test_legacy_frontmatter_reads_does_not_mask_real_zero(self):
        # reads: 50 en el frontmatter no debe tapar un 0 real del store.
        rel = "insights/legacy.md"
        _concept(self.vault / rel, days_old=60, extra_fm="reads: 50\n")
        results = collect_stale(self.vault)
        self.assertEqual(len(_reads_signals(_signals(results, rel))), 1)

    # ── Detalle y clasificación ─────────────────────────────────────────

    def test_signal_recorded_in_details(self):
        rel = "insights/viejo.md"
        _concept(self.vault / rel, days_old=60)
        results = collect_stale(self.vault)
        r = next(x for x in results if x["file"] == rel)
        self.assertEqual(r["details"].get("reads"), 0)

    def test_no_backlinks_and_no_commits_plus_reads_makes_stale(self):
        # En un vault de prueba sin git: no backlinks + no commits (new?) + reads=0
        # Tipo NO exento: `Insight` dejó de sumar la señal 4 (decisión del
        # triaje dreaming, implementada 2026-10-15 en types.exempt_from_no_backlinks),
        # así que la combinación de 3 señales se prueba con un tipo que sí la suma.
        rel = "research/viejo.md"
        _concept(self.vault / rel, days_old=60, ctype="Research")
        results = collect_stale(self.vault)
        r = next(x for x in results if x["file"] == rel)
        self.assertGreaterEqual(r["signal_count"], 3)
        self.assertEqual(r["level"], "STALE")

    # ── Config ──────────────────────────────────────────────────────────

    def test_config_property_default(self):
        self.assertEqual(Config(self.vault).stale_reads_zero_min_days, 30)

    def test_config_property_reads_yaml_key(self):
        (self.vault / ".okf.config.yaml").write_text(
            "stale:\n  reads_zero_min_days: 7\n", encoding="utf-8")
        self.assertEqual(Config(self.vault).stale_reads_zero_min_days, 7)

    # ── Plumbing de run() ───────────────────────────────────────────────

    def test_run_json_honours_config(self):
        rel = "insights/nuevo.md"
        _concept(self.vault / rel, days_old=2)
        (self.vault / ".okf.config.yaml").write_text(
            "stale:\n  reads_zero_min_days: 0\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # El modo --json siempre sale 0 (solo serializa): el contrato
            # del exit code vive en el modo texto.
            run(SimpleNamespace(json=True), self.vault, Config(self.vault))
        data = json.loads(out.getvalue())
        self.assertEqual(len(_reads_signals(_signals(data, rel))), 1)

    def test_run_text_exit_code_reflects_config(self):
        # Tipo no exento de la señal 4 (ver el test de las 3 señales).
        rel = "research/nuevo.md"
        _concept(self.vault / rel, days_old=2, ctype="Research")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code_default = run(SimpleNamespace(json=False), self.vault, None)
        self.assertEqual(code_default, 0)  # 2 señales: ATTENTION, no STALE
        self.assertEqual(_reads_signals(_signals(
            collect_stale(self.vault), rel)), [])

        (self.vault / ".okf.config.yaml").write_text(
            "stale:\n  reads_zero_min_days: 0\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code_cfg = run(SimpleNamespace(json=False), self.vault, Config(self.vault))
        self.assertEqual(code_cfg, 1)  # 3 señales: STALE
        self.assertIn("STALE", out.getvalue())

    def test_run_json_without_config_uses_default(self):
        rel = "insights/nuevo.md"
        _concept(self.vault / rel, days_old=2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run(SimpleNamespace(json=True), self.vault, None)
        data = json.loads(out.getvalue())
        self.assertEqual(_reads_signals(_signals(data, rel)), [])


class StaleReadsStorePerfTests(unittest.TestCase):
    """El store se lee UNA vez por corrida, no una vez por concepto."""

    def test_get_reads_called_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            for i in range(5):
                _concept(vault / f"insights/c{i}.md", days_old=60)
            calls = []
            import cli.commands.stale as stale_mod
            real = stale_mod.get_reads

            def counting(v):
                calls.append(v)
                return real(v)

            stale_mod.get_reads = counting
            try:
                collect_stale(vault)
            finally:
                stale_mod.get_reads = real
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()