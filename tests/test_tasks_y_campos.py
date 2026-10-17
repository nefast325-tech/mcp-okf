"""Fase 3: las tareas como notas y el esquema de campos por tipo.

- cli/fields.py: `types.fields` declara campos exigidos, valores cerrados y
  fechas; `validate` (13), `new` y `edit` lo aplican (aviso o error con strict).
- `okf tasks list`: tareas con responsable, vencimiento, iniciativa heredada
  de la nota a la que aplican; vencidas primero.
- `okf tasks promote`: una casilla abierta pasa a ser una nota de tarea con
  `aplica` → la nota, y la casilla queda como enlace (deja de contarse).
- `board`: las tarjetas traen responsable y vence.
"""

import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.config import Config
from cli.fields import check_fields
from cli.commands.tasks import collect_tasks, promote, run as tasks_run
from cli.commands.validate import _validate_file
from cli.commands.search import find_todos

TS = "2026-01-01T00:00:00-05:00"


def _config(strict_fields=True):
    return {
        "types": {
            "valid": ["Plan", "Task"],
            "directory": {"Plan": "plans", "Task": "tasks"},
            "exempt_from_no_backlinks": ["Plan", "Task"],
            "states": {"strict": True, "schemas": {
                "trabajo": {"types": ["Plan", "Task"], "initial": "pendiente",
                            "states": ["pendiente", "en-curso", "hecha"],
                            "transitions": {"pendiente": ["en-curso"], "en-curso": ["hecha"],
                                            "hecha": ["en-curso"]},
                            "done": ["hecha"]},
            }},
            "fields": {"strict": strict_fields, "schemas": {
                "Task": {"responsable": {"required": True,
                                         "values": ["rafael", "jaime", "claude"]},
                         "vence": {"format": "date"}},
            }},
        },
        "graph": {"strict_pairs": True, "inherit_default_pairs": False},
        "edge_types": {"aplica": {"valid_pairs": [["Task", "Plan"]]}},
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }


def _note(vault, rel, ctype, status, body="Cuerpo.", **fields):
    lines = ["---", f"type: {ctype}", f'title: "{Path(rel).stem}"', 'description: "D."',
             f"status: {status}", f"timestamp: {TS}", f"created: {TS}"]
    links = fields.pop("links", None)
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    if links:
        lines.append("links:")
        for target, etype in links:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {etype}")
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + f"\n\n{body}\n", encoding="utf-8")
    return p


class Base(unittest.TestCase):
    strict_fields = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(
            yaml.safe_dump(_config(self.strict_fields)), encoding="utf-8")
        _note(self.vault, "plans/p1.md", "Plan", "en-curso",
              body="## Pasos\n\n- [x] hecho\n- [ ] **Firmar** los estatutos\n- [ ] otra cosa",
              iniciativa="inmobiliaria")
        _note(self.vault, "tasks/t1.md", "Task", "pendiente", responsable="rafael",
              vence="2026-01-10", links=[("plans/p1.md", "aplica")])
        _note(self.vault, "tasks/t2.md", "Task", "hecha", responsable="jaime",
              vence="2026-01-05", iniciativa="sistema", links=[("plans/p1.md", "aplica")])
        _note(self.vault, "tasks/t3.md", "Task", "en-curso", responsable="claude",
              links=[("plans/p1.md", "aplica")])
        self.config = Config(self.vault)
        self.today = date(2026, 2, 1)

    def tearDown(self):
        self.tmp.cleanup()


class TestCheckFields(Base):
    def test_sin_esquema_no_valida(self):
        self.assertEqual(check_fields(self.config, "Plan", {}), [])

    def test_exigido_valores_y_fecha(self):
        self.assertEqual(len(check_fields(self.config, "Task", {})), 1)
        self.assertIn("exige el campo 'responsable'", check_fields(self.config, "Task", {})[0])
        p = check_fields(self.config, "Task", {"responsable": "pepe", "vence": "mañana"})
        self.assertEqual(len(p), 2)
        self.assertIn("no está en los valores", p[0])
        self.assertIn("no es una fecha ISO", p[1])
        self.assertEqual(check_fields(self.config, "Task",
                                      {"responsable": "rafael", "vence": date(2026, 1, 1)}), [])


