"""`search --body` dice dónde, cuánto y bajo qué título coincidió el cuerpo.

Contrato (ronda 2, D3): con `--body`, cada acierto de cuerpo (`match: "body"`)
trae en JSON `line` (línea de la primera mención, 1-indexada desde el inicio
del archivo, igual que numera `read`), `snippet` (esa línea, ≤ 160 chars,
centrada en el término, «…» en los bordes recortados), `section` (último
encabezado Markdown que precede a la mención, ≤ 60 chars, o null) y `matches`
(líneas del cuerpo que mencionan el término; con varios términos AND, las que
mencionan al menos uno). En la tabla, donde decía «(cuerpo)» ahora dice
«(cuerpo L<n> · <k> menc. · § <sección>)». Los aciertos de frontmatter no
llevan estos campos y sin `--body` el JSON no cambia.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cli.config import Config
from cli.commands.search import run as search_run, locate_body_match

CONFIG = (
    "types:\n  valid: [Decision, Plan]\n"
    "features:\n  cognitive_trace: false\n"
)

# Frontmatter corto (5 líneas de archivo incluyendo los dos `---`) y largo
# (links y descripción multilínea): la línea del cuerpo debe contarse desde
# el inicio del archivo en los dos casos.
FM_CORTO = (
    '---\ntype: Plan\ntitle: "Sitio del cliente"\n'
    'description: "Publicar la portada"\nstatus: pendiente\n---\n'
)
FM_LARGO = (
    '---\ntype: Decision\ntitle: "Leads al CRM"\n'
    'description: >\n  Una descripción\n  en dos líneas\n'
    "status: aceptada\ntags: [leads, crm]\n"
    "links:\n  - target: plans/sitio.md\n    type: depende\n"
    "  - target: plans/otro.md\n    type: extiende\n---\n"
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SearchLineaSeccionMencionesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        _write(self.vault / ".okf.config.yaml", CONFIG)
        self.config = Config(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def _search(self, query, body=False, json_out=True):
        args = SimpleNamespace(json=json_out, todos=False, all=False, aging=False,
                               query=query, filter_type=None, filter_status=None,
                               cyber_field=None, cyber_value=None, review_due=False,
                               since=None, until=None, with_graph=False,
                               all_versions=False, body=body)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = search_run(args, self.vault, self.config)
        self.assertEqual(rc, 0)
        return json.loads(out.getvalue()) if json_out else out.getvalue()

    def _cuerpo(self, query="formulario"):
        data = self._search(query, body=True)
        return {c["file"]: c for c in data if c["match"] == "body"}

    # ── línea ─────────────────────────────────────────────────────────────

    def test_linea_cuenta_desde_el_inicio_del_archivo_con_frontmatter_corto(self):
        # FM_CORTO ocupa las líneas 1-6; el cuerpo empieza en la 7 (vacía).
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + "\nIntro.\n\nFalta el formulario de contacto.\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(hit["line"], 10)
        texto = (self.vault / "plans" / "sitio.md").read_text(encoding="utf-8")
        self.assertIn("formulario", texto.split("\n")[hit["line"] - 1])

    def test_linea_cuenta_desde_el_inicio_del_archivo_con_frontmatter_largo(self):
        # FM_LARGO ocupa las líneas 1-14; el cuerpo empieza en la 15.
        _write(self.vault / "decisions" / "leads.md",
               FM_LARGO + "\nIntro.\nEl formulario del sitio manda el lead.\n")
        hit = self._cuerpo()["decisions/leads.md"]
        self.assertEqual(hit["line"], 17)
        texto = (self.vault / "decisions" / "leads.md").read_text(encoding="utf-8")
        self.assertIn("formulario", texto.split("\n")[hit["line"] - 1])

    # ── sección ───────────────────────────────────────────────────────────

    def test_seccion_es_el_ultimo_encabezado_que_precede(self):
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + "\n# Plan\n\n## Frente 1 — el sitio\n\nTexto.\n\n"
               "### Detalle\n\nUn formulario de contacto.\n\n## Frente 2\n\nOtro formulario.\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(hit["section"], "Detalle")
        self.assertEqual(hit["matches"], 2)

    def test_seccion_es_null_sin_encabezados(self):
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + "\nUn formulario sin títulos.\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertIsNone(hit["section"])
        self.assertEqual(hit["line"], 8)

    def test_seccion_se_recorta_a_60_y_un_hash_en_bloque_de_codigo_no_es_encabezado(self):
        largo = "Encabezado " * 10  # 110 chars
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + f"\n## {largo}\n\n```\n# esto es un comentario\n```\n\n"
               "El formulario.\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(len(hit["section"]), 60)
        self.assertTrue(hit["section"].endswith("…"))
        self.assertTrue(hit["section"].startswith("Encabezado"))

    def test_mencion_en_el_propio_encabezado_reporta_ese_encabezado(self):
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + "\n## Antes\n\n## El formulario\n\nTexto.\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(hit["section"], "El formulario")
        self.assertEqual(hit["snippet"], "## El formulario")

    # ── menciones ─────────────────────────────────────────────────────────

    def test_matches_cuenta_lineas_no_ocurrencias(self):
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + "\nformulario y otro formulario en la misma línea.\n"
               "Nada.\nOtro formulario.\nY el FORMULARIO en mayúsculas.\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(hit["matches"], 3)
        self.assertEqual(hit["line"], 8)

    def test_con_varios_terminos_cuenta_lineas_con_al_menos_uno_de_los_del_cuerpo(self):
        # «leads» está en el frontmatter (tags): no se localiza en el cuerpo.
        # «formulario» y «turnstile» solo en el cuerpo: cuentan las líneas
        # que mencionan cualquiera de los dos.
        _write(self.vault / "decisions" / "leads.md",
               FM_LARGO + "\nLos leads llegan.\nEl formulario pasa.\n"
               "Turnstile lo filtra.\nFormulario y turnstile juntos.\n")
        hit = self._cuerpo("leads formulario turnstile")["decisions/leads.md"]
        self.assertEqual(hit["matches"], 3)
        self.assertEqual(hit["line"], 17)
        self.assertIn("formulario", hit["snippet"])

    # ── snippet ───────────────────────────────────────────────────────────

    def test_snippet_corto_es_la_linea_entera_sin_sangria(self):
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + "\n   - [ ] Construir el\tformulario  de contacto\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(hit["snippet"], "- [ ] Construir el formulario de contacto")

    def test_snippet_largo_se_recorta_a_160_centrado_con_puntos_suspensivos(self):
        linea = "a" * 200 + " formulario " + "b" * 200
        _write(self.vault / "plans" / "sitio.md", FM_CORTO + "\n" + linea + "\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(len(hit["snippet"]), 160)
        self.assertIn("formulario", hit["snippet"])
        self.assertTrue(hit["snippet"].startswith("…"))
        self.assertTrue(hit["snippet"].endswith("…"))
        self.assertNotIn("\n", hit["snippet"])
        # centrado: el término queda cerca de la mitad
        pos = hit["snippet"].index("formulario")
        self.assertTrue(60 <= pos <= 90, pos)

    def test_snippet_con_el_termino_al_final_solo_recorta_el_borde_izquierdo(self):
        linea = "a" * 300 + " formulario"
        _write(self.vault / "plans" / "sitio.md", FM_CORTO + "\n" + linea + "\n")
        hit = self._cuerpo()["plans/sitio.md"]
        self.assertEqual(len(hit["snippet"]), 160)
        self.assertTrue(hit["snippet"].startswith("…"))
        self.assertTrue(hit["snippet"].endswith("formulario"))

    # ── frontmatter y sin --body ──────────────────────────────────────────

    def test_acierto_de_frontmatter_no_lleva_los_campos(self):
        _write(self.vault / "decisions" / "leads.md",
               '---\ntype: Decision\ntitle: "Leads"\n'
               'description: "El formulario escribe en el CRM"\nstatus: aceptada\n---\n'
               "\nEl formulario también aquí.\n")
        data = self._search("formulario", body=True)
        self.assertEqual(data[0]["match"], "frontmatter")
        for k in ("line", "snippet", "section", "matches", "_body", "_body_raw", "_body_start"):
            self.assertNotIn(k, data[0])

    def test_sin_body_el_json_no_cambia(self):
        _write(self.vault / "decisions" / "leads.md",
               '---\ntype: Decision\ntitle: "Leads"\n'
               'description: "El formulario escribe en el CRM"\nstatus: aceptada\n---\n'
               "\nCuerpo.\n")
        _write(self.vault / "plans" / "sitio.md", FM_CORTO + "\nEl formulario.\n")
        data = self._search("formulario", body=False)
        self.assertEqual([c["file"] for c in data], ["decisions/leads.md"])
        for k in ("match", "line", "snippet", "section", "matches",
                  "_body", "_body_raw", "_body_start"):
            self.assertNotIn(k, data[0])

    # ── tabla ─────────────────────────────────────────────────────────────

    def test_la_tabla_muestra_linea_menciones_y_seccion(self):
        _write(self.vault / "plans" / "sitio.md",
               FM_CORTO + "\n## Frente 1 — el sitio\n\nUn formulario.\nOtro formulario.\n")
        out = self._search("formulario", body=True, json_out=False)
        self.assertIn("(cuerpo L10 · 2 menc. · § Frente 1 — el sitio)", out)

    def test_la_tabla_sin_encabezado_omite_la_seccion(self):
        _write(self.vault / "plans" / "sitio.md", FM_CORTO + "\nUn formulario.\n")
        out = self._search("formulario", body=True, json_out=False)
        self.assertIn("(cuerpo L8 · 1 menc.)", out)
        self.assertNotIn("§", out)

    # ── helper ────────────────────────────────────────────────────────────

    def test_locate_sin_terminos_de_texto_devuelve_vacio(self):
        concept = {"title": "t", "description": "d", "tags": [],
                   "_body_raw": "formulario", "_body_start": 5}
        self.assertEqual(locate_body_match(concept, "type:plan"),
                         {"line": None, "snippet": "", "section": None, "matches": 0})


if __name__ == "__main__":
    unittest.main()
