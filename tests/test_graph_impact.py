"""Tests de _cmd_impact — análisis de impacto ontológico.

Dos defectos que estos tests fijan:

1. El bloque que trata `depende` no filtraba por tipo de arista: recorría todo
   `typed_in` y etiquetaba cada vecino como "depende de este nodo", cualquiera
   fuese su tipo real. Un vecino unido por `refina` se reportaba además como
   `depende`, que es una afirmación falsa sobre la relación.

2. El conteo mezclaba aristas con nodos: los encabezados usaban `len(lista)`,
   el cuerpo imprimía `sorted(set(lista))` y el total volvía a sumar `len()`.
   Un nodo alcanzado por dos aristas contaba dos veces en "N potentially
   impacted nodes", y el número del encabezado podía no coincidir con las
   líneas impresas debajo.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.commands.graph import _cmd_impact


def _graph(typed_in=None, typed_out=None):
    return {
        "decisions/base.md": {
            "typed_in": typed_in or [],
            "typed_out": typed_out or [],
        },
        "insights/otro.md": {"typed_in": [], "typed_out": []},
    }


class GraphImpactTests(unittest.TestCase):
    def test_refina_no_se_reporta_tambien_como_depende(self):
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "refina", "score": 0.2},
        ])
        out = _cmd_impact(g, "decisions/base.md")
        self.assertIn("refina este nodo", out)
        self.assertNotIn("depende de este nodo", out)

    def test_un_nodo_con_una_arista_cuenta_uno(self):
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "refina", "score": 0.2},
        ])
        out = _cmd_impact(g, "decisions/base.md")
        self.assertIn("Total: 1 potentially impacted nodes.", out)

    def test_un_nodo_con_dos_aristas_sigue_contando_uno(self):
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "refina", "score": 0.2},
            {"target": "insights/otro.md", "type": "aplica", "score": 0.2},
        ])
        out = _cmd_impact(g, "decisions/base.md")
        self.assertIn("Total: 1 potentially impacted nodes.", out)
        # Los dos motivos siguen visibles: deduplicar nodos no pierde información.
        self.assertIn("refina este nodo", out)
        self.assertIn("aplica este nodo", out)

    def test_depende_si_se_reporta_cuando_la_arista_es_depende(self):
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "depende", "score": 0.8},
        ])
        out = _cmd_impact(g, "decisions/base.md")
        self.assertIn("depende de este nodo", out)

    def test_el_encabezado_cuenta_lo_mismo_que_lista(self):
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "refina", "score": 0.2},
            {"target": "insights/otro.md", "type": "aplica", "score": 0.2},
        ])
        out = _cmd_impact(g, "decisions/base.md")
        # El encabezado dice cuántos nodos hay; debajo se lista uno solo.
        self.assertIn("INFORMATIVE (1):", out)

    def test_sin_aristas_tipadas_no_reporta_impacto(self):
        out = _cmd_impact(_graph(), "decisions/base.md")
        self.assertIn("no typed edges indicating impact", out)


if __name__ == "__main__":
    unittest.main()


class GraphImpactNivelPorAristaTests(unittest.TestCase):
    """La arista declarada manda; el score semántico anota, no degrada.

    Caso real (controlresearch-vault, 31-oct-2026): tres decisiones aceptadas
    que `extiende`n una decisión reemplazada caían en «informativo» porque su
    score de tags/descripción era 0.65, y `graph impact` sobre la reemplazada
    no las mostraba como algo a revisar.
    """

    def test_extiende_declarada_con_score_bajo_es_considerar_revision(self):
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "extiende", "score": 0.25},
        ])
        out = _cmd_impact(g, "decisions/base.md")
        self.assertIn("🟡", out)
        self.assertNotIn("🔵", out)
        self.assertIn("ajuste: 0.25", out)
        self.assertIn("bajo", out)

    def test_aplica_y_depende_declaradas_son_revision_obligatoria(self):
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "aplica", "score": 0.3},
        ])
        out = _cmd_impact(g, "decisions/base.md")
        self.assertIn("🔴", out)
        g = _graph(typed_in=[
            {"target": "insights/otro.md", "type": "depende", "score": 0.1},
        ])
        self.assertIn("🔴", _cmd_impact(g, "decisions/base.md"))

    def test_fundamenta_saliente_es_revision_obligatoria(self):
        g = _graph(typed_out=[
            {"target": "insights/otro.md", "type": "fundamenta", "score": 0.2},
        ])
        self.assertIn("🔴", _cmd_impact(g, "decisions/base.md"))

    def test_sin_score_no_anota_ajuste(self):
        g = _graph(typed_in=[{"target": "insights/otro.md", "type": "refina"}])
        out = _cmd_impact(g, "decisions/base.md")
        self.assertIn("🔵", out)
        self.assertNotIn("ajuste", out)
