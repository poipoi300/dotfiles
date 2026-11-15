#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf>=4"]
# ///
"""AI-fingerprint and style checker for the humanizer skill.

Reads txt/md/rst/html/docx/odt/pptx/tex/eml/pdf (or stdin with "-"), scores how
much the prose looks machine-written (0 = human-like, 100 = obvious AI), lists
the offending spans with line numbers, and exits 1 when the text fails.

It is a heuristic linter, not a real detector: passing here does not guarantee
that a third-party detector agrees.
"""
from __future__ import annotations

import argparse
import bisect
import email
import email.policy
import html.parser
import importlib
import json
import re
import statistics
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_THRESHOLD = 15.0  # calibrated: human prose scored 0-16 (median 3), AI prose 2-52 (median 27)
MIN_WORDS_FOR_STATS = 60
MIN_SENTENCES_FOR_STATS = 8
DENSITY_FLOOR_WORDS = 300  # densities are computed over at least this many words

# --------------------------------------------------------------------------
# Text extraction: every format becomes light markdown (#, -, **bold**, | rows)
# --------------------------------------------------------------------------

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    parent_of = {child: parent for parent in root.iter() for child in parent}
    lines: list[str] = []
    for paragraph in root.iter(f"{W_NS}p"):
        style_node = paragraph.find(f"{W_NS}pPr/{W_NS}pStyle")
        style = style_node.get(f"{W_NS}val", "") if style_node is not None else ""
        in_table = any(
            parent_of.get(ancestor) is not None and ancestor.tag == f"{W_NS}tc"
            for ancestor in _ancestors(paragraph, parent_of)
        )
        is_list = paragraph.find(f"{W_NS}pPr/{W_NS}numPr") is not None
        pieces: list[tuple[str, bool]] = []
        for run in paragraph.iter(f"{W_NS}r"):
            bold_node = run.find(f"{W_NS}rPr/{W_NS}b")
            bold = bold_node is not None and bold_node.get(f"{W_NS}val", "true") not in ("0", "false")
            text = "".join(
                " " if node.tag == f"{W_NS}tab" else ("\n" if node.tag == f"{W_NS}br" else (node.text or ""))
                for node in run
                if node.tag in (f"{W_NS}t", f"{W_NS}tab", f"{W_NS}br")
            )
            if text:
                pieces.append((text, bold))
        if not pieces:
            lines.append("")
            continue
        all_bold = all(bold for _, bold in pieces)
        body = "".join(
            f"**{text.strip()}** " if bold and not all_bold else text for text, bold in pieces
        ).strip()
        heading = re.match(r"(?:Heading|Titre)\s*(\d)", style, re.I)
        if style.lower() == "title":
            lines.append(f"# {body}")
        elif heading:
            lines.append(f"{'#' * int(heading.group(1))} {body}")
        elif in_table:
            lines.append(f"| {body}")
        elif is_list:
            lines.append(f"- {body}")
        else:
            lines.append(body)
        lines.append("")
    return "\n".join(lines)


def _ancestors(node, parent_of):
    while node in parent_of:
        node = parent_of[node]
        yield node


def extract_pptx(path: Path) -> str:
    out: list[str] = []
    with zipfile.ZipFile(path) as archive:
        slides = sorted(
            (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)\.xml", n).group(1)),
        )
        for number, name in enumerate(slides, 1):
            root = ET.fromstring(archive.read(name))
            out.append(f"## Slide {number}\n")
            for paragraph in root.iter(f"{A_NS}p"):
                text = "".join(t.text or "" for t in paragraph.iter(f"{A_NS}t")).strip()
                if text:
                    out.append(f"- {text}")
            out.append("")
    return "\n".join(out)


