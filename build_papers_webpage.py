#!/usr/bin/env python3
"""Parse research-paper PDFs and generate a static arXiv-like browser."""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
from pathlib import Path

from scripts.parse_papers import summarize_folder


def read_rows(source: Path) -> list[dict[str, str]]:
    with source.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["authors_list"] = [item.strip() for item in row.get("authors", "").split(";") if item.strip()]
        row["institutions_list"] = [
            item.strip() for item in row.get("institution_set", "").split(";") if item.strip()
        ]
    return rows


def page_html(rows: list[dict[str, str]], title: str, csv_href: str) -> str:
    payload = json.dumps(rows, ensure_ascii=False)
    safe_title = html.escape(title)
    safe_csv_href = html.escape(csv_href, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{
      --ink: #202124;
      --muted: #5f6368;
      --line: #d8dadd;
      --paper: #ffffff;
      --soft: #f6f7f8;
      --accent: #8c1d18;
      --accent-2: #1f6f78;
      --mark: #fff3bf;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: #f2f1ef;
      font-family: Arial, Helvetica, sans-serif;
      line-height: 1.45;
    }}
    header {{
      background: var(--accent);
      color: #fff;
      border-bottom: 4px solid #5e120f;
    }}
    .topbar {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 22px 16px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: end;
    }}
    h1 {{
      margin: 0;
      font-size: 25px;
      letter-spacing: 0;
    }}
    .meta {{
      margin-top: 4px;
      color: #f1d6d4;
      font-size: 13px;
    }}
    .source-link {{
      color: #fff;
      text-decoration: none;
      border: 1px solid rgba(255,255,255,.45);
      padding: 8px 10px;
      border-radius: 4px;
      font-size: 13px;
      white-space: nowrap;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 22px 34px;
    }}
    .controls {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 12px;
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(180px, 270px) 150px;
      gap: 10px;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    input, select {{
      width: 100%;
      min-height: 38px;
      border: 1px solid #bfc4ca;
      border-radius: 4px;
      padding: 8px 10px;
      background: #fff;
      color: var(--ink);
      font-size: 14px;
    }}
    .count {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
      white-space: nowrap;
    }}
    .papers {{
      margin-top: 14px;
      display: grid;
      gap: 12px;
    }}
    article {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-left: 5px solid var(--accent);
      border-radius: 4px;
      padding: 14px 16px 15px;
    }}
    .title-row {{
      display: flex;
      gap: 12px;
      align-items: baseline;
      justify-content: space-between;
    }}
    h2 {{
      margin: 0;
      font-size: 19px;
      line-height: 1.28;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .authors {{
      margin-top: 8px;
      color: #1f2933;
      font-size: 14px;
    }}
    .authors span {{
      color: var(--muted);
      font-weight: 700;
    }}
    .institutions {{
      margin-top: 8px;
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .tag {{
      border: 1px solid #c8d6d8;
      background: #edf7f8;
      color: #174f57;
      padding: 3px 7px;
      border-radius: 4px;
      font-size: 12px;
    }}
    details {{
      margin-top: 10px;
      border-top: 1px solid #eceff1;
      padding-top: 9px;
    }}
    summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 700;
      font-size: 13px;
    }}
    .abstract {{
      margin: 8px 0 0;
      color: #33383d;
      font-size: 14px;
    }}
    .mini {{
      margin-top: 10px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .mini strong {{
      color: #42474d;
    }}
    mark {{
      background: var(--mark);
      padding: 0 1px;
    }}
    .empty {{
      background: var(--paper);
      border: 1px solid var(--line);
      padding: 26px;
      border-radius: 4px;
      color: var(--muted);
      text-align: center;
    }}
    @media (max-width: 780px) {{
      .topbar {{
        grid-template-columns: 1fr;
        align-items: start;
      }}
      .controls {{
        grid-template-columns: 1fr;
        position: static;
      }}
      .count {{ text-align: left; }}
      .title-row {{
        display: block;
      }}
      .mini {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>{safe_title}</h1>
        <div class="meta">{len(rows)} papers parsed from first-page PDFs</div>
      </div>
      <a class="source-link" href="{safe_csv_href}">CSV</a>
    </div>
  </header>
  <main>
    <section class="controls" aria-label="Paper filters">
      <input id="search" type="search" placeholder="Search title, author, affiliation, abstract">
      <select id="institution"><option value="">All institutions</option></select>
      <select id="sort">
        <option value="title">Title</option>
        <option value="authors">Authors</option>
        <option value="institution">Institution</option>
      </select>
      <div id="count" class="count"></div>
    </section>
    <section id="papers" class="papers"></section>
  </main>
  <script>
    const PAPERS = {payload};

    const state = {{
      q: "",
      institution: "",
      sort: "title"
    }};

    const search = document.querySelector("#search");
    const institution = document.querySelector("#institution");
    const sort = document.querySelector("#sort");
    const papers = document.querySelector("#papers");
    const count = document.querySelector("#count");

    function escapeHtml(value) {{
      return String(value || "").replace(/[&<>"']/g, ch => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[ch]));
    }}

    function highlight(value) {{
      const text = escapeHtml(value || "");
      if (!state.q) return text;
      const terms = state.q.split(/\\s+/).filter(Boolean).slice(0, 5);
      let out = text;
      for (const term of terms) {{
        const safe = term.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&");
        out = out.replace(new RegExp(`(${{safe}})`, "ig"), "<mark>$1</mark>");
      }}
      return out;
    }}

    function paperText(row) {{
      return [
        row.title,
        row.authors,
        row.affiliations,
        row.institution_set,
        row.abstract,
        row.problem_solved,
        row.how_solved
      ].join(" ").toLowerCase();
    }}

    function populateInstitutions() {{
      const counts = new Map();
      for (const row of PAPERS) {{
        for (const inst of row.institutions_list || []) {{
          counts.set(inst, (counts.get(inst) || 0) + 1);
        }}
      }}
      const options = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      institution.insertAdjacentHTML("beforeend", options.map(([name, n]) =>
        `<option value="${{escapeHtml(name)}}">${{escapeHtml(name)}} (${{n}})</option>`
      ).join(""));
    }}

    function filteredRows() {{
      const q = state.q.toLowerCase();
      const rows = PAPERS.filter(row => {{
        const matchesText = !q || paperText(row).includes(q);
        const matchesInstitution = !state.institution || (row.institutions_list || []).includes(state.institution);
        return matchesText && matchesInstitution;
      }});
      rows.sort((a, b) => {{
        if (state.sort === "authors") return (a.authors || "").localeCompare(b.authors || "");
        if (state.sort === "institution") return (a.institution_set || "").localeCompare(b.institution_set || "");
        return (a.title || "").localeCompare(b.title || "");
      }});
      return rows;
    }}

    function render() {{
      const rows = filteredRows();
      count.textContent = `${{rows.length}} / ${{PAPERS.length}} papers`;
      if (!rows.length) {{
        papers.innerHTML = '<div class="empty">No matching papers</div>';
        return;
      }}
      papers.innerHTML = rows.map((row, idx) => {{
        const title = row.title || row.filename || "Untitled";
        const institutions = (row.institutions_list || []).length
          ? row.institutions_list.map(inst => `<span class="tag">${{highlight(inst)}}</span>`).join("")
          : '<span class="tag">N/A</span>';
        const abstract = row.abstract || [row.problem_solved, row.how_solved].filter(Boolean).join(" ");
        return `
          <article>
            <div class="title-row">
              <h2>${{highlight(title)}}</h2>
            </div>
            <div class="authors"><span>Authors:</span> ${{highlight(row.authors || "N/A")}}</div>
            <div class="institutions">${{institutions}}</div>
            <details open>
              <summary>Abstract</summary>
              <p class="abstract">${{highlight(abstract || "N/A")}}</p>
            </details>
            <div class="mini">
              <div><strong>Problem:</strong> ${{highlight(row.problem_solved || "N/A")}}</div>
              <div><strong>Approach:</strong> ${{highlight(row.how_solved || "N/A")}}</div>
            </div>
          </article>
        `;
      }}).join("");
    }}

    search.addEventListener("input", event => {{
      state.q = event.target.value.trim();
      render();
    }});
    institution.addEventListener("change", event => {{
      state.institution = event.target.value;
      render();
    }});
    sort.addEventListener("change", event => {{
      state.sort = event.target.value;
      render();
    }});

    populateInstitutions();
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse a directory of research-paper PDFs and generate an arXiv-style HTML page."
    )
    parser.add_argument("--pdf-dir", type=Path, default=Path("papers"))
    parser.add_argument("--title", default="Research Papers")
    args = parser.parse_args()

    source = Path("data/papers_parsed.csv")
    xlsx = Path("data/papers_parsed.xlsx")
    out = Path("web/papers.html")

    source.parent.mkdir(parents=True, exist_ok=True)
    print(f"Parsing PDFs in {args.pdf_dir} -> {source}")
    summarize_folder(args.pdf_dir, source, xlsx)

    rows = read_rows(source)
    out.parent.mkdir(parents=True, exist_ok=True)
    csv_href = Path(os.path.relpath(source.resolve(), out.resolve().parent)).as_posix()
    out.write_text(page_html(rows, args.title, csv_href), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
