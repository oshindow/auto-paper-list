"""Build the ICLR 2026 top-institutions treemap.

Reads the accepted-papers spreadsheet with per-author affiliations, normalises
institution names, counts each institution at most once per paper (the same rule
the AI World NeurIPS leaderboard uses), and produces:

  - iclr2026/iclr2026_institutions_ranked.csv  (full ranked table)
  - iclr2026/iclr2026_top50_treemap.png        (LinkedIn-ready PNG)
  - iclr2026/iclr2026_top50_treemap.svg        (vector copy)

Run:
    python3 make_iclr_treemap.py
"""

from __future__ import annotations

import colorsys
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import pandas as pd
import squarify

ROOT = Path(__file__).resolve().parent
# The PDF-derived data (built by build_pdf_spreadsheet.py from the scraper
# output + parse_pdf_affiliations.py) lives at data/iclr2026_accepted_pdf.csv.
# We fall back to the public spreadsheet (data/iclr2026_public.csv), which
# ships with the repo and is the same data with cleaner column names.
DATA_DIR = ROOT / "data"
CSV_PDF = DATA_DIR / "iclr2026_accepted_pdf.csv"
PUBLIC_CSV = DATA_DIR / "iclr2026_public.csv"
XLSX_OR = DATA_DIR / "iclr2026_accepted.xlsx"
OUT_DIR = ROOT / "charts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# These are configured by configure_source() at the top of main().
SOURCE: Path = CSV_PDF
INST_COLUMN: str = "Institutions_best"
SOURCE_LABEL: str = "pdf"
SHAPE: str = "wide"  # "wide" (16:9) or "square" (1:1)
OUT_FILES: dict = {}
OUT_FILES_GROUPED: dict = {}
OUT_SENSITIVITY: Path = OUT_DIR / "iclr2026_method_sensitivity.csv"


def figure_dimensions() -> tuple[float, float, float, float, float, float]:
    """Returns (fig_w, fig_h, plot_x, plot_y, plot_w, plot_h) based on SHAPE."""
    if SHAPE == "square":
        # 18×18 square. Reserve more vertical space for the title/legend block.
        return 18.0, 18.0, 0.4, 2.6, 17.2, 14.4
    return 24.0, 13.5, 0.5, 2.1, 23.0, 10.0


def configure_source(mode: str, shape: str = "wide") -> None:
    """Set globals for `pdf` or `openreview` data source. Output filenames get
    a `_pdf` or `_openreview` suffix (and `_square` when shape=square) so the
    variants don't overwrite each other."""
    global SOURCE, INST_COLUMN, SOURCE_LABEL, SHAPE
    global OUT_FILES, OUT_FILES_GROUPED, OUT_SENSITIVITY

    SHAPE = shape
    shape_suffix = "_square" if shape == "square" else ""

    if mode == "pdf":
        if CSV_PDF.exists():
            SOURCE = CSV_PDF
            INST_COLUMN = "Institutions_best"
        elif PUBLIC_CSV.exists():
            # Default for repo users — same data, different column name.
            SOURCE = PUBLIC_CSV
            INST_COLUMN = "Institutions"
        else:
            raise SystemExit(
                "No PDF-derived data found. Either run the full pipeline "
                "(scrape → download_pdfs → build_pdf_spreadsheet) or download "
                "data/iclr2026_public.csv from the repo's release."
            )
        SOURCE_LABEL = "pdf"
        suffix = "_pdf" + shape_suffix
    elif mode == "openreview":
        if not XLSX_OR.exists():
            raise SystemExit(
                "OpenReview spreadsheet not found. Run scrape_openreview.py "
                "first (requires OPENREVIEW_USERNAME/OPENREVIEW_PASSWORD env)."
            )
        SOURCE = XLSX_OR
        INST_COLUMN = "Institutions"
        SOURCE_LABEL = "openreview"
        suffix = "_openreview" + shape_suffix
    else:
        raise SystemExit(f"unknown mode: {mode}")

    OUT_FILES = {
        "unique":       (OUT_DIR / f"iclr2026_institutions_ranked_unique{suffix}.csv",
                         OUT_DIR / f"iclr2026_top50_treemap_unique{suffix}.png",
                         OUT_DIR / f"iclr2026_top50_treemap_unique{suffix}.svg"),
        "first_author": (OUT_DIR / f"iclr2026_institutions_ranked_first_author{suffix}.csv",
                         OUT_DIR / f"iclr2026_top50_treemap_first_author{suffix}.png",
                         OUT_DIR / f"iclr2026_top50_treemap_first_author{suffix}.svg"),
        "fractional":   (OUT_DIR / f"iclr2026_institutions_ranked_fractional{suffix}.csv",
                         OUT_DIR / f"iclr2026_top50_treemap_fractional{suffix}.png",
                         OUT_DIR / f"iclr2026_top50_treemap_fractional{suffix}.svg"),
    }
    OUT_FILES_GROUPED = {
        "unique":       (OUT_DIR / f"iclr2026_top50_treemap_unique_grouped{suffix}.png",
                         OUT_DIR / f"iclr2026_top50_treemap_unique_grouped{suffix}.svg"),
        "first_author": (OUT_DIR / f"iclr2026_top50_treemap_first_author_grouped{suffix}.png",
                         OUT_DIR / f"iclr2026_top50_treemap_first_author_grouped{suffix}.svg"),
        "fractional":   (OUT_DIR / f"iclr2026_top50_treemap_fractional_grouped{suffix}.png",
                         OUT_DIR / f"iclr2026_top50_treemap_fractional_grouped{suffix}.svg"),
    }
    OUT_SENSITIVITY = OUT_DIR / f"iclr2026_method_sensitivity{suffix}.csv"

METHOD_DESCRIPTIONS = {
    "unique":       "each institution counted once per paper",
    "first_author": "first-author institution only",
    "fractional":   "fractional credit (1/N where N = distinct institutions on the paper)",
}
METHOD_TITLES = {
    "unique":       "Unique-affiliation method · each institution counted once per paper",
    "first_author": "First-author method · only the first author's institution",
    "fractional":   "Fractional method · each paper split 1/N across its institutions",
}

# ---------------------------------------------------------------------------
# Normalisation rules
# ---------------------------------------------------------------------------
# Each rule is (regex on the lowercased, pre-cleaned string, canonical name,
# country/region). The first matching rule wins, so list more-specific rules
# above more-general ones (e.g. "microsoft research asia" before "microsoft").

