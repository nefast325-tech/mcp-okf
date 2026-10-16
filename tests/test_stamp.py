"""`okf stamp`: timestamp = último cambio de CONTENIDO, decidido contra HEAD.

- Cambia el cuerpo y el timestamp quedó igual → se renueva.
- Cambia solo el status (y alguien renovó el timestamp) → se restaura el de HEAD.
- Cambia el cuerpo y el timestamp ya se renovó → no se toca.
- Archivo nuevo o sin timestamp → no se toca.
- Con --staged escribe y vuelve a stagear.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cli.commands.stamp import decide, run

HEAD_TS = "2026-01-01T00:00:00-05:00"
NEW_TS = "2026-02-02T00:00:00-05:00"


def _doc(status="vigente", ts=HEAD_TS, body="Cuerpo.", title="T"):
    return ("---\ntype: Insight\n"
            f'title: "{title}"\ndescription: "D."\nstatus: {status}\n'
            f"timestamp: {ts}\ncreated: {HEAD_TS}\n---\n\n{body}\n")


class TestDecide(unittest.TestCase):
    def test_cuerpo_cambio_timestamp_igual_se_renueva(self):
        new, reason = decide(_doc(body="Otro."), _doc(), now=NEW_TS)
        self.assertIn(f"timestamp: {NEW_TS}", new)
        self.assertIn("renovado", reason)

    def test_title_cambio_se_renueva(self):
        new, _ = decide(_doc(title="Nuevo"), _doc(), now=NEW_TS)
        self.assertIn(f"timestamp: {NEW_TS}", new)

    def test_solo_status_con_timestamp_renovado_se_restaura(self):
        new, reason = decide(_doc(status="superada", ts=NEW_TS), _doc(), now=NEW_TS)
        self.assertIn(f"timestamp: {HEAD_TS}", new)
        self.assertIn("status: superada", new)
        self.assertIn("restaurado", reason)

    def test_cuerpo_cambio_y_timestamp_ya_renovado_no_se_toca(self):
        new, _ = decide(_doc(body="Otro.", ts=NEW_TS), _doc(), now="2026-03-03T00:00:00-05:00")
        self.assertIsNone(new)

    def test_sin_cambios_no_se_toca(self):
        self.assertIsNone(decide(_doc(), _doc())[0])

    def test_archivo_nuevo_no_se_toca(self):
        self.assertIsNone(decide(_doc(body="x"), None)[0])

    def test_sin_timestamp_no_se_toca(self):
        doc = _doc().replace(f"timestamp: {HEAD_TS}\n", "")
        self.assertIsNone(decide(doc, _doc())[0])


class TestRunStaged(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "insights").mkdir()
        self.p = self.vault / "insights" / "a.md"
        self.p.write_text(_doc(), encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self.vault, check=True,
                              capture_output=True, text=True).stdout

    def _staged_content(self):
        return self._git("show", ":insights/a.md")

    def test_status_solo_restaura_y_restagea(self):
        self.p.write_text(_doc(status="superada", ts=NEW_TS), encoding="utf-8")
        self._git("add", "-A")
        code = run(SimpleNamespace(staged=True, targets=None), self.vault)
        self.assertEqual(code, 0)
        self.assertIn(f"timestamp: {HEAD_TS}", self.p.read_text(encoding="utf-8"))
        self.assertIn(f"timestamp: {HEAD_TS}", self._staged_content())

    def test_cuerpo_renueva_y_restagea(self):
        self.p.write_text(_doc(body="Otro cuerpo."), encoding="utf-8")
        self._git("add", "-A")
        run(SimpleNamespace(staged=True, targets=None), self.vault)
        text = self.p.read_text(encoding="utf-8")
        self.assertNotIn(f"timestamp: {HEAD_TS}", text)
        self.assertNotIn(f"timestamp: {HEAD_TS}", self._staged_content())
        self.assertIn(f"created: {HEAD_TS}", text)


if __name__ == "__main__":
    unittest.main()
