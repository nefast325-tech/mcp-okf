"""Modo público del MCP okf: scope de subárbol + solo-lectura.

Contrato (2026-09-11): un agente PÚBLICO (perfil `atencion` del servicio al
cliente de la cartera) lee una carpeta publicada del vault y no puede ver,
enumerar, buscar ni traversar nada fuera de ella; tampoco puede escribir.

Los tests invocan el CLI como subproceso (`python3 -m cli …`) porque el scope
es estado de proceso: cada caso arranca limpio y se prueba exactamente la
interfaz que usa el server MCP (que solo propaga el prefijo vía `--scope`).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import access

REPO = Path(__file__).resolve().parent.parent


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class BaseVaultTest(unittest.TestCase):
    """Vault de prueba: una carpeta publicada + dos carpetas internas."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self._build_vault()

    def tearDown(self):
        self._tmp.cleanup()

    def _build_vault(self):
        _write(
            self.vault / ".okf.config.yaml",
            "types:\n  valid: [Decision, Project]\n"
            "features:\n  cognitive_trace: false\n",
        )
        # ── Carpeta publicada (lo único que un agente público puede ver) ──
        _write(
            self.vault / "publico" / "index.md",
            '---\ndescription: "Cartera de proyectos — información publicada"\n---\n\n'
            "# Publico\n\n* [doral-country.md](doral-country.md)\n"
            "* [doral-suite.md](doral-suite.md)\n",
        )
        _write(
            self.vault / "publico" / "doral-country.md",
            '---\ntype: Project\ntitle: "Doral Country"\n'
            'description: "Apartamentos en torres en la Zona Norte, desde $259.500.000"\n'
            "tags: [doral, apartamentos]\n---\n\n"
            "Ficha pública de Doral Country. Ver [[doral-suite]] y "
            "[[decisions/precio-secreto]].\n",
        )
        _write(
            self.vault / "publico" / "doral-suite.md",
            '---\ntype: Project\ntitle: "Doral Suite"\n'
            'description: "Apartamentos suite en la Zona Norte, inventario final"\n'
            "tags: [doral, suite]\n---\n\nFicha pública de Doral Suite.\n",
        )
        # ── Contenido interno: NUNCA visible para el agente público ──
        _write(
            self.vault / "decisions" / "precio-secreto.md",
            '---\ntype: Decision\ntitle: "Estrategia de precio y margen"\n'
            'description: "Margen confidencial de la cartera — no publicar"\n'
            "tags: [precio, interno, margen]\n---\n\n"
            "Margen interno del 18 %.\n",
        )
        _write(
            self.vault / "insights" / "secreto.md",
            '---\ntype: Decision\ntitle: "Insight interno de la cartera"\n'
            'description: "Observación interna sobre margen y descuentos"\n'
            "tags: [interno]\n---\n\nNada publicable.\n",
        )

    def cli(self, *args, scope=None, env_extra=None):
        """Ejecuta el CLI como subproceso y devuelve el CompletedProcess."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        env["OKF_VAULT"] = str(self.vault)
        env.pop("OKF_SCOPE", None)
        env.pop("OKF_READONLY", None)
        env.update(env_extra or {})
        cmd = [sys.executable, "-m", "cli", "--vault", str(self.vault)]
        if scope is not None:
            cmd += ["--scope", scope]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO))


class ScopeAcotaLaLecturaTest(BaseVaultTest):
    def test_scope_acota_la_busqueda_a_la_carpeta_publicada(self):
        scoped = self.cli("search", "--json", scope="publico")
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        self.assertIn("publico/doral-country.md", scoped.stdout)
        self.assertNotIn("decisions/precio-secreto", scoped.stdout)
        self.assertNotIn("insights/secreto", scoped.stdout)

        completo = self.cli("search", "--json")
        self.assertIn("decisions/precio-secreto.md", completo.stdout)

    def test_read_de_un_slug_de_fuera_del_scope_no_resuelve(self):
        # Por ruta relativa completa
        por_ruta = self.cli("read", "decisions/precio-secreto", scope="publico")
        self.assertEqual(por_ruta.returncode, 1)
        self.assertNotIn("Margen interno", por_ruta.stdout)
        # Y por nombre de archivo (el atajo que usa un agente real)
        por_nombre = self.cli("read", "precio-secreto", scope="publico")
        self.assertEqual(por_nombre.returncode, 1)
        self.assertNotIn("Margen interno", por_nombre.stdout)

    def test_read_de_un_slug_relativo_resuelve_dentro_del_scope(self):
        result = self.cli("read", "doral-country", scope="publico")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ficha pública de Doral Country", result.stdout)

    def test_read_del_index_resuelve_al_index_del_scope(self):
        # El punto de entrada del agente público: read("index.md") → publico/index.md
        result = self.cli("read", "index.md", scope="publico")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cartera de proyectos — información publicada", result.stdout)

    def test_search_no_encuentra_contenido_interno(self):
        result = self.cli("search", "--query", "margen", scope="publico")
        self.assertEqual(result.returncode, 0)
        self.assertIn("(no results)", result.stdout)
        self.assertNotIn("precio-secreto", result.stdout)

    def test_traverse_no_filtra_vecinos_de_fuera_del_scope(self):
        result = self.cli("traverse", "publico/doral-country",
                          "--edge-type", "depende", scope="publico")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("doral-suite", result.stdout)
        # El wikilink de la ficha pública hacia una decisión interna no se resuelve
        self.assertNotIn("precio-secreto", result.stdout)
        self.assertNotIn("Estrategia de precio", result.stdout)

    def test_traverse_no_arranca_desde_un_nodo_de_fuera(self):
        result = self.cli("traverse", "decisions/precio-secreto",
                          "--edge-type", "depende", scope="publico")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Margen interno", result.stdout)

    def test_un_scope_invalido_aborta_en_vez_de_degradar(self):
        result = self.cli("read", "doral-country", scope="..")
        self.assertEqual(result.returncode, 1)
        self.assertIn("intenta salir del vault", result.stderr)

    def test_instancia_sin_scope_sigue_viendo_el_vault_completo(self):
        result = self.cli("read", "precio-secreto")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Margen interno", result.stdout)


class ModoSoloLecturaTest(BaseVaultTest):
    def test_new_bloqueado_y_no_crea_archivo(self):
        result = self.cli(
            "new", "--type", "Project", "--title", "Colado",
            "--description", "No debería crearse",
            env_extra={"OKF_READONLY": "true"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("solo-lectura", result.stderr)
        self.assertFalse(list(self.vault.rglob("colado*")))

    def test_edit_bloqueado(self):
        result = self.cli(
            "edit", "publico/doral-country", "--description", "cambiada",
            env_extra={"OKF_READONLY": "true"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("solo-lectura", result.stderr)
        self.assertIn("Apartamentos en torres",
                      (self.vault / "publico" / "doral-country.md").read_text())

    def test_index_bloqueado(self):
        result = self.cli("index", env_extra={"OKF_READONLY": "true"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("solo-lectura", result.stderr)

    def test_lectura_sigue_funcionando_en_solo_lectura(self):
        result = self.cli("read", "doral-country", scope="publico",
                          env_extra={"OKF_READONLY": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ficha pública", result.stdout)


class ServidorModoPublicoTest(BaseVaultTest):
    """El server propaga el scope al CLI y no registra las tools de escritura."""

    def _server_info(self, extra_env=None, extra_args=(), extra_script=""):
        """Importa server.py en un subproceso y devuelve CLI + tools registradas."""
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{REPO}:{os.environ.get('PYTHONPATH', '')}"
        env["OKF_VAULT"] = str(self.vault)
        env.pop("OKF_SCOPE", None)
        env.pop("OKF_READONLY", None)
        env.update(extra_env or {})
        code = (
            "import json, server\n"
            "print(json.dumps({\n"
            "  'cli': server.CLI,\n"
            "  'tools': sorted(t.name for t in server.mcp._tool_manager.list_tools()),\n"
            "  'scope': server._SCOPE,\n"
            "  'readonly': server._READONLY,\n"
            "}))\n" + extra_script
        )
        argv = [sys.executable, "-c", code]
        argv += list(extra_args)
        result = subprocess.run(argv, capture_output=True,
                                text=True, env=env, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_sin_flags_registra_todas_las_tools(self):
        info = self._server_info()
        self.assertNotIn("--scope", info["cli"])
        for tool in ("read", "search", "traverse", "new", "edit", "index"):
            self.assertIn(tool, info["tools"])

    def test_scope_se_propaga_al_cli(self):
        info = self._server_info({"OKF_SCOPE": "publico"})
        self.assertIn("--scope", info["cli"])
        self.assertEqual(info["cli"][info["cli"].index("--scope") + 1], "publico")

    def test_readonly_no_registra_tools_de_escritura(self):
        info = self._server_info({"OKF_SCOPE": "publico", "OKF_READONLY": "true"})
        for tool in ("read", "search", "traverse", "todos", "graph", "file_info"):
            self.assertIn(tool, info["tools"])
        for tool in ("new", "edit", "index", "touch", "canvas", "graph_command",
                     "analytics", "trace", "graph_suggest_edge_types"):
            self.assertNotIn(tool, info["tools"])

    # ── Política por ARGUMENTO (lo que se pinea en el config del perfil) ──
    # El alcance declarado por argv no depende de que el entorno se propague al
    # subproceso MCP: si el env se perdiera, la instancia quedaría SIN alcance
    # (fail-open) y el agente público vería el vault entero.

    def test_scope_por_argumento_se_propaga_al_cli(self):
        info = self._server_info(extra_args=["--scope", "publico"])
        self.assertEqual(info["scope"], "publico")
        self.assertEqual(info["cli"][info["cli"].index("--scope") + 1], "publico")

    def test_scope_por_argumento_acepta_la_forma_con_igual(self):
        info = self._server_info(extra_args=["--scope=publico"])
        self.assertEqual(info["scope"], "publico")
        self.assertIn("publico", info["cli"])

    def test_readonly_por_argumento_no_registra_tools_de_escritura(self):
        info = self._server_info(extra_args=["--scope", "publico", "--readonly"])
        self.assertTrue(info["readonly"])
        self.assertIn("read", info["tools"])
        for tool in ("new", "edit", "index", "touch", "canvas", "graph_command",
                     "analytics", "trace", "graph_suggest_edge_types"):
            self.assertNotIn(tool, info["tools"])

    def test_el_argumento_gana_sobre_el_entorno(self):
        info = self._server_info(extra_env={"OKF_SCOPE": "otro"},
                                 extra_args=["--scope", "publico"])
        self.assertEqual(info["scope"], "publico")
        self.assertNotIn("otro", info["cli"])


class AccessModuleTest(unittest.TestCase):
    """Unitarios de la política de acceso."""

    def tearDown(self):
        access.set_scope(None)

    def test_normalize_scope(self):
        self.assertEqual(access.normalize_scope("publico/"), "publico")
        self.assertEqual(access.normalize_scope("/a/b/"), "a/b")
        self.assertEqual(access.normalize_scope("a//b"), "a/b")
        self.assertEqual(access.normalize_scope("./publico"), "publico")
        self.assertIsNone(access.normalize_scope(""))
        self.assertIsNone(access.normalize_scope("/"))
        with self.assertRaises(ValueError):
            access.normalize_scope("../etc")
        with self.assertRaises(ValueError):
            access.normalize_scope("publico/../../etc")

    def test_under_scope(self):
        access.set_scope(None)
        self.assertTrue(access.under_scope("decisions/x.md"))
        access.set_scope("publico")
        self.assertTrue(access.under_scope("publico/doral-country.md"))
        self.assertTrue(access.under_scope(["publico", "doral-country.md"]))
        self.assertTrue(access.under_scope("publico"))
        self.assertFalse(access.under_scope("publico2/x.md"))
        self.assertFalse(access.under_scope("decisions/x.md"))
        self.assertFalse(access.under_scope("../fuera.md"))

    def test_is_readonly_default_off(self):
        self.assertFalse(access.is_readonly(env={}))
        for valor in ("1", "true", "TRUE", "yes", "on", "sí"):
            self.assertTrue(access.is_readonly(env={"OKF_READONLY": valor}))
        self.assertFalse(access.is_readonly(env={"OKF_READONLY": "false"}))

    def test_resolve_scope_precedencia_cli_sobre_env(self):
        env = {"OKF_SCOPE": "publico"}
        self.assertEqual(access.resolve_scope("otro", env=env), "otro")
        self.assertEqual(access.resolve_scope(None, env=env), "publico")
        self.assertIsNone(access.resolve_scope(None, env={}))

    def test_scope_root(self):
        vault = Path("/tmp/vault")
        self.assertEqual(access.scope_root(vault, "publico"), vault / "publico")
        self.assertEqual(access.scope_root(vault, None), vault)

    def test_write_commands_no_incluye_touch(self):
        # `touch` solo lee estadísticas (el incremento manual está deprecado):
        # no es un comando de escritura.
        self.assertNotIn("touch", access.WRITE_COMMANDS)
        for cmd in ("new", "edit", "index", "canvas", "migrate-reads"):
            self.assertIn(cmd, access.WRITE_COMMANDS)


class InScopeFileTest(unittest.TestCase):
    def tearDown(self):
        access.set_scope(None)

    def test_in_scope_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _write(vault / "publico" / "a.md", "x")
            _write(vault / "decisions" / "b.md", "x")
            access.set_scope(None)
            self.assertTrue(access.in_scope_file(vault / "decisions" / "b.md", vault))
            access.set_scope("publico")
            self.assertTrue(access.in_scope_file(vault / "publico" / "a.md", vault))
            self.assertFalse(access.in_scope_file(vault / "decisions" / "b.md", vault))
            # Un path fuera del vault nunca está en scope
            self.assertFalse(access.in_scope_file(Path("/etc/hosts"), vault))


class ReadFindFileTest(BaseVaultTest):
    """Resolución de slugs de `read` con y sin scope (in-process)."""

    def tearDown(self):
        access.set_scope(None)

    def test_find_file_no_devuelve_archivos_de_fuera(self):
        from cli.commands import read as read_cmd

        access.set_scope(None)
        self.assertEqual(read_cmd._find_file("insights/secreto", self.vault),
                         self.vault / "insights" / "secreto.md")

        access.set_scope("publico")
        self.assertIsNone(read_cmd._find_file("secreto", self.vault))
        self.assertEqual(read_cmd._find_file("doral-suite", self.vault),
                         self.vault / "publico" / "doral-suite.md")
        # El path que `read` imprime (relativo al vault) se puede volver a pasar
        self.assertEqual(read_cmd._find_file("publico/doral-suite", self.vault),
                         self.vault / "publico" / "doral-suite.md")
        self.assertEqual(read_cmd._find_file("publico/doral-suite.md", self.vault),
                         self.vault / "publico" / "doral-suite.md")
        self.assertIsNone(read_cmd._find_file("decisions/precio-secreto", self.vault))
        self.assertIsNone(read_cmd._find_file("no-existe-jamas", self.vault))


if __name__ == "__main__":
    unittest.main()