class TestValidate(Base):
    def test_strict_rechaza_task_sin_responsable(self):
        p = _note(self.vault, "tasks/t4.md", "Task", "pendiente")
        ok, msg = _validate_file(p, self.vault, config=self.config,
                                 definitions=self.config.edge_type_definitions())
        self.assertFalse(ok)
        self.assertIn("exige el campo 'responsable'", msg)

    def test_task_completa_es_valida(self):
        ok, msg = _validate_file(self.vault / "tasks/t1.md", self.vault, config=self.config,
                                 definitions=self.config.edge_type_definitions())
        self.assertTrue(ok, msg)


class TestValidateAviso(Base):
    strict_fields = False

    def test_sin_strict_es_aviso(self):
        p = _note(self.vault, "tasks/t4.md", "Task", "pendiente")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, _ = _validate_file(p, self.vault, config=self.config,
                                   definitions=self.config.edge_type_definitions())
        self.assertTrue(ok)
        self.assertIn("exige el campo 'responsable'", err.getvalue())


class TestNewYEdit(Base):
    def _new(self, **kw):
        from cli.commands.new import run
        args = SimpleNamespace(concept_type="Task", title="Nueva", description="D.", tags=None,
                               status=None, resource=None, cyber=False, dry_run=False, body=None,
                               body_file=None, links=None, entity=None, filename=None,
                               omit_timestamps=False, fields=None, force=False)
        for k, v in kw.items():
            setattr(args, k, v)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return run(args, self.vault, self.config)

    def test_new_exige_el_campo(self):
        self.assertEqual(self._new(), 1)
        self.assertFalse((self.vault / "tasks/nueva.md").exists())
        self.assertEqual(self._new(fields=["responsable=rafael"]), 0)
        text = (self.vault / "tasks/nueva.md").read_text(encoding="utf-8")
        self.assertIn("responsable: rafael", text)
        self.assertIn("status: pendiente", text)
        self.assertIn("## Qué hay que hacer", text)

    def test_edit_rechaza_valor_fuera_de_lista(self):
        from cli.commands.edit import run
        args = SimpleNamespace(slug="tasks/t1", title=None, description=None, tags=None,
                               status=None, resource=None, body=None, body_file=None, links=None,
                               clear_links=False, fields=["responsable=pepe"], dry_run=False,
                               force=False)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(args, self.vault, self.config), 1)
        self.assertIn("responsable: rafael", (self.vault / "tasks/t1.md").read_text(encoding="utf-8"))


class TestTasksList(Base):
    def test_lista_ordenada_vencidas_primero(self):
        tasks = collect_tasks(self.vault, self.config, today=self.today)
        self.assertEqual([t["file"] for t in tasks], ["tasks/t1", "tasks/t2", "tasks/t3"])
        self.assertTrue(tasks[0]["vencida"])
        self.assertFalse(tasks[1]["vencida"])  # hecha: vencer ya no aplica
        self.assertEqual(tasks[0]["aplica"], "plans/p1")
        self.assertEqual(tasks[0]["aplica_title"], "p1")

    def test_iniciativa_propia_o_heredada(self):
        t = {x["file"]: x for x in collect_tasks(self.vault, self.config, today=self.today)}
        self.assertEqual(t["tasks/t1"]["iniciativa"], ["inmobiliaria"])  # heredada del plan
        self.assertEqual(t["tasks/t2"]["iniciativa"], ["sistema"])       # propia
        self.assertEqual([x["file"] for x in collect_tasks(
            self.vault, self.config, iniciativa="inmobiliaria", today=self.today)],
            ["tasks/t1", "tasks/t3"])

    def test_filtros(self):
        self.assertEqual([t["file"] for t in collect_tasks(
            self.vault, self.config, responsable="jaime", today=self.today)], ["tasks/t2"])
        self.assertEqual([t["file"] for t in collect_tasks(
            self.vault, self.config, vencidas=True, today=self.today)], ["tasks/t1"])
        self.assertEqual([t["file"] for t in collect_tasks(
            self.vault, self.config, status="en-curso", today=self.today)], ["tasks/t3"])

    def test_run_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tasks_run(SimpleNamespace(tasks_command="list", responsable=None, iniciativa=None,
                                             status=None, vencidas=False, as_json=True),
                             self.vault, self.config)
        self.assertEqual(code, 0)
        import json
        self.assertEqual(len(json.loads(out.getvalue())), 3)


