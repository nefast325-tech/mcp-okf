"""Fase 4 del rediseño: fechas de verificación, campos que resuelven a una
nota (Persona) y la versión vigente de una nota reemplazada.

- cli/fields.py: `resolves: Persona` en el esquema de campos — el valor tiene
  que nombrar una nota de ese tipo (archivo o carpeta de entidad) o estar en
  `values`.
- cli/verificacion.py: `verificado_el`/`revisar_el`, plazo por tipo o por
  prefijo de ruta (`types.review`), validación 14, `new` sella la fecha,
  `stale` señal 8 y `review` lista lo vencido.
- cli/vigente.py: `traverse` y `search` saltan a la versión vigente por
  defecto; `--all-versions` recorre la cadena. `validate` avisa cuando una
  nota vigente se apoya en una reemplazada.
"""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.config import Config
from cli.fields import check_fields, reset_resolve_cache
from cli.verificacion import check_review_dates, due_date, overdue_days, review_days_for
from cli.vigente import build_superseded_map
from cli.commands.validate import _validate_file
from cli.commands.graph import build_graph

TS = "2026-01-01T00:00:00-05:00"


def _config(review_strict=False, fields_strict=True):
    return {
        "types": {
            "valid": ["Decision", "Insight", "Project", "Persona", "Task", "Plan"],
            "by_entity": ["Persona", "Project"],
            "directory": {"Decision": "decisions", "Insight": "insights",
                          "Project": "projects", "Persona": "personas",
                          "Task": "tasks", "Plan": "plans"},
            "exempt_from_no_backlinks": ["Decision", "Insight", "Project", "Persona",
                                         "Task", "Plan"],
            "states": {"strict": True, "schemas": {
                "conocimiento": {"types": ["Insight"], "initial": "vigente",
                                 "states": ["vigente", "en-revision", "superada"],
                                 "replaced": "superada", "pending": ["en-revision"]},
                "compromiso": {"types": ["Decision"], "initial": "propuesta",
                               "states": ["propuesta", "aceptada", "reemplazada"],
                               "replaced": "reemplazada", "pending": ["propuesta"]},
                "trabajo": {"types": ["Project", "Task", "Plan"], "initial": "pendiente",
                            "states": ["pendiente", "en-curso", "hecha"], "done": ["hecha"]},
                "registro": {"types": ["Persona"], "states": []},
            }},
            "fields": {"strict": fields_strict, "schemas": {
                "Decision": {"decidida_por": {"required": True, "resolves": "Persona",
                                              "values": ["skynet"]},
                             "decidida_el": {"required": True, "format": "date"}},
                "Task": {"responsable": {"required": True, "resolves": "Persona"}},
            }},
            "review": {"strict": review_strict,
                       "days": {"Insight": 90},
                       "paths": {"publico/": 30}},
        },
        "graph": {"strict_pairs": True, "inherit_default_pairs": False},
        "edge_types": {
            "corrige": {},
            "fundamenta": {"valid_pairs": [["Decision", "Insight"]]},
            "extiende": {"valid_pairs": [["Insight", "Insight"], ["Decision", "Decision"]]},
        },
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }


def _note(vault, rel, ctype, status=None, body="Cuerpo.", links=None, **fields):
    lines = ["---", f"type: {ctype}", f'title: "{Path(rel).stem}"', 'description: "D."',
             f"timestamp: {TS}", f"created: {TS}"]
    if status:
        lines.append(f"status: {status}")
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
    review_strict = False
    fields_strict = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(
            yaml.safe_dump(_config(self.review_strict, self.fields_strict)), encoding="utf-8")
        self.config = Config(self.vault)
        reset_resolve_cache()
        _note(self.vault, "personas/rafael/rafael-hernandez.md", "Persona")
        _note(self.vault, "personas/jaime/jaime.md", "Persona")

    def tearDown(self):
        self.tmp.cleanup()
        reset_resolve_cache()

    def validate(self, rel):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, msg = _validate_file(self.vault / rel, self.vault,
                                     definitions=self.config.edge_type_definitions(),
                                     config=self.config)
        return ok, msg, err.getvalue()


# ── resolves: Persona ────────────────────────────────────────────────────────

