"""Tope por defecto de `traverse` (`traverse.budget` en .okf.config.yaml).

Medido el 19-nov-2026: con 131 notas los agentes hicieron 262 traverse y el
más grande alcanzó 87 nodos (8.700 tokens); con 10.131 notas, una nota a la
que apuntan 400 devolvía más de lo que un agente puede leer de una vez.
`--budget` ya existía pero había que pedirlo. Ahora, sin --budget, rige el tope
del vault (100 por defecto); --budget 0 lo quita. Mientras el tope no corta,
la salida es byte a byte la de antes.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cli.commands.traverse import run as traverse_run
from cli.config import Config

N_HOJAS = 12  # notas que apuntan al nodo central: 13 alcanzables con él


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    CONFIG = ""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        _write(self.vault / ".okf.config.yaml",
               "types:\n  valid: [Insight]\nfeatures:\n  cognitive_trace: false\n" + self.CONFIG)
        _write(self.vault / "insights" / "centro.md",
               '---\ntype: Insight\ntitle: "Centro"\nstatus: vigente\ndescription: "Al que todos apuntan."\n---\n\nC.\n')
        for i in range(N_HOJAS):
            _write(self.vault / "insights" / f"hoja-{i:02d}.md",
                   f'---\ntype: Insight\ntitle: "Hoja {i}"\nstatus: vigente\ndescription: "Una hoja."\n'
                   "links:\n  - target: insights/centro.md\n    type: extiende\n---\n\nH.\n")
        self.config = Config(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def traverse(self, budget=None, json_out=False, esperado=0):
        args = SimpleNamespace(target="insights/centro.md", seeds=None, depth=1, direction="in",
                               json=json_out, no_cyber=True, edge_type="extiende", filter=True,
                               all_versions=False, compact=True, budget=budget)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = traverse_run(args, self.vault, self.config)
        self.assertEqual(rc, esperado, err.getvalue())
        return json.loads(out.getvalue()) if json_out else out.getvalue()


class TestTopeDelVault(Base):
    CONFIG = "traverse:\n  budget: 5\n"

    def test_sin_budget_corta_en_el_tope_y_dice_cuantos_quedaron_fuera(self):
        out = self.traverse()
        self.assertIn("✂️  8 nodo(s) más quedaron fuera por el tope del vault (5)", out)
        self.assertIn("pide budget=0", out)
        self.assertIn("📎 recibo: 5 nodos de 13", out)
        self.assertIn("truncado: sí (tope del vault: 5)", out)

    def test_en_json_el_recibo_lo_marca(self):
        r = self.traverse(json_out=True)["receipt"]
        self.assertEqual((r["nodes"], r["reachable"], r["truncated"], r["budget"]), (5, 13, True, 5))
        self.assertTrue(r["budget_default"])

    def test_budget_0_lo_quita(self):
        out = self.traverse(budget=0)
        self.assertNotIn("✂️", out)
        self.assertIn("📎 recibo: 13 nodos de 13", out)
        self.assertIn("truncado: no", out)
        self.assertNotIn("budget", out.splitlines()[-1])

    def test_un_budget_explicito_manda_y_no_agrega_aviso(self):
        out = self.traverse(budget=3)
        self.assertNotIn("✂️", out)
        self.assertIn("truncado: sí (--budget 3)", out)
        self.assertNotIn("budget_default", self.traverse(budget=3, json_out=True)["receipt"])

    def test_budget_negativo_es_error(self):
        self.traverse(budget=-1, esperado=1)


class TestBajoElTope(Base):
    """Sin `traverse:` en la config rige 100: con 13 alcanzables no corta nada."""

    def test_la_salida_es_identica_a_la_de_sin_tope(self):
        self.assertEqual(self.config.traverse_budget, 100)
        self.assertEqual(self.traverse(), self.traverse(budget=0))
        r = self.traverse(json_out=True)["receipt"]
        self.assertIsNone(r["budget"])
        self.assertFalse(r["truncated"])
        self.assertNotIn("budget_default", r)


class TestConfigRara(unittest.TestCase):
    def _config(self, texto):
        with tempfile.TemporaryDirectory() as d:
            _write(Path(d) / ".okf.config.yaml", texto)
            return Config(Path(d)).traverse_budget

    def test_valores_invalidos_vuelven_a_100(self):
        for texto in ("traverse:\n  budget: mucho\n", "traverse:\n  budget: -3\n", "traverse: 7\n"):
            with self.subTest(texto=texto):
                self.assertEqual(self._config(texto), 100)

    def test_0_es_sin_tope(self):
        self.assertEqual(self._config("traverse:\n  budget: 0\n"), 0)


if __name__ == "__main__":
    unittest.main()