RULES: list[tuple[str, str, str]] = [
    # ---------------- USA ----------------
    (r"\b(massachusetts institute of technology|mit\b|mit csail|csail)\b", "MIT", "USA"),
    (r"\b(stanford)\b", "Stanford University", "USA"),
    (r"\b(carnegie mellon|cmu\b)\b", "Carnegie Mellon University", "USA"),
    (r"\b(uc berkeley|berkeley|university of california,? berkeley|university of california berkeley)\b", "UC Berkeley", "USA"),
    (r"\b(princeton)\b", "Princeton University", "USA"),
    (r"\b(harvard)\b", "Harvard University", "USA"),
    (r"\b(yale)\b", "Yale University", "USA"),
    (r"\b(cornell)\b", "Cornell University", "USA"),
    (r"\b(columbia university)\b", "Columbia University", "USA"),
    (r"\b(university of pennsylvania|upenn\b|wharton)\b", "University of Pennsylvania", "USA"),
    (r"\b(new york university|nyu\b)\b", "NYU", "USA"),
    (r"\b(university of chicago)\b", "University of Chicago", "USA"),
    (r"\b(johns hopkins|jhu\b)\b", "Johns Hopkins University", "USA"),
    (r"\b(university of washington|uw seattle|uw,? seattle)\b", "University of Washington", "USA"),
    (r"\b(university of southern california|usc\b)\b", "USC", "USA"),
    (r"\b(university of california,? los angeles|ucla\b)\b", "UCLA", "USA"),
    (r"\b(university of california,? san diego|ucsd\b)\b", "UC San Diego", "USA"),
    (r"\b(university of california,? santa barbara|ucsb\b)\b", "UC Santa Barbara", "USA"),
    (r"\b(university of california,? santa cruz|ucsc\b)\b", "UC Santa Cruz", "USA"),
    (r"\b(university of california,? irvine|uci\b)\b", "UC Irvine", "USA"),
    (r"\b(university of california,? davis|ucd\b)\b", "UC Davis", "USA"),
    (r"\b(university of california,? riverside)\b", "UC Riverside", "USA"),
    (r"\b(university of california,? merced)\b", "UC Merced", "USA"),
    (r"\b(university of california\b(?!,))", "University of California (other)", "USA"),
    (r"\b(university of illinois.*urbana|uiuc\b|university of illinois,? urbana|illinois urbana)\b", "UIUC", "USA"),
    (r"\b(university of illinois.*chicago|uic\b)\b", "University of Illinois Chicago", "USA"),
    (r"\b(georgia institute of technology|georgia tech)\b", "Georgia Tech", "USA"),
    (r"\b(university of texas,? austin|ut austin|university of texas at austin)\b", "UT Austin", "USA"),
    (r"\b(university of texas at dallas|ut dallas)\b", "UT Dallas", "USA"),
    (r"\b(texas a&m\b|texas a and m)\b", "Texas A&M University", "USA"),
    (r"\b(university of michigan)\b", "University of Michigan", "USA"),
    (r"\b(michigan state)\b", "Michigan State University", "USA"),
    (r"\b(university of maryland)\b", "University of Maryland", "USA"),
    (r"\b(university of wisconsin)\b", "University of Wisconsin–Madison", "USA"),
    (r"\b(university of minnesota)\b", "University of Minnesota", "USA"),
    (r"\b(university of massachusetts.*amherst|umass amherst)\b", "UMass Amherst", "USA"),
    (r"\b(purdue)\b", "Purdue University", "USA"),
    (r"\b(rice university)\b", "Rice University", "USA"),
    (r"\b(duke university)\b", "Duke University", "USA"),
    (r"\b(brown university)\b", "Brown University", "USA"),
    (r"\b(dartmouth)\b", "Dartmouth College", "USA"),
    (r"\b(northwestern university)\b", "Northwestern University", "USA"),
    (r"\b(northeastern university)\b", "Northeastern University", "USA"),
    (r"\b(university of virginia)\b", "University of Virginia", "USA"),
    (r"\b(virginia tech|virginia polytechnic)\b", "Virginia Tech", "USA"),
    (r"\b(university of north carolina|unc(\b| chapel)|chapel hill)\b", "UNC Chapel Hill", "USA"),
    (r"\b(north carolina state)\b", "NC State University", "USA"),
    (r"\b(arizona state)\b", "Arizona State University", "USA"),
    (r"\b(university of arizona)\b", "University of Arizona", "USA"),
    (r"\b(pennsylvania state|penn state)\b", "Penn State", "USA"),
    (r"\b(rutgers)\b", "Rutgers University", "USA"),
    (r"\b(stony brook|state university of new york at stony brook)\b", "Stony Brook University", "USA"),
    (r"\b(state university of new york at buffalo|university at buffalo|suny buffalo)\b", "University at Buffalo", "USA"),
    (r"\b(ohio state)\b", "Ohio State University", "USA"),
    (r"\b(boston university)\b", "Boston University", "USA"),
    (r"\b(university of central florida|ucf\b)\b", "University of Central Florida", "USA"),
    (r"\b(rensselaer)\b", "RPI", "USA"),
    (r"\b(rochester institute of technology|rit\b)\b", "RIT", "USA"),
    (r"\b(university of rochester)\b", "University of Rochester", "USA"),
    (r"\b(university of pittsburgh)\b", "University of Pittsburgh", "USA"),
    (r"\b(notre dame)\b", "University of Notre Dame", "USA"),
    (r"\b(washington university,? saint louis|washu)\b", "Washington University in St. Louis", "USA"),
    (r"\b(emory)\b", "Emory University", "USA"),
    (r"\b(case western)\b", "Case Western Reserve University", "USA"),
    (r"\b(stevens institute)\b", "Stevens Institute of Technology", "USA"),
    (r"\b(lehigh)\b", "Lehigh University", "USA"),
    (r"\b(george mason)\b", "George Mason University", "USA"),
    (r"\b(toyota technological institute|tti(-| )?chicago)\b", "TTIC", "USA"),
    (r"\b(allen institute)\b", "Allen Institute for AI", "USA"),
    (r"\b(flatiron institute)\b", "Flatiron Institute", "USA"),
    (r"\b(university of houston)\b", "University of Houston", "USA"),
    (r"\b(simon fraser)\b", "Simon Fraser University", "Canada"),
    (r"\b(california institute of technology|caltech)\b", "Caltech", "USA"),
    (r"\b(new jersey institute of technology|njit)\b", "NJIT", "USA"),
    (r"\b(university of connecticut)\b", "University of Connecticut", "USA"),
    (r"\b(university of florida)\b", "University of Florida", "USA"),
    (r"\b(university of utah)\b", "University of Utah", "USA"),
    (r"\b(iowa state)\b", "Iowa State University", "USA"),
    (r"\b(worcester polytechnic)\b", "WPI", "USA"),
    (r"\b(university of georgia)\b", "University of Georgia", "USA"),
    (r"\b(tulane)\b", "Tulane University", "USA"),
    (r"\b(university of tulsa)\b", "University of Tulsa", "USA"),
    (r"\b(brookhaven)\b", "Brookhaven National Lab", "USA"),
    (r"\b(lawrence livermore)\b", "Lawrence Livermore National Lab", "USA"),

    # ---------------- USA Industry ----------------
    (r"\b(google deepmind|deepmind)\b", "Google DeepMind", "UK"),
    (r"\b(google research|research,? google|google\b)\b", "Google", "USA"),
    (r"\b(microsoft)\b", "Microsoft", "USA"),
    (r"\b(meta ai|fair\b|facebook|meta\b)\b", "Meta", "USA"),
    (r"\b(nvidia)\b", "NVIDIA", "USA"),
    (r"\b(apple)\b", "Apple", "USA"),
    (r"\b(amazon)\b", "Amazon", "USA"),
    (r"\b(openai)\b", "OpenAI", "USA"),
    (r"\b(anthropic)\b", "Anthropic", "USA"),
    (r"\b(adobe)\b", "Adobe", "USA"),
    (r"\b(salesforce)\b", "Salesforce", "USA"),
    (r"\b(intel\b)\b", "Intel", "USA"),
    (r"\b(advanced micro devices|amd\b)\b", "AMD", "USA"),
    (r"\b(qualcomm)\b", "Qualcomm", "USA"),
    (r"\b(international business machines|ibm\b)\b", "IBM", "USA"),
    (r"\b(scale ai)\b", "Scale AI", "USA"),
    (r"\b(snap inc|snapchat)\b", "Snap", "USA"),
    (r"\b(genentech)\b", "Genentech", "USA"),
    (r"\b(thinking machines)\b", "Thinking Machines Lab", "USA"),
    (r"\b(physical intelligence)\b", "Physical Intelligence", "USA"),
    (r"\b(luma ai)\b", "Luma AI", "USA"),
    (r"\b(cohere)\b", "Cohere", "Canada"),
    (r"\b(hugging ?face)\b", "Hugging Face", "USA"),
    (r"\b(\bxai\b)", "xAI", "USA"),
    (r"\b(cisco)\b", "Cisco", "USA"),
    (r"\b(oracle)\b", "Oracle", "USA"),
    (r"\b(spotify)\b", "Spotify", "Sweden"),
    (r"\b(snowflake)\b", "Snowflake", "USA"),
    (r"\b(morgan stanley)\b", "Morgan Stanley", "USA"),
    (r"\b(toyota research)\b", "Toyota Research Institute", "USA"),
    (r"\b(cerebras)\b", "Cerebras", "USA"),
    (r"\b(sandboxaq)\b", "SandboxAQ", "USA"),
    (r"\b(accenture)\b", "Accenture", "USA"),
    (r"\b(lambda\b)", "Lambda", "USA"),
    (r"\b(earth species)\b", "Earth Species Project", "USA"),

    # ---------------- China Mainland ----------------
    (r"\b(tsinghua)\b", "Tsinghua University", "China"),
    (r"\b(peking university)\b", "Peking University", "China"),
    (r"\b(zhejiang university)\b", "Zhejiang University", "China"),
    (r"\b(shanghai jiao ?tong)\b", "Shanghai Jiao Tong University", "China"),
    (r"\b(fudan)\b", "Fudan University", "China"),
    (r"\b(university of science and technology of china|ustc\b)\b", "USTC", "China"),
    (r"\b(university of the chinese academy of sciences|university of chinese academy of sciences|ucas\b)\b", "UCAS", "China"),
    (r"\b(institute of automation|cas institute of automation)\b", "CAS Institute of Automation", "China"),
    (r"\b(institute of computing technology,? chinese academy)\b", "CAS Institute of Computing Technology", "China"),
    (r"\b(institute of software,? chinese academy)\b", "CAS Institute of Software", "China"),
    (r"\b(institute of information engineering,? chinese academy)\b", "CAS Institute of Information Engineering", "China"),
    (r"\b(shenyang institute of automation)\b", "CAS Shenyang Institute of Automation", "China"),
    (r"\b(shenzhen institutes? of advanced technology)\b", "CAS Shenzhen Institute of Advanced Tech", "China"),
    (r"\b(chinese academy of sciences|cas\b|china,? cas)\b", "Chinese Academy of Sciences", "China"),
    (r"\b(nanjing university of aeronautics|nuaa\b)\b", "Nanjing U. of Aeronautics & Astronautics", "China"),
    (r"\b(nanjing university of science and technology|njust)\b", "Nanjing U. of Science & Technology", "China"),
    (r"\b(nanjing university)\b", "Nanjing University", "China"),
    (r"\b(harbin institute of technology|hit\b)\b", "Harbin Institute of Technology", "China"),
    (r"\b(renmin university)\b", "Renmin University of China", "China"),
    (r"\b(beijing university of aeronautics and astronautics|beihang|buaa)\b", "Beihang University", "China"),
    (r"\b(beijing institute of technology)\b", "Beijing Institute of Technology", "China"),
    (r"\b(beijing jiaotong)\b", "Beijing Jiaotong University", "China"),
    (r"\b(beijing university of posts? and telecommunication|beijing university of post|bupt)\b", "BUPT", "China"),
    (r"\b(beijing university of technology)\b", "Beijing U. of Technology", "China"),
    (r"\b(beijing normal)\b", "Beijing Normal University", "China"),
    (r"\b(beijing academy of artificial intelligence|baai)\b", "BAAI", "China"),
    (r"\b(beijing institute for general artificial intelligence|bigai)\b", "BIGAI", "China"),
    (r"\b(huazhong university of science and technology|hust\b)\b", "HUST (Wuhan)", "China"),
    (r"\b(south china university of technology|scut)\b", "South China U. of Technology", "China"),
    (r"\b(sun yat-?sen)\b", "Sun Yat-sen University", "China"),
    (r"\b(east china normal)\b", "East China Normal University", "China"),
    (r"\b(xi'?an jiaotong-?liverpool)\b", "Xi’an Jiaotong–Liverpool University", "China"),
    (r"\b(xi'?an jiaotong)\b", "Xi’an Jiaotong University", "China"),
    (r"\b(xidian|xi'?an university of electronic science)\b", "Xidian University", "China"),
    (r"\b(westlake)\b", "Westlake University", "China"),
    (r"\b(shanghai artificial intelligence laboratory|shanghai ai lab(oratory)?|shanghai aritifcal)\b", "Shanghai AI Lab", "China"),
    (r"shanghai innovation institut", "Shanghai Innovation Institute", "China"),
    (r"\b(shanghai jiaotong)\b", "Shanghai Jiao Tong University", "China"),
    (r"\b(shanghaitech)\b", "ShanghaiTech University", "China"),
    (r"\b(shanghai university of finance and economics|sufe)\b", "Shanghai U. of Finance & Economics", "China"),
    (r"\b(shanghai university)\b", "Shanghai University", "China"),
    (r"\b(shanghai academy of artificial intelligence)\b", "Shanghai Academy of AI for Science", "China"),
    (r"\b(southeast university)\b", "Southeast University", "China"),
    (r"\b(southern university of science and technology|sustech)\b", "SUSTech", "China"),
    (r"\b(tongji)\b", "Tongji University", "China"),
    (r"\b(xiamen)\b", "Xiamen University", "China"),
    (r"\b(tianjin university)\b", "Tianjin University", "China"),
    (r"\b(nankai)\b", "Nankai University", "China"),
    (r"\b(jilin university)\b", "Jilin University", "China"),
    (r"\b(shandong university)\b", "Shandong University", "China"),
    (r"\b(sichuan university)\b", "Sichuan University", "China"),
    (r"\b(wuhan university of technology)\b", "Wuhan U. of Technology", "China"),
    (r"\b(wuhan university)\b", "Wuhan University", "China"),
    (r"\b(university of electronic science and technology of china|uestc)\b", "UESTC", "China"),
    (r"\b(shenzhen university of advanced technology)\b", "Shenzhen U. of Advanced Tech", "China"),
    (r"\b(shenzhen university)\b", "Shenzhen University", "China"),
    (r"\b(soochow university)\b", "Soochow University", "China"),
    (r"\b(central south university)\b", "Central South University", "China"),
    (r"\b(northwest polytechnical|northwestern polytechnical)\b", "Northwestern Polytechnical U.", "China"),
    (r"\b(national university of defense technology|nudt)\b", "NUDT", "China"),
    (r"\b(university of science and technology beijing|ustb)\b", "USTB", "China"),
    (r"\b(dalian university of technology)\b", "Dalian U. of Technology", "China"),
    (r"\b(zhengzhou university)\b", "Zhengzhou University", "China"),
    (r"\b(hunan university)\b", "Hunan University", "China"),
    (r"\b(anhui university)\b", "Anhui University", "China"),
    (r"\b(jinan university)\b", "Jinan University", "China"),
    (r"\b(hangzhou dianzi)\b", "Hangzhou Dianzi University", "China"),
    (r"\b(communication university of china)\b", "Communication U. of China", "China"),
    (r"\b(great bay university)\b", "Great Bay University", "China"),
    (r"\b(guangdong university of technology)\b", "Guangdong U. of Technology", "China"),
    (r"\b(guangzhou university)\b", "Guangzhou University", "China"),
    (r"\b(eastern institute of technology,? ningbo)\b", "Eastern Institute of Tech, Ningbo", "China"),
    (r"\b(zhongguancun academy)\b", "Zhongguancun Academy", "China"),
    (r"\b(peng cheng laboratory|pengcheng laboratory)\b", "Peng Cheng Lab", "China"),
    (r"\b(institute for ai industry research|air,? tsinghua)\b", "Tsinghua University", "China"),
    (r"\b(zhejiang university of technology)\b", "Zhejiang U. of Technology", "China"),
    (r"\b(hefei university of technology)\b", "Hefei U. of Technology", "China"),
    (r"\b(shanxi university)\b", "Shanxi University", "China"),
    (r"\b(chongqing university)\b", "Chongqing University", "China"),
    (r"\b(shahjalal)\b", "Shahjalal U. of Science & Technology", "Bangladesh"),

    # ---------------- China Industry ----------------
    (r"\b(alibaba)\b", "Alibaba", "China"),
    (r"\b(byte\s?dance|bytedance)\b", "ByteDance", "China"),
    (r"\b(huawei)\b", "Huawei", "China"),
    (r"\b(tencent)\b", "Tencent", "China"),
    (r"\b(baidu)\b", "Baidu", "China"),
    (r"\b(meituan)\b", "Meituan", "China"),
    (r"\b(kuaishou|快手)\b", "Kuaishou", "China"),
    (r"\b(ant group)\b", "Ant Group", "China"),
    (r"\b(xiaohongshu)\b", "Xiaohongshu", "China"),
    (r"\b(xiaomi)\b", "Xiaomi", "China"),
    (r"\b(jd\.com|jd ai)\b", "JD.com", "China"),
    (r"\b(sensetime)\b", "SenseTime", "China"),
    (r"\b(moonshot)\b", "Moonshot AI", "China"),
    (r"\b(stepfun)\b", "StepFun", "China"),
    (r"\b(tongyi)\b", "Alibaba", "China"),
    (r"\b(oppo)\b", "OPPO", "China"),
    (r"\b(vivo)\b", "Vivo", "China"),
    (r"\b(iflytek)\b", "iFlytek", "China"),
    (r"\b(li auto)\b", "Li Auto", "China"),
    (r"\b(china telecom)\b", "China Telecom", "China"),
    (r"\b(lenovo)\b", "Lenovo", "China"),
    (r"\b(2077ai)\b", "2077AI", "China"),

    # ---------------- Hong Kong / Macau ----------------
    (r"\b(hong kong university of science and technology|hkust)(\s*\(?guangzhou\)?| \(gz\))", "HKUST (Guangzhou)", "China"),
    (r"\b(hong kong university of science and technology|hkust)\b", "HKUST", "Hong Kong"),
    (r"\b(chinese university of hong kong|cuhk).*shenzhen", "CUHK (Shenzhen)", "China"),
    (r"\b(chinese university of hong kong|cuhk)\b", "CUHK", "Hong Kong"),
    (r"\b(university of hong kong|hku\b)\b", "University of Hong Kong", "Hong Kong"),
    (r"\b(hong kong polytechnic|polyu)\b", "Hong Kong Polytechnic University", "Hong Kong"),
    (r"\b(hong kong baptist|hkbu)\b", "Hong Kong Baptist University", "Hong Kong"),
    (r"\b(city university of hong kong)\b", "City University of Hong Kong", "Hong Kong"),
    (r"\b(lingnan university)\b", "Lingnan University", "Hong Kong"),
    (r"\b(university of macau)\b", "University of Macau", "China"),

    # ---------------- South Korea ----------------
    (r"\b(korea advanced institute of science.*technology|kaist)\b", "KAIST", "South Korea"),
    (r"\b(seoul national university|snu\b)\b", "Seoul National University", "South Korea"),
    (r"\b(yonsei)\b", "Yonsei University", "South Korea"),
    (r"\b(korea university)\b", "Korea University", "South Korea"),
    (r"\b(pohang university of science|postech)\b", "POSTECH", "South Korea"),
    (r"\b(ulsan national institute|unist)\b", "UNIST", "South Korea"),
    (r"\b(sungkyunkwan|sung kyun kwan|skku)\b", "SungKyunKwan University", "South Korea"),
    (r"\b(gwangju institute of science|gist)\b", "GIST", "South Korea"),
    (r"\b(kyung hee)\b", "Kyung Hee University", "South Korea"),
    (r"\b(chung-?ang)\b", "Chung-Ang University", "South Korea"),
    (r"\b(sogang)\b", "Sogang University", "South Korea"),
    (r"\b(naver)\b", "NAVER", "South Korea"),
    (r"\b(lg ai|lg corporation)\b", "LG AI Research", "South Korea"),
    (r"\b(krafton)\b", "KRAFTON", "South Korea"),
    (r"\b(sk telecom)\b", "SK Telecom", "South Korea"),
    (r"\b(electronics and telecommunications research institute|etri)\b", "ETRI", "South Korea"),
    (r"\b(samsung)\b", "Samsung", "South Korea"),

    # ---------------- Singapore ----------------
    (r"\b(national university of singapore|nus\b|national university of singaore)\b", "NUS", "Singapore"),
    (r"\b(nanyang technological university|ntu\b(?!.*tokyo)|ntu singapore)\b", "NTU Singapore", "Singapore"),
    (r"\b(singapore management university|smu\b)\b", "Singapore Management University", "Singapore"),
    (r"\b(singapore university of technology and design|sutd)\b", "SUTD", "Singapore"),
    (r"\b(a\*?star|astar)\b", "A*STAR", "Singapore"),
    (r"\b(sea ai lab)\b", "Sea AI Lab", "Singapore"),

    # ---------------- UK ----------------
    (r"\b(university of oxford|oxford)\b", "University of Oxford", "UK"),
    (r"\b(university of cambridge|cambridge)\b", "University of Cambridge", "UK"),
    (r"\b(imperial college)\b", "Imperial College London", "UK"),
    (r"\b(university college london|ucl\b)\b", "UCL", "UK"),
    (r"\b(king'?s college london|kcl)\b", "King’s College London", "UK"),
    (r"\b(queen mary)\b", "Queen Mary University of London", "UK"),
    (r"\b(university of edinburgh)\b", "University of Edinburgh", "UK"),
    (r"\b(university of bristol)\b", "University of Bristol", "UK"),
    (r"\b(university of manchester)\b", "University of Manchester", "UK"),
    (r"\b(university of birmingham)\b", "University of Birmingham", "UK"),
    (r"\b(university of glasgow)\b", "University of Glasgow", "UK"),
    (r"\b(university of southampton)\b", "University of Southampton", "UK"),
    (r"\b(university of liverpool)\b", "University of Liverpool", "UK"),
    (r"\b(university of exeter)\b", "University of Exeter", "UK"),
    (r"\b(university of surrey)\b", "University of Surrey", "UK"),
    (r"\b(university of bath)\b", "University of Bath", "UK"),
    (r"\b(university of sheffield)\b", "University of Sheffield", "UK"),
    (r"\b(lancaster university)\b", "Lancaster University", "UK"),
    (r"\b(university of warwick)\b", "University of Warwick", "UK"),

    # ---------------- Canada ----------------
    (r"\b(university of toronto)\b", "University of Toronto", "Canada"),
    (r"(université de montréal|universite de montreal|university of montr|\bmontreal\b|mcgill|\bmila\b|hec montréal|mila - quebec)", "McGill / U Montréal / Mila", "Canada"),
    (r"\b(university of british columbia|ubc\b)\b", "University of British Columbia", "Canada"),
    (r"\b(university of waterloo)\b", "University of Waterloo", "Canada"),
    (r"\b(university of alberta)\b", "University of Alberta", "Canada"),
    (r"\b(concordia university)\b", "Concordia University", "Canada"),
    (r"\b(simon fraser)\b", "Simon Fraser University", "Canada"),
    (r"\b(western university|university of western ontario)\b", "Western University", "Canada"),
    (r"\b(université du québec|école de technologie supérieure|ets,? québec)\b", "ÉTS Montréal", "Canada"),

    # ---------------- Switzerland ----------------
    (r"\b(eth ?z(urich|ürich)?|swiss federal institute of technology(?! lausanne))", "ETH Zurich", "Switzerland"),
    (r"\b(epfl|swiss federal institute of technology lausanne|épf lausanne|epf lausanne)\b", "EPFL", "Switzerland"),
    (r"\b(university of zurich|uzh)\b", "University of Zurich", "Switzerland"),
    (r"\b(university of basel)\b", "University of Basel", "Switzerland"),

    # ---------------- Germany ----------------
    (r"\b(technische universität münchen|technical university of munich|technical university munich|tu munich|tum\b)", "Technical University of Munich", "Germany"),
    (r"\b(ludwig-maximilians|lmu munich)\b", "LMU Munich", "Germany"),
    (r"\b(eberhard-?karls-?universität tübingen|university of tübingen|university of tuebingen)\b", "University of Tübingen", "Germany"),
    (r"\b(ellis institute tübingen)\b", "ELLIS Institute Tübingen", "Germany"),
    (r"\b(rheinische friedrich-?wilhelms.*bonn|university of bonn)\b", "University of Bonn", "Germany"),
    (r"\b(ruprecht-?karls-?universität heidelberg|heidelberg university)\b", "Heidelberg University", "Germany"),
    (r"\b(karlsruher institut|karlsruhe institute of technology|kit\b)", "KIT", "Germany"),
    (r"\b(technische universität darmstadt|tu darmstadt)\b", "TU Darmstadt", "Germany"),
    (r"\b(technische universität berlin|tu berlin)\b", "TU Berlin", "Germany"),
    (r"\b(technische universität dortmund|tu dortmund)\b", "TU Dortmund", "Germany"),
    (r"\b(deutsches krebsforschungszentrum|dkfz)\b", "DKFZ Heidelberg", "Germany"),
    (r"\b(saarland|universität des saarlandes|saarland informatics)\b", "Saarland University", "Germany"),
    (r"\b(mpi-?sws|max planck institute for software systems)\b", "MPI for Software Systems", "Germany"),
    (r"\b(max planck institute for intelligent systems|max-?planck institute|mpi(\b| ))", "Max Planck Institutes", "Germany"),
    (r"\b(rheinland-?pfälzische technische universität)\b", "RPTU Kaiserslautern–Landau", "Germany"),
    (r"\b(german research center for ai|dfki)\b", "DFKI", "Germany"),
    (r"\b(fraunhofer)\b", "Fraunhofer", "Germany"),
    (r"\b(university of technology nuremberg)\b", "U. of Technology Nuremberg", "Germany"),
    (r"\b(hasso plattner)\b", "Hasso Plattner Institute", "Germany"),
    (r"\b(bosch)\b", "Bosch", "Germany"),

    # ---------------- France ----------------
    (r"\b(\binria\b)", "Inria", "France"),
    (r"\b(école polytechnique|ecole polytechnique)\b", "École Polytechnique", "France"),
    (r"\b(télécom paris|telecom paris)\b", "Télécom Paris", "France"),
    (r"\b(\bcnrs\b)", "CNRS", "France"),
    (r"\b(sorbonne|paris vi)\b", "Sorbonne Université", "France"),
    (r"\b(sciences po|paris iii|sorbonne-?nouvelle)\b", "Sciences Po / Sorbonne-Nouvelle", "France"),
    (r"\b(université gustave eiffel)\b", "Université Gustave Eiffel", "France"),
    (r"\b(ensae|école nationale de la statistique)\b", "ENSAE Paris", "France"),

    # ---------------- Other Europe ----------------
    (r"\b(university of amsterdam|uva\b)\b", "University of Amsterdam", "Netherlands"),
    (r"\b(delft university of technology|tu delft)\b", "TU Delft", "Netherlands"),
    (r"\b(eindhoven university of technology|tu/e)\b", "TU Eindhoven", "Netherlands"),
    (r"\b(ku leuven)\b", "KU Leuven", "Belgium"),
    (r"\b(vrije universiteit brussel)\b", "Vrije Universiteit Brussel", "Belgium"),
    (r"\b(aalto)\b", "Aalto University", "Finland"),
    (r"\b(university of helsinki)\b", "University of Helsinki", "Finland"),
    (r"\b(kth royal institute of technology|kth\b)\b", "KTH Royal Institute of Technology", "Sweden"),
    (r"\b(university of oslo)\b", "University of Oslo", "Norway"),
    (r"\b(university of copenhagen)\b", "University of Copenhagen", "Denmark"),
    (r"\b(technical university of denmark|dtu)\b", "Technical University of Denmark", "Denmark"),
    (r"\b(istituto italiano di tecnologia|iit\b(?!.*delhi)|iit -)\b", "Italian Institute of Technology", "Italy"),
    (r"\b(politecnico di milano|polytechnic institute of milan)\b", "Politecnico di Milano", "Italy"),
    (r"\b(bocconi)\b", "Bocconi University", "Italy"),
    (r"\b(sapienza|university of roma|university of rome|università di pisa|university of pisa)\b", "Sapienza / U. Pisa", "Italy"),
    (r"\b(university of trento)\b", "University of Trento", "Italy"),
    (r"\b(university of modena)\b", "University of Modena and Reggio Emilia", "Italy"),
    (r"\b(\bist austria\b|institute of science and technology austria)\b", "IST Austria", "Austria"),
    (r"\b(institute of science and technology(?! austria)| isto?\b)\b", "Institute of Science Tokyo", "Japan"),
    (r"\b(johannes kepler|jku linz)\b", "JKU Linz", "Austria"),
    (r"\b(technische universität wien|tu wien)\b", "TU Wien", "Austria"),
    (r"\b(czech technical|cvut)\b", "Czech Technical University Prague", "Czechia"),
    (r"\b(university of helsinki)\b", "University of Helsinki", "Finland"),
    (r"\b(universitat pompeu fabra|upf)\b", "Universitat Pompeu Fabra", "Spain"),
    (r"\b(instituto superior técnico|tecnico lisboa)\b", "Instituto Superior Técnico", "Portugal"),
    (r"\b(higher school of economics)\b", "HSE Moscow", "Russia"),
    (r"\b(lomonosov|moscow state university)\b", "Lomonosov MSU", "Russia"),
    (r"\b(yandex)\b", "Yandex", "Russia"),
    (r"\b(innopolis)\b", "Innopolis University", "Russia"),
    (r"\b(institute of numerical mathematics)\b", "Institute of Numerical Mathematics", "Russia"),
    (r"\b(moscow independent research)\b", "Moscow Indep. Research Institute of AI", "Russia"),
    (r"\b(cispa)\b", "CISPA", "Germany"),
    (r"\b(aithyra)\b", "Aithyra", "Austria"),

    # ---------------- Israel ----------------
    (r"\b(technion)\b", "Technion", "Israel"),
    (r"\b(tel aviv university)\b", "Tel Aviv University", "Israel"),
    (r"\b(weizmann)\b", "Weizmann Institute of Science", "Israel"),
    (r"\b(hebrew university)\b", "Hebrew University of Jerusalem", "Israel"),
    (r"\b(ben gurion|ben-gurion)\b", "Ben Gurion University", "Israel"),

    # ---------------- Middle East ----------------
    (r"\b(mohamed bin zayed|mbzuai)\b", "MBZUAI", "UAE"),
    (r"\b(king abdullah university|kaust)\b", "KAUST", "Saudi Arabia"),
    (r"\b(sharif university)\b", "Sharif University of Technology", "Iran"),

    # ---------------- Australia / NZ ----------------
    (r"\b(university of melbourne)\b", "University of Melbourne", "Australia"),
    (r"\b(university of sydney)\b", "University of Sydney", "Australia"),
    (r"\b(university of new south wales|unsw)\b", "UNSW Sydney", "Australia"),
    (r"\b(university of technology sydney|uts\b)\b", "UTS Sydney", "Australia"),
    (r"\b(monash)\b", "Monash University", "Australia"),
    (r"\b(australian national university|anu\b)\b", "Australian National University", "Australia"),
    (r"\b(university of queensland)\b", "University of Queensland", "Australia"),
    (r"\b(university of adelaide|adelaide university)\b", "University of Adelaide", "Australia"),
    (r"\b(royal melbourne institute of technology|rmit)\b", "RMIT University", "Australia"),
    (r"\b(university of western australia)\b", "U. of Western Australia", "Australia"),
    (r"\b(deakin)\b", "Deakin University", "Australia"),
    (r"\b(griffith university)\b", "Griffith University", "Australia"),
    (r"\b(\bcsiro\b)", "CSIRO", "Australia"),
    (r"\b(university of auckland)\b", "University of Auckland", "New Zealand"),
    (r"\b(victoria university of wellington)\b", "Victoria U. Wellington", "New Zealand"),

    # ---------------- Japan ----------------
    (r"\b(university of tokyo|utokyo)\b", "University of Tokyo", "Japan"),
    (r"\b(institute of science tokyo|tokyo institute of technology|tokyo tech)\b", "Institute of Science Tokyo", "Japan"),
    (r"\b(kyoto university)\b", "Kyoto University", "Japan"),
    (r"\b(osaka university)\b", "Osaka University", "Japan"),
    (r"\b(tohoku university)\b", "Tohoku University", "Japan"),
    (r"\b(\briken\b)", "RIKEN", "Japan"),
    (r"\b(\bntt\b)", "NTT", "Japan"),
    (r"\b(sony ai|sony group|sony research)\b", "Sony", "Japan"),
    (r"\b(sakana ai)\b", "Sakana AI", "Japan"),

    # ---------------- Taiwan ----------------
    (r"\b(national taiwan university|ntu taipei)\b", "National Taiwan University", "Taiwan"),
    (r"\b(national yang ming chiao tung|nycu)\b", "NYCU", "Taiwan"),

    # ---------------- India ----------------
    (r"\b(indian institute of technology,? delhi|iit delhi)\b", "IIT Delhi", "India"),
    (r"\b(indian institute of technology,? bombay|iit bombay)\b", "IIT Bombay", "India"),
    (r"\b(indian institute of technology|iit\b(?! delhi))", "Indian Institute of Technology", "India"),
    (r"\b(indian institute of science|iisc)\b", "Indian Institute of Science", "India"),

    # ---------------- Other Asia ----------------
    (r"\b(tiktok)\b", "TikTok", "China"),
    (r"\b(naver labs europe)\b", "NAVER Labs Europe", "France"),
]

