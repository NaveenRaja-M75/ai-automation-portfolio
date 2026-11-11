"""`python -m frost.backup` — protect the one thing that cannot be regenerated.

The design notes have called the vault "the one thing you cannot regenerate"
since the beginning, and then left it on a single disk with no history. FROST is
carefully prevented from damaging it; nothing was protecting it from a dropped
laptop or from you deleting a note and noticing three weeks later. That is a
larger real-world risk to this data than every prompt-injection scenario in
`ROADMAP.md` combined, and it is the cheapest one to close.

Two layers, because they fail differently:

- **Git, inside the vault.** Recovers *content*: a note you deleted, a paragraph
  you overwrote, what a file said last Tuesday. Costs nothing at this size and
  Obsidian ignores the `.git` directory entirely.
- **A mirror on OneDrive.** Recovers from *the disk*. Git history lives on the
  same disk as the vault, so on its own it survives mistakes but not hardware.

The mirror copies `.git` too, so the off-device copy carries the full history
rather than a flat snapshot. It never deletes from the destination: a stale file
in a backup is noise, a deleted one is the failure this exists to prevent.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import VAULT

GREEN, YELLOW, DIM, RESET, BOLD = (
    "\033[32m", "\033[33m", "\033[2m", "\033[0m", "\033[1m",
)

# Obsidian rewrites these constantly and they carry no content — committing them
# turns a clean history into noise.
GITIGNORE = """\
# FROST backup — Obsidian working state, not content.
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.obsidian/cache
.trash/
.DS_Store
Thumbs.db
"""


def _default_mirror() -> Path | None:
    for candidate in (
        Path(os.getenv("OneDrive", "")) if os.getenv("OneDrive") else None,
        Path.home() / "OneDrive",
    ):
        if candidate and candidate.is_dir():
            return candidate / "FROST Backup"
    return None


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    # Identity is passed per-invocation so this works even when the machine has
    # no global git config, which is the common case on a fresh Windows box.
    return subprocess.run(
        ["git", "-c", "user.name=FROST", "-c", "user.email=frost@localhost",
         "-C", str(vault), *args],
        capture_output=True, text=True,
    )


def snapshot(vault: Path) -> str:
    """Commit the current state of the vault. Returns a one-line summary."""
    if not (vault / ".git").is_dir():
        result = _git(vault, "init", "-q")
        if result.returncode != 0:
            return f"git init failed: {result.stderr.strip()}"
        (vault / ".gitignore").write_text(GITIGNORE, encoding="utf-8")

    _git(vault, "add", "-A")
    staged = _git(vault, "diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        count = _git(vault, "rev-list", "--count", "HEAD").stdout.strip() or "0"
        return f"no changes since the last snapshot ({count} in history)"

    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    result = _git(vault, "commit", "-q", "-m", f"Vault snapshot {stamp}")
    if result.returncode != 0:
        return f"commit failed: {(result.stderr or result.stdout).strip()}"
    changed = len(staged.splitlines())
    total = _git(vault, "rev-list", "--count", "HEAD").stdout.strip()
    return f"committed {changed} changed file(s) — {total} snapshots in history"


def mirror(vault: Path, destination: Path) -> str:
    """Copy the vault, history included, to a second location."""
    target = destination / vault.name
    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(vault, target, dirs_exist_ok=True)
    except OSError as exc:
        return f"mirror failed: {exc}"
    files = sum(1 for p in target.rglob("*") if p.is_file())
    return f"mirrored to {target} ({files} files, history included)"


def main() -> int:
    print(f"\n{BOLD}FROST vault backup{RESET}")

    if not VAULT:
        print(f"  {YELLOW}No vault configured{RESET} — set FROST_VAULT in .env")
        return 1
    vault = Path(VAULT)
    if not vault.is_dir():
        print(f"  {YELLOW}Vault not found{RESET}: {vault}")
        return 1

    notes = sum(1 for _ in vault.rglob("*.md"))
    print(f"  vault            {vault}")
    print(f"  notes            {notes}")

    print(f"\n{BOLD}History{RESET}")
    print(f"  {GREEN}·{RESET} {snapshot(vault)}")
    print(f"  {DIM}recover a deleted note:  git -C \"{vault}\" log --diff-filter=D "
          f"--name-only{RESET}")

    print(f"\n{BOLD}Off-device copy{RESET}")
    configured = os.getenv("FROST_BACKUP_DIR")
    destination = Path(configured) if configured else _default_mirror()
    if destination is None:
        print(f"  {YELLOW}skipped{RESET} — no OneDrive folder found and "
              "FROST_BACKUP_DIR is not set")
        print(f"  {DIM}history still protects you from mistakes, but not from "
              f"this disk failing{RESET}")
    else:
        print(f"  {GREEN}·{RESET} {mirror(vault, destination)}")

    print(f"\n{DIM}  Run this whenever you have written something you would mind "
          f"losing.{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
