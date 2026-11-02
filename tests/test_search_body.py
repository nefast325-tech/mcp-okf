"""`search --body`: la búsqueda también lee el cuerpo de la nota.

Contrato (2026-11-01): `search --query` es substring AND sobre título,
descripción y tags; no lee el cuerpo. Un agente que busca «formulario» y
recibe 2 de 9 notas concluye «no existe» sobre una muestra silenciosa.

- Sin `--body` el comportamiento no cambia, pero cuando hay pocos resultados
  (<5) el pie dice cuántas notas más mencionan la consulta solo en el cuerpo.
- Con `--body` esas notas salen después de las de frontmatter, marcadas
  `(cuerpo)` en la tabla y con `match: "body"` en JSON.

Se invoca el CLI como subproceso, igual que el server MCP.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SearchBodyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        _write(
            self.vault / ".okf.config.yaml",
            "types:\n  valid: [Decision, Plan]\n"
            "features:\n  cognitive_trace: false\n",
        )
        # Coincide en el frontmatter (descripción)
        _write(
            self.vault / "decisions" / "leads-al-crm.md",
            '---\ntype: Decision\ntitle: "Leads al CRM"\n'
            'description: "El formulario del sitio escribe en el CRM"\n'
            "status: aceptada\ntags: [leads]\n---\n\nNada más que decir.\n",
        )
        # Coincide SOLO en el cuerpo
        _write(
            self.vault / "plans" / "sitio-cliente.md",
            '---\ntype: Plan\ntitle: "Sitio del cliente"\n'
            'description: "Publicar la portada y medir las visitas"\n'
            "status: pendiente\ntags: [sitio]\n---\n\n"
            "Falta conectar el formulario de contacto con el CRM.\n",
        )
        # No coincide en ningún lado
        _write(
            self.vault / "plans" / "otro.md",
            '---\ntype: Plan\ntitle: "Otro plan"\n'
            'description: "Sin relación"\nstatus: pendiente\n---\n\nTexto ajeno.\n',
        )

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["OKF_VAULT"] = str(self.vault)
        env["OKF_SESSION_PURPOSE"] = "test"
        cmd = [sys.executable, "-m", "cli", "--vault", str(self.vault)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO))

    def test_sin_body_no_lee_el_cuerpo_y_avisa_al_pie(self):
        r = self.cli("search", "--query", "formulario")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("decisions/leads-al-crm.md", r.stdout)
        self.assertNotIn("plans/sitio-cliente.md", r.stdout)
        self.assertIn("1 concept(s)", r.stdout)
        self.assertIn('(1 nota(s) más mencionan "formulario" en el cuerpo; '
                      "--body para verlas)", r.stdout)
        self.assertNotIn("(cuerpo)", r.stdout)

    def test_sin_body_el_json_no_cambia(self):
        r = self.cli("search", "--query", "formulario", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual([c["file"] for c in data], ["decisions/leads-al-crm.md"])
        self.assertNotIn("match", data[0])
        self.assertNotIn("_body", data[0])

    def test_sin_coincidencias_de_cuerpo_no_hay_pie(self):
        r = self.cli("search", "--query", "leads")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("en el cuerpo", r.stdout)

    def test_con_body_encuentra_y_ordena_despues_del_frontmatter(self):
        r = self.cli("search", "--query", "formulario", "--body")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 concept(s)", r.stdout)
        lineas = [l for l in r.stdout.splitlines() if ".md" in l]
        self.assertIn("decisions/leads-al-crm.md", lineas[0])
        self.assertNotIn("(cuerpo)", lineas[0])
        self.assertIn("plans/sitio-cliente.md", lineas[1])
        # Desde la ronda 2 el marcador dice dónde: «(cuerpo L<n> · <k> menc.)».
        self.assertIn("(cuerpo L9 · 1 menc.)", lineas[1])
        self.assertTrue(lineas[1].endswith(")"), lineas[1])
        self.assertNotIn("plans/otro.md", r.stdout)
        self.assertNotIn("--body para verlas", r.stdout)

    def test_con_body_el_json_lleva_match(self):
        r = self.cli("search", "--query", "formulario", "--body", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(
            [(c["file"], c["match"]) for c in data],
            [("decisions/leads-al-crm.md", "frontmatter"),
             ("plans/sitio-cliente.md", "body")],
        )
        self.assertNotIn("_body", data[0])

    def test_con_body_sigue_siendo_and_entre_tokens(self):
        # «formulario» y «contacto» están ambos en el cuerpo del plan: coincide.
        # Un token que no aparece en ningún lado sigue vaciando el resultado.
        r = self.cli("search", "--query", "formulario contacto", "--body", "--json")
        self.assertEqual([c["file"] for c in json.loads(r.stdout)],
                         ["plans/sitio-cliente.md"])
        r = self.cli("search", "--query", "formulario inexistente", "--body", "--json")
        self.assertEqual(json.loads(r.stdout), [])


if __name__ == "__main__":
    unittest.main()