COMPILED = [(re.compile(p, re.IGNORECASE), name, country) for p, name, country in RULES]

# Country -> region for treemap colour grouping
COUNTRY_REGION = {
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
    "Netherlands": "Other Europe",
    "Belgium": "Other Europe",
    "Sweden": "Other Europe",
    "Finland": "Other Europe",
    "Norway": "Other Europe",
    "Denmark": "Other Europe",
    "Italy": "Other Europe",
    "Austria": "Other Europe",
    "Czechia": "Other Europe",
    "Spain": "Other Europe",
    "Portugal": "Other Europe",
    "Russia": "Other Europe",
    "Ireland": "Other Europe",
    "Israel": "Israel",
    "UAE": "Middle East",
    "Saudi Arabia": "Middle East",
    "Iran": "Middle East",
    "Australia": "Australia & NZ",
    "New Zealand": "Australia & NZ",
    "India": "India",
    "Bangladesh": "South Asia",
    "Other": "Other / Unknown",
}

# Canonical names that count as "industry" (corporate / for-profit research labs).
# Everything else — universities, government/non-profit research institutes — is
# treated as "academia" for the within-region shade.
INDUSTRY_NAMES: set[str] = {
    # USA + global
    "Google", "Google DeepMind", "Microsoft", "Meta", "Amazon", "Apple",
    "NVIDIA", "Adobe", "Salesforce", "IBM", "Intel", "AMD", "Qualcomm",
    "OpenAI", "Anthropic", "xAI", "Cisco", "Oracle", "Spotify", "Snowflake",
    "Snap", "Toyota Research Institute", "Cerebras", "SandboxAQ",
    "Hugging Face", "Cohere", "Genentech", "Thinking Machines Lab",
    "Physical Intelligence", "Luma AI", "Lambda", "Earth Species Project",
    "Morgan Stanley", "Accenture", "Scale AI",
    # China industry
    "Alibaba", "ByteDance", "Tencent", "Huawei", "Baidu", "Meituan",
    "Kuaishou", "Ant Group", "Xiaohongshu", "Xiaomi", "JD.com",
    "SenseTime", "Moonshot AI", "StepFun", "OPPO", "Vivo", "iFlytek",
    "Li Auto", "China Telecom", "Lenovo", "2077AI", "TikTok",
    # Japan industry
    "NTT", "Sony", "Sakana AI",
    # Korea industry
    "NAVER", "LG AI Research", "Samsung", "KRAFTON", "SK Telecom",
    # Other
    "Yandex", "Bosch", "NAVER Labs Europe", "Sea AI Lab",
}


