"""El tope del smoke de `validate --all` en `health` escala con el tamaño del vault.

Regresión: el smoke corría `validate --all` con un tope fijo de 15 s. Su costo
crece con el vault (un `git show` por archivo y re-parseo de los destinos de las
aristas): en un vault de 326 archivos tardó 19,7–24,2 s (3-nov-2026), `health`
daba 🔴 7/8 por timeout y el pre-commit (`health --strict`) rechazaba todo commit
sobre un vault válido.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.commands import health


def _timeout_de_validate(vault):
    topes = {}

    def fake_run(cmd, *args, **kwargs):
        topes[" ".join(cmd[3:])] = kwargs.get("timeout")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(health.subprocess, "run", side_effect=fake_run):
        health._check_scripts(vault)
    return topes["validate --all"]


def _vault_con(n):
    d = tempfile.mkdtemp()
    for i in range(n):
        Path(d, f"nota-{i}.md").write_text(
            f"---\ntype: Insight\ntitle: Nota {i}\ndescription: d\n---\ncuerpo\n")
    return d


class TestSmokeValidateTimeout(unittest.TestCase):
    def test_vault_grande_recibe_tope_proporcional(self):
        tope = _timeout_de_validate(_vault_con(326))
        self.assertGreaterEqual(
            tope, 326 * health.VALIDATE_SMOKE_S_PER_FILE,
            f"validate --all con 326 archivos recibió {tope}s: un tope fijo deja "
            f"health en 7/8 y traba el pre-commit en vaults grandes")

    def test_vault_chico_conserva_el_piso(self):
        self.assertEqual(_timeout_de_validate(_vault_con(3)), health.VALIDATE_SMOKE_MIN_S)

    def test_vault_inexistente_no_rompe(self):
        self.assertEqual(_timeout_de_validate("/tmp/vault-inexistente"),
                         health.VALIDATE_SMOKE_MIN_S)


if __name__ == "__main__":
    unittest.main()
