"""Pares de aristas: estrictos y sin herencia de los defaults embebidos.

- `graph.inherit_default_pairs: false` → los valid_pairs de cli/edge_types.py
  (vocabulario de otro vault: Criterio, LeccionAprendida…) no se unen; el
  vault declara los suyos. Descripción, transitividad e inversa se conservan.
- `graph.strict_pairs: true` → un par fuera de la lista es ERROR en validate y
  new/edit no escriben la arista. Por defecto sigue siendo aviso.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.config import Config
from cli.edge_types import EDGE_TYPE_DEFINITIONS

TS = "2026-01-01T00:00:00-05:00"


def _concept(vault, rel, ctype, links=None):
    lines = ["---", f"type: {ctype}", 'title: "T"', 'description: "D."',
             f"timestamp: {TS}", f"created: {TS}"]
    if links:
        lines.append("links:")
        for target, etype in links:
            lines += [f"  - target: {target}", f"    type: {etype}"]
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n\nCuerpo.\n", encoding="utf-8")
    return p


def _config(vault, strict=False, inherit=True):
    data = {
        "types": {"valid": ["Lesson", "Insight"],
                  "directory": {"Lesson": "lessons", "Insight": "insights"}},
        "graph": {"strict_pairs": strict, "inherit_default_pairs": inherit},
        # El vault declara el único par que usa.
        "edge_types": {"refina": {"valid_pairs": [["Lesson", "Insight"]]}},
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }
    (vault / ".okf.config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return Config(vault)


class TestInherit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_por_defecto_se_heredan_los_pares_embebidos(self):
        defs = _config(self.vault).edge_type_definitions()
        self.assertIn(("Insight", "Insight"), defs["refina"]["valid_pairs"])
        self.assertIn(("Lesson", "Insight"), defs["refina"]["valid_pairs"])

    def test_sin_herencia_solo_quedan_los_del_vault(self):
        defs = _config(self.vault, inherit=False).edge_type_definitions()
        self.assertEqual(defs["refina"]["valid_pairs"], [("Lesson", "Insight")])
        # Los tipos no declarados quedan sin pares (= sin restricción).
        self.assertEqual(defs["extiende"]["valid_pairs"], [])
        # Las propiedades del tipo se conservan.
        self.assertEqual(defs["extiende"]["inverse"],
                         EDGE_TYPE_DEFINITIONS["extiende"]["inverse"])
        self.assertEqual(defs["fundamenta"]["transitive"],
                         EDGE_TYPE_DEFINITIONS["fundamenta"]["transitive"])


class TestStrictPairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        _concept(self.vault, "insights/i.md", "Insight")
        # Insight refina Lesson: par NO declarado por el vault.
        _concept(self.vault, "lessons/l.md", "Lesson")
        _concept(self.vault, "insights/j.md", "Insight", links=[("lessons/l.md", "refina")])

    def tearDown(self):
        self.tmp.cleanup()

    def _validate(self, config):
        from cli.commands.validate import run
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(SimpleNamespace(target=None, all=True), self.vault, config)
        return code, out.getvalue() + err.getvalue()

    def test_par_atipico_es_aviso_por_defecto(self):
        code, out = self._validate(_config(self.vault, inherit=False))
        self.assertEqual(code, 0)
        self.assertIn("Par atípico", out)

    def test_par_atipico_es_error_con_strict(self):
        code, out = self._validate(_config(self.vault, strict=True, inherit=False))
        self.assertEqual(code, 1)
        self.assertIn("Par atípico", out)

    def test_new_no_escribe_la_arista_con_strict(self):
        from cli.commands.new import run
        config = _config(self.vault, strict=True, inherit=False)
        args = SimpleNamespace(concept_type="Insight", title="K", description="D",
                               tags=None, status=None, resource=None, cyber=False,
                               dry_run=False, body=None, body_file=None,
                               links=["lessons/l.md:refina"], entity=None,
                               filename=None, omit_timestamps=False, fields=None,
                               force=False)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run(args, self.vault, config)
        self.assertEqual(code, 1)
        self.assertIn("Par atípico", err.getvalue())
        self.assertFalse((self.vault / "insights" / "k.md").exists())

    def test_edit_no_escribe_la_arista_con_strict(self):
        from cli.commands.edit import run
        config = _config(self.vault, strict=True, inherit=False)
        args = SimpleNamespace(slug="insights/i.md", title=None, description=None,
                               tags=None, status=None, resource=None, body=None,
                               body_file=None, links=["lessons/l.md:refina"],
                               clear_links=False, fields=None, dry_run=False, force=False)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run(args, self.vault, config)
        self.assertEqual(code, 1)
        self.assertNotIn("links:", (self.vault / "insights" / "i.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
