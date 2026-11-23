"""`changes`: qué cambió en el vault, leído del historial de git.

Contrato: una línea por cambio (día en hora de Colombia, nota, qué cambió, autor),
del más reciente al más viejo. Reconoce nota nueva, cambio de estado, reemplazo
(arista `corrige` nueva), otras aristas, cabecera, movida y borrada; las ediciones
de solo texto se cuentan al pie y no se listan salvo que se pidan. Filtros:
`--since`/`--until` (inclusive), `--type`, `--kind`, `--to-status`, `--note`.
Sin git, error que se dice. No escribe nada.
"""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cli.config import Config
from cli.vault import apply_config
from cli.commands.changes import run as changes_run

CONFIG = "types:\n  valid: [Decision, Plan]\nfeatures:\n  cognitive_trace: false\n"


def _nota(tipo, titulo, estado, links=(), cuerpo="Cuerpo."):
    cab = f'---\ntype: {tipo}\ntitle: "{titulo}"\ndescription: "{titulo}"\nstatus: {estado}\n'
    if links:
        cab += "links:\n" + "".join(f"  - target: {t}\n    type: {e}\n" for t, e in links)
    return cab + f"---\n\n{cuerpo}\n"


class ChangesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name).resolve() / "vault"
        self.vault.mkdir()
        self._git("init", "-q", "-b", "main")
        self._escribir(".okf.config.yaml", CONFIG)
        self._escribir("decisions/vieja.md", _nota("Decision", "Vieja", "aceptada"))
        self._escribir("plans/uno.md", _nota("Plan", "Uno", "pendiente", [("decisions/vieja.md", "aplica")]))
        self._commit("alta", "Ana", "2026-10-20T10:00:00-05:00")
        self._escribir("plans/uno.md", _nota("Plan", "Uno", "pendiente", [("decisions/vieja.md", "aplica")],
                                           cuerpo="Cuerpo con una frase más."))
        self._commit("texto", "Ana", "2026-10-21T10:00:00-05:00")
        self._escribir("decisions/vieja.md", _nota("Decision", "Vieja", "reemplazada"))
        self._escribir("decisions/nueva.md", _nota("Decision", "Nueva", "aceptada", [("decisions/vieja.md", "corrige")]))
        self._commit("reemplazo", "Beto", "2026-10-26T23:30:00-05:00")
        self._escribir("plans/uno.md", _nota("Plan", "Uno con otro nombre", "cancelada",
                                           [("decisions/nueva.md", "aplica")], cuerpo="Cuerpo con una frase más."))
        self._commit("cancelar", "Ana", "2026-10-29T09:00:00-05:00")
        self.config = Config(self.vault)
        apply_config(self.config)

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args, env=None):
        subprocess.run(["git", "-C", str(self.vault), *args], check=True, capture_output=True,
                       env={**os.environ, **(env or {})})

    def _escribir(self, rel, texto):
        ruta = self.vault / rel
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto, encoding="utf-8")

    def _commit(self, asunto, autor, fecha):
        self._git("add", "-A")
        self._git("-c", f"user.name={autor}", "-c", "user.email=a@example.test", "commit", "-q", "-m", asunto,
                  env={"GIT_AUTHOR_DATE": fecha, "GIT_COMMITTER_DATE": fecha})

    def _run(self, **kw):
        base = dict(since=None, until=None, filter_type=None, kind=None, to_status=None, note=None,
                    limit=None, json=True)
        base.update(kw)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = changes_run(SimpleNamespace(**base), self.vault, self.config)
        return rc, out.getvalue(), err.getvalue()

    def _cambios(self, **kw):
        rc, out, err = self._run(**kw)
        self.assertEqual(rc, 0, err)
        return [(c["date"], c["note"], c["kind"], c["detail"]) for c in json.loads(out)]

    def test_lista_del_mas_reciente_al_mas_viejo_sin_las_ediciones_de_texto(self):
        cambios = self._cambios()
        self.assertEqual([c[:3] for c in cambios], [
            ("2026-10-29", "plans/uno", "estado"),
            ("2026-10-29", "plans/uno", "aristas"),
            ("2026-10-29", "plans/uno", "cabecera"),
            ("2026-10-26", "decisions/nueva", "nueva"),
            ("2026-10-26", "decisions/nueva", "reemplazo"),
            ("2026-10-26", "decisions/vieja", "estado"),
            ("2026-10-20", "decisions/vieja", "nueva"),
            ("2026-10-20", "plans/uno", "nueva"),
        ])
        self.assertEqual(cambios[0][3], "estado: pendiente → cancelada")
        self.assertEqual(cambios[4][3], "reemplaza a decisions/vieja")
        self.assertEqual(cambios[3][3], "nueva (Decision, aceptada)")

    def test_to_status_dice_cuando_paso_a_ese_estado(self):
        self.assertEqual(self._cambios(to_status="reemplazada"),
                         [("2026-10-26", "decisions/vieja", "estado", "estado: aceptada → reemplazada")])
        self.assertEqual(len(self._cambios(to_status="reemplazada, cancelada")), 2)

    def test_since_y_until_son_inclusivos(self):
        self.assertEqual({c[0] for c in self._cambios(since="2026-10-26", until="2026-10-26")}, {"2026-10-26"})
        self.assertEqual({c[0] for c in self._cambios(since="2026-10-27")}, {"2026-10-29"})
        self.assertEqual({c[0] for c in self._cambios(until="2026-10-20")}, {"2026-10-20"})

    def test_la_historia_de_una_nota_incluye_sus_ediciones_de_texto(self):
        kinds = [c[2] for c in self._cambios(note="plans/uno")]
        self.assertEqual(kinds, ["estado", "aristas", "cabecera", "texto", "nueva"])
        self.assertEqual([c[2] for c in self._cambios(note="uno.md", kind="texto")], ["texto"])

    def test_tipo_y_kind_filtran(self):
        self.assertEqual({c[1] for c in self._cambios(filter_type="Decision")}, {"decisions/vieja", "decisions/nueva"})
        self.assertEqual([c[1] for c in self._cambios(kind="nueva", filter_type="plan")], ["plans/uno"])

    def test_tabla_con_pie_y_cuenta_de_texto(self):
        rc, out, err = self._run(json=False)
        self.assertEqual(rc, 0, err)
        self.assertIn("2026-10-26  decisions/vieja", out)
        self.assertIn("estado: aceptada → reemplazada  ·  Beto", out)
        self.assertIn("8 cambio(s) en 3 nota(s), del 2026-10-20 al 2026-10-29", out)
        self.assertIn("1 edición(es) de solo texto no se listan", out)
        self.assertNotIn(".md", out)

    def test_limite(self):
        rc, out, err = self._run(json=False, limit=2)
        self.assertIn("(los 2 más recientes de 8: --limit 0 para todos)", out)

    def test_borrada_y_movida(self):
        self._git("mv", "plans/uno.md", "plans/dos.md")
        (self.vault / "decisions/nueva.md").unlink()
        self._commit("mover y borrar", "Ana", "2026-10-30T09:00:00-05:00")
        cambios = self._cambios(since="2026-10-30")
        self.assertIn(("2026-10-30", "plans/dos", "movida", "movida desde plans/uno"), cambios)
        self.assertIn(("2026-10-30", "decisions/nueva", "borrada", "borrada"), cambios)

    def test_un_merge_cuenta_una_vez_en_la_fecha_del_merge(self):
        # Lo que entra por un PR se lee contra el primer padre: un cambio en la fecha del
        # merge, no otro más en la fecha del commit de la rama.
        self._git("checkout", "-q", "-b", "rama")
        self._escribir("plans/uno.md", _nota("Plan", "Uno con otro nombre", "pendiente",
                                           [("decisions/nueva.md", "aplica")], cuerpo="Cuerpo con una frase más."))
        self._commit("en la rama", "Beto", "2026-10-30T09:00:00-05:00")
        self._git("checkout", "-q", "main")
        fecha = "2026-10-31T09:00:00-05:00"
        self._git("-c", "user.name=Ana", "-c", "user.email=a@example.test", "merge", "-q", "--no-ff",
                  "-m", "merge de la rama", "rama", env={"GIT_AUTHOR_DATE": fecha, "GIT_COMMITTER_DATE": fecha})
        self.assertEqual(self._cambios(since="2026-10-30"),
                         [("2026-10-31", "plans/uno", "estado", "estado: cancelada → pendiente")])
        rc, out, err = self._run(since="2026-10-30")
        self.assertEqual(json.loads(out)[0]["subject"], "merge de la rama")

    def test_md_en_medio_del_nombre_no_se_borra(self):
        # Solo se quita la extensión: «.md» dentro del nombre es parte del slug.
        self._escribir("decisions/usar-agents.md-como-guia.md", _nota("Decision", "Guía", "aceptada"))
        self._escribir("plans/uno.md", _nota("Plan", "Uno con otro nombre", "cancelada",
                                           [("decisions/usar-agents.md-como-guia.md", "aplica")],
                                           cuerpo="Cuerpo con una frase más."))
        self._commit("guía", "Ana", "2026-10-30T09:00:00-05:00")
        cambios = self._cambios(since="2026-10-30")
        self.assertIn(("2026-10-30", "decisions/usar-agents.md-como-guia", "nueva", "nueva (Decision, aceptada)"),
                      cambios)
        self.assertIn(("2026-10-30", "plans/uno", "aristas",
                       "aristas: +aplica decisions/usar-agents.md-como-guia, −aplica decisions/nueva"), cambios)
        self.assertEqual([c[1] for c in self._cambios(note="usar-agents.md-como-guia.md")],
                         ["decisions/usar-agents.md-como-guia"])

    def test_errores_que_se_dicen(self):
        rc, out, err = self._run(kind="estdo")
        self.assertEqual(rc, 1)
        self.assertIn("--kind desconocido: estdo", err)
        rc, out, err = self._run(since="ayer")
        self.assertEqual(rc, 1)
        self.assertIn("ISO 8601", err)

    def test_sin_git_lo_dice(self):
        with tempfile.TemporaryDirectory() as otro:
            vault = Path(otro).resolve() / "v"
            vault.mkdir()
            (vault / ".okf.config.yaml").write_text(CONFIG, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            args = SimpleNamespace(since=None, until=None, filter_type=None, kind=None, to_status=None,
                                   note=None, limit=None, json=False)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = changes_run(args, vault, Config(vault))
            self.assertEqual(rc, 1)
            self.assertIn("no es un repositorio de git", err.getvalue())


if __name__ == "__main__":
    unittest.main()
