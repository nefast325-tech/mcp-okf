"""CLI regressions: partial predicates, additional body hits and Unicode folding."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class SearchRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(
            "types:\n  valid: [Decision, Insight]\nfeatures:\n  cognitive_trace: false\n",
            encoding="utf-8",
        )

    def note(self, name, title="other", description="", body="", **fields):
        fm = dict(type="Decision", title=title, description=description,
                  status="vigente", cyber={"state": "good"})
        fm.update(fields)
        # JSON is valid YAML; use JSON scalars to preserve Unicode exactly.
        text = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}"
                                      for k, v in fm.items()) + "\n---\n\n" + body + "\n"
        (self.vault / name).write_text(text, encoding="utf-8")
        return text

    def cli(self, query, *args):
        env = dict(os.environ, PYTHONPATH=str(REPO), OKF_VAULT=str(self.vault),
                   OKF_SESSION_PURPOSE="test")
        result = subprocess.run(
            [sys.executable, "-m", "cli", "--vault", str(self.vault),
             "search", "--query", query, *args],
            cwd=REPO, env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def paths(self, output):
        return {line.split()[0] for line in output.splitlines()
                if line.startswith("  ") and line.split() and line.split()[0].endswith(".md")}

    def test_inline_predicates_apply_to_full_and_partial_in_both_outputs(self):
        variants = {
            "good": ("Decision", "other", "vigente", "good"),
            "wrong-type": ("Insight", "other", "vigente", "good"),
            "wrong-title": ("Decision", "another", "vigente", "good"),
            "wrong-status": ("Decision", "other", "pendiente", "good"),
            "wrong-cyber": ("Decision", "other", "vigente", "bad"),
        }
        filters = [
            "type:Decision", "title:other", "type:Decision title:other",
            "status:vigente", "cyber.state:good",
            "type:Decision status:vigente", "title:other cyber.state:good",
            "type:Decision title:other status:vigente cyber.state:good",
        ]
        for body_mode in (False, True):
            for name, (kind, title, status, state) in variants.items():
                for partial in (False, True):
                    words = "alpha" if partial else "alpha beta"
                    self.note(f"{name}-{partial}.md", title=title, type=kind,
                              status=status, cyber={"state": state},
                              description="" if body_mode else words,
                              body=words if body_mode else "")
            for inline in filters:
                allow_partial = not any(t.startswith(("status:", "cyber.")) for t in inline.split())
                expected = set()
                for name, values in variants.items():
                    fields: dict[str, str] = dict(zip(("type", "title", "status", "cyber.state"), values))
                    if all(fields[k].lower() == v.lower()
                           for k, v in (t.split(":", 1) for t in inline.split())):
                        expected.add(f"{name}-False.md")
                        if allow_partial:
                            expected.add(f"{name}-True.md")
                for json_out in (False, True):
                    with self.subTest(body=body_mode, filters=inline, json=json_out):
                        options = ["--limit", "30"]
                        if body_mode:
                            options.append("--body")
                        if json_out:
                            options.append("--json")
                        out = self.cli("alpha beta " + inline, *options)
                        if json_out:
                            data = json.loads(out)
                            actual = {c["file"] for c in data}
                            for c in data:
                                if c["file"].endswith("True.md"):
                                    self.assertEqual(c["match"], "partial")
                                    self.assertEqual(c["terms_matched"], "1/2")
                        else:
                            actual = self.paths(out)
                        self.assertEqual(actual, expected)

    def test_partial_type_inline_and_separate_filters_are_intersected(self):
        self.note("insight.md", description="alpha", type="Insight")
        self.note("decision.md", description="alpha")
        for extra in ([], ["--body"]):
            out = self.cli("alpha beta type:Decision", "--type", "Insight",
                           "--json", "--limit", "3", *extra)
            self.assertEqual(json.loads(out), [])

    def test_body_counter_excludes_displayed_partials_after_limit(self):
        self.note("a.md", title="alpha", body="beta")
        self.note("b.md", title="alpha", body="beta")
        self.note("c.md", body="alpha beta")
        for limit, remaining in ((1, 2), (2, 1), (3, 1), (0, 3)):
            with self.subTest(limit=limit):
                out = self.cli("alpha beta", "--limit", str(limit))
                self.assertEqual(len(self.paths(out)), min(limit, 2) if limit else 0)
                self.assertIn(f"({remaining} nota(s) más mencionan", out)

    def test_single_displayed_partial_is_not_also_an_additional_body_hit(self):
        self.note("a.md", title="alpha", body="beta")
        out = self.cli("alpha beta")
        self.assertEqual(self.paths(out), {"a.md"})
        self.assertIn("parcial: 1/2", out)
        self.assertNotIn("--body para verlas", out)

    def test_body_counter_handles_full_hits_plus_limited_partials(self):
        self.note("full.md", title="alpha beta")
        self.note("a.md", title="alpha", body="beta")
        self.note("b.md", title="alpha", body="beta")
        for limit, remaining in ((1, 2), (2, 1), (3, 0)):
            with self.subTest(limit=limit):
                out = self.cli("alpha beta", "--limit", str(limit))
                self.assertEqual(len(self.paths(out)), limit)
                if remaining:
                    self.assertIn(f"({remaining} nota(s) más mencionan", out)
                else:
                    self.assertNotIn("--body para verlas", out)

    def test_nfc_nfd_documents_and_queries_are_equivalent_substrings(self):
        self.note("nfc.md", title="Página web")
        self.note("nfd.md", title=unicodedata.normalize("NFD", "Página web"))
        for query in ("pagina", "página", "pa\u0301gina", "ÁGIN", "a\u0301gin"):
            with self.subTest(query=query):
                self.assertEqual(self.paths(self.cli(query)), {"nfc.md", "nfd.md"})

    def test_nfd_body_snippet_keeps_original_text_and_line_positions(self):
        for form in ("NFC", "NFD"):
            # Enough removed marks before the match to expose folded offsets.
            line = unicodedata.normalize(form, "á" * 180 + " Página objetivo " + "z" * 200)
            text = self.note(f"{form}.md", body="# Sección\nAntes.\n" + line + "\nOtra página.")
            expected_line = text.splitlines().index(line) + 1
            for query in ("pagina", "página", "pa\u0301gina", "a\u0301gin"):
                with self.subTest(form=form, query=query):
                    data = json.loads(self.cli(query, "--body", "--json"))
                    hit = next(c for c in data if c["file"] == f"{form}.md")
                    self.assertEqual(hit["line"], expected_line)
                    self.assertEqual(hit["section"], "Sección")
                    self.assertEqual(hit["matches"], 2)
                    self.assertLessEqual(len(hit["snippet"]), 160)
                    self.assertIn(unicodedata.normalize(form, "Página"), hit["snippet"])
                    self.assertIn(hit["snippet"].strip("…"), line)

    def test_exact_inline_filters_do_not_fold_accents_or_normalize_unicode(self):
        for form in ("NFC", "NFD"):
            title = unicodedata.normalize(form, "Página")
            self.note(f"{form}.md", title=title, description="alpha", status=title,
                      cyber={"state": title})
        for field in ("title", "status", "cyber.state"):
            for value, expected in (("pagina", set()), ("página", {"NFC.md"}),
                                    ("pa\u0301gina", {"NFD.md"})):
                with self.subTest(field=field, value=value):
                    out = self.cli(f"alpha beta {field}:{value}", "--json", "--limit", "3")
                    # Status/cyber disable partials; title still permits them.
                    self.assertEqual({c["file"] for c in json.loads(out)},
                                     expected if field == "title" else set())
                    out = self.cli(f"alpha {field}:{value}", "--json")
                    self.assertEqual({c["file"] for c in json.loads(out)}, expected)


if __name__ == "__main__":
    unittest.main()
