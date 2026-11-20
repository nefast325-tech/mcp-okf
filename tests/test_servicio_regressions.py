"""Regression boundaries found during independent review of the HTTP service."""
import asyncio
import json
import os
import concurrent.futures
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import servicio

USER = {"nombre": "Ana", "correo": "ana@example.test", "permiso": "escritura"}


def git(v, *args):
    return subprocess.run(["git", "-C", str(v), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class TestKeyFailures(unittest.TestCase):
    def test_parallel_key_creation_keeps_every_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'keys.json'
            def create(i):
                return servicio.Llaves(p).crear(str(i), '', 'lectura')
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                keys = list(pool.map(create, range(8)))
            self.assertEqual(len(json.loads(p.read_text())), 8)
            for key in keys:
                self.assertIsNotNone(servicio.Llaves(p).buscar('Bearer ' + key))

    def test_temporary_key_file_is_private_from_creation(self):
        with tempfile.TemporaryDirectory() as d:
            replace = os.replace
            modes = []
            def inspect(source, target):
                modes.append(Path(source).stat().st_mode & 0o777)
                return replace(source, target)
            with patch('servicio.os.replace', side_effect=inspect):
                servicio.Llaves(Path(d) / 'keys.json').crear('Ana', '', 'lectura')
            self.assertEqual(modes, [0o600])

    def test_malformed_json_shape_is_unauthorized_not_server_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'keys.json'
            for value in ([1], 'wrong', 1, True):
                p.write_text(json.dumps(value))
                with self.subTest(value=value):
                    self.assertIsNone(servicio.Llaves(p).buscar('Bearer fake'))

    def test_create_does_not_destroy_unreadable_key_database(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'keys.json'
            p.write_text('{broken')
            before = p.read_bytes()
            with self.assertRaises(ValueError):
                servicio.Llaves(p).crear('Ana', '', 'lectura')
            self.assertEqual(p.read_bytes(), before)

    def test_revoke_same_mtime_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'keys.json'
            keys = servicio.Llaves(p)
            key = keys.crear('Ana', '', 'lectura')
            self.assertIsNotNone(keys.buscar('Bearer ' + key))
            old = p.stat()
            new = p.with_suffix('.replacement')
            new.write_text('{}')
            os.utime(new, ns=(old.st_atime_ns, old.st_mtime_ns))
            os.replace(new, p)
            self.assertIsNone(keys.buscar('Bearer ' + key))


class TestHistoryFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.v = Path(self.tmp.name)
        git(self.v, 'init', '-q')
        (self.v / 'a.md').write_text('before')
        git(self.v, 'add', 'a.md')
        git(self.v, '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'initial')

    def test_local_state_is_not_committed(self):
        (self.v / 'a.md').write_text('after')
        (self.v / '.okf').mkdir()
        (self.v / '.okf/state.sqlite').write_text('dummy runtime data')
        sha = servicio.confirmar(self.v, USER, 'edit', ['edit', str(self.v / 'a.md')])
        self.assertTrue(sha)
        paths = git(self.v, 'show', '--pretty=', '--name-only', 'HEAD').splitlines()
        self.assertEqual(paths, ['a.md'])

    def test_failed_hook_is_explicit(self):
        (self.v / 'a.md').write_text('after')
        hook = self.v / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nprintf "fixture hook rejected\\n" >&2\nexit 1\n')
        hook.chmod(0o700)
        with self.assertRaisesRegex(RuntimeError, 'fixture hook rejected'):
            servicio.confirmar(self.v, USER, 'edit', ['edit', str(self.v / 'a.md')])

    def test_dirty_tree_refuses_before_writing(self):
        (self.v / 'unrelated.md').write_text('someone else')
        called = []
        def edit(slug: str = '') -> str:
            called.append(slug)
            (self.v / 'a.md').write_text('after')
            return 'updated'
        request = SimpleNamespace(state=SimpleNamespace(okf_usuario=USER))
        mcp = SimpleNamespace(get_context=lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)))
        wrapped = servicio.envolver(edit, True, mcp, vault=self.v, autocommit=True)
        result = asyncio.run(wrapped(slug='a'))
        self.assertIn('[historial bloqueado]', result)
        self.assertEqual(called, [])
        self.assertEqual((self.v / 'a.md').read_text(), 'before')

    def test_non_git_autocommit_refuses_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                servicio.validar_historial(Path(d))

    def test_all_write_wrappers_hold_the_lock_and_version_suggestions(self):
        request = SimpleNamespace(state=SimpleNamespace(okf_usuario=USER))
        mcp = SimpleNamespace(get_context=lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)))
        for name in ('graph_command', 'graph_suggest_edge_types'):
            def write():
                self.assertTrue(servicio.ESCRITURA.locked())
                if name == 'graph_suggest_edge_types':
                    (self.v / 'a.md').write_text('suggestions')
                return 'done'
            write.__name__ = name
            output = asyncio.run(servicio.envolver(write, True, mcp, vault=self.v, autocommit=True)())
            if name == 'graph_suggest_edge_types':
                self.assertIn('commit ', output)
                self.assertEqual(git(self.v, 'log', '-1', '--format=%an'), 'Ana')

    def test_dry_run_does_not_commit_or_claim_history(self):
        def edit(dry_run: bool = False):
            return 'dry-run'
        request = SimpleNamespace(state=SimpleNamespace(okf_usuario=USER))
        mcp = SimpleNamespace(get_context=lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)))
        with patch('servicio.confirmar', side_effect=AssertionError('must not commit')):
            output = asyncio.run(servicio.envolver(edit, True, mcp, vault=self.v, autocommit=True)(dry_run=True))
        self.assertEqual(output, 'dry-run')


if __name__ == '__main__':
    unittest.main()