def extract_odt(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    out: list[str] = []
    for node in root.iter():
        if node.tag == f"{TEXT_NS}h":
            level = int(node.get(f"{TEXT_NS}outline-level", "1"))
            out.append(f"{'#' * level} {''.join(node.itertext()).strip()}\n")
        elif node.tag == f"{TEXT_NS}p":
            out.append(f"{''.join(node.itertext()).strip()}\n")
    return "\n".join(out)


class _HtmlToMarkdown(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "head", "svg", "nav", "footer"}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "ul", "ol", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif re.fullmatch(r"h[1-6]", tag) or tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(re.sub(r"\s+", " ", data))


def extract_html(raw: str) -> str:
    parser = _HtmlToMarkdown()
    parser.feed(raw)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_tex(raw: str) -> str:
    text = re.sub(r"(?<!\\)%.*", "", raw)
    text = re.sub(r"\\begin\{(equation|align|figure|table|tabular|lstlisting|verbatim)\*?\}.*?\\end\{\1\*?\}", " ", text, flags=re.S)
    text = re.sub(r"\$\$.*?\$\$|\$[^$\n]+\$", " ", text, flags=re.S)
    text = re.sub(r"\\(?:sub)*section\*?\{([^}]*)\}", r"\n\n# \1\n\n", text)
    text = re.sub(r"\\(?:textbf|textit|emph|cite[a-z]*|ref|label|footnote)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})*", " ", text)
    return re.sub(r"[{}]", "", text)


def extract_eml(path: Path) -> str:
    message = email.message_from_binary_file(path.open("rb"), policy=email.policy.default)
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    content = body.get_content()
    return extract_html(content) if body.get_content_type() == "text/html" else content


def extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("PDF support needs pypdf: run with `uv run` (script declares it) or `pip install pypdf`.")
    pages = [page.extract_text() or "" for page in PdfReader(str(path)).pages]
    text = "\n\n".join(pages)
    text = re.sub(r"-\n(?=[a-zà-ÿ])", "", text)  # de-hyphenate line breaks
    return re.sub(r"(?<![.!?:;\n])\n(?!\n)", " ", text)  # unwrap hard-wrapped lines


def extract_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        return extract_docx(file_path)
    if suffix == ".pptx":
        return extract_pptx(file_path)
    if suffix in (".odt", ".odp"):
        return extract_odt(file_path)
    if suffix == ".pdf":
        return extract_pdf(file_path)
    if suffix == ".eml":
        return extract_eml(file_path)
    if suffix in (".rtf", ".doc", ".xls", ".xlsx", ".ppt", ".pages", ".key"):
        raise SystemExit(f"{suffix} is not supported; convert to .docx, .odt, .pdf or .txt first.")
    raw = file_path.read_text(encoding="utf-8-sig", errors="replace")
    if suffix in (".html", ".htm", ".xhtml"):
        return extract_html(raw)
    if suffix == ".tex":
        return extract_tex(raw)
    return raw


# --------------------------------------------------------------------------
# Document model
# --------------------------------------------------------------------------

WORD = re.compile(r"\w+(?:['\u2019-]\w+)*", re.U)
URL = re.compile(r"https?://[^\s)>\]]+|www\.[^\s)>\]]+|[\w.+-]+@[\w-]+\.[\w.-]+")
FRONT_MATTER = re.compile(r"\A---\n.*?\n---[ \t]*\n", re.S)
FENCE = re.compile(r"^(```|~~~)[^\n]*\n.*?^\1[^\n]*$", re.S | re.M)
LIST_LINE = re.compile(r"^\s*(?:[-*+\u2022]|\d{1,3}[.)])\s+")
INLINE_HEADER_BULLET = re.compile(r"^\s*(?:[-*+\u2022]|\d{1,3}[.)])\s+\*\*[^*\n]{1,60}\*\*\s*[:\u2013\u2014-]", re.M)
BOLD = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2705\u274C\u2728]")
INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad\u202a-\u202e\u2066-\u2069]")
LATIN_CYRILLIC_MIX = re.compile(r"\b(?=\w*[a-zA-Z])(?=\w*[\u0400-\u04ff])\w+\b")
EM_DASH = re.compile(r"\u2014|\u2013| -- | --- ")


def blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def normalise(text: str) -> str:
    """Length-preserving normalisation so match offsets stay valid."""
    return (
        text.replace("\r\n", "\n").replace("\r", "\n")
        .replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")
        .replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")
    )


def mask_nonprose(text: str) -> str:
    text = FRONT_MATTER.sub(blank, text, count=1)
    text = FENCE.sub(blank, text)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    text = re.sub(r"`[^`\n]+`", blank, text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", blank, text)
    text = re.sub(r"(?<=\])\([^)\s]*\)", blank, text)
    return URL.sub(blank, text)


@dataclass
class Finding:
    signal: str
    line: int
    snippet: str
    hint: str = ""
    hard: bool = False
    weight: float = 1.0


@dataclass
class SignalResult:
    name: str
    weight: float
    severity: float
    value: str
    applicable: bool = True

    @property
    def points(self) -> float:
        return self.weight * self.severity if self.applicable else 0.0


@dataclass
class Document:
    path: str
    lang: str
    profile: str
    raw: str
    scan: str  # normalised + masked, same length/offsets as raw
    line_starts: list[int]
    paragraphs: list[tuple[int, str]]  # (line number, text) of running prose
    headings: list[tuple[int, str]]
    sentences: list[str]
    word_count: int
    is_markdown_like: bool

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)

    def snippet(self, start: int, end: int, pad: int = 28) -> str:
        left, right = max(0, start - pad), min(len(self.scan), end + pad)
        return re.sub(r"\s+", " ", self.scan[left:right]).strip()


def detect_language(text: str) -> str:
    sample = text.lower()
    french = len(re.findall(r"\b(?:le|la|les|des|une|est|et|que|qui|dans|pour|pas|sur|avec|nous|vous|ce|cette|au|aux|du|ne|se|ses|son|sont|mais|plus)\b", sample))
    english = len(re.findall(r"\b(?:the|and|of|to|is|in|that|it|for|with|as|was|are|this|be|on|by|not|have|but|they|from)\b", sample))
    accents = len(re.findall(r"[éèêàçùôîâœ]", sample))
    return "fr" if french + accents * 0.3 > english else "en"


