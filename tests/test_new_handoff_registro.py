"""`new --type Handoff` respeta la capa que el vault declara para Handoff.

Regresión (vista el 2026-11-01 en controlresearch-vault, que pone Handoff en la
capa `registro` con `states: []`): la plantilla de Handoff escribía
`status: pending` siempre, así que cada Handoff nacía inválido —
«'Handoff' no lleva status (capa 'registro', es un registro)»— y había que
borrarle el status con `edit --field status=` antes de poder commitear.

Sin config, o con un esquema que sí declara estados para Handoff, no cambia
nada: `status: pending` sigue siendo el default.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.commands.new import _build_frontmatter, run
from cli.config import Config

SCHEMA = {
    "types": {
        "valid": ["Insight", "Handoff"],
        "directory": {"Insight": "insights", "Handoff": "handoffs"},
        "states": {
            "strict": True,
            "schemas": {
                "conocimiento": {
                    "types": ["Insight"], "initial": "vigente",
                    "states": ["vigente", "superada"],
                    "transitions": {"vigente": ["superada"]},
                    "replaced": "superada", "pending": [],
                },
                "registro": {"types": ["Handoff"], "states": []},
            },
        },
    },
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}


def _args(**kw):
    d = {"concept_type": "Handoff", "title": "Relevo de prueba", "description": "Un relevo",
         "tags": None, "status": None, "resource": None, "cyber": False, "dry_run": False,
         "body": None, "body_file": None, "links": None, "fields": None, "no_timestamps": False,
         "filename": None, "entity": None, "force": False}
    d.update(kw)
    return SimpleNamespace(**d)


class TestHandoffEnCapaRegistro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(SCHEMA), encoding="utf-8")
        (self.vault / "handoffs").mkdir()
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def test_frontmatter_sin_status_cuando_el_vault_lo_declara_registro(self):
        fm = _build_frontmatter("Handoff", "T", "d", None, None, None, False, config=self.config)
        self.assertNotIn("status:", fm)
        self.assertIn("repo_state:", fm, "los demás campos de Handoff se conservan")

    def test_sin_config_sigue_pending(self):
        fm = _build_frontmatter("Handoff", "T", "d", None, None, None, False)
        self.assertIn("status: pending", fm)

    def test_new_crea_un_handoff_que_validate_acepta(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = run(_args(), self.vault, self.config)
        self.assertEqual(code, 0, out.getvalue())
        archivo = self.vault / "handoffs" / "relevo-de-prueba.md"
        contenido = archivo.read_text(encoding="utf-8")
        self.assertNotIn("status:", contenido.split("---")[1])
        from cli.commands.validate import run as validate
        vout = io.StringIO()
        with contextlib.redirect_stdout(vout), contextlib.redirect_stderr(vout):
            vcode = validate(SimpleNamespace(target="handoffs/relevo-de-prueba.md", all=False),
                             self.vault, self.config)
        self.assertEqual(vcode, 0, vout.getvalue())
        self.assertNotIn("no lleva status", vout.getvalue())


if __name__ == "__main__":
    unittest.main()
