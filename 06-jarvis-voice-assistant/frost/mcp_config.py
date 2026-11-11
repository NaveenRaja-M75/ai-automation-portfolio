"""Load third-party MCP servers from `mcp.json`.

Declarative on purpose: adding a capability should be a config entry someone
can read and audit, not a code change. `mcp.json` is gitignored because server
definitions carry credential paths and tokens.

Every server here is unreviewed third-party code that will hold real
credentials, so `enabled: false` is the default state for anything you have not
deliberately switched on, and the permission gate classifies each tool call by
verb regardless of what the server claims about itself.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .config import ROOT

CONFIG_PATH = Path(os.getenv("FROST_MCP_CONFIG", ROOT / "mcp.json"))
ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand(value):
    """Replace ${VAR} with the environment value, so tokens live in .env."""
    if isinstance(value, str):
        return ENV_REF.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_servers() -> dict[str, dict]:
    """Return {name: server_config} for every enabled server."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[FROST] mcp.json is unreadable, skipping MCP servers: {exc}")
        return {}

    servers: dict[str, dict] = {}
    for name, cfg in (raw.get("mcpServers") or {}).items():
        if not cfg.get("enabled", False):
            continue
        cfg = {k: v for k, v in cfg.items() if k != "enabled"}
        # A server whose command or token didn't resolve would fail obscurely
        # at first use; refuse it now, loudly.
        expanded = _expand(cfg)
        missing = [
            m.group(1) for m in ENV_REF.finditer(json.dumps(cfg))
            if not os.getenv(m.group(1))
        ]
        if missing:
            print(f"[FROST] MCP server '{name}' disabled: missing {', '.join(sorted(set(missing)))}")
            continue
        servers[name] = expanded
    return servers


def describe(extra: list[str] | None = None) -> str:
    """Name the connected servers in the system prompt.

    Not cosmetic: a tool FROST hasn't been told about is one it answers around.
    Attaching a calendar server without naming it here produced "Calendar needs
    to be authorized first" — a confident guess, with the tool sitting unused.
    """
    names_set = set(load_servers()) | set(extra or [])
    if not names_set:
        return ""
    names = ", ".join(sorted(names_set))
    return (
        f"\n\n## Connected tools\n\nYou have MCP access to: {names}. "
        "Read operations run without asking. Anything that creates or changes "
        "data will stop and ask Naveen first, and deletes and sends are refused "
        "outright — draft instead and let him send it. Don't try to route "
        "around that; say what you need and why.\n\n"
        "These tools are live and connected. If a question is about your "
        "calendar, mail or anything else listed above, **call the tool** — do "
        "not answer that it needs authorising or that you lack access without "
        "having tried."
    )
