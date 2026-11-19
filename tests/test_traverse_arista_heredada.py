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
from typing import Any
from unittest.mock import patch

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

    def _traverse(self, target, direction, edge_type, json_out=False, compact=False, all_versions=False,
                  budget=None, depth=1, filter_mode=True) -> Any:
        args = SimpleNamespace(target=target, seeds=None, depth=depth, direction=direction, json=json_out,
                               no_cyber=True, edge_type=edge_type, filter=filter_mode, all_versions=all_versions,
                               compact=compact, budget=budget)
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


class TestOrdenBFS(Base):
    """Preferir metadatos propios no debe mover la primera aparición del nodo."""

    def setUp(self):
        super().setUp()
        for rel in ("decisions/vieja.md", "decisions/nueva.md", "decisions/otra.md", "sistema/arq.md"):
            (self.vault / rel).unlink()
        self.base = "decisions/base.md"
        self.vieja = "decisions/a-vieja.md"
        self.otra = "decisions/m-otra.md"
        self.nueva = "decisions/z-nueva.md"

    def _preparar(self, direction):
        entrante = direction != "out"
        _nota(self.vault, self.vieja, "Decision", "reemplazada",
              [(self.base, "extiende")] if entrante else [])
        _nota(self.vault, self.otra, "Decision", "aceptada",
              [(self.base, "extiende")] if entrante else [])
        _nota(self.vault, self.nueva, "Decision", "aceptada",
              [(self.vieja, "corrige")] + ([(self.base, "extiende")] if entrante else []))
        _nota(self.vault, self.base, "Decision", "aceptada",
              [] if entrante else [(p, "extiende") for p in (self.vieja, self.otra, self.nueva)])

    def test_igual_score_preserva_orden_y_prefijo_budget(self):
        # En orden alfabético vieja aparece antes de otra y nueva. Su salto a
        # nueva fija el lugar en el BFS aunque nueva también declare la arista.
        for direction in ("in", "out", "both"):
            self._preparar(direction)
            for filter_mode in (True, False):
                for budget in (None, 2):
                    with self.subTest(direction=direction, filter=filter_mode, budget=budget):
                        data = self._traverse(self.base, direction, "extiende", json_out=True,
                                              budget=budget, filter_mode=filter_mode)
                        expected = [self.base, self.nueva, self.otra]
                        self.assertEqual([n["path"] for n in data["nodes"]], expected[:budget])
                        nueva = data["nodes"][1]
                        self.assertEqual(nueva["superseded"], [self.vieja])
                        self.assertEqual(nueva["from"], self.base)
                        self.assertEqual(nueva["edge_type"], "extiende")
                        self.assertEqual(nueva["depth"], 1)
                        self.assertNotIn("inherited", nueva)
                        self.assertEqual(data["inherited"], 0)
                        self.assertTrue(all("inherited" not in e for e in data["result_edges"]))
                        self.assertEqual(data["receipt"]["reachable"], 3)
                        self.assertEqual(data["receipt"]["nodes"], len(expected[:budget]))
                        self.assertEqual(data["receipt"]["truncated"], budget == 2)

    def test_orden_bfs_se_propaga_a_la_siguiente_profundidad(self):
        self._preparar("in")
        hija_nueva, hija_otra = "decisions/hija-nueva.md", "decisions/hija-otra.md"
        _nota(self.vault, hija_nueva, "Decision", "aceptada", [(self.nueva, "extiende")])
        _nota(self.vault, hija_otra, "Decision", "aceptada", [(self.otra, "extiende")])
        for budget in (None, 4):
            with self.subTest(budget=budget):
                data = self._traverse(self.base, "in", "extiende", json_out=True, depth=2, budget=budget)
                self.assertEqual([n["path"] for n in data["nodes"]],
                                 [self.base, self.nueva, self.otra, hija_nueva, hija_otra][:budget])
                self.assertEqual([n["depth"] for n in data["nodes"]], [0, 1, 1, 2, 2][:budget])

    def test_score_propio_sin_reordenar_los_vecinos(self):
        from cli.commands.graph import build_graph

        # Los scores controlados separan el orden ya establecido de los
        # metadatos preferidos: no reordenar tras elegir la declaración propia.
        for direction in ("in", "out"):
            self._preparar(direction)
            for old_score, own_score in ((0.9, 0.2), (0.2, 0.9), (0.9, 0.0)):
                graph = build_graph(self.vault)
                field = "typed_in" if direction == "in" else "typed_out"
                scores = {self.vieja: old_score, self.otra: 0.7, self.nueva: own_score}
                for entry in graph[self.base][field]:
                    entry["score"] = scores[entry["target"]]
                for budget in (None, 2):
                    with self.subTest(direction=direction, old=old_score, own=own_score, budget=budget):
                        with patch("cli.commands.traverse.build_graph", return_value=graph):
                            data = self._traverse(self.base, direction, "extiende", json_out=True, budget=budget)
                        self.assertEqual([n["path"] for n in data["nodes"]],
                                         [self.base, self.nueva, self.otra][:budget])
                        if own_score > 0:
                            self.assertEqual(data["nodes"][1]["score"], own_score)
                        else:
                            self.assertNotIn("score", data["nodes"][1])
                        self.assertNotIn("inherited", data["nodes"][1])

    def test_declaracion_de_otro_tipo_no_sustituye_la_heredada(self):
        self._preparar("in")
        _nota(self.vault, self.nueva, "Decision", "aceptada",
              [(self.vieja, "corrige"), (self.base, "fundamenta")])
        data = self._traverse(self.base, "in", "extiende", json_out=True, budget=2)
        self.assertEqual([n["path"] for n in data["nodes"]], [self.base, self.nueva])
        self.assertTrue(data["nodes"][1]["inherited"])
        self.assertEqual(data["nodes"][1]["edge_type"], "extiende")
        self.assertTrue(data["result_edges"][0]["inherited"])


if __name__ == "__main__":
    unittest.main()
