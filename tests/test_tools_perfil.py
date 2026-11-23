"""Perfil de herramientas: `--tools`, `OKF_TOOLS` o `mcp.tools` dejan registradas solo las pedidas.

Cada herramienta registrada pesa en el contexto del agente en cada turno. Sin
perfil el server registra todas, como siempre. `consulta` deja las cinco de
consultar (read, search, traverse, graph, todos, changes); se pueden sumar herramientas
sueltas y quitar con «-nombre». El argumento gana al entorno y este a la
configuración del vault. Un nombre mal escrito aborta al arrancar: esconder una
herramienta en silencio sería peor que no arrancar.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONSULTA = ["changes", "graph", "read", "search", "todos", "traverse"]


class ToolsPerfilTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name).resolve() / "vault"
        self.vault.mkdir(parents=True)
        self._config("")

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self, extra):
        (self.vault / ".okf.config.yaml").write_text(
            "types:\n  valid: [Decision]\nfeatures:\n  cognitive_trace: false\n" + extra, encoding="utf-8")

    def _server(self, env=None, args=(), code=None):
        entorno = dict(os.environ, PYTHONPATH=f"{REPO}:{os.environ.get('PYTHONPATH', '')}",
                       OKF_VAULT=str(self.vault))
        for clave in ("OKF_TOOLS", "OKF_READONLY", "OKF_SCOPE"):
            entorno.pop(clave, None)
        entorno.update(env or {})
        code = code or ("import json, server\n"
                        "print(json.dumps(sorted(t.name for t in server.mcp._tool_manager.list_tools())))\n")
        return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True,
                              env=entorno, cwd=str(REPO))

    def _tools(self, env=None, args=()):
        r = self._server(env, args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_sin_perfil_registra_todas(self):
        tools = self._tools()
        for nombre in CONSULTA + ["new", "edit", "health", "canvas"]:
            self.assertIn(nombre, tools)

    def test_perfil_consulta_por_entorno(self):
        self.assertEqual(self._tools({"OKF_TOOLS": "consulta"}), CONSULTA)

    def test_perfil_mas_una_herramienta_por_argumento(self):
        self.assertEqual(self._tools(args=["--tools", "consulta,health"]), sorted(CONSULTA + ["health"]))

    def test_quitar_del_conjunto_completo(self):
        tools = self._tools({"OKF_TOOLS": "-canvas,-graph_command"})
        self.assertNotIn("canvas", tools)
        self.assertNotIn("graph_command", tools)
        self.assertIn("new", tools)

    def test_perfil_en_la_configuracion_del_vault(self):
        self._config("mcp:\n  tools: [consulta, stale]\n")
        self.assertEqual(self._tools(), sorted(CONSULTA + ["stale"]))

    def test_argumento_gana_a_entorno_y_entorno_a_configuracion(self):
        self._config("mcp:\n  tools: consulta\n")
        self.assertEqual(self._tools({"OKF_TOOLS": "read"}), ["read"])
        self.assertEqual(self._tools({"OKF_TOOLS": "read"}, args=["--tools=read,search"]), ["read", "search"])

    def test_solo_lectura_sigue_mandando_dentro_del_perfil(self):
        tools = self._tools({"OKF_TOOLS": "consulta,edit", "OKF_READONLY": "true"})
        self.assertEqual(tools, CONSULTA)

    def test_nombre_desconocido_aborta_al_arrancar(self):
        r = self._server({"OKF_TOOLS": "consutla"}, code="import server\nserver._main()\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("consutla", r.stderr)
        self.assertIn("consulta", r.stderr)


if __name__ == "__main__":
    unittest.main()
