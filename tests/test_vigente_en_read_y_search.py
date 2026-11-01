"""La versión vigente también se anuncia en `read` y en `search`.

- `read` de una nota reemplazada imprime, antes del cuerpo y a stdout, el
  bloque «♻️ …» con la vigente (o dice que no hay vigente declarada); una
  nota vigente que corrige a otras imprime «↩️ Corrige a: …».
- `search` cuenta al pie solo las reemplazadas que coinciden con la consulta
  y el tipo, cada una con su vigente; si ninguna coincide no hay pie.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.config import Config
from cli.commands.read import run as read_run
from cli.commands.search import run as search_run

TS = "2026-01-01T00:00:00-05:00"

CONFIG = {
    "types": {
        "valid": ["Decision", "Insight"],
        "directory": {"Decision": "decisions", "Insight": "insights"},
        "states": {"strict": True, "schemas": {
            "conocimiento": {"types": ["Insight"], "initial": "vigente",
                             "states": ["vigente", "en-revision", "superada"],
                             "replaced": "superada"},
            "compromiso": {"types": ["Decision"], "initial": "propuesta",
                           "states": ["propuesta", "aceptada", "reemplazada"],
                           "replaced": "reemplazada"},
        }},
    },
    "graph": {"strict_pairs": False},
    "edge_types": {"corrige": {}},
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}


def _note(vault, rel, ctype, status, title, description, links=None):
    lines = ["---", f"type: {ctype}", f'title: "{title}"', f'description: "{description}"',
             f"timestamp: {TS}", f"created: {TS}", f"status: {status}"]
    if links:
        lines.append("links:")
        for target, etype in links:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {etype}")
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n\nCuerpo.\n", encoding="utf-8")


class TestVigenteEnReadYSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
        self.config = Config(self.vault)
        # vieja (reemplazada) ← corrige ← nueva (aceptada)
        _note(self.vault, "decisions/vieja.md", "Decision", "reemplazada",
              "El CRM es Twenty", "Decidimos usar twenty como CRM.")
        _note(self.vault, "decisions/nueva.md", "Decision", "aceptada",
              "El CRM es propio", "El CRM deja de depender de terceros.",
              links=[("decisions/vieja.md", "corrige")])
        # huérfana: reemplazada sin que nadie la corrija
        _note(self.vault, "insights/huerfana.md", "Insight", "superada",
              "Insight viejo", "Un hallazgo sobre el formulario que nadie corrigió.")
        _note(self.vault, "insights/vivo.md", "Insight", "vigente",
              "Insight vivo", "Un hallazgo sobre los leads.")

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self, target):
        args = SimpleNamespace(target=target, offset=1, limit=500, no_touch=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = read_run(args, self.vault, self.config)
        self.assertEqual(rc, 0, err.getvalue())
        return out.getvalue()

    def _search(self, query, filter_type=None):
        args = SimpleNamespace(json=False, todos=False, all=False, aging=False, query=query,
                               filter_type=filter_type, filter_status=None,
                               cyber_field=None, cyber_value=None, review_due=False,
                               since=None, until=None, with_graph=False, all_versions=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = search_run(args, self.vault, self.config)
        self.assertEqual(rc, 0)
        return out.getvalue()

    # ── read ──────────────────────────────────────────────────────────────

    def test_read_de_una_reemplazada_anuncia_la_vigente_antes_del_cuerpo(self):
        out = self._read("decisions/vieja")
        primera = out.splitlines()[0]
        self.assertTrue(primera.startswith("♻️"), primera)
        self.assertIn("status: reemplazada", primera)
        self.assertIn("Versión vigente: decisions/nueva — El CRM es propio", primera)
        self.assertIn("Leé esa", primera)
        self.assertIn("1|---", out)  # el cuerpo sigue después del bloque

    def test_read_de_una_reemplazada_sin_corrector_lo_dice(self):
        out = self._read("insights/huerfana")
        primera = out.splitlines()[0]
        self.assertIn("status: superada", primera)
        self.assertIn("sin versión vigente declarada", primera)

    def test_read_de_la_correctora_dice_a_quien_corrige(self):
        out = self._read("decisions/nueva")
        self.assertTrue(out.startswith("↩️  Corrige a: decisions/vieja"), out.splitlines()[0])
        self.assertNotIn("♻️", out)

    def test_read_de_una_vigente_comun_no_agrega_nada(self):
        out = self._read("insights/vivo")
        self.assertTrue(out.startswith("1|---"), out.splitlines()[0])

    # ── search ────────────────────────────────────────────────────────────

    def test_search_que_solo_coincide_con_la_reemplazada_muestra_la_flecha(self):
        out = self._search("twenty", filter_type="Decision")
        self.assertIn("(no results)", out)
        self.assertIn("1 nota(s) reemplazada(s) coinciden y se ocultan", out)
        self.assertIn("decisions/vieja → vigente decisions/nueva", out)

    def test_search_reemplazada_sin_corrector_lo_dice_en_el_pie(self):
        out = self._search("formulario")
        self.assertIn("insights/huerfana → sin vigente declarada", out)

    def test_search_sin_reemplazadas_que_coincidan_no_muestra_pie(self):
        out = self._search("leads")
        self.assertIn("insights/vivo.md", out)
        self.assertNotIn("reemplazada(s)", out)
        self.assertNotIn("superseded", out)

    def test_search_el_tipo_tambien_filtra_el_pie(self):
        # «hallazgo» coincide con la huérfana (Insight), pero se pide Decision
        out = self._search("hallazgo", filter_type="Decision")
        self.assertNotIn("reemplazada(s)", out)


if __name__ == "__main__":
    unittest.main()
