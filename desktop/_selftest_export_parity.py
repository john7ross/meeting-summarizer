"""Export parity: the CONTENT must not depend on the chosen format, and the
selected version must be the one that lands on disk.

Every format is read back and decoded for real - including the PDF, whose text
is recovered through the embedded ``/ToUnicode`` CMap, so "the file is non-empty"
can never pass for "the file has the content".

    backend\\python\\python.exe desktop\\_selftest_export_parity.py
"""
import base64
import json
import re
import sys
import shutil
import tempfile
import zipfile
import zlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "desktop"))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.backend import exporter as E          # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


# ── readers ──────────────────────────────────────────────────────────────────
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_html(path: Path) -> str:
    raw = _read_text(path)
    raw = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<style.*?</style>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", raw)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return text


def _read_json(path: Path) -> str:
    def walk(node):
        if isinstance(node, dict):
            return " ".join(f"{k} {walk(v)}" for k, v in node.items())
        if isinstance(node, list):
            return " ".join(walk(v) for v in node)
        return str(node)
    return walk(json.loads(_read_text(path)))


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    xml = xml.replace("</w:p>", "\n")
    return re.sub(r"<[^>]+>", "", xml)


def _pdf_raw_streams(raw: bytes):
    """Yield each stream's bytes, sliced by its declared ``/Length``.

    Searching for the next ``endstream`` looks simpler but is wrong: embedded
    font programs are binary and can contain that very sequence, truncating the
    stream. Which fonts get subset depends on the characters used - including the
    export timestamp - so the damage moved between runs and looked like a flake.
    """
    for m in re.finditer(rb"/Length\s+(\d+)[^>]*>>\s*stream\r?\n", raw):
        start = m.end()
        yield raw[start:start + int(m.group(1))]


def _a85_body(chunk: bytes) -> bytes:
    """Payload of an ASCII85 stream, minus ONLY its two-byte ``~>`` terminator.

    ``rstrip(b">~")`` looks equivalent and is not: ``>`` is a LEGAL ASCII85 data
    character, so every payload whose last data byte is ``>`` lost it. a85decode
    then returned truncated bytes, zlib refused them, the loop below fell through
    to the raw chunk, no ``Tj`` was found and _read_pdf returned "" - a PDF that
    every real reader (verified against pypdf) decodes perfectly. Because the
    export timestamp is part of the compressed content, whether the last byte
    landed on ``>`` changed with the clock: ~5% of runs, never reproducible in
    isolation. The stamps that trigger it are pinned in STAMPS below.
    """
    body = chunk.strip()
    return body[:-2] if body.endswith(b"~>") else body


def _pdf_streams(raw: bytes) -> list:
    """Decoded stream payloads. reportlab writes page content as
    ``/ASCII85Decode /FlateDecode``, so plain zlib alone recovers the fonts but
    NOT the text - which would make every PDF assertion below vacuous."""
    out = []
    for chunk in _pdf_raw_streams(raw):
        data = chunk
        for decode in (lambda b: zlib.decompress(b),
                       lambda b: zlib.decompress(
                           base64.a85decode(_a85_body(b), adobe=False,
                                            ignorechars=b" \t\r\n")),
                       lambda b: base64.a85decode(b.strip(), adobe=True,
                                                  ignorechars=b" \t\r\n")):
            try:
                data = decode(chunk)
                break
            except Exception:  # noqa: BLE001 - try the next encoding
                continue
        out.append(data)
    return out


def _pdf_cmaps(streams: list) -> list:
    """code -> character maps, one per embedded subset font."""
    maps = []
    for stream in streams:
        if b"beginbfchar" not in stream:
            continue
        table = {}
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
                                   stream):
            try:
                code = int(src, 16)
                point = int(dst, 16)
            except ValueError:
                continue
            if point:
                table[code] = chr(point)
        if table:
            maps.append(table)
    return maps


_PDF_ESCAPES = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8,
                ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}


