"""Modo servicio (servicio.py): MCP por HTTP con una llave por persona y commit automático.

Para que personas que no usan git, y agentes en otras máquinas, lean y escriban
el vault al instante sin clon ni commits. Medido el 20-nov-2026 con tres
usuarios sobre HTTP (~/okf-lab/servicio/probar.py): 401 sin llave, la llave de
lectura no escribe, lo que escribe una persona lo lee otra al instante, una
relación inventada se rechaza en el momento, 20 ediciones simultáneas sobre la
misma nota sin perder ninguna y 21 commits con su autor.
"""

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import servicio  # noqa: E402


class TestLlaves(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "llaves.json"
        self.llaves = servicio.Llaves(self.ruta)

    def tearDown(self):
        self.tmp.cleanup()

    def test_crear_guarda_solo_la_huella_y_en_modo_600(self):
        llave = self.llaves.crear("Ana", "ana@x.test", "escritura")
        self.assertTrue(llave.startswith("okf_"))
        texto = self.ruta.read_text(encoding="utf-8")
        self.assertNotIn(llave, texto)
        self.assertIn("Ana", texto)
        self.assertEqual(stat.S_IMODE(self.ruta.stat().st_mode), 0o600)

    def test_buscar(self):
        llave = self.llaves.crear("Ana", "ana@x.test", "lectura")
        self.assertEqual(self.llaves.buscar(f"Bearer {llave}"),
                         {"nombre": "Ana", "correo": "ana@x.test", "permiso": "lectura"})
        for malo in ("", llave, "Bearer ", "Bearer okf_falsa", f"Basic {llave}"):
            with self.subTest(malo=malo[:12]):
                self.assertIsNone(self.llaves.buscar(malo))

    def test_permiso_invalido(self):
        with self.assertRaises(ValueError):
            self.llaves.crear("Ana", "", "admin")

    def test_una_llave_nueva_sirve_sin_reiniciar(self):
        otra = servicio.Llaves(self.ruta)
        self.assertIsNone(otra.buscar("Bearer okf_x"))
        llave = self.llaves.crear("Beto", "", "escritura")
        os.utime(self.ruta, ns=(1, 2))  # mtime distinto aunque el reloj sea grueso
        self.assertEqual(otra.buscar(f"Bearer {llave}")["nombre"], "Beto")


class TestGuardia(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.llaves = servicio.Llaves(Path(self.tmp.name) / "llaves.json")
        self.llave = self.llaves.crear("Ana", "ana@x.test", "escritura")
        self.vistos = []

        async def app(scope, receive, send):
            self.vistos.append(scope.get("state", {}).get("okf_usuario"))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        self.app = servicio.Guardia(app, self.llaves)

    def tearDown(self):
        self.tmp.cleanup()

    def _get(self, headers):
        async def pedir():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
                return await c.get("/mcp", headers=headers)
        return asyncio.run(pedir())

    def test_sin_llave_401_y_no_llega_al_mcp(self):
        for headers in ({}, {"Authorization": "Bearer okf_falsa"}):
            r = self._get(headers)
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.headers["www-authenticate"], "Bearer")
        self.assertEqual(self.vistos, [])

    def test_con_llave_pasa_y_lleva_el_usuario(self):
        r = self._get({"Authorization": f"Bearer {self.llave}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.vistos, [{"nombre": "Ana", "correo": "ana@x.test", "permiso": "escritura"}])


class TestEnvoltorio(unittest.TestCase):
    def _mcp(self, usuario):
        pedido = SimpleNamespace(state=SimpleNamespace(okf_usuario=usuario))
        ctx = SimpleNamespace(request_context=SimpleNamespace(request=pedido))
        return SimpleNamespace(get_context=lambda: ctx)

    def test_lectura_no_escribe_y_escritura_si(self):
        def new(title: str = "") -> str:
            return f"creada {title} por {servicio.USUARIO.get()['nombre']}"
        lectora = servicio.envolver(new, True, self._mcp({"nombre": "Cora", "correo": "", "permiso": "lectura"}))
        escritora = servicio.envolver(new, True, self._mcp({"nombre": "Ana", "correo": "", "permiso": "escritura"}))
        self.assertTrue(asyncio.run(lectora(title="x")).startswith("[sin permiso]"))
        self.assertEqual(asyncio.run(escritora(title="x")), "creada x por Ana")

    def test_conserva_la_firma_para_el_esquema_del_mcp(self):
        import inspect
        def edit(slug: str, base_revision: str = "") -> str:
            return slug
        envuelta = servicio.envolver(edit, True, self._mcp(None))
        self.assertEqual(list(inspect.signature(envuelta).parameters), ["slug", "base_revision"])
        self.assertTrue(inspect.iscoroutinefunction(envuelta))
        self.assertTrue(asyncio.run(envuelta(slug="a")).startswith("[sin llave]"))


class TestConfirmar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.v)], check=True)
        (self.v / "a.md").write_text("uno\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.v), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.v), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "0"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_commit_con_el_autor_y_la_nota_relativa(self):
        (self.v / "a.md").write_text("dos\n", encoding="utf-8")
        sha = servicio.confirmar(self.v, {"nombre": "Ana", "correo": "ana@x.test"}, "okf_edit",
                                 ["edit", str(self.v / "a.md"), "--body", "dos"])
        self.assertTrue(sha)
        log = subprocess.run(["git", "-C", str(self.v), "log", "-1", "--format=%an <%ae>|%s"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(log, "Ana <ana@x.test>|edit: a (por Ana, servicio OKF)")

    def test_sin_cambios_no_hay_commit(self):
        self.assertEqual(servicio.confirmar(self.v, {"nombre": "Ana", "correo": ""}, "okf_edit", ["edit", "a"]), "")

    def test_sin_git_no_hace_nada(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.md").write_text("x", encoding="utf-8")
            self.assertEqual(servicio.confirmar(Path(d), None, "okf_new", ["new"]), "")


class TestArranque(unittest.TestCase):
    def test_http_sin_llaves_no_arranca(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".okf.config.yaml").write_text("types:\n  valid: [Insight]\n", encoding="utf-8")
            r = subprocess.run([sys.executable, str(REPO / "server.py"), "--vault", d, "--http", "127.0.0.1:0"],
                               capture_output=True, text=True, timeout=60,
                               env={**os.environ, "OKF_LLAVES": "", "PYTHONPATH": str(REPO)})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--llaves", r.stderr)


if __name__ == "__main__":
    unittest.main()
