"""Regressions for PR52: trace boundaries, YAML keys and protected links.

All credentials are synthetic and assembled at runtime. Trace tests run the
real MCP entry functions, CLI subprocess, SQLite and JSONL in an isolated vault.
"""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from cli.commands.edit import run as edit_run
from cli.commands.new import run as new_run
from cli.config import Config
from cli.redact import MARCA
from cli.revision import revision_de_archivo

REPO = Path(__file__).resolve().parents[1]
GITHUB = "gh" + "p_" + "FAKE" + "abcdefgh0123456789" * 2
AWS = "AK" + "IA" + "FAKE0123456789AB"


class WriteVault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.config_data = {
            "types": {"valid": ["Insight"], "directory": {"Insight": "insights"}},
            "writes": {"base_revision": "required"},
            "features": {"cognitive_trace": True,
                         "trace_db_path": "trace.db", "trace_jsonl_path": "trace.jsonl"},
        }
        self._config()

    def _config(self):
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(self.config_data), encoding="utf-8")
        self.config = Config(self.vault)

    def _run(self, command, **kw):
        defaults = dict(concept_type="Insight", title="Note", description="Test note.",
                        slug="insights/note", body=None, fields=None, links=None,
                        clear_links=False, base_revision=None, force=False)
        defaults.update(kw)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = command(SimpleNamespace(**defaults), self.vault, self.config)
        return rc, out.getvalue(), err.getvalue()

    @property
    def note(self):
        return self.vault / "insights/note.md"

    def _fm(self):
        return yaml.safe_load(self.note.read_text(encoding="utf-8").split("---", 2)[1])


