"""Regresión: un concepto muerto no tiene loop cibernético abierto.

`cli review` escaneaba `cyber.review_on` sin mirar `status`, así que un
concepto que completó apoptosis (reemplazada, obsoleta, descartada, cancelada,
deprecada) seguía apareciendo como vencido para siempre — y `health` y el
dashboard lo contaban como loop roto. Detectado el 2026-10-22: los 2 únicos
vencidos del vault eran una Decision `reemplazada` y un Plan `cancelado`.

Contrato (cli/states.py.is_dead_status + cli/commands/review.py.collect_due):

- Con esquema: `dead` de la capa + `replaced` → omitido, y anexado a
  `skipped` si el llamador pasa esa lista.
- Sin esquema: heurística por subcadena sobre los marcadores históricos.
- `hecha`/`aceptada`/`en-curso` NO son muerte: su review sigue siendo tarea.
- `cibernetica.review_on_vencidos` del dashboard hereda el mismo filtro.
"""

import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.commands.dashboard_snapshot import _cibernetica_section
from cli.commands.review import collect_due, get_today_str, run
from cli.config import Config
from cli.states import dead_states, is_dead_status

SCHEMA = {
    "types": {
        "valid": ["Decision", "Insight", "Plan"],
        "directory": {"Decision": "decisions", "Insight": "insights",
                      "Plan": "plans"},
        "states": {
            "strict": False,
            "schemas": {
                "conocimiento": {
                    "types": ["Insight"],
                    "initial": "vigente",
                    "states": ["vigente", "superada", "obsoleta"],
                    "replaced": "superada",
                    "dead": ["superada", "obsoleta"],
                },
                "compromiso": {
                    "types": ["Decision"],
                    "initial": "propuesta",
                    "states": ["propuesta", "aceptada", "reemplazada", "descartada"],
                    "replaced": "reemplazada",
                    "dead": ["reemplazada", "descartada"],
                },
                "trabajo": {
                    "types": ["Plan"],
                    "initial": "pendiente",
                    "states": ["pendiente", "hecha", "cancelada"],
                    "replaced": None,
                    "dead": ["cancelada"],
                },
            },
        },
    },
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}


def _fecha(delta_dias=0):
    hoy = datetime.strptime(get_today_str(), "%Y-%m-%d")
    return (hoy + timedelta(days=delta_dias)).strftime("%Y-%m-%d")


def _concepto(vault, rel, ctype, status, review_on=None, outcome="success"):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"type: {ctype}\n"
        'title: "Concepto de prueba"\n'
        'description: "Concepto de prueba para conceptos muertos"\n'
        f"status: {status}\n"
        "cyber:\n"
        '  sensor: "prueba"\n'
        f"  outcome: {outcome}\n"
        f"  review_on: {review_on or _fecha(-1)}\n"
        "---\n\nCuerpo.\n",
        encoding="utf-8",
    )
    return path


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        data = yaml.safe_load(yaml.safe_dump(SCHEMA))
        (self.vault / ".okf.config.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()


class EstadosMuertosTest(Base):

    def test_dead_states_incluye_replaced(self):
        self.assertEqual(dead_states(self.config, "Insight"),
                         {"superada", "obsoleta"})
        self.assertEqual(dead_states(self.config, "Decision"),
                         {"reemplazada", "descartada"})

    def test_hecha_no_es_muerte(self):
        self.assertFalse(is_dead_status(self.config, "Plan", "hecha"))
        self.assertTrue(is_dead_status(self.config, "Plan", "cancelada"))

    def test_sin_esquema_usa_heuristica(self):
        self.assertTrue(is_dead_status(None, "Insight", "obsoleta"))
        self.assertTrue(is_dead_status(None, "Plan", "cancelada"))
        self.assertFalse(is_dead_status(None, "Insight", "vigente"))


class ReviewOmiteMuertosTest(Base):

    def test_decision_reemplazada_no_aparece(self):
        _concepto(self.vault, "decisions/vieja.md", "Decision", "reemplazada")
        self.assertEqual(collect_due(self.vault, config=self.config), [])

    def test_plan_cancelado_no_aparece(self):
        _concepto(self.vault, "plans/descartado.md", "Plan", "cancelada")
        self.assertEqual(collect_due(self.vault, config=self.config), [])

    def test_aceptada_sigue_siendo_tarea(self):
        _concepto(self.vault, "decisions/viva.md", "Decision", "aceptada")
        due = collect_due(self.vault, config=self.config)
        self.assertEqual([d["file"] for d in due], ["decisions/viva.md"])
        self.assertEqual(due[0]["severity"], "verify")

    def test_mezcla_solo_devuelve_vivos(self):
        _concepto(self.vault, "decisions/muerta.md", "Decision", "descartada")
        _concepto(self.vault, "insights/vieja.md", "Insight", "obsoleta")
        _concepto(self.vault, "insights/viva.md", "Insight", "vigente")
        due = collect_due(self.vault, config=self.config)
        self.assertEqual([d["file"] for d in due], ["insights/viva.md"])

    def test_skipped_anexa_los_muertos(self):
        _concepto(self.vault, "decisions/muerta.md", "Decision", "reemplazada")
        skipped = []
        collect_due(self.vault, config=self.config, skipped=skipped)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["file"], "decisions/muerta.md")
        self.assertEqual(skipped[0]["status"], "reemplazada")

    def test_sin_config_la_heuristica_tambien_filtra(self):
        _concepto(self.vault, "decisions/muerta.md", "Decision", "reemplazada")
        self.assertEqual(collect_due(self.vault, config=None), [])


class ReviewRunTest(Base):

    def _run(self, argv_extra=()):
        args = SimpleNamespace(json=False, count=False)
        out = StringIO()
        with redirect_stdout(out):
            code = run(args, self.vault, self.config)
        return code, out.getvalue()

    def test_solo_muertos_no_es_loop_roto(self):
        _concepto(self.vault, "decisions/muerta.md", "Decision", "reemplazada")
        _concepto(self.vault, "plans/cancelado.md", "Plan", "cancelada")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("Nothing pending review", out)

    def test_menciona_los_omitidos(self):
        _concepto(self.vault, "decisions/muerta.md", "Decision", "reemplazada")
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("1 concepto(s) muerto(s) omitido(s)", out)
        self.assertIn("decisions/muerta", out)


class DashboardOmiteMuertosTest(Base):

    def test_review_on_vencidos_no_cuenta_muertos(self):
        _concepto(self.vault, "decisions/muerta.md", "Decision", "reemplazada")
        _concepto(self.vault, "insights/viva.md", "Insight", "vigente")
        ciber = _cibernetica_section(self.vault, self.config)
        self.assertEqual(ciber["review_on_vencidos"], 1)
        self.assertEqual(ciber["review_on_vencidos_nodes"], ["insights/viva"])


if __name__ == "__main__":
    unittest.main()
