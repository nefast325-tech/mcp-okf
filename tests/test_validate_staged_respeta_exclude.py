"""`validate` (modo staged) salta los .md de los directorios excluidos, igual
que `--all`: una plantilla de Obsidian en Templates/ con `{{date}}` en el
frontmatter no es un concepto y no puede bloquear el commit."""

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.commands.validate import run
from cli.config import Config

TS = "2026-01-01T00:00:00-05:00"


class TestValidateStagedExclude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump({
            "types": {"valid": ["Plan"], "directory": {"Plan": "plans"}},
            "exclude": {"files": ["index.md"], "dirs": [".git", "Templates"]},
        }), encoding="utf-8")
        (self.vault / "plans").mkdir()
        (self.vault / "plans/p.md").write_text(
            f'---\ntype: Plan\ntitle: "P"\ndescription: "D."\ntimestamp: {TS}\n---\n\nCuerpo.\n',
            encoding="utf-8")
        (self.vault / "Templates").mkdir()
        (self.vault / "Templates/Task.md").write_text(
            "---\ntype: Task\ntitle: \"{{title}}\"\ndescription: \"\"\n"
            "timestamp: {{date:YYYY-MM-DD}}T{{time:HH:mm:ss}}-05:00\n---\n\nPlantilla.\n",
            encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.vault, check=True)
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(SimpleNamespace(target=None, all=False), self.vault, self.config)
        return code, out.getvalue() + err.getvalue()

    def test_staged_salta_el_directorio_excluido(self):
        code, text = self._run()
        self.assertEqual(code, 0, text)
        self.assertIn("1 file(s) valid", text)
        self.assertNotIn("Templates/Task.md", text)

    def test_sin_exclusion_si_la_valida(self):
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump({
            "types": {"valid": ["Plan"], "directory": {"Plan": "plans"}},
            "exclude": {"files": ["index.md"], "dirs": [".git"]},
        }), encoding="utf-8")
        self.config = Config(self.vault)
        code, text = self._run()
        self.assertEqual(code, 1)
        self.assertIn("Templates/Task.md", text)


if __name__ == "__main__":
    unittest.main()