def is_industry(name: str) -> bool:
    return name in INDUSTRY_NAMES


REGION_COLORS = {
    "USA":               "#1f77b4",  # blue
    "China (Mainland)":  "#d62728",  # red
    "Hong Kong": "#ff7f0e",  # orange
    "South Korea":       "#17becf",  # cyan
    "Singapore":         "#9467bd",  # purple
    "UK":                "#2ca02c",  # green
    "Switzerland":       "#8c564b",  # brown
    "Germany":           "#e377c2",  # pink
    "France":            "#bcbd22",  # olive
    "Other Europe":      "#7f7f7f",  # grey
    "Canada": "#5b9bd5",  # light blue
    "Israel":            "#c49c2b",  # gold
    "Japan":             "#f29ec4",  # rose
    "Australia & NZ":    "#6db95a",  # leaf
    "Middle East":       "#a87a3d",  # bronze
    "Taiwan":            "#5cb8a8",  # teal-light
    "India":             "#e08214",  # ochre
    "South Asia":        "#cdb079",
    "Other / Unknown":   "#bdbdbd",
}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
DEPT_PREFIXES = re.compile(
    r"""^\s*
        (
            department\ of\ [^,]+,\s* |
            school\ of\ [^,]+,\s* |
            college\ of\ [^,]+,\s* |
            faculty\ of\ [^,]+,\s* |
            computer\ science\ department,?\s* |
            electrical\ engineering\ &\ computer\ science\ department,?\s* |
            ai\ labs,\s* |
            research,?\s*
        )+
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalise(raw: str) -> tuple[str | None, str | None]:
    """Map a raw affiliation string to (canonical name, country).

    Returns (None, None) if the string is empty/garbage.
    Returns (raw_cleaned, "Other") if no rule matches.
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    s = s.strip(",;\"'` \t")
    if not s:
        return None, None
    # Strip leading "Department of X," / "School of Y," repeatedly
    s = DEPT_PREFIXES.sub("", s).strip()
    if not s:
        return None, None
    low = s.lower()
    for pat, name, country in COMPILED:
        if pat.search(low):
            return name, country
    # No match — return cleaned version with "Other" country
    # Trim repeated self-mentions like "Foo, Foo"
    parts = [p.strip() for p in s.split(",")]
    if len(parts) >= 2 and parts[0].lower() == parts[1].lower():
        s = parts[0]
    return s, "Other"


# ---------------------------------------------------------------------------
# Counting — three methods
# ---------------------------------------------------------------------------
def _paper_canonical_lists(df: pd.DataFrame):
    """For each paper, yield (ordered_canonical_list, ordered_country_list)."""
    for s in df[INST_COLUMN].fillna(""):
        names: list[str] = []
        countries: list[str] = []
        for raw in str(s).split(";"):
            name, country = normalise(raw)
            if not name:
                continue
            names.append(name)
            countries.append(country or "Other")
        yield names, countries


def count_unique_per_paper(df):
    """AI World rule: each institution counted once per paper."""
    counter: Counter = Counter()
    name_to_country: dict[str, str] = {}
    for names, countries in _paper_canonical_lists(df):
        seen: set[str] = set()
        for n, c in zip(names, countries):
            if n in seen:
                continue
            seen.add(n)
            counter[n] += 1
            name_to_country.setdefault(n, c)
    return counter, name_to_country


def count_first_author(df):
    """Each paper contributes 1 to the institution of its first listed author."""
    counter: Counter = Counter()
    name_to_country: dict[str, str] = {}
    for names, countries in _paper_canonical_lists(df):
        if not names:
            continue
        n, c = names[0], countries[0]
        counter[n] += 1
        name_to_country.setdefault(n, c)
    return counter, name_to_country


def count_fractional(df):
    """Each paper split 1/N across its N distinct institutions."""
    counter: Counter = Counter()
    name_to_country: dict[str, str] = {}
    for names, countries in _paper_canonical_lists(df):
        seen: set[str] = set()
        ordered = []
        for n, c in zip(names, countries):
            if n in seen:
                continue
            seen.add(n)
            ordered.append((n, c))
        if not ordered:
            continue
        share = 1.0 / len(ordered)
        for n, c in ordered:
            counter[n] += share
            name_to_country.setdefault(n, c)
    return counter, name_to_country


# ---------------------------------------------------------------------------
# Treemap rendering
# ---------------------------------------------------------------------------
def _wrap_name(name: str, max_chars: int) -> str:
    """Greedy word-wrap into lines no longer than max_chars."""
    if len(name) <= max_chars or " " not in name:
        return name
    words = name.split()
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _best_fit(name: str, w_pt: float, h_pt: float, max_fs: float = 30.0):
    """Pick a wrap and fontsize so that `name` fits in a w_pt x h_pt box.

    Returns (fontsize, wrapped_name, n_lines) or (None, None, None).
    """
    # DejaVu Sans Bold avg char width ≈ 0.62 * fontsize at common sizes.
    char_w_factor = 0.62
    line_h_factor = 1.20
    best = None
    for n_lines in (1, 2, 3, 4):
        max_chars = max(4, int(len(name) / n_lines) + 4)
        wrapped = _wrap_name(name, max_chars=max_chars)
        actual_lines = wrapped.count("\n") + 1
        if actual_lines > n_lines:
            continue
        longest = max(len(line) for line in wrapped.split("\n"))
        fs_w = w_pt / max(1, longest * char_w_factor)
        fs_h = h_pt / (actual_lines * line_h_factor)
        fs = min(max_fs, fs_w, fs_h)
        if fs < 5.5:
            continue
        if best is None or fs > best[0]:
            best = (fs, wrapped, actual_lines)
    return best if best else (None, None, None)


def _text_pt_w(text: str, fs: float, char_w_factor: float = 0.55) -> float:
    """Approximate width in pt of `text` (regular weight) at `fs`."""
    return len(text) * char_w_factor * fs


def _draw_label(ax, x, y, w, h, name, count, pct):
    """Draw institution name + percentage + count inside a rectangle.

    `w` and `h` are in matplotlib data coordinates which equal inches on the page.
    The count `(N)` is always shown so the chart never drops paper-count info.
    Returns a list of (text_obj, kind, rect_bounds) for post-render fit checking.
    """
    placed: list[tuple] = []
    if w < 0.35 or h < 0.22:
        return placed

    PT_PER_IN = 72.0
    pad = 0.14
    inner_w_pt = max(1.0, (w - 2 * pad) * PT_PER_IN)
    inner_h_pt = max(1.0, (h - 2 * pad) * PT_PER_IN)

    # Allocate ~60% of cell height to the name, ~30% to the suffix.
    name_h_pt = inner_h_pt * 0.62
    suffix_h_pt = inner_h_pt * 0.32

    suffix_full = f"{pct:.1f}%   ({count})"
    suffix_short = f"({count})"

    fs_name, wrapped, n_lines = _best_fit(name, inner_w_pt, name_h_pt, max_fs=26.0)
    cx = x + w / 2

    # ---- Path A: full label (name + suffix) ----
    if fs_name is not None and fs_name >= 6.5:
        # Suffix fontsize: aim for 0.65× name, capped by the suffix vertical budget.
        suffix_fs = max(4.5, min(fs_name * 0.65, suffix_h_pt / 1.20))
        suffix = ""
        # Try full, then short
        for cand in (suffix_full, suffix_short):
            if _text_pt_w(cand, suffix_fs) <= inner_w_pt:
                suffix = cand
                break
        # If even "(N)" doesn't fit at suffix_fs, shrink suffix_fs until it does.
        if not suffix:
            tmp = suffix_short
            sfs = suffix_fs
            while sfs > 4.0 and _text_pt_w(tmp, sfs) > inner_w_pt:
                sfs -= 0.5
            if _text_pt_w(tmp, sfs) <= inner_w_pt:
                suffix = tmp
                suffix_fs = sfs
        # If even at 4pt it doesn't fit, fall through to Path B.
        if suffix:
            gap_pt = 4.0
            total_h_pt = n_lines * fs_name * 1.20 + gap_pt + suffix_fs * 1.20
            total_h_in = total_h_pt / PT_PER_IN
            top = y + h / 2 - total_h_in / 2
            t_name = ax.text(
                cx, top, wrapped,
                ha="center", va="top",
                fontsize=fs_name, color="white", fontweight="bold",
                linespacing=1.05,
            )
            placed.append((t_name, "name", (x, y, w, h)))
            suffix_y = top + (n_lines * fs_name * 1.20 + gap_pt) / PT_PER_IN
            t_suf = ax.text(
                cx, suffix_y, suffix,
                ha="center", va="top",
                fontsize=suffix_fs, color="#f0f0f0", fontweight="normal",
            )
            placed.append((t_suf, "pct", (x, y, w, h)))
            return placed

    # ---- Path B: name + (N) only, smaller, side-by-side or stacked ----
    # The name fits but the cell is too short for two lines + suffix.
    # Render a compact two-line label: short name + (N) on second line.
    fs_compact, wrapped_compact, n_lines_c = _best_fit(name, inner_w_pt, inner_h_pt * 0.65, max_fs=16.0)
    if fs_compact is not None and fs_compact >= 5.0:
        suffix_fs = max(4.0, fs_compact * 0.75)
        # If "(N)" doesn't fit at suffix_fs, shrink it.
        tmp = suffix_short
        while suffix_fs > 3.5 and _text_pt_w(tmp, suffix_fs) > inner_w_pt:
            suffix_fs -= 0.3
        gap_pt = 2.5
        total_h_pt = n_lines_c * fs_compact * 1.20 + gap_pt + suffix_fs * 1.20
        total_h_in = total_h_pt / PT_PER_IN
        top = y + h / 2 - total_h_in / 2
        t_name = ax.text(
            cx, top, wrapped_compact,
            ha="center", va="top",
            fontsize=fs_compact, color="white", fontweight="bold",
            linespacing=1.05,
        )
        placed.append((t_name, "name_only", (x, y, w, h)))
        suffix_y = top + (n_lines_c * fs_compact * 1.20 + gap_pt) / PT_PER_IN
        t_suf = ax.text(
            cx, suffix_y, suffix_short,
            ha="center", va="top",
            fontsize=suffix_fs, color="#f0f0f0", fontweight="normal",
        )
        placed.append((t_suf, "pct", (x, y, w, h)))
        return placed

    # ---- Path C: abbreviation + (N) ----
    if w >= 0.4 and h >= 0.26:
        abbrev = "".join(p[0] for p in name.split() if p and p[0].isalpha())[:4].upper() or "·"
        # Compact 2-line render: abbrev on top, (N) on bottom
        ab_fs = 8.0
        cnt_fs = 6.5
        gap_pt = 2.0
        total_h_pt = ab_fs * 1.20 + gap_pt + cnt_fs * 1.20
        total_h_in = total_h_pt / PT_PER_IN
        top = y + h / 2 - total_h_in / 2
        t = ax.text(
            cx, top, abbrev,
            ha="center", va="top",
            fontsize=ab_fs, color="white", fontweight="bold",
        )
        placed.append((t, "abbrev", (x, y, w, h)))
        cnt_y = top + (ab_fs * 1.20 + gap_pt) / PT_PER_IN
        t2 = ax.text(
            cx, cnt_y, suffix_short,
            ha="center", va="top",
            fontsize=cnt_fs, color="#f0f0f0",
        )
        placed.append((t2, "pct", (x, y, w, h)))
    return placed


def _shrink_overflowing_text(fig, ax, placed):
    """Measure each placed text and shrink/hide if it overflows its rectangle.

    Iterates up to 3 times (each iteration shrinks the worst overflows; subsequent
    iterations catch anything that's still too big).
    """
    inv = ax.transData.inverted()

    for _ in range(3):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        any_changed = False
        for t, kind, (x, y, w, h) in placed:
            if not t.get_visible():
                continue
            bbox = t.get_window_extent(renderer)
            (x0, y0) = inv.transform((bbox.x0, bbox.y0))
            (x1, y1) = inv.transform((bbox.x1, bbox.y1))
            tw = abs(x1 - x0)
            th = abs(y1 - y0)
            pad = 0.10
            avail_w = max(0.01, w - 2 * pad)
            # Vertical budget: name gets ~55% if pct is also drawn, name_only gets full
            if kind == "name":
                avail_h = max(0.01, (h - 2 * pad) * 0.62)
            elif kind == "pct":
                avail_h = max(0.01, (h - 2 * pad) * 0.32)
            elif kind == "region_header":
                avail_w = max(0.01, w - 0.30)  # extra horizontal padding for headers
                avail_h = max(0.01, h * 0.85)
            else:
                avail_h = max(0.01, h - 2 * pad)

            scale_w = avail_w / tw if tw > avail_w else 1.0
            scale_h = avail_h / th if th > avail_h else 1.0
            scale = min(scale_w, scale_h)
            if scale < 0.99:
                new_fs = t.get_fontsize() * scale * 0.97
                if kind == "region_header":
                    # Headers should NEVER vanish. Floor at 5pt.
                    t.set_fontsize(max(5.0, new_fs))
                else:
                    # Institution labels: hide if the result would be unreadably tiny.
                    if new_fs < 4.0:
                        t.set_visible(False)
                    else:
                        t.set_fontsize(new_fs)
                any_changed = True
        if not any_changed:
            break


def render_treemap(top_df: pd.DataFrame, total_papers: int, out_png: Path, out_svg: Path,
                   method_key: str):
    sizes = top_df["count"].astype(float).tolist()
    labels = top_df["institution"].tolist()
    counts = top_df["count"].tolist()
    pcts = top_df["percentage"].tolist()
    regions = top_df["region"].tolist()
    colors = [REGION_COLORS.get(r, REGION_COLORS["Other / Unknown"]) for r in regions]

    fig_w, fig_h, plot_x, plot_y, plot_w, plot_h = figure_dimensions()
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=160)
    fig.patch.set_facecolor("#0e1117")
    ax.set_facecolor("#0e1117")

    norm = squarify.normalize_sizes(sizes, plot_w, plot_h)
    rects = squarify.squarify(norm, plot_x, plot_y, plot_w, plot_h)

    placed_all: list[tuple] = []
    for r, color, name, count, pct in zip(rects, colors, labels, counts, pcts):
        rect = patches.Rectangle(
            (r["x"], r["y"]), r["dx"], r["dy"],
            facecolor=color, edgecolor="#0e1117", linewidth=2.5,
        )
        ax.add_patch(rect)
        # For fractional, count is a float — use rounded display for label
        display_count = int(round(count)) if isinstance(count, float) else count
        placed_all.extend(_draw_label(ax, r["x"], r["y"], r["dx"], r["dy"],
                                      name, display_count, pct))

    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.invert_yaxis()
    ax.set_axis_off()

    fig.text(
        0.022, 0.965,
        "Who is shaping the AI frontier at ICLR 2026?",
        color="white", fontsize=34, fontweight="bold", ha="left", va="top",
    )
    src_label = "affiliations from paper PDFs" if SOURCE_LABEL == "pdf" else "affiliations from OpenReview profiles"
    subtitle = (
        f"Top 50 institutions by accepted paper count   ·   "
        f"{total_papers:,} accepted papers   ·   {METHOD_DESCRIPTIONS[method_key]}   ·   {src_label}"
    )
    fig.text(
        0.022, 0.910, subtitle,
        color="#c8c8c8", fontsize=15.5, ha="left", va="top",
    )

    # Legend ordered by region prevalence in top-50
    region_totals = top_df.groupby("region")["count"].sum().sort_values(ascending=False)
    legend_regions = list(region_totals.index)
    handles = [
        patches.Patch(color=REGION_COLORS.get(r, REGION_COLORS["Other / Unknown"]), label=r)
        for r in legend_regions
    ]
    ax.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.07),
        ncol=min(len(handles), 8),
        frameon=False, labelcolor="#dddddd", fontsize=11,
    )

    fig.text(
        0.025, 0.025,
        f"Source: ICLR 2026 accepted papers (OpenReview).  Institution names normalized from per-author affiliations.  "
        f"Method shown: {METHOD_TITLES[method_key]}.",
        color="#9a9a9a", fontsize=9, ha="left", va="bottom",
    )
    fig.text(
        0.978, 0.040, "Dmytro Lopushanskyy",
        color="#e6e6e6", fontsize=13, ha="right", va="bottom", fontweight="bold",
    )
    fig.text(
        0.978, 0.022, "linkedin.com/in/dmytrolopushanskyy",
        color="#b8b8b8", fontsize=10.5, ha="right", va="bottom",
    )

    # Post-render: shrink any text that overflows its cell
    _shrink_overflowing_text(fig, ax, placed_all)

    fig.savefig(out_png, facecolor=fig.get_facecolor(), bbox_inches="tight", dpi=200)
    fig.savefig(out_svg, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def _shift_lightness(hex_color: str, delta: float) -> str:
    """Shift the lightness of a hex colour by delta in [-0.5, 0.5]."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    h2, l2, s2 = colorsys.rgb_to_hls(r, g, b)
    l2 = max(0.05, min(0.95, l2 + delta))
    r2, g2, b2 = colorsys.hls_to_rgb(h2, l2, s2)
    return "#{:02x}{:02x}{:02x}".format(int(r2 * 255), int(g2 * 255), int(b2 * 255))


def render_treemap_grouped(top_df: pd.DataFrame, total_papers: int,
                           out_png: Path, out_svg: Path, method_key: str):
    """Two-level treemap: regions are outer cells (sized by cumulative count);
    each region's institutions tile its interior."""

    # Aggregate by region, sort regions by total contribution
    region_totals = (
        top_df.groupby("region")["count"].sum().sort_values(ascending=False)
    )
    grand_total = float(region_totals.sum())

    fig_w, fig_h, plot_x, plot_y, plot_w, plot_h = figure_dimensions()
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=160)
    fig.patch.set_facecolor("#0e1117")
    ax.set_facecolor("#0e1117")

    region_norm = squarify.normalize_sizes(region_totals.tolist(), plot_w, plot_h)
    region_rects = squarify.squarify(region_norm, plot_x, plot_y, plot_w, plot_h)

    placed_all: list[tuple] = []
    region_borders: list[tuple] = []
    for (region_name, region_count), rrect in zip(region_totals.items(), region_rects):
        rx, ry, rw, rh = rrect["x"], rrect["y"], rrect["dx"], rrect["dy"]
        base_color = REGION_COLORS.get(region_name, REGION_COLORS["Other / Unknown"])

        # --- Decide header fontsize *first*, then size the strip to fit ---
        region_pct = 100.0 * region_count / grand_total
        header_text_full = f"{region_name}   ·   {region_pct:.1f}%"
        max_header_fs = 24.0
        approx_w_pt = (rw - 0.60) * 72.0  # generous horizontal padding
        # Pick the largest fontsize that fits the full header horizontally
        fs_w_full = approx_w_pt / max(1, len(header_text_full) * 0.62)
        # Cap by a "looks reasonable for this region size" heuristic
        ideal_fs = min(max_header_fs, max(9.0, rh * 7.0))
        header_fs = max(8.0, min(ideal_fs, fs_w_full))
        header_text = header_text_full
        # If percentage doesn't fit, drop it and re-size for just the region name
        if fs_w_full < 9.5:
            header_text = region_name
            fs_w_short = approx_w_pt / max(1, len(header_text) * 0.62)
            header_fs = max(8.0, min(ideal_fs, fs_w_short))

        # Strip height = fontsize × 1.7 (generous breathing room above and below text).
        # Allow up to 38% of region height; floor at 0.24 inch so tiny regions still
        # have a readable strip.
        strip_h_from_fs = header_fs * 1.70 / 72.0
        max_allowed_h = max(0.24, rh * 0.38)
        if strip_h_from_fs > max_allowed_h:
            header_fs = max(7.5, max_allowed_h * 72.0 / 1.70)
            strip_h_from_fs = header_fs * 1.70 / 72.0
        header_h = max(0.24, strip_h_from_fs)

        # Lightened header strip across the top of the region
        ax.add_patch(patches.Rectangle(
            (rx, ry), rw, header_h,
            facecolor=_shift_lightness(base_color, +0.22),
            edgecolor="none",
        ))
        t_header = ax.text(
            rx + rw / 2,
            ry + header_h / 2,
            header_text,
            ha="center", va="center",
            fontsize=header_fs, color="#0e1117", fontweight="bold",
        )
        placed_all.append((t_header, "region_header", (rx, ry, rw, header_h)))

        # Inner area for institutions
        inst_x = rx
        inst_y = ry + header_h
        inst_w = rw
        inst_h = rh - header_h
        if inst_h <= 0.10 or inst_w <= 0.10:
            region_borders.append((rx, ry, rw, rh))
            continue

        region_insts = top_df[top_df["region"] == region_name].copy()
        region_insts = region_insts.sort_values("count", ascending=False).reset_index(drop=True)
        if region_insts.empty:
            region_borders.append((rx, ry, rw, rh))
            continue

        inst_sizes = region_insts["count"].astype(float).tolist()
        inst_norm = squarify.normalize_sizes(inst_sizes, inst_w, inst_h)
        inst_rects = squarify.squarify(inst_norm, inst_x, inst_y, inst_w, inst_h)

        for k, (ir, (_, inst_row)) in enumerate(zip(inst_rects, region_insts.iterrows())):
            # Two shades per region: academia (base) vs industry (darker)
            inst_name = inst_row["institution"]
            if is_industry(inst_name):
                color_inst = _shift_lightness(base_color, -0.20)
            else:
                color_inst = base_color
            ax.add_patch(patches.Rectangle(
                (ir["x"], ir["y"]), ir["dx"], ir["dy"],
                facecolor=color_inst,
                edgecolor="#0e1117", linewidth=1.4,
            ))
            display_count = int(round(inst_row["count"])) if isinstance(inst_row["count"], float) else inst_row["count"]
            placed_all.extend(_draw_label(
                ax, ir["x"], ir["y"], ir["dx"], ir["dy"],
                inst_name, display_count, inst_row["percentage"],
            ))

        region_borders.append((rx, ry, rw, rh))

    # Draw thick region boundaries on top so they stay visible above institution borders
    for (rx, ry, rw, rh) in region_borders:
        ax.add_patch(patches.Rectangle(
            (rx, ry), rw, rh,
            facecolor="none", edgecolor="#0e1117", linewidth=4.5,
        ))

    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.invert_yaxis()
    ax.set_axis_off()

    fig.text(
        0.022, 0.965,
        "Who is shaping the AI frontier at ICLR 2026?",
        color="white", fontsize=34, fontweight="bold", ha="left", va="top",
    )
    src_label = "affiliations from paper PDFs" if SOURCE_LABEL == "pdf" else "affiliations from OpenReview profiles"
    subtitle = (
        f"Top 50 institutions grouped by region   ·   "
        f"region size = cumulative paper count   ·   "
        f"{METHOD_DESCRIPTIONS[method_key]}   ·   {src_label}"
    )
    fig.text(
        0.022, 0.910, subtitle,
        color="#c8c8c8", fontsize=15.5, ha="left", va="top",
    )

    fig.text(
        0.025, 0.025,
        f"Source: ICLR 2026 accepted papers (OpenReview).  Institution names normalized from per-author affiliations.  "
        f"Method: {METHOD_TITLES[method_key]}.  "
        f"Within each region, lighter shade = academia / research institute, darker shade = industry.",
        color="#9a9a9a", fontsize=9, ha="left", va="bottom",
    )
    fig.text(
        0.978, 0.040, "Dmytro Lopushanskyy",
        color="#e6e6e6", fontsize=13, ha="right", va="bottom", fontweight="bold",
    )
    fig.text(
        0.978, 0.022, "linkedin.com/in/dmytrolopushanskyy",
        color="#b8b8b8", fontsize=10.5, ha="right", va="bottom",
    )

    _shrink_overflowing_text(fig, ax, placed_all)

    fig.savefig(out_png, facecolor=fig.get_facecolor(), bbox_inches="tight", dpi=200)
    fig.savefig(out_svg, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _build_ranked_df(counter, name_to_country, total_papers, denom_for_pct):
    """Convert a counter into a ranked DataFrame.

    `denom_for_pct` is the number used as the percentage denominator: for
    `unique`, it's `total_papers` (so percentages can sum to >100%); for
    `first_author` and `fractional`, percentages are share-of-total and sum
    to ~100%.
    """
    rows = []
    for rank, (name, count) in enumerate(counter.most_common(), start=1):
        country = name_to_country.get(name, "Other")
        region = COUNTRY_REGION.get(country, "Other / Unknown")
        rows.append({
            "rank": rank,
            "institution": name,
            "count": round(count, 3) if isinstance(count, float) else count,
            "percentage": 100.0 * count / denom_for_pct,
            "country": country,
            "region": region,
        })
    return pd.DataFrame(rows)


def _print_top20(method_key, full_df):
    print()
    print(f"=== {METHOD_TITLES[method_key]} ===")
    print(f"{'Rank':<5} {'Count':>7} {'%':>6}  Institution  ·  Country")
    print("-" * 78)
    for _, r in full_df.head(20).iterrows():
        cnt = f"{r['count']:.1f}" if isinstance(r['count'], float) else f"{r['count']}"
        print(f"{r['rank']:<5} {cnt:>7} {r['percentage']:>5.2f}%  {r['institution']}  ·  {r['country']}")


def main():
    df = pd.read_csv(SOURCE) if SOURCE.suffix == ".csv" else pd.read_excel(SOURCE)
    print(f"Loaded {len(df)} papers from {SOURCE.name} (using {INST_COLUMN})")
    total_papers = len(df)

    # 1. Compute the three counters
    counters = {
        "unique":       count_unique_per_paper(df),
        "first_author": count_first_author(df),
        "fractional":   count_fractional(df),
    }

    # 2. Build ranked tables, write CSVs, render treemaps
    ranked = {}
    for method_key, (counter, name_to_country) in counters.items():
        if method_key == "unique":
            denom = total_papers  # share-of-papers (can exceed 100% summed)
        else:
            denom = sum(counter.values())  # share-of-credit (sums to 100%)
        full_df = _build_ranked_df(counter, name_to_country, total_papers, denom)
        ranked[method_key] = full_df

        csv_path, png_path, svg_path = OUT_FILES[method_key]
        full_df.to_csv(csv_path, index=False, quoting=csv.QUOTE_MINIMAL)
        print(f"Wrote {csv_path} ({len(full_df)} rows)")

        _print_top20(method_key, full_df)

        top50 = full_df.head(50).copy()
        render_treemap(top50, total_papers, png_path, svg_path, method_key)
        print(f"Wrote {png_path}")
        print(f"Wrote {svg_path}")

        gpng, gsvg = OUT_FILES_GROUPED[method_key]
        render_treemap_grouped(top50, total_papers, gpng, gsvg, method_key)
        print(f"Wrote {gpng}")
        print(f"Wrote {gsvg}")

    # 3. Sensitivity table — top-30 of `unique`, with rank/share under each method
    primary = ranked["unique"].head(30).copy()
    rank_lookup = {
        m: dict(zip(ranked[m]["institution"], ranked[m]["rank"]))
        for m in ranked
    }
    pct_lookup = {
        m: dict(zip(ranked[m]["institution"], ranked[m]["percentage"]))
        for m in ranked
    }
    rows = []
    for _, r in primary.iterrows():
        inst = r["institution"]
        rows.append({
            "institution": inst,
            "rank_unique":          rank_lookup["unique"].get(inst, ""),
            "pct_unique":           round(pct_lookup["unique"].get(inst, 0.0), 2),
            "rank_first_author":    rank_lookup["first_author"].get(inst, ""),
            "pct_first_author":     round(pct_lookup["first_author"].get(inst, 0.0), 2),
            "rank_fractional":      rank_lookup["fractional"].get(inst, ""),
            "pct_fractional":       round(pct_lookup["fractional"].get(inst, 0.0), 2),
        })
    sens = pd.DataFrame(rows)
    sens.to_csv(OUT_SENSITIVITY, index=False, quoting=csv.QUOTE_MINIMAL)
    print()
    print(f"Wrote {OUT_SENSITIVITY}")

    # 4. Print rank-stability table
    print()
    print("=== Rank stability across methods (top-30 by unique method) ===")
    print(f"{'Inst':<35} {'Uniq':>7} {'1st':>7} {'Frac':>7}")
    print("-" * 60)
    for _, r in sens.iterrows():
        print(f"{r['institution'][:34]:<35} "
              f"{int(r['rank_unique']):>4}  "
              f"{int(r['rank_first_author']) if r['rank_first_author'] != '' else '–':>5}  "
              f"{int(r['rank_fractional']) if r['rank_fractional'] != '' else '–':>5}  ")

    # 5. Quick "is the top-20 stable?" summary
    print()
    top20_unique = set(ranked["unique"].head(20)["institution"])
    top20_first = set(ranked["first_author"].head(20)["institution"])
    top20_frac = set(ranked["fractional"].head(20)["institution"])
    print(f"Top-20 overlap (unique ∩ first_author): {len(top20_unique & top20_first)}/20")
    print(f"Top-20 overlap (unique ∩ fractional):   {len(top20_unique & top20_frac)}/20")
    print(f"Top-20 overlap (first_author ∩ fractional): {len(top20_first & top20_frac)}/20")
    print(f"Top-20 in ALL three methods: {len(top20_unique & top20_first & top20_frac)}/20")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--source",
        choices=["pdf", "openreview", "both"],
        default="pdf",
        help="Which affiliation source to use. 'both' generates both versions.",
    )
    ap.add_argument(
        "--shape",
        choices=["wide", "square"],
        default="wide",
        help="Aspect ratio of the figure: 16:9 (default) or 1:1 (square).",
    )
    args = ap.parse_args()
    modes = ["pdf", "openreview"] if args.source == "both" else [args.source]
    for mode in modes:
        print(f"\n========== Generating charts for source={mode} shape={args.shape} ==========")
        configure_source(mode, shape=args.shape)
        main()