class TestResolves(Base):
    def test_resuelve_por_carpeta_de_entidad_y_por_archivo(self):
        for value in ("rafael", "rafael-hernandez", "Jaime"):
            problems = check_fields(self.config, "Task", {"responsable": value}, vault=self.vault)
            self.assertEqual(problems, [], value)

    def test_values_sigue_valiendo_junto_a_resolves(self):
        problems = check_fields(self.config, "Decision",
                                {"decidida_por": "skynet", "decidida_el": "2026-10-19"},
                                vault=self.vault)
        self.assertEqual(problems, [])

    def test_texto_libre_no_resuelve(self):
        problems = check_fields(self.config, "Decision",
                                {"decidida_por": "pepito", "decidida_el": "2026-10-19"},
                                vault=self.vault)
        self.assertEqual(len(problems), 1)
        self.assertIn("no resuelve a una Persona", problems[0])

    def test_campo_exigido_dice_que_resuelve(self):
        problems = check_fields(self.config, "Decision", {"decidida_el": "2026-10-19"},
                                vault=self.vault)
        self.assertEqual(len(problems), 1)
        self.assertIn("una nota Persona", problems[0])
        self.assertIn("skynet", problems[0])

    def test_sin_vault_solo_vale_values(self):
        self.assertEqual(check_fields(self.config, "Decision",
                                      {"decidida_por": "skynet", "decidida_el": "2026-10-19"}), [])
        self.assertEqual(len(check_fields(self.config, "Decision",
                                          {"decidida_por": "rafael", "decidida_el": "2026-10-19"})), 1)

    def test_validate_rechaza_decision_sin_quien_decidio(self):
        _note(self.vault, "decisions/d.md", "Decision", status="aceptada")
        ok, msg, _ = self.validate("decisions/d.md")
        self.assertFalse(ok)
        self.assertIn("decidida_por", msg)
        self.assertIn("decidida_el", msg)

    def test_validate_acepta_decision_con_persona(self):
        _note(self.vault, "decisions/d.md", "Decision", status="aceptada",
              decidida_por="rafael", decidida_el="2026-10-16")
        ok, msg, _ = self.validate("decisions/d.md")
        self.assertTrue(ok, msg)

    def test_new_rechaza_responsable_que_no_es_persona_y_acepta_uno_que_si(self):
        from cli.commands.new import run as new_run
        def run(responsable):
            args = SimpleNamespace(concept_type="Task", title=f"T {responsable}", description="D.",
                                   tags=None, status=None, resource=None, cyber=False, dry_run=False,
                                   body=None, body_file=None, links=None, entity=None, filename=None,
                                   omit_timestamps=False, fields=[f"responsable={responsable}"])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = new_run(args, self.vault, self.config)
            return rc, err.getvalue()
        rc, err = run("pepito")
        self.assertEqual(rc, 1)
        self.assertIn("no resuelve a una Persona", err)
        rc, err = run("rafael")
        self.assertEqual(rc, 0, err)


# ── verificado_el / revisar_el ───────────────────────────────────────────────

