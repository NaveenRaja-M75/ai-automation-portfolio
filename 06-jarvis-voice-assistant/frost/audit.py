"""`python -m frost.audit` — read the log nobody was reading.

Every gate decision has been appended to `audit.jsonl` since the beginning, and
until now nothing ever looked at it. A log written but never read is not a
control, it is a comfort: it proves you *could* have noticed, not that you would
have.

What this looks for, in the order it matters:

- **Repeated identical denials.** Almost always a broken workflow rather than an
  attack — FROST trying the same blocked thing over and over because nothing
  told it a different route exists. Each one is a candidate for either a lesson
  or an allowlist entry, and they are invisible without counting.
- **Denials in the categories that would matter if they were real**: credential
  reads, destructive shell patterns, writes to FROST's own code. One of these is
  worth understanding. A run of them is worth stopping for.
- **What FROST actually does all day.** The tool mix is the honest answer to
  "what is this for", and it is usually not what you assumed.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from .config import AUDIT_LOG

GREEN, YELLOW, RED, DIM, RESET, BOLD = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m", "\033[1m",
)

# Denials worth a human's attention, most serious first. Matched against the
# reason text the gate recorded, so these track the gate's own wording.
NOTABLE = [
    ("credential access", RED, ("credential", "private-data", ".ssh", ".aws")),
    ("FROST's own code", RED, ("own code", "own configuration", "own frost/",
                               "own tests/", "own .git/", "own memory/")),
    ("destructive command", RED, ("destructive pattern",)),
    ("third-party send/delete", YELLOW, ("does not delete or send",)),
    ("host shell", YELLOW, ("on the host", "outside the sandbox")),
    ("unattended screen capture", YELLOW, ("screen capture requires",)),
]


def _load(limit: int) -> list[dict]:
    if not AUDIT_LOG.exists():
        return []
    rows: list[dict] = []
    with AUDIT_LOG.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn last line after a crash; not worth failing over
    return rows[-limit:]


def _describe(row: dict) -> str:
    """The most identifying field of a call, for grouping."""
    payload = row.get("input") or {}
    for key in ("command", "file_path", "notebook_path", "path", "pattern", "slug"):
        value = payload.get(key)
        if value:
            text = str(value)
            return text if len(text) <= 70 else text[:67] + "..."
    return "-"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    limit = 5000
    for arg in argv:
        if arg.startswith("--last="):
            limit = int(arg.split("=", 1)[1])

    rows = _load(limit)
    print(f"\n{BOLD}FROST audit{RESET}")
    print(f"  log              {AUDIT_LOG}")
    if not rows:
        print(f"  {DIM}nothing recorded yet{RESET}\n")
        return 0

    denied = [r for r in rows if r.get("verdict") == "DENY"]
    span = ""
    if rows[0].get("ts") and rows[-1].get("ts"):
        span = f"  {DIM}{rows[0]['ts'][:16]} -> {rows[-1]['ts'][:16]}{RESET}"
    print(f"  decisions        {len(rows)}  ({len(denied)} denied){span}")

    print(f"\n{BOLD}Worth a look{RESET}")
    flagged = False
    for label, colour, markers in NOTABLE:
        hits = [r for r in denied
                if any(m in (r.get("reason") or "").lower() for m in markers)]
        if hits:
            flagged = True
            print(f"  {colour}{len(hits):>4}{RESET}  {label}")
            for row in hits[-2:]:
                print(f"        {DIM}{row.get('ts', '')[:16]}  "
                      f"{row.get('tool', '?')}  {_describe(row)}{RESET}")
    if not flagged:
        print(f"  {GREEN}nothing{RESET} - no credential reads, destructive "
              "commands or self-modification attempts")

    repeats = Counter(
        (r.get("tool", "?"), _describe(r)) for r in denied
    )
    recurring = [(count, tool, what) for (tool, what), count in repeats.items()
                 if count >= 3]
    print(f"\n{BOLD}Blocked repeatedly{RESET}  "
          f"{DIM}usually a workflow that needs a route, not an attack{RESET}")
    if recurring:
        for count, tool, what in sorted(recurring, reverse=True)[:8]:
            print(f"  {YELLOW}{count:>4}x{RESET}  {tool:<28} {what}")
        print(f"  {DIM}each of these is a candidate for a lesson, an allowlist "
              f"entry, or a rethink{RESET}")
    else:
        print(f"  {GREEN}none{RESET} - nothing was denied the same way three times")

    print(f"\n{BOLD}What FROST actually does{RESET}")
    for tool, count in Counter(r.get("tool", "?") for r in rows).most_common(8):
        refused = sum(1 for r in denied if r.get("tool") == tool)
        share = f"  {DIM}({refused} denied){RESET}" if refused else ""
        print(f"  {count:>5}  {tool}{share}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
