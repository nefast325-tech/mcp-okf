"""`okf board`: el vault como JSON de tablero.

- Columnas = estados del esquema por capa; la capa de registro no exporta tarjetas.
- Cada tarjeta trae capa, status, iniciativa (string o lista), casillas y stale.
- Las tareas salen de las casillas abiertas con antigüedad.
- --iniciativa filtra tarjetas y tareas; --out escribe el archivo.
- Sin esquema: una capa 'sin-esquema' con los status observados.
"""

import io
import json
import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.commands.board import build_board, run
from cli.config import Config

TS = "2026-01-01T00:00:00-05:00"
SCHEMA = {
    "types": {
        "valid": ["Plan", "Insight", "Session"],
        "directory": {"Plan": "plans", "Insight": "insights", "Session": "sessions"},
        "exempt_from_no_backlinks": ["Session", "Insight", "Plan"],
        "states": {"strict": True, "schemas": {
            "trabajo": {"types": ["Plan"], "initial": "pendiente",
                        "states": ["pendiente", "en-curso", "hecha"]},
            "conocimiento": {"types": ["Insight"], "initial": "vigente",
                             "states": ["vigente", "superada"]},
            "registro": {"types": ["Session"], "states": []},
        }},
    },
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}


def _note(vault, rel, ctype, status=None, iniciativa=None, body="Cuerpo."):
    lines = ["---", f"type: {ctype}", 'title: "T"', 'description: "D."',
             "tags: [a, b]", f"timestamp: {TS}", f"created: {TS}"]
    if status:
        lines.append(f"status: {status}")
    if iniciativa is not None:
        lines.append(f"iniciativa: {iniciativa}")
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + f"\n\n{body}\n", encoding="utf-8")


class BoardBase(unittest.TestCase):
    config_data = SCHEMA

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(self.config_data), encoding="utf-8")
        _note(self.vault, "plans/p1.md", "Plan", "en-curso", "inmobiliaria",
              body="- [x] hecha\n- [ ] pendiente uno\n- [ ] pendiente dos")
        _note(self.vault, "plans/p2.md", "Plan", "hecha", "[inmobiliaria, caribe-tropical]")
        _note(self.vault, "insights/i1.md", "Insight", "vigente")
        _note(self.vault, "sessions/s1.md", "Session", body="- [ ] no es tarea viva")
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.vault, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"],
                       cwd=self.vault, check=True)
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()


class TestBoard(BoardBase):
    def test_columnas_desde_el_esquema_y_sin_registro(self):
        b = build_board(self.vault, self.config)
        self.assertEqual(b["columnas"]["trabajo"], ["pendiente", "en-curso", "hecha"])
        self.assertNotIn("registro", b["columnas"])
        files = {c["file"] for c in b["tarjetas"]}
        self.assertEqual(files, {"plans/p1", "plans/p2", "insights/i1"})

    def test_tarjeta_trae_capa_iniciativa_casillas_y_stale(self):
        b = build_board(self.vault, self.config)
        p1 = next(c for c in b["tarjetas"] if c["file"] == "plans/p1")
        self.assertEqual(p1["capa"], "trabajo")
        self.assertEqual(p1["status"], "en-curso")
        self.assertEqual(p1["iniciativa"], ["inmobiliaria"])
        self.assertEqual(p1["casillas"], {"hechas": 1, "total": 3})
        self.assertEqual(p1["tags"], ["a", "b"])
        self.assertIn(p1["stale"]["level"], ("STALE", "ATTENTION", "FRESH"))
        p2 = next(c for c in b["tarjetas"] if c["file"] == "plans/p2")
        self.assertEqual(p2["iniciativa"], ["inmobiliaria", "caribe-tropical"])
        i1 = next(c for c in b["tarjetas"] if c["file"] == "insights/i1")
        self.assertIsNone(i1["iniciativa"])

    def test_tarjeta_trae_relaciones_y_entrantes(self):
        # Una nota con aristas tipadas: destino sin .md, con .md, y uno que no existe.
        p3 = self.vault / "plans" / "p3.md"
        p3.write_text(
            "---\ntype: Plan\ntitle: \"P3\"\ndescription: \"D.\"\nstatus: pendiente\n"
            f"timestamp: {TS}\ncreated: {TS}\nlinks:\n"
            "  - target: plans/p1\n    type: aplica\n"
            "  - target: insights/i1.md\n    type: depende\n"
            "  - target: plans/no-existe\n    type: extiende\n---\n\nCuerpo.\n",
            encoding="utf-8")
        b = build_board(self.vault, self.config)
        por = {c["file"]: c for c in b["tarjetas"]}
        self.assertEqual(por["plans/p3"]["relaciones"], [
            {"type": "aplica", "target": "plans/p1", "title": "T"},
            {"type": "depende", "target": "insights/i1", "title": "T"},
            {"type": "extiende", "target": "plans/no-existe", "title": None},
        ])
        self.assertEqual(por["plans/p3"]["entrantes"], 0)
        self.assertEqual(por["plans/p1"]["entrantes"], 1)
        self.assertEqual(por["insights/i1"]["entrantes"], 1)
        self.assertEqual(por["plans/p2"]["entrantes"], 0)
        self.assertEqual(por["plans/p2"]["relaciones"], [])

    def test_tareas_son_casillas_abiertas_con_antiguedad(self):
        b = build_board(self.vault, self.config)
        self.assertEqual(sorted(t["text"] for t in b["tareas"]), ["pendiente dos", "pendiente uno"])
        self.assertTrue(all(t["file"] == "plans/p1" for t in b["tareas"]))
        self.assertIn(b["tareas"][0]["age_status"], ("fresh", "aging", "stale", "unknown"))

    def test_salud_por_capa(self):
        b = build_board(self.vault, self.config)
        self.assertEqual(b["salud"]["trabajo"]["por_estado"], {"pendiente": 0, "en-curso": 1, "hecha": 1})
        self.assertEqual(b["salud"]["conocimiento"]["por_estado"], {"vigente": 1, "superada": 0})

    def test_filtro_por_iniciativa(self):
        b = build_board(self.vault, self.config, iniciativa="caribe-tropical")
        self.assertEqual([c["file"] for c in b["tarjetas"]], ["plans/p2"])
        self.assertEqual(b["tareas"], [])
        self.assertEqual(b["iniciativa"], "caribe-tropical")

    def test_out_escribe_json(self):
        out = self.vault / "tablero.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = run(SimpleNamespace(out=str(out), iniciativa=None, no_tasks=True),
                       self.vault, self.config)
        self.assertEqual(code, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["tareas"], [])
        self.assertEqual(len(data["tarjetas"]), 3)
        self.assertTrue(data["commit"])


class TestBoardSinEsquema(BoardBase):
    config_data = {
        "types": {"valid": ["Plan", "Insight", "Session"],
                  "directory": {"Plan": "plans", "Insight": "insights", "Session": "sessions"}},
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }

    def test_columnas_observadas(self):
        b = build_board(self.vault, self.config, with_tasks=False)
        self.assertEqual(b["columnas"], {"sin-esquema": ["en-curso", "hecha", "vigente"]})
        self.assertEqual(len(b["tarjetas"]), 4)  # sin esquema, Session también entra
        self.assertTrue(all(c["capa"] == "sin-esquema" for c in b["tarjetas"]))


if __name__ == "__main__":
    unittest.main()
