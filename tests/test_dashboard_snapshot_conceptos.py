"""Tests para la sección `conceptos` del snapshot de dashboard-snapshot.

Cubre:
1. Una entrada por concepto (frontmatter con type); los generados
   (index/log/dashboard) y los nodos sin type quedan fuera.
2. Campos file/type/title/status/timestamp/stale/cyber con tipos correctos.
3. cyber por nodo: outcome/vencido/target_metric; null sin bloque cyber.
4. Mapeo FRESH→FRESCO / ATTENTION→ATENCION / STALE→STALE.
5. Orden: STALE → ATENCION → FRESCO → (sin stale), dentro por file.
6. El resto del schema del snapshot queda intacto.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cli.commands.dashboard_snapshot import _build_snapshot, _domain_today, run
from cli.reads_store import increment_reads

# Commit reciente simulado: elimina la señal "no commits" de collect_stale
# en vaults de prueba que no son repos git (build_git_dates_index → vacío).
# Fechas relativas a hoy: con fechas fijas el test se vencía solo. El
# 19-nov-2026 el timestamp por defecto (20-ago) cumplió 91 días, pasó el umbral
# de 90 y la nota sumó una señal más (ATENCION → STALE).
_HOY = datetime.now(timezone.utc)
_RECENT_COMMIT = (_HOY - timedelta(days=2)).isoformat(timespec="seconds")
_RECENT_TS = (_HOY - timedelta(days=10)).strftime("%Y-%m-%dT12:00:00-05:00")


def _concepto(vault, rel, tipo="Decision", status="aplicada",
              timestamp=None, cyber=None, body=""):
    """Escribe un nodo con frontmatter válido en el vault fixture."""
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f"type: {tipo}",
        'title: "Concepto de prueba"',
        'description: "Concepto de prueba con descripcion larga"',
        f"timestamp: {timestamp or _RECENT_TS}",
    ]
    if status is not None:
        fm.append(f"status: {status}")
    if cyber:
        fm.append("cyber:")
        fm.append(f"  outcome: {cyber.get('outcome', 'pending')}")
        if cyber.get("review_on"):
            fm.append(f"  review_on: {cyber['review_on']}")
        fm.append("  target_metric:")
        fm.append(f"    name: {cyber.get('metric_name', 'demometro')}")
    fm.append("---")
    path.write_text("\n".join(fm) + "\n" + body, encoding="utf-8")
    return path


class ConceptosFixture(unittest.TestCase):
    """Vault fixture: conceptos con distintos niveles de staleness y cyber."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        # a ↔ b se linkean entre sí: ambos tienen backlinks
        _concepto(self.vault, "conceptos/a.md", body="Ver [[conceptos/b.md]]\n")
        _concepto(self.vault, "conceptos/b.md", body="Ver [[conceptos/a.md]]\n")
        _concepto(self.vault, "conceptos/cyber.md",
                  cyber={"outcome": "pending", "review_on": "2020-01-01"})
        _concepto(self.vault, "conceptos/cyber-futuro.md",
                  cyber={"outcome": "success",
                         "review_on": (_domain_today()
                                       + timedelta(days=10)).isoformat(),
                         "metric_name": "travesias_vs_busquedas"})
        _concepto(self.vault, "conceptos/sin-status.md", status=None)
        # Minuta de sesión: tiene type, debe entrar como concepto
        # (tipo "Sesion" como en el vault real; stale.py la exime de backlinks)
        _concepto(self.vault, "sesiones/sesion-x.md", tipo="Sesion", status=None)
        # Nodo con frontmatter pero sin type → NO es concepto
        suelta = self.vault / "notas" / "suelta.md"
        suelta.parent.mkdir()
        suelta.write_text("---\ntitle: \"Nota suelta\"\n"
                          'description: "Sin type, sin concepto"\n---\n',
                          encoding="utf-8")
        # Generado: lo excluye find_md_files por nombre
        (self.vault / "index.md").write_text(
            "---\ntype: Decision\ntitle: \"Index\"\n"
            'description: "Generado, no es concepto"\n---\n',
            encoding="utf-8",
        )
        # a y la minuta de sesión son nodos "vivos": tienen lecturas en el
        # store de telemetría. Sin esto, la señal reads=0 (que desde el fix
        # lee .okf/state/reads.jsonl y no el frontmatter) los marcaría
        # ATENCION y no FRESCO.
        increment_reads(self.vault / "conceptos/a.md", self.vault)
        increment_reads(self.vault / "sesiones/sesion-x.md", self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def _snapshot(self):
        return _build_snapshot(self.vault, config=None,
                               db_path="/nonexistent/events.db", source="test")

    def _por_file(self, snap):
        return {c["file"]: c for c in snap["conceptos"]}


class TestConceptosSeccion(ConceptosFixture):
    def test_conceptos_incluyen_todos_los_nodos_con_type(self):
        snap = self._snapshot()
        files = {c["file"] for c in snap["conceptos"]}
        esperados = {
            "conceptos/a", "conceptos/b", "conceptos/cyber",
            "conceptos/cyber-futuro", "conceptos/sin-status",
            "sesiones/sesion-x",
        }
        # index.md (generado) y notas/suelta.md (sin type) quedan fuera
        self.assertEqual(files, esperados)

    def test_campos_y_tipos(self):
        snap = self._snapshot()
        self.assertGreaterEqual(len(snap["conceptos"]), 6)
        for c in snap["conceptos"]:
            self.assertIsInstance(c["file"], str)
            self.assertFalse(c["file"].endswith(".md"))
            self.assertIsInstance(c["type"], str)
            self.assertTrue(c["type"])
            self.assertIsInstance(c["title"], str)
            self.assertIsInstance(c["status"], str)
            self.assertIsInstance(c["timestamp"], (str, type(None)))
            self.assertIsInstance(c["stale"], (dict, type(None)))
            self.assertIsInstance(c["cyber"], (dict, type(None)))
        # stale no nulo en la práctica: todo concepto con frontmatter válido
        # aparece en collect_stale (misma pasada, mismas exclusiones)
        self.assertTrue(all(c["stale"] is not None for c in snap["conceptos"]))
        stale = self._por_file(snap)["conceptos/a"]["stale"]
        for k in ("level", "signal_count", "signals"):
            self.assertIn(k, stale)
        self.assertIsInstance(stale["signal_count"], int)
        self.assertIsInstance(stale["signals"], list)

    def test_status_vacio_si_no_tiene(self):
        snap = self._snapshot()
        self.assertEqual(self._por_file(snap)["conceptos/sin-status"]["status"], "")

    def test_file_slug_sin_extension(self):
        snap = self._snapshot()
        self.assertIn("sesiones/sesion-x", {c["file"] for c in snap["conceptos"]})


class TestCyberPorNodo(ConceptosFixture):
    def test_cyber_review_on_vencido(self):
        snap = self._snapshot()
        cyber = self._por_file(snap)["conceptos/cyber"]["cyber"]
        self.assertIsInstance(cyber, dict)
        self.assertEqual(cyber["outcome"], "pending")
        self.assertEqual(cyber["review_on"], "2020-01-01")
        self.assertTrue(cyber["vencido"])
        self.assertEqual(cyber["target_metric"], "demometro")

    def test_cyber_review_on_futuro_no_vencido(self):
        snap = self._snapshot()
        cyber = self._por_file(snap)["conceptos/cyber-futuro"]["cyber"]
        self.assertEqual(cyber["outcome"], "success")
        self.assertFalse(cyber["vencido"])
        self.assertEqual(cyber["target_metric"], "travesias_vs_busquedas")

    def test_sin_cyber_es_null(self):
        snap = self._snapshot()
        self.assertIsNone(self._por_file(snap)["conceptos/a"]["cyber"])


class TestNivelesYOrden(ConceptosFixture):
    def test_mapeo_fresh_attention_stale(self):
        with mock.patch("cli.commands.stale.git_last_commit_date",
                        return_value=_RECENT_COMMIT):
            # Viejo + sin status + sin backlinks → 3 señales → STALE
            _concepto(self.vault, "conceptos/viejo.md",
                      timestamp="2025-01-01T12:00:00-05:00", status=None)
            snap = self._snapshot()
        por_file = self._por_file(snap)
        self.assertEqual(por_file["conceptos/viejo"]["stale"]["level"], "STALE")
        self.assertEqual(por_file["conceptos/cyber"]["stale"]["level"], "ATENCION")
        self.assertEqual(por_file["conceptos/a"]["stale"]["level"], "FRESCO")
        self.assertEqual(por_file["sesiones/sesion-x"]["stale"]["level"], "FRESCO")
        niveles = {c["stale"]["level"] for c in snap["conceptos"]}
        self.assertEqual(niveles, {"STALE", "ATENCION", "FRESCO"})

    def test_orden_stale_atencion_fresco_por_file(self):
        with mock.patch("cli.commands.stale.git_last_commit_date",
                        return_value=_RECENT_COMMIT):
            _concepto(self.vault, "conceptos/viejo.md",
                      timestamp="2025-01-01T12:00:00-05:00", status=None)
            snap = self._snapshot()
        orden = {"STALE": 0, "ATENCION": 1, "FRESCO": 2}
        niveles = [orden[c["stale"]["level"]] for c in snap["conceptos"]]
        self.assertEqual(niveles, sorted(niveles))
        for nivel in ("STALE", "ATENCION", "FRESCO"):
            files = [c["file"] for c in snap["conceptos"]
                     if c["stale"]["level"] == nivel]
            self.assertEqual(files, sorted(files))


class TestSchemaIntacto(ConceptosFixture):
    def test_resto_del_schema_sigue_igual(self):
        run(SimpleNamespace(db="/nonexistent/events.db", source="test",
                            canvas=False), self.vault, None)
        snap = json.loads((self.vault / "dashboard.json").read_text(
            encoding="utf-8"))
        esperadas = {"generated_at", "generated_by", "source", "health", "graph",
                     "cibernetica", "actividad", "calor_estructural",
                     "conceptos", "negocio", "session_diff", "tendencias"}
        self.assertEqual(set(snap.keys()), esperadas)
        self.assertIsNone(snap["negocio"])
        self.assertIsNone(snap["session_diff"])  # sin db → capa deshabilitada
        for k in ("score", "max_score", "errors", "warnings"):
            self.assertIn(k, snap["health"])
        calor = snap["calor_estructural"]
        self.assertEqual(set(calor["stale_distribution"].keys()),
                         {"FRESCO", "ATENCION", "STALE"})
        # La distribución coincide con la suma de niveles de los conceptos
        # que sí tienen type (la distribución cuenta también nodos sin type)
        conceptos = snap["conceptos"]
        dist = calor["stale_distribution"]
        self.assertGreaterEqual(dist["STALE"],
                                sum(1 for c in conceptos
                                    if c["stale"]["level"] == "STALE"))


if __name__ == "__main__":
    unittest.main()