def build_document(path: str, text: str, lang: str, profile: str, abbreviations: list[str]) -> Document:
    raw = normalise(text)
    scan = mask_nonprose(raw)
    line_starts = [0]
    for match in re.finditer(r"\n", scan):
        line_starts.append(match.end())

    paragraphs: list[tuple[int, str]] = []
    headings: list[tuple[int, str]] = []
    current: list[str] = []
    current_line = 0
    previous_was_list = False

    def flush() -> None:
        nonlocal current
        if current:
            paragraphs.append((current_line, " ".join(current)))
            current = []

    for number, line in enumerate(scan.split("\n"), 1):
        stripped = line.strip()
        if not stripped:
            flush()
            previous_was_list = False
        elif stripped.startswith("#"):
            flush()
            headings.append((number, stripped.lstrip("#").strip()))
            previous_was_list = False
        elif LIST_LINE.match(line):
            flush()
            previous_was_list = True
        elif stripped[0] in "|>" or re.fullmatch(r"[-*_=]{3,}", stripped):
            flush()
        elif previous_was_list and line.startswith("  "):
            continue
        else:
            previous_was_list = False
            if not current:
                current_line = number
            current.append(stripped)
    flush()

    sentences: list[str] = []
    for _, paragraph in paragraphs:
        sentences.extend(split_sentences(paragraph, abbreviations))
    word_count = sum(len(WORD.findall(paragraph)) for _, paragraph in paragraphs)
    if word_count == 0:  # headings-only or list-only document: count everything
        word_count = len(WORD.findall(scan))
    is_markdown_like = bool(re.search(r"^#{1,6} |\*\*|^\s*[-*] ", raw, re.M))
    return Document(path, lang, profile, raw, scan, line_starts, paragraphs, headings, sentences, word_count, is_markdown_like)


def split_sentences(paragraph: str, abbreviations: list[str]) -> list[str]:
    protected = re.sub(
        r"\b(" + "|".join(re.escape(a) for a in abbreviations) + r")\.", lambda m: m.group(1) + "\0", paragraph, flags=re.I
    )
    protected = re.sub(r"\b([A-Z])\.(?=\s?[A-Z])", "\\1\0", protected)
    parts = re.split(r"(?<=[.!?\u2026])[\"')\]\u00bb\u201d]*\s+(?=[\"\u201c\u00ab(\[]?[A-Z\u00c0-\u00de0-9])", protected)
    return [p.replace("\0", ".").strip() for p in parts if WORD.search(p)]


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

def ramp(value: float, low: float, high: float) -> float:
    if high == low:
        return 1.0 if value >= high else 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def effective_words(doc: Document) -> float:
    return max(doc.word_count, DENSITY_FLOOR_WORDS)


def scan_regex(doc: Document, pattern: str, signal: str, hint: str = "", weight: float = 1.0, hard: bool = False,
               flags: int = re.I | re.M) -> list[Finding]:
    findings = []
    for match in re.finditer(pattern, doc.scan, flags):
        findings.append(Finding(signal, doc.line_of(match.start()), doc.snippet(match.start(), match.end()), hint, hard, weight))
    return findings