def _pdf_unescape(body: bytes) -> bytes:
    """Resolve PDF string escapes, INCLUDING the octal ``\\ddd`` form.

    Non-ASCII glyph codes are written octal-escaped, so an unescaper that only
    knows ``\\(``/``\\)``/``\\\\`` recovers Latin text and silently loses every
    Cyrillic character - which would let a broken PDF export pass this file.
    """
    out = bytearray()
    i, n = 0, len(body)
    while i < n:
        char = body[i]
        if char != 92:                      # not a backslash
            out.append(char)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        nxt = body[i]
        if 48 <= nxt <= 55:                 # octal: up to three digits
            digits = ""
            while i < n and len(digits) < 3 and 48 <= body[i] <= 55:
                digits += chr(body[i])
                i += 1
            out.append(int(digits, 8) & 0xFF)
        elif nxt in _PDF_ESCAPES:
            out.append(_PDF_ESCAPES[nxt])
            i += 1
        elif nxt in (10, 13):               # line continuation
            i += 1
        else:
            out.append(nxt)
            i += 1
    return bytes(out)


def _read_pdf(path: Path) -> str:
    """Text of a reportlab PDF, decoded through its /ToUnicode CMaps."""
    raw = path.read_bytes()
    streams = _pdf_streams(raw)
    maps = _pdf_cmaps(streams)
    literals = []
    for stream in streams:
        if b"Tj" not in stream and b"TJ" not in stream:
            continue
        for lit in re.findall(rb"\((?:\\.|[^()\\])*\)", stream):
            literals.append(_pdf_unescape(lit[1:-1]))
    if not literals:
        return ""
    decoded = []
    for body in literals:
        for table in maps or [{}]:
            decoded.append("".join(table.get(b, "") for b in body))
        decoded.append(body.decode("latin-1", "replace"))
    return "\n".join(decoded)


READERS = {"txt": _read_text, "md": _read_text, "html": _read_html,
           "json": _read_json, "docx": _read_docx, "pdf": _read_pdf}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


# ── fixture ──────────────────────────────────────────────────────────────────
SUMMARY_TEXT = (
    "## Обсудили\n"
    "ZETAMARKER бюджет проекта на следующий квартал.\n\n"
    "## Решили\n"
    "OMEGAMARKER согласовать смету до пятницы.\n"
)
ANALYSIS = {
    "characteristics": {"keyTopics": ["ТЕМАОДИН", "TOPICTWO"]},
    "actionItems": [{"task": "ЗАДАЧААЛЬФА", "assignee": "Иванов",
                     "priority": "high", "deadline": "2026-06-01"}],
    "risks": [{"description": "РИСКБЕТА", "severity": "high",
               "impact": "срыв сроков"}],
    "quotes": [{"text": "ЦИТАТАГАММА", "speaker": "Петров"}],
    "technologies": [{"name": "TECHDELTA", "category": "database"}],
    "questions": [{"question": "ВОПРОСЭПСИЛОН", "owner": "Сидоров"}],
    "recommendations": [{"recommendation": "РЕКОМЕНДАЦИЯДЗЕТА"}],
    "followupQuestions": [{"question": "ВОПРОСЭТА"}],
    "sentiment": {"overall": "positive", "engagement": "high"},
    "category": {"category": "КАТЕГОРИЯТЕТА", "tags": ["ТЕГЙОТА"]},
    "formalProtocol": {"protocolNumber": "42", "participants": ["Иванов", "Петров"],
                       "agenda": ["ПУНКТКАППА"]},
}
SUMMARY_TOKENS = ["ZETAMARKER", "OMEGAMARKER"]
ANALYSIS_TOKENS = ["ЗАДАЧААЛЬФА", "РИСКБЕТА", "ЦИТАТАГАММА", "TECHDELTA",
                   "ВОПРОСЭПСИЛОН", "РЕКОМЕНДАЦИЯДЗЕТА", "ВОПРОСЭТА",
                   "ТЕМАОДИН", "КАТЕГОРИЯТЕТА"]

