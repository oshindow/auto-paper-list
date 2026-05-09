"""Retry PDFs that failed earlier with exponential backoff and slow polling."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
XLSX = ROOT / "data" / "iclr2026_accepted.xlsx"
OUT_DIR = ROOT / "data" / "pdfs"
LOG = ROOT / "data" / "pdf_downloads_retry.log"

WORKERS = 3
TIMEOUT = 90


def forum_ids() -> list[str]:
    df = pd.read_excel(XLSX)
    out = []
    for url in df["OpenReview URL"].dropna():
        m = re.search(r"forum\?id=([^&]+)", str(url))
        if m:
            out.append(m.group(1))
    return out


def download_one(fid: str) -> tuple[str, bool, str]:
    target = OUT_DIR / f"{fid}.pdf"
    if target.exists() and target.stat().st_size > 1024:
        return fid, True, "already-on-disk"
    url = f"https://openreview.net/pdf?id={fid}"
    last_err = ""
    for attempt in range(6):
        try:
            r = requests.get(url, timeout=TIMEOUT,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 429:
                wait = 60 + attempt * 30
                last_err = f"429 attempt {attempt}, sleeping {wait}s"
                time.sleep(wait)
                continue
            r.raise_for_status()
            if not r.content[:5].startswith(b"%PDF"):
                last_err = "non-PDF body"
                time.sleep(15)
                continue
            target.write_bytes(r.content)
            return fid, True, ""
        except Exception as e:
            last_err = repr(e)
            time.sleep(10 + attempt * 5)
    return fid, False, last_err


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    accepted = forum_ids()
    have = {p.stem for p in OUT_DIR.glob("*.pdf") if p.stat().st_size > 1024}
    todo = [fid for fid in accepted if fid not in have]
    print(f"Accepted papers: {len(accepted)}")
    print(f"Already on disk: {len(have & set(accepted))}")
    print(f"Will retry:      {len(todo)}")
    if not todo:
        return

    fails: list[tuple[str, str]] = []
    done = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(download_one, fid): fid for fid in todo}
        for f in as_completed(futures):
            fid, ok, err = f.result()
            done += 1
            if not ok:
                fails.append((fid, err))
            if done % 25 == 0:
                elapsed = time.time() - start
                rate = done / max(elapsed, 1e-9)
                print(f"  {done}/{len(todo)}  rate={rate:.2f}/s  fails={len(fails)}", flush=True)
    elapsed = time.time() - start
    print(f"\nDone {done - len(fails)}/{len(todo)} in {elapsed/60:.1f}m. {len(fails)} failures.")
    if fails:
        with LOG.open("w") as f:
            for fid, err in fails:
                f.write(f"{fid}\t{err}\n")
        print(f"Failures logged to {LOG}")


if __name__ == "__main__":
    main()
