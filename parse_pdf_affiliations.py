"""Parse author affiliations from ICLR-formatted PDF first pages.

Handles four common layout patterns:
  A. Numbered markers      "Author1*,1,2 Author2,2,3" + "1Inst A 2Inst B 3Inst C"
  B. No markers / shared   "Author1, Author2"        + "Single Institution"
  C. Per-author blocks     name / affil / email triples (one author per stanza)
  D. Multi-line affiliations (markers on separate lines)

Public API:
    parse_pdf(path) -> dict
        {
          'success': bool,
          'authors': [str],
          'affiliations_per_author': [[str]],
          'institutions_set': [str],
          'pattern': 'A' | 'B' | 'C' | 'unknown',
          'reason': str,        # only when success=False
        }
"""
from __future__ import annotations

import re
from pathlib import Path

import pypdf

# Marker glyphs used for superscript footnotes
MARKER_GLYPHS = "*†‡§¶∗⋆⋄♯♭♮"
MARKER_GLYPH_RE = "[" + re.escape(MARKER_GLYPHS) + "]"

# Stop tokens that mark the end of the header section
STOP_RE = re.compile(
    r"^("
    r"ABSTRACT|Abstract|abstract|"
    r"\d+\s+I[Nn][Tt][Rr]|"
    r"\d+\s+Introduction|"
    r"Figure\s+\d+|"
    r"1\s+I[Nn][Tt][Rr][Oo]"
    r")"
)


def _read_first_page(path: str) -> str | None:
    try:
        reader = pypdf.PdfReader(path)
        return reader.pages[0].extract_text() or ""
    except Exception:
        return None


def _extract_head_block(text: str) -> list[str]:
    """Lines between the conference header and abstract/intro/figure.

    Includes email lines (so per-author Pattern C stanzas can be detected),
    stops only at the actual section break (abstract/intro/figure).
    """
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    seen_conf_header = False
    for i, ln in enumerate(lines[:50]):
        s = ln.strip()
        if not s:
            continue
        if not seen_conf_header and (
            "ICLR 2026" in s
            or "iclr 2026" in s.lower()
            or "Conference Paper" in s
            or "Workshop" in s
            or "Published as" in s
        ):
            seen_conf_header = True
            continue
        if STOP_RE.match(s):
            break
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Pattern A — numbered markers
# ---------------------------------------------------------------------------
_TITLE_STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "with",
                    "is", "are", "as", "to", "and", "or", "via", "from",
                    "by", "into", "at", "be"}


def _is_title_line(s: str) -> bool:
    """A title line is mostly uppercase letters, or title-case English with stopwords."""
    # Reject lines that look like affil markers — but NOT titles starting with a
    # number like "3D-AWARE...".
    # Heuristic: a digit followed by a capital and a lowercase letter is a marker
    # ("1Meta", "2University"). A digit followed by an all-caps acronym in a
    # short line (e.g. "2AITHYRA", "3KAIST") is also a marker, not a title.
    words = s.split()
    if re.match(r"^\s*\d{1,2}\s*[A-ZÀ-Ý][a-zà-ÿ]", s):
        return False
    if re.match(r"^\s*\d{1,2}\s*[A-ZÀ-Ý]", s) and len(words) <= 3:
        return False
    # Also reject lines with multiple digit-marker chunks (e.g. "1A 2B 3C")
    if len(re.findall(r"\b\d{1,2}\s*[A-ZÀ-Ý]", s)) >= 3:
        return False
    # Reject lines that explicitly start with a strong institution keyword like
    # "University of X" or "Department of Y" (these are clearly affiliation lines,
    # not titles), but DON'T reject titles that merely *contain* a word like
    # "Foundation" or "Center".
    if re.match(r"^\s*(University|Universit[áèéë]|Universit[äé]t|Université|"
                r"Department|Faculty|School|College|Institute|Laboratory|Lab\b|"
                r"Center|Centre)\b", s, re.I):
        return False
    alpha = [c for c in s if c.isalpha()]
    if len(alpha) < 5:
        return False
    upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
    # Multi-word all-caps lines are titles ("TOPOLOGICAL FLOW MATCHING").
    if len(words) >= 2 and upper_ratio >= 0.60:
        return True
    # A single-word all-caps line is a title only if it's long enough (>12 chars).
    # Short single-word all-caps tokens are typically institution acronyms
    # (AITHYRA, KAIST, MBZUAI, EPFL, MIT) — NOT titles.
    if len(words) == 1 and upper_ratio >= 0.60 and len(s.strip()) > 12:
        return True
    # Title-case English title — has multiple short stopwords + at least some caps
    word_tokens = re.findall(r"[A-Za-z]+", s)
    if not word_tokens:
        return False
    stop_count = sum(1 for w in word_tokens if w.lower() in _TITLE_STOPWORDS)
    if stop_count >= 2 and upper_ratio >= 0.18:
        return True
    return False


