"""El índice de una skill hereda la description de su SKILL.md.

Medido el 22-nov-2026 en OKF-Vault: las 6 skills disparaban
«no description in index.md» al regenerar índices porque su index.md no lleva
prosa ni frontmatter (solo el título y la lista de guías), y el índice padre
las listaba sin descripción. La descripción existe en el SKILL.md: el índice
del directorio tiene que leerla de ahí.
"""

import tempfile
import unittest
from pathlib import Path

from cli.commands.index import _generate_index, _get_dir_description

DESC = "Use when preparando salida y compartimentación para un enganche."


def _skill(vault, nombre, desc):
    d = vault / "skills" / nombre
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\n"
        "type: Skill\n"
        'title: "Una skill"\n'
        f'description: "{desc}"\n'
        "status: vigente\n"
        "---\n\n"
        "# Una skill\n\nCuerpo.\n",
        encoding="utf-8",
    )
    return d


class TestSkillDescription(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_de_skill_hereda_la_description_del_skill_md(self):
        d = _skill(self.vault, "opsec", DESC)
        out = _generate_index(d, self.vault)
        self.assertIn(f'description: "{DESC}"', out)
        self.assertIn(f"[SKILL.md](SKILL.md) - {DESC}", out)

    def test_index_padre_lista_la_skill_con_su_description(self):
        d = _skill(self.vault, "opsec", DESC)
        (d / "index.md").write_text(_generate_index(d, self.vault), encoding="utf-8")
        self.assertEqual(_get_dir_description(self.vault / "skills", "opsec"), DESC)

    def test_index_propio_con_description_manda_sobre_el_skill_md(self):
        d = _skill(self.vault, "opsec", DESC)
        (d / "index.md").write_text(
            "---\n"
            'description: "Descripción propia del índice."\n'
            "---\n\n# Opsec\n",
            encoding="utf-8",
        )
        out = _generate_index(d, self.vault)
        self.assertIn('description: "Descripción propia del índice."', out)
        self.assertNotIn(DESC, out.split("##")[0])


if __name__ == "__main__":
    unittest.main()
