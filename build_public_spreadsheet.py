"""Generate a clean, shareable spreadsheet of ICLR 2026 accepted papers.

Reads `iclr2026/iclr2026_accepted_pdf.csv` (PDF-cleaned data with OpenReview
fallback) and produces:

  iclr2026/iclr2026_public.csv   (UTF-8 with BOM — opens cleanly in Excel)
  iclr2026/iclr2026_public.xlsx  (same data, xlsx-formatted)

Compared to the raw build, this file:
  • Uses the merged best-available author/affiliation columns
  • Adds an `Institutions_canonical` column normalized via the chart's rules
  • Strips control chars, stray glyph markers, and common PDF artefacts
  • Normalizes Unicode to NFC (`Z¨u` → `ü`)
  • Drops conference-logistics columns (date / location / session) that aren't
    relevant to institutional analysis.
"""
from __future__ import annotations

import csv
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

try:
    from pylatexenc.latex2text import LatexNodes2Text
    _LATEX = LatexNodes2Text()
except ImportError:
    _LATEX = None


# Unicode subscript/superscript maps for digits + a few letters/symbols.
_SUB_MAP = {
    **{c: chr(0x2080 + i) for i, c in enumerate("0123456789")},
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}
_SUP_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ",
    "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ",
    "m": "ᵐ", "n": "ⁿ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ",
    "t": "ᵗ", "u": "ᵘ", "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}


def _flatten_math_alphanumerics(s: str) -> str:
    """Convert math-bold / math-italic / etc. Unicode letters back to plain
    ASCII (e.g. 𝐋𝐢 → Li). Preserves other math symbols like ℓ, α, ∇, ∂, ∞."""
    out = []
    for c in s:
        cp = ord(c)
        # U+1D400 – U+1D7FF is the Mathematical Alphanumeric Symbols block.
        if 0x1D400 <= cp <= 0x1D7FF:
            out.append(unicodedata.normalize("NFKC", c))
        else:
            out.append(c)
    return "".join(out)


def latex_to_unicode(s: str) -> str:
    """Convert LaTeX math markup commonly seen in paper titles to Unicode.
    `$\\alpha$` → α, `$\\nabla$` → ∇, `$\\textrm{-less}$` → -less, etc.

    Falls through unchanged when pylatexenc isn't available."""
    if not s or _LATEX is None:
        return s
    if "$" not in s and "\\" not in s and "{" not in s:
        return s
    try:
        out = _LATEX.latex_to_text(s)
    except Exception:
        return s
    out = _flatten_math_alphanumerics(out)

    def _sub_brace(m):
        body = m.group(1)
        if all(c in _SUB_MAP for c in body):
            return "".join(_SUB_MAP[c] for c in body)
        return body

    def _sup_brace(m):
        body = m.group(1)
        if all(c in _SUP_MAP for c in body):
            return "".join(_SUP_MAP[c] for c in body)
        return body

    out = re.sub(r"_\{([^{}]*)\}", _sub_brace, out)
    out = re.sub(r"\^\{([^{}]*)\}", _sup_brace, out)
    out = re.sub(r"_(\w)", lambda m: _SUB_MAP.get(m.group(1), "_" + m.group(1)), out)
    out = re.sub(r"\^(\w)", lambda m: _SUP_MAP.get(m.group(1), "^" + m.group(1)), out)
    return out

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import make_iclr_treemap as m  # noqa: E402

SRC = ROOT / "data" / "iclr2026_accepted_pdf.csv"
OUT_CSV = ROOT / "data" / "iclr2026_public.csv"
OUT_XLSX = ROOT / "data" / "iclr2026_public.xlsx"

# --- sanitization helpers ----------------------------------------------------

# Stray "marker" glyphs that PDF extraction sometimes leaves dangling on names
STRAY_MARKERS = re.compile(r"[*†‡§¶∗⋆⋄♯♭♮×≀⋉°·‰‱⊕⊗⊙∘∙△▽◦◯■□●○]+")