def _looks_like_author_line(s: str) -> bool:
    """Heuristic: at least one capitalized name token, contains markers OR commas."""
    if "@" in s:
        return False
    # Must have ≥2 capitalized words and a separator (comma, space-comma, or 'and')
    name_token = r"[A-ZÀ-Ý][\w’'.\-À-ÿ]+"
    cap_count = len(re.findall(name_token, s))
    if cap_count < 2:
        return False
    if "," in s or " and " in s.lower() or re.search(MARKER_GLYPH_RE, s) or re.search(r"\d", s):
        return True
    # Single-line list of names separated by spaces, e.g. "Patrice Bechard Chao Wang Amir..."
    if cap_count >= 3 and len(s.split()) <= cap_count * 2 + 2:
        return True
    return False


def _looks_like_affil_line(s: str) -> bool:
    """Heuristic: starts with a marker (digit or glyph) followed by an institution-like phrase,
    or is short and contains an institution keyword. Rejects footnote lines."""
    # Reject only emails (`name@domain.tld`); plain '@' as in "CompVis @ LMU Munich" is fine.
    if re.search(r"\S+@\S+\.\w+", s):
        return False
    # Numbered marker prefix (but NOT "1 INTRODUCTION") — accept these as affil
    # lines even if they contain inline footnote text; _split_affiliations will
    # drop the footnote chunks downstream.
    m = re.match(r"^\s*(\d{1,2})\s*([A-Z]|´|˜|´´|´´´|École|Ecole|Universit|Department|School|College|Institute)", s)
    if m and not re.match(r"^\d{1,2}\s+(I[Nn][Tt][Rr]|Introduction|RELATED|Method|Background)", s):
        return True
    # Otherwise reject pure footnote lines like "∗Equal contribution †Corresponding author"
    if _is_footnote_text(s):
        return False
    # Lines that start with a glyph marker (∗, †, ‡) AND have institution-like content.
    # If it doesn't have institution keywords, treat as a footnote.
    if re.match(r"^\s*" + MARKER_GLYPH_RE + r"\s*[A-ZÀ-Ý]", s):
        # Already passed the _is_footnote_text check above, so this is OK
        # but we still want at least an institution keyword to confirm.
        if _INST_KW_RE.search(s):
            return True
        return False
    inst_kw = r"(University|Institut[ae]|Universit|Universität|Universidade|Universidad|Université|College|School|Department|Laboratory|Lab\b|Research|Inc\.|Corp|Group|Academy|Foundation|AI Lab|Center|Centre|Co\.|Ltd|GmbH|AG|S\.A\.|National|Federal|Royal)"
    if re.search(inst_kw, s):
        return True
    return False


# A name-token regex to extract author tokens from a line.
# Important: the name pattern uses [A-Za-z…] (letters only — NO digits)
# so superscript markers like "Chen1" don't get glued to the name.
LETTER = r"[A-Za-zÀ-ÿа-яА-Я]"
NAME_BODY = r"[A-Za-zÀ-ÿа-яА-Я’'.\-]"
NAME_TOK = re.compile(
    r"([A-ZÀ-Ýа-яА-Я]" + NAME_BODY + r"+(?:\s+" + LETTER + NAME_BODY + r"+){0,4})"
    r"((?:\s*[*†‡§¶∗⋆⋄♯♭♮,]\s*\d+|\s*[*†‡§¶∗⋆⋄♯♭♮]|\s*\d+)*)"
)


