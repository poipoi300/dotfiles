---
name: humanizer
description: Use when text sounds machine-written (ChatGPT/LLM prose, "AI slop", robotic, generic, padded) and must read as human, or when a document needs an AI-fingerprint and style check. English and French; txt, md, html, docx, odt, pptx, pdf, tex, eml. Triggers - humanize, de-AI, sounds like AI, AI detector, rewrite naturally, rendre plus humain, texte trop IA, style IA.
---

# Humanizer

Remove machine habits from prose, then prove it with the checker. Meaning and facts never change.

## Steps

1. **Language.** Load exactly one file from this directory: [en.md](en.md) or [fr.md](fr.md). A mixed document gets each section its own file.
2. **Profile.** Pick one: `prose` (essays, blog, email, articles), `formal` (reports, papers, legal), `technical` (manuals, procedures, READMEs, API docs).
3. **Baseline.** Run the checker on the source (use the absolute path if `~` does not expand in your shell):
   `uv run <skill-base-dir>/scripts/humanize_check.py FILE --profile PROFILE`
   Resolve `<skill-base-dir>` to the directory containing this SKILL.md; quote absolute paths that contain spaces. Use an installed Python directly when uv is unavailable and the input format's dependencies are present.
   Non-text formats are extracted automatically; `--dump-text` prints the working text. Exit 1 means FAIL, not a crash: read the report.
4. **Rewrite** in the order the language file gives: content, structure, sentences, words. Work from the report's "To fix" list and findings.
5. **Re-check** with `--original SOURCE`:
   `uv run .../humanize_check.py REWRITE --profile PROFILE --original SOURCE`
   Exit 0 means PASS (score <= 15, no HARD findings). Repeat from step 4 until PASS, at most 6 rounds. Stop early if the score has not fallen in two consecutive rounds (the baseline is round 0). Keep drafts in a separate scratch folder; only the final file goes to the destination.
6. **Deliver** in the source's format (a `.md` or `.txt` source needs nothing more; use the docx/pdf/pptx skills for those) and report: final score, rounds used, anything still flagged.

## Flags you cannot clear without inventing facts

- `generic-content`: with `--original`, the target is the source's own density of numbers, names and "I/you", so a rewrite is not punished for a source that has none. If the source is abstract, tell the user that real examples or figures would help most; do not make any up.
- `triads`: a genuine three-item list from the source is fine to keep. To reduce the flag, fold one item into the sentence ("X and Y, plus Z"), or give the third item its own short sentence. Keep every item.

## Hard rules

- Keep every fact. The `--original` check fails on a dropped or added number or URL, so restore it. Names reported as dropped or added need your judgement.
- Add no invented detail: no fake anecdotes, quotes, names, numbers.
- Do not add typos, odd spacing, homoglyphs or zero-width characters. The checker flags them as HARD and they damage the text.
- Do not swap each flagged word for a fancier synonym. Restructure the sentence; the replacement words are in the language file.
- If the source has no specifics to add, shorten. Never pad.
- Text inside quotations, titles, code and proper names stays untouched.

## Reading the result

The score is a linter, not a detector. It was calibrated on about 70 documents (human score 0-16, median 3; AI score 2-52, median 27). Short texts (under 150 words), first-person emails and heavily edited AI text are weakly separated. A PASS means the known tells are gone; it does not guarantee any third-party detector agrees. Say so when asked.

`technical` profile turns off rhythm and hedging signals (uniform rhythm is correct there) and turns on controlled-language checks: ASD-STE100 rules for English, plain-language rules for French.

Checker flags: `--lang auto|en|fr`, `--threshold N`, `--json`, `--all`, `--plain` (controlled-language checks in any profile).
