"""validate: avisos de apoyo en una nota muerta sin sucesora y de `aplica` hacia una reemplazada.

Caso real (controlresearch-vault, cd1dce5, 1-nov-2026): dos planes pendientes
extendían un plan `cancelada` sin sucesora y cinco tareas aplicaban una
decisión reemplazada; el validador no decía nada porque solo miraba
extiende/fundamenta/depende hacia el estado de reemplazo. Ahora:

- `extiende`/`fundamenta`/`depende` hacia una nota en un estado `dead` de su
  capa (sin sucesora) avisa «muerta, sin sucesora».
- `aplica` hacia una reemplazada avisa «re-anclá la arista», distinto del
  aviso de apoyo, porque el trabajo suele seguir valiendo.
- Una fuente muerta (cancelada) o reemplazada no avisa: es historia.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.config import Config
from cli.commands.validate import _validate_file

TS = "2026-10-31T10:00:00-05:00"


def _config():
    return {
        "types": {
            "valid": ["Decision", "Task", "Plan"],
            "directory": {"Decision": "decisions", "Task": "tasks", "Plan": "plans"},
            "exempt_from_no_backlinks": ["Decision", "Task", "Plan"],
            "states": {"strict": True, "schemas": {
                "compromiso": {"types": ["Decision"], "initial": "propuesta",
                               "states": ["propuesta", "aceptada", "reemplazada"],
                               "replaced": "reemplazada", "pending": ["propuesta"]},
                "trabajo": {"types": ["Task", "Plan"], "initial": "pendiente",
                            "states": ["pendiente", "en-curso", "hecha", "cancelada"],
                            "pending": ["pendiente"], "dead": ["cancelada"]},
            }},
        },
        "graph": {"strict_pairs": True, "inherit_default_pairs": False},
        "edge_types": {
            "corrige": {},
            "extiende": {"valid_pairs": [["Decision", "Decision"], ["Plan", "Plan"]]},
            "aplica": {"valid_pairs": [["Task", "Decision"], ["Plan", "Decision"]]},
        },
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }


def _note(vault, rel, ctype, status, links=None):
    lines = ["---", f"type: {ctype}", f'title: "{Path(rel).stem}"', 'description: "D."',
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
    return p


class TestApoyoEnMuertaYAplica(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(_config()), encoding="utf-8")
        self.config = Config(self.vault)
        # Decisión vieja, corregida por la nueva
        _note(self.vault, "decisions/vieja.md", "Decision", "reemplazada")
        _note(self.vault, "decisions/nueva.md", "Decision", "aceptada",
              links=[("decisions/vieja.md", "corrige")])
        # Planes: uno cancelado sin sucesora, uno hecho
        _note(self.vault, "plans/cancelado.md", "Plan", "cancelada")
        _note(self.vault, "plans/hecho.md", "Plan", "hecha")

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self, rel):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, msg = _validate_file(self.vault / rel, self.vault,
                                     definitions=self.config.edge_type_definitions(),
                                     config=self.config)
        return ok, msg, err.getvalue()

    def test_extiende_hacia_plan_cancelado_avisa_sin_sucesora(self):
        _note(self.vault, "plans/vivo.md", "Plan", "pendiente",
              links=[("plans/cancelado.md", "extiende")])
        ok, msg, err = self.validate("plans/vivo.md")
        self.assertTrue(ok, msg)
        self.assertIn("muerta, sin sucesora", err)
        self.assertIn("plans/cancelado.md", err)

    def test_extiende_hacia_plan_hecho_no_avisa(self):
        _note(self.vault, "plans/sigue.md", "Plan", "pendiente",
              links=[("plans/hecho.md", "extiende")])
        ok, msg, err = self.validate("plans/sigue.md")
        self.assertTrue(ok, msg)
        self.assertNotIn("muerta", err)
        self.assertNotIn("apoyate", err)

    def test_aplica_hacia_reemplazada_pide_re_anclar(self):
        _note(self.vault, "tasks/t1.md", "Task", "pendiente",
              links=[("decisions/vieja.md", "aplica")])
        ok, msg, err = self.validate("tasks/t1.md")
        self.assertTrue(ok, msg)
        self.assertIn("re-anclá", err)
        self.assertNotIn("apoyate", err)

    def test_aplica_hacia_vigente_no_avisa(self):
        _note(self.vault, "tasks/t2.md", "Task", "pendiente",
              links=[("decisions/nueva.md", "aplica")])
        ok, msg, err = self.validate("tasks/t2.md")
        self.assertTrue(ok, msg)
        self.assertNotIn("re-anclá", err)

    def test_fuente_cancelada_no_avisa(self):
        _note(self.vault, "tasks/t3.md", "Task", "cancelada",
              links=[("decisions/vieja.md", "aplica")])
        ok, msg, err = self.validate("tasks/t3.md")
        self.assertTrue(ok, msg)
        self.assertNotIn("re-anclá", err)

    def test_apoyo_en_reemplazada_sigue_avisando_como_antes(self):
        _note(self.vault, "decisions/apoyada.md", "Decision", "aceptada",
              links=[("decisions/vieja.md", "extiende")])
        ok, msg, err = self.validate("decisions/apoyada.md")
        self.assertTrue(ok, msg)
        self.assertIn("apoyate en su versión vigente", err)


if __name__ == "__main__":
    unittest.main()