def _split_affiliations(s: str) -> list[tuple[str, str]]:
    """Split an affiliation line into (marker, institution) pairs.

    Returns only NUMERIC marker pairs (1, 2, 3, ...) and one "all" pair when
    the line has no markers at all. Glyph markers (∗, †, ‡) and any chunk
    matching footnote text (Equal contribution, Corresponding author) are
    silently dropped — those are author-credit notes, not institutions.
    """
    s = s.strip().rstrip(",.")
    # Split before each digit-marker followed by a capital letter (with optional
    # whitespace between), AND before each glyph-marker so we can drop those
    # chunks cleanly. Handles both "1Inst" and "1 Inst" layouts.
    out = re.split(
        r"(?=\b\d{1,2}\s*[A-ZÀ-Ý´])"
        r"|(?=" + MARKER_GLYPH_RE + r")",
        s,
    )
    pairs: list[tuple[str, str]] = []
    for chunk in out:
        c = chunk.strip().rstrip(",.")
        if not c:
            continue
        m = re.match(r"^\s*(\d{1,2})\s*(.+)$", c)
        if m:
            text = _clean_affil_text(m.group(2))
            if not text or _is_footnote_text(text):
                continue
            pairs.append((m.group(1), text))
            continue
        if re.match(r"^\s*" + MARKER_GLYPH_RE, c):
            continue
        text = _clean_affil_text(c)
        if not text or _is_footnote_text(text):
            continue
        pairs.append(("all", text))
    return pairs


def _extract_author_markers(line: str) -> list[tuple[str, list[str]]]:
    """For an author line, return [(name, [marker1, marker2, ...]), ...]."""
    out: list[tuple[str, list[str]]] = []
    for m in NAME_TOK.finditer(line):
        name = m.group(1).strip().rstrip(",")
        if name.lower() in {"and", "the", "of", "for", "via"}:
            continue
        marker_blob = m.group(2) or ""
        # Extract individual markers (digits or marker glyphs) from the blob
        markers = re.findall(r"\d+|" + MARKER_GLYPH_RE, marker_blob)
        out.append((name, markers))
    # Filter out very short matches that look like title remnants
    out = [(n, mk) for n, mk in out if len(n) >= 3 and " " in n or len(n) > 4]
    return out


def _parse_pattern_a(head: list[str]) -> dict | None:
    """Numbered-marker layout. Returns dict on success, None on fail."""
    classes: list[str] = []
    for s in head:
        if re.search(r"\S+@\S+\.\w+", s):  # actual email (name@domain.tld)
            classes.append("email")
        elif STOP_RE.match(s):
            classes.append("stop")
        elif _is_title_line(s):
            classes.append("title")
        elif _looks_like_affil_line(s):
            classes.append("affil")
        elif _looks_like_author_line(s):
            classes.append("author")
        else:
            classes.append("other")

    author_lines: list[str] = []
    affil_lines: list[str] = []
    seen_author = False
    seen_affil = False
    for s, c in zip(head, classes):
        if c == "title":
            if seen_author or seen_affil:
                break
            continue
        if c == "author" and not seen_affil:
            author_lines.append(s)
            seen_author = True
            continue
        if c == "affil":
            if not seen_author and not seen_affil:
                continue
            affil_lines.append(s)
            seen_affil = True
            continue
        if c == "stop":
            break
        if c == "email":
            # Don't break on email — just skip (some papers list email before affils).
            continue
        if c == "other":
            if seen_author and not seen_affil:
                # Could be a continuation of the author line (multi-line authors).
                if _looks_like_author_line(s):
                    author_lines.append(s)
                    continue
            # Once we have both author + affil lines, the header is done — stop.
            if seen_affil:
                break

    if not author_lines or not affil_lines:
        return None

    # Reject if an "author"-classified line appears after the first "affil" —
    # that's an interleaved name/affil structure (Pattern C/D), not Pattern A.
    saw_affil = False
    for c in classes:
        if c == "affil":
            saw_affil = True
        elif c == "author" and saw_affil:
            return None

    # Parse author markers
    authors: list[tuple[str, list[str]]] = []
    for line in author_lines:
        authors.extend(_extract_author_markers(line))
    if not authors:
        return None

    # Parse affiliations
    raw_affils: list[tuple[str, str]] = []
    for line in affil_lines:
        raw_affils.extend(_split_affiliations(line))
    if not raw_affils:
        return None

    marker_to_inst: dict[str, str] = {}
    shared_affils: list[str] = []
    for marker, inst in raw_affils:
        if marker == "all":
            shared_affils.append(inst)
        else:
            marker_to_inst[marker] = inst

    # Build per-author institution lists. Only numeric markers map to
    # institutions — glyph markers (∗, †, ‡) are footnotes (equal contribution,
    # corresponding author, etc.) and never refer to institutions.
    per_author: list[list[str]] = []
    for name, markers in authors:
        numeric_markers = [m for m in markers if m.isdigit()]
        if numeric_markers:
            insts = [marker_to_inst[m] for m in numeric_markers if m in marker_to_inst]
        else:
            insts = []
        if not insts and shared_affils:
            insts = list(shared_affils)
        per_author.append(insts)

    # If most authors got no institution, try shared_affils as fallback
    if shared_affils and sum(1 for a in per_author if a) < len(per_author) * 0.5:
        per_author = [list(shared_affils) for _ in authors]

    # Filter: papers with no marker mapping at all → fail
    if not any(per_author):
        return None

    return {
        "success": True,
        "authors": [n for n, _ in authors],
        "affiliations_per_author": per_author,
        "institutions_set": list(dict.fromkeys(
            [v for vs in per_author for v in vs]
            + shared_affils
            + list(marker_to_inst.values())
        )),
        "pattern": "A",
    }