# openpyxl-illegal control characters
ILLEGAL_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Common PDF extraction artefact: a single capital letter, a space, then a
# lowercase letter — e.g. "Y oung" should be "Young", "Z u" should be "Zu".
# Restrict to inside word context to avoid breaking real "I am" etc.
PDF_SPLIT_NAME = re.compile(r"\b([A-ZÀ-Ý])\s+(?=[a-zà-ÿ])")

# Common artefact in unicode-aware text extraction: combining chars left raw.
# We just normalize via NFC.

# Local copy of the institution-keyword regex (matches "University", "Institute",
# etc.). Used to decide whether a comma in an affiliation slot is a separator
# between distinct affiliations or part of one institution's name.
INST_KW_RE = re.compile(
    r"\b(Universit[áèéëâ]?|Institut[ae]?|Universität|Université|"
    r"School|College|Department|Laboratory|\bLab\b|Research|"
    r"Inc\.?|Corp\.?|Group|Foundation|Cent[er]+|Co\.|Ltd|GmbH|"
    r"National|Federal|Royal|Faculty|Academy|Office|Hospital|"
    r"University|Center)\b",
    re.I,
)

# Useful country labels for a "Region" column
COUNTRY_REGION_DISPLAY = {
    "USA": "USA",
    "Canada": "Canada",
    "China": "China (Mainland)",
    "Hong Kong": "Hong Kong",
    "Taiwan": "Taiwan",
    "South Korea": "South Korea",
    "Japan": "Japan",
    "Singapore": "Singapore",
    "UK": "UK",
    "Switzerland": "Switzerland",
    "Germany": "Germany",
    "France": "France",
    "Israel": "Israel",
    "UAE": "Middle East",
    "Saudi Arabia": "Middle East",
    "Iran": "Middle East",
    "India": "India",
    "Australia": "Australia & NZ",
    "New Zealand": "Australia & NZ",
}


def sanitize(s: str | float) -> str:
    if not isinstance(s, str):
        return "" if s is None or (isinstance(s, float) and pd.isna(s)) else str(s)
    s = unicodedata.normalize("NFC", s)
    s = ILLEGAL_CTRL.sub(" ", s)
    # Collapse any sequence of stray marker glyphs surrounded by whitespace into a single space
    s = STRAY_MARKERS.sub(" ", s)
    # Heal split capital words ("Y oung" → "Young", "Z u" → "Zu")
    s = PDF_SPLIT_NAME.sub(r"\1", s)
    # Collapse multiple spaces and stray punctuation patterns
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",\s*,", ",", s)
    s = s.strip().strip(",;:.")
    return s


def split_authors(s: str) -> list[str]:
    return [sanitize(a) for a in str(s or "").split(";") if a.strip()]


def split_affils_by_author(s: str) -> list[str]:
    """Per-author affiliation list (semicolon-separated, in author order)."""
    return [sanitize(a) for a in str(s or "").split(";") if a.strip()]


def _normalize_per_author(slot: str) -> list[tuple[str, str]]:
    """Resolve a single per-author affiliation slot to (canonical name, country)
    pairs.

    Heuristic:
      - Count institution keywords ('University', 'Institute', 'Lab', etc.) in
        the slot.
      - If ≥2, treat commas as separators between distinct affiliations. Keep
        every piece, even ones whose canonical resolution falls back to
        'Other' (so 'Vector Institute' survives even without an explicit rule).
      - If 0–1, treat commas as part of one institution name (e.g. 'Institute
        of Mechanics, Armenia' is one place, not two).
    """
    slot = sanitize(slot)
    if not slot:
        return []

    inst_kw_count = len(INST_KW_RE.findall(slot))

    if inst_kw_count >= 2:
        out: list[tuple[str, str]] = []
        for piece in slot.split(","):
            piece = piece.strip()
            if not piece:
                continue
            name, country = m.normalise(piece)
            if name:
                out.append((name, country or "Other"))
        # Dedupe while preserving order.
        seen: set[str] = set()
        uniq: list[tuple[str, str]] = []
        for n, c in out:
            if n not in seen:
                uniq.append((n, c))
                seen.add(n)
        return uniq

    # Single-affiliation fallback: try the whole slot and keep what we get.
    full_name, full_country = m.normalise(slot)
    if full_name:
        return [(full_name, full_country or "Other")]
    return []


