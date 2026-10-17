"""`validate` (modo staged) con el vault en un subdirectorio del repo git.

`git diff --name-only` da rutas relativas a la RAÍZ del repo; si el vault vive
en `monorepo/vault/`, `vault / "vault/nota.md"` no existe y el modo staged
descartaba todo en silencio: «✅ 0 file(s) valid», exit 0, ningún commit
bloqueado. Issue #37. `staged_paths` usa `--relative`, que emite las rutas
relativas al vault y descarta los staged de fuera de él.
"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from cli.commands.validate import run
from cli.commands import stamp as stamp_cmd
from cli.config import Config

TS = "2026-01-01T00:00:00-05:00"
ROTO = "---\ntype: Plan\ntimestamp: {{roto\n---\n\nMALO.\n"


def _git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                   capture_output=True, text=True)


class TestStagedVaultEnSubdirectorio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.vault = self.repo / "mi-vault"
        (self.vault / "planes").mkdir(parents=True)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump({
            "types": {"valid": ["Plan"], "directory": {"Plan": "planes"}},
            "exclude": {"files": ["index.md"], "dirs": [".git", "Templates"]},
        }), encoding="utf-8")
        (self.vault / "planes/p.md").write_text(
            f'---\ntype: Plan\ntitle: "P"\ndescription: "D."\ntimestamp: {TS}\n'
            "---\n\nCuerpo.\n", encoding="utf-8")
        _git(self.repo, "init", "-q")
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def _staged_validate(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(SimpleNamespace(target=None, all=False), self.vault, self.config)
        return code, out.getvalue() + err.getvalue()

    def test_yaml_roto_en_subdirectorio_bloquea(self):
        (self.vault / "roto.md").write_text(ROTO, encoding="utf-8")
        _git(self.repo, "add", "-A")
        code, text = self._staged_validate()
        self.assertEqual(code, 1, text)
        self.assertIn("roto.md", text)

    def test_concepto_valido_en_subdirectorio_se_valida(self):
        _git(self.repo, "add", "-A")
        code, text = self._staged_validate()
        self.assertEqual(code, 0, text)
        self.assertIn("1 file(s) valid", text)

    def test_staged_fuera_del_vault_no_entra(self):
        (self.repo / "otro").mkdir()
        (self.repo / "otro/fuera.md").write_text(ROTO, encoding="utf-8")
        _git(self.repo, "add", "-A")
        code, text = self._staged_validate()
        self.assertEqual(code, 0, text)
        self.assertNotIn("fuera.md", text)

    def test_sin_repo_avisa_en_vez_de_callar(self):
        suelto = Path(self.tmp.name) / "suelto"   # sin .git en ningún ancestro
        suelto.mkdir()
        (suelto / ".okf.config.yaml").write_text(yaml.safe_dump({
            "types": {"valid": ["Plan"], "directory": {"Plan": "planes"}},
        }), encoding="utf-8")
        (suelto / "roto.md").write_text(ROTO, encoding="utf-8")
        config = Config(suelto)
        env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(self.tmp.name))
        with mock.patch.dict(os.environ, env, clear=True):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = run(SimpleNamespace(target=None, all=False), suelto, config)
        text = out.getvalue() + err.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("no pude listar", text)

    def test_stamp_staged_en_subdirectorio_renueva(self):
        """El mismo patrón estaba en `stamp --staged` (también corre en el hook)."""
        _git(self.vault, "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A")
        _git(self.vault, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "base")
        (self.vault / "planes/p.md").write_text(
            f'---\ntype: Plan\ntitle: "P"\ndescription: "D."\ntimestamp: {TS}\n'
            "---\n\nCuerpo cambiado.\n", encoding="utf-8")
        _git(self.vault, "add", "-A")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            stamp_cmd.run(SimpleNamespace(staged=True, targets=None),
                          self.vault, self.config)
        text = out.getvalue()
        self.assertIn("timestamp renovado", text)
        self.assertNotIn(TS, (self.vault / "planes/p.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