# ---------------------------------------------------------------------------
# Pattern B — no markers, shared affiliation
# ---------------------------------------------------------------------------
def _parse_pattern_b(head: list[str]) -> dict | None:
    """Single-stanza shared-affiliation layout.

    Find the first email line, walk back to skip title lines, treat the
    LAST line before the email as the shared affiliation, and EVERY earlier
    non-title line as containing author names (multi-line author blocks
    supported).
    """
    email_idx = next((i for i, s in enumerate(head) if "@" in s), None)
    if email_idx is None or email_idx < 2:
        return None

    # Find the last contiguous title line at the top so we can skip them
    body_start = 0
    for i in range(email_idx):
        if not _is_title_line(head[i]):
            body_start = i
            break

    body = head[body_start:email_idx]
    body = _trim_email_continuation_tail(body)
    if len(body) < 2:
        return None

    # Greedy split: walk from the bottom of `body` collecting affiliation
    # lines until we hit a line that *clearly* looks like an authors-only
    # list (comma-separated capitalized name tokens, no inst/place/digit
    # clues). Everything before that point is name_lines.
    affil_lines: list[str] = []
    name_lines: list[str] = []
    in_names = False
    for s in reversed(body):
        if _is_title_line(s):
            # Title — stop walking; everything beyond was the title block.
            break
        if in_names:
            name_lines.insert(0, s)
            continue
        if _looks_purely_authors(s):
            in_names = True
            name_lines.insert(0, s)
            continue
        affil_lines.insert(0, s)

    if not name_lines or not affil_lines:
        return None

    # Author extraction: split each name line on commas / 'and' / '&'
    authors: list[str] = []
    for nl in name_lines:
        cleaned = re.sub(r"[*†‡§¶∗⋆⋄♯♭♮]", "", nl)
        cleaned = re.sub(r"\d", "", cleaned)
        if "," in cleaned or " and " in cleaned.lower() or " & " in cleaned:
            parts = re.split(r",|\s+and\s+|\s+&\s+", cleaned)
        else:
            # Multiple names separated only by whitespace, e.g.
            # "Wolfgang Lehrach Daniel Hennes Miguel Lazaro-Gredilla"
            # Split into pairs of (FirstName LastName).
            tokens = cleaned.split()
            parts = []
            buf: list[str] = []
            for tok in tokens:
                if buf and tok and tok[0].isupper() and len(buf) >= 2:
                    # New author starts (every 2 tokens for plain First-Last names).
                    parts.append(" ".join(buf))
                    buf = [tok]
                else:
                    buf.append(tok)
            if buf:
                parts.append(" ".join(buf))
        for p in parts:
            n = p.strip().rstrip(",.;:")
            if 3 <= len(n) <= 80 and re.search(r"[A-ZÀ-Ý]", n):
                authors.append(n)
    if len(authors) < 1:
        return None

    # Drop footnote lines, strip leading glyph markers, clean inline footnote phrases
    insts = []
    for s in affil_lines:
        cleaned = _clean_affil_text(s)
        if not cleaned or _is_footnote_text(cleaned):
            continue
        insts.append(cleaned)
    if not insts:
        return None

    per_author = [list(insts) for _ in authors]
    return {
        "success": True,
        "authors": authors,
        "affiliations_per_author": per_author,
        "institutions_set": list(dict.fromkeys(insts)),
        "pattern": "B",
    }


