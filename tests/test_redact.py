"""Tachado de secretos en new/edit (cli/redact.py).

Medido antes del cambio contra un servidor que sí tacha: `new` guardaba enteras
16 de 16 llaves falsas y `validate --all` salía en 0 con ellas adentro. Las
cadenas son inventadas: tienen la forma de una llave real y la palabra FALSA.

Se arman por partes (`_j`): en este archivo no hay ninguna cadena con la forma
completa de una llave, para que el escáner de secretos de un repositorio
público no las tome por reales.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from cli.commands.edit import run as edit_run
from cli.commands.new import run as new_run
from cli.config import Config
from cli.redact import MARCA, tachar

R = "CLAVEFALSADEPRUEBA"


def _j(*partes):
    return "".join(partes)


ANTHROPIC = _j("sk-", "ant-", "api03-", R, "0" * 28)
CLAVE_BD = _j("ClaveFalsa", "DePrueba", "2026")
URL_BD = _j("postgres://crm:", "{s}", "@db.interno:5432/crm")

# (regla esperada, cadena que no debe quedar, texto donde va)
SECRETOS = [
    ("anthropic", ANTHROPIC, "ANTHROPIC_API_KEY={s}"),
    ("openai", _j("sk-", "proj-", R, "0" * 22), "La llave del proyecto es {s}"),
    ("github", _j("gh", "p_", R, "0" * 18), "El token del bot es {s}"),
    ("github-pat", _j("github", "_pat_", R, "0" * 40), "Token de solo lectura: {s}"),
    ("aws", _j("AK", "IA", "FALSADEPRUEBA000"), "Llave de acceso del respaldo: {s}"),
    ("llave-privada", _j("MIIEclave", "FALSAdePRUEBA", "0" * 22),
     _j("-----BEGIN RSA ", "PRIVATE KEY-----", "\n{s}\n", "-----END RSA ", "PRIVATE KEY-----")),
    ("slack", _j("xo", "xb-", "0" * 12, "-", R), "El bot publica con {s}"),
    ("jwt", _j("ey", "JhbGciOiJIUzI1NiJ9", ".", "ey", "JzdWIiOiJwcnVlYmEifQ", ".", "FIRMAFALSADEPRUEBA0000"),
     "Sesión de prueba: {s}"),
    ("google", _j("AI", "zaSy", R, "0" * 15), "La llave de Maps es {s}"),
    ("stripe", _j("sk_", "live_", R, "0000"), "La pasarela cobra con {s}"),
    ("asignacion", CLAVE_BD, _j("DB_PASS", "WORD={s}")),
    ("credenciales-en-url", _j("ClaveFalsa", "DePrueba"), URL_BD),
    ("authorization", _j(R, "0" * 10), _j("Authorization: ", "Bearer {s}")),
    ("telegram", _j("123456789", ":AA", R, "0" * 15), "El bot avisa con {s}"),
    ("npm", _j("np", "m_", R, "0" * 18), "Publicamos con {s}"),
    ("contrasena-en-prosa", "Tucan-Verde-2026", "La contraseña del panel es {s} y la tiene el gerente."),
]

# Lo que NO es un secreto y no se toca.
LIMPIOS = [
    "El vault quedó congelado en cd1dce5f0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    "La sesión de trabajo fue 927765cb-ce10-41d8-943b-9ea093602771",
    "La contraseña del panel se rota cada año y la guarda el gerente.",
    "password: obligatoria",
    "la clave es escuchar al cliente antes de proponer",
    "El enlace del anuncio lleva utm_medium=paid",
    "Cargá la llave con ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY} o api_key=<tu_llave_aca>",
    "El token se rota cada año; la contraseña es responsabilidad del gerente.",
]


class TestTachar(unittest.TestCase):
    def test_cada_formato_se_tacha_y_dice_que_regla(self):
        for regla, secreto, molde in SECRETOS:
            with self.subTest(regla=regla):
                limpio, reglas = tachar(molde.format(s=secreto))
                self.assertNotIn(secreto, limpio)
                self.assertIn(MARCA, limpio)
                self.assertIn(regla, reglas)

    def test_lo_que_no_es_secreto_vuelve_identico(self):
        for texto in LIMPIOS:
            with self.subTest(texto=texto[:40]):
                self.assertEqual(tachar(texto), (texto, []))

    def test_dos_reglas_sobre_la_misma_cadena_dejan_un_solo_tachon(self):
        # `API_KEY=` (asignación) y `sk-ant-…` (anthropic) ven el mismo valor. Aplicadas en
        # cadena dejan «[tachado] tachado]»; fundidas, una marca y nada más.
        limpio, reglas = tachar(f"Para no buscarla: ANTHROPIC_API_KEY={ANTHROPIC}")
        self.assertEqual(limpio, f"Para no buscarla: ANTHROPIC_API_KEY={MARCA}")
        self.assertEqual(len(reglas), 1)

    def test_conserva_lo_que_rodea_al_secreto(self):
        limpio, _ = tachar(URL_BD.format(s=_j("ClaveFalsa", "DePrueba")) + " tiene el embudo.")
        self.assertEqual(limpio, URL_BD.format(s=MARCA) + " tiene el embudo.")
        limpio, _ = tachar("La contraseña del panel es Tucan-Verde-2026.")
        self.assertEqual(limpio, f"La contraseña del panel es {MARCA}.")

    def test_tachar_dos_veces_no_cambia_nada(self):
        una, _ = tachar(_j("DB_PASS", "WORD=", CLAVE_BD, " y token ", ANTHROPIC))
        self.assertEqual(tachar(una), (una, []))

    def test_lo_que_no_es_texto_vuelve_tal_cual(self):
        self.assertEqual(tachar(None), (None, []))
        self.assertEqual(tachar(""), ("", []))


class VaultBase(unittest.TestCase):
    CONFIG = {"types": {"valid": ["Insight"]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / ".okf.config.yaml").write_text(yaml.safe_dump(self.CONFIG), encoding="utf-8")
        self.config = Config(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def _correr(self, run, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run(args, self.vault, self.config)
        return rc, out.getvalue(), err.getvalue()

    def _new(self, **kw):
        base = dict(concept_type="Insight", title="Una nota", description="Una línea.", tags=None, status=None,
                    resource=None, cyber=False, dry_run=False, body=None, body_file=None, links=None,
                    entity=None, filename=None, omit_timestamps=False, fields=None, force=False)
        base.update(kw)
        return self._correr(new_run, SimpleNamespace(**base))

    def _edit(self, slug, **kw):
        base = dict(slug=slug, title=None, description=None, tags=None, status=None, resource=None, body=None,
                    body_file=None, links=None, clear_links=False, dry_run=False, fields=None, base_revision=None)
        base.update(kw)
        return self._correr(edit_run, SimpleNamespace(**base))

    def _nota(self):
        notas = [p for p in self.vault.rglob("*.md")]
        self.assertEqual(len(notas), 1, notas)
        return notas[0].read_text(encoding="utf-8")


class TestNewTacha(VaultBase):
    def test_el_cuerpo_se_guarda_sin_la_llave_y_la_salida_lo_dice(self):
        rc, out, _ = self._new(body=f"Cargamos la llave: ANTHROPIC_API_KEY={ANTHROPIC}")
        self.assertEqual(rc, 0)
        self.assertNotIn(ANTHROPIC, self._nota())
        self.assertIn(f"ANTHROPIC_API_KEY={MARCA}", self._nota())
        self.assertIn("🔒 1 secreto tachado", out)
        self.assertIn("anthropic", out)
        self.assertNotIn(ANTHROPIC, out)

    def test_una_nota_limpia_no_imprime_aviso(self):
        rc, out, _ = self._new(body="Un aprendizaje sin secretos.")
        self.assertEqual(rc, 0)
        self.assertNotIn("🔒", out)

    def test_titulo_descripcion_y_campos_tambien(self):
        rc, out, _ = self._new(title=f"Token {ANTHROPIC}", description="La contraseña del panel es Tucan-Verde-2026.",
                               fields=[_j("api_", "key=", CLAVE_BD)], body="Sin nada.")
        self.assertEqual(rc, 0)
        nota = self._nota()
        for secreto in (ANTHROPIC, "Tucan-Verde-2026", CLAVE_BD):
            self.assertNotIn(secreto, nota)
        # el nombre del archivo sale del título ya tachado
        self.assertNotIn(R.lower(), next(self.vault.rglob("*.md")).name)
        self.assertIn("🔒 3 secretos tachados", out)

    def test_el_cuerpo_que_llega_por_archivo(self):
        f = self.vault / "cuerpo.txt"
        f.write_text(_j("El bot publica con ", "xo", "xb-", "0" * 12, "-", R), encoding="utf-8")
        rc, _, _ = self._new(body_file=str(f))
        f.unlink()
        self.assertEqual(rc, 0)
        self.assertNotIn(R, self._nota())

    def test_los_controles_quedan_enteros(self):
        cuerpo = "\n".join(LIMPIOS)
        rc, out, _ = self._new(body=cuerpo)
        self.assertEqual(rc, 0)
        self.assertNotIn("🔒", out)
        for linea in LIMPIOS:
            self.assertIn(linea, self._nota())


class TestEditTacha(VaultBase):
    def test_edit_guarda_el_cuerpo_sin_la_llave(self):
        self._new(body="Cuerpo inicial.")
        slug = str(next(self.vault.rglob("*.md")).relative_to(self.vault))
        rc, out, _ = self._edit(slug, body=_j("DB_PASS", "WORD=", CLAVE_BD, "\nToken: ", ANTHROPIC))
        self.assertEqual(rc, 0)
        nota = self._nota()
        self.assertNotIn(CLAVE_BD, nota)
        self.assertNotIn(ANTHROPIC, nota)
        self.assertIn("🔒 2 secretos tachados", out)


class TestApagado(VaultBase):
    CONFIG = {"types": {"valid": ["Insight"]}, "writes": {"redact": False}}

    def test_con_writes_redact_false_se_guarda_lo_que_llega(self):
        self.assertFalse(self.config.writes_redact)
        rc, out, _ = self._new(body=f"ANTHROPIC_API_KEY={ANTHROPIC}")
        self.assertEqual(rc, 0)
        self.assertIn(ANTHROPIC, self._nota())
        self.assertNotIn("🔒", out)


class TestTraza(unittest.TestCase):
    def test_los_parametros_que_van_a_la_traza_salen_tachados(self):
        import server
        params = {"title": f"Token {ANTHROPIC}", "body": _j("DB_PASS", "WORD=", CLAVE_BD, "..."),
                  "fields": [_j("api_", "key=", ANTHROPIC)], "cyber": False, "links": None}
        limpio = server._sin_secretos(params)
        self.assertNotIn(ANTHROPIC, str(limpio))
        self.assertNotIn(CLAVE_BD, str(limpio))
        self.assertIs(limpio["cyber"], False)
        self.assertIsNone(limpio["links"])


if __name__ == "__main__":
    unittest.main()