def compute(doc: Document, lex, profile_cfg: dict, baseline_concreteness: float | None = None) -> tuple[list[SignalResult], list[Finding], dict]:
    findings: list[Finding] = []
    signals: list[SignalResult] = []
    stats: dict = {}
    n_eff = effective_words(doc)
    per_1k = lambda count: count / n_eff * 1000  # noqa: E731
    enough_for_stats = (
        doc.word_count >= MIN_WORDS_FOR_STATS and len(doc.sentences) >= MIN_SENTENCES_FOR_STATS
    )

    # --- hard flags -----------------------------------------------------
    for pattern in lex.CHATBOT_RESIDUE:
        findings += scan_regex(doc, pattern, "chatbot-residue", "remove: assistant wrapper or tool residue", hard=True)
    for match in INVISIBLE.finditer(doc.raw[1:] if doc.raw.startswith("\ufeff") else doc.raw):
        findings.append(Finding("invisible-chars", doc.line_of(match.start()), repr(match.group(0)), "zero-width/bidi/soft-hyphen characters: remove", hard=True))
    for match in LATIN_CYRILLIC_MIX.finditer(doc.raw):
        findings.append(Finding("homoglyphs", doc.line_of(match.start()), match.group(0), "mixed Latin/Cyrillic letters in one word", hard=True))

    # --- vocabulary ------------------------------------------------------
    vocab_hits: list[tuple[int, int, int, str]] = []  # (start, end, tier, plain)
    for pattern, tier, plain in lex.VOCAB:
        for match in re.finditer(rf"\b(?:{pattern})\b", doc.scan, re.I):
            vocab_hits.append((match.start(), match.end(), tier, plain or ""))
    vocab_hits.sort()
    weighted = sum(1.0 if tier == 1 else 0.35 for _, _, tier, _ in vocab_hits)
    for start, end, tier, plain in vocab_hits:
        if tier == 1 or per_1k(len([h for h in vocab_hits if h[2] == 2])) > 8:
            findings.append(Finding("ai-vocabulary", doc.line_of(start), doc.snippet(start, end, 20), f"-> {plain}" if plain else "", weight=1.0 if tier == 1 else 0.35))
    signals.append(SignalResult("vocabulary", 11, ramp(per_1k(weighted), 1.5, 9.0), f"{per_1k(weighted):.1f} weighted hits/1k words"))

    tier1_starts = [start for start, _, tier, _ in vocab_hits if tier == 1]
    window_best = 0
    for index, start in enumerate(tier1_starts):
        in_window = [s for s in tier1_starts[index:] if s - start <= 900]  # ~150 words
        window_best = max(window_best, len(in_window))
    signals.append(SignalResult("vocab-cluster", 4, ramp(window_best, 1, 4), f"{window_best} tier-1 words within ~150 words"))

    # --- stock phrases ---------------------------------------------------
    phrase_weight = 0.0
    for pattern, weight, hint in lex.PHRASES:
        hits = scan_regex(doc, pattern, "stock-phrase", hint, weight)
        phrase_weight += weight * len(hits)
        findings += hits
    signals.append(SignalResult("stock-phrases", 12, ramp(per_1k(phrase_weight), 1.0, 6.0), f"{per_1k(phrase_weight):.1f} weighted hits/1k words"))

    # --- structural patterns --------------------------------------------
    negative = []
    for pattern in lex.NEGATIVE_PARALLELISM:
        negative += scan_regex(doc, pattern, "negative-parallelism", "state the point; drop the 'not X but Y' frame")
    seen_spans = {(f.line, f.snippet[:20]) for f in negative}
    negative = [f for i, f in enumerate(negative) if (f.line, f.snippet[:20]) not in {(g.line, g.snippet[:20]) for g in negative[:i]}]
    findings += negative
    signals.append(SignalResult("negative-parallelism", 8, ramp(per_1k(len(negative)), 0.5, 3.5), f"{len(negative)} hits"))

    riders = scan_regex(doc, lex.PARTICIPLE_RIDERS, "participle-rider", "end the sentence; make the idea its own sentence or cut it")
    findings += riders
    signals.append(SignalResult("participle-riders", 5, ramp(per_1k(len(riders)), 0.5, 4.0), f"{len(riders)} hits"))

    copula = scan_regex(doc, lex.COPULA_AVOIDANCE, "copula-avoidance", "use is / are / has")
    findings += copula
    signals.append(SignalResult("copula-avoidance", 3, ramp(per_1k(len(copula)), 0.5, 3.5), f"{len(copula)} hits"))

    opener_re = re.compile(rf"^\W*(?:{lex.TRANSITIONS})\b", re.I)
    transition_count = sum(1 for s in doc.sentences if opener_re.match(s))
    transition_share = transition_count / max(1, len(doc.sentences))
    if profile_cfg["transitions"]:
        for _, paragraph in doc.paragraphs:
            for sentence in split_sentences(paragraph, lex.ABBREVIATIONS):
                if opener_re.match(sentence):
                    findings.append(Finding("transition-opener", _line_for(doc, sentence), sentence[:50], "drop the connector or merge the sentences", weight=0.5))
    signals.append(SignalResult("transition-openers", 5, ramp(transition_share, *profile_cfg["transition_ramp"]) if enough_for_stats and profile_cfg["transitions"] else 0.0,
                                f"{transition_share:.0%} of sentences", applicable=enough_for_stats and profile_cfg["transitions"]))

    dash_count = len(EM_DASH.findall(doc.scan))
    signals.append(SignalResult("dashes", 2, ramp(per_1k(dash_count), 2.0, 9.0), f"{per_1k(dash_count):.1f}/1k words", applicable=profile_cfg["dashes"]))
    if profile_cfg["dashes"] and per_1k(dash_count) > 4:
        for match in EM_DASH.finditer(doc.scan):
            findings.append(Finding("dash", doc.line_of(match.start()), doc.snippet(match.start(), match.end(), 20), "period, comma, colon or parentheses", weight=0.25))

    triad = re.compile(
        rf"\b[^\W\d_]+(?: [^\W\d_]+){{0,2}}, [^\W\d_]+(?: [^\W\d_]+){{0,2}},? {lex.TRIAD_CONJUNCTION} [^\W\d_]+(?: [^\W\d_]+){{0,2}}\b", re.I | re.U)
    triad_sentences = [s for s in doc.sentences if triad.search(s)]
    triad_share = len(triad_sentences) / max(1, len(doc.sentences))
    for sentence in triad_sentences[:8]:
        findings.append(Finding("triad", _line_for(doc, sentence), triad.search(sentence).group(0), "three-item list: keep only if there really are three", weight=0.25))
    signals.append(SignalResult("triads", 5, ramp(triad_share, *lex.TRIAD_RAMP) if enough_for_stats else 0.0, f"{triad_share:.0%} of sentences", applicable=enough_for_stats))

    rhetorical = []
    rhetorical += scan_regex(doc, r"(?:^|(?<=[.!?] ))[A-ZÀ-Ý][^.!?\n]{3,45}\? [A-ZÀ-Ý][^.!?\n]{2,40}[.!]", "question-answer", "ask-and-answer setup: state it directly", flags=re.M)
    short_run = 0
    for sentence in doc.sentences:
        if len(WORD.findall(sentence)) <= 4:
            short_run += 1
            if short_run == 3:
                rhetorical.append(Finding("staccato-fragments", _line_for(doc, sentence), sentence[:50], "three or more fragments in a row: merge into one claim"))
        else:
            short_run = 0
    findings += rhetorical
    signals.append(SignalResult("rhetorical-staging", 2, ramp(per_1k(len(rhetorical)), 0.5, 3.0), f"{len(rhetorical)} hits"))

    closer_re = re.compile(lex.CLOSERS, re.I)
    closer_hit = bool(doc.paragraphs) and len(doc.paragraphs) >= 3 and bool(closer_re.match(doc.paragraphs[-1][1]))
    if closer_hit:
        findings.append(Finding("summary-closer", doc.paragraphs[-1][0], doc.paragraphs[-1][1][:60], "end on the last concrete fact, not a recap", weight=0.5))
    signals.append(SignalResult("summary-closer", 2, 1.0 if closer_hit else 0.0, "yes" if closer_hit else "no"))

    # --- formatting ------------------------------------------------------
    bold_count = len(BOLD.findall(doc.scan))
    inline_headers = len(INLINE_HEADER_BULLET.findall(doc.scan))
    emoji_count = len(EMOJI.findall(doc.scan))
    title_case = sum(1 for _, h in doc.headings if _is_title_case(h))
    title_case_share = title_case / len(doc.headings) if len(doc.headings) >= 2 else 0.0
    heading_density = len(doc.headings) / max(1, doc.word_count) * 100
    format_sev = (
        ramp(per_1k(bold_count), 3, 12) * 0.9
        + ramp(inline_headers, 1, 4)
        + ramp(emoji_count, 0, 3)
        + ramp(title_case_share, 0.5, 1.0) * (1.0 if lex is not None and doc.lang == "en" else 0.6)
        + ramp(heading_density, 1.5, 4.0) * 0.6
    )
    if inline_headers >= 2:
        findings.append(Finding("inline-header-bullets", 0, f"{inline_headers} bullets shaped '- **Label:** text'", "turn into prose or plain list", weight=0.5))
    if bold_count and per_1k(bold_count) > 3:
        findings.append(Finding("bold-decoration", 0, f"{bold_count} bold spans", "remove bold unless it marks a term or warning", weight=0.5))
    if emoji_count:
        findings.append(Finding("emoji", 0, f"{emoji_count} emoji", "remove emoji used as formatting", weight=0.5))
    if title_case_share >= 0.5:
        findings.append(Finding("title-case-headings", 0, f"{title_case}/{len(doc.headings)} headings", "use sentence case" if doc.lang == "en" else "casse de phrase (pas de Title Case)", weight=0.5))
    signals.append(SignalResult("formatting", 7, min(1.0, format_sev / 1.8), f"bold {bold_count}, inline-header bullets {inline_headers}, emoji {emoji_count}, title-case headings {title_case}/{len(doc.headings)}"))

    # --- statistical rhythm ---------------------------------------------
    lengths = [len(WORD.findall(s)) for s in doc.sentences]
    if lengths:
        stats["sentences"] = len(lengths)
        stats["mean_sentence_words"] = round(statistics.fmean(lengths), 1)
    if enough_for_stats:
        mean_len = statistics.fmean(lengths)
        cv = statistics.pstdev(lengths) / mean_len if mean_len else 0.0
        band_share = sum(14 <= n <= 26 for n in lengths) / len(lengths)
        stats["sentence_length_cv"] = round(cv, 2)
        stats["share_14_to_26_words"] = f"{band_share:.0%}"
        low, high = profile_cfg["burstiness_ramp"]
        use_rhythm = profile_cfg["burstiness"]
        signals.append(SignalResult("sentence-rhythm", 9, ramp(high - cv, 0, high - low) if use_rhythm else 0.0,
                                    f"length variation {cv:.2f} (human prose usually >= {high:.2f})", applicable=use_rhythm))
        signals.append(SignalResult("sentence-length-band", 4, ramp(band_share, 0.42, 0.70) if use_rhythm else 0.0,
                                    f"{band_share:.0%} of sentences are 14-26 words (human usually < 45%)", applicable=use_rhythm))
        openers = [WORD.findall(s)[0].lower() for s in doc.sentences if WORD.findall(s)]
        repeats = sum(1 for a, b in zip(openers, openers[1:]) if a == b)
        repeat_share = repeats / max(1, len(openers) - 1)
        signals.append(SignalResult("repeated-openers", 2, ramp(repeat_share, 0.08, 0.30), f"{repeat_share:.0%} consecutive same-word starts"))
        sizes = [len(split_sentences(p, lex.ABBREVIATIONS)) for _, p in doc.paragraphs]
        if len(sizes) >= 4 and statistics.fmean(sizes):
            para_cv = statistics.pstdev(sizes) / statistics.fmean(sizes)
            stats["paragraph_length_cv"] = round(para_cv, 2)
            signals.append(SignalResult("paragraph-uniformity", 8, ramp(0.55 - para_cv, 0, 0.30) if use_rhythm else 0.0,
                                        f"paragraph size variation {para_cv:.2f} (human usually >= 0.55)", applicable=use_rhythm))
        hedge_share = sum(1 for s in doc.sentences if re.search(lex.HEDGES, s, re.I)) / len(doc.sentences)
        stats["hedged_sentences"] = f"{hedge_share:.0%}"
        signals.append(SignalResult("hedging", 5, ramp(hedge_share, 0.22, 0.55) if profile_cfg["hedging"] else 0.0,
                                    f"{hedge_share:.0%} of sentences hedge (can/may/often...)", applicable=profile_cfg["hedging"]))
        words_lower = [w.lower() for w in WORD.findall(" ".join(p for _, p in doc.paragraphs))]
        if len(words_lower) >= 100:
            window = 100
            ratios = [len(set(words_lower[i:i + window])) / window for i in range(0, len(words_lower) - window + 1, 25)]
            stats["lexical_diversity_mattr"] = round(statistics.fmean(ratios), 2)
        concrete = concreteness(doc, lex)
        stats["concreteness_per_1k"] = round(concrete)
        target = 60.0 if baseline_concreteness is None else min(60.0, baseline_concreteness)  # a rewrite cannot invent specifics the source lacks
        note = "" if baseline_concreteness is None else f" (target = source's own {baseline_concreteness:.0f}, capped at 60)"
        signals.append(SignalResult("generic-content", 8, ramp(target - concrete, 0, 45) if profile_cfg["concreteness"] else 0.0,
                                    f"{concrete:.0f} concrete markers/1k words (numbers, names, I/you, questions); human essays usually > 60{note}",
                                    applicable=profile_cfg["concreteness"]))
    else:
        stats["note"] = "too short for rhythm statistics; lexical signals only"
    return signals, findings, stats