class TestTraceBoundary(WriteVault):
    def _exercise(self, redact):
        self.config_data["writes"]["redact"] = redact
        self._config()
        # Import server in a fresh process so startup and CLI share the test vault.
        code = """
import server
from cli.redact import tachar
from cli.revision import revision_de_archivo
secrets = [('gh' + 'p_' + 'FAKE' + 'abcdefgh0123456789' * 2, 65),
           ('AK' + 'IA' + 'FAKE0123456789AB', 90)]
for index, (token, offset) in enumerate(secrets):
    body = '.' * offset + token + ' tail'
    title = 'Note' + str(index)
    path = server.VAULT / 'insights' / (title.lower() + '.md')
    result = server.new('Insight', title=title, description='Test note.', body=body)
    assert '✅ Created:' in result, result
    expected = tachar(body)[0] if REDACT else body
    assert expected in path.read_text(), 'CLI arguments/body were changed'
    result = server.edit(str(path.relative_to(server.VAULT)), body=body,
                         base_revision=revision_de_archivo(path))
    assert '✅ Updated:' in result, result
    assert expected in path.read_text(), 'CLI arguments/body were changed'
""".replace("REDACT", repr(redact))
        env = {**os.environ, "OKF_VAULT": str(self.vault), "HOME": str(self.vault),
               "OKF_CONFIG": str(self.vault / "missing-global.yaml"),
               "PYTHONPATH": str(REPO), "OKF_SESSION_ID": "pr52-test"}
        for key in ("OKF_SCOPE", "OKF_READONLY", "OKF_MCP_CALLER", "OKF_PYLIBS"):
            env.pop(key, None)
        proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with sqlite3.connect(self.vault / "trace.db") as db:
            rows = db.execute("SELECT tool, params, exit_code FROM events ORDER BY id").fetchall()
        jsonl = [json.loads(line) for line in (self.vault / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(jsonl), 4)
        from cli.redact import tachar
        expected = []
        for token, offset in ((GITHUB, 65), (AWS, 90)):
            clean = tachar('.' * offset + token + ' tail')[0]
            expected.extend([clean[:100] + '...' if len(clean) > 100 else clean] * 2)
        for index, ((tool, params, rc), event) in enumerate(zip(rows, jsonl)):
            with self.subTest(redact=redact, index=index, tool=tool):
                self.assertEqual(rc, 0)
                self.assertEqual(event['exit_code'], 0)
                self.assertEqual(event['tool'], tool)
                sql_params = json.loads(params)
                self.assertEqual(sql_params['body'], expected[index])
                self.assertEqual(event['params']['body'], expected[index])
                self.assertLessEqual(len(sql_params['body']), 103)
                for value in (GITHUB[:35], AWS[:10]):
                    self.assertNotIn(value, params)
                    self.assertNotIn(value, json.dumps(event))

    def test_redact_full_body_before_trace_preview(self):
        self._exercise(True)

    def test_disabled_note_redaction_does_not_change_trace_or_cli_arguments(self):
        self._exercise(False)


class TestFieldYaml(WriteVault):
    def test_secret_keys_round_trip_new_nested_edit_and_later_edit(self):
        for metadata in ({GITHUB: 'safe', 'keep': 42},
                         {'nested': {GITHUB: 'safe', 'keep': 42}}):
            with self.subTest(metadata=metadata):
                if self.note.exists():
                    self.note.unlink()
                rc, _, err = self._run(new_run, fields=['metadata=' + json.dumps(metadata)])
                self.assertEqual(rc, 0, err)
                expected = json.loads(json.dumps(metadata).replace(GITHUB, MARCA))
                self.assertEqual(self._fm()['metadata'], expected)
                rc, _, err = self._run(edit_run, fields=['metadata=' + json.dumps(metadata), 'extra=1'])
                self.assertEqual(rc, 0, err)
                self.assertEqual(self._fm()['metadata'], expected)
                self.assertEqual(self._run(edit_run, title='Still editable')[0], 0)
                self.assertNotIn(GITHUB, self.note.read_text())

    def test_edit_secret_key_on_existing_note(self):
        self.assertEqual(self._run(new_run)[0], 0)
        rc, _, err = self._run(edit_run, fields=['metadata=' + json.dumps({GITHUB: 'safe', 'keep': 42})])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._fm()['metadata'], {MARCA: 'safe', 'keep': 42})
        self.assertEqual(self._run(edit_run, title='Readable')[0], 0)

    def test_arbitrary_keys_and_json_types_survive_new_and_edit(self):
        metadata = {'yes': 'true', 'null': 'null', '42': '42', '': '',
                    ':': 'colon', '2026-99-01': 'invalid date', '2026-01-01': 'date key',
                    'colon: key': {'[brackets]': True, '#hash': False,
                                   'line\nbreak': None, 'empty': {}, 'list': []},
                    'objects': [{'on': False, 'count': 42, 'none': None}, ['yes', True]],
                    'numbers': [0.25, 1e20, -1e-20],
                    'date': '2026-01-01'}
        rc, _, err = self._run(new_run, fields=['metadata=' + json.dumps(metadata)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._fm()['metadata'], metadata)
        self.assertEqual(self._run(edit_run, title='After edit')[0], 0)
        self.assertEqual(self._fm()['metadata'], metadata)
        # Edit also accepts arbitrary top-level/dotted keys, unlike new.
        for key in ('[literal]', 'yes', 'colon: key'):
            self.assertEqual(self._run(edit_run, fields=[key + '=safe'])[0], 0)
            self.assertEqual(self._fm()[key], 'safe')

    def test_edit_refuses_invalid_generated_frontmatter_without_touching_note(self):
        self.assertEqual(self._run(new_run)[0], 0)
        before = self.note.read_bytes()
        with patch('cli.commands.edit._serialize', return_value='---\ninvalid: [\n---\n'):
            rc, _, err = self._run(edit_run, title='Changed')
        self.assertEqual(rc, 1)
        self.assertIn('frontmatter', err)
        self.assertEqual(self.note.read_bytes(), before)

    def test_forced_new_refuses_invalid_frontmatter_before_replacing_note(self):
        self.assertEqual(self._run(new_run)[0], 0)
        before = self.note.read_bytes()
        with patch('cli.commands.new._build_frontmatter', return_value='---\ninvalid: [\n---\n'):
            rc, _, err = self._run(new_run, force=True)
        self.assertEqual(rc, 1)
        self.assertIn('frontmatter', err)
        self.assertEqual(self.note.read_bytes(), before)


class TestProtectedLinks(WriteVault):
    def setUp(self):
        super().setUp()
        self.assertEqual(self._run(new_run, title='Target')[0], 0)
        self.assertEqual(self._run(new_run, links=['insights/target:extiende'])[0], 0)

    def test_fields_cannot_replace_delete_or_mutate_links_with_or_without_revision(self):
        original = self.note.read_bytes()
        original_revision = revision_de_archivo(self.note)
        for value in ('links=[]', 'links=null', 'links=',
                      'links=[{"target":"missing.md","type":"bogus"}]',
                      'links.target=missing', 'links.child=[]'):
            for revision in (None, original_revision):
                with self.subTest(value=value, revision=revision):
                    self.note.write_bytes(original)
                    before = self.note.read_bytes()
                    rc, _, err = self._run(edit_run, fields=['extra=1', value], base_revision=revision)
                    self.assertEqual(rc, 1)
                    self.assertIn('--link', err)
                    self.assertEqual(self.note.read_bytes(), before)

    def test_dedicated_flags_require_revision_and_still_work_with_current_revision(self):
        for kwargs in ({'clear_links': True}, {'links': ['insights/target:refina']}):
            before = self.note.read_bytes()
            rc, _, err = self._run(edit_run, **kwargs)
            self.assertEqual(rc, 1)
            self.assertIn('exige --base-revision', err)
            self.assertEqual(self.note.read_bytes(), before)
            rc, _, err = self._run(edit_run, base_revision=revision_de_archivo(self.note), **kwargs)
            self.assertEqual(rc, 0, err)
            if kwargs.get('clear_links'):
                self.assertNotIn('links', self._fm())
            else:
                self.assertEqual(self._fm()['links'], [{'target': 'insights/target.md', 'type': 'refina'}])


if __name__ == '__main__':
    unittest.main()
