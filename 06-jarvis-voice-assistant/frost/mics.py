"""`python -m frost.mics` — list input devices so you can pick a better one.

Microphone choice beats model choice. A laptop's far-field array is built to
capture a room, which means it also captures your speakers, your fan, and
whoever else is in it — and then Whisper has to guess. A headset mic sitting
5cm from your mouth removes most of that problem before any model runs.
"""

from __future__ import annotations

from .config import INPUT_DEVICE_INDEX

BOLD, DIM, GREEN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[0m"

# Names that usually mean "close to your mouth" vs "somewhere in the room".
_CLOSE = ("headset", "headphone", "airdopes", "buds", "boom", "usb mic")
_FAR = ("array", "internal", "built-in", "stereo mix", "sound mapper", "speaker")


def _hint(name: str) -> str:
    low = name.lower()
    if any(k in low for k in _CLOSE):
        return f"{GREEN}recommended{RESET} — close-talking mic"
    if any(k in low for k in _FAR):
        return f"{DIM}far-field; picks up the room and your speakers{RESET}"
    return ""


def main() -> int:
    try:
        import pyaudio
    except ImportError:
        print("pyaudio not installed. Run: pip install -r requirements-voice.txt")
        return 1

    pa = pyaudio.PyAudio()
    try:
        default_index = pa.get_default_input_device_info().get("index")
    except Exception:
        default_index = None

    print(f"\n{BOLD}Input devices{RESET}")
    seen: set[str] = set()
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) <= 0:
            continue
        name = str(info.get("name", "")).strip()
        # Windows lists the same device under several host APIs.
        key = name.lower()[:28]
        if key in seen:
            continue
        seen.add(key)

        marks = []
        if info["index"] == default_index:
            marks.append("system default")
        if info["index"] == INPUT_DEVICE_INDEX:
            marks.append(f"{GREEN}FROST is using this{RESET}")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        print(f"  {info['index']:>3}  {name[:44]:46}{_hint(name)}{suffix}")
    pa.terminate()

    print(f"\n{BOLD}To use one:{RESET} put part of its NAME in .env\n")
    print("  FROST_INPUT_DEVICE=Airdopes\n")
    print(f"{DIM}  Use a name, not a number. Indices are reassigned when a")
    print(f"  Bluetooth device connects, so a pinned number can silently")
    print(f"  become a different device -- or an output, which fails with")
    print(f"  '[Errno -9998] Invalid number of channels'.{RESET}\n")
    if INPUT_DEVICE_INDEX is None:
        print(f"{DIM}Currently unset, so the system default is used.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