def concreteness(doc: Document, lex) -> float:
    """Concrete markers per 1k words: numerals, mid-sentence capitalised names, 1st/2nd person, questions, contractions."""
    text = " ".join(p for _, p in doc.paragraphs)
    words = max(len(WORD.findall(text)), 1)
    numerals = len(re.findall(r"\d+", text))
    names = 0
    for sentence in doc.sentences:
        names += sum(1 for t in WORD.findall(sentence)[1:] if t[0].isupper() and len(t) > 1 and t != "I")
    persons = len(re.findall(lex.PERSON, text, 0 if doc.lang == "en" else re.I))
    questions = text.count("?")
    contractions = len(re.findall(lex.CONTRACTION, text)) if lex.CONTRACTION else 0
    return (numerals + names + persons + 2 * questions + contractions) * 1000 / words


def _is_title_case(heading: str) -> bool:
    words = [w for w in WORD.findall(heading) if len(w) > 3]
    if len(words) < 3:
        return False
    return sum(w[0].isupper() for w in words) / len(words) >= 0.75


def _line_for(doc: Document, sentence: str) -> int:
    probe = re.sub(r"\s+", " ", sentence)[:30]
    flat = re.sub(r"[ \t]+", " ", doc.scan)
    index = flat.find(probe)
    if index < 0:
        index = doc.scan.find(sentence[:15])
    return doc.line_of(max(index, 0))


