"""`search --edge/--linked*`: un salto tipado exacto, con el estado del otro extremo.

Contrato: `--edge T` deja las notas que DECLARAN una arista de tipo T en `links:`;
`--linked`, `--linked-type` y `--linked-status` acotan la nota del otro extremo;
`--edge-dir in` lo invierte (la nota del resultado RECIBE la arista). Es exacto:
una arista hacia una nota reemplazada cuenta hacia esa nota, no hacia su vigente.
Cada resultado trae en JSON `edges` (las aristas que cumplieron) y la tabla las
lista al final. `--type`, `--status` y los filtros de arista aceptan varios
valores con coma. Sin estos flags, `search` no cambia.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cli.config import Config
from cli.vault import apply_config
from cli.commands.search import run as search_run

CONFIG = """\
types:
  valid: [Decision, Plan, Task]
  states:
    strict: true
    schemas:
      compromiso:
        types: [Decision]
        initial: propuesta
        states: [propuesta, aceptada, reemplazada]
        transitions: {propuesta: [aceptada], aceptada: [reemplazada]}
        replaced: reemplazada
      trabajo:
        types: [Plan, Task]
        initial: pendiente
        states: [pendiente, en-curso, hecha, cancelada]
        transitions: {pendiente: [en-curso, cancelada], en-curso: [hecha, cancelada]}
        dead: [cancelada]
features:
  cognitive_trace: false
