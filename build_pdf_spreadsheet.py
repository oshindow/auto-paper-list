"""Re-generate the accepted-papers spreadsheet using PDF-derived affiliations.

For each accepted paper:
  1. Try to parse its PDF (iclr2026/pdfs/<forum_id>.pdf) for author affiliations.
  2. On success, write the per-author affiliations into the new sheet.
  3. On failure, fall back to the OpenReview-derived data already in the
     original spreadsheet (and tag the row).

Output:
  iclr2026/iclr2026_accepted_pdf.xlsx   — full sheet with PDF-derived data
  iclr2026/iclr2026_accepted_pdf.csv    — same, csv copy
  iclr2026/pdf_parse_summary.txt        — coverage stats + failure list
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_pdf_affiliations import parse_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parent
SRC_XLSX = ROOT / "data" / "iclr2026_accepted.xlsx"
PDF_DIR = ROOT / "data" / "pdfs"
OUT_XLSX = ROOT / "data" / "iclr2026_accepted_pdf.xlsx"
OUT_CSV = ROOT / "data" / "iclr2026_accepted_pdf.csv"
SUMMARY = ROOT / "data" / "pdf_parse_summary.txt"


def extract_forum_id(url: str) -> str | None:
    m = re.search(r"forum\?id=([^&]+)", str(url))
    return m.group(1) if m else None


def main():
    df = pd.read_excel(SRC_XLSX)
    df["forum_id"] = df["OpenReview URL"].map(extract_forum_id)
    n_total = len(df)
    print(f"Loaded {n_total} accepted papers from {SRC_XLSX.name}")

    pdf_inst: list[str | None] = []
    pdf_authors: list[str | None] = []
    pdf_pattern: list[str] = []
    pdf_status: list[str] = []  # 'pdf', 'pdf_no_pdf', 'pdf_parse_fail'

    n_have_pdf = 0
    n_parsed = 0
    n_no_pdf = 0
    n_fail = 0
    pattern_counts: dict[str, int] = {}

    t0 = time.time()
    for i, row in df.iterrows():
        fid = row["forum_id"]
        path = PDF_DIR / f"{fid}.pdf" if fid else None
        if not path or not path.exists():
            pdf_inst.append(None)
            pdf_authors.append(None)
            pdf_pattern.append("")
            pdf_status.append("no_pdf")
            n_no_pdf += 1
            continue
        n_have_pdf += 1
        r = parse_pdf(path)
        if r.get("success"):
            authors = r["authors"]
            per_author = r["affiliations_per_author"]
            joined_authors = "; ".join(authors)
            joined_insts = "; ".join(
                ", ".join(a) if a else "" for a in per_author
            )
            pdf_authors.append(joined_authors)
            pdf_inst.append(joined_insts)
            pdf_pattern.append(r["pattern"])
            pdf_status.append("pdf")
            n_parsed += 1
            pattern_counts[r["pattern"]] = pattern_counts.get(r["pattern"], 0) + 1
        else:
            pdf_inst.append(None)
            pdf_authors.append(None)
            pdf_pattern.append("")
            pdf_status.append("parse_fail")
            n_fail += 1

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / max(elapsed, 1e-9)
            eta = (n_total - i - 1) / max(rate, 1e-9)
            print(f"  {i+1}/{n_total}  rate={rate:.1f}/s  eta={eta/60:.1f}m  parsed={n_parsed}  fail={n_fail}  no_pdf={n_no_pdf}")

    elapsed = time.time() - t0

    # Sanitize per-cell illegal chars BEFORE adding to the dataframe.
    _ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    def _clean(v):
        if not isinstance(v, str):
            return v
        return _ILLEGAL.sub(" ", v)
    pdf_authors = [_clean(v) for v in pdf_authors]
    pdf_inst = [_clean(v) for v in pdf_inst]

    # Build the output dataframe.
    df["Authors_PDF"] = pdf_authors
    df["Institutions_PDF"] = pdf_inst
    df["PDF_pattern"] = pdf_pattern
    df["Affil_source"] = pdf_status
    # The merged "best available" columns: PDF if we have it, else fall back to
    # the existing OpenReview-derived data.
    df["Authors_best"] = df["Authors_PDF"].where(df["Authors_PDF"].notna(), df["Authors"])
    df["Institutions_best"] = df["Institutions_PDF"].where(df["Institutions_PDF"].notna(), df["Institutions"])
    # Source tag for the merged columns
    df["Affil_source_merged"] = df["Affil_source"].map({
        "pdf": "PDF",
        "no_pdf": "OpenReview (no PDF)",
        "parse_fail": "OpenReview (PDF parse failed)",
    })

    # Reorder for convenience
    cols = [c for c in df.columns if c != "forum_id"] + ["forum_id"]
    df = df[cols]

    # CSV write first — never fails, no validation.
    df.to_csv(OUT_CSV, index=False)
    print(f"Wrote {OUT_CSV}")

    # XLSX: aggressively sanitize. openpyxl rejects [\x00-\x08\x0b\x0c\x0e-\x1f]
    # but we also strip \x7f (DEL) and any other char above U+10000 just to be safe.
    _ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    def _clean(v):
        if not isinstance(v, str):
            return v
        v = _ILLEGAL.sub(" ", v)
        # Replace any chars in private-use / surrogate ranges
        v = "".join(c if (ord(c) < 0xD800 or 0xE000 <= ord(c) < 0xF8FF) else " " for c in v)
        # And anything above the BMP that openpyxl might choke on
        v = "".join(c if ord(c) < 0x10000 else " " for c in v)
        return v
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(_clean)

    df.to_excel(OUT_XLSX, index=False)
    print(f"Wrote {OUT_XLSX}  ({len(df)} rows)")
    print(f"Wrote {OUT_CSV}")

    summary = [
        f"ICLR 2026 PDF affiliation parsing summary",
        f"=========================================",
        f"Generated:                {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total accepted papers:    {n_total}",
        f"PDFs available locally:   {n_have_pdf}  ({100*n_have_pdf/n_total:.1f}%)",
        f"PDFs parsed successfully: {n_parsed}  ({100*n_parsed/n_total:.1f}%)",
        f"PDFs failed to parse:     {n_fail}",
        f"PDFs missing locally:     {n_no_pdf}",
        f"Pattern breakdown:",
    ]
    for k, v in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        summary.append(f"  {k}: {v}  ({100*v/max(n_parsed,1):.1f}% of parsed)")
    summary.append(f"Elapsed: {elapsed:.1f}s")
    SUMMARY.write_text("\n".join(summary) + "\n")
    print()
    print("\n".join(summary))


if __name__ == "__main__":
    main()
