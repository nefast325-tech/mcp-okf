"""Tests de `traverse --compact`, `--budget` y el recibo.

Un agente que consulta el grafo paga cada traverse en tokens de contexto y no
sabe cuánto va a costar antes de llamar. Estos tests fijan tres cosas:

1. `--compact` (una línea por nodo) pesa menos bytes que la salida normal.
2. `--budget N` devuelve exactamente N nodos y marca la salida como truncada,
   con y sin `--compact`.
3. El recibo aparece siempre: `📎 recibo:` en texto y la clave `receipt` en
   JSON, con nodos devueltos, nodos alcanzables, bytes y tokens (bytes // 4).
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.config import Config
from cli.commands.traverse import run as traverse_run


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TraverseCompactTests(unittest.TestCase):
    """Vault mínimo: un origen con wikilinks a cuatro vecinas (5 alcanzables)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        _write(self.vault / ".okf.config.yaml",
               "types:\n  valid: [Insight]\nfeatures:\n  cognitive_trace: false\n")
        vecinas = ["a", "b", "c", "d"]
        enlaces = " ".join(f"[[insights/{v}]]" for v in vecinas)
        _write(self.vault / "insights" / "origen.md",
               '---\ntype: Insight\ntitle: "Origen"\nstatus: vigente\n'
               'description: "El nodo desde el que arranca el recorrido"\n'
               "tags: [prueba, origen]\n---\n\n" f"Ver {enlaces}.\n")
        descripcion_larga = "x" * 150  # más de 80: --compact la trunca
        for v in vecinas:
            _write(self.vault / "insights" / f"{v}.md",
                   f'---\ntype: Insight\ntitle: "Vecina {v}"\nstatus: vigente\n'
                   f'description: "{descripcion_larga}"\n'
                   "tags: [prueba, vecina, etiqueta-larga-que-pesa]\n---\n\nCuerpo.\n")
        self.config = Config(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def _traverse(self, **kw):
        args = SimpleNamespace(target="insights/origen.md", seeds=None,
                               depth=kw.get("depth", 1), direction="both",
                               json=kw.get("json", False), no_cyber=True,
                               edge_type=None, filter=False, all_versions=False,
                               compact=kw.get("compact", False),
                               budget=kw.get("budget", None))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = traverse_run(args, self.vault, self.config)
        self.assertEqual(rc, 0, err.getvalue())
        return out.getvalue()

    # ── --compact ──

    def test_compact_reduce_bytes(self):
        normal = self._traverse()
        compacto = self._traverse(compact=True)
        self.assertLess(len(compacto.encode("utf-8")), len(normal.encode("utf-8")))

    def test_compact_una_linea_por_nodo_sin_tags_ni_from(self):
        compacto = self._traverse(compact=True)
        lineas_nodo = [l for l in compacto.splitlines() if "insights/" in l and "Traversal" not in l]
        self.assertEqual(len(lineas_nodo), 5)
        self.assertNotIn("🏷️", compacto)
        self.assertNotIn("← insights/origen.md", compacto)  # no repite el `from`
        self.assertIn("· via wikilink", compacto)
        # La descripción de 150 caracteres queda en 80 más la elipsis
        self.assertIn("x" * 80 + "…", compacto)
        self.assertNotIn("x" * 81, compacto)

    def test_compact_no_cambia_el_orden(self):
        def rutas(texto):
            return [l.split()[1] for l in texto.splitlines()
                    if l.lstrip().startswith(("📍", "←", "→"))]
        self.assertEqual(rutas(self._traverse()), rutas(self._traverse(compact=True)))

    # ── --budget ──

    def test_budget_3_devuelve_3_nodos_y_marca_truncado(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                texto = self._traverse(budget=3, compact=compact)
                rutas = [l for l in texto.splitlines()
                         if l.lstrip().startswith(("📍", "←", "→"))]
                self.assertEqual(len(rutas), 3)
                self.assertIn("Nodes: 3", texto)
                self.assertIn("📎 recibo: 3 nodos de 5", texto)
                self.assertIn("truncado: sí (--budget 3)", texto)

    def test_budget_holgado_no_trunca(self):
        texto = self._traverse(budget=50)
        self.assertIn("📎 recibo: 5 nodos de 5", texto)
        self.assertIn("truncado: no (--budget 50)", texto)

    def test_budget_en_json(self):
        data = json.loads(self._traverse(json=True, budget=3))
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["nodes"]), 3)
        self.assertEqual(data["receipt"]["nodes"], 3)
        self.assertEqual(data["receipt"]["reachable"], 5)
        self.assertTrue(data["receipt"]["truncated"])
        self.assertEqual(data["receipt"]["budget"], 3)

    # ── Recibo ──

    def test_el_recibo_aparece_siempre_y_mide_lo_que_lo_precede(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                texto = self._traverse(compact=compact)
                lineas = texto.splitlines()
                self.assertTrue(lineas[-1].startswith("📎 recibo: 5 nodos de 5 · depth 1 · "))
                self.assertIn("truncado: no", lineas[-1])
                # bytes = lo que precede al recibo; tokens = bytes // 4
                cuerpo = "\n".join(lineas[:-1]) + "\n"
                nbytes = len(cuerpo.encode("utf-8"))
                self.assertIn(f"{nbytes} bytes ≈ {nbytes // 4} tokens", lineas[-1])

    def test_el_recibo_en_json(self):
        data = json.loads(self._traverse(json=True))
        r = data["receipt"]
        self.assertEqual(r["nodes"], 5)
        self.assertEqual(r["reachable"], 5)
        self.assertEqual(r["depth"], 1)
        self.assertFalse(r["truncated"])
        self.assertIsNone(r["budget"])
        self.assertEqual(r["tokens"], r["bytes"] // 4)
        self.assertGreater(r["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
