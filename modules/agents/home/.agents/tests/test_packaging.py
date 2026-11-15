"""The pinned PKGBUILDs must agree with what the skill itself pins."""

import os
import re
import unittest
from pathlib import Path

SKILL = Path(os.path.abspath(__file__)).parents[1] / "skills" / "impeccable" / "scripts"
# Follow the arcus symlink back into the module to find pkg/; absent when only a copy is installed.
PKG = Path(os.path.abspath(__file__)).resolve().parents[3] / "pkg"


@unittest.skipUnless((PKG / "impeccable-engine/PKGBUILD").is_file(), "module pkg/ directory not reachable")
class PackagingTests(unittest.TestCase):
    def test_engine_package_matches_the_skill_version_and_checksum(self):
        text = (PKG / "impeccable-engine/PKGBUILD").read_text(encoding="utf-8")
        version = re.search(r"^pkgver=(\S+)$", text, re.M)[1]
        self.assertEqual(version, (SKILL / "VERSION").read_text().strip())
        digest = re.search(r"^sha256sums=\('([0-9a-f]{64})'", text, re.M)[1]
        recorded = {tuple(line.split()[:2]): line.split()[2] for line in
                    (SKILL / "engine-checksums.txt").read_text().splitlines()
                    if line.strip() and not line.startswith("#")}
        self.assertEqual(recorded[(version, "impeccable-linux-x64")], digest)

    def test_every_source_is_checksummed(self):
        for name in ("rtk", "impeccable-engine"):
            text = (PKG / name / "PKGBUILD").read_text(encoding="utf-8")
            sources = re.search(r"^source=\((.*?)\)", text, re.M | re.S)[1].count("::")
            sums = re.search(r"^sha256sums=\((.*?)\)", text, re.M | re.S)[1]
            self.assertEqual(len(re.findall(r"'[0-9a-f]{64}'", sums)), sources, name)
            self.assertNotIn("SKIP", sums, name)


if __name__ == "__main__":
    unittest.main()
