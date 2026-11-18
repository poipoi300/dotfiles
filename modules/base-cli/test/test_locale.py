"""The module's /etc/locale.conf may only name UTF-8 locales that glibc supports
and that the module's /etc/locale.gen enables.

An unknown or ungenerated value (e.g. LC_MEASUREMENT=metric) makes every perl
and C program warn "Setting locale failed" and fall back.
Run from the repo root: python3 -I -B -m unittest discover -s modules/base-cli/test
"""
import pathlib
import unittest

ETC = pathlib.Path(__file__).resolve().parent.parent / "system/etc"
SUPPORTED = pathlib.Path("/usr/share/i18n/SUPPORTED")


def entries(path: pathlib.Path) -> list[str]:
    """Non-blank, non-comment lines, stripped."""
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def first_fields(path: pathlib.Path) -> set[str]:
    return {line.split()[0] for line in entries(path)}


class LocaleConf(unittest.TestCase):
    def setUp(self):
        self.values = {}
        for line in entries(ETC / "locale.conf"):
            key, _, value = line.partition("=")
            self.values[key] = value

    def test_keys_are_locale_variables(self):
        for key in self.values:
            self.assertTrue(key == "LANG" or key.startswith("LC_"), f"unexpected key {key}")

    def test_values_are_utf8(self):
        for key, value in self.values.items():
            self.assertTrue(value.endswith(".UTF-8"), f"{key}={value} is not a .UTF-8 locale")

    def test_values_are_enabled_in_locale_gen(self):
        enabled = first_fields(ETC / "locale.gen")
        for key, value in self.values.items():
            self.assertIn(value, enabled, f"{key}={value} is not enabled in the module's locale.gen")

    @unittest.skipUnless(SUPPORTED.exists(), f"{SUPPORTED} missing (glibc i18n data)")
    def test_values_are_supported_by_glibc(self):
        known = first_fields(SUPPORTED)
        for key, value in self.values.items():
            self.assertIn(value, known, f"{key}={value} is not in {SUPPORTED}")


if __name__ == "__main__":
    unittest.main()
