"""`search` con texto ordena por relevancia y corta a `limit`.

Medido el 19-nov-2026 con 10.131 notas: las búsquedas reales de los agentes
devolvían en promedio 436 notas en orden de ruta (30.000 tokens por
pregunta) y la nota correcta llegó a salir en el lugar 482. Coincidir sigue
siendo substring AND; cambia el orden (BM25F) y la tabla muestra las 20 más
relevantes con un pie que dice cuántas más hay.

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


def _nota(vault, rel, titulo, descripcion="Una nota.", cuerpo="Texto.", tipo="Insight", status="vigente"):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'---\ntype: {tipo}\ntitle: "{titulo}"\ndescription: "{descripcion}"\n'
                 f"status: {status}\n---\n\n{cuerpo}\n", encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        (self.vault / ".okf.config.yaml").write_text(
            "types:\n  valid: [Insight, Servicio, Task]\nfeatures:\n  cognitive_trace: false\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(REPO), OKF_VAULT=str(self.vault), OKF_SESSION_PURPOSE="test")
        r = subprocess.run([sys.executable, "-m", "cli", "--vault", str(self.vault)] + list(args),
                           capture_output=True, text=True, env=env, cwd=str(REPO))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def rutas(self, salida):
        return [l.split()[0] for l in salida.splitlines() if l.startswith("  ") and ".md" in l.split()[0]]


class TestOrden(Base):
    def test_el_termino_al_inicio_de_palabra_gana_al_que_cae_dentro_de_otra(self):
        # Por ruta saldría primero «a-proyecto»; «cto» está dentro de «proyecto».
        _nota(self.vault, "a-proyecto.md", "Proyecto del producto", "Un proyecto directo.")
        _nota(self.vault, "z-cto.md", "CTO fraccional", "Un CTO por horas.")
        self.assertEqual(self.rutas(self.cli("search", "--query", "cto"))[0], "z-cto.md")

    def test_el_titulo_pesa_mas_que_el_cuerpo(self):
        _nota(self.vault, "a.md", "Otra cosa", "Nada.", cuerpo="De paso: dominio, dominio.")
        _nota(self.vault, "z.md", "Renovar el dominio", "Nada.")
        self.assertEqual(self.rutas(self.cli("search", "--query", "dominio", "--body"))[0], "z.md")

    def test_coincidir_sigue_siendo_and_y_las_parciales_van_despues_marcadas(self):
        _nota(self.vault, "a.md", "Plan de dominio")
        _nota(self.vault, "b.md", "Plan de correo")
        out = self.cli("search", "--query", "plan dominio")
        self.assertEqual(self.rutas(out), ["a.md", "b.md"])
        lineas = {r: l for l in out.splitlines() for r in ("a.md", "b.md") if l.startswith(f"  {r}")}
        self.assertNotIn("parcial", lineas["a.md"])
        self.assertIn("(parcial: 1/2 palabras)", lineas["b.md"])
        # El JSON sin --limit no se completa: solo las que tienen todas las palabras.
        self.assertEqual([c["file"] for c in json.loads(self.cli("search", "--query", "plan dominio", "--json"))],
                         ["a.md"])


class TestCorte(Base):
    def setUp(self):
        super().setUp()
        for i in range(25):
            _nota(self.vault, f"notas/n{i:02d}.md", f"Cliente número {i}")

    def test_la_tabla_muestra_20_y_dice_cuantas_mas(self):
        out = self.cli("search", "--query", "cliente")
        self.assertEqual(len(self.rutas(out)), 20)
        self.assertIn("las 20 más relevantes de 25; hay 5 más", out)

    def test_limit_0_las_muestra_todas_sin_pie(self):
        out = self.cli("search", "--query", "cliente", "--limit", "0")
        self.assertEqual(len(self.rutas(out)), 25)
        self.assertNotIn("más relevantes", out)

    def test_el_json_no_se_corta_salvo_que_se_pida(self):
        self.assertEqual(len(json.loads(self.cli("search", "--query", "cliente", "--json"))), 25)
        self.assertEqual(len(json.loads(self.cli("search", "--query", "cliente", "--json", "--limit", "3"))), 3)

    def test_sin_texto_un_filtro_lista_todo_en_orden_de_ruta(self):
        out = self.cli("search", "--type", "Insight")
        rutas = self.rutas(out)
        self.assertEqual(len(rutas), 25)
        self.assertEqual(rutas, sorted(rutas))
        self.assertNotIn("más relevantes", out)


class TestTildes(Base):
    def test_con_o_sin_tilde_encuentra_lo_mismo(self):
        _nota(self.vault, "a.md", "Página web del cliente", cuerpo="La campaña arranca el lunes.")
        for q in ("pagina", "página", "PAGINA"):
            self.assertEqual(self.rutas(self.cli("search", "--query", q)), ["a.md"], q)
        self.assertEqual(self.rutas(self.cli("search", "--query", "campana", "--body")), ["a.md"])

    def test_los_filtros_campo_valor_no_cambian(self):
        _nota(self.vault, "a.md", "Uno", status="vigente")
        self.assertEqual(self.rutas(self.cli("search", "--query", "status:vigente")), ["a.md"])


class TestParciales(Base):
    def setUp(self):
        super().setUp()
        _nota(self.vault, "decisions/inventario.md", "El inventario de activos vive en el vault")
        _nota(self.vault, "notas/otra.md", "Otra cosa")

    def test_si_ninguna_tiene_todas_las_palabras_completa_con_la_mitad_y_lo_marca(self):
        out = self.cli("search", "--query", "inventario activos suscripciones")
        self.assertEqual(self.rutas(out), ["decisions/inventario.md"])
        self.assertIn("(parcial: 2/3 palabras)", out)
        self.assertIn("0 nota(s) con todas las palabras; se completó con 1", out)

    def test_en_json_dice_cuantas_palabras_tiene(self):
        data = json.loads(self.cli("search", "--query", "inventario activos suscripciones", "--json"))
        self.assertEqual(data, [])  # el JSON no se completa: sin --limit no hay corte ni relleno
        data = json.loads(self.cli("search", "--query", "inventario activos suscripciones", "--json", "--limit", "5"))
        self.assertEqual(data[0]["match"], "partial")
        self.assertEqual(data[0]["terms_matched"], "2/3")

    def test_menos_de_la_mitad_no_entra(self):
        out = self.cli("search", "--query", "inventario cuentas suscripciones")
        self.assertEqual(self.rutas(out), [])

    def test_una_sola_palabra_no_tiene_parciales(self):
        self.assertEqual(self.rutas(self.cli("search", "--query", "suscripciones")), [])

    def test_con_filtro_de_estado_no_se_completa(self):
        out = self.cli("search", "--query", "inventario activos suscripciones", "--status", "vigente")
        self.assertEqual(self.rutas(out), [])

    def test_las_que_tienen_todas_van_primero(self):
        _nota(self.vault, "z/completa.md", "Inventario de activos y suscripciones")
        out = self.cli("search", "--query", "inventario activos suscripciones")
        self.assertEqual(self.rutas(out), ["z/completa.md", "decisions/inventario.md"])


if __name__ == "__main__":
    unittest.main()
