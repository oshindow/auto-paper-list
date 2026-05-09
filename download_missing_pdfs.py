"""Download every accepted ICLR 2026 paper's PDF that isn't already on disk.

Reads forum IDs from iclr2026/iclr2026_accepted.xlsx, skips IDs that already
have a .pdf on disk, fetches the rest from openreview.net/pdf?id=<id> with a
small thread pool, and logs failures.
"""
from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
XLSX = ROOT / "data" / "iclr2026_accepted.xlsx"
OUT_DIR = ROOT / "data" / "pdfs"
LOG = ROOT / "data" / "pdf_downloads.log"

PDF_URL = "https://openreview.net/pdf?id={fid}"
WORKERS = 8
TIMEOUT = 60
MAX_RETRIES = 2


def forum_ids() -> list[str]:
    df = pd.read_excel(XLSX)
    ids: list[str] = []
    for url in df["OpenReview URL"].dropna():
        m = re.search(r"forum\?id=([^&]+)", str(url))
        if m:
            ids.append(m.group(1))
    return ids


def already_downloaded() -> set[str]:
    return {p.stem for p in OUT_DIR.glob("*.pdf")}


def download_one(fid: str) -> tuple[str, bool, str]:
    target = OUT_DIR / f"{fid}.pdf"
    url = PDF_URL.format(fid=fid)
    last_err = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=TIMEOUT, stream=True,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            head = r.content[:5] if hasattr(r, "content") else b""
            if not head.startswith(b"%PDF"):
                # Not a PDF — usually rate-limit HTML. Retry after a delay.
                last_err = f"non-pdf body (head={head!r})"
                time.sleep(2 + attempt * 2)
                continue
            target.write_bytes(r.content)
            return fid, True, ""
        except Exception as e:
            last_err = repr(e)
            time.sleep(1 + attempt)
    return fid, False, last_err


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_ids = forum_ids()
    have = already_downloaded()
    todo = [fid for fid in all_ids if fid not in have]
    print(f"Total accepted papers: {len(all_ids)}")
    print(f"Already on disk:       {len(have & set(all_ids))}")
    print(f"Will download:         {len(todo)}")

    if not todo:
        print("Nothing to do.")
        return

    failures: list[tuple[str, str]] = []
    done = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(download_one, fid): fid for fid in todo}
        for f in as_completed(futures):
            fid, ok, err = f.result()
            done += 1
            if not ok:
                failures.append((fid, err))
            if done % 50 == 0:
                elapsed = time.time() - start
                rate = done / max(elapsed, 1e-9)
                remaining = (len(todo) - done) / max(rate, 1e-9)
                print(f"  {done}/{len(todo)}  rate={rate:.1f}/s  eta={remaining/60:.1f}m  fails={len(failures)}")

    elapsed = time.time() - start
    print(f"\nDone: {done - len(failures)}/{len(todo)} succeeded in {elapsed/60:.1f}m")
    if failures:
        with LOG.open("w") as f:
            for fid, err in failures:
                f.write(f"{fid}\t{err}\n")
        print(f"  {len(failures)} failures logged to {LOG}")


if __name__ == "__main__":
    main()