tmp = Path(tempfile.mkdtemp(prefix="ms_parity_"))
# ``export_date`` is PINNED on purpose. Left unset, the exporter stamps
# datetime.now(), whose digits change every run - and which digits appear decides
# which glyphs get subset into the embedded font, so the PDF's bytes differed on
# every run and any weakness in the reader surfaced as a once-in-twenty "flake"
# that no amount of re-running would reproduce. Pinning makes the PDF
# byte-deterministic; the timestamp path itself is covered deliberately below.
META = {"video_name": "meeting.mkv", "language": "ru", "duration": "30m 49s",
        "wordCount": 1234, "participants": "Иванов, Петров",
        "export_date": "2026-07-26 12:30"}

check("format_list_is_complete", set(E.FORMATS) ==
      {"txt", "md", "json", "html", "pdf", "docx"}, str(E.FORMATS))

# ── summary in every format ──────────────────────────────────────────────────
summary_text_by_fmt = {}
for fmt in E.FORMATS:
    path = E.default_export_path(tmp, "meeting", "summary", 1, fmt)
    E.export("summary", SUMMARY_TEXT, fmt, path, META)
    check(f"summary_written_{fmt}", Path(path).exists()
          and Path(path).stat().st_size > 0,
          f"{Path(path).stat().st_size} bytes" if Path(path).exists() else "missing")
    summary_text_by_fmt[fmt] = _norm(READERS[fmt](Path(path)))

for token in SUMMARY_TOKENS:
    missing = [f for f, text in summary_text_by_fmt.items() if token not in text]
    check(f"summary_token_in_every_format_{token}", not missing,
          "all 6 formats" if not missing else f"MISSING FROM: {missing}")

# ── analysis in every format ────────────────────────────────────────────────
analysis_text_by_fmt = {}
for fmt in E.FORMATS:
    path = E.default_export_path(tmp, "meeting", "analysis", 1, fmt)
    E.export("analysis", ANALYSIS, fmt, path, META)
    check(f"analysis_written_{fmt}", Path(path).exists()
          and Path(path).stat().st_size > 0,
          f"{Path(path).stat().st_size} bytes" if Path(path).exists() else "missing")
    analysis_text_by_fmt[fmt] = _norm(READERS[fmt](Path(path)))

for token in ANALYSIS_TOKENS:
    missing = [f for f, text in analysis_text_by_fmt.items() if token not in text]
    check(f"analysis_token_in_every_format_{token}", not missing,
          "all 6 formats" if not missing else f"MISSING FROM: {missing}")

# The PDF reader must be proving something: if it decoded nothing, the checks
# above would be vacuous for that format.
check("pdf_decoder_actually_reads_text",
      len(analysis_text_by_fmt["pdf"]) > 200
      and any(t in analysis_text_by_fmt["pdf"] for t in ANALYSIS_TOKENS),
      f"{len(analysis_text_by_fmt['pdf'])} chars decoded")

# Meeting metadata travels with every format too.
for token in ("30m 49s", "Иванов"):
    missing = [f for f, text in analysis_text_by_fmt.items() if token not in text]
    check(f"analysis_meta_in_every_format_{token.replace(' ', '')}", not missing,
          "all 6 formats" if not missing else f"MISSING FROM: {missing}")

# ── versioning ──────────────────────────────────────────────────────────────
check("v1_filename_is_plain",
      Path(E.default_export_path(tmp, "meeting", "summary", 1, "md")).name
      == "meeting_summary.md")
check("v2_filename_is_suffixed",
      Path(E.default_export_path(tmp, "meeting", "summary", 2, "md")).name
      == "meeting_summary_v2.md")
check("analysis_versions_are_independent",
      Path(E.default_export_path(tmp, "meeting", "analysis", 3, "md")).name
      == "meeting_analysis_v3.md")
check("raw_is_never_versioned",
      Path(E.default_export_path(tmp, "meeting", "raw", 5, "txt")).name
      == "meeting_raw.txt")

# Different versions must not overwrite one another, and each file must hold
# ITS OWN content - the defect being guarded is a v2 export that writes v1 text.
v1_path = Path(E.default_export_path(tmp, "vers", "summary", 1, "md"))
v2_path = Path(E.default_export_path(tmp, "vers", "summary", 2, "md"))
E.export("summary", "ВЕРСИЯПЕРВАЯ содержимое", "md", v1_path, META)
E.export("summary", "ВЕРСИЯВТОРАЯ содержимое", "md", v2_path, META)
check("versions_are_separate_files", v1_path != v2_path and v1_path.exists()
      and v2_path.exists())
