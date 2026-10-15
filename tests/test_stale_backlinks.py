"""Señal 4 del detector de stale: «no backlinks».

Bug que estos tests fijan: `build_backlinks_index` resolvía cada wikilink por
NOMBRE DE ARCHIVO y no leía las aristas tipadas (`links:` del frontmatter).
Dos efectos medidos en un vault real de 229 conceptos (15-oct-2026): de 58
conceptos marcados «no backlinks», 18 sí tenían backlinks según `graph`.

  1. Basenames repetidos: `[[projects/x/x]]` caía en cualquier `x.md` del
     vault (el último indexado), así que el backlink se le atribuía a otro
     archivo y el verdadero destino quedaba marcado como huérfano.
  2. Aristas tipadas: un concepto al que solo apunta una arista tipada
     quedaba marcado «no backlinks», aunque `graph backlinks` lo listara.

Contrato: la señal 4 usa la misma resolución que `graph` (`build_graph`),
contando wikilinks y aristas tipadas.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.commands.stale import build_backlinks_index


def _concept(vault, rel, body="Cuerpo.", extra_fm="", ctype="Insight"):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\n"
        f"type: {ctype}\n"
        'title: "T"\n'
        'description: "d"\n'
        f"{extra_fm}"
        "---\n\n" + body + "\n",
        encoding="utf-8",
    )


class StaleBacklinksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_wikilink_con_ruta_no_cae_en_otro_archivo_del_mismo_nombre(self):
        # Dos archivos con el mismo basename en carpetas distintas.
        _concept(self.vault, "projects/doral/doral.md", ctype="Project")
        _concept(self.vault, "publico/doral.md", ctype="Project")
        _concept(self.vault, "decisions/d.md",
                 body="Aplica a [[projects/doral/doral|Doral]].", ctype="Decision")
        idx = build_backlinks_index(self.vault)
        self.assertIn("decisions/d.md", idx.get("projects/doral/doral.md", []))
        self.assertNotIn("decisions/d.md", idx.get("publico/doral.md", []))

    def test_arista_tipada_cuenta_como_backlink(self):
        _concept(self.vault, "lessons/l.md", ctype="Lesson")
        _concept(self.vault, "research/r.md", ctype="Research",
                 extra_fm="links:\n  - target: lessons/l.md\n    type: aplica\n")
        idx = build_backlinks_index(self.vault)
        self.assertIn("research/r.md", idx.get("lessons/l.md", []))

    def test_concepto_sin_nada_sigue_sin_backlinks(self):
        _concept(self.vault, "insights/solo.md")
        _concept(self.vault, "insights/otro.md", body="Sin enlaces.")
        idx = build_backlinks_index(self.vault)
        self.assertFalse(idx.get("insights/solo.md"))


if __name__ == "__main__":
    unittest.main()