"""


def _nota(tipo, titulo, estado, links=()):
    cab = f'---\ntype: {tipo}\ntitle: "{titulo}"\ndescription: "{titulo}"\nstatus: {estado}\n'
    if links:
        cab += "links:\n" + "".join(f"  - target: {t}\n    type: {e}\n" for t, e in links)
    return cab + "---\n\nCuerpo.\n"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SearchAristaEstadoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name).resolve() / "vault"
        _write(self.vault / ".okf.config.yaml", CONFIG)
        v = self.vault
        _write(v / "decisions/vieja.md", _nota("Decision", "Vieja", "reemplazada"))
        _write(v / "decisions/nueva.md", _nota("Decision", "Nueva", "aceptada",
                                               [("decisions/vieja.md", "corrige")]))
        _write(v / "decisions/hija.md", _nota("Decision", "Hija", "reemplazada",
                                              [("decisions/vieja.md", "extiende")]))
        _write(v / "plans/atrasado.md", _nota("Plan", "Atrasado", "pendiente",
                                              [("decisions/vieja.md", "aplica")]))
        _write(v / "plans/muerto.md", _nota("Plan", "Muerto", "cancelada",
                                            [("decisions/vieja.md", "aplica")]))
        _write(v / "plans/al-dia.md", _nota("Plan", "Al día", "pendiente",
                                            [("decisions/nueva.md", "aplica"),
                                             ("plans/atrasado.md", "extiende")]))
        _write(v / "tasks/abierta.md", _nota("Task", "Abierta", "en-curso",
                                             [("plans/atrasado.md", "aplica")]))
        _write(v / "tasks/cerrada.md", _nota("Task", "Cerrada", "cancelada",
                                             [("plans/atrasado.md", "aplica")]))
        _write(v / "tasks/otra.md", _nota("Task", "Otra", "pendiente",
                                          [("plans/al-dia.md", "aplica")]))
        self.config = Config(self.vault)
        apply_config(self.config)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, **kw):
        base = dict(json=True, todos=False, all=False, aging=False, query=None, filter_type=None,
                    filter_status=None, cyber_field=None, cyber_value=None, review_due=False,
                    since=None, until=None, with_graph=False, all_versions=False, body=False,
                    limit=None, edge=None, edge_dir="out", linked=None, linked_type=None,
                    linked_status=None)
        base.update(kw)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = search_run(SimpleNamespace(**base), self.vault, self.config)
        return rc, out.getvalue(), err.getvalue()

    def _archivos(self, **kw):
        rc, out, err = self._run(**kw)
        self.assertEqual(rc, 0, err)
        return sorted(c["file"] for c in json.loads(out))

    def test_planes_que_aplican_decisiones_reemplazadas_incluye_el_cancelado(self):
        self.assertEqual(
            self._archivos(filter_type="Plan", edge="aplica", linked_type="Decision",
                           linked_status="reemplazada"),
            ["plans/atrasado.md", "plans/muerto.md"])

    def test_es_exacto_no_sustituye_la_reemplazada_por_su_vigente(self):
        # al-dia aplica la NUEVA: no debe salir al pedir las que aplican la vieja.
        self.assertEqual(self._archivos(edge="aplica", linked="decisions/vieja"),
                         ["plans/atrasado.md", "plans/muerto.md"])

    def test_linked_acepta_md_nombre_suelto_y_varios_con_coma(self):
        self.assertEqual(self._archivos(filter_type="Task", edge="aplica", linked="plans/atrasado.md"),
                         ["tasks/abierta.md", "tasks/cerrada.md"])
        self.assertEqual(self._archivos(filter_type="Task", edge="aplica", linked="atrasado, plans/al-dia"),
                         ["tasks/abierta.md", "tasks/cerrada.md", "tasks/otra.md"])

    def test_estado_propio_y_estado_del_otro_extremo_se_combinan(self):
        self.assertEqual(self._archivos(filter_type="Task", filter_status="cancelada", edge="aplica",
                                        linked="plans/atrasado"),
                         ["tasks/cerrada.md"])

    def test_tipo_y_estado_aceptan_varios_valores(self):
        self.assertEqual(self._archivos(filter_type="Task", filter_status="pendiente, en-curso"),
                         ["tasks/abierta.md", "tasks/otra.md"])
        self.assertEqual(self._archivos(filter_type="plan,task", filter_status="cancelada"),
                         ["plans/muerto.md", "tasks/cerrada.md"])

    def test_edge_dir_in_devuelve_la_nota_que_recibe_la_arista(self):
        rc, out, err = self._run(filter_type="Plan", edge="aplica", edge_dir="in", linked_type="Task",
                                 linked_status="pendiente,en-curso")
        self.assertEqual(rc, 0, err)
        datos = {c["file"]: c for c in json.loads(out)}
        self.assertEqual(sorted(datos), ["plans/al-dia.md", "plans/atrasado.md"])
        self.assertEqual(datos["plans/atrasado.md"]["edges"],
                         [{"type": "aplica", "dir": "in", "linked": "tasks/abierta",
                           "linked_type": "Task", "linked_status": "en-curso"}])

    def test_sin_edge_cualquier_arista_tipada_cuenta(self):
        self.assertEqual(self._archivos(linked="plans/atrasado"),
                         ["plans/al-dia.md", "tasks/abierta.md", "tasks/cerrada.md"])

    def test_la_reemplazada_que_cumple_se_anuncia_al_pie_y_sale_con_all_versions(self):
        rc, out, err = self._run(json=False, filter_type="Decision", edge="extiende", linked="decisions/vieja")
        self.assertEqual(rc, 0, err)
        self.assertIn("(no results)", out)
        self.assertIn("1 nota(s) reemplazada(s) coinciden y se ocultan: decisions/hija", out)
        self.assertEqual(self._archivos(filter_type="Decision", edge="extiende", linked="decisions/vieja",
                                        all_versions=True),
                         ["decisions/hija.md"])

    def test_la_tabla_lista_las_aristas_sin_md(self):
        rc, out, err = self._run(json=False, filter_type="Plan", edge="aplica", linked_status="reemplazada")
        self.assertEqual(rc, 0, err)
        self.assertIn("## Aristas que cumplen el filtro", out)
        self.assertIn("└─ aplica → decisions/vieja  (Decision · reemplazada)", out)
        seccion = out.split("## Aristas", 1)[1]
        self.assertNotIn(".md", seccion)  # el trazador toma un token .md como nodo resultado

    def test_arista_desconocida_y_nota_inexistente_son_errores_que_se_dicen(self):
        rc, out, err = self._run(edge="aplcia")
        self.assertEqual(rc, 1)
        self.assertIn("tipo de arista desconocido: aplcia", err)
        self.assertIn("aplica", err)
        rc, out, err = self._run(edge="aplica", linked="plans/no-existe")
        self.assertEqual(rc, 1)
        self.assertIn("no hay ninguna nota «plans/no-existe»", err)

    def test_con_texto_no_se_rellena_con_parciales(self):
        rc, out, err = self._run(query="atrasado inexistente", edge="aplica", linked="decisions/vieja")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), [])

    def test_sin_filtros_de_arista_la_salida_no_cambia(self):
        rc, out, err = self._run(filter_type="Plan")
        self.assertEqual(rc, 0, err)
        for c in json.loads(out):
            self.assertNotIn("edges", c)


if __name__ == "__main__":
    unittest.main()
