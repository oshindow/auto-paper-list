"""Parse authors, affiliations, and short abstract summaries from arbitrary PDFs.

This module intentionally uses the same lightweight dependency as the ICLR parser
(`pypdf`) but does not assume an ICLR/OpenReview template.  It focuses on the
first page and handles common layouts where affiliations appear below authors,
inline with numeric markers, or are absent.
"""
from __future__ import annotations

import re
from pathlib import Path

import pypdf

from parse_pdf_affiliations import (
    MARKER_GLYPH_RE,
    _INST_KW_RE,
    _PLACE_KW,
    _clean_affil_text,
    _is_footnote_text,
    _split_affiliations,
)


SECTION_RE = re.compile(
    r"^(abstract|a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|summary|index\s+terms|"
    r"keywords|1\.?\s+introduction|i\.?\s+introduction|introduction)\b",
    re.I,
)
ABSTRACT_RE = re.compile(r"^(abstract|a\s*b\s*s\s*t\s*r\s*a\s*c\s*t)", re.I)
INTRO_RE = re.compile(r"^(1\.?\s+introduction|i\.?\s+introduction|introduction)\b", re.I)
EMAIL_RE = re.compile(r"\S+@\S+\.\w+")
AUTHOR_MARK_RE = re.compile(r"(\d+|" + MARKER_GLYPH_RE + r"|[A-Z])+$")

AFFIL_HINT_RE = re.compile(
    r"\b("
    r"University|Universit|Université|Universität|Institute|Institut|College|"
    r"School|Department|Laboratory|Lab\b|Research|Academy|Foundation|Center|"
    r"Centre|Inc\.?|Corp\.?|Ltd\.?|LLC|GmbH|Company|NVIDIA|Google|Meta|"
    r"Microsoft|Amazon|Alibaba|Tencent|ByteDance|OpenAI|Anthropic"
    r")\b",
    re.I,
)

BAD_AUTHOR_WORDS = {
    "abstract",
    "introduction",
    "preprint",
    "arxiv",
    "github",
    "demo",
    "code",
    "date",
    "keywords",
    "index terms",
    "correspondence",
    "corresponding author",
    "equal contribution",
}


def _read_first_page(path: str | Path) -> str | None:
    try:
        reader = pypdf.PdfReader(str(path))
        if not reader.pages:
            return ""
        return reader.pages[0].extract_text() or ""
    except Exception:
        return None


