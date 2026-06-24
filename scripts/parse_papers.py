#!/usr/bin/env python3
"""Parse PDFs under papers/ and write a spreadsheet summary."""
from pathlib import Path
import argparse
import csv
import sys
import zipfile
from html import escape
try:
    import pandas as pd
except Exception:
    pd = None

# Ensure repo root is on sys.path so sibling modules import correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parse_arbitrary_pdf_first_page import parse_arbitrary_pdf


FIELDNAMES = [
    "filename",
    "title",
    "authors",
    "affiliations",
    "institution_set",
    "abstract",
    "pattern",
    "parse_status",
    "problem_solved",
    "how_solved",
]


def _write_minimal_xlsx(rows: list[dict], out_xlsx: Path) -> None:
    """Write a basic Excel workbook using only the Python standard library."""
    data = [FIELDNAMES] + [[str(row.get(field, "")) for field in FIELDNAMES] for row in rows]

    def cell_ref(row_idx: int, col_idx: int) -> str:
        letters = ""
        col = col_idx
        while col:
            col, rem = divmod(col - 1, 26)
            letters = chr(65 + rem) + letters
        return f"{letters}{row_idx}"

    sheet_rows = []
    for r_idx, row in enumerate(data, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            cells.append(
                f'<c r="{cell_ref(r_idx, c_idx)}" t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(value)}"
                "</t></is></c>"
            )
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="papers_parsed" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    with zipfile.ZipFile(out_xlsx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def summarize_folder(folder: Path, out_csv: Path, out_xlsx: Path | None = None):
    rows = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() == ".pdf":
            r = parse_arbitrary_pdf(p)
            if r.get("success"):
                authors = "; ".join(r.get("authors", []))
                # join affiliations per author as 'Author -> A;B'
                affs = []
                for a, af in zip(r.get("authors", []), r.get("affiliations_per_author", [])):
                    affs.append(f"{a} -> {', '.join(af) if af else 'N/A'}")
                affiliations = "; ".join(affs)
                inst_set = "; ".join(r.get("institutions_set", []))
                pattern = r.get("pattern")
                abstract = r.get("abstract", "")
                problem = r.get("problem_solved", "")
                solution = r.get("how_solved", "")
            else:
                authors = ""
                affiliations = ""
                inst_set = ""
                pattern = r.get("pattern")
                abstract = r.get("abstract", "")
                problem = ""
                solution = ""
            rows.append({
                "filename": p.name,
                "title": r.get("title", ""),
                "authors": authors,
                "affiliations": affiliations,
                "institution_set": inst_set,
                "abstract": abstract,
                "pattern": pattern,
                "parse_status": "success" if r.get("success") else r.get("reason", "failed"),
                "problem_solved": problem,
                "how_solved": solution,
            })

    # write CSV
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    # optionally write Excel if pandas is available
    if out_xlsx and pd is not None:
        df = pd.DataFrame(rows)
        df.to_excel(out_xlsx, index=False)
    elif out_xlsx:
        _write_minimal_xlsx(rows, out_xlsx)


if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Extract paper metadata from the first page of a directory of PDFs."
    )
    parser.add_argument("--pdf-dir", type=Path, default=base / "papers")
    parser.add_argument("--out-csv", type=Path, default=base / "data/papers_parsed.csv")
    parser.add_argument("--out-xlsx", type=Path, default=base / "data/papers_parsed.xlsx")
    args = parser.parse_args()

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    args.out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    print(f"Parsing PDFs in {args.pdf_dir} -> {args.out_csv}")
    summarize_folder(args.pdf_dir, args.out_csv, args.out_xlsx)
    print("Done.")
