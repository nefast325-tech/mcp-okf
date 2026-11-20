"""Tests del store de telemetría de lecturas (fuera del frontmatter).

Decisión 2026-08-27: los read counters NO se escriben en el frontmatter
(versionaban ruido en cada lectura y generaban conflictos en vaults
multi-actor con sync automático). Viven en <vault>/.okf/state/reads.jsonl
(no versionado, append-only con lock).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.reads_store import (
    find_vault,
    get_reads,
    increment_reads,
    migrate_frontmatter_reads,
    store_path,
)


class TestReadsStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / ".okf.config.yaml").write_text(
            "types:\n  valid: [Insight]\n", encoding="utf-8"
        )
        self.dec = self.vault / "decisions"
        self.dec.mkdir()
        self.note = self.dec / "foo.md"
        self.note.write_text(
            "---\ntype: Insight\ntitle: Foo\ndescription: Bar\n---\n# Foo\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_increment_creates_store_and_counts(self):
        self.assertEqual(increment_reads(self.note, self.vault), 1)
        self.assertEqual(increment_reads(self.note, self.vault), 2)
        self.assertEqual(get_reads(self.vault).get("decisions/foo.md"), 2)

    def test_store_never_touches_frontmatter(self):
        increment_reads(self.note, self.vault)
        content = self.note.read_text(encoding="utf-8")
        self.assertNotIn("reads:", content)
        # El archivo queda exactamente como estaba
        self.assertTrue(content.startswith("---\ntype: Insight\ntitle: Foo"))

    def test_store_is_outside_git_tracked_content(self):
        increment_reads(self.note, self.vault)
        self.assertTrue(store_path(self.vault).exists())
        self.assertNotIn(".md", str(store_path(self.vault).relative_to(self.vault)))

    def test_migrate_seeds_and_cleans(self):
        self.note.write_text(
            "---\ntype: Insight\ntitle: Foo\ndescription: Bar\nreads: 7\n---\n# Foo\n",
            encoding="utf-8",
        )
        seeded, cleaned = migrate_frontmatter_reads(self.vault)
        self.assertEqual((seeded, cleaned), (1, 1))
        self.assertNotIn("reads:", self.note.read_text(encoding="utf-8"))
        self.assertEqual(get_reads(self.vault).get("decisions/foo.md"), 7)

    def test_legacy_migration_is_explicit_not_a_read_side_effect(self):
        self.note.write_text(
            "---\ntype: Insight\ntitle: Foo\ndescription: Bar\nreads: 5\n---\n# Foo\n",
            encoding="utf-8",
        )
        before = self.note.read_bytes()
        new_val = increment_reads(self.note, self.vault)
        self.assertEqual(new_val, 1)
        self.assertEqual(self.note.read_bytes(), before)
        self.assertEqual(migrate_frontmatter_reads(self.vault), (1, 1))
        self.assertEqual(get_reads(self.vault).get("decisions/foo.md"), 6)

    def test_migrate_preserves_body(self):
        body = "# Foo\n\n## Seccion 1\nContenido con **formato** y [link](url).\n\n### Sub\n- item 1\n- item 2\n"
        self.note.write_text(
            "---\ntype: Insight\ntitle: Foo\ndescription: Bar\nreads: 7\n---\n" + body,
            encoding="utf-8",
        )
        seeded, cleaned = migrate_frontmatter_reads(self.vault)
        self.assertEqual((seeded, cleaned), (1, 1))
        restored = self.note.read_text(encoding="utf-8")
        self.assertIn(body, restored)  # el body sobrevive íntegro
        self.assertNotIn("reads:", restored)

    def test_increment_legacy_preserves_entire_note(self):
        body = "# Foo\n\nParrafo del body que NO debe perderse.\n"
        self.note.write_text(
            "---\ntype: Insight\ntitle: Foo\ndescription: Bar\nreads: 5\n---\n" + body,
            encoding="utf-8",
        )
        before = self.note.read_bytes()
        new_val = increment_reads(self.note, self.vault)
        self.assertEqual(new_val, 1)
        restored = self.note.read_text(encoding="utf-8")
        self.assertIn(body, restored)
        self.assertEqual(self.note.read_bytes(), before)

    def test_legacy_field_in_body_is_not_touched(self):
        # Un 'reads:' en el body no debe confundirse con el contador
        self.note.write_text(
            "---\ntype: Insight\ntitle: Foo\ndescription: Bar\n---\n"
            "# Foo\n\nEl campo reads: 99 del body no es frontmatter.\n",
            encoding="utf-8",
        )
        self.assertEqual(increment_reads(self.note, self.vault), 1)
        content = self.note.read_text(encoding="utf-8")
        self.assertIn("reads: 99", content)  # el body quedó intacto

    def test_find_vault_walks_up(self):
        deep = self.dec / "sub" / "x.md"
        deep.parent.mkdir(parents=True)
        deep.write_text("x", encoding="utf-8")
        self.assertEqual(find_vault(deep), self.vault.resolve())

    def test_multiple_files_aggregate(self):
        other = self.vault / "insights" / "bar.md"
        other.parent.mkdir(parents=True)
        other.write_text(
            "---\ntype: Insight\ntitle: Bar\ndescription: Baz\n---\n# Bar\n",
            encoding="utf-8",
        )
        increment_reads(self.note, self.vault)
        increment_reads(other, self.vault)
        increment_reads(other, self.vault)
        reads = get_reads(self.vault)
        self.assertEqual(reads.get("decisions/foo.md"), 1)
        self.assertEqual(reads.get("insights/bar.md"), 2)


if __name__ == "__main__":
    unittest.main()