def _clean_line(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _first_page_lines(text: str) -> list[str]:
    return [_clean_line(ln) for ln in text.splitlines() if _clean_line(ln)]


def _section_index(lines: list[str], pattern: re.Pattern[str]) -> int | None:
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return None


def _is_affiliation_line(line: str) -> bool:
    if EMAIL_RE.search(line):
        return False
    if _is_footnote_text(line):
        return False
    if "foundation model" in line.lower():
        return False
    if re.match(r"^\s*\d{1,2}\s*[A-ZÀ-Ý]", line):
        return True
    if AFFIL_HINT_RE.search(line) or _INST_KW_RE.search(line) or _PLACE_KW.search(line):
        return True
    return False


def _looks_like_short_org(line: str) -> bool:
    if EMAIL_RE.search(line) or _is_footnote_text(line) or SECTION_RE.match(line):
        return False
    if re.search(r"\b(model|models|report|title|paper)\b", line, re.I):
        return False
    words = line.split()
    if not (1 <= len(words) <= 3):
        return False
    if all(re.match(r"^[A-ZÀ-Ý][A-Za-zÀ-ÿ'’.\-]+$", word) for word in words):
        return True
    return False


def _capitalized_token_count(line: str) -> int:
    return len(re.findall(r"\b[A-ZÀ-Ý][A-Za-zÀ-ÿ'’.\-]+\b", line))


def _looks_like_author_start(line: str) -> bool:
    low = line.lower()
    if any(w in low for w in BAD_AUTHOR_WORDS):
        return False
    if ":" in line and "," not in line:
        return False
    if EMAIL_RE.search(line) or _is_affiliation_line(line):
        return False
    cap_count = _capitalized_token_count(line)
    if cap_count < 2:
        return False
    has_author_punctuation = "," in line or re.search(r"\d|" + MARKER_GLYPH_RE, line)
    if has_author_punctuation and len(line) <= 700:
        return True
    return False


def _looks_like_author_continuation(line: str) -> bool:
    if _looks_like_author_start(line):
        return True
    if EMAIL_RE.search(line) or _is_affiliation_line(line):
        return False
    return "," in line and _capitalized_token_count(line) >= 2


def _find_author_block(header: list[str]) -> tuple[list[str], int, int]:
    """Return (author_lines, start, end_exclusive)."""
    best: tuple[list[str], int, int] | None = None
    for i, line in enumerate(header):
        if not _looks_like_author_start(line):
            continue
        block = [line]
        j = i + 1
        while j < len(header) and _looks_like_author_continuation(header[j]):
            block.append(header[j])
            j += 1
        text = " ".join(block)
        # Prefer candidates with explicit author markers or comma-separated names.
        score = (2 if re.search(r"\d|" + MARKER_GLYPH_RE, text) else 0) + text.count(",")
        if best is None or score > (" ".join(best[0]).count(",") + 2):
            best = (block, i, j)
    return best or ([], -1, -1)


def _is_individual_author_line(line: str) -> bool:
    if _is_affiliation_line(line) or _is_footnote_text(line):
        return False
    line = EMAIL_RE.sub("", line)
    name, _ = _strip_author_markers(line)
    low = name.lower()
    if any(w in low for w in BAD_AUTHOR_WORDS):
        return False
    if any(w in low for w in ("interaction", "dialogue", "generation", "model", "benchmark", "technical report")):
        return False
    tokens = name.split()
    if not (2 <= len(tokens) <= 6):
        return False
    if not re.match(r"^[A-ZÀ-Ý]", tokens[0]):
        return False
    # Long title fragments tend to have many lowercase function words.
    stopwords = {"a", "an", "the", "of", "for", "in", "on", "with", "to", "and"}
    if sum(1 for tok in tokens if tok.lower() in stopwords) >= 2:
        return False
    return True


def _find_individual_author_block(header: list[str]) -> tuple[list[str], int, int]:
    first_affil = next((i for i, line in enumerate(header) if _is_affiliation_line(line)), len(header))
    block: list[str] = []
    start = -1
    end = -1
    scan_start = 1 if len(header) > 1 else 0
    for i, line in enumerate(header[:first_affil]):
        if i < scan_start:
            continue
        if _is_individual_author_line(line):
            if start < 0:
                start = i
            block.append(line)
            end = i + 1
        elif block:
            # Allow a short run of one-author-per-line stanzas only.
            break
    if len(block) >= 2:
        return block, start, end
    return [], -1, -1


def _find_space_separated_author_block(header: list[str]) -> tuple[list[str], int, int]:
    first_affil = next((i for i, line in enumerate(header) if _is_affiliation_line(line)), len(header))
    candidates: list[str] = []
    start = -1
    scan_start = 1 if len(header) > 1 else 0
    for i, line in enumerate(header[:first_affil]):
        if i < scan_start:
            continue
        low = line.lower()
        if any(k in low for k in ("figure", "demo", " model", " code", "github", "huggingface", "technical report")):
            break
        if "team" in low and "," in line:
            break
        letters = [ch for ch in line if ch.isalpha()]
        if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.75:
            continue
        if _is_individual_author_line(line):
            if start < 0:
                start = i
            candidates.append(line)
            continue
        if "," not in line and _capitalized_token_count(line) >= 4 and not _is_affiliation_line(line):
            words = line.split()
            stopwords = {"a", "an", "the", "of", "for", "in", "on", "with", "to", "and"}
            if sum(1 for word in words if word.lower() in stopwords) <= 1:
                if start < 0:
                    start = i
                candidates.append(line)
                continue
        if candidates:
            break
    if candidates and sum(_capitalized_token_count(line) for line in candidates) >= 4:
        return candidates, start, start + len(candidates)
    return [], -1, -1


def _find_single_or_team_author_block(header: list[str]) -> tuple[list[str], int, int]:
    scan_start = 1 if len(header) > 1 else 0
    for i, line in enumerate(header):
        if i < scan_start:
            continue
        if SECTION_RE.match(line):
            break
        if "team" in line.lower() and "," in line:
            return [line], i, i + 1
        if _is_affiliation_line(line):
            break
        if _is_individual_author_line(line):
            return [line], i, i + 1
    return [], -1, -1


def _strip_author_markers(name: str) -> tuple[str, list[str]]:
    name = name.strip(" ,;")
    markers = re.findall(r"\d+|" + MARKER_GLYPH_RE, name)
    marker_atom = r"(?:\d+|" + MARKER_GLYPH_RE + r"|\*)"
    name = re.sub(r"(?:\s*,?\s*" + marker_atom + r")+\s*$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" ,;"), markers


def _parse_authors(author_lines: list[str]) -> list[tuple[str, list[str]]]:
    if len(author_lines) >= 2 and all(_is_individual_author_line(line) for line in author_lines):
        parts = [EMAIL_RE.sub("", line).strip() for line in author_lines]
    elif author_lines and all("," not in line for line in author_lines) and sum(_capitalized_token_count(line) for line in author_lines) >= 4:
        words = " ".join(EMAIL_RE.sub("", line) for line in author_lines).split()
        parts = [" ".join(words[i:i + 2]) for i in range(0, len(words) - 1, 2)]
    else:
        text = " ".join(author_lines)
        text = EMAIL_RE.sub("", text)
        text = re.sub(r"\s*,\s*", ", ", text)
        text = re.sub(r"\s+and\s+", ", ", text, flags=re.I)
        text = re.sub(r"\s*&\s*", ", ", text)
        # Preserve affiliation marker lists ("2,4") while splitting authors.
        text = re.sub(r"(?<=\d)\s*,\s*(?=\d)", "|", text)
        # Insert a separator in extraction glitches: "Wang1,Yuhao" or
        # "Zhou 2 Julia Wang2".
        text = re.sub(r"(?<=[a-zà-ÿ])(\d+)(?=[A-ZÀ-Ý])", r"\1, ", text)
        text = re.sub(r"(?<=[\d*†‡§¶∗⋆⋄♯♭♮])\s+(?=[A-ZÀ-Ý][a-zà-ÿ])", ", ", text)
        parts = [p.strip().replace("|", ",") for p in text.split(",") if p.strip()]

    authors: list[tuple[str, list[str]]] = []
    for part in parts:
        if AFFIL_HINT_RE.search(part) or EMAIL_RE.search(part):
            continue
        name, markers = _strip_author_markers(part)
        low = name.lower()
        if any(w in low for w in BAD_AUTHOR_WORDS):
            continue
        tokens = name.split()
        if "team" in name.lower():
            pass
        elif not (2 <= len(tokens) <= 6):
            continue
        if not re.match(r"^[A-ZÀ-Ý]", tokens[0]):
            continue
        if len(name) > 90:
            continue
        authors.append((name, markers))

    # Deduplicate while preserving order.
    deduped: list[tuple[str, list[str]]] = []
    seen = set()
    for name, markers in authors:
        key = re.sub(r"\W+", "", name).lower()
        if key and key not in seen:
            deduped.append((name, markers))
            seen.add(key)
    return deduped


def _parse_affiliations(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    marker_to_inst: dict[str, str] = {}
    shared: list[str] = []
    joined_lines: list[str] = []
    buf: list[str] = []
    for line in lines:
        if EMAIL_RE.search(line) or _is_footnote_text(line):
            if buf:
                joined_lines.append(" ".join(buf))
                buf = []
            continue
        if _is_affiliation_line(line) or _looks_like_short_org(line):
            buf.append(line)
            continue
        if buf and re.search(r"\b\d{1,2}\s*[A-ZÀ-Ý]", line):
            buf.append(line)
            continue
        if buf:
            joined_lines.append(" ".join(buf))
            buf = []
    if buf:
        joined_lines.append(" ".join(buf))

    for line in joined_lines:
        if EMAIL_RE.search(line) or _is_footnote_text(line):
            continue
        if not (_is_affiliation_line(line) or _looks_like_short_org(line)):
            continue
        pairs = _split_affiliations(line)
        if not pairs:
            cleaned = _clean_affil_text(line)
            if cleaned:
                pairs = [("all", cleaned)]
        for marker, inst in pairs:
            inst = _clean_affil_text(inst)
            if not inst or _is_footnote_text(inst):
                continue
            if marker.isdigit():
                marker_to_inst[marker] = inst
            elif marker == "all":
                shared.append(inst)
    shared = list(dict.fromkeys(shared))
    return marker_to_inst, shared


def _extract_title(header: list[str], author_start: int) -> str:
    title_lines = header[:author_start] if author_start > 0 else []
    cleaned: list[str] = []
    for line in title_lines:
        low = line.lower()
        if low.startswith(("arxiv:", "preprint", "code:", "demo:", "date:")):
            continue
        if SECTION_RE.match(line):
            break
        cleaned.append(line)
    return " ".join(cleaned).strip()


def _normalize_page_text(text: str) -> str:
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_abstract(text: str) -> str:
    lines = _first_page_lines(text)
    start = _section_index(lines, ABSTRACT_RE)
    if start is None:
        return ""
    chunks: list[str] = []
    first = re.sub(ABSTRACT_RE, "", lines[start], count=1).strip(" :-")
    if first:
        chunks.append(first)
    for line in lines[start + 1:]:
        if INTRO_RE.match(line) or re.match(r"^(index\s+terms|keywords)\b", line, re.I):
            break
        chunks.append(line)
    return _normalize_page_text(" ".join(chunks))


def _extract_unlabeled_summary(lines: list[str], author_end: int) -> str:
    if author_end < 0:
        return ""
    chunks: list[str] = []
    for line in lines[author_end:]:
        if INTRO_RE.match(line):
            break
        if re.match(r"^(code|demo|date|keywords|index\s+terms)\s*:", line, re.I):
            break
        if EMAIL_RE.search(line) or _is_affiliation_line(line) or _is_footnote_text(line):
            continue
        if not chunks and not re.match(r"^(we|this|while|current|recent|natural|speech|real-time|as)\b", line, re.I):
            continue
        chunks.append(line)
    return _normalize_page_text(" ".join(chunks))


def _split_sentences(text: str) -> list[str]:
    text = _normalize_page_text(text)
    if not text:
        return []
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return [p.strip() for p in pieces if len(p.strip()) > 25]


def _one_sentence_summary(abstract: str) -> tuple[str, str]:
    sentences = _split_sentences(abstract)
    if not sentences:
        return "", ""
    problem_kw = re.compile(
        r"\b(however|despite|lack|lacks|lacking|challenge|challenging|gap|"
        r"limited|limitation|remain|fails?|difficult|need|requires?|ignore|"
        r"suboptimal|overhead|latency|costly)\b",
        re.I,
    )
    solution_kw = re.compile(
        r"\b(we\s+(propose|present|introduce|develop|build|construct|design|"
        r"release|evaluate)|this\s+(paper|work|study)\s+(proposes|presents|"
        r"introduces|develops)|our\s+(benchmark|framework|model|method|system))\b",
        re.I,
    )
    problem = next((s for s in sentences if problem_kw.search(s)), sentences[0])
    solution = next((s for s in sentences if solution_kw.search(s) and s != problem), "")
    if not solution:
        solution = next((s for s in sentences[1:] if s != problem), "")
    return problem, solution


def parse_arbitrary_pdf(path: str | Path) -> dict:
    text = _read_first_page(path)
    if text is None:
        return {"success": False, "reason": "pdf_read_error"}

    lines = _first_page_lines(text)
    section_idx = _section_index(lines, SECTION_RE)
    header_end = section_idx if section_idx is not None else min(len(lines), 40)
    header = lines[:header_end]
    author_lines, author_start, author_end = _find_author_block(header)
    authors_with_markers = _parse_authors(author_lines)
    space_lines, space_start, space_end = _find_space_separated_author_block(header)
    space_authors = _parse_authors(space_lines)
    if len(space_authors) > len(authors_with_markers):
        author_lines, author_start, author_end = space_lines, space_start, space_end
        authors_with_markers = space_authors
    if len(authors_with_markers) < 2:
        author_lines, author_start, author_end = _find_individual_author_block(header)
        authors_with_markers = _parse_authors(author_lines)
    if len(authors_with_markers) < 2:
        author_lines, author_start, author_end = _find_space_separated_author_block(header)
        authors_with_markers = _parse_authors(author_lines)
    if not authors_with_markers:
        author_lines, author_start, author_end = _find_single_or_team_author_block(header)
        authors_with_markers = _parse_authors(author_lines)
    if not authors_with_markers:
        for i, line in enumerate(header[1:], start=1):
            if _looks_like_short_org(line) or _is_affiliation_line(line):
                author_lines, author_start, author_end = [line], i, i + 1
                authors_with_markers = [(line, [])]
                break

    affil_region = header[author_end:] if author_end >= 0 else header
    marker_to_inst, shared_affils = _parse_affiliations(affil_region)
    if author_lines and len(author_lines) == 1 and "team" in author_lines[0].lower() and "," in author_lines[0]:
        inline_affil = _clean_affil_text(author_lines[0].split(",", 1)[1])
        if inline_affil:
            shared_affils.append(inline_affil)
            shared_affils = list(dict.fromkeys(shared_affils))
    if not marker_to_inst and not shared_affils and author_lines and len(author_lines) == 1:
        if _looks_like_short_org(author_lines[0]) or _is_affiliation_line(author_lines[0]):
            shared_affils = [author_lines[0]]

    authors = [name for name, _ in authors_with_markers]
    per_author: list[list[str]] = []
    for _, markers in authors_with_markers:
        numeric_markers = [m for m in markers if m.isdigit()]
        affils = [marker_to_inst[m] for m in numeric_markers if m in marker_to_inst]
        if not affils and shared_affils:
            affils = list(shared_affils)
        per_author.append(list(dict.fromkeys(affils)))

    institutions = list(dict.fromkeys([a for affs in per_author for a in affs] + shared_affils + list(marker_to_inst.values())))
    abstract = _extract_abstract(text)
    if not abstract:
        abstract = _extract_unlabeled_summary(lines, author_end)
    problem, solution = _one_sentence_summary(abstract)

    return {
        "success": bool(authors),
        "reason": "" if authors else "authors_not_found",
        "title": _extract_title(header, author_start),
        "authors": authors,
        "affiliations_per_author": per_author,
        "institutions_set": institutions,
        "abstract": abstract,
        "problem_solved": problem,
        "how_solved": solution,
        "pattern": "arbitrary_first_page",
    }


if __name__ == "__main__":
    import sys

    for pdf in sys.argv[1:]:
        result = parse_arbitrary_pdf(pdf)
        print(f"\n=== {pdf} ===")
        print(f"success={result.get('success')} reason={result.get('reason', '')}")
        print(f"title={result.get('title', '')}")
        for author, affils in zip(result.get("authors", []), result.get("affiliations_per_author", [])):
            print(f"  {author} -> {', '.join(affils)}")
        print("problem:", result.get("problem_solved", ""))
        print("solution:", result.get("how_solved", ""))