# ---------------------------------------------------------------------------
# Pattern C — per-author stanza (name / affil / email)
# ---------------------------------------------------------------------------
_NAME_LINE_RE = re.compile(
    r"^[A-ZÀ-Ýа-яА-Я]" + NAME_BODY + r"+(\s+" + LETTER + NAME_BODY +
    r"+){0,4}\s*[*†‡§¶∗⋆⋄♯♭♮]?\s*$"
)

# Footnote keywords — text after a glyph marker like ∗/†/‡ is almost always
# author-credit metadata ("Equal contribution", "Project lead"), not an
# institution. We use this to (a) reject "affiliation lines" that are really
# footnote lines, and (b) drop (marker, text) pairs whose text matches.
_FOOTNOTE_KW = re.compile(
    r"\b("
    r"equal\s+(contribut|advis|first|senior|second|third)|"
    r"contribut(ed|ing)?\s+equally|"
    r"correspond(ing|ence)?(\s+author)?|"
    r"co-?correspond|co-?first|co-?senior|co-?lead|co-?author|"
    r"project\s+lead|"
    r"these\s+authors|"
    r"both\s+authors|"
    r"the\s+first\s+author|"
    r"the\s+senior\s+author|"
    r"first\s+(author|two\s+authors)|"
    r"senior\s+author|"
    r"shared\s+(first|last|senior)|"
    r"work\s+(done|performed)\s+(during|while|at|in)|"
    r"now\s+at|"
    r"acknowledg|"
    r"random\s+order|"
    r"alphabetical(ly)?|"
    r"after\s+the\s+first|"
    r"to\s+whom\s+correspondence|"
    r"\bcounsel|"
    r"advising"
    r")\b",
    re.IGNORECASE,
)


def _is_footnote_text(s: str) -> bool:
    return bool(_FOOTNOTE_KW.search(s))


_FOOTNOTE_INLINE = re.compile(
    r"(\s*[*†‡§¶∗⋆⋄♯♭♮]?\s*"
    r"(equal\s+contribut\w*|contributed\s+equally|"
    r"corresponding\s+author[s]?|co-?corresponding(\s+author[s]?)?|"
    r"project\s+lead|equal\s+advis\w*|"
    r"these\s+authors|both\s+authors|"
    r"first\s+(author|two\s+authors)|senior\s+author|"
    r"co-?first|co-?senior|co-?lead|"
    r"shared\s+(first|last|senior)|"
    r"work\s+(done|performed)\s+(during|while|at|in)[^,;]*|"
    r"now\s+at[^,;]*)"
    r"\s*[.,;:]?\s*)",
    re.IGNORECASE,
)


def _clean_affil_text(s: str) -> str:
    """Strip leading/trailing footnote phrases and stray markers from an
    extracted affiliation string. Returns the cleaned text or '' if nothing
    institution-like remains.
    """
    if not s:
        return ""
    s = re.sub(r"^\s*[*†‡§¶∗⋆⋄♯♭♮]\s*", "", s)
    # Strip leading footnote phrases (one or more)
    while True:
        new = _FOOTNOTE_INLINE.sub("", s, count=1) if _FOOTNOTE_INLINE.match(s) else s
        if new == s:
            break
        s = new
    # Strip trailing footnote phrases (one or more, with optional preceding markers)
    for _ in range(3):
        m = _FOOTNOTE_INLINE.search(s)
        if not m:
            break
        # Only strip if the match is at (or very near) the end
        if m.end() >= len(s) - 1:
            s = s[:m.start()]
        else:
            break
    s = s.strip()
    s = s.strip(",.;:* †‡§¶∗⋆⋄♯♭♮")
    return s.strip()


_INST_KW_RE = re.compile(
    r"\b(Universit[áèéëâ]?|Institut[ae]?|Universität|Université|"
    r"School|College|Department|Laboratory|\bLab\b|Research|"
    r"Inc\.?|Corp\.?|Group|Foundation|Cent[er]+|Co\.|Ltd|GmbH|"
    r"National|Federal|Royal|Faculty|Academy|Office|Hospital|"
    r"University|Center)\b",
    re.I,
)


