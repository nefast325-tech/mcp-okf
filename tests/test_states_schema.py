"""Máquina de estados por tipo declarada en la config (cli/states.py).

Cubre:
- Config: `types.states` se lee; sin esquema todo sigue como antes.
- check_status / check_transition / is_replaced_status / pending_states.
- validate: incumplir el esquema es aviso por defecto y error con strict;
  un tipo de registro con status es problema; `corrige` usa el estado de
  reemplazo del esquema; una nota reemplazada sin `corrige` entrante avisa.
- new: sin --status arranca en el inicial; un status fuera del esquema no crea.
- edit: rechaza transición no permitida, la acepta con --force; un cambio de
  status NO renueva timestamp, un cambio de cuerpo sí.
- stale: señal 3 lee `pending` del esquema; señal 6 cualquier tipo con estados.
"""

import contextlib
import io
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.config import Config
from cli.states import (
    check_status,
    check_transition,
    initial_status,
    is_replaced_status,
    pending_states,
    requires_status,
    schema_for,
)

SCHEMA = {
    "types": {
        "valid": ["Decision", "Insight", "Plan", "Session"],
        "directory": {"Decision": "decisions", "Insight": "insights",
                      "Plan": "plans", "Session": "sessions"},
        "states": {
            "strict": False,
            "schemas": {
                "conocimiento": {
                    "types": ["Insight"],
                    "initial": "vigente",
                    "states": ["vigente", "en-revision", "superada"],
                    "transitions": {
                        "vigente": ["en-revision", "superada"],
                        "en-revision": ["vigente", "superada"],
                        "superada": ["en-revision"],
                    },
                    "replaced": "superada",
                    "pending": ["en-revision"],
                },
                "compromiso": {
                    "types": ["Decision"],
                    "initial": "propuesta",
                    "states": ["propuesta", "aceptada", "reemplazada", "descartada"],
                    "transitions": {
                        "propuesta": ["aceptada", "descartada"],
                        "aceptada": ["reemplazada"],
                        "descartada": ["propuesta"],
                    },
                    "replaced": "reemplazada",
                    "pending": ["propuesta"],
                },
                "registro": {"types": ["Session"], "states": []},
            },
        },
    },
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}

TS = "2026-01-01T00:00:00-05:00"


def _concept(vault, rel, ctype, status=None, links=None, body="Cuerpo.", title="T"):
    lines = ["---", f"type: {ctype}", f'title: "{title}"', 'description: "Una descripción."']
    if status is not None:
        lines.append(f"status: {status}")
    lines += [f"timestamp: {TS}", f"created: {TS}"]
    if links:
        lines.append("links:")
        for target, etype in links:
            lines += [f"  - target: {target}", f"    type: {etype}"]
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + f"\n\n{body}\n", encoding="utf-8")
    return p


