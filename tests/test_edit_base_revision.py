"""Revisión base en edit (cli/revision.py).

Medido antes del cambio: tres agentes leen la misma versión de una nota y cada
uno escribe el cuerpo que leyó más una línea suya. Las tres escrituras salían
«✅ Updated» y al final quedaba una sola línea: dos cambios perdidos sin aviso
(12 de 18 con los tres escribiendo a la vez).
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.commands.edit import run as edit_run
from cli.commands.read import run as read_run
from cli.config import Config
from cli.revision import normalizar, revision_de, revision_de_archivo

NOTA = """---
type: Insight
title: "Nota compartida"
description: "Una nota que varios agentes editan."
status: vigente
tags: [prueba]
created: 2026-01-01T00:00:00-05:00
timestamp: 2026-01-01T00:00:00-05:00
---

Cuerpo original.
"""

REPO = Path(__file__).resolve().parent.parent


class Base(unittest.TestCase):
    CONFIG = {"types": {"valid": ["Insight"]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(self.CONFIG), encoding="utf-8")
        (self.vault / "insights").mkdir()
        self.nota = self.vault / "insights" / "nota.md"
        self.nota.write_text(NOTA, encoding="utf-8")
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def _correr(self, run, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run(args, self.vault, self.config)
        return rc, out.getvalue(), err.getvalue()

    def _edit(self, **kw):
        base = dict(slug="insights/nota", title=None, description=None, tags=None, status=None, resource=None,
                    body=None, body_file=None, links=None, clear_links=False, dry_run=False, fields=None,
                    base_revision=None)
        base.update(kw)
        return self._correr(edit_run, SimpleNamespace(**base))

    def _leer(self):
        """Lo que hace un agente: read, y de ahí el cuerpo y la revisión."""
        rc, out, err = self._correr(read_run, SimpleNamespace(target="insights/nota", offset=1, limit=500, no_touch=True))
        self.assertEqual(rc, 0, err)
        rev = next(l.split("rev:")[1].strip() for l in err.splitlines() if "🔖" in l)
        lineas = [l.split("|", 1)[1] for l in out.splitlines() if "|" in l]
        cuerpo = "\n".join(lineas[lineas.index("---", 1) + 1:]).strip()
        return cuerpo, rev

    def _texto(self):
        return self.nota.read_text(encoding="utf-8")


class TestRevisionEnRead(Base):
    def test_read_da_la_revision_por_stderr_y_no_toca_stdout(self):
        rc, out, err = self._correr(read_run, SimpleNamespace(target="insights/nota", offset=1, limit=500, no_touch=True))
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("1|---"))
        self.assertNotIn("🔖", out)
        self.assertIn(f"🔖 rev: {revision_de_archivo(self.nota)}", err)

    def test_leer_no_cambia_la_revision(self):
        _, antes = self._leer()
        self._correr(read_run, SimpleNamespace(target="insights/nota", offset=1, limit=500, no_touch=False))
        _, despues = self._leer()
        self.assertEqual(antes, despues)

    def test_normalizar_acepta_como_la_pasa_un_agente(self):
        for forma in ("3fa9c1d2e4b7", " 3fa9c1d2e4b7 ", "`3fa9c1d2e4b7`", "rev: 3fa9c1d2e4b7", "REV:3FA9C1D2E4B7"):
            self.assertEqual(normalizar(forma), "3fa9c1d2e4b7")
        self.assertEqual(normalizar(None), "")


class TestEditConBase(Base):
    def test_con_la_revision_vigente_escribe_y_devuelve_la_nueva(self):
        cuerpo, rev = self._leer()
        rc, out, _ = self._edit(body=cuerpo + "\n\n- marca ana", base_revision=rev)
        self.assertEqual(rc, 0)
        self.assertIn("- marca ana", self._texto())
        self.assertIn(f"🔖 rev: {revision_de(self._texto())}", out)
        self.assertNotEqual(revision_de(self._texto()), rev)

    def test_con_una_revision_vieja_no_escribe_y_lo_dice(self):
        cuerpo, rev = self._leer()
        self.assertEqual(self._edit(body=cuerpo + "\n\n- marca ana", base_revision=rev)[0], 0)
        antes = self._texto()
        rc, out, err = self._edit(body=cuerpo + "\n\n- marca beto", base_revision=rev)
        self.assertEqual(rc, 1)
        self.assertEqual(self._texto(), antes)                 # ni un byte
        self.assertIn("cambió desde que la leíste", err)
        self.assertIn(rev, err)
        self.assertNotIn("✅", out)

    def test_tres_leen_lo_mismo_y_no_se_pierde_ningun_cambio(self):
        # El escenario medido: sin revisión base quedaba 1 marca de 3.
        lecturas = {n: self._leer() for n in ("ana", "beto", "cora")}
        aceptadas = []
        for nombre, (cuerpo, rev) in lecturas.items():
            rc, _, _ = self._edit(body=f"{cuerpo}\n\n- marca {nombre}", base_revision=rev)
            if rc == 0:
                aceptadas.append(nombre)
        self.assertEqual(aceptadas, ["ana"])                   # las otras dos se rechazan, con aviso
        for nombre in ("beto", "cora"):                        # releen y reaplican
            cuerpo, rev = self._leer()
            self.assertEqual(self._edit(body=f"{cuerpo}\n\n- marca {nombre}", base_revision=rev)[0], 0)
        for nombre in ("ana", "beto", "cora"):
            self.assertIn(f"- marca {nombre}", self._texto())

    def test_la_revision_tambien_se_comprueba_al_cambiar_solo_un_campo(self):
        _, rev = self._leer()
        self.assertEqual(self._edit(fields=["marca_ana=1"])[0], 0)
        rc, _, err = self._edit(fields=["marca_beto=1"], base_revision=rev)
        self.assertEqual(rc, 1)
        self.assertIn("cambió desde que la leíste", err)

    def test_no_deja_temporales_y_el_candado_vive_fuera_de_las_notas(self):
        cuerpo, rev = self._leer()
        self._edit(body=cuerpo + "\n\nmás", base_revision=rev)
        self.assertEqual(sorted(p.name for p in (self.vault / "insights").iterdir()), ["nota.md"])
        self.assertTrue((self.vault / ".okf" / "state" / "locks").is_dir())


class TestSinBase(Base):
    def test_optional_reemplazar_el_cuerpo_sin_base_escribe_y_avisa(self):
        rc, out, err = self._edit(body="Cuerpo nuevo.")
        self.assertEqual(rc, 0)
        self.assertIn("Cuerpo nuevo.", self._texto())
        self.assertIn("sin --base-revision", err)
        self.assertIn("el cuerpo", err)

    def test_optional_cambiar_campos_estado_o_tags_no_avisa(self):
        for kw in ({"fields": ["marca=1"]}, {"tags": "uno,dos"}, {"description": "Otra línea."}):
            rc, _, err = self._edit(**kw)
            self.assertEqual(rc, 0)
            self.assertNotIn("base-revision", err)

    def test_dry_run_no_avisa_ni_exige(self):
        rc, _, err = self._edit(body="Cuerpo nuevo.", dry_run=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("base-revision", err)
        self.assertIn("Cuerpo original.", self._texto())


class TestRequired(Base):
    CONFIG = {"types": {"valid": ["Insight"]}, "writes": {"base_revision": "required"}}

    def test_required_reemplazar_el_cuerpo_sin_base_no_escribe(self):
        self.assertEqual(self.config.writes_base_revision, "required")
        antes = self._texto()
        rc, _, err = self._edit(body="Cuerpo nuevo.")
        self.assertEqual(rc, 1)
        self.assertEqual(self._texto(), antes)
        self.assertIn("exige --base-revision", err)

    def test_required_los_campos_se_siguen_fundiendo_sin_base(self):
        self.assertEqual(self._edit(fields=["marca_ana=1"])[0], 0)
        self.assertEqual(self._edit(fields=["marca_beto=2"])[0], 0)
        self.assertIn("marca_ana: 1", self._texto())
        self.assertIn("marca_beto: 2", self._texto())

    def test_required_con_base_escribe(self):
        cuerpo, rev = self._leer()
        self.assertEqual(self._edit(body=cuerpo + "\n\nmás", base_revision=rev)[0], 0)


class TestOff(Base):
    CONFIG = {"types": {"valid": ["Insight"]}, "writes": {"base_revision": "off"}}

    def test_off_no_avisa_pero_una_base_vieja_se_sigue_rechazando(self):
        _, rev = self._leer()
        rc, _, err = self._edit(body="Cuerpo nuevo.")
        self.assertEqual(rc, 0)
        self.assertNotIn("base-revision", err)
        self.assertEqual(self._edit(body="Otro cuerpo.", base_revision=rev)[0], 1)


AGENTE = r"""
import re, subprocess, sys
vault, nombre, rondas, repo = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
cli = [sys.executable, "-m", "cli", "--vault", vault]
for n in range(rondas):
    for _ in range(60):                                  # releer y reintentar si otro escribió antes
        r = subprocess.run(cli + ["read", "insights/nota", "--no-touch"], capture_output=True, text=True, cwd=repo)
        rev = re.search(r"rev: ([0-9a-f]{12})", r.stderr).group(1)
        lineas = [l.split("|", 1)[1] for l in r.stdout.splitlines() if "|" in l]
        cuerpo = "\n".join(lineas[lineas.index("---", 1) + 1:]).strip()
        e = subprocess.run(cli + ["edit", "insights/nota", "--body", f"{cuerpo}\n- marca {nombre}-{n}",
                                  "--base-revision", rev], capture_output=True, text=True, cwd=repo)
        if e.returncode == 0:
            break
    else:
        sys.exit(f"{nombre}-{n} no logró escribir")
"""


class TestCarrera(Base):
    def test_tres_procesos_a_la_vez_no_pierden_ninguna_escritura(self):
        # Sin candado, dos procesos comprueban la revisión antes de que ninguno escriba y
        # los dos pasan. Medido antes del cambio: quedaban 6 marcas de 18.
        rondas = 3
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        procesos = [subprocess.Popen([sys.executable, "-c", AGENTE, str(self.vault), n, str(rondas), str(REPO)],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for n in ("ana", "beto", "cora")]
        for p in procesos:
            _, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
        texto = self._texto()
        faltan = [f"{n}-{i}" for n in ("ana", "beto", "cora") for i in range(rondas) if f"- marca {n}-{i}\n" not in texto + "\n"]
        self.assertEqual(faltan, [])


if __name__ == "__main__":
    unittest.main()
