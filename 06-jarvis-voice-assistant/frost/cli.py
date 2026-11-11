"""Text-mode FROST. Start here — no microphone required.

Run:  python -m frost.cli
      python -m frost.cli --speak     (also read replies aloud)
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .brain import FROST
from .config import EXIT_PHRASES, MODEL

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


async def console_permission(tool_name: str, description: str) -> bool:
    print(f"\n{BOLD}[permission]{RESET} FROST wants to {description}")
    try:
        answer = await asyncio.to_thread(input, "  allow? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        # Non-interactive run (piped stdin, one-shot mode). Fail closed: no
        # human means no approval, never an implicit yes.
        print("  no console available -> denied")
        return False
    return answer.strip().lower() in {"y", "yes"}


async def main() -> int:
    parser = argparse.ArgumentParser(description="FROST (text mode)")
    parser.add_argument("--speak", action="store_true", help="read replies aloud")
    parser.add_argument("prompt", nargs="*", help="one-shot prompt, then exit")
    args = parser.parse_args()

    speaker = None
    if args.speak:
        from .speech import Speaker

        speaker = Speaker()

    def status(msg: str) -> None:
        print(f"{DIM}  · {msg}{RESET}")

    async with FROST(ask_permission=console_permission, on_status=status) as frost:

        async def emit(text: str) -> None:
            print(text)
            if speaker is not None:
                await speaker.say(text)

        if args.prompt:
            await frost.ask(" ".join(args.prompt), on_text=emit)
            if speaker is not None:
                # say() only queues; without this the process exits mid-sentence.
                await speaker.wait_idle()
            return 0

        print(f"{BOLD}FROST online.{RESET} {DIM}({MODEL}){RESET}  Ctrl-C or 'exit' to quit.\n")
        while True:
            try:
                user = await asyncio.to_thread(input, f"{BOLD}you ›{RESET} ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            user = user.strip()
            if not user:
                continue
            if user.lower() in EXIT_PHRASES:
                break
            print(f"{BOLD}FROST ›{RESET} ", end="", flush=True)
            try:
                await frost.ask(user, on_text=emit)
            except KeyboardInterrupt:
                await frost.interrupt()
                if speaker is not None:
                    speaker.stop()
                print("\n[interrupted]")
            if speaker is not None:
                await speaker.wait_idle()
            print()

    if speaker is not None:
        speaker.close()
    print("Shutting down. Goodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