# --------------------------------------------------------------------------
# Controlled-language ("plain") checks: ASD-STE100 condensed / langage clair
# --------------------------------------------------------------------------

def plain_checks(doc: Document, lex, strict: bool) -> list[Finding]:
    cfg = lex.PLAIN
    findings: list[Finding] = []
    for _, paragraph in doc.paragraphs:
        for sentence in split_sentences(paragraph, lex.ABBREVIATIONS):
            count = len(WORD.findall(sentence))
            if count > cfg["max_sentence_words"]:
                findings.append(Finding("plain-long-sentence", _line_for(doc, sentence), f"{count} words: {sentence[:50]}...", f"split it (max {cfg['max_sentence_words']} words)"))
        if len(split_sentences(paragraph, lex.ABBREVIATIONS)) > cfg["max_paragraph_sentences"]:
            findings.append(Finding("plain-long-paragraph", _line_for(doc, paragraph), paragraph[:50] + "...", f"max {cfg['max_paragraph_sentences']} sentences per paragraph"))
    for match in re.finditer(cfg["passive"], doc.scan, re.I):
        last_word = match.group(0).split()[-1].lower()
        if last_word in cfg["passive_stop"]:
            continue
        findings.append(Finding("plain-passive", doc.line_of(match.start()), doc.snippet(match.start(), match.end(), 15), "name the actor; use active voice", weight=0.5))
    if cfg.get("contraction") and strict:
        findings += scan_regex(doc, cfg["contraction"], "plain-contraction", "write the full form (technical profile)", weight=0.5)
    findings += scan_regex(doc, cfg["modal"], "plain-modal", "must (required) / can (possible): avoid vague modals", weight=0.5)
    for pattern, hint in cfg["nominal"]:
        findings += scan_regex(doc, pattern, "plain-wording", hint, weight=0.5)
    if strict:
        findings += scan_regex(doc, r";", "plain-semicolon", "two sentences", weight=0.25)
    return findings


# --------------------------------------------------------------------------
# Preservation check between original and rewritten text
# --------------------------------------------------------------------------

NUMBER = re.compile(r"\d+(?:[ ,.\u202f]\d+)*")
ACRONYM = re.compile(r"\b[A-Z]{2,}\b")