check("v1_keeps_its_own_content",
      "ВЕРСИЯПЕРВАЯ" in _read_text(v1_path)
      and "ВЕРСИЯВТОРАЯ" not in _read_text(v1_path))
check("v2_keeps_its_own_content",
      "ВЕРСИЯВТОРАЯ" in _read_text(v2_path)
      and "ВЕРСИЯПЕРВАЯ" not in _read_text(v2_path))

# The same version exported to different formats must carry the same version's
# text - a per-format version mix-up is what makes exports untrustworthy.
for fmt in E.FORMATS:
    p2 = Path(E.default_export_path(tmp, "vers", "summary", 2, fmt))
    E.export("summary", "ВЕРСИЯВТОРАЯ содержимое", fmt, p2, META)
    text = _norm(READERS[fmt](p2))
    # When this fails it must say WHY on the spot, and the file must be KEPT:
    # a bare "version text missing" once cost a long investigation that could not
    # reproduce it, because the evidence was a deleted temp file.
    ok = "ВЕРСИЯВТОРАЯ" in text and "ВЕРСИЯПЕРВАЯ" not in text
    detail = ""
    if not ok:
        kept = Path(tempfile.gettempdir()) / f"ms_parity_FAIL_{p2.name}"
        shutil.copy2(p2, kept)
        detail = (f"font={E._register_pdf_font()} decoded={len(text)} chars "
                  f"sample={text[:80]!r} size={p2.stat().st_size}B kept={kept}")
    check(f"v2_content_in_{fmt}", ok, detail)
    check(f"v2_filename_marks_version_{fmt}", "_v2." in p2.name, p2.name)

# ── the PDF must never silently lose Cyrillic ───────────────────────────────
# Helvetica has no Cyrillic glyphs. If no Unicode font can be registered (a font
# file briefly locked by another process is enough), a silent fallback produces a
# PDF whose Russian text is simply gone while txt/md/html/docx are intact - the
# exact "content depends on the format" failure this file exists to prevent.
_original_font = E._register_pdf_font
E._register_pdf_font = lambda: "Helvetica"
try:
    E.export("summary", "Кириллический текст.", "pdf", tmp / "nofont.pdf", META)
    check("refuses_pdf_without_a_unicode_font", False, "silently wrote a PDF")
except RuntimeError as exc:
    check("refuses_pdf_without_a_unicode_font", True, str(exc)[:60])
    check("font_error_names_the_alternatives",
          "DOCX" in str(exc) and "font" in str(exc).lower())
ASCII_META = {"video_name": "meeting.mkv", "language": "en", "duration": "30m 49s",
              "wordCount": 1234, "participants": "Ivanov, Petrov",
              "export_date": "2026-07-26 12:30"}
try:
    E.export("summary", "Plain ASCII only.", "pdf", tmp / "ascii.pdf", ASCII_META)
    check("ascii_only_pdf_still_allowed", (tmp / "ascii.pdf").stat().st_size > 500)
except Exception as exc:  # noqa: BLE001
    check("ascii_only_pdf_still_allowed", False, repr(exc))
E._register_pdf_font = _original_font
check("real_font_registration_works", E._register_pdf_font() != "Helvetica",
      E._register_pdf_font())

# ── the reader's own limit, guarded explicitly ───────────────────────────────
# _read_pdf decodes literal by literal, so it needs each word to arrive inside ONE
# literal. reportlab subsets a TTF into at most 255 glyphs per embedded font: past
# that it emits a SECOND subset, a word can straddle both, and it reaches this
# reader as two literals joined by whitespace - reported as "text missing" while
# the PDF is perfectly correct. The fixture currently sits well under the cliff;
# if adding characters ever pushes it over, this check fails FIRST and says what
# to do, instead of surfacing as an unreproducible flake in some other assertion.
_probe = tmp / "subset_probe.pdf"
E.export("summary", SUMMARY_TEXT, "pdf", _probe, META)
_pairs = sum(len(t) for t in _pdf_cmaps(_pdf_streams(_probe.read_bytes())))
check("pdf_glyph_subset_stays_in_one_font", 0 < _pairs < 250,
      f"{_pairs} glyphs; at 255 reportlab splits the subset and _read_pdf must "
      f"start tracking the current font per text-show operation")

