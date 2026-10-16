"""El panel y el CLI miden el MISMO stale.

`dashboard_snapshot._collect_stale_results` le pasaba a `collect_stale` sus
umbrales por parámetro, pero no `config=`, así que el detector caía en los
literales legacy (señal 3 = `propuesta`, señal 6 = Decision) mientras `stale` y
`board` leían el esquema del vault (`pending` por capa). Medido en OKF-Vault con
el esquema activo: 72 de 700 conceptos divergían —el panel daba ATTENTION donde
el CLI daba STALE—. Misma clase que el #21, el #29 y el #30: dos consumidores de
la misma primitiva midiendo distinto.
"""

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from cli.commands.dashboard_snapshot import _collect_stale_results
from cli.commands.stale import collect_stale
from cli.config import Config

TS_VIEJO = (datetime.now(timezone.utc) - timedelta(days=60)).strftime(
    "%Y-%m-%dT%H:%M:%S-05:00")

SCHEMA = {
    "types": {
        "valid": ["Plan"],
        "directory": {"Plan": "plans"},
        "exempt_from_no_backlinks": ["Plan"],
        "states": {"strict": True, "schemas": {
            "trabajo": {"types": ["Plan"], "initial": "pendiente",
                        "states": ["pendiente", "en-curso", "hecha"],
                        "pending": ["pendiente"]},
        }},
    },
    "exclude": {"files": ["index.md"], "dirs": [".git"]},
}


class TestParidadConElPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "plans").mkdir(parents=True, exist_ok=True)
        (self.vault / ".okf.config.yaml").write_text(
            yaml.safe_dump(SCHEMA), encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.vault, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.vault, check=True)
        # Un Plan en el estado que el esquema cuenta como pendiente (no `propuesta`)
        (self.vault / "plans/p.md").write_text(
            "---\ntype: Plan\ntitle: \"P\"\ndescription: \"d.\"\n"
            f"status: pendiente\ntimestamp: {TS_VIEJO}\ncreated: {TS_VIEJO}\n---\n\nCuerpo.\n",
            encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.vault, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.vault, check=True)
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def test_el_panel_y_el_cli_dan_las_mismas_senales(self):
        del_panel = {r["file"]: r["signals"] for r in _collect_stale_results(self.vault, self.config)}
        del_cli = {r["file"]: r["signals"] for r in collect_stale(self.vault, config=self.config)}
        self.assertEqual(del_cli, del_panel,
                         "el panel tiene que leer el esquema igual que `stale`")
        # Y el estado pendiente del esquema tiene que encender la señal 3:
        # con los literales legacy (`propuesta`) este Plan no sonaba.
        senales = del_panel.get("plans/p.md", [])
        self.assertTrue(any("proposal unresolved" in s for s in senales),
                        f"faltó la señal 3 del esquema: {senales}")


if __name__ == "__main__":
    unittest.main()