def facts_of(text: str) -> dict[str, set[str]]:
    clean = FENCE.sub(" ", normalise(text))
    urls = {u.rstrip(".,;:") for u in URL.findall(clean)}
    no_urls = URL.sub(" ", clean)
    numbers = {re.sub(r"[ ,.\u202f]", "", m.group(0)) for m in NUMBER.finditer(no_urls)}
    names: set[str] = set()
    for block in re.split(r"\n\s*\n|\n(?=#|\s*[-*] )", no_urls):
        block = re.sub(r"^[#>\s*\-\d.)]+", "", re.sub(r"\s+", " ", block))
        for sentence in split_sentences(block, ["mr", "mrs", "dr", "st", "vs", "m", "mme"]):
            for token in WORD.findall(sentence)[1:]:
                if len(token) > 1 and token[0].isupper() and not token.isupper() and token != "I":
                    names.add(token)
    return {"urls": urls, "numbers": numbers, "names": names | set(ACRONYM.findall(no_urls))}


def preservation(original: str, rewritten: str) -> tuple[list[Finding], dict]:
    before, after = facts_of(original), facts_of(rewritten)
    findings: list[Finding] = []
    for kind, hard in (("urls", True), ("numbers", True), ("names", False)):
        for item in sorted(before[kind] - after[kind]):
            findings.append(Finding("preservation-dropped", 0, f"{kind[:-1]} '{item}' missing from rewrite", "restore it or confirm it was meant to go", hard=hard))
        for item in sorted(after[kind] - before[kind]):
            findings.append(Finding("preservation-added", 0, f"{kind[:-1]} '{item}' not in original", "possible invented fact: remove or source it", hard=hard))
    ratio = len(WORD.findall(rewritten)) / max(1, len(WORD.findall(original)))
    return findings, {"length_ratio": round(ratio, 2)}


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

PROFILES = {
    # essays, blog posts, emails, articles, stories, speeches
    "prose": dict(transitions=True, transition_ramp=(0.04, 0.20), dashes=True, burstiness=True, burstiness_ramp=(0.30, 0.62), hedging=True, concreteness=True, plain=False),
    # reports, papers, legal, business documents: tolerates connectors and steadier rhythm
    "formal": dict(transitions=True, transition_ramp=(0.08, 0.28), dashes=True, burstiness=True, burstiness_ramp=(0.25, 0.52), hedging=False, concreteness=True, plain=False),
    # manuals, procedures, READMEs, API docs: uniform rhythm is correct; controlled-language rules apply
    "technical": dict(transitions=False, transition_ramp=(0.1, 0.3), dashes=True, burstiness=False, burstiness_ramp=(0.25, 0.50), hedging=False, concreteness=False, plain=True),
}


def verdict_for(score: float, threshold: float, hard: bool) -> str:
    if hard:
        return "FAIL (hard flags)"
    if score <= threshold:
        return "PASS"
    return "FAIL (far above threshold)" if score > 2 * threshold else "FAIL"


def analyse(path: str, args, original_text: str | None = None) -> dict:
    text = extract_text(path)
    lang = args.lang if args.lang != "auto" else detect_language(text)
    lex = importlib.import_module(f"lexicon_{lang}")
    profile = args.profile
    cfg = PROFILES[profile]
    doc = build_document(path, text, lang, profile, lex.ABBREVIATIONS)
    if args.dump_text:
        return {"dump": doc.raw}
    baseline = None
    if original_text is not None:
        original_doc = build_document(path, original_text, lang, profile, lex.ABBREVIATIONS)
        baseline = concreteness(original_doc, lex)
    signals, findings, stats = compute(doc, lex, cfg, baseline)
    if cfg["plain"] or args.plain:
        plain_findings = plain_checks(doc, lex, strict=cfg["plain"])
        findings += plain_findings
        violations_per_100 = sum(f.weight for f in plain_findings) / max(doc.word_count, 40) * 100
        signals.append(SignalResult("controlled-language", 20, ramp(violations_per_100, 2.0, 10.0),
                                    f"{violations_per_100:.1f} weighted rule breaks per 100 words (ASD-STE100 / langage clair)"))
    preserve_stats = {}
    if original_text is not None:
        extra, preserve_stats = preservation(original_text, text)
        findings += extra
    score = round(sum(s.points for s in signals), 1)
    hard_findings = [f for f in findings if f.hard]
    warnings = []
    if doc.word_count < MIN_WORDS_FOR_STATS:
        warnings.append(f"only {doc.word_count} words of prose: score is unreliable")
    if preserve_stats and not 0.5 <= preserve_stats["length_ratio"] <= 1.5:
        warnings.append(f"rewrite is {preserve_stats['length_ratio']}x the original length")
    return {
        "file": path, "lang": lang, "profile": profile, "words": doc.word_count,
        "score": score, "threshold": args.threshold,
        "verdict": verdict_for(score, args.threshold, bool(hard_findings)),
        "signals": [dict(name=s.name, weight=s.weight, points=round(s.points, 1), value=s.value) for s in signals if s.applicable],
        "stats": stats | preserve_stats, "warnings": warnings,
        "findings": [f.__dict__ for f in findings],
    }



