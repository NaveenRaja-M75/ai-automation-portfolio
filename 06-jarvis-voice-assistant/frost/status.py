"""`python -m frost.status` — what is actually protecting you right now.

Security posture you can't inspect is security posture you can't trust. This
prints the live configuration rather than what the README claims.
"""

from __future__ import annotations

from .config import (
    AUDIT_LOG,
    EFFORT,
    MEMORY_DIR,
    MODEL,
    ROOT,
    SANDBOX_IMAGE,
    SANDBOX_NETWORK,
    SILENCE_DURATION,
    STT_MODEL,
    STT_PROVIDER,
    TTS_BACKEND,
    WORKSPACE,
)
from .brain import MAX_IDENTICAL_CALLS, MAX_TOOL_CALLS_PER_TURN
from .sandbox import detect_backend, scrubbed_env
from .vision import SENSITIVE_WINDOWS, VISION_ENABLED

GREEN, YELLOW, RESET, BOLD = "\033[32m", "\033[33m", "\033[0m", "\033[1m"


def main() -> int:
    backend = detect_backend()
    isolated = backend == "docker"

    print(f"\n{BOLD}FROST status{RESET}")
    print(f"  model            {MODEL}  (effort: {EFFORT})")
    print(f"  root             {ROOT}")
    print(f"  workspace        {WORKSPACE}")
    print(f"  memory           {MEMORY_DIR}  ({len(list(MEMORY_DIR.glob('*.md'))) - 1} facts)")

    print(f"\n{BOLD}Execution{RESET}")
    if isolated:
        print(f"  sandbox          {GREEN}docker{RESET} — real isolation")
        print(f"  image            {SANDBOX_IMAGE}")
        print(f"  network          {'bridge' if SANDBOX_NETWORK else 'none'}")
    else:
        print(f"  sandbox          {YELLOW}subprocess{RESET} — hardened, NOT isolated")
        print("  note             install Docker Desktop for real isolation;")
        print("                   until then the permission gate is the boundary")

    leaked = [k for k in scrubbed_env() if any(
        m in k.upper() for m in ("KEY", "TOKEN", "SECRET", "PASSWORD"))]
    mark = f"{GREEN}clean{RESET}" if not leaked else f"{YELLOW}{leaked}{RESET}"
    print(f"  child env        {mark} ({len(scrubbed_env())} vars passed through)")

    print(f"\n{BOLD}Eyes{RESET}")
    if VISION_ENABLED:
        print(f"  screen capture   {GREEN}on{RESET} — announced, audited, "
              "refused unattended")
        print(f"  refuses windows  {len(SENSITIVE_WINDOWS)} patterns "
              "(password managers, banking, sign-in, OTP)")
    else:
        print(f"  screen capture   {YELLOW}off{RESET} (FROST_VISION=0)")
    print(f"  loop breaker     {MAX_IDENTICAL_CALLS} identical calls or "
          f"{MAX_TOOL_CALLS_PER_TURN} total ends the turn")

    print(f"\n{BOLD}Audit{RESET}")
    print(f"  log              {AUDIT_LOG}")
    if AUDIT_LOG.exists():
        lines = AUDIT_LOG.read_text(encoding="utf-8").strip().splitlines()
        denied = sum(1 for line in lines if '"DENY"' in line)
        print(f"  entries          {len(lines)}  ({denied} denied)")
    else:
        print("  entries          none yet")
    writable = "outside write scope (good)" if not _within_writable() else "WRITABLE (bad)"
    print(f"  tamper-resistance {writable}")

    print(f"\n{BOLD}Voice{RESET}")
    print(f"  tts              {TTS_BACKEND}")
    # Showing only the local model name implied local transcription even when a
    # cloud provider was doing the work — say which is actually in use.
    if STT_PROVIDER == "local":
        print(f"  stt              {STT_MODEL}  (on this machine)")
    else:
        print(f"  stt              {GREEN}{STT_PROVIDER}{RESET} cloud  "
              f"(audio leaves this machine; local fallback: {STT_MODEL})")
    print(f"  silence cutoff   {SILENCE_DURATION}s")
    for label, ok, detail in _voice_preflight():
        mark = f"{GREEN}ok{RESET}" if ok else f"{YELLOW}missing{RESET}"
        print(f"  {label:16} {mark}  {detail}")
    print()
    return 0


def _voice_preflight() -> list[tuple[str, bool, str]]:
    """Check every voice dependency separately.

    Worth the lines: installing RealtimeSTT alone leaves out the transcription
    engine and the VAD models, and the resulting failure surfaces at first
    microphone use as `[WinError 127]`, which points nowhere near the cause.
    """
    import importlib.util
    from pathlib import Path as _Path

    def present(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            return False

    rows = [
        ("edge-tts", present("edge_tts"), "neural voices"),
        ("pygame", present("pygame"), "playback + barge-in"),
        ("RealtimeSTT", present("realtimestt") or present("RealtimeSTT"), "mic loop"),
        ("faster-whisper", present("faster_whisper"), "transcription engine"),
        ("onnxruntime", present("onnxruntime"), "runs the VAD model"),
    ]

    onnx_found = False
    if present("silero_vad"):
        try:
            spec = importlib.util.find_spec("silero_vad")
            data = _Path(spec.origin).parent / "data"
            onnx_found = any(data.glob("*.onnx"))
        except Exception:
            onnx_found = False
    rows.append(
        ("silero-vad", onnx_found, "ONNX VAD models" if onnx_found else "run: pip install silero-vad")
    )

    # A torch/torchaudio mismatch is the usual cause of WinError 127. Only worth
    # reporting when both are installed, since the ONNX path doesn't need them.
    if present("torch") and present("torchaudio"):
        try:
            import torch
            import torchaudio

            tv, av = torch.__version__.split("+")[0], torchaudio.__version__.split("+")[0]
            aligned = tv.rsplit(".", 1)[0] == av.rsplit(".", 1)[0]
            rows.append((
                "torch/audio",
                aligned,
                f"torch {tv} / torchaudio {av}"
                + ("" if aligned else "  <- mismatched; ONNX VAD avoids it"),
            ))
        except Exception as exc:
            # torchaudio raising on import is the WinError 127 case. Harmless
            # while VAD runs on ONNX, but say so rather than hiding it: a
            # diagnostic that swallows a broken dependency is worse than none.
            rows.append((
                "torch/audio",
                onnx_found,
                f"torchaudio broken ({type(exc).__name__}); unused while ONNX VAD is active"
                if onnx_found
                else f"torchaudio broken ({type(exc).__name__}) and no ONNX fallback",
            ))
    return rows


def _within_writable() -> bool:
    for root in (WORKSPACE, ROOT):
        try:
            AUDIT_LOG.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


if __name__ == "__main__":
    raise SystemExit(main())
