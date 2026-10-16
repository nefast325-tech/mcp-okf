"""Tests de _check_git_hook — el hook se busca donde git lo busca.

El check miraba siempre `<vault>/.git/hooks/pre-commit`. Con la activación que
recomienda el propio vault (`git config core.hooksPath hooks`), o dentro de un
worktree (donde `.git` es un archivo), daba «pre-commit hook does not exist»
aunque el hook existiera y corriera: health quedaba en 7/8 y, como el hook
corre `health --strict`, el commit se abortaba. Medido el 15-oct-2026 en un
clon nuevo de un vault con `core.hooksPath hooks`: 7/8 y commit rechazado.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cli.commands.health import _check_git_hook


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _hook(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(path, 0o755)


class GitHookHooksPathTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name) / "vault"
        self.vault.mkdir()
        _git(self.vault, "init", "-q")
        _git(self.vault, "config", "user.email", "t@t")
        _git(self.vault, "config", "user.name", "t")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_hook_por_defecto_sigue_funcionando(self):
        _hook(self.vault / ".git" / "hooks" / "pre-commit")
        ok, err = _check_git_hook(self.vault)
        self.assertTrue(ok, err)

    def test_sin_hook_sigue_fallando(self):
        ok, err = _check_git_hook(self.vault)
        self.assertFalse(ok)
        self.assertIn("does not exist", err)

    def test_respeta_core_hookspath(self):
        _hook(self.vault / "hooks" / "pre-commit")
        _git(self.vault, "config", "core.hooksPath", "hooks")
        ok, err = _check_git_hook(self.vault)
        self.assertTrue(ok, err)

    def test_hookspath_sin_hook_falla(self):
        # Con core.hooksPath, un hook en .git/hooks no corre: no debe contar.
        _hook(self.vault / ".git" / "hooks" / "pre-commit")
        (self.vault / "hooks").mkdir()
        _git(self.vault, "config", "core.hooksPath", "hooks")
        ok, _ = _check_git_hook(self.vault)
        self.assertFalse(ok)

    def test_funciona_dentro_de_un_worktree(self):
        _hook(self.vault / ".git" / "hooks" / "pre-commit")
        (self.vault / "a.md").write_text("x\n", encoding="utf-8")
        _git(self.vault, "add", "a.md")
        _git(self.vault, "commit", "-q", "-m", "init")
        wt = Path(self.temp_dir.name) / "wt"
        _git(self.vault, "worktree", "add", "-q", str(wt))
        self.assertTrue((wt / ".git").is_file())
        ok, err = _check_git_hook(wt)
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
