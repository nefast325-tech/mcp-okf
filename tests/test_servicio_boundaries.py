"""Security regressions at the CLI boundary, not just the HTTP wrapper."""
import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cli.commands.graph import _cmd_suggest_edge_types, build_graph
from cli.reads_store import get_reads, increment_reads, migrate_frontmatter_reads

REPO = Path(__file__).resolve().parent.parent
DEFS = {'refina': {'description': 'fixture', 'valid_pairs': [('Decision', 'Decision')]}}


class TestReadOnlyBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        (self.vault / '.okf.config.yaml').write_text('types:\n  valid: [Decision]\nfeatures:\n  cognitive_trace: false\n')
        directory = self.vault / 'decisions'
        directory.mkdir()
        self.source = directory / 'a.md'
        self.source.write_text('---\ntype: Decision\ntitle: A\ndescription: fixture\nreads: 9\n---\n[[decisions/b.md]]\n')
        (directory / 'b.md').write_text('---\ntype: Decision\ntitle: B\ndescription: fixture\n---\nNo links\n')

    def cli(self, *args):
        readonly = '--readonly' in args
        args = tuple(arg for arg in args if arg != '--readonly')
        return subprocess.run([sys.executable, '-m', 'cli', '--vault', str(self.vault), *args],
                              capture_output=True, text=True, timeout=30,
                              env={**os.environ, 'OKF_READONLY': '1' if readonly else '', 'PYTHONPATH': str(REPO)})

    def test_readonly_cli_cannot_apply_graph_suggestions(self):
        before = self.source.read_bytes()
        result = self.cli('--readonly', 'graph', 'suggest-edge-types', '--apply', '--min-score', '0.01')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.source.read_bytes(), before)

    def test_readonly_last_mutation_gate_rejects_direct_apply(self):
        graph = build_graph(self.vault)
        before = self.source.read_bytes()
        with patch.dict(os.environ, {'OKF_READONLY': '1'}):
            with self.assertRaises(PermissionError):
                _cmd_suggest_edge_types(self.vault, graph, apply=True, definitions=DEFS)
        self.assertEqual(self.source.read_bytes(), before)

    def test_readonly_read_does_not_migrate_legacy_markdown(self):
        before = self.source.read_bytes()
        result = self.cli('--readonly', 'read', 'decisions/a')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(get_reads(self.vault)['decisions/a.md'], 1)

    def test_graph_api_cannot_inject_flags_even_for_writer(self):
        import server
        with patch.object(server, '_run') as run:
            result = server.graph(command='suggest-edge-types', arg='--apply')
            self.assertIn('[sin permiso]', result)
            run.assert_not_called()

    def test_graph_apply_locks_rereads_and_atomically_preserves_concurrent_edit(self):
        from cli.revision import escribir_atomico
        graph = build_graph(self.vault)
        @contextlib.contextmanager
        def lock(vault, filepath):
            # Deterministic interleaving: another edit completed before this lock.
            filepath.write_text(filepath.read_text() + 'concurrent body marker\n')
            yield
        with patch('cli.revision.candado', side_effect=lock) as locked:
            with patch('cli.revision.escribir_atomico', wraps=escribir_atomico) as atomic:
                _cmd_suggest_edge_types(self.vault, graph, apply=True, definitions=DEFS)
        self.assertTrue(locked.called)
        self.assertTrue(atomic.called)
        self.assertIn('target: decisions/b.md', self.source.read_text())
        self.assertIn('concurrent body marker', self.source.read_text())

    def test_explicit_migration_preserves_counts_without_duplicate_baselines(self):
        increment_reads(self.source, self.vault)
        self.assertEqual(migrate_frontmatter_reads(self.vault), (1, 1))
        self.assertEqual(migrate_frontmatter_reads(self.vault), (0, 0))
        self.assertEqual(get_reads(self.vault)['decisions/a.md'], 10)


