#!/usr/bin/env python3
"""
Health check. The things that break quietly, surfaced before they bite.

Every failure this catches is one that produces NO error at the moment it
happens. A token that expired last week does not announce itself; the next
sourcing run simply returns nothing and looks like a market with no leads.
A ledger that got deleted does not warn you; it just starts re-pitching
people you already contacted. Silent failures are the expensive ones.
"""
import os
import sys
import json
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V


def token_status():
    from dotenv import load_dotenv
    import requests
    load_dotenv(V.ROOT / ".env", override=True)
    t = (os.getenv("META_ACCESS_TOKEN") or "").strip()
    if not t:
        return "MISSING", "no META_ACCESS_TOKEN in .env", 0
    try:
        d = requests.get("https://graph.facebook.com/debug_token",
                         params={"input_token": t, "access_token": t},
                         timeout=15).json().get("data", {})
    except Exception:
        return "UNKNOWN", "could not reach Meta to check", 0
    if not d.get("is_valid"):
        return "DEAD", "token rejected by Meta, regenerate it", 0
    ea = d.get("expires_at")
    if not ea:
        return "OK", "valid, no expiry reported", 999
    if ea == 0:
        return "OK", "never expires", 999
    left = (datetime.datetime.fromtimestamp(ea) - datetime.datetime.now()).days
    if left <= 0:
        return "DEAD", "expired", 0
    if left <= 7:
        return "EXPIRING", f"only {left} days left, extend it now", left
    until = datetime.datetime.fromtimestamp(ea).date()
    return "OK", f"{left} days left (until {until})", left


def check():
    rows = []

    state, msg, days = token_status()
    rows.append(("Meta token", state, msg))

    seen = V.load_seen()
    rows.append(("Ledger", "OK" if len(seen) > 100 else "THIN",
                 f"{len(seen)} names"))

    backup = any((V.ROOT / p).exists() for p in
                 ("_state/seen.backup.json", "_state/seen.json.bak"))
    rows.append(("Ledger backup", "OK" if backup else "MISSING",
                 "local copy present" if backup else
                 "no backup. Copy _state/seen.json to Drive"))

    stale = []
    for stage in V.STAGES:
        for p in V.notes_in(stage):
            fm, _ = V.read_note(p)
            if fm.get("status") and fm["status"] != stage:
                stale.append(p.name)
    rows.append(("Note sync", "OK" if not stale else "DESYNC",
                 "all statuses match folders" if not stale
                 else f"{len(stale)} stale, run: python run.py repair"))

    leaked = [p.name for p in Path(V.VAULT).rglob("*.md")
              if "access_token=EAA" in p.read_text(encoding="utf-8", errors="ignore")]
    rows.append(("Credentials", "OK" if not leaked else "LEAK",
                 "no token in any note" if not leaked else f"{len(leaked)} notes leak a token"))

    waiting = len(V.notes_in("analyzed"))
    approved = len(V.approved_notes())
    rows.append(("Pipeline", "OK",
                 f"{waiting} awaiting review, {approved} approved and ready"))

    print("=" * 62)
    print("  HEALTH CHECK")
    print("=" * 62)
    bad = 0
    for name, state, msg in rows:
        mark = {"OK": "  ok  ", "MISSING": " WARN ", "THIN": " WARN ",
                "EXPIRING": " WARN ", "DESYNC": " WARN ",
                "DEAD": " FAIL ", "LEAK": " FAIL ", "UNKNOWN": " ???  "}.get(state, "      ")
        if state in ("DEAD", "LEAK"):
            bad += 1
        print(f" [{mark}] {name:<16} {msg}")
    print("=" * 62)
    if bad:
        print("  Fix the FAIL lines before running anything else.")
    return bad


if __name__ == "__main__":
    sys.exit(1 if check() else 0)
