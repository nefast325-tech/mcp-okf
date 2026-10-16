"""Señal 3 con umbral por tipo y señal 4 con exentos desde config.

Implementa N1/N2 del plan del ajuste del detector (2026-09-05) y la decisión
2026-10-15 de externalizar el vocabulario de exentos:

  - **Señal 3**: un `type: Insight` con `status: propuesta` no dispara hasta
    los 90 días (`stale.propuesta_days_insight`); el resto de los tipos
    mantiene 30. Un Insight es una hipótesis: su ciclo es integrarse o
    archivarse, no "aplicarse".
  - **Señal 4**: la lista de tipos exentos sale de config
    (`types.exempt_from_no_backlinks`), no de una tupla hardcodeada con el
    vocabulario de un vault. El default embebido es el set del Criterio de
    obsolescencia (MarcoTeorico, Spec, Tool, Agente, Sesion, Session, Skill)
    más Insight, que la decisión de triaje dreaming autoriza.

Antes: la tupla `("MarcoTeorico", "Spec", "Tool", "Agente", "Sesion")` no
matcheaba el dialecto del vault de cliente (`Session`), no incluía `Skill` ni
`Insight`, y el umbral de la señal 3 era global (30 días para todo).

Los vaults de prueba son repos git con un commit del día: así la señal 5
(«sin commits») no contamina el nivel, y cada test aserta el nivel real.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.commands.stale import collect_stale
from cli.config import Config
from cli.reads_store import store_path


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _concept(vault, rel, days_old, ctype="Insight", status="propuesta",
             extra_fm="", body="Cuerpo."):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    fm_status = f"status: {status}\n" if status else ""
    p.write_text(
        "---\n"
        f"type: {ctype}\n"
        'title: "T"\n'
        'description: "d"\n'
        f"{fm_status}"
        f'timestamp: "{_iso(days_old)}"\n'
        f"{extra_fm}"
        "---\n\n" + body + "\n",
        encoding="utf-8",
    )


def _signals(results, rel):
    for r in results:
        if r["file"] == rel:
            return r["signals"]
    raise AssertionError(f"{rel} no está en los resultados")


def _level(results, rel):
    for r in results:
        if r["file"] == rel:
            return r["level"]
    raise AssertionError(f"{rel} no está en los resultados")


class StaleBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for cmd in (["init", "-q"], ["config", "user.email", "t@t"],
                    ["config", "user.name", "t"]):
            subprocess.run(["git", *cmd], cwd=str(self.vault), check=True,
                           capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _commit(self):
        """Commitea el vault de prueba: apaga la señal «no commits (new?)»."""
        for cmd in (["add", "-A"], ["commit", "-q", "-m", "t"]):
            subprocess.run(["git", *cmd], cwd=str(self.vault), check=True,
                           capture_output=True)

    def _add_read(self, rel):
        """Una lectura en el store: apaga la señal 2 (reads=0)."""
        p = store_path(self.vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"slug": rel, "ts": "2026-09-01T00:00:00-0500"}) + "\n")


class StaleInsightUmbralTests(StaleBase):
    """Señal 3: 90 días para Insight, 30 para el resto."""

    def test_insight_25d_no_dispara(self):
        rel = "insights/dreaming/hipotesis-joven.md"
        _concept(self.vault, rel, days_old=25)
        self._add_read(rel)
        self._commit()
        results = collect_stale(self.vault)
        self.assertEqual(_signals(results, rel), [])
        self.assertEqual(_level(results, rel), "FRESH")

    def test_insight_95d_dispara(self):
        rel = "insights/dreaming/hipotesis-vieja.md"
        _concept(self.vault, rel, days_old=95)
        self._add_read(rel)
        self._commit()
        # timestamp_days alto para aislar la señal 3 de la señal 1.
        results = collect_stale(self.vault, timestamp_days=365)
        self.assertEqual(_signals(results, rel), ["proposal unresolved (95d)"])

    def test_insight_65d_con_backlink_es_fresco(self):
        # El plan pedía este caso a 95 días, pero a esa edad el umbral de 90
        # (que el mismo plan introduce) ya dispara la señal 3: 95d y «0
        # señales» son incompatibles. A 65 días —por encima de los 30 del
        # resto de los tipos, por debajo de los 90 de Insight— la integración
        # se mide sola: con backlink, 0 señales.
        rel = "insights/dreaming/hipotesis-integrada.md"
        _concept(self.vault, rel, days_old=65)
        self._add_read(rel)
        _concept(self.vault, "decisions/cita.md", days_old=1, ctype="Decision",
                 status="aplicada",
                 body=f"Ver [[{rel.replace('.md', '')}|la hipótesis]].")
        self._commit()
        results = collect_stale(self.vault, timestamp_days=365)
        self.assertEqual(_signals(results, rel), [])
        self.assertEqual(_level(results, rel), "FRESH")

    def test_insight_95d_integrado_no_suma_la_senal_4(self):
        # Integrado pero viejo: queda la señal 3 (proposal unresolved) y NO la 4.
        rel = "insights/dreaming/hipotesis-vieja-integrada.md"
        _concept(self.vault, rel, days_old=95)
        self._add_read(rel)
        _concept(self.vault, "decisions/cita.md", days_old=1, ctype="Decision",
                 status="aplicada",
                 body=f"Ver [[{rel.replace('.md', '')}|la hipótesis]].")
        self._commit()
        results = collect_stale(self.vault, timestamp_days=365)
        self.assertEqual(_signals(results, rel), ["proposal unresolved (95d)"])

    def test_decision_25d_mantiene_el_umbral_de_30(self):
        rel = "decisions/joven.md"
        _concept(self.vault, rel, days_old=25, ctype="Decision")
        self._add_read(rel)
        self._commit()
        results = collect_stale(self.vault)
        # No dispara la señal 3 (25 < 30); sí la 4 (Decision no es exenta).
        self.assertEqual(_signals(results, rel), ["no backlinks"])

    def test_decision_95d_dispara_igual_que_antes(self):
        rel = "decisions/vieja.md"
        _concept(self.vault, rel, days_old=95, ctype="Decision")
        self._add_read(rel)
        self._commit()
        results = collect_stale(self.vault, timestamp_days=365)
        self.assertEqual(sorted(_signals(results, rel)),
                         ["no backlinks", "proposal unresolved (95d)"])

    def test_el_umbral_de_insight_es_configurable(self):
        rel = "insights/dreaming/hipotesis-joven.md"
        _concept(self.vault, rel, days_old=25)
        self._add_read(rel)
        self._commit()
        results = collect_stale(self.vault, timestamp_days=365,
                                propuesta_days_insight=10)
        self.assertEqual(_signals(results, rel), ["proposal unresolved (25d)"])


class StaleExentosConfigTests(StaleBase):
    """Señal 4: los tipos exentos son vocabulario del vault (config)."""

    def test_default_embebido_exime_el_dialecto_del_vault_de_cliente(self):
        # `Session` (vault de cliente) no generaba la señal: la tupla decía Sesion.
        rel = "sessions/sesion-1.md"
        _concept(self.vault, rel, days_old=5, ctype="Session", status="")
        self._commit()
        results = collect_stale(self.vault)
        self.assertEqual(_signals(results, rel), [])
        self.assertEqual(_level(results, rel), "FRESH")

    def test_el_criterio_exime_skill_e_insight(self):
        for rel, ctype in (("skills/una/SKILL.md", "Skill"),
                           ("insights/idea.md", "Insight")):
            _concept(self.vault, rel, days_old=5, ctype=ctype, status="")
        self._commit()
        results = collect_stale(self.vault)
        self.assertEqual(_signals(results, "skills/una/SKILL.md"), [])
        self.assertEqual(_signals(results, "insights/idea.md"), [])

    def test_un_tipo_no_exento_dispara(self):
        rel = "lessons/leccion.md"
        _concept(self.vault, rel, days_old=5, ctype="Lesson", status="")
        self._commit()
        results = collect_stale(self.vault)
        self.assertEqual(_signals(results, rel), ["no backlinks"])

    def test_la_config_manda_sobre_el_default_embebido(self):
        # Un vault declara su propio set: `Session` deja de estar exento y un
        # tipo nuevo sí lo está. La config es la fuente, no la tupla.
        (self.vault / ".okf.config.yaml").write_text(
            "types:\n  exempt_from_no_backlinks:\n    - TipoPropio\n",
            encoding="utf-8",
        )
        _concept(self.vault, "sessions/sesion-1.md", days_old=5,
                 ctype="Session", status="")
        _concept(self.vault, "cosas/propio.md", days_old=5,
                 ctype="TipoPropio", status="")
        self._commit()
        cfg = Config(self.vault)
        self.assertEqual(cfg.types_exempt_from_no_backlinks, {"TipoPropio"})
        results = collect_stale(
            self.vault, exempt_no_backlinks=cfg.types_exempt_from_no_backlinks)
        self.assertEqual(_signals(results, "sessions/sesion-1.md"), ["no backlinks"])
        self.assertEqual(_signals(results, "cosas/propio.md"), [])


if __name__ == "__main__":
    unittest.main()