class TestCanvasConfinement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / 'vault'
        (self.vault / 'insights').mkdir(parents=True)
        (self.vault / '.okf.config.yaml').write_text('types:\n  valid: [Insight]\nfeatures:\n  cognitive_trace: false\n')
        self.note = self.vault / 'insights/a.md'
        self.note.write_text('---\ntype: Insight\ntitle: A\ndescription: fixture\n---\n[[insights/b.md]]\n')
        (self.vault / 'insights/b.md').write_text('---\ntype: Insight\ntitle: B\ndescription: fixture\n---\nNo links\n')
        self.keys = self.root / 'keys-fixture.json'
        self.keys.write_text('{"secret": true}\n')

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'cli', '--vault', str(self.vault), *args],
                              capture_output=True, text=True, timeout=30, cwd=self.root,
                              env={**os.environ, 'PYTHONPATH': str(REPO)})

    def test_generate_cannot_overwrite_file_outside_vault(self):
        before = self.keys.read_bytes()
        result = self.cli('canvas', 'generate', 'insights/a', '--output', str(self.keys))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.keys.read_bytes(), before)

    def test_relative_traversal_symlink_and_wrong_extension_are_rejected(self):
        (self.vault / 'escape').symlink_to(self.root)
        for target in ('../keys-fixture.json', 'escape/keys-fixture.json', 'notes.txt'):
            with self.subTest(target=target):
                result = self.cli('canvas', 'generate', 'insights/a', '--output', target)
                self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.keys.read_bytes(), b'{"secret": true}\n')
        self.assertFalse((self.vault / 'notes.txt').exists())

    def test_validate_and_layout_cannot_touch_outside_or_symlinked_canvas(self):
        external = self.root / 'external.canvas'
        external.write_text('{"nodes": [], "edges": []}')
        self.assertEqual(self.cli('canvas', 'validate', str(external), '--fix').returncode, 1)
        self.assertEqual(self.cli('canvas', 'layout', str(external), 'grid').returncode, 1)
        (self.vault / 'link.canvas').symlink_to(external)
        self.assertEqual(self.cli('canvas', 'validate', 'link.canvas').returncode, 1)
        self.assertEqual(external.read_text(), '{"nodes": [], "edges": []}')

    def test_generate_dry_run_resolves_but_writes_nothing(self):
        result = self.cli('canvas', 'generate', 'insights/a', '--output', 'mapas/x.canvas', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"dry_run": true', result.stdout)
        self.assertFalse((self.vault / 'mapas/x.canvas').exists())

    def test_wrapper_versions_generate_but_skips_validate_without_fix(self):
        import asyncio
        import servicio
        def canvas(action: str = '', fix: bool = False, dry_run: bool = False) -> str:
            (self.vault / 'mapas').mkdir(exist_ok=True)
            (self.vault / 'mapas/x.canvas').write_text('{}')
            return 'ok'
        request = SimpleNamespace(state=SimpleNamespace(okf_usuario={'nombre': 'Ana', 'correo': '', 'permiso': 'escritura'}))
        mcp = SimpleNamespace(get_context=lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)))
        wrapped = servicio.envolver(canvas, True, mcp, vault=self.vault, autocommit=True)
        with patch('servicio.validar_historial'), patch('servicio.confirmar', return_value='abc123') as commit:
            self.assertIn('commit abc123', asyncio.run(wrapped(action='generate')))
            self.assertEqual(commit.call_count, 1)
            self.assertNotIn('commit', asyncio.run(wrapped(action='validate', dry_run=True)))
            self.assertEqual(commit.call_count, 1)
            self.assertIn('commit abc123', asyncio.run(wrapped(action='validate', fix=True)))
            self.assertEqual(commit.call_count, 2)

    def test_canvas_dry_run_works_with_dirty_history_and_never_calls_commit(self):
        import asyncio
        import servicio
        def canvas(action: str = '', fix: bool = False, dry_run: bool = False) -> str:
            return 'simulated'
        request = SimpleNamespace(state=SimpleNamespace(okf_usuario={'nombre': 'Ana', 'correo': '', 'permiso': 'escritura'}))
        mcp = SimpleNamespace(get_context=lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)))
        wrapped = servicio.envolver(canvas, True, mcp, vault=self.vault, autocommit=True)
        with patch('servicio.validar_historial', side_effect=RuntimeError('dirty')) as preflight:
            with patch('servicio.confirmar') as commit:
                for action in ('generate', 'layout'):
                    self.assertEqual(asyncio.run(wrapped(action=action, dry_run=True)), 'simulated')
                preflight.assert_not_called()
                commit.assert_not_called()
                self.assertIn('[historial bloqueado]', asyncio.run(wrapped(action='validate', fix=True, dry_run=True)))

    def test_relative_validate_layout_use_the_checked_vault_path_not_cwd(self):
        # Dos archivos homónimos: solamente el del vault puede cambiar.
        initial = '{"nodes": [{"id":"a","type":"text","text":"x","x":33,"y":7,"width":200,"height":100}],"edges":[]}'
        checked = self.vault / 'x.canvas'
        external = self.root / 'x.canvas'
        checked.write_text(initial)
        external.write_text(initial)
        result = self.cli('canvas', 'validate', 'x.canvas', '--fix')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(external.read_text(), initial)
        self.assertNotEqual(checked.read_text(), initial)
        checked.write_text(initial)
        result = self.cli('canvas', 'layout', 'x.canvas', 'grid')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(external.read_text(), initial)
        self.assertNotEqual(checked.read_text(), initial)

    def test_canvas_destinations_respect_scope_and_reject_internal_symlink(self):
        outside_scope = self.vault / 'outside.canvas'
        result = self.cli('--scope', 'insights', 'canvas', 'generate', 'insights/a', '--output', str(outside_scope))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(outside_scope.exists())
        result = self.cli('--scope', 'insights', 'canvas', 'generate', 'insights/a')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.vault / 'insights/mapas/a.canvas').exists())
        target = self.vault / 'internal.canvas'
        target.write_text('{"nodes":[],"edges":[]}')
        (self.vault / 'alias.canvas').symlink_to(target)
        self.assertEqual(self.cli('canvas', 'validate', 'alias.canvas').returncode, 1)


if __name__ == '__main__':
    unittest.main()