class TestVerificacion(Base):
    def test_plazo_por_tipo_y_por_ruta(self):
        self.assertEqual(review_days_for(self.config, "Insight"), 90)
        self.assertIsNone(review_days_for(self.config, "Decision"))
        # El prefijo manda sobre el tipo: una ficha pública caduca a los 30
        self.assertEqual(review_days_for(self.config, "Project", "publico/x/ficha.md"), 30)
        self.assertEqual(review_days_for(self.config, "Insight", "publico/x/ficha.md"), 30)
        self.assertIsNone(review_days_for(self.config, "Project", "projects/x/ficha.md"))

    def test_fecha_de_revision_derivada_o_explicita(self):
        fm = {"verificado_el": "2026-01-01"}
        self.assertEqual(due_date(self.config, "Insight", fm), date(2026, 3, 31) + timedelta(days=1))
        fm = {"verificado_el": "2026-01-01", "revisar_el": "2026-02-01"}
        self.assertEqual(due_date(self.config, "Insight", fm), date(2026, 2, 1))
        # Sin plazo ni revisar_el no hay fecha; sin verificado_el tampoco
        self.assertIsNone(due_date(self.config, "Decision", {"verificado_el": "2026-01-01"}))
        self.assertIsNone(due_date(self.config, "Insight", {}))

    def test_vencimiento_en_dias(self):
        today = date(2026, 10, 19)
        fm = {"verificado_el": "2026-01-01"}
        self.assertEqual(overdue_days(self.config, "Insight", fm, today=today),
                         (today - (date(2026, 1, 1) + timedelta(days=90))).days)
        self.assertIsNone(overdue_days(self.config, "Insight",
                                       {"verificado_el": today.isoformat()}, today=today))

    def test_formato_y_orden(self):
        self.assertEqual(check_review_dates(self.config, "Insight",
                                            {"verificado_el": "2026-01-01", "revisar_el": "2026-04-01"}), [])
        bad = check_review_dates(self.config, "Insight", {"verificado_el": "ayer"})
        self.assertEqual(len(bad), 1)
        self.assertIn("fecha ISO", bad[0])
        order = check_review_dates(self.config, "Insight",
                                   {"verificado_el": "2026-05-01", "revisar_el": "2026-04-01"})
        self.assertEqual(len(order), 1)
        self.assertIn("anterior", order[0])

    def test_validate_avisa_sin_strict(self):
        _note(self.vault, "insights/i.md", "Insight", status="vigente", verificado_el="ayer")
        ok, msg, err = self.validate("insights/i.md")
        self.assertTrue(ok, msg)
        self.assertIn("verificado_el", err)

    def test_new_sella_verificado_el_en_tipos_con_plazo(self):
        from cli.commands.new import run as new_run
        def run(ctype, fields=None, title=None):
            args = SimpleNamespace(concept_type=ctype, title=title or f"N {ctype}", description="D.",
                                   tags=None, status=None, resource=None, cyber=False, dry_run=False,
                                   body=None, body_file=None, links=None, entity=None, filename=None,
                                   omit_timestamps=False, fields=fields)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = new_run(args, self.vault, self.config)
            self.assertEqual(rc, 0, err.getvalue())
        run("Insight")
        text = (self.vault / "insights" / "n-insight.md").read_text(encoding="utf-8")
        self.assertIn(f"verificado_el: {date.today().isoformat()}", text)
        self.assertNotIn("revisar_el", text)
        run("Plan")
        text = (self.vault / "plans" / "n-plan.md").read_text(encoding="utf-8")
        self.assertNotIn("verificado_el", text)
        run("Insight", fields=["verificado_el=2026-01-15"], title="Con fecha propia")
        text = (self.vault / "insights" / "con-fecha-propia.md").read_text(encoding="utf-8")
        self.assertIn("verificado_el: 2026-01-15", text)
        self.assertNotIn(date.today().isoformat(), text.split("verificado_el")[1].splitlines()[0])

    def test_stale_senal_8_y_review_lista_lo_vencido(self):
        from cli.commands.stale import collect_stale
        from cli.commands.review import collect_verification_due, run as review_run
        old = (date.today() - timedelta(days=200)).isoformat()
        _note(self.vault, "insights/vieja.md", "Insight", status="vigente", verificado_el=old)
        _note(self.vault, "insights/fresca.md", "Insight", status="vigente",
              verificado_el=date.today().isoformat())
        _note(self.vault, "publico/x/ficha.md", "Project", status="en-curso",
              verificado_el=(date.today() - timedelta(days=45)).isoformat())
        _note(self.vault, "projects/x/interna.md", "Project", status="en-curso",
              verificado_el=(date.today() - timedelta(days=400)).isoformat())
        results = {r["file"]: r for r in collect_stale(self.vault, config=self.config)}
        self.assertTrue(any(s.startswith("verification overdue") for s in results["insights/vieja.md"]["signals"]))
        self.assertFalse(any("verification" in s for s in results["insights/fresca.md"]["signals"]))
        self.assertTrue(any("verification" in s for s in results["publico/x/ficha.md"]["signals"]))
        self.assertFalse(any("verification" in s for s in results["projects/x/interna.md"]["signals"]))

        due = collect_verification_due(self.vault, config=self.config)
        self.assertEqual(sorted(d["file"] for d in due), ["insights/vieja.md", "publico/x/ficha.md"])
        self.assertTrue(all(d["kind"] == "verificacion" and d["vencido"] for d in due))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = review_run(SimpleNamespace(json=True, count=False), self.vault, self.config)
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertEqual({d["kind"] for d in data}, {"verificacion"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review_run(SimpleNamespace(json=False, count=False), self.vault, self.config)
        self.assertIn("Verificación vencida (2)", out.getvalue())


class TestVerificacionStrict(Base):
    review_strict = True

    def test_validate_rechaza_con_strict(self):
        _note(self.vault, "insights/i.md", "Insight", status="vigente",
              verificado_el="2026-05-01", revisar_el="2026-04-01")
        ok, msg, _ = self.validate("insights/i.md")
        self.assertFalse(ok)
        self.assertIn("anterior", msg)


# ── versión vigente ──────────────────────────────────────────────────────────

class TestVigente(Base):
    def setUp(self):
        super().setUp()
        # Cadena: v1 (superada) ← corrige ← v2 (superada) ← corrige ← v3 (vigente)
        _note(self.vault, "insights/v1.md", "Insight", status="superada")
        _note(self.vault, "insights/v2.md", "Insight", status="superada",
              links=[("insights/v1.md", "corrige")])
        _note(self.vault, "insights/v3.md", "Insight", status="vigente",
              links=[("insights/v2.md", "corrige")])
        # Una decisión vigente que se apoya en la superada v1, y otra en v3
        _note(self.vault, "decisions/apoyada-en-vieja.md", "Decision", status="aceptada",
              decidida_por="rafael", decidida_el="2026-01-01",
              links=[("insights/v1.md", "fundamenta")])
        _note(self.vault, "decisions/apoyada-en-vigente.md", "Decision", status="aceptada",
              decidida_por="rafael", decidida_el="2026-01-01",
              links=[("insights/v3.md", "fundamenta")])
        # Nota que enlaza por wikilink a v1
        _note(self.vault, "insights/lectora.md", "Insight", status="vigente",
              body="Ver [[insights/v1]].")

    def test_mapa_sigue_la_cadena_hasta_la_vigente(self):
        graph = build_graph(self.vault)
        m = build_superseded_map(self.vault, graph, self.config)
        self.assertEqual(m, {"insights/v1.md": "insights/v3.md", "insights/v2.md": "insights/v3.md"})

    def test_sin_esquema_el_mapa_esta_vacio(self):
        cfg = _config()
        cfg["types"]["states"] = {"strict": False, "schemas": {}}
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        graph = build_graph(self.vault)
        self.assertEqual(build_superseded_map(self.vault, graph, Config(self.vault)), {})

    def _traverse(self, target, **kw):
        from cli.commands.traverse import run as traverse_run
        args = SimpleNamespace(target=target, seeds=None, depth=kw.get("depth", 1),
                               direction="both", json=True, no_cyber=True, edge_type=None,
                               filter=False, all_versions=kw.get("all_versions", False))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = traverse_run(args, self.vault, self.config)
        self.assertEqual(rc, 0, err.getvalue())
        return json.loads(out.getvalue())

    def test_traverse_salta_a_la_vigente(self):
        data = self._traverse("insights/lectora.md")
        paths = {n["path"]: n for n in data["nodes"]}
        self.assertIn("insights/v3.md", paths)
        self.assertNotIn("insights/v1.md", paths)
        self.assertEqual(sorted(paths["insights/v3.md"]["superseded"]), ["insights/v1.md"])
        self.assertFalse(data["all_versions"])

    def test_traverse_all_versions_recorre_la_cadena(self):
        data = self._traverse("insights/lectora.md", all_versions=True)
        paths = {n["path"] for n in data["nodes"]}
        self.assertIn("insights/v1.md", paths)
        self.assertNotIn("insights/v3.md", paths)
        self.assertTrue(data["all_versions"])

    def test_traverse_desde_una_superada_arranca_en_la_vigente(self):
        data = self._traverse("insights/v1.md")
        self.assertEqual(data["origin"], "insights/v3.md")
        self.assertTrue(any("Superseded" in w for w in data.get("warnings", [])))

    def _search(self, **kw):
        from cli.commands.search import run as search_run
        args = SimpleNamespace(json=True, todos=False, all=False, aging=False, query=None,
                               filter_type="Insight", filter_status=kw.get("status"),
                               cyber_field=None, cyber_value=None, review_due=False,
                               since=None, until=None, with_graph=False,
                               all_versions=kw.get("all_versions", False))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = search_run(args, self.vault, self.config)
        self.assertEqual(rc, 0)
        return {c["file"] for c in json.loads(out.getvalue())}

    def test_search_oculta_las_superadas_salvo_que_se_pidan(self):
        self.assertEqual(self._search(), {"insights/v3.md", "insights/lectora.md"})
        self.assertEqual(self._search(all_versions=True),
                         {"insights/v1.md", "insights/v2.md", "insights/v3.md", "insights/lectora.md"})
        self.assertEqual(self._search(status="superada"), {"insights/v1.md", "insights/v2.md"})

    def test_validate_avisa_cuando_una_vigente_se_apoya_en_una_superada(self):
        ok, msg, err = self.validate("decisions/apoyada-en-vieja.md")
        self.assertTrue(ok, msg)
        self.assertIn("reemplazada", err)
        self.assertIn("insights/v1.md", err)
        ok, msg, err = self.validate("decisions/apoyada-en-vigente.md")
        self.assertTrue(ok, msg)
        self.assertNotIn("reemplazada", err)
        # La cadena de corrige en sí no avisa: v3 corrige a v2 (superada) y eso es lo esperado
        ok, msg, err = self.validate("insights/v3.md")
        self.assertTrue(ok, msg)
        self.assertNotIn("apoyate", err)


if __name__ == "__main__":
    unittest.main()
