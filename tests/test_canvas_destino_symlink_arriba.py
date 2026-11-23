"""`canvas`: un symlink ARRIBA del vault no es un escape; uno DENTRO sí.

Regresión de v0.4.36: `_canvas_destino` comparaba cada ancestro del destino con el
vault ya resuelto. Si el vault se nombraba por una ruta con un symlink por encima
(`/var` y `/tmp` en macOS, un `~/vaults` enlazado a otro disco), un destino absoluto
nunca coincidía con el vault resuelto y el recorrido seguía hasta ese symlink:
`canvas validate|layout <ruta absoluta>` y `canvas generate --output <ruta absoluta>`
fallaban con «el destino atraviesa un symlink», igual que `generate_canvas` llamada
con un vault sin resolver (así la llaman tres pruebas, rojas en macOS). Por el CLI y
con el destino por defecto no se notaba: el CLI resuelve el vault antes. La regla que
se quería era otra: ningún symlink entre la raíz del vault y el destino.
"""

import tempfile
import unittest
from pathlib import Path

from cli.commands.canvas import _canvas_destino


class CanvasDestinoSymlinkArribaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        real = Path(self._tmp.name).resolve()
        (real / "disco" / "vault" / "mapas").mkdir(parents=True)
        (real / "atajo").symlink_to(real / "disco")
        self.real = real
        self.vault_real = real / "disco" / "vault"
        self.vault_por_atajo = real / "atajo" / "vault"   # mismo vault, nombrado por un symlink

    def tearDown(self):
        self._tmp.cleanup()

    def test_destino_absoluto_por_la_ruta_con_symlink_arriba_se_acepta(self):
        dest = _canvas_destino(self.vault_por_atajo, self.vault_por_atajo / "mapas" / "x.canvas")
        self.assertEqual(dest, self.vault_real / "mapas" / "x.canvas")

    def test_destino_relativo_y_destino_ya_resuelto_siguen_funcionando(self):
        esperado = self.vault_real / "mapas" / "x.canvas"
        self.assertEqual(_canvas_destino(self.vault_por_atajo, "mapas/x.canvas"), esperado)
        self.assertEqual(_canvas_destino(self.vault_por_atajo, esperado), esperado)
        self.assertEqual(_canvas_destino(self.vault_real, esperado), esperado)

    def test_symlink_dentro_del_vault_se_sigue_rechazando(self):
        (self.vault_real / "escape").symlink_to(self.real)
        for vault in (self.vault_real, self.vault_por_atajo):
            for destino in ("escape/x.canvas", vault / "escape" / "x.canvas"):
                with self.subTest(vault=vault, destino=destino):
                    with self.assertRaises(PermissionError):
                        _canvas_destino(vault, destino)

    def test_symlink_interno_que_vuelve_al_vault_tampoco_pasa(self):
        # «vault/vuelta» apunta a la raíz del vault: resuelve adentro, pero es un symlink interno.
        (self.vault_real / "vuelta").symlink_to(self.vault_real)
        with self.assertRaises(PermissionError) as ctx:
            _canvas_destino(self.vault_por_atajo, self.vault_por_atajo / "vuelta" / "mapas" / "x.canvas")
        self.assertIn("symlink", str(ctx.exception))

    def test_archivo_canvas_que_es_symlink_se_rechaza(self):
        (self.vault_real / "real.canvas").write_text('{"nodes":[],"edges":[]}')
        (self.vault_real / "alias.canvas").symlink_to(self.vault_real / "real.canvas")
        with self.assertRaises(PermissionError):
            _canvas_destino(self.vault_por_atajo, self.vault_por_atajo / "alias.canvas")

    def test_fuera_del_vault_y_extension_equivocada_se_rechazan(self):
        with self.assertRaises(PermissionError) as ctx:
            _canvas_destino(self.vault_por_atajo, self.real / "afuera.canvas")
        self.assertIn("fuera del vault", str(ctx.exception))
        with self.assertRaises(PermissionError):
            _canvas_destino(self.vault_por_atajo, "../afuera.canvas")
        with self.assertRaises(PermissionError):
            _canvas_destino(self.vault_por_atajo, "mapas/notas.txt")


if __name__ == "__main__":
    unittest.main()
