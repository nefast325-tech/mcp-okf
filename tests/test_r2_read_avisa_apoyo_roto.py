"""`read` avisa, antes del cuerpo, cuando la nota se apoya en algo derogado.

Dolor D2 de la ronda 2: una nota vigente con `extiende`/`fundamenta`/
`depende`/`aplica` hacia una nota reemplazada (o muerta sin sucesora) devolvía
el cuerpo sin decirlo; `validate` lo detecta pero esa salida no se lee cuando
se razona sobre el cuerpo. Ahora `read` imprime a stdout una línea por arista
rota, con la arista, el destino, su estado y la vigente si existe, y cierra
con el conteo y qué hacer. Una nota limpia no cambia ni un byte.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.config import Config
from cli.commands.read import run as read_run
from cli.vigente import apoyos_rotos_para_read, formato_apoyos_rotos

TS = "2026-01-01T00:00:00-05:00"

CONFIG = {
    "types": {
        "valid": ["Decision", "Sistema", "Plan"],
        "directory": {"Decision": "decisions", "Sistema": "sistema", "Plan": "plans"},
        "states": {"strict": True, "schemas": {
            "compromiso": {"types": ["Decision"], "initial": "propuesta",
                           "states": ["propuesta", "aceptada", "reemplazada"],
                           "replaced": "reemplazada"},
            "infra": {"types": ["Sistema"], "initial": "vigente",
                      "states": ["vigente", "en-revision", "deprecada"],
                      "replaced": "deprecada"},
            "trabajo": {"types": ["Plan"], "initial": "pendiente",
                        "states": ["pendiente", "en-curso", "hecha", "cancelada"],
                        "dead": ["cancelada"]},
        }},
    },
    "graph": {"strict_pairs": False},
    "edge_types": {"corrige": {}, "fundamenta": {}, "extiende": {}, "depende": {},
                   "aplica": {}},
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}


def _note(vault, rel, ctype, status, title, links=None, body="Cuerpo.\n"):
    lines = ["---", f"type: {ctype}", f'title: "{title}"', f'description: "{title}."',
             f"timestamp: {TS}", f"created: {TS}", f"status: {status}"]
    if links:
        lines.append("links:")
        for target, etype in links:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {etype}")
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n\n" + body, encoding="utf-8")


class TestReadAvisaApoyoRoto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
        self.config = Config(self.vault)
        # vieja-a y vieja-b reemplazadas, las dos corregidas por `nueva`;
        # `huerfana` reemplazada sin que nadie la corrija.
        _note(self.vault, "decisions/vieja-a.md", "Decision", "reemplazada", "Vieja A")
        _note(self.vault, "decisions/vieja-b.md", "Decision", "reemplazada", "Vieja B")
        _note(self.vault, "decisions/huerfana.md", "Decision", "reemplazada", "Huerfana")
        _note(self.vault, "decisions/nueva.md", "Decision", "aceptada", "Nueva",
              links=[("decisions/vieja-a.md", "corrige"), ("decisions/vieja-b.md", "corrige")])
        _note(self.vault, "decisions/viva.md", "Decision", "aceptada", "Viva")
        # plan cancelado (muerto, sin sucesora) y un plan que lo extiende
        _note(self.vault, "plans/cancelado.md", "Plan", "cancelada", "Cancelado")
        _note(self.vault, "plans/sigue.md", "Plan", "pendiente", "Sigue",
              links=[("plans/cancelado.md", "extiende"), ("decisions/viva.md", "depende")])
        # sistema vigente apoyado en dos reemplazadas con la misma vigente, una
        # huérfana y una viva; el target sin `.md` también tiene que resolver
        cuerpo = "\n".join(f"Línea {i}." for i in range(1, 21)) + "\n"
        _note(self.vault, "sistema/arquitectura.md", "Sistema", "vigente", "Arquitectura",
              links=[("decisions/vieja-a.md", "fundamenta"),
                     ("decisions/viva.md", "fundamenta"),
                     ("decisions/vieja-b", "fundamenta"),
                     ("decisions/huerfana.md", "aplica")],
              body=cuerpo)
        _note(self.vault, "sistema/limpio.md", "Sistema", "vigente", "Limpio",
              links=[("decisions/viva.md", "fundamenta"), ("decisions/nueva.md", "depende")])
        # una reemplazada que a su vez se apoya en otra reemplazada: es historia
        _note(self.vault, "sistema/viejo.md", "Sistema", "deprecada", "Viejo",
              links=[("decisions/vieja-a.md", "fundamenta")])
        _note(self.vault, "sistema/nuevo.md", "Sistema", "vigente", "Nuevo",
              links=[("sistema/viejo.md", "corrige")])
        # `aplica` hacia una muerta (no reemplazada) no avisa, como en validate
        _note(self.vault, "sistema/aplica-muerta.md", "Sistema", "vigente", "Aplica muerta",
              links=[("plans/cancelado.md", "aplica")])

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self, target, offset=1, limit=500):
        args = SimpleNamespace(target=target, offset=offset, limit=limit, no_touch=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = read_run(args, self.vault, self.config)
        self.assertEqual(rc, 0, err.getvalue())
        return out.getvalue()

    @staticmethod
    def _avisos(out):
        return [l for l in out.splitlines() if l.startswith("⚠️")]

    # ── reemplazada con vigente ───────────────────────────────────────────

    def test_reemplazada_con_vigente_una_linea_por_arista_antes_del_cuerpo(self):
        out = self._read("sistema/arquitectura")
        lines = out.splitlines()
        avisos = self._avisos(out)
        self.assertEqual(len(avisos), 3, out)
        self.assertEqual(
            avisos[0],
            "⚠️  se apoya (fundamenta) en decisions/vieja-a, reemplazada "
            "→ vigente decisions/nueva")
        # misma vigente que la línea anterior: no se repite el slug
        self.assertEqual(
            avisos[1],
            "⚠️  se apoya (fundamenta) en decisions/vieja-b, reemplazada → la misma vigente")
        self.assertEqual(
            avisos[2],
            "⚠️  se apoya (aplica) en decisions/huerfana, reemplazada (sin vigente declarada)")
        # cierre con conteo y qué hacer, y recién después el cuerpo numerado
        self.assertEqual(lines[3], "   → 3 apoyos rotos: re-anclá la arista o revisá el cuerpo.")
        self.assertEqual(lines[4], "1|---")
        self.assertNotIn("♻️", out)
        # la viva no aparece en el banner
        self.assertNotIn("decisions/viva", "\n".join(lines[:4]))

    def test_el_helper_devuelve_las_aristas_en_orden_de_links(self):
        rotos = apoyos_rotos_para_read(self.vault, "sistema/arquitectura.md", self.config)
        self.assertEqual([(r["type"], r["target"], r["clase"], r["vigente"]) for r in rotos], [
            ("fundamenta", "decisions/vieja-a.md", "reemplazada", "decisions/nueva.md"),
            ("fundamenta", "decisions/vieja-b.md", "reemplazada", "decisions/nueva.md"),
            ("aplica", "decisions/huerfana.md", "reemplazada", None),
        ])

    def test_la_vigente_se_repite_si_no_es_la_de_la_linea_anterior(self):
        rotos = [
            {"type": "fundamenta", "target": "decisions/a.md", "target_status": "reemplazada",
             "clase": "reemplazada", "vigente": "decisions/x.md"},
            {"type": "fundamenta", "target": "decisions/b.md", "target_status": "reemplazada",
             "clase": "reemplazada", "vigente": "decisions/y.md"},
            {"type": "depende", "target": "decisions/c.md", "target_status": "reemplazada",
             "clase": "reemplazada", "vigente": "decisions/x.md"},
        ]
        lines = formato_apoyos_rotos(rotos)
        self.assertTrue(lines[0].endswith("→ vigente decisions/x"))
        self.assertTrue(lines[1].endswith("→ vigente decisions/y"))
        self.assertTrue(lines[2].endswith("→ vigente decisions/x"), lines[2])
        self.assertEqual(formato_apoyos_rotos([]), [])

    # ── muerta sin sucesora ───────────────────────────────────────────────

    def test_apoyo_en_plan_cancelado_avisa_muerta_sin_sucesora(self):
        out = self._read("plans/sigue")
        lines = out.splitlines()
        self.assertEqual(lines[0], "⚠️  se apoya (extiende) en plans/cancelado, cancelada "
                                   "(muerta, sin sucesora)")
        self.assertEqual(lines[1], "   → 1 apoyo roto: re-anclá la arista o revisá el cuerpo.")
        self.assertEqual(lines[2], "1|---")
        self.assertNotIn("decisions/viva", "\n".join(lines[:2]))

    def test_aplica_hacia_una_muerta_no_avisa_como_en_validate(self):
        out = self._read("sistema/aplica-muerta")
        self.assertTrue(out.startswith("1|---"), out.splitlines()[0])

    # ── limpio ────────────────────────────────────────────────────────────

    def test_nota_limpia_no_cambia_ni_un_byte(self):
        out = self._read("sistema/limpio")
        self.assertTrue(out.startswith("1|---"), out.splitlines()[0])
        self.assertNotIn("⚠️", out)
        self.assertNotIn("apoyo", out)

    def test_una_reemplazada_que_se_apoya_en_otra_es_historia_y_no_avisa(self):
        out = self._read("sistema/viejo")
        self.assertTrue(out.startswith("♻️"), out.splitlines()[0])
        self.assertNotIn("⚠️", out)

    def test_el_grafo_de_corrige_da_el_mismo_mapa_que_build_graph(self):
        from cli.commands.graph import build_graph
        from cli.vigente import build_superseded_map, grafo_de_corrige
        fm_cache = {}
        liviano = build_superseded_map(self.vault, grafo_de_corrige(self.vault, fm_cache),
                                       self.config, fm_cache=fm_cache)
        completo = build_superseded_map(self.vault, build_graph(self.vault), self.config)
        self.assertEqual(liviano, completo)
        self.assertEqual(liviano, {"decisions/vieja-a.md": "decisions/nueva.md",
                                   "decisions/vieja-b.md": "decisions/nueva.md",
                                   "sistema/viejo.md": "sistema/nuevo.md"})
        # un grafo ya construido se acepta tal cual
        rotos = apoyos_rotos_para_read(self.vault, "sistema/arquitectura.md", self.config,
                                graph=build_graph(self.vault))
        self.assertEqual(rotos[0]["vigente"], "decisions/nueva.md")

    def test_sin_esquema_de_estados_no_hay_banner(self):
        self.assertEqual(apoyos_rotos_para_read(self.vault, "sistema/arquitectura.md", None), [])

    # ── --offset / --limit ────────────────────────────────────────────────

    def test_offset_y_limit_no_rompen_el_banner(self):
        # frontmatter de 17 líneas + 1 en blanco: «Línea 1.» es la 19
        out = self._read("sistema/arquitectura", offset=20, limit=3)
        lines = out.splitlines()
        self.assertEqual(len(self._avisos(out)), 3)
        self.assertTrue(lines[3].startswith("   → 3 apoyos rotos"))
        self.assertEqual(lines[4], "20|Línea 2.")
        self.assertEqual(lines[6], "22|Línea 4.")
        self.assertIn("─── truncated (22/", out)
        # el banner sigue apareciendo aunque el tramo pedido sea el último
        out2 = self._read("plans/sigue", offset=10, limit=100)
        self.assertTrue(out2.startswith("⚠️  se apoya (extiende) en plans/cancelado"))
        self.assertNotIn("truncated", out2)


if __name__ == "__main__":
    unittest.main()