# ── the ASCII85 terminator must not eat payload ─────────────────────────────
# Direct guard on the helper, so the reader's own decoding bug is caught here
# rather than as an unexplained "the PDF is empty" three sections further down.
_a85_payload = base64.a85encode(zlib.compress(b"Tj marker payload"))
check("a85_body_keeps_a_trailing_gt",
      _a85_body(b">" + _a85_payload[1:] + b"~>") == b">" + _a85_payload[1:]
      and _a85_body(_a85_payload + b"~>") == _a85_payload,
      "'>' is legal ASCII85 data; only the two-byte '~>' terminates the stream")

# ── the timestamp must not decide whether the PDF is readable ────────────────
# Pinning export_date above removed the run-to-run variation, so the variation now
# gets covered ON PURPOSE instead of by chance: which digits the stamp contains
# decides which glyphs enter the embedded subset, and the reader has to survive
# every one of them. Sweeping all ten digits deterministically is strictly more
# coverage than one random clock reading per run, and it fails reproducibly.
STAMPS = ["2026-07-26 12:30", "1970-01-01 00:00", "2099-12-31 23:59",
          "2026-04-05 06:07", "2026-08-09 10:11",
          # These six were found by sweeping the timestamp space: each makes the
          # page's ASCII85 payload end on a literal ``>`` right before the ``~>``
          # terminator, which is what the old rstrip(b">~") ate (see _a85_body).
          # They are the flake, pinned - remove them and it goes back to being a
          # once-in-twenty ghost that no amount of re-running reproduces.
          "2026-01-31 00:59", "2026-07-01 09:59", "2026-07-01 23:00",
          "2026-07-18 23:37", "2026-12-18 09:37", "2026-12-18 23:59",
          None]   # None = the live clock
for idx, stamp in enumerate(STAMPS):
    meta = dict(META)
    if stamp is None:
        meta.pop("export_date")
    else:
        meta["export_date"] = stamp
    out = tmp / f"stamp_{idx}.pdf"
    try:
        E.export("summary", "ВЕРСИЯВТОРАЯ содержимое", "pdf", out, meta)
        text = _norm(_read_pdf(out))
        found = "ВЕРСИЯВТОРАЯ" in text
    except Exception as exc:                             # noqa: BLE001
        found, text = False, repr(exc)
    label = "live_clock" if stamp is None else stamp.replace(" ", "_").replace(":", "")
    check(f"pdf_readable_with_stamp_{label}", found,
          "" if found else f"decoded={len(text)} sample={text[:60]!r}")

# ── guards ──────────────────────────────────────────────────────────────────
for bad in ("doc", "rtf", ""):
    try:
        E.export("summary", "x", bad, tmp / "x.out", META)
        check(f"rejects_unknown_format_{bad or 'empty'}", False, "no error")
    except ValueError:
        check(f"rejects_unknown_format_{bad or 'empty'}", True)
try:
    E.export("nonsense", "x", "txt", tmp / "x.txt", META)
    check("rejects_unknown_kind", False, "no error")
except ValueError:
    check("rejects_unknown_kind", True)

print("\n".join(results))
_ok = bool(results) and not any(r.startswith("FAIL") for r in results)
print("SUMMARY " + ("ALL_PASS" if _ok else "HAS_FAILURES"))
# Clean up only on success: a failing run's artefacts are the evidence, and the
# ms_parity_FAIL_* copies above point into this directory. Left unconditional,
# every green run leaked a temp tree (161 had accumulated when this was found).
if _ok:
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print(f"kept for diagnosis: {tmp}")
sys.exit(0 if _ok else 1)
