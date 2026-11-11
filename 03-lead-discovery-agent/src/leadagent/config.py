"""Loads settings.yaml and the niche playbooks."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
NICHE_DIR = CONFIG_DIR / "niches"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
DB_PATH = ROOT / "leads.db"


def _load_dotenv() -> None:
    """Minimal .env reader - avoids a dependency for four variables."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    _load_dotenv()
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def niches() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(NICHE_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            spec = yaml.safe_load(fh)
        out[spec["key"]] = spec
    return out


def niche(key: str) -> dict[str, Any]:
    available = niches()
    if key not in available:
        raise KeyError(
            f"Unknown niche {key!r}. Available: {', '.join(sorted(available))}. "
            f"Add one by dropping a YAML file in {NICHE_DIR}."
        )
    return available[key]


def env(name: str, required: bool = False) -> str | None:
    _load_dotenv()
    value = os.environ.get(name)
    if required and not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value
