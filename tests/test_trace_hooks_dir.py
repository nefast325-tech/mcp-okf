"""El layer `hooks` de trace busca donde git busca, no en `.git/hooks` a secas.

Misma clase que el fix #30 (health): con `core.hooksPath` —la activación que
recomienda el CLAUDE.md de skynet-vault— el hook vive en `<vault>/hooks/`, y en
un worktree `.git` es un archivo. `trace --layers hooks` leía
`vault/.git/hooks` a mano y respondía «(no results)» en silencio aunque el
hook existiera.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.commands import trace

MARCA = "marca-unica-del-hook"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _hook(path, text=f"#!/bin/sh\n# {MARCA}\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o755)


class TraceHooksLayerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name) / "vault"
        self.vault.mkdir()
        _git(self.vault, "init", "-q")
        _git(self.vault, "config", "user.email", "t@t")
        _git(self.vault, "config", "user.name", "t")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, vault):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trace.run(Namespace(query=MARCA, layers="hooks"), vault)
        return buf.getvalue()

    def test_encuentra_el_hook_por_defecto(self):
        _hook(self.vault / ".git" / "hooks" / "pre-commit")
        self.assertIn(MARCA, self._run(self.vault))

    def test_encuentra_el_hook_con_core_hookspath(self):
        _hook(self.vault / "hooks" / "pre-commit")
        _git(self.vault, "config", "core.hooksPath", "hooks")
        self.assertIn(MARCA, self._run(self.vault))

    def test_encuentra_el_hook_dentro_de_un_worktree(self):
        _hook(self.vault / ".git" / "hooks" / "pre-commit")
        (self.vault / "a.md").write_text("x\n", encoding="utf-8")
        _git(self.vault, "add", "a.md")
        _git(self.vault, "commit", "-q", "-m", "init")
        wt = Path(self.temp_dir.name) / "wt"
        _git(self.vault, "worktree", "add", "-q", str(wt))
        self.assertTrue((wt / ".git").is_file())
        self.assertIn(MARCA, self._run(wt))

    def test_con_hookspath_no_reporta_el_hook_de_git_hooks(self):
        # Con core.hooksPath, un hook en .git/hooks no corre: trace no debe leerlo.
        _hook(self.vault / ".git" / "hooks" / "pre-commit")
        (self.vault / "hooks").mkdir()
        _git(self.vault, "config", "core.hooksPath", "hooks")
        self.assertNotIn(MARCA, self._run(self.vault))


if __name__ == "__main__":
    unittest.main()
