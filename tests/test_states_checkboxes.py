"""Las casillas del cuerpo acotan el estado (esquema `done`, cli/states.py).

- Una nota en un estado `done` con casillas abiertas: aviso por defecto,
  error con strict; `edit --status` la rechaza (salvo --force).
- Una nota en el estado inicial con casillas marcadas: aviso siempre.
- `cancelada` no está en `done`: puede tener casillas abiertas.
- Sin `done` en el esquema, nada cambia.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.config import Config
from cli.states import check_checkboxes

TS = "2026-01-01T00:00:00-05:00"


def _config(vault, strict=False, done=("hecha",)):
    data = {
        "types": {"valid": ["Plan"], "directory": {"Plan": "plans"},
                  "states": {"strict": strict, "schemas": {"trabajo": {
                      "types": ["Plan"], "initial": "pendiente",
                      "states": ["pendiente", "en-curso", "hecha", "cancelada"],
                      "transitions": {"pendiente": ["en-curso"], "en-curso": ["hecha", "cancelada"]},
                      "done": list(done)}}}},
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }
    (vault / ".okf.config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return Config(vault)


def _plan(vault, rel, status, body):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'---\ntype: Plan\ntitle: "T"\ndescription: "D."\nstatus: {status}\n'
                 f"timestamp: {TS}\ncreated: {TS}\n---\n\n{body}\n", encoding="utf-8")
    return p


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hecha_con_abiertas_es_problema(self):
        c = _config(self.vault)
        p, w = check_checkboxes(c, "Plan", "hecha", 2, 1)
        self.assertEqual(len(p), 1); self.assertEqual(w, [])

    def test_hecha_sin_abiertas_y_cancelada_pasan(self):
        c = _config(self.vault)
        self.assertEqual(check_checkboxes(c, "Plan", "hecha", 3, 0), ([], []))
        self.assertEqual(check_checkboxes(c, "Plan", "cancelada", 0, 4), ([], []))

    def test_inicial_con_marcadas_es_aviso(self):
        c = _config(self.vault)
        p, w = check_checkboxes(c, "Plan", "pendiente", 1, 2)
        self.assertEqual(p, []); self.assertEqual(len(w), 1)

    def test_sin_done_no_hay_regla(self):
        c = _config(self.vault, done=())
        self.assertEqual(check_checkboxes(c, "Plan", "hecha", 0, 5), ([], []))
        self.assertEqual(check_checkboxes(None, "Plan", "hecha", 0, 5), ([], []))


class TestValidateYEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.vault = Path(self.tmp.name)
        self.p = _plan(self.vault, "plans/p.md", "hecha", "- [x] una\n- [ ] dos")
        _plan(self.vault, "plans/q.md", "pendiente", "- [x] ya\n- [ ] no")

    def tearDown(self):
        self.tmp.cleanup()

    def _validate(self, config):
        from cli.commands.validate import run
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(SimpleNamespace(target=None, all=True), self.vault, config)
        return code, out.getvalue() + err.getvalue()

    def test_aviso_por_defecto(self):
        code, out = self._validate(_config(self.vault))
        self.assertEqual(code, 0)
        self.assertIn("casilla(s) abierta(s)", out)
        self.assertIn("ya marcada(s)", out)

    def test_error_con_strict_solo_para_hecha(self):
        code, out = self._validate(_config(self.vault, strict=True))
        self.assertEqual(code, 1)
        self.assertIn("plans/p.md", out)
        self.assertIn("casilla(s) abierta(s)", out)
        # La inicial con marcadas sigue siendo aviso, no error.
        self.assertNotIn("plans/q.md: ", out.split("file(s) with errors")[-1].split("\n")[1] if "file(s) with errors" in out else "")

    def test_edit_rechaza_terminar_con_abiertas(self):
        from cli.commands.edit import run
        config = _config(self.vault)
        r = _plan(self.vault, "plans/r.md", "en-curso", "- [ ] falta")
        args = lambda **kw: SimpleNamespace(**{**dict(slug="plans/r.md", title=None, description=None, tags=None,
                                                      status=None, resource=None, body=None, body_file=None, links=None,
                                                      clear_links=False, fields=None, dry_run=False, force=False), **kw})
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(run(args(status="hecha"), self.vault, config), 1)
        self.assertIn("casilla(s) abierta(s)", err.getvalue())
        self.assertIn("status: en-curso", r.read_text(encoding="utf-8"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(args(status="cancelada"), self.vault, config), 0)
            self.assertEqual(run(args(status="hecha", force=True), self.vault, config), 0)
        self.assertIn("status: hecha", r.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
