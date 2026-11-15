#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Smoke tests for humanize_check.py: format extraction, hard flags, preservation, scoring direction.

Run: uv run tests/test_checker.py
"""
import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import humanize_check as hc  # noqa: E402

AI_EN = """# Unlocking The Power Of Remote Work

In today's fast-paced digital world, remote work has become a crucial part of the modern landscape. It serves as a testament to how businesses can foster collaboration, enhance productivity, and unlock new opportunities.

Additionally, remote work plays a pivotal role in talent acquisition. Companies can leverage a diverse array of candidates, ensuring a robust and comprehensive hiring process. Moreover, it is important to note that flexibility can help employees thrive, highlighting the value of trust.

Furthermore, this isn't just about convenience; it's about building a resilient culture that can adapt to change. Ultimately, embracing remote work is key to navigating the evolving landscape of modern business.
"""

HUMAN_EN = """We moved to remote work in March 2021. Nobody asked me; the landlord raised the rent by 40% and that settled it.

The first month was a mess. Marta lost two clients because she kept missing calls (her router, not her fault). I worked from the kitchen table and ate too much bread.

Did it get better? Yes. By June we had a Slack channel for dumb jokes, a shared calendar, and a rule: no meetings before 10. Revenue went from 118k to 131k that year.

I still miss the office coffee.
"""

AI_FR = """# Libérer Le Potentiel Du Télétravail

Dans un monde en constante évolution, le télétravail est devenu un élément crucial du paysage professionnel. Il témoigne de la capacité des entreprises à favoriser la collaboration, optimiser la productivité et exploiter le potentiel des équipes.

En effet, il est important de noter que le télétravail joue un rôle clé dans le recrutement. Par ailleurs, il permet de bénéficier d'un vaste éventail de profils, garantissant un processus robuste et complet. De plus, la flexibilité peut aider les employés à s'épanouir.

Ce n'est pas seulement une question de confort, c'est une culture résiliente qui s'adapte au changement. En somme, adopter le télétravail est essentiel pour naviguer dans le paysage actuel.
"""

WORD_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Plan</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>Plain sentence about </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>bold</w:t></w:r><w:r><w:t> text.</w:t></w:r></w:p>'
    "</w:body></w:document>"
)


def args(**overrides):
    base = dict(lang="auto", profile="prose", threshold=hc.DEFAULT_THRESHOLD, plain=False, dump_text=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok  {message}")


def main() -> int:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "ai_en.md").write_text(AI_EN, encoding="utf-8")
        (root / "human_en.txt").write_text(HUMAN_EN, encoding="utf-8")
        (root / "ai_fr.md").write_text(AI_FR, encoding="utf-8")

        ai_en = hc.analyse(str(root / "ai_en.md"), args())
        human_en = hc.analyse(str(root / "human_en.txt"), args())
        ai_fr = hc.analyse(str(root / "ai_fr.md"), args())
        check(ai_en["score"] > human_en["score"] + 15, f"EN AI ({ai_en['score']}) scores well above human ({human_en['score']})")
        check(ai_en["verdict"].startswith("FAIL"), "EN AI sample fails")
        check(human_en["verdict"] == "PASS", "EN human sample passes")
        check(ai_fr["lang"] == "fr" and ai_fr["verdict"].startswith("FAIL"), f"FR AI sample detected as fr and fails ({ai_fr['score']})")

        # extraction
        with zipfile.ZipFile(root / "t.docx", "w") as archive:
            archive.writestr("word/document.xml", WORD_DOCUMENT)
        extracted = hc.extract_text(str(root / "t.docx"))
        check("# Plan" in extracted and "**bold**" in extracted, "docx headings and bold extracted")
        (root / "t.html").write_text("<html><body><h1>Title</h1><p>One <strong>two</strong></p><script>x=1</script></body></html>", encoding="utf-8")
        extracted = hc.extract_text(str(root / "t.html"))
        check("# Title" in extracted and "**two**" in extracted and "x=1" not in extracted, "html extracted, scripts dropped")
        (root / "t.tex").write_text("\\section{Intro}\nSome \\textbf{bold} text $x^2$.\n", encoding="utf-8")
        check("# Intro" in hc.extract_text(str(root / "t.tex")), "tex section extracted")
        with zipfile.ZipFile(root / "t.pptx", "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="x" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Hello slide</a:t></a:r></a:p></p:sld>')
        check("Hello slide" in hc.extract_text(str(root / "t.pptx")), "pptx slide text extracted")

        # hard flags
        (root / "residue.md").write_text("Here is a revised version of your text.\n\nThe cat sat.\u200b Please see [Your Name].", encoding="utf-8")
        residue = hc.analyse(str(root / "residue.md"), args())
        kinds = {f["signal"] for f in residue["findings"] if f["hard"]}
        check({"chatbot-residue", "invisible-chars"} <= kinds, f"hard flags raised: {sorted(kinds)}")
        check(residue["verdict"] == "FAIL (hard flags)", "hard flags force FAIL")

        # preservation
        original = "Revenue grew 12% to 4,500 euros. See https://example.com/a for Marta Dupont."
        (root / "orig.txt").write_text(original, encoding="utf-8")
        (root / "good.txt").write_text("Revenue rose 12% to 4,500 euros (https://example.com/a), says Marta Dupont.", encoding="utf-8")
        (root / "bad.txt").write_text("Revenue grew 15% to 4,500 euros.", encoding="utf-8")
        good = hc.analyse(str(root / "good.txt"), args(), original)
        bad = hc.analyse(str(root / "bad.txt"), args(), original)
        check(not [f for f in good["findings"] if f["signal"].startswith("preservation")], "faithful rewrite has no preservation findings")
        dropped = [f["snippet"] for f in bad["findings"] if f["signal"].startswith("preservation") and f["hard"]]
        check(len(dropped) >= 3, f"changed/dropped number and URL are hard findings: {dropped}")

        # technical profile switches rhythm signals off and plain checks on
        technical = hc.analyse(str(root / "human_en.txt"), args(profile="technical"))
        check(not any(s["name"] == "sentence-rhythm" for s in technical["signals"]), "technical profile disables rhythm signal")
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
