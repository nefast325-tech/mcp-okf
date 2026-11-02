"""La coherencia que avisa `validate` también se ve en `stale` y en `health`.

Caso real (controlresearch-vault, cd1dce5, 1-nov-2026): `validate --all`
emitía 15 avisos de apoyo en reemplazada o en muerta sin sucesora desde 13
nodos vivos, pero `stale` los clasificaba 🟢 y `health --json` daba
warnings=0. La regla ahora vive en cli/vigente.py (`apoyos_rotos`) y:

- `validate` redacta sus avisos desde ella (mismo texto que antes; lo fija
  tests/test_validate_apoyo_en_muerta_y_aplica.py);
- `stale` agrega la señal 9, una por arista, con `details.apoyos_rotos`;
- `health` muestra la sección «coherencia» (conteo de aristas y nodos,
  primeros detalles, clave `coherencia` en `--json`) SIN contarla como
  warning: `--strict` no cambia de código salvo `graph.coherencia_strict`.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from cli.config import Config
from cli.commands import health, stale
from cli.vigente import apoyos_rotos, describe_apoyo

TS = "2026-10-31T10:00:00-05:00"


def _config(coherencia_strict=None, con_esquema=True):
    cfg = {
        "types": {
            "valid": ["Decision", "Task", "Plan"],
            "directory": {"Decision": "decisions", "Task": "tasks", "Plan": "plans"},
            "exempt_from_no_backlinks": ["Decision", "Task", "Plan"],
        },
        "graph": {"strict_pairs": False, "inherit_default_pairs": False},
        "edge_types": {
            "corrige": {},
            "extiende": {"valid_pairs": [["Decision", "Decision"], ["Plan", "Plan"]]},
            "aplica": {"valid_pairs": [["Task", "Decision"], ["Task", "Plan"]]},
        },
        "exclude": {"files": ["index.md"], "dirs": [".git"]},
    }
    if con_esquema:
        cfg["types"]["states"] = {"strict": True, "schemas": {
            "compromiso": {"types": ["Decision"], "initial": "propuesta",
                           "states": ["propuesta", "aceptada", "reemplazada"],
                           "replaced": "reemplazada", "pending": ["propuesta"]},
            "trabajo": {"types": ["Task", "Plan"], "initial": "pendiente",
                        "states": ["pendiente", "en-curso", "hecha", "cancelada"],
                        "pending": ["pendiente"], "dead": ["cancelada"]},
        }}
    if coherencia_strict is not None:
        cfg["graph"]["coherencia_strict"] = coherencia_strict
    return cfg


def _note(vault, rel, ctype, status, links=None):
    lines = ["---", f"type: {ctype}", f'title: "{Path(rel).stem}"',
             'description: "Nota de prueba para la coherencia del grafo."',
             f"timestamp: {TS}", f"created: {TS}", f"status: {status}"]
    if links:
        lines.append("links:")
        for target, etype in links:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {etype}")
    lines.append("---")
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n\nCuerpo.\n", encoding="utf-8")
    return p


# Los checks de health que dependen de la máquina (scripts, hook, git,
# plugins, red) se neutralizan para que el único warning posible sea el de
# coherencia: así el código de salida de --strict dice solo lo que probamos.
_LIMPIO = {
    "_check_indices": (0, []),
    "_check_broken_links": [],
    "_check_scripts": (1, []),
    "_check_git_hook": (True, None),
    "_check_cyber": (0, [], []),
    "_check_plugin_hash_sync": (0, []),
    "_check_timestamp_git": (0, [], []),
    "_check_version": {"local": "0.0.0", "latest": None, "up_to_date": None,
                       "source": "test"},
}


class TestCoherenciaEnStaleYHealth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self._escribir_config(_config())
        # Decisión vieja, corregida por la nueva
        _note(self.vault, "decisions/vieja.md", "Decision", "reemplazada")
        _note(self.vault, "decisions/nueva.md", "Decision", "aceptada",
              links=[("decisions/vieja.md", "corrige")])
        # Planes: uno cancelado sin sucesora, uno hecho
        _note(self.vault, "plans/cancelado.md", "Plan", "cancelada")
        _note(self.vault, "plans/hecho.md", "Plan", "hecha")
        # Tres apoyos rotos…
        _note(self.vault, "decisions/apoyada.md", "Decision", "aceptada",
              links=[("decisions/vieja.md", "extiende")])
        _note(self.vault, "tasks/t1.md", "Task", "pendiente",
              links=[("decisions/vieja.md", "aplica")])
        _note(self.vault, "plans/vivo.md", "Plan", "pendiente",
              links=[("plans/cancelado.md", "extiende")])
        # …y tres que no lo son: apoyo en una hecha, fuente cancelada,
        # `aplica` hacia una muerta (historia, no incoherencia).
        _note(self.vault, "plans/sigue.md", "Plan", "pendiente",
              links=[("plans/hecho.md", "extiende")])
        _note(self.vault, "tasks/t3.md", "Task", "cancelada",
              links=[("decisions/vieja.md", "aplica")])
        _note(self.vault, "tasks/t4.md", "Task", "pendiente",
              links=[("plans/cancelado.md", "aplica")])

    def tearDown(self):
        self.tmp.cleanup()

    def _escribir_config(self, cfg):
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        self.config = Config(self.vault)

    def _health(self, strict=False, json_out=True, config="propia"):
        cfg = self.config if config == "propia" else config
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for name, value in _LIMPIO.items():
                stack.enter_context(mock.patch.object(health, name, return_value=value))
            stack.enter_context(contextlib.redirect_stdout(out))
            rc = health.run(SimpleNamespace(strict=strict, json=json_out), self.vault, cfg)
        text = out.getvalue()
        return rc, (json.loads(text) if json_out else text)

    def _stale(self):
        return {r["file"]: r for r in stale.collect_stale(self.vault, config=self.config)}

    # ── la función reutilizable ──

    def test_apoyos_rotos_devuelve_las_tres_aristas_con_su_clase_y_vigente(self):
        rotos = apoyos_rotos(self.vault, self.config)
        self.assertEqual(set(rotos), {"decisions/apoyada.md", "tasks/t1.md", "plans/vivo.md"})
        apoyada = rotos["decisions/apoyada.md"][0]
        self.assertEqual((apoyada["type"], apoyada["clase"], apoyada["vigente"]),
                         ("extiende", "reemplazada", "decisions/nueva.md"))
        self.assertEqual(rotos["tasks/t1.md"][0]["clase"], "reemplazada")
        vivo = rotos["plans/vivo.md"][0]
        self.assertEqual((vivo["clase"], vivo["vigente"]), ("muerta", None))
        self.assertEqual(describe_apoyo(vivo),
                         "apoyo en muerta sin sucesora (extiende → plans/cancelado)")

    def test_sin_esquema_de_estados_no_hay_apoyos_rotos(self):
        self._escribir_config(_config(con_esquema=False))
        self.assertEqual(apoyos_rotos(self.vault, self.config), {})
        self.assertEqual(apoyos_rotos(self.vault, None), {})

    # ── stale ──

    def test_stale_senala_el_apoyo_en_reemplazada_con_su_vigente(self):
        r = self._stale()["decisions/apoyada.md"]
        self.assertIn("apoyo en reemplazada (extiende → decisions/vieja)", r["signals"])
        self.assertNotEqual(r["level"], "FRESH")
        self.assertEqual(r["details"]["apoyos_rotos"], [{
            "type": "extiende", "target": "decisions/vieja",
            "target_status": "reemplazada", "clase": "reemplazada",
            "vigente": "decisions/nueva"}])

    def test_stale_senala_aplica_hacia_reemplazada_y_apoyo_en_muerta(self):
        res = self._stale()
        self.assertIn("apoyo en reemplazada (aplica → decisions/vieja)",
                      res["tasks/t1.md"]["signals"])
        vivo = res["plans/vivo.md"]
        self.assertIn("apoyo en muerta sin sucesora (extiende → plans/cancelado)",
                      vivo["signals"])
        self.assertIsNone(vivo["details"]["apoyos_rotos"][0]["vigente"])

    def test_stale_no_senala_los_casos_limpios(self):
        res = self._stale()
        for rel in ("plans/sigue.md", "tasks/t3.md", "tasks/t4.md", "decisions/nueva.md"):
            self.assertFalse([s for s in res[rel]["signals"] if "apoyo" in s], rel)
            self.assertNotIn("apoyos_rotos", res[rel]["details"], rel)

    def test_stale_json_expone_la_senal(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = stale.run(SimpleNamespace(json=True), self.vault, self.config)
        self.assertEqual(rc, 0)
        por_archivo = {r["file"]: r for r in json.loads(out.getvalue())}
        self.assertIn("apoyo en reemplazada (extiende → decisions/vieja)",
                      por_archivo["decisions/apoyada.md"]["signals"])
        self.assertEqual(por_archivo["decisions/apoyada.md"]["details"]["apoyos_rotos"][0]["vigente"],
                         "decisions/nueva")

    def test_stale_sin_esquema_no_agrega_la_senal(self):
        self._escribir_config(_config(con_esquema=False))
        res = self._stale()
        self.assertFalse([s for s in res["decisions/apoyada.md"]["signals"] if "apoyo" in s])

    # ── health ──

    def test_health_json_cuenta_aristas_y_nodos_sin_contarlos_como_warning(self):
        rc, h = self._health()
        c = h["results"]["coherencia"]
        self.assertEqual((c["evaluada"], c["aristas"], c["nodos"], c["strict"]),
                         (True, 3, 3, False))
        self.assertEqual(len(c["details"]), 3)
        self.assertIn("decisions/apoyada: apoyo en reemplazada (extiende → decisions/vieja) "
                      "· vigente: decisions/nueva", c["details"])
        self.assertEqual(h["warnings"], 0)
        self.assertEqual(h["errors"], 0)
        self.assertEqual(rc, 0)

    def test_health_texto_muestra_la_seccion(self):
        _, text = self._health(json_out=False)
        self.assertIn("Coherencia: 3 apoyo(s) en reemplazada o muerta sin sucesora desde 3 nodo(s)",
                      text)
        self.assertIn("no cuenta en --strict", text)
        self.assertIn("apoyo en muerta sin sucesora (extiende → plans/cancelado)", text)
        self.assertIn("all clear", text)  # sigue sin warnings

    def test_health_strict_no_cambia_sin_config(self):
        rc, h = self._health(strict=True)
        self.assertEqual(rc, 0)
        self.assertEqual(h["warnings"], 0)
        self.assertFalse([w for w in h["warning_details"] if "coherencia" in w])

    def test_health_strict_falla_con_coherencia_strict_en_config(self):
        self._escribir_config(_config(coherencia_strict=True))
        self.assertTrue(self.config.graph_coherencia_strict)
        rc, h = self._health(strict=True)
        self.assertEqual(rc, 1)
        self.assertEqual(h["warnings"], 3)
        self.assertTrue(all(w.startswith("coherencia: ") for w in h["warning_details"]))
        self.assertTrue(h["results"]["coherencia"]["strict"])
        # Sin --strict el código de salida sigue siendo 0: la config solo
        # decide si el aviso cuenta, no convierte health en bloqueante.
        rc, _ = self._health(strict=False)
        self.assertEqual(rc, 0)
        _, text = self._health(json_out=False)
        self.assertIn("cuenta en --strict", text)

    def test_health_sin_esquema_ni_config_no_evalua(self):
        self._escribir_config(_config(con_esquema=False))
        rc, h = self._health(strict=True)
        c = h["results"]["coherencia"]
        self.assertEqual((c["evaluada"], c["aristas"], c["nodos"]), (False, 0, 0))
        self.assertEqual(rc, 0)
        rc, h = self._health(strict=True, config=None)
        self.assertFalse(h["results"]["coherencia"]["evaluada"])
        self.assertEqual(rc, 0)
        _, text = self._health(json_out=False, config=None)
        self.assertIn("Coherencia: no evaluada (sin esquema de estados)", text)


if __name__ == "__main__":
    unittest.main()
