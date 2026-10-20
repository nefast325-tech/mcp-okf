"""Fase 5 del rediseño: la iniciativa como campo validado y el alcance por campo.

- cli/fields.py: `types.fields.common` aplica un esquema a todos los tipos
  salvo `except`; un campo puede llevar lista y cada ítem se valida.
- cli/access.py: `--scope campo=valor` acota la instancia a las notas cuyo
  campo (texto o lista) contiene el valor. Una nota sin el campo queda fuera,
  igual que los índices; el scope por ruta sigue como estaba.

Los tests del scope invocan el CLI como subproceso: el scope es estado de
proceso y así se prueba la interfaz que usa el server MCP.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from cli import access
from cli.config import Config
from cli.fields import check_fields, fields_schema_for

REPO = Path(__file__).resolve().parent.parent
TS = "2026-01-01T00:00:00-05:00"


def _note(vault, rel, ctype, body="Cuerpo.", **fields):
    lines = ["---", f"type: {ctype}", f'title: "{Path(rel).stem}"', 'description: "D."',
             f"timestamp: {TS}", f"created: {TS}"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + f"\n\n{body}\n", encoding="utf-8")
    return p


def _config(strict=True):
    return {
        "types": {
            "valid": ["Decision", "Insight", "Session", "Task"],
            "directory": {"Decision": "decisions", "Insight": "insights",
                          "Session": "sessions", "Task": "tasks"},
            "fields": {"strict": strict,
                       "common": {"fields": {"iniciativa": {
                           "required": True,
                           "values": ["inmobiliaria", "caribe-tropical", "sistema"]}},
                                  "except": ["Session", "Task"]},
                       "schemas": {"Task": {"responsable": {"required": True,
                                                            "values": ["rafael"]}}}},
        },
        "features": {"cognitive_trace": False},
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }


class TestEsquemaComun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(_config()), encoding="utf-8")
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def test_el_comun_aplica_a_todos_salvo_los_exentos(self):
        self.assertIn("iniciativa", fields_schema_for(self.config, "Decision"))
        self.assertIn("iniciativa", fields_schema_for(self.config, "Insight"))
        self.assertIsNone(fields_schema_for(self.config, "Session"))
        # Task está exenta del común pero conserva su propio esquema
        task = fields_schema_for(self.config, "Task")
        self.assertNotIn("iniciativa", task)
        self.assertIn("responsable", task)

    def test_el_esquema_del_tipo_pisa_al_comun_campo_a_campo(self):
        cfg = _config()
        cfg["types"]["fields"]["schemas"]["Insight"] = {"iniciativa": {"required": False}}
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        config = Config(self.vault)
        self.assertEqual(check_fields(config, "Insight", {}), [])
        self.assertEqual(len(check_fields(config, "Decision", {})), 1)

    def test_campo_exigido_y_valores(self):
        self.assertEqual(len(check_fields(self.config, "Decision", {})), 1)
        self.assertEqual(check_fields(self.config, "Decision", {"iniciativa": "sistema"}), [])
        bad = check_fields(self.config, "Decision", {"iniciativa": "shabbo"})
        self.assertEqual(len(bad), 1)
        self.assertIn("no está en los valores", bad[0])

    def test_lista_valida_cada_item(self):
        ok = check_fields(self.config, "Decision", {"iniciativa": ["inmobiliaria", "caribe-tropical"]})
        self.assertEqual(ok, [])
        bad = check_fields(self.config, "Decision", {"iniciativa": ["inmobiliaria", "marte"]})
        self.assertEqual(len(bad), 1)
        self.assertIn("marte", bad[0])
        vacia = check_fields(self.config, "Decision", {"iniciativa": []})
        self.assertEqual(len(vacia), 1)
        self.assertIn("lista vacía", vacia[0])

    def test_validate_lo_aplica(self):
        import contextlib, io
        from cli.commands.validate import _validate_file
        _note(self.vault, "decisions/sin.md", "Decision")
        _note(self.vault, "decisions/con.md", "Decision", iniciativa="[inmobiliaria, sistema]")
        _note(self.vault, "sessions/s.md", "Session")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok_sin, msg = _validate_file(self.vault / "decisions/sin.md", self.vault, config=self.config)
            ok_con, msg2 = _validate_file(self.vault / "decisions/con.md", self.vault, config=self.config)
            ok_ses, msg3 = _validate_file(self.vault / "sessions/s.md", self.vault, config=self.config)
        self.assertFalse(ok_sin)
        self.assertIn("iniciativa", msg)
        self.assertTrue(ok_con, msg2)
        self.assertTrue(ok_ses, msg3)


class TestNormalizeScopePorCampo(unittest.TestCase):
    def test_campo_valor(self):
        self.assertEqual(access.normalize_scope("iniciativa=caribe-tropical"), "iniciativa=caribe-tropical")
        self.assertEqual(access.normalize_scope(" iniciativa = sistema "), "iniciativa=sistema")

    def test_rechaza_formas_invalidas(self):
        for bad in ("=x", "ini ciativa=x", "iniciativa=", "iniciativa=a/b", "iniciativa=.."):
            with self.assertRaises(ValueError, msg=bad):
                access.normalize_scope(bad)

    def test_el_scope_por_ruta_sigue_igual(self):
        self.assertEqual(access.normalize_scope("publico/inmobiliaria/"), "publico/inmobiliaria")
        self.assertFalse(access.is_field_scope("publico"))
        self.assertTrue(access.is_field_scope("iniciativa=x"))


class ScopePorCampoCLITest(unittest.TestCase):
    """Vault con tres iniciativas; la instancia acotada a caribe-tropical."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(_config(strict=False)),
                                                     encoding="utf-8")
        (self.vault / "index.md").write_text('---\ndescription: "Raíz"\n---\n\n# Vault\n',
                                             encoding="utf-8")
        _note(self.vault, "decisions/caribe.md", "Decision", iniciativa="caribe-tropical",
              body="Decisión del Caribe. Ver [[decisions/canal]] y [[decisions/inmo]].")
        _note(self.vault, "decisions/canal.md", "Decision",
              iniciativa="[inmobiliaria, caribe-tropical]",
              body="Canal de atención compartido. Ver [[decisions/inmo]].")
        _note(self.vault, "decisions/inmo.md", "Decision", iniciativa="inmobiliaria",
              body="Margen interno de la cartera: confidencial.")
        _note(self.vault, "decisions/sin-campo.md", "Decision",
              body="Nota vieja sin iniciativa: no debe verse desde una instancia acotada.")

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args, scope=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env.pop("OKF_SCOPE", None)
        cmd = [sys.executable, "-m", "cli", "--vault", str(self.vault)]
        if scope is not None:
            cmd += ["--scope", scope]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO))

    def test_search_solo_ve_las_notas_de_la_iniciativa(self):
        r = self.cli("search", "--json", scope="iniciativa=caribe-tropical")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("decisions/caribe.md", r.stdout)
        self.assertIn("decisions/canal.md", r.stdout)        # lista: también cuenta
        self.assertNotIn("decisions/inmo.md", r.stdout)
        self.assertNotIn("sin-campo", r.stdout)              # sin campo: fuera (fail-closed)
        self.assertNotIn("confidencial", r.stdout)

    def test_read_de_fuera_no_resuelve_y_el_indice_tampoco(self):
        fuera = self.cli("read", "decisions/inmo", scope="iniciativa=caribe-tropical")
        self.assertEqual(fuera.returncode, 1)
        self.assertNotIn("confidencial", fuera.stdout)
        por_nombre = self.cli("read", "inmo", scope="iniciativa=caribe-tropical")
        self.assertEqual(por_nombre.returncode, 1)
        indice = self.cli("read", "index", scope="iniciativa=caribe-tropical")
        self.assertEqual(indice.returncode, 1)
        dentro = self.cli("read", "decisions/caribe", scope="iniciativa=caribe-tropical")
        self.assertEqual(dentro.returncode, 0, dentro.stderr)

    def test_traverse_no_cruza_a_otra_iniciativa(self):
        r = self.cli("traverse", "decisions/caribe", "--depth", "2", "--json", "--no-cyber",
                     scope="iniciativa=caribe-tropical")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("decisions/canal.md", r.stdout)
        self.assertNotIn("decisions/inmo.md", r.stdout)
        desde_fuera = self.cli("traverse", "decisions/inmo", "--json", "--no-cyber",
                               scope="iniciativa=caribe-tropical")
        self.assertEqual(desde_fuera.returncode, 1)

    def test_sin_scope_se_ve_todo_y_un_scope_invalido_aborta(self):
        todo = self.cli("search", "--json")
        self.assertIn("decisions/inmo.md", todo.stdout)
        self.assertIn("decisions/sin-campo.md", todo.stdout)
        malo = self.cli("search", "--json", scope="iniciativa=a/b")
        self.assertEqual(malo.returncode, 1)
        self.assertIn("inválido", malo.stderr)

    def test_el_scope_por_ruta_sigue_funcionando(self):
        r = self.cli("search", "--json", scope="decisions")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("decisions/inmo.md", r.stdout)
        self.assertIn("decisions/sin-campo.md", r.stdout)


if __name__ == "__main__":
    unittest.main()
