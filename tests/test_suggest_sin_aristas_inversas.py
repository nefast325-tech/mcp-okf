"""suggest-edge-types no propone como ALTA una arista que cierra un ciclo.

Medido en skynet-vault el 22-oct-2026 (v0.4.30): de 391 sugerencias ALTA, 68
eran la referencia de vuelta de una arista ya tipada en sentido contrario, y 26
pares salían ALTA en las dos direcciones. Todos los tipos son direccionales, así
que aplicar cualquiera de esas escribe un ciclo (A refina B y B refina A).
"""

import tempfile
import unittest
from pathlib import Path

from cli.commands.graph import build_graph, _cmd_suggest_edge_types

FM = """---
type: Decision
title: "{t}"
description: "Decisión {t}"
timestamp: 2026-01-01T00:00:00-05:00
created: 2026-01-01T00:00:00-05:00
{links}---
Texto con [[decisions/{otro}.md]]
"""


# Un único tipo válido para Decision → Decision: sin ambigüedad, el par sale
# ALTA (como en un vault que declara sus pares en la config).
DEFS = {"refina": {"description": "x", "valid_pairs": [("Decision", "Decision")]}}


def _bucket(out, conf):
    lines, on = [], False
    for line in out.splitlines():
        if line.startswith("=== "):
            on = line.startswith(f"=== {conf} ")
            continue
        if on and line.strip():
            lines.append(line.strip())
    return lines


class TestSuggestSinAristasInversas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "decisions").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, otro, links=""):
        (self.vault / "decisions" / f"{name}.md").write_text(
            FM.format(t=name, otro=otro, links=links), encoding="utf-8"
        )

    def test_la_referencia_de_vuelta_de_una_arista_tipada_no_es_alta(self):
        # nueva refina vieja (tipada); vieja menciona a nueva sin tipo.
        self._write("nueva", "vieja",
                    "links:\n  - target: decisions/vieja.md\n    type: refina\n")
        self._write("vieja", "nueva")
        out = _cmd_suggest_edge_types(self.vault, build_graph(self.vault), definitions=DEFS)
        alta = _bucket(out, "ALTA")
        self.assertFalse(
            any("decisions/vieja.md → decisions/nueva.md" in l for l in alta),
            f"La inversa de una arista tipada salió ALTA: {alta}",
        )

    def test_apply_no_escribe_la_arista_inversa(self):
        self._write("nueva", "vieja",
                    "links:\n  - target: decisions/vieja.md\n    type: refina\n")
        self._write("vieja", "nueva")
        _cmd_suggest_edge_types(self.vault, build_graph(self.vault), apply=True,
                                definitions=DEFS)
        texto = (self.vault / "decisions" / "vieja.md").read_text(encoding="utf-8")
        self.assertNotIn("target: decisions/nueva.md", texto)

    def test_un_par_sugerido_en_las_dos_direcciones_no_es_alta(self):
        self._write("a", "b")
        self._write("b", "a")
        out = _cmd_suggest_edge_types(self.vault, build_graph(self.vault), definitions=DEFS)
        self.assertEqual(_bucket(out, "ALTA"), [],
                         "Un par mutuo salió ALTA en alguna dirección")

    def test_una_arista_sin_conflicto_sigue_siendo_alta(self):
        # Control: un solo wikilink, sin inversa, mantiene su confianza.
        self._write("a", "b")
        (self.vault / "decisions" / "b.md").write_text(
            FM.format(t="b", otro="zzz", links="").replace(
                "Texto con [[decisions/zzz.md]]", "Sin enlaces"),
            encoding="utf-8",
        )
        out = _cmd_suggest_edge_types(self.vault, build_graph(self.vault), definitions=DEFS)
        alta = _bucket(out, "ALTA")
        self.assertTrue(any("decisions/a.md → decisions/b.md" in l for l in alta),
                        f"El control perdió su ALTA: {out}")


if __name__ == "__main__":
    unittest.main()