class TestPromote(Base):
    def _promote(self, **kw):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return promote(self.vault, self.config, "plans/p1", **kw)

    def test_promote_crea_la_tarea_y_reemplaza_la_casilla(self):
        antes = len(find_todos(self.vault))
        self.assertEqual(self._promote(match="Firmar", responsable="rafael", vence="2026-03-01"), 0)
        task = self.vault / "tasks/firmar-los-estatutos.md"
        self.assertTrue(task.exists())
        text = task.read_text(encoding="utf-8")
        self.assertIn("type: Task", text)
        self.assertIn('title: "Firmar los estatutos"', text)
        self.assertIn("status: pendiente", text)
        self.assertIn("responsable: rafael", text)
        self.assertIn("vence: 2026-03-01", text)
        self.assertIn("  - target: plans/p1.md\n    type: aplica", text)
        self.assertIn("Sale de [[plans/p1|p1]] (línea 14).", text)
        plan = (self.vault / "plans/p1.md").read_text(encoding="utf-8")
        self.assertNotIn("- [ ] **Firmar** los estatutos", plan)
        self.assertIn("- → [[tasks/firmar-los-estatutos|**Firmar** los estatutos]]", plan)
        self.assertEqual(len(find_todos(self.vault)), antes - 1)
        ok, msg = _validate_file(task, self.vault, config=self.config,
                                 definitions=self.config.edge_type_definitions())
        self.assertTrue(ok, msg)

    def test_promote_por_linea_y_rechazos(self):
        self.assertEqual(self._promote(line=15, responsable="claude"), 0)
        self.assertTrue((self.vault / "tasks/otra-cosa.md").exists())
        # línea que no es casilla abierta
        self.assertEqual(self._promote(line=13, responsable="claude"), 1)
        # sin responsable, con esquema estricto
        self.assertEqual(self._promote(match="Firmar"), 1)
        self.assertFalse((self.vault / "tasks/firmar-los-estatutos.md").exists())
        # fecha mal escrita
        self.assertEqual(self._promote(match="Firmar", responsable="rafael", vence="01/03/2026"), 1)
        # casilla inexistente
        self.assertEqual(self._promote(match="no existe", responsable="rafael"), 1)

    def test_promote_exige_el_par_declarado(self):
        cfg = _config()
        cfg["edge_types"] = {"aplica": {"valid_pairs": [["Plan", "Plan"]]}}
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        config = Config(self.vault)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = promote(self.vault, config, "plans/p1", match="Firmar", responsable="rafael")
        self.assertEqual(code, 1)
        self.assertIn("[Task, Plan]", err.getvalue())

    def test_dry_run_no_escribe(self):
        self.assertEqual(self._promote(match="Firmar", responsable="rafael", dry_run=True), 0)
        self.assertFalse((self.vault / "tasks/firmar-los-estatutos.md").exists())
        self.assertIn("- [ ] **Firmar**", (self.vault / "plans/p1.md").read_text(encoding="utf-8"))


class TestBoardCampos(Base):
    def test_tarjeta_trae_responsable_y_vence(self):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        from cli.commands.board import build_board
        b = build_board(self.vault, self.config, with_tasks=False)
        t1 = next(c for c in b["tarjetas"] if c["file"] == "tasks/t1")
        self.assertEqual(t1["responsable"], "rafael")
        self.assertEqual(t1["vence"], "2026-01-10")
        p1 = next(c for c in b["tarjetas"] if c["file"] == "plans/p1")
        self.assertIsNone(p1["responsable"])


if __name__ == "__main__":
    unittest.main()
