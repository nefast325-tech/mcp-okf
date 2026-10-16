"""El par de una arista se evalúa aunque el target se escriba sin `.md`.

`_check_edge_type_consistency` resolvía el destino con `vault / target` a
secas. Un link `- target: decisions/otra-nota` (sin extensión, como los escribe
el vault OKF) no resolvía: el tipo del destino quedaba en `'?'` y el par era
imposible de declarar en `valid_pairs`, así que `graph.strict_pairs: true` era
inalcanzable en ese vault. Ahora resuelve con `resolve_link`, la MISMA
resolución que `graph` y que el chequeo de wikilinks.
"""

import tempfile
import unittest
from pathlib import Path

import yaml

from cli.commands.validate import _check_edge_type_consistency
from cli.config import Config

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


def _config(vault, strict=False):
    data = {
        "types": {"valid": ["Insight"], "directory": {"Insight": "insights"}},
        "graph": {"strict_pairs": strict, "inherit_default_pairs": False},
        # El vault declara el par que usa, con el destino siempre Insight.
        "edge_types": {"refina": {"valid_pairs": [["Insight", "Insight"]]}},
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }
    (vault / ".okf.config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return Config(vault)


class TestTargetSinExtension(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "insights").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_par_declarado_con_target_sin_md_no_avisa(self):
        _concept(self.vault, "insights/destino.md", "Insight")
        src = _concept(self.vault, "insights/origen.md", "Insight",
                       [("insights/destino", "refina")])
        cfg = _config(self.vault)
        errors, warnings = _check_edge_type_consistency(src, self.vault, config=cfg)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [], "un target sin .md resolvía en '?' y avisaba")

    def test_par_atipico_con_target_sin_md_reporta_el_tipo_real(self):
        _concept(self.vault, "insights/destino.md", "Sistema")
        src = _concept(self.vault, "insights/origen.md", "Insight",
                       [("insights/destino", "refina")])
        cfg = _config(self.vault)
        errors, warnings = _check_edge_type_consistency(src, self.vault, config=cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("'Sistema'", warnings[0], "tiene que tipificar el destino real")
        self.assertNotIn("'?'", warnings[0])

    def test_strict_pairs_lo_marca_error_con_target_sin_md(self):
        _concept(self.vault, "insights/destino.md", "Sistema")
        src = _concept(self.vault, "insights/origen.md", "Insight",
                       [("insights/destino", "refina")])
        cfg = _config(self.vault, strict=True)
        errors, warnings = _check_edge_type_consistency(src, self.vault, config=cfg)
        self.assertEqual(len(errors), 1)
        self.assertEqual(warnings, [])

    def test_target_inexistente_sigue_siendo_interrogacion(self):
        src = _concept(self.vault, "insights/origen.md", "Insight",
                       [("insights/no-existe", "refina")])
        cfg = _config(self.vault)
        errors, warnings = _check_edge_type_consistency(src, self.vault, config=cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("'?'", warnings[0], "un target que no existe no se puede tipificar")


if __name__ == "__main__":
    unittest.main()
