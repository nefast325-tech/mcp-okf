"""The disposable index must never prevent reading authoritative Markdown."""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from cli import indice
from cli.commands.graph import build_graph
from cli.commands.search import find_concepts


class IndexRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        indice._MEMO.clear()
        self.addCleanup(indice._MEMO.clear)

    def note(self, extra=""):
        path = self.vault / "a.md"
        path.write_text("---\ntype: Insight\ntitle: Alpha\ndescription: D\n"
                        + extra + "---\nBody [[b]].\n", encoding="utf-8")
        return path

    def test_reserved_markers_and_unusual_yaml_types_are_lossless(self):
        extras = [
            'extra: {"$fecha": "texto"}\n',
            'extra: {"$momento": "texto"}\n',
            'extra: {"$fecha": "2026-11-19"}\n',
            'extra: {"$momento": "2026-11-19T10:00:00"}\n',
            'extra: {"$fecha": [1, 2]}\n',
            'extra: {"$momento": null}\n',
            'extra: !!set {a: null, b: null}\n',
            'extra: !!binary YWJj\n',
            'extra: {1: uno}\n',
        ]
        for active in ("1", "0"):
            for extra in extras:
                with self.subTest(active=active, extra=extra), mock.patch.dict(os.environ, {"OKF_INDICE": active}):
                    path = self.note(extra)
                    expected = indice.leer(path)
                    for _ in range(2):
                        indice._MEMO.clear()
                        self.assertTrue(build_graph(self.vault))
                        self.assertEqual(len(find_concepts(self.vault)), 1)
                        self.assertEqual(indice.notas(self.vault)["a.md"], expected)

    def test_unreadable_note_is_skipped_repeatedly_then_recovers_without_stat_change(self):
        self.note()
        original = Path.read_text

        def unreadable(path, *args, **kwargs):
            if path == self.vault / "a.md":
                raise PermissionError("temporarily unreadable")
            return original(path, *args, **kwargs)

        for graph_first in (False, True):
            with self.subTest(graph_first=graph_first):
                indice._MEMO.clear()
                (self.vault / indice.RUTA).unlink(missing_ok=True)
                with mock.patch.object(Path, "read_text", unreadable):
                    if graph_first:
                        self.assertEqual(build_graph(self.vault)["a.md"]["out"], [])
                    self.assertEqual(find_concepts(self.vault), [])
                    self.assertEqual(find_concepts(self.vault), [])
                self.assertEqual(len(find_concepts(self.vault)), 1)


    def test_corrupt_rows_fall_back_and_are_repaired(self):
        path = self.note()
        expected = indice.leer(path)
        corruptions = [
            ("fm", "{"), ("enlaces", "{"),
            ("fm", '{"extra":{"$fecha":"invalid"}}'),
            ("fm", '{"extra":{"$momento":[]}}'),
            ("fm", "[]"), ("enlaces", "null"), ("enlaces", '[42]'),
            ("cuerpo", None), ("cuerpo_inicio", "invalid"),
        ]
        for column, value in corruptions:
            with self.subTest(column=column, value=value):
                (self.vault / indice.RUTA).unlink(missing_ok=True)
                indice._MEMO.clear()
                indice.notas(self.vault)
                with closing(sqlite3.connect(self.vault / indice.RUTA)) as con, con:
                    con.execute(f"UPDATE notas SET {column}=?", (value,))
                indice._MEMO.clear()
                self.assertEqual(indice.notas(self.vault)["a.md"], expected)
                # A second process should use the repaired row, not reread.
                indice._MEMO.clear()
                with mock.patch.object(indice, "leer", wraps=indice.leer) as read:
                    self.assertEqual(indice.notas(self.vault)["a.md"], expected)
                read.assert_not_called()
                with closing(sqlite3.connect(self.vault / indice.RUTA)) as con:
                    fm, links = con.execute("SELECT fm, enlaces FROM notas").fetchone()
                self.assertEqual(json.loads(fm)["title"], "Alpha")
                self.assertEqual(json.loads(links), ["b"])

    def test_connection_closes_when_opening_fails(self):
        con = mock.Mock()
        con.execute.side_effect = sqlite3.OperationalError("broken initialization")
        with mock.patch.object(indice.sqlite3, "connect", return_value=con):
            with self.assertRaises(sqlite3.Error):
                indice._abrir(self.vault)
        con.close.assert_called_once()

    def test_connection_closes_even_if_source_reader_raises(self):
        self.note()
        real = indice._abrir(self.vault)
        con = mock.Mock(wraps=real)
        with mock.patch.object(indice, "_abrir", return_value=con), mock.patch.object(
                indice, "leer", side_effect=RuntimeError("unexpected reader failure")):
            with self.assertRaises(RuntimeError):
                indice.notas(self.vault)
        con.close.assert_called_once()
        with self.assertRaises(sqlite3.ProgrammingError):
            real.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
