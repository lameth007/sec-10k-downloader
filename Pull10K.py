import json
import os
import re
import subprocess
from pathlib import Path

import requests
from sec_edgar_downloader import Downloader

CHROME_BIN = "google-chrome"
PERIOD_RE = re.compile(r"CONFORMED PERIOD OF REPORT:\s*(\d{4})")
CIK_RE = re.compile(r"CENTRAL INDEX KEY:\s*(\d+)")
IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

# Resolve relative to this script's own folder, so it works no matter
# what directory you launch it from.
script_dir = os.path.dirname(os.path.abspath(__file__))

# SEC EDGAR requires every request to identify a name and email address
# (see https://www.sec.gov/os/webmaster-faq#developers). Ask for it once
# and reuse the saved values on later runs.
identity_path = Path(script_dir) / ".edgar_identity.json"
if identity_path.exists():
    identity = json.loads(identity_path.read_text())
else:
    print("SEC EDGAR requires a name and email address to identify requests.")
    identity = {
        "name": input("Your name or organization: ").strip(),
        "email": input("Your email address: ").strip(),
    }
    identity_path.write_text(json.dumps(identity))

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

dl = Downloader(identity["name"], identity["email"], str(cache_dir))

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
    primary_doc = primary_docs[0]

    # The primary document references images (charts, logos) that SEC
    # EDGAR serves as separate files. Fetch any not already cached so
    # Chrome can find them locally when rendering the page.
    cik_match = CIK_RE.search(full_submission.read_text(errors="ignore"))
    if cik_match:
        cik = cik_match.group(1).lstrip("0")
        accession_no_dashes = accession_dir.name.replace("-", "")
        html = primary_doc.read_text(errors="ignore")
        for image_name in IMG_SRC_RE.findall(html):
            if image_name.startswith(("http://", "https://", "data:")):
                continue
            image_path = accession_dir / image_name
            if image_path.exists():
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{image_name}"
            try:
                resp = requests.get(url, headers={"User-Agent": dl.user_agent}, timeout=30)
                if resp.ok:
                    image_path.write_bytes(resp.content)
                else:
                    print(f"Could not fetch image {image_name} for {accession_dir.name}: HTTP {resp.status_code}")
            except requests.RequestException as e:
                print(f"Could not fetch image {image_name} for {accession_dir.name}: {e}")

    pdf_path = annual_reports_dir / f"{symbol}-10K-{year}.pdf"
    try:
        subprocess.run(
            [
                CHROME_BIN,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                f"--print-to-pdf={pdf_path}",
                "--no-pdf-header-footer",
                primary_doc.resolve().as_uri(),
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
