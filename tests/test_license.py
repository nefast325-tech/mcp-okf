"""Test de coherencia de licencia: una sola licencia, declarada en tres lugares.

``LICENSE`` es el texto legal (fuente de verdad); ``pyproject.toml`` y el
README la declaran para que PyPI y GitHub la muestren. Antes de v0.4.34 el
repo decía MIT en pyproject y en el README pero no tenía ``LICENSE``, así que
GitHub no detectaba licencia (``licenseInfo: null``) y un fork quedaba sin
términos: el que lee el README ve MIT, el que clona el repo no ve nada.

Estos tests protegen el contrato:
- que ``LICENSE`` exista en la raíz y sea el texto MIT completo (OSI)
- que no quede como borrador (marcadores TODO/<year>/<copyright holders>)
- que la licencia declarada en ``pyproject.toml`` coincida con ``LICENSE``
- que el README enlace a ``LICENSE`` y no contradiga la declarada
"""

import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE = ROOT / "LICENSE"
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"

# Frases que solo aparecen en el texto MIT canónico (no en Apache/BSD/GPL).
MIT_MARKERS = (
    'Permission is hereby granted, free of charge, to any person obtaining a copy',
    'to deal in the Software without restriction',
    'The above copyright notice and this permission notice shall be included in all',
    'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND',
)


class LicenseCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.license_text = LICENSE.read_text(encoding="utf-8")
        with PYPROJECT.open("rb") as fh:
            self.project = tomllib.load(fh)["project"]
        self.readme_text = README.read_text(encoding="utf-8")

    def test_license_file_exists_at_root(self):
        self.assertTrue(
            LICENSE.is_file(),
            "falta LICENSE en la raíz: GitHub no detecta licencia y el fork "
            "queda sin términos aunque el README diga MIT.",
        )

    def test_license_is_canonical_mit(self):
        # El texto MIT canónico viene partido en líneas (~80 col): se compara
        # con los saltos normalizados, si no cualquier reflow rompe el test.
        flat = " ".join(self.license_text.split())
        for marker in MIT_MARKERS:
            self.assertIn(marker, flat, f"LICENSE no es el texto MIT: falta {marker!r}")

    def test_license_has_a_real_copyright_line(self):
        match = re.search(r"^Copyright \(c\) \d{4} .+$", self.license_text, re.MULTILINE)
        self.assertIsNotNone(match, "LICENSE sin línea de copyright ('Copyright (c) AAAA titular').")
        line = match.group(0) if match else ""
        for placeholder in ("<year>", "<copyright holders>", "TODO", "YYYY"):
            self.assertNotIn(placeholder, line)

    def test_pyproject_declares_the_same_license(self):
        declared = self.project.get("license")
        spdx = declared.get("text") if isinstance(declared, dict) else declared
        self.assertEqual(spdx, "MIT", f"pyproject declara {spdx!r} y LICENSE es MIT.")
        self.assertIn(
            "LICENSE",
            self.project.get("license-files", []),
            "license-files no incluye LICENSE: el texto no viaja en el wheel/sdist.",
        )

    def test_no_legacy_license_classifier(self):
        classifiers = self.project.get("classifiers", [])
        self.assertFalse(
            [c for c in classifiers if c.startswith("License ::")],
            "classifier 'License ::' junto a license = \"MIT\" (SPDX): setuptools>=77 "
            "rechaza la combinación.",
        )

    def test_readme_links_to_license_file(self):
        self.assertIn("[LICENSE](LICENSE)", self.readme_text, "el README no enlaza a LICENSE.")
        self.assertIn("MIT", self.readme_text)
        self.assertNotRegex(
            self.readme_text,
            r"##\s+License\s*\n+\s*(Apache|BSD|GPL|AGPL)",
            "el README declara otra licencia que LICENSE (MIT).",
        )


if __name__ == "__main__":
    unittest.main()