_PLACE_KW = re.compile(
    r"\b(USA|UK|France|Germany|Spain|Italy|China|Japan|Korea|India|Israel|"
    r"Canada|Australia|Switzerland|Netherlands|Belgium|Sweden|Finland|"
    r"Denmark|Norway|Austria|Russia|Czech|Greece|Portugal|Brazil|Mexico|"
    r"Singapore|Taiwan|Iran|Turkey|Egypt|Bangladesh|Vietnam|Thailand|"
    r"Hungary|Poland|Ireland|Estonia|Latvia|Lithuania|Slovakia|Slovenia|"
    r"Bulgaria|Romania|Croatia|Serbia|Ukraine|"
    r"Lyon|Berlin|Paris|London|Tokyo|Beijing|Shanghai|Munich|Barcelona|"
    r"Rome|Stockholm|Amsterdam|Brussels|Vienna|Zurich|Zürich|Geneva|"
    r"Sydney|Melbourne|Toronto|Vancouver|Montreal|Boston|Chicago|"
    r"Seattle|Houston|Austin|Dallas|Atlanta|Pittsburgh|Detroit|Portland|"
    r"Princeton|Cambridge|Oxford|Stanford|Berkeley|"
    r"Mannheim|Tübingen|Heidelberg|Hamburg|Stuttgart|Frankfurt|Leipzig)\b"
)


def _looks_purely_authors(s: str) -> bool:
    """Stricter than _looks_like_author_line — used in Pattern B's reverse walk
    to decide whether a line is the author list (vs an affiliation line). Only
    returns True if the line clearly contains a comma-separated author list
    with no institution / place / numeric clues."""
    if "@" in s:
        return False
    if _INST_KW_RE.search(s):
        return False
    if _PLACE_KW.search(s):
        return False
    if re.search(r"\b\d{2,}\b", s):  # has multi-digit numbers (postcodes, dept ids, years)
        return False
    cap_count = len(re.findall(r"[A-ZÀ-Ý][a-zA-ZÀ-ÿ’'.\-]+", s))
    if cap_count < 2:
        return False
    if not ("," in s or " and " in s.lower() or " & " in s):
        return False
    return True


def _looks_like_email_prefix(s: str) -> bool:
    """Detects lines that look like the start of a multi-line email block,
    e.g. '{wpl, hennes, lazarogredilla,'  — lowercase tokens, commas, often a
    leading '{', and no capital letters."""
    s = s.strip()
    if not s:
        return False
    if s.startswith("{"):
        return True
    if s.endswith(","):
        # ends with comma AND has no capital letters → likely email continuation
        if not re.search(r"[A-ZÀ-Ý]", s):
            return True
    if "," in s and not re.search(r"[A-ZÀ-Ý]", s):
        return True
    return False


def _trim_email_continuation_tail(body: list[str]) -> list[str]:
    """Drop trailing lines from `body` that look like multi-line email starts."""
    while body and _looks_like_email_prefix(body[-1]):
        body = body[:-1]
    return body


def _is_name_line(s: str) -> bool:
    """A bare 'name only' line (used by Pattern C). Rejects institution-like text."""
    if "@" in s:
        return False
    s2 = re.sub(r"[*†‡§¶∗⋆⋄♯♭♮]+\s*$", "", s.strip())
    if _INST_KW_RE.search(s2):
        return False
    # All-caps single-word lines are institution acronyms (AITHYRA, MIT, KAIST, etc.),
    # not names.
    words = s2.split()
    if len(words) == 1 and s2.isupper() and len(s2) >= 2:
        return False
    return bool(_NAME_LINE_RE.match(s2))


