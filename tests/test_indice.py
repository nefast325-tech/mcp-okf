"""Índice local de notas (cli/indice.py).

Medido el 19-nov-2026 con 10.131 notas: search y traverse releían y
parseaban todas las notas en cada llamada (4-13 s). El índice guarda lo
leído en .okf/state/indice.sqlite y solo relee lo que cambió. Estas pruebas
fijan que nunca devuelva algo distinto de lo que hay en disco.
"""

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cli import indice
from cli.access import under_scope
from cli.vault import EXCLUDE_DIRS, EXCLUDE_FILES, find_md_files


def _nota(vault, rel, titulo, cuerpo="Cuerpo.", extra=""):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'---\ntype: Insight\ntitle: "{titulo}"\ndescription: "D."\n{extra}---\n\n{cuerpo}\n',
                 encoding="utf-8")
    return p


def _tocar(p, texto):
    """Reescribe y asegura que cambie el mtime aunque el reloj del disco sea grueso."""
    antes = p.stat().st_mtime_ns
    p.write_text(texto, encoding="utf-8")
    if p.stat().st_mtime_ns == antes:
        os.utime(p, ns=(antes + 1_000_000, antes + 1_000_000))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        indice._MEMO.clear()

    def tearDown(self):
        indice._MEMO.clear()
        self.tmp.cleanup()

    def notas(self, **kw):
        # Cada llamada simula un proceso nuevo: sin memo, solo la base.
        indice._MEMO.clear()
        return indice.notas(self.vault, **kw)


class TestReuso(Base):
    def test_la_segunda_llamada_no_relee_y_devuelve_lo_mismo(self):
        _nota(self.vault, "a.md", "Uno", "Ver [[b]].")
        _nota(self.vault, "b.md", "Dos")
        primera = self.notas()
        self.assertTrue((self.vault / indice.RUTA).exists())
        with mock.patch.object(indice, "leer", wraps=indice.leer) as leer:
            segunda = self.notas()
        self.assertEqual(leer.call_count, 0)
        self.assertEqual(primera, segunda)
        self.assertEqual(segunda["a.md"]["enlaces"], ["b"])
        self.assertEqual(segunda["b.md"]["fm"]["title"], "Dos")

    def test_relee_solo_lo_que_cambio(self):
        a = _nota(self.vault, "a.md", "Uno")
        _nota(self.vault, "b.md", "Dos")
        self.notas()
        _tocar(a, '---\ntype: Insight\ntitle: "Uno cambiado"\ndescription: "D."\n---\n\nNuevo.\n')
        with mock.patch.object(indice, "leer", wraps=indice.leer) as leer:
            datos = self.notas()
        self.assertEqual(leer.call_count, 1)
        self.assertEqual(datos["a.md"]["fm"]["title"], "Uno cambiado")
        self.assertIn("Nuevo.", datos["a.md"]["cuerpo"])

    def test_un_archivo_borrado_desaparece(self):
        _nota(self.vault, "a.md", "Uno")
        b = _nota(self.vault, "b.md", "Dos")
        self.notas()
        b.unlink()
        self.assertEqual(set(self.notas()), {"a.md"})
        con = sqlite3.connect(str(self.vault / indice.RUTA))
        self.assertEqual([r for (r,) in con.execute("SELECT ruta FROM notas")], ["a.md"])
        con.close()

    def test_en_el_mismo_proceso_ve_lo_que_otro_escribio(self):
        a = _nota(self.vault, "a.md", "Uno")
        indice.notas(self.vault)
        _tocar(a, '---\ntype: Insight\ntitle: "Otro"\ndescription: "D."\n---\n\nX.\n')
        self.assertEqual(indice.notas(self.vault)["a.md"]["fm"]["title"], "Otro")
        self.assertEqual(indice.frontmatter(self.vault, "a.md")["title"], "Otro")


class TestFidelidad(Base):
    def test_fechas_y_tipos_vuelven_identicos(self):
        extra = ("timestamp: 2026-11-19T10:00:00-05:00\ncreated: 2026-11-19\n"
                 "reads: 3\nleaf: true\ntags: [a, b]\ncyber:\n  review_on: 2026-12-01\n")
        _nota(self.vault, "a.md", "Uno", extra=extra)
        directo = indice.leer(self.vault / "a.md")["fm"]
        self.notas()
        guardado = self.notas()["a.md"]["fm"]
        self.assertEqual(guardado, directo)
        self.assertEqual(guardado["timestamp"],
                         datetime(2026, 11, 19, 10, 0, tzinfo=timezone(timedelta(hours=-5))))
        self.assertIsInstance(guardado["created"], date)
        self.assertEqual(guardado["cyber"]["review_on"], date(2026, 12, 1))

    def test_lo_que_no_vuelve_identico_se_relee_del_archivo(self):
        # Una clave numérica: JSON la volvería texto. Esa nota no se guarda parseada.
        _nota(self.vault, "a.md", "Uno", extra="mapa:\n  1: uno\n")
        self.notas()
        self.assertEqual(self.notas()["a.md"]["fm"]["mapa"], {1: "uno"})

    def test_nota_sin_frontmatter_da_enlaces_y_fm_vacio(self):
        (self.vault / "suelta.md").write_text("Solo texto con [[otra]].\n", encoding="utf-8")
        self.notas()
        datos = self.notas()["suelta.md"]
        self.assertIsNone(datos["fm"])
        self.assertEqual(datos["enlaces"], ["otra"])


class TestSinIndice(Base):
    def test_okf_indice_0_no_crea_nada_y_da_lo_mismo(self):
        _nota(self.vault, "a.md", "Uno")
        with mock.patch.dict(os.environ, {"OKF_INDICE": "0"}):
            datos = self.notas()
        self.assertFalse((self.vault / indice.RUTA).exists())
        self.assertEqual(datos["a.md"]["fm"]["title"], "Uno")

    def test_base_danada_no_rompe_la_lectura(self):
        _nota(self.vault, "a.md", "Uno")
        ruta = self.vault / indice.RUTA
        ruta.parent.mkdir(parents=True)
        ruta.write_bytes(b"esto no es una base sqlite" * 100)
        self.assertEqual(self.notas()["a.md"]["fm"]["title"], "Uno")

    def test_carpeta_de_estado_sin_permiso_de_escritura(self):
        _nota(self.vault, "a.md", "Uno")
        estado = self.vault / ".okf" / "state"
        estado.mkdir(parents=True)
        estado.chmod(0o500)
        try:
            self.assertEqual(self.notas()["a.md"]["fm"]["title"], "Uno")
        finally:
            estado.chmod(0o700)


class TestListado(unittest.TestCase):
    """find_md_files con os.walk da lo mismo y en el mismo orden que el rglob de antes."""

    def test_mismo_resultado_que_rglob(self):
        with tempfile.TemporaryDirectory() as d:
            v = Path(d)
            for rel in ("a-c.md", "a/b.md", "a/z.md", "B.md", "b.md", ".oculta/x.md", ".git/y.md",
                        "Templates/t.md", "x/index.md", "x/sub/n.md", "x/sub/n.txt", "x.md/dentro.md"):
                p = v / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("---\ntype: Insight\n---\n", encoding="utf-8")
            viejo = []
            for md in sorted(v.rglob("*.md")):
                if md.name in EXCLUDE_FILES or md.is_dir():
                    continue
                parts = md.relative_to(v).parts
                if any(p in EXCLUDE_DIRS for p in parts) or not under_scope(parts):
                    continue
                viejo.append(md)
            self.assertEqual(find_md_files(v), viejo)


if __name__ == "__main__":
    unittest.main()
