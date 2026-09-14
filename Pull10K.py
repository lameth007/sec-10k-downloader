import os
import re
import subprocess
from pathlib import Path

from sec_edgar_downloader import Downloader

CHROME_BIN = "google-chrome"
PERIOD_RE = re.compile(r"CONFORMED PERIOD OF REPORT:\s*(\d{4})")

# Resolve relative to this script's own folder, so it works no matter
# what directory you launch it from.
script_dir = os.path.dirname(os.path.abspath(__file__))
symbol = input("Company symbol: ").strip().upper()

symbol_dir = Path(script_dir) / symbol
annual_reports_dir = symbol_dir / "Annual Reports"
# Hidden cache of raw SEC downloads, kept between runs so a re-run only
# fetches filings it doesn't already have (not visible next to the PDFs).
cache_dir = symbol_dir / ".raw-filings-cache"
annual_reports_dir.mkdir(parents=True, exist_ok=True)
cache_dir.mkdir(parents=True, exist_ok=True)

existing_years = {
    match.group(1)
    for path in annual_reports_dir.glob(f"{symbol}-10K-*.pdf")
    if (match := re.search(r"(\d{4})\.pdf$", path.name))
}

dl = Downloader("Null", "sec.gov.proximity851@simplelogin.com", str(cache_dir))

# download_details=True also fetches the clean primary filing document
# (not just the raw full-submission text dump), which is what we convert.
# Files already present in the cache from a prior run are not re-downloaded.
dl.get("10-K", symbol, download_details=True)

filings_root = cache_dir / "sec-edgar-filings" / symbol / "10-K"

converted = 0
for accession_dir in sorted(p for p in filings_root.iterdir() if p.is_dir()):
    full_submission = accession_dir / "full-submission.txt"
    year_match = PERIOD_RE.search(full_submission.read_text(errors="ignore"))
    if not year_match:
        print(f"Skipping {accession_dir.name}: could not determine filing year")
        continue
    year = year_match.group(1)

    if year in existing_years:
        continue

    primary_docs = [p for p in accession_dir.iterdir() if p.stem == "primary-document"]
    if not primary_docs:
        print(f"Skipping {accession_dir.name}: no primary document found")
        continue

    pdf_path = annual_reports_dir / f"{symbol}-10K-{year}.pdf"
    try:
        subprocess.run(
            [
                CHROME_BIN,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                f"--print-to-pdf={pdf_path}",
                "--print-to-pdf-no-header",
                primary_docs[0].resolve().as_uri(),
            ],
            check=True,
            capture_output=True,
        )
        print(f"Saved {pdf_path.name}")
        converted += 1
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {accession_dir.name}: {e.stderr.decode(errors='ignore')}")

if converted == 0:
    print("Already up to date - no new 10-Ks to convert.")
