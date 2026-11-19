"""traverse marca la arista heredada de una nota reemplazada.

Por defecto el recorrido salta de una nota reemplazada a su versión vigente. La
arista por la que llegó se declaró con la reemplazada, pero la salida decía
«← vigente · via extiende», igual que una arista propia. Medido el 18-nov-2026
con un agente sobre un vault real: a «¿qué decisiones aceptadas fundamenta esta
arquitectura?» sumó a la sucesora en 4 de 5 corridas (respondió 5 en vez de 4).
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.commands.traverse import run as traverse_run
from cli.config import Config

TS = "2026-01-01T00:00:00-05:00"

CONFIG = {
    "types": {
        "valid": ["Decision", "Sistema"],
        "directory": {"Decision": "decisions", "Sistema": "sistema"},
        "states": {"strict": True, "schemas": {
            "compromiso": {"types": ["Decision"], "initial": "propuesta",
                           "states": ["propuesta", "aceptada", "reemplazada"],
                           "replaced": "reemplazada", "pending": ["propuesta"]},
            "infra": {"types": ["Sistema"], "initial": "vigente", "states": ["vigente"]},
        }},
    },
    "graph": {"inherit_default_pairs": False},
    "edge_types": {"corrige": {}, "extiende": {}, "fundamenta": {}},
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}


def _nota(vault, rel, tipo, estado, links=()):
    lineas = ["---", f"type: {tipo}", f'title: "{Path(rel).stem}"', 'description: "D."',
              f"timestamp: {TS}", f"created: {TS}", f"status: {estado}"]
    if links:
        lineas.append("links:")
        for destino, arista in links:
            lineas += [f"  - target: {destino}", f"    type: {arista}"]
    lineas.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lineas) + "\n\nCuerpo.\n", encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
        self.config = Config(self.vault)
        # base  ←extiende─ vieja (reemplazada)  ←corrige─ nueva (aceptada)
        # base  ←extiende─ otra (aceptada)
        # arq ─fundamenta→ vieja, otra
        _nota(self.vault, "decisions/base.md", "Decision", "aceptada")
        _nota(self.vault, "decisions/vieja.md", "Decision", "reemplazada", [("decisions/base.md", "extiende")])
        _nota(self.vault, "decisions/nueva.md", "Decision", "aceptada", [("decisions/vieja.md", "corrige")])
        _nota(self.vault, "decisions/otra.md", "Decision", "aceptada", [("decisions/base.md", "extiende")])
        _nota(self.vault, "sistema/arq.md", "Sistema", "vigente",
              [("decisions/vieja.md", "fundamenta"), ("decisions/otra.md", "fundamenta")])

    def tearDown(self):
        self.tmp.cleanup()

    def _traverse(self, target, direction, edge_type, json_out=False, compact=False, all_versions=False):
        args = SimpleNamespace(target=target, seeds=None, depth=1, direction=direction, json=json_out,
                               no_cyber=True, edge_type=edge_type, filter=True, all_versions=all_versions,
                               compact=compact, budget=None)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = traverse_run(args, self.vault, self.config)
        self.assertEqual(rc, 0, err.getvalue())
        return json.loads(out.getvalue()) if json_out else out.getvalue()

    def _linea(self, salida, nota):
        return next(l for l in salida.splitlines() if nota in l and ("←" in l or "→" in l))


class TestEntrante(Base):
    """«¿Qué decisiones extienden base?»: la vieja la extendía; la nueva solo la reemplaza."""

    def test_json_marca_la_heredada_y_no_la_declarada(self):
        data = self._traverse("decisions/base", "in", "extiende", json_out=True)
        nodos = {n["path"]: n for n in data["nodes"]}
        self.assertTrue(nodos["decisions/nueva.md"].get("inherited"))
        self.assertEqual(nodos["decisions/nueva.md"]["superseded"], ["decisions/vieja.md"])
        self.assertNotIn("inherited", nodos["decisions/otra.md"])
        self.assertEqual(data["inherited"], 1)
        aristas = {e["to"]: e for e in data["result_edges"]}
        self.assertTrue(aristas["decisions/nueva.md"].get("inherited"))
        self.assertNotIn("inherited", aristas["decisions/otra.md"])

    def test_compacto_lo_dice_en_la_linea_del_nodo(self):
        out = self._traverse("decisions/base", "in", "extiende", compact=True)
        self.assertIn("via extiende (heredada, no declarada por esta nota) · ♻️ vigente de: vieja",
                      self._linea(out, "decisions/nueva.md"))
        self.assertTrue(self._linea(out, "decisions/otra.md").rstrip().endswith("· via extiende"))

    def test_la_cabecera_explica_como_leerla(self):
        for compact in (False, True):
            out = self._traverse("decisions/base", "in", "extiende", compact=compact)
            self.assertIn("1 arista heredada", out)
            self.assertIn("NO declara esa arista", out)

    def test_salida_normal_marca_la_linea_via(self):
        out = self._traverse("decisions/base", "in", "extiende")
        via = [l for l in out.splitlines() if l.strip().startswith("via extiende")]
        self.assertEqual(len(via), 2)
        self.assertEqual(sum("heredada" in l for l in via), 1)


class TestSaliente(Base):
    """«¿Qué decisiones fundamenta arq?»: fundamenta a la vieja, no a la nueva."""

    def test_la_sucesora_llega_marcada(self):
        data = self._traverse("sistema/arq", "out", "fundamenta", json_out=True)
        nodos = {n["path"]: n for n in data["nodes"]}
        self.assertTrue(nodos["decisions/nueva.md"].get("inherited"))
        self.assertNotIn("inherited", nodos["decisions/otra.md"])


class TestSinHerencia(Base):
    def test_un_recorrido_sin_reemplazadas_no_cambia(self):
        out = self._traverse("decisions/otra", "out", "extiende", compact=True)
        self.assertNotIn("heredada", out)
        data = self._traverse("decisions/otra", "out", "extiende", json_out=True)
        self.assertEqual(data["inherited"], 0)
        self.assertTrue(all("inherited" not in e for e in data["result_edges"]))

    def test_all_versions_muestra_la_reemplazada_y_no_marca_nada(self):
        data = self._traverse("decisions/base", "in", "extiende", json_out=True, all_versions=True)
        rutas = {n["path"] for n in data["nodes"]}
        self.assertIn("decisions/vieja.md", rutas)
        self.assertNotIn("decisions/nueva.md", rutas)
        self.assertEqual(data["inherited"], 0)

    def test_si_la_vigente_declara_la_arista_manda_la_declarada(self):
        # La nueva, además de corregir a la vieja, extiende base por su cuenta.
        _nota(self.vault, "decisions/nueva.md", "Decision", "aceptada",
              [("decisions/vieja.md", "corrige"), ("decisions/base.md", "extiende")])
        data = self._traverse("decisions/base", "in", "extiende", json_out=True)
        nodos = {n["path"]: n for n in data["nodes"]}
        self.assertNotIn("inherited", nodos["decisions/nueva.md"])
        self.assertEqual(data["inherited"], 0)


if __name__ == "__main__":
    unittest.main()
