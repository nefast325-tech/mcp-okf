"""Tope por defecto de `traverse` (`traverse.budget` en .okf.config.yaml).

Medido el 19-nov-2026: con 131 notas los agentes hicieron 262 traverse y el
más grande alcanzó 87 nodos (8.700 tokens); con 10.131 notas, una nota a la
que apuntan 400 devolvía más de lo que un agente puede leer de una vez.
`--budget` ya existía pero había que pedirlo. Ahora, sin --budget, rige el tope
del vault (100 por defecto); --budget 0 lo quita. Mientras el tope no corta,
la salida es byte a byte la de antes.
"""

import asyncio
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cli.commands.traverse import run as traverse_run
from cli.config import Config

N_HOJAS = 12  # notas que apuntan al nodo central: 13 alcanzables con él


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    CONFIG = ""
    N_HOJAS = N_HOJAS

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        _write(self.vault / ".okf.config.yaml",
               "types:\n  valid: [Insight]\nfeatures:\n  cognitive_trace: false\n" + self.CONFIG)
        _write(self.vault / "insights" / "centro.md",
               '---\ntype: Insight\ntitle: "Centro"\nstatus: vigente\ndescription: "Al que todos apuntan."\n---\n\nC.\n')
        for i in range(self.N_HOJAS):
            _write(self.vault / "insights" / f"hoja-{i:02d}.md",
                   f'---\ntype: Insight\ntitle: "Hoja {i}"\nstatus: vigente\ndescription: "Una hoja."\n'
                   "links:\n  - target: insights/centro.md\n    type: extiende\n---\n\nH.\n")
        self.config = Config(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def traverse(self, budget=None, json_out=False, esperado=0):
        args = SimpleNamespace(target="insights/centro.md", seeds=None, depth=1, direction="in",
                               json=json_out, no_cyber=True, edge_type="extiende", filter=True,
                               all_versions=False, compact=True, budget=budget)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = traverse_run(args, self.vault, self.config)
        self.assertEqual(rc, esperado, err.getvalue())
        return json.loads(out.getvalue()) if json_out else out.getvalue()


class TestTopeDelVault(Base):
    CONFIG = "traverse:\n  budget: 5\n"

    def test_sin_budget_corta_en_el_tope_y_dice_cuantos_quedaron_fuera(self):
        out = self.traverse()
        self.assertIn("✂️  8 nodo(s) más quedaron fuera por el tope del vault (5)", out)
        self.assertIn("pide budget=0", out)
        self.assertIn("📎 recibo: 5 nodos de 13", out)
        self.assertIn("truncado: sí (tope del vault: 5)", out)

    def test_en_json_el_recibo_lo_marca(self):
        r = self.traverse(json_out=True)["receipt"]
        self.assertEqual((r["nodes"], r["reachable"], r["truncated"], r["budget"]), (5, 13, True, 5))
        self.assertTrue(r["budget_default"])

    def test_budget_0_lo_quita(self):
        out = self.traverse(budget=0)
        self.assertNotIn("✂️", out)
        self.assertIn("📎 recibo: 13 nodos de 13", out)
        self.assertIn("truncado: no", out)
        self.assertNotIn("budget", out.splitlines()[-1])

    def test_un_budget_explicito_manda_y_no_agrega_aviso(self):
        out = self.traverse(budget=3)
        self.assertNotIn("✂️", out)
        self.assertIn("truncado: sí (--budget 3)", out)
        self.assertNotIn("budget_default", self.traverse(budget=3, json_out=True)["receipt"])

    def test_budget_negativo_es_error(self):
        self.traverse(budget=-1, esperado=1)


class TestBajoElTope(Base):
    """Sin `traverse:` en la config rige 100: con 13 alcanzables no corta nada."""

    def test_la_salida_es_identica_a_la_de_sin_tope(self):
        self.assertEqual(self.config.traverse_budget, 100)
        self.assertEqual(self.traverse(), self.traverse(budget=0))
        r = self.traverse(json_out=True)["receipt"]
        self.assertIsNone(r["budget"])
        self.assertFalse(r["truncated"])
        self.assertNotIn("budget_default", r)


class TestConfigRara(unittest.TestCase):
    def _config(self, texto):
        with tempfile.TemporaryDirectory() as d:
            _write(Path(d) / ".okf.config.yaml", texto)
            return Config(Path(d)).traverse_budget

    def test_valores_invalidos_vuelven_a_100(self):
        for texto in ("traverse:\n  budget: mucho\n", "traverse:\n  budget: -3\n", "traverse: 7\n"):
            with self.subTest(texto=texto):
                self.assertEqual(self._config(texto), 100)

    def test_0_es_sin_tope(self):
        self.assertEqual(self._config("traverse:\n  budget: 0\n"), 0)


class TestMCPBudgetFlag(unittest.TestCase):
    """La tool registrada conserva la distinción omitido/0/positivo/negativo."""

    def test_mcp_pasa_todo_budget_explicito_al_cli_y_a_la_traza(self):
        import server

        for supplied in ({}, {"budget": None}, {"budget": 0},
                         {"budget": 7}, {"budget": -1}):
            with self.subTest(supplied=supplied):
                with patch.object(server, "_run", return_value="ok") as run:
                    asyncio.run(server.mcp.call_tool("traverse", {
                        "slug": "insights/centro.md", "edge_type": "extiende",
                        **supplied,
                    }))
                run.assert_called_once()
                args = run.call_args.args[0]
                params = run.call_args.kwargs["params"]
                self.assertEqual(run.call_args.kwargs["tool_name"], "okf_traverse")
                budget = supplied.get("budget")
                if budget is None:
                    self.assertNotIn("--budget", args)
                    self.assertNotIn("budget", params)
                else:
                    self.assertIn("--budget", args)
                    self.assertEqual(args[args.index("--budget") + 1], str(budget))
                    self.assertEqual(params["budget"], budget)


class TestMCPTopeReal(Base):
    """MCP stdio → wrapper → CLI real → recibo y traza, con 121 nodos."""

    N_HOJAS = 120

    def setUp(self):
        super().setUp()
        # Nada se escribe en la traza del usuario: ambos destinos son del fixture.
        self._configure()

    def _configure(self, budget=None):
        texto = (
            "types:\n  valid: [Insight]\n"
            "features:\n  cognitive_trace: true\n"
            "  trace_db_path: .trace/events.db\n"
            "  trace_jsonl_path: .trace/events.jsonl\n"
        )
        if budget is not None:
            texto += f"traverse:\n  budget: {budget}\n"
        _write(self.vault / ".okf.config.yaml", texto)

    def _call(self, **overrides):
        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ, PYTHONPATH=str(root), OKF_VAULT=str(self.vault))
        for key in ("OKF_SCOPE", "OKF_READONLY", "OKF_PYLIBS"):
            env.pop(key, None)
        arguments = {
            "slug": "insights/centro.md", "depth": 1, "direction": "in",
            "no_cyber": True, "edge_type": "extiende", "filter": True,
            "compact": True, "json_output": True, **overrides,
        }
        process = StdioServerParameters(
            command=sys.executable, args=[str(root / "server.py")],
            env=env, cwd=str(root),
        )

        async def call(errlog):
            async with stdio_client(process, errlog=errlog) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    return await session.call_tool("traverse", arguments)

        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
            result = asyncio.run(asyncio.wait_for(call(errlog), timeout=45))
            errlog.seek(0)
            self.assertFalse(result.isError, errlog.read())
        return "\n".join(block.text for block in result.content if block.type == "text")

    def _trace(self):
        events = [json.loads(line) for line in
                  (self.vault / ".trace/events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["tool"], "okf_traverse")
        with contextlib.closing(sqlite3.connect(self.vault / ".trace/events.db")) as db:
            rows = db.execute(
                "SELECT tool, exit_code, nodes_count, params FROM events"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        tool, exit_code, count, params = rows[0]
        self.assertEqual(tool, event["tool"])
        self.assertEqual(exit_code, event["exit_code"])
        self.assertEqual(json.loads(params), event["params"])
        return event, count

    def _assert_json_result(self, output, count, budget, default=False):
        data = json.loads(output)
        self.assertEqual(data["total"], count)
        paths = [node["path"] for node in data["nodes"]]
        self.assertEqual(len(paths), count)
        self.assertEqual(len(set(paths)), count)
        receipt = data["receipt"]
        self.assertEqual(receipt["nodes"], count)
        self.assertEqual(receipt["reachable"], self.N_HOJAS + 1)
        self.assertEqual(receipt["budget"], budget)
        self.assertEqual(receipt["truncated"], count < self.N_HOJAS + 1)
        if default:
            self.assertTrue(receipt["budget_default"])
        else:
            self.assertNotIn("budget_default", receipt)
        event, traced_count = self._trace()
        self.assertEqual(event["exit_code"], 0)
        self.assertEqual(traced_count, count)
        self.assertEqual(event["result_nodes"], paths)
        self.assertEqual(event["result_edges"], data["result_edges"])
        self.assertEqual(len(event["result_edges"]), count - 1)
        return event

    def test_omitido_aplica_100_en_recibo_y_traza(self):
        event = self._assert_json_result(self._call(), 100, 100, default=True)
        self.assertNotIn("budget", event["params"])

    def test_omitido_usa_el_tope_configurado_no_100(self):
        self._configure(budget=5)
        event = self._assert_json_result(self._call(), 5, 5, default=True)
        self.assertNotIn("budget", event["params"])

    def test_cero_quita_el_tope_en_recibo_y_traza(self):
        self._configure(budget=5)
        event = self._assert_json_result(self._call(budget=0), 121, None)
        self.assertEqual(event["params"]["budget"], 0)

    def test_positivo_prevalece_sobre_el_tope_configurado(self):
        self._configure(budget=5)
        event = self._assert_json_result(self._call(budget=7), 7, 7)
        self.assertEqual(event["params"]["budget"], 7)

    def test_negativo_devuelve_error_cli_sin_degradar_al_default(self):
        output = self._call(budget=-1)
        self.assertIn("Error: --budget must be >= 0", output)
        self.assertIn("[exit_code: 1]", output)
        self.assertNotIn('"receipt"', output)
        event, count = self._trace()
        self.assertEqual(event["exit_code"], 1)
        self.assertEqual(event["params"]["budget"], -1)
        self.assertIsNone(count)
        self.assertNotIn("result_nodes", event)
        self.assertNotIn("result_edges", event)

    def test_texto_y_extraccion_secundaria_de_traza_respetan_100(self):
        output = self._call(json_output=False)
        self.assertIn("✂️  21 nodo(s) más quedaron fuera por el tope del vault (100)", output)
        self.assertIn("📎 recibo: 100 nodos de 121", output)
        self.assertIn("truncado: sí (tope del vault: 100)", output)
        event, count = self._trace()
        self.assertEqual(event["exit_code"], 0)
        self.assertEqual(count, 100)
        self.assertEqual(len(event["result_nodes"]), 100)
        self.assertEqual(len(event["result_edges"]), 99)
        self.assertNotIn("budget", event["params"])


if __name__ == "__main__":
    unittest.main()
