"""python -m jevbot   (run from the tradebot folder)"""

from __future__ import annotations

import asyncio
import logging
import sys

from .config import from_env


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = from_env()
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from .engine import Engine
    logging.getLogger("jevbot").info("mode=%s symbols=%s jev=%s primary=%s", cfg.mode, ",".join(cfg.symbols),
                                     cfg.jev_provider if cfg.jev_enabled else "off", cfg.primary_signal)
    try:
        asyncio.run(Engine(cfg).run())
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