def _parse_pattern_c(head: list[str]) -> dict | None:
    """Per-author stanzas separated by emails.

    Structure:  name_lines... / affil_line(s) / email   x N
    The last line(s) before each email are taken as the affiliation; everything
    above (within the stanza) is parsed as author name(s).
    """
    i = 0
    while i < len(head) and _is_title_line(head[i]):
        i += 1
    rest = head[i:]

    emails = [j for j, s in enumerate(rest) if "@" in s]
    if len(emails) < 2:
        return None

    authors: list[str] = []
    per_author_affils: list[list[str]] = []
    prev_end = -1
    for em_idx in emails:
        stanza = rest[prev_end + 1:em_idx]
        prev_end = em_idx
        if len(stanza) < 2:
            continue
        # Last line of the stanza = affil. Skip footnote lines from the bottom up.
        affil_line = ""
        affil_idx = len(stanza) - 1
        while affil_idx >= 0:
            cand = _clean_affil_text(stanza[affil_idx].strip())
            if cand and not _is_footnote_text(cand):
                affil_line = cand
                break
            affil_idx -= 1
        name_lines = stanza[:affil_idx]
        if not affil_line or not name_lines:
            continue
        # If the affil line itself contains multiple numbered chunks
        # ("1Inst A 2Inst B 3Inst C"), split it so each institution lands as a
        # separate string in the set.
        affil_chunks = _split_affiliations(affil_line)
        numeric_chunks = [t for m, t in affil_chunks if m.isdigit()]
        if len(numeric_chunks) >= 2:
            stanza_affils = numeric_chunks
        else:
            stanza_affils = [affil_line]

        # Extract author names from name_lines
        stanza_authors: list[str] = []
        for nl in name_lines:
            cleaned = re.sub(r"[*†‡§¶∗⋆⋄♯♭♮]", "", nl)
            cleaned = re.sub(r"\d", "", cleaned)
            if "," in cleaned or " and " in cleaned.lower() or " & " in cleaned:
                parts = re.split(r",|\s+and\s+|\s+&\s+", cleaned)
            else:
                parts = [cleaned]
            for p in parts:
                n = p.strip().rstrip(",.;:")
                if 3 <= len(n) <= 80 and re.search(r"[A-ZÀ-Ý]", n):
                    stanza_authors.append(n)
        if not stanza_authors:
            continue
        for n in stanza_authors:
            authors.append(n)
            per_author_affils.append(list(stanza_affils))

    if len(authors) < 2 or not per_author_affils:
        return None
    return {
        "success": True,
        "authors": authors,
        "affiliations_per_author": per_author_affils,
        "institutions_set": list(dict.fromkeys(
            [v for vs in per_author_affils for v in vs]
        )),
        "pattern": "C",
    }