class VaultBase(unittest.TestCase):
    strict = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        data = yaml.safe_load(yaml.safe_dump(SCHEMA))
        data["types"]["states"]["strict"] = self.strict
        (self.vault / ".okf.config.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def _validate_all(self):
        from cli.commands.validate import run
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(SimpleNamespace(target=None, all=True), self.vault, self.config)
        return code, out.getvalue() + err.getvalue()


class TestSchemaHelpers(VaultBase):
    def test_schema_for_and_initial(self):
        self.assertEqual(schema_for(self.config, "Insight")["layer"], "conocimiento")
        self.assertIsNone(schema_for(self.config, "Plan"))
        self.assertEqual(initial_status(self.config, "Decision"), "propuesta")
        self.assertIsNone(initial_status(self.config, "Session"))
        self.assertTrue(requires_status(self.config, "Insight"))
        self.assertFalse(requires_status(self.config, "Session"))
        self.assertFalse(requires_status(self.config, "Plan"))

    def test_check_status(self):
        self.assertEqual(check_status(self.config, "Insight", "vigente"), [])
        self.assertTrue(check_status(self.config, "Insight", "activa"))
        self.assertTrue(check_status(self.config, "Insight", None))
        self.assertTrue(check_status(self.config, "Session", "aplicada"))
        self.assertEqual(check_status(self.config, "Session", None), [])
        # Sin esquema: nada que decir.
        self.assertEqual(check_status(self.config, "Plan", "lo-que-sea"), [])
        self.assertEqual(check_status(None, "Insight", "lo-que-sea"), [])

    def test_check_transition(self):
        self.assertIsNone(check_transition(self.config, "Decision", "propuesta", "aceptada"))
        self.assertIsNotNone(check_transition(self.config, "Decision", "aceptada", "propuesta"))
        self.assertIsNotNone(check_transition(self.config, "Decision", "propuesta", "inventada"))
        # Sin status previo, cualquier estado del esquema vale.
        self.assertIsNone(check_transition(self.config, "Decision", None, "aceptada"))
        # Mismo estado: no es transición.
        self.assertIsNone(check_transition(self.config, "Decision", "aceptada", "aceptada"))
        self.assertIsNone(check_transition(self.config, "Plan", "x", "y"))

    def test_is_replaced_status(self):
        self.assertTrue(is_replaced_status(self.config, "Insight", "superada"))
        self.assertFalse(is_replaced_status(self.config, "Insight", "deprecada"))
        # Sin esquema: heurística histórica por subcadena.
        self.assertTrue(is_replaced_status(self.config, "Plan", "deprecada"))
        self.assertTrue(is_replaced_status(None, "Insight", "supercedida"))
        self.assertFalse(is_replaced_status(None, "Insight", "aplicada"))

    def test_pending_states(self):
        self.assertEqual(pending_states(self.config, "Insight"), {"en-revision"})
        self.assertEqual(pending_states(self.config, "Plan"), {"propuesta"})
        self.assertEqual(pending_states(None, "Insight"), {"propuesta"})


class TestValidateAviso(VaultBase):
    def test_fuera_del_esquema_es_aviso(self):
        _concept(self.vault, "insights/a.md", "Insight", status="activa")
        code, out = self._validate_all()
        self.assertEqual(code, 0)
        self.assertIn("status 'activa' no está en el esquema", out)

    def test_registro_con_status_es_aviso(self):
        _concept(self.vault, "sessions/s.md", "Session", status="aplicada")
        code, out = self._validate_all()
        self.assertEqual(code, 0)
        self.assertIn("no lleva status", out)

    def test_reemplazada_sin_corrige_es_aviso(self):
        _concept(self.vault, "insights/vieja.md", "Insight", status="superada")
        code, out = self._validate_all()
        self.assertEqual(code, 0)
        self.assertIn("sin arista 'corrige' entrante", out)

    def test_corrige_hacia_estado_de_reemplazo_del_esquema_pasa(self):
        _concept(self.vault, "insights/vieja.md", "Insight", status="superada")
        _concept(self.vault, "insights/nueva.md", "Insight", status="vigente",
                 links=[("insights/vieja.md", "corrige")])
        code, out = self._validate_all()
        self.assertEqual(code, 0)
        self.assertNotIn("not in its replaced state", out)

    def test_corrige_hacia_nota_vigente_es_error(self):
        _concept(self.vault, "insights/vieja.md", "Insight", status="vigente")
        _concept(self.vault, "insights/nueva.md", "Insight", status="vigente",
                 links=[("insights/vieja.md", "corrige")])
        code, out = self._validate_all()
        self.assertEqual(code, 1)
        self.assertIn("not in its replaced state", out)


class TestValidateStrict(VaultBase):
    strict = True

    def test_fuera_del_esquema_es_error(self):
        _concept(self.vault, "insights/a.md", "Insight", status="activa")
        code, out = self._validate_all()
        self.assertEqual(code, 1)
        self.assertIn("status 'activa' no está en el esquema", out)

    def test_sin_status_es_error(self):
        _concept(self.vault, "decisions/d.md", "Decision")
        code, out = self._validate_all()
        self.assertEqual(code, 1)
        self.assertIn("exige status", out)

    def test_reemplazada_sin_corrige_es_error(self):
        _concept(self.vault, "insights/vieja.md", "Insight", status="superada")
        code, out = self._validate_all()
        self.assertEqual(code, 1)
        self.assertIn("sin arista 'corrige' entrante", out)

    def test_conforme_pasa(self):
        _concept(self.vault, "insights/a.md", "Insight", status="vigente")
        _concept(self.vault, "decisions/d.md", "Decision", status="aceptada")
        _concept(self.vault, "sessions/s.md", "Session")
        _concept(self.vault, "plans/p.md", "Plan", status="cualquiera")
        code, out = self._validate_all()
        self.assertEqual(code, 0, out)


class TestNew(VaultBase):
    def _new(self, **kw):
        from cli.commands.new import run
        defaults = dict(concept_type="Decision", title="Nueva", description="Desc",
                        tags=None, status=None, resource=None, cyber=False,
                        dry_run=False, body=None, body_file=None, links=None,
                        entity=None, filename=None, omit_timestamps=False,
                        fields=None, force=False)
        defaults.update(kw)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run(SimpleNamespace(**defaults), self.vault, self.config)
        return code, err.getvalue()

    def test_sin_status_arranca_en_inicial(self):
        code, _ = self._new()
        self.assertEqual(code, 0)
        text = (self.vault / "decisions" / "nueva.md").read_text(encoding="utf-8")
        self.assertIn("status: propuesta", text)

    def test_status_fuera_del_esquema_no_crea(self):
        code, err = self._new(status="aplicada")
        self.assertEqual(code, 1)
        self.assertIn("no está en el esquema", err)
        self.assertFalse((self.vault / "decisions" / "nueva.md").exists())

    def test_registro_con_status_no_crea(self):
        code, err = self._new(concept_type="Session", status="aplicada")
        self.assertEqual(code, 1)
        self.assertIn("no lleva status", err)

    def test_registro_sin_status_crea_sin_status(self):
        code, _ = self._new(concept_type="Session")
        self.assertEqual(code, 0)
        text = (self.vault / "sessions" / "nueva.md").read_text(encoding="utf-8")
        self.assertNotIn("status:", text)


class TestEdit(VaultBase):
    def setUp(self):
        super().setUp()
        self.path = _concept(self.vault, "decisions/d.md", "Decision", status="aceptada")

    def _edit(self, **kw):
        from cli.commands.edit import run
        defaults = dict(slug="decisions/d.md", title=None, description=None,
                        tags=None, status=None, resource=None, body=None,
                        body_file=None, links=None, clear_links=False,
                        fields=None, dry_run=False, force=False)
        defaults.update(kw)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run(SimpleNamespace(**defaults), self.vault, self.config)
        return code, err.getvalue(), self.path.read_text(encoding="utf-8")

    def test_transicion_permitida(self):
        code, _, text = self._edit(status="reemplazada")
        self.assertEqual(code, 0)
        self.assertIn("status: reemplazada", text)

    def test_transicion_no_permitida_se_rechaza(self):
        code, err, text = self._edit(status="propuesta")
        self.assertEqual(code, 1)
        self.assertIn("transición no permitida", err)
        self.assertIn("status: aceptada", text)

    def test_force_permite_la_migracion(self):
        code, _, text = self._edit(status="propuesta", force=True)
        self.assertEqual(code, 0)
        self.assertIn("status: propuesta", text)

    def test_estado_inexistente_se_rechaza(self):
        code, err, _ = self._edit(status="aplicada")
        self.assertEqual(code, 1)
        self.assertIn("no está en el esquema", err)

    def test_cambio_de_status_no_renueva_timestamp(self):
        code, _, text = self._edit(status="reemplazada")
        self.assertEqual(code, 0)
        self.assertIn(f"timestamp: {TS}", text)

    def test_cambio_de_cuerpo_renueva_timestamp(self):
        code, _, text = self._edit(body="Cuerpo nuevo.")
        self.assertEqual(code, 0)
        m = re.search(r"^timestamp: (.*)$", text, re.M)
        self.assertNotEqual(m.group(1), TS)


class TestStale(VaultBase):
    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        old = "2025-01-01T00:00:00-05:00"
        for rel, ctype, status in (("insights/pend.md", "Insight", "en-revision"),
                                   ("insights/prop.md", "Insight", "propuesta"),
                                   ("insights/sin.md", "Insight", None),
                                   ("sessions/s.md", "Session", None)):
            p = _concept(self.vault, rel, ctype, status=status)
            p.write_text(p.read_text(encoding="utf-8").replace(TS, old), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.vault, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                        "-q", "-m", "init"], cwd=self.vault, check=True)

    def _signals(self):
        from cli.commands.stale import collect_stale
        rows = collect_stale(self.vault, exempt_no_backlinks=set(), config=self.config)
        return {r["file"]: r["signals"] for r in rows}

    def test_senal_3_lee_pending_del_esquema(self):
        sig = self._signals()
        self.assertTrue(any("unresolved" in s for s in sig["insights/pend.md"]))
        # «propuesta» ya no es pendiente para un Insight con esquema.
        self.assertFalse(any("unresolved" in s for s in sig["insights/prop.md"]))

    def test_senal_6_cualquier_tipo_con_estados(self):
        sig = self._signals()
        self.assertIn("insight without status", sig["insights/sin.md"])
        self.assertFalse(any("without status" in s for s in sig["sessions/s.md"]))


if __name__ == "__main__":
    unittest.main()