FIX_ADVICE = {
    "sentence-rhythm": "Vary sentence length on purpose: add a very short sentence (3-7 words) next to a long one (30+). Do not just split sentences evenly.",
    "sentence-length-band": "Too many mid-length sentences: make some much shorter and let one or two run long.",
    "paragraph-uniformity": "Vary paragraph size: merge two paragraphs, give one idea a single-sentence paragraph, let one paragraph run long.",
    "generic-content": "Text is generic. Add only facts the source already holds: names, numbers, dates, places, a concrete example. If the source has none, shorten instead of padding.",
    "hedging": "Too many can/may/often hedges. State what is known as fact; hedge only what is really uncertain.",
    "triads": "Lists of exactly three recur. Keep a triad only where there are three things; otherwise use two, four, or one developed item.",
    "stock-phrases": "Replace each stock phrase with the plain claim it was wrapping (see Findings hints).",
    "vocabulary": "Swap listed words for plain ones, then re-read: do not replace each with a fancy synonym.",
    "negative-parallelism": "State the point directly instead of 'not X but Y'.",
    "participle-riders": "End the sentence before the -ing clause; make that idea a sentence or drop it.",
    "transition-openers": "Cut sentence-opening connectors (Additionally, Ultimately, En effet...). Let the order of ideas carry the link.",
    "summary-closer": "Delete the recap ending. End on the last concrete point.",
    "formatting": "Strip decorative bold, emoji, Title Case headings and 'Label: text' bullets; use prose.",
    "repeated-openers": "Consecutive sentences start with the same word: change the subject or merge.",
    "copula-avoidance": "Use is / are / has instead of serves as / boasts / features.",
    "rhetorical-staging": "Drop ask-and-answer setups and fragment runs; make the claim.",
    "controlled-language": "Fix the plain-* findings: split long sentences, name the actor, use the simple verb, replace should/might with must/can.",
    "dashes": "Replace dashes with period, comma, colon or parentheses.",
}

def render(result: dict, top: int, show_all: bool) -> str:
    out = [f"{result['file']}  [{result['lang']}, {result['profile']}]  words={result['words']}"]
    out.append(f"AI-likeness {result['score']}/100 (pass <= {result['threshold']}) -> {result['verdict']}")
    for warning in result["warnings"]:
        out.append(f"! {warning}")
    contributing = sorted((s for s in result["signals"] if s["points"] > 0), key=lambda s: -s["points"])
    if contributing:
        out.append("Signals: " + " | ".join(f"{s['name']} {s['points']} ({s['value']})" for s in contributing))
    fixes = [f"  - {FIX_ADVICE[s['name']]}" for s in contributing if s["points"] >= 1.5 and s["name"] in FIX_ADVICE]
    if fixes and not result["verdict"].startswith("PASS"):
        out.append("To fix, in order of impact:")
        out.extend(fixes)
    stats = {k: v for k, v in result["stats"].items() if k != "note"}
    if stats:
        out.append("Stats: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    if result["stats"].get("note") and not result["warnings"]:
        out.append(f"! {result['stats']['note']}")
    findings = result["findings"]
    hard = [f for f in findings if f["hard"]]
    soft = sorted((f for f in findings if not f["hard"]), key=lambda f: (-f["weight"], f["line"]))
    shown = hard + (soft if show_all else soft[:top])
    if shown:
        out.append("Findings:")
        for f in shown:
            where = f"L{f['line']}" if f["line"] else "doc"
            mark = "HARD " if f["hard"] else ""
            hint = f"  {f['hint']}" if f["hint"] else ""
            out.append(f"  {where:>5} {mark}{f['signal']}: \"{f['snippet']}\"{hint}")
        if not show_all and len(soft) > top:
            out.append(f"  ... {len(soft) - top} more (use --all)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+", help="file(s) to check, or - for stdin")
    parser.add_argument("--lang", choices=["auto", "en", "fr"], default="auto")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="prose",
                        help="prose (essays, emails, articles), formal (reports, papers), technical (manuals, READMEs)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="pass when score <= this (default %(default)s)")
    parser.add_argument("--original", help="original text file; verifies numbers, URLs and names survive the rewrite")
    parser.add_argument("--plain", action="store_true", help="also run controlled-language checks (on by default for technical)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--top", type=int, default=15, help="max soft findings to print")
    parser.add_argument("--all", action="store_true", help="print every finding")
    parser.add_argument("--dump-text", action="store_true", help="print the extracted text and exit")
    args = parser.parse_args(argv)

    original_text = extract_text(args.original) if args.original else None
    results, failed = [], False
    for path in args.files:
        try:
            result = analyse(path, args, original_text)
        except SystemExit:
            raise
        except Exception as error:  # noqa: BLE001 - report per-file, keep going
            print(f"{path}: error: {error}", file=sys.stderr)
            return 2
        if "dump" in result:
            print(result["dump"])
            continue
        results.append(result)
        failed = failed or not result["verdict"].startswith("PASS")
    if args.json:
        print(json.dumps(results if len(results) != 1 else results[0], ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(render(r, args.top, args.all) for r in results))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