# ---------------------------------------------------------------------------
# Pattern D — alternating name/affil pairs (no emails)
# ---------------------------------------------------------------------------
def _parse_pattern_d(head: list[str]) -> dict | None:
    """Alternating (name_line, affil_line) pairs with no email delimiters.

    Common for industry-only papers (Apple, EleutherAI, etc.) where the
    typesetter prints each author on its own line followed by their lab.
    """
    i = 0
    while i < len(head) and _is_title_line(head[i]):
        i += 1
    rest = head[i:]
    # Stop at first email or section break
    end = next((j for j, s in enumerate(rest) if "@" in s or STOP_RE.match(s)), len(rest))
    body = rest[:end]
    if len(body) < 4 or len(body) % 2 == 1:
        return None

    name_lines = body[0::2]
    affil_lines = body[1::2]

    # Reject if any of the affil slots is footnote text or looks like more authors.
    if any(_is_footnote_text(s) for s in affil_lines):
        return None
    if any(_looks_purely_authors(s) for s in affil_lines):
        return None
    # If any of the *name* slots looks like a title fragment (multi-word all-caps),
    # the alternating-pair structure has broken down — reject.
    if any(_is_title_line(s) for s in name_lines):
        return None
    # If any *name* slot has an institution keyword (University, Department,
    # Institute, etc.), the alternation is broken — names don't look like that.
    # This catches cases where each author has a multi-line affiliation, which
    # Pattern E handles instead.
    if any(_INST_KW_RE.search(s) for s in name_lines):
        return None

    # Stricter: every affil_line must look like an actual affiliation
    # (have an institution keyword, a country, or be a known short institution).
    inst_match = sum(
        1 for s in affil_lines
        if _INST_KW_RE.search(s) or _PLACE_KW.search(s)
    )
    has_repeats = len(set(affil_lines)) < len(affil_lines)
    if not (has_repeats and inst_match >= 1) and inst_match < max(2, len(affil_lines) * 2 // 3):
        return None

    authors: list[str] = []
    per_author_affils: list[list[str]] = []
    for nl, al in zip(name_lines, affil_lines):
        cleaned = re.sub(r"[*†‡§¶∗⋆⋄♯♭♮]", "", nl)
        cleaned = re.sub(r"\d", "", cleaned)
        if "," in cleaned or " and " in cleaned.lower() or " & " in cleaned:
            parts = re.split(r",|\s+and\s+|\s+&\s+", cleaned)
        else:
            tokens = cleaned.split()
            if len(tokens) >= 4:
                # Multiple authors separated only by whitespace.
                parts = []
                buf: list[str] = []
                for tok in tokens:
                    if buf and tok and tok[0].isupper() and len(buf) >= 2:
                        parts.append(" ".join(buf))
                        buf = [tok]
                    else:
                        buf.append(tok)
                if buf:
                    parts.append(" ".join(buf))
            else:
                parts = [cleaned]
        cleaned_al = _clean_affil_text(al.strip())
        if not cleaned_al:
            continue
        for p in parts:
            n = p.strip().rstrip(",.;:")
            if 3 <= len(n) <= 80 and re.search(r"[A-ZÀ-Ý]", n):
                authors.append(n)
                per_author_affils.append([cleaned_al])

    if len(authors) < 2:
        return None
    return {
        "success": True,
        "authors": authors,
        "affiliations_per_author": per_author_affils,
        "institutions_set": list(dict.fromkeys(
            [a for affils in per_author_affils for a in affils]
        )),
        "pattern": "D",
    }


# ---------------------------------------------------------------------------
# Pattern E — per-author stanzas with multi-line affiliations (no emails)
# ---------------------------------------------------------------------------
def _parse_pattern_e(head: list[str]) -> dict | None:
    """Per-author stanzas separated by name-line transitions. Each stanza
    has 1 name line followed by 1+ affiliation lines. Handles cases like:

        Lars Holdijk
        Department of Computer Science
        University of Oxford
        Michael Bronstein
        Department of Computer Science
        University of Oxford,
        AITHYRA

    Where Pattern D's strict 1:1 alternation can't apply because authors
    have multi-line affiliations.
    """
    i = 0
    while i < len(head) and _is_title_line(head[i]):
        i += 1
    rest = head[i:]
    # Drop email lines — they don't help us segment stanzas in this pattern.
    rest = [s for s in rest if not re.search(r"\S+@\S+\.\w+", s)]

    if len(rest) < 4:
        return None

    # Group: a name line starts a new stanza, non-name lines extend the
    # current stanza's affiliation list.
    stanzas: list[list[str]] = []
    current: list[str] = []
    for s in rest:
        if _is_name_line(s):
            if current:
                stanzas.append(current)
            current = [s]
        else:
            if current:
                current.append(s)
    if current:
        stanzas.append(current)

    valid = [st for st in stanzas if len(st) >= 2]
    if len(valid) < 2:
        return None

    # Sanity: at least one affil line per stanza must contain an institution
    # keyword OR be a known institution acronym; otherwise the structure is
    # probably misclassified.
    inst_match = 0
    for st in valid:
        if any(_INST_KW_RE.search(al) for al in st[1:]):
            inst_match += 1
    if inst_match < max(2, len(valid) * 2 // 3):
        return None

    authors: list[str] = []
    per_author_affils: list[list[str]] = []
    for st in valid:
        name = re.sub(r"[*†‡§¶∗⋆⋄♯♭♮]+\s*$", "", st[0]).strip()
        affils: list[str] = []
        for al in st[1:]:
            cleaned = _clean_affil_text(al)
            if cleaned and not _is_footnote_text(cleaned):
                affils.append(cleaned)
        if not affils:
            continue
        authors.append(name)
        per_author_affils.append(affils)

    if len(authors) < 2 or not per_author_affils:
        return None
    return {
        "success": True,
        "authors": authors,
        "affiliations_per_author": per_author_affils,
        "institutions_set": list(dict.fromkeys(
            [v for vs in per_author_affils for v in vs]
        )),
        "pattern": "E",
    }


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------
def parse_pdf(path: str | Path) -> dict:
    text = _read_first_page(str(path))
    if text is None:
        return {"success": False, "reason": "pdf_read_error", "pattern": "unknown"}
    head = _extract_head_block(text)
    if not head:
        return {"success": False, "reason": "empty_head", "pattern": "unknown"}

    for parser, label in [
        (_parse_pattern_a, "A"),
        (_parse_pattern_c, "C"),
        (_parse_pattern_d, "D"),
        (_parse_pattern_e, "E"),
        (_parse_pattern_b, "B"),
    ]:
        res = parser(head)
        if res and res.get("success"):
            # Sanity: discard if absolutely no institutions were extracted.
            if res.get("institutions_set"):
                return res
    return {"success": False, "reason": "unknown_format", "pattern": "unknown"}


# ---------------------------------------------------------------------------
# Sanity-check CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        r = parse_pdf(p)
        print(f"\n=== {p} ===")
        print(f"pattern={r.get('pattern')}  success={r.get('success')}  reason={r.get('reason','')}")
        if r.get("success"):
            for n, ais in zip(r["authors"], r["affiliations_per_author"]):
                print(f"  {n:30s} -> {', '.join(ais)}")
            print(f"  set: {r['institutions_set']}")
