"""Con OKF_READONLY, el smoke test de `health` no cuenta `new --dry-run` como fallo.

Regresión (vista el 2026-11-01 con varios agentes sobre un vault): un server MCP
arrancado con OKF_READONLY=true —el perfil de un auditor o de un agente
público— corre `health --strict` y recibe siempre «❌ Scripts: 7/8» y 🔴,
porque el smoke lanza `cli new … --dry-run` y main() rechaza `new` entero en
solo-lectura, aunque --dry-run no escribe. Un agente de solo lectura nunca
veía el vault en verde, sin que el vault tuviera nada mal.

Ahora el smoke omite en solo-lectura los comandos que main() bloquearía, los
devuelve aparte (`skipped`) y health los imprime como omitidos, no como error.
"""
import os
import unittest
from unittest import mock

from cli.commands import health


def _fake_run_factory(llamadas):
    def fake_run(cmd, *args, **kwargs):
        llamadas.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")
    return fake_run


class TestSmokeEnSoloLectura(unittest.TestCase):
    def test_sin_readonly_el_smoke_incluye_new(self):
        llamadas = []
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OKF_READONLY", None)
            with mock.patch.object(health.subprocess, "run", side_effect=_fake_run_factory(llamadas)):
                ok, failed, skipped = health._check_scripts("/tmp/vault-inexistente")
        self.assertIn("new", [c[3] for c in llamadas])
        self.assertEqual(skipped, [])
        self.assertEqual(failed, [])
        self.assertEqual(ok, len(llamadas))

    def test_con_readonly_omite_new_y_no_lo_cuenta_como_fallo(self):
        llamadas = []
        with mock.patch.dict(os.environ, {"OKF_READONLY": "true"}):
            with mock.patch.object(health.subprocess, "run", side_effect=_fake_run_factory(llamadas)):
                ok, failed, skipped = health._check_scripts("/tmp/vault-inexistente")
        self.assertNotIn("new", [c[3] for c in llamadas],
                         "en solo-lectura el smoke no debe lanzar `new`: main() lo rechaza")
        self.assertEqual(failed, [])
        self.assertEqual(ok, len(llamadas))
        self.assertEqual(len(skipped), 1)
        self.assertIn("OKF_READONLY", skipped[0])
        self.assertIn("new", skipped[0])

    def test_con_readonly_los_demas_comandos_siguen_corriendo(self):
        llamadas = []
        with mock.patch.dict(os.environ, {"OKF_READONLY": "true"}):
            with mock.patch.object(health.subprocess, "run", side_effect=_fake_run_factory(llamadas)):
                health._check_scripts("/tmp/vault-inexistente")
        comandos = {c[3] for c in llamadas}
        for esperado in ("graph", "search", "traverse", "validate", "touch", "audit", "review"):
            self.assertIn(esperado, comandos)


if __name__ == "__main__":
    unittest.main()