def canonicalize_affils(s: str) -> tuple[str, str, str]:
    """For one row's full institution string, return:
        canonical_set:   '; '.join of unique canonical names
        countries:       '; '.join of unique countries
        regions:         '; '.join of unique display regions
    """
    seen_n: set[str] = set()
    names: list[str] = []
    seen_c: set[str] = set()
    countries: list[str] = []
    for slot in str(s or "").split(";"):
        for name, country in _normalize_per_author(slot):
            if name and name not in seen_n:
                names.append(name)
                seen_n.add(name)
            if country and country not in seen_c:
                countries.append(country)
                seen_c.add(country)

    regions_out: list[str] = []
    seen_r: set[str] = set()
    for c in countries:
        r = COUNTRY_REGION_DISPLAY.get(c) or m.COUNTRY_REGION.get(c, "Other")
        if r not in seen_r:
            regions_out.append(r)
            seen_r.add(r)

    return "; ".join(names), "; ".join(countries), "; ".join(regions_out)


def main():
    df = pd.read_csv(SRC)
    n = len(df)
    print(f"Loaded {n} accepted papers from {SRC.name}")

    # Use merged best-available columns
    authors = df["Authors_best"].fillna("").astype(str)
    insts = df["Institutions_best"].fillna("").astype(str)

    # Sanitize raw text per row
    cleaned_authors = authors.apply(lambda s: "; ".join(split_authors(s)))
    cleaned_insts = insts.apply(lambda s: "; ".join(split_affils_by_author(s)))

    # Canonical / countries / regions
    canon = insts.apply(canonicalize_affils)
    canon_names = canon.apply(lambda t: t[0])
    canon_countries = canon.apply(lambda t: t[1])
    canon_regions = canon.apply(lambda t: t[2])

    def _clean_text(s):
        return sanitize(latex_to_unicode(s if isinstance(s, str) else ""))

    out = pd.DataFrame({
        "Decision":               df["Decision"].apply(sanitize),
        "Title":                  df["Title"].apply(_clean_text),
        "Authors":                cleaned_authors,
        "Institutions":           cleaned_insts,
        "Institutions_canonical": canon_names,
        "Countries":              canon_countries,
        "Regions":                canon_regions,
        "Affiliation_source":     df["Affil_source"].fillna("").apply(sanitize),
        "Primary_Area":           df["Primary Area"].fillna("").apply(sanitize) if "Primary Area" in df.columns else "",
        "Keywords":               df["Keywords"].fillna("").apply(_clean_text) if "Keywords" in df.columns else "",
        "Abstract":               df["Abstract"].fillna("").apply(_clean_text) if "Abstract" in df.columns else "",
        "OpenReview_URL":         df["OpenReview URL"].fillna("").apply(sanitize) if "OpenReview URL" in df.columns else "",
    })

    # Final per-cell sweep — make sure no control chars or unicode issues remain.
    for c in out.columns:
        out[c] = out[c].apply(sanitize)

    # CSV with UTF-8 BOM so Excel opens it without mojibake.
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig",
               quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    print(f"Wrote {OUT_CSV}  ({len(out)} rows)")

    # XLSX export (openpyxl rejects some control chars; we already stripped them).
    try:
        out.to_excel(OUT_XLSX, index=False)
        print(f"Wrote {OUT_XLSX}")
    except Exception as e:
        print(f"xlsx export failed ({e}); CSV is canonical.")

    # Quick summary
    pdf_count = (df["Affil_source"] == "pdf").sum()
    print()
    print(f"Affiliation source breakdown:")
    print(f"  PDF (paper title block): {pdf_count}  ({100*pdf_count/n:.1f}%)")
    print(f"  PDF parse failed:        {(df['Affil_source'] == 'parse_fail').sum()}")
    print(f"  No PDF (404 / missing):  {(df['Affil_source'] == 'no_pdf').sum()}")


if __name__ == "__main__":
    main()
