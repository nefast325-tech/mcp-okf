"""End-to-end HTTP boundaries using a disposable vault and dummy identities.

Run locally only: uses an ephemeral loopback listener, no external endpoints.
"""
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

import httpx
import servicio

REPO = Path(__file__).resolve().parent.parent


def git(vault, *args):
    return subprocess.run(['git', '-C', str(vault), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


class TestHTTPIntegration(unittest.TestCase):
    def _revision(self, text):
        match = re.search(r'rev: ([0-9a-f]+)', text)
        self.assertIsNotNone(match, text)
        assert match is not None
        return match.group(1)

    def test_real_http_permissions_concurrency_history_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'
            vault.mkdir()
            (vault / '.okf.config.yaml').write_text(
                'types:\n  valid: [Insight]\nwrites:\n  base_revision: required\n'
                'features:\n  cognitive_trace: false\n', encoding='utf-8')
            (vault / '.gitignore').write_text('.okf/\n.obsidian/\n', encoding='utf-8')
            (vault / 'insights').mkdir()
            legacy = vault / 'insights/legacy.md'
            legacy.write_text('---\ntype: Insight\ntitle: Legacy\ndescription: Old counter\nreads: 9\n---\nLegacy body\n')
            git(vault, 'init', '-q')
            git(vault, 'add', '-A')
            git(vault, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'initial')
            keys = servicio.Llaves(root / 'keys.json')
            ana = keys.crear('Ana', 'ana@example.test', 'escritura')
            beto = keys.crear('Beto', 'beto@example.test', 'escritura')
            cora = keys.crear('Cora', 'cora@example.test', 'lectura')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = {**os.environ, 'OKF_HTTP': '', 'OKF_LLAVES': '', 'OKF_AUTOCOMMIT': '',
                   'OKF_READONLY': '', 'OKF_SCOPE': '', 'PYTHONPATH': str(REPO)}
            with (root / 'server.log').open('w+') as log:
                proc = subprocess.Popen([sys.executable, str(REPO / 'server.py'), '--vault', str(vault),
                                         '--http', f'127.0.0.1:{port}', '--llaves', str(keys.ruta), '--autocommit'],
                                        env=env, stdout=log, stderr=log)
                try:
                    url = f'http://127.0.0.1:{port}/mcp'
                    ready = False
                    for _ in range(100):
                        if proc.poll() is not None:
                            log.seek(0)
                            self.fail('HTTP server exited: ' + log.read())
                        try:
                            response = httpx.get(url, timeout=0.2, trust_env=False)
                            if response.status_code == 401:
                                ready = True
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    self.assertTrue(ready, 'HTTP server did not become ready')
                    asyncio.run(self._exercise(url, vault, keys, ana, beto, cora))
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

    async def _exercise(self, url, vault, keys, ana, beto, cora):
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            async def rpc(key, method, params=None):
                response = await client.post(url, headers={
                    'Authorization': 'Bearer ' + key,
                    'Accept': 'application/json, text/event-stream',
                    'MCP-Protocol-Version': '2025-03-26',
                }, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn('mcp-session-id', response.headers)
                if response.headers.get('content-type', '').startswith('text/event-stream'):
                    payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                    data = next(payload for payload in payloads if payload.get('id') == 1)
                else:
                    data = response.json()
                self.assertNotIn('error', data, data)
                return data['result']

            async def tool(key, name, **arguments):
                result = await rpc(key, 'tools/call', {'name': name, 'arguments': arguments})
                self.assertFalse(result.get('isError'), result)
                return '\n'.join(item.get('text', '') for item in result.get('content', []))

            for key in ('', 'fake'):
                response = await client.get(url, headers={'Authorization': 'Bearer ' + key} if key else {})
                self.assertEqual(response.status_code, 401)
            await rpc(ana, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                         'clientInfo': {'name': 'review-fixture', 'version': '1'}})
            tools = await rpc(cora, 'tools/list')
            self.assertEqual(len(tools['tools']), 20)
            bad_host = await client.post(url, headers={
                'Authorization': 'Bearer ' + ana, 'Host': 'untrusted.example.test',
                'Accept': 'application/json, text/event-stream',
            }, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
            self.assertEqual(bad_host.status_code, 421)
            bad_origin = await client.post(url, headers={
                'Authorization': 'Bearer ' + ana, 'Origin': 'https://untrusted.example.test',
                'Accept': 'application/json, text/event-stream',
            }, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
            self.assertEqual(bad_origin.status_code, 403)
            legacy = vault / 'insights/legacy.md'
            before_read = legacy.read_bytes()
            await tool(cora, 'read', slug='insights/legacy')
            self.assertEqual(legacy.read_bytes(), before_read)
            self.assertEqual(git(vault, 'status', '--porcelain'), '')
            for key in (ana, cora):
                bypass = await tool(key, 'graph', command='suggest-edge-types', arg='--apply')
                self.assertIn('[sin permiso]', bypass)
            for name, args in {
                'new': {'type': 'Insight', 'title': 'Denied'},
                'edit': {'slug': 'insights/shared', 'body': 'denied'},
                'index': {}, 'touch': {}, 'canvas': {'action': 'generate'},
                'graph_command': {'action': 'clear_highlights'},
                'graph_suggest_edge_types': {'apply': True}, 'analytics': {}, 'trace': {'query': 'x'},
            }.items():
                output = await tool(cora, name, **args)
                self.assertIn('[sin permiso]', output, name)
            created = await tool(ana, 'new', type='Insight', title='Shared', description='HTTP fixture', body='Initial body')
            self.assertIn('commit ', created)
            note = vault / 'insights/shared.md'
            self.assertTrue(note.exists(), created)
            read = await tool(beto, 'read', slug='insights/shared')
            self.assertIn('Initial body', read)
            missing_revision = await tool(ana, 'edit', slug='insights/shared', body='not allowed')
            self.assertIn('[exit_code: 1]', missing_revision)
            self.assertNotIn('not allowed', note.read_text())
            before_invalid = git(vault, 'rev-parse', 'HEAD')
            invalid = await tool(ana, 'new', type='Insight', title='Invalid', description='invalid edge',
                                 links='insights/shared:inventada')
            self.assertIn('[exit_code: 1]', invalid)
            self.assertEqual(git(vault, 'rev-parse', 'HEAD'), before_invalid)
            rejected = 0
            for round_number in range(10):
                snapshot = note.read_text(encoding='utf-8')
                revision = self._revision(await tool(ana, 'read', slug='insights/shared'))
                body = snapshot.split('---', 2)[2].strip()
                results = await asyncio.gather(*[
                    tool(key, 'edit', slug='insights/shared', body=body + f'\n{name}-{round_number}', base_revision=revision)
                    for name, key in [('Ana', ana), ('Beto', beto)]
                ])
                self.assertEqual(sum('commit ' in value for value in results), 1, results)
                for (name, key), result in zip([('Ana', ana), ('Beto', beto)], results):
                    if '[exit_code: 1]' in result:
                        rejected += 1
                        reread = await tool(key, 'read', slug='insights/shared')
                        current = self._revision(reread)
                        newbody = note.read_text().split('---', 2)[2].strip()
                        retry = await tool(key, 'edit', slug='insights/shared', body=newbody + f'\n{name}-{round_number}', base_revision=current)
                        self.assertIn('commit ', retry)
                for name in ('Ana', 'Beto'):
                    self.assertIn(f'{name}-{round_number}', note.read_text())
            self.assertEqual(rejected, 10)
            authors = Counter(git(vault, 'log', '--format=%an', 'HEAD', '^HEAD~21').splitlines())
            self.assertEqual(authors, {'Ana': 11, 'Beto': 10})
            self.assertEqual(git(vault, 'status', '--porcelain'), '')
            before = keys.ruta.stat()
            database = json.loads(keys.ruta.read_text())
            database.pop(servicio._huella(cora))
            replacement = keys.ruta.with_suffix('.new')
            replacement.write_text(json.dumps(database))
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, keys.ruta)
            response = await client.get(url, headers={'Authorization': 'Bearer ' + cora})
            self.assertEqual(response.status_code, 401)
            hook = vault / '.git/hooks/pre-commit'
            hook.write_text('#!/bin/sh\nprintf "fixture hook rejected\\n" >&2\nexit 1\n')
            hook.chmod(0o700)
            failed = await tool(ana, 'edit', slug='insights/shared', description='hook-failure')
            self.assertIn('[error de historial]', failed)
            self.assertIn('fixture hook rejected', failed)
            blocked = await tool(beto, 'edit', slug='insights/shared', description='must not overwrite')
            self.assertIn('[historial bloqueado]', blocked)
            self.assertNotIn('must not overwrite', note.read_text())
            print('HTTP E2E: 9 read-key write denials; 20 concurrent edits + 10 rejected/retried; '
                  '21 attributed commits; revocation=401; hook failure visible; next write blocked')


if __name__ == '__main__':
    unittest.main()
