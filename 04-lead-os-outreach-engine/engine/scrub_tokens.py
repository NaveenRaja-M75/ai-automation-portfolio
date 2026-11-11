#!/usr/bin/env python3
"""
Remove the Meta access token from every hyperlink in the old lead sheets.

The early ad-gap scripts wrote Meta's ad_snapshot_url into a "See their ad"
column. Meta returns that URL with the access token in the query string, so
over 1,000 links across 20 sheets carry a live credential. Anyone the file is
sent to, or any tool it is uploaded to, can read the token and use it.

Each such link is replaced with the public Ad Library page for the same ad
(the ad id is kept, the token is dropped). Every file is copied to
_token_backup/ first, so the change can be undone.

    python engine/scrub_tokens.py            preview, changes nothing
    python engine/scrub_tokens.py --apply    make the change
"""
import re
import sys
import shutil
import argparse
from pathlib import Path

import openpyxl

FOLDERS = [Path(r"C:\Users\naveen\Desktop\Lead_Generation7"),
           Path(r"C:\Users\naveen\Desktop\Pricing")]
BACKUP = Path(r"C:\Users\naveen\Desktop\Lead_Generation7\_token_backup")


def safe(url):
    m = re.search(r"[?&]id=(\d+)", url)
    return f"https://www.facebook.com/ads/library/?id={m.group(1)}" if m else \
        "https://www.facebook.com/ads/library/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    total = 0
    for folder in FOLDERS:
        for f in sorted(folder.glob("*.xlsx")):
            if f.name.startswith("~$"):
                continue
            wb = openpyxl.load_workbook(f)
            n = 0
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for c in row:
                        if c.hyperlink and "access_token" in (c.hyperlink.target or ""):
                            n += 1
                            if a.apply:
                                c.hyperlink = safe(c.hyperlink.target)
                        if isinstance(c.value, str) and "access_token" in c.value:
                            n += 1
                            if a.apply:
                                c.value = re.sub(r"access_token=[^&\s\"]+", "", c.value)
            if n:
                total += n
                print(f"  {n:>4} links  {f.name}")
                if a.apply:
                    BACKUP.mkdir(exist_ok=True)
                    shutil.copy2(f, BACKUP / f.name)
                    wb.save(f)
    print(f"\n{total} token-bearing links "
          f"{'removed (backups in ' + str(BACKUP) + ')' if a.apply else 'found. Nothing changed. Run with --apply to fix.'}")
    if a.apply and total:
        print("The backups still contain the token. Delete that folder once you have "
              "checked the files open fine.")


if __name__ == "__main__":
    main()
