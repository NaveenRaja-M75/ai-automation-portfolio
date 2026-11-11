"""Voice-mode FROST: local transcription, streamed speech, keyboard control.

Run:  python -m frost.voice

    SPACE   talk (press, then speak)
    P       pause / resume speech
    S       stop speaking now
    Q       quit

Design note, learned from a real session that went badly:

Always-on listening is the wrong default. A room has coughs, fans, other people
and — because the mic must stay live for barge-in — FROST's own voice coming
back through the speakers. Every one of those became a command. Worse, a short
phrase like "Hi FROST" is under half a second of audio with no context, so a
small model does not mishear it so much as *guess* at it: "Air process", "Air
philosophy", "Air frost".

Push-to-talk removes the guessing rather than trying to out-model it. FROST
transcribes exactly one utterance, when you asked it to, and ignores the room
the rest of the time.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time

from .brain import FROST
from .config import (
    EXIT_PHRASES,
    RESET_PHRASES,
    HALF_DUPLEX,
    HALLUCINATIONS,
    INPUT_DEVICE_INDEX,
    LISTEN_MODE,
    PUSH_SILENCE_DURATION,
    SILENCE_DURATION,
    STOP_WORDS,
    STT_MODEL,
    STT_PROVIDER,
    VOICE_EFFORT,
    VOICE_MODEL,
    WAKE_WORD,
    WORKSPACE,
)
from .transcript import clean_transcript
from .speech import Speaker

MIN_UTTERANCE_CHARS = 2
# Consecutive microphone failures before voice gives up. Bluetooth earbuds drop
# and come back constantly, so one failure means nothing; five in a row means the
# device is genuinely gone and continuing to retry would just spin.
MAX_MIC_FAILURES = 5
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _normalise(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum() or c.isspace()).strip()


def _workspace_snapshot() -> dict[str, float]:
    """Path -> mtime for everything in the workspace, to diff across a turn."""
    try:
        return {
            str(p): p.stat().st_mtime
            for p in WORKSPACE.rglob("*")
            if p.is_file() and ".home" not in p.parts
        }
    except OSError:
        return {}


class VoiceSession:
    def __init__(self, on_event=None) -> None:
        # The overlay renders from these events. Kept as a plain callback so
        # voice mode still works headless with no UI attached.
        self.on_event = on_event or (lambda _kind, _text: None)
        self.speaker = Speaker()
        self.recorder = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaking_turn = False
        self._talk = asyncio.Event()
        # Typed questions jump ahead of the microphone: if something is waiting
        # here when a turn starts, we use it instead of recording.
        self._typed: asyncio.Queue = asyncio.Queue()
        self._quit = threading.Event()
        self._frost: FROST | None = None
        self._cloud = None  # set when STT_PROVIDER != "local"

    # --- keyboard ----------------------------------------------------------

    def _start_keys(self) -> None:
        """Read keys on a thread. msvcrt is stdlib and needs no privileges."""
        try:
            import msvcrt
        except ImportError:
            print("  · keyboard control unavailable on this platform")
            return

        def loop() -> None:
            while not self._quit.is_set():
                if msvcrt.kbhit():
                    try:
                        key = msvcrt.getch().lower()
                    except Exception:
                        continue
                    if self._loop is not None:
                        self._loop.call_soon_threadsafe(self._on_key, key)
                time.sleep(0.03)

        threading.Thread(target=loop, daemon=True).start()

    def _on_key(self, key: bytes) -> None:
        if key == b" ":
            self._talk.set()
        elif key == b"p":
            paused = self.speaker.pause()
            print(f"  · {'paused' if paused else 'resumed'}")
        elif key in (b"s", b"\x1b"):
            self.speaker.stop()
            if self._frost is not None:
                asyncio.create_task(self._frost.interrupt())
            print("  · stopped")
        elif key == b"q":
            self._quit.set()
            self._talk.set()

    # --- microphone --------------------------------------------------------

    def _build_recorder(self):
        from RealtimeSTT import AudioToTextRecorder

        push = LISTEN_MODE == "push"
        kwargs = dict(
            model=STT_MODEL,
            language="en",
            silero_sensitivity=0.4,
            # Push-to-talk waits far longer before deciding you've finished,
            # because a keypress already told us you meant to speak.
            post_speech_silence_duration=(
                PUSH_SILENCE_DURATION if push else SILENCE_DURATION
            ),
            min_length_of_recording=0.5,
            spinner=False,
            beam_size=3,
            # Keeps a second of audio from before speech was detected, so the
            # first syllable isn't clipped.
            pre_recording_buffer_duration=1.0,
        )
        if not push:
            kwargs["on_vad_detect_start"] = self._on_speech_start
        if INPUT_DEVICE_INDEX is not None:
            kwargs["input_device_index"] = INPUT_DEVICE_INDEX
        if LISTEN_MODE == "wake":
            kwargs.update(
                wakeword_backend="oww",
                wake_words=WAKE_WORD,
                wake_words_sensitivity=0.5,
            )
        return AudioToTextRecorder(**kwargs)

    def _on_speech_start(self) -> None:
        # Only meaningful in always-on mode, where barge-in exists.
        if self._speaking_turn:
            self.speaker.stop()

    async def listen(self) -> str:
        if self._cloud is not None:
            return await self._cloud.listen()
        return (await asyncio.to_thread(self.recorder.text) or "").strip()

    def _rebuild_listener(self) -> None:
        """Throw away the dead audio stream and open a fresh one.

        Cheap enough to do on every failure: the cloud path is a PyAudio stream
        and a model name, and the local path reloads a model already in memory.
        Both are wrapped because a device that just vanished will often raise
        again on the way out, and failing to close is not a reason to stay dead.
        """
        if self._cloud is not None:
            try:
                self._cloud.close()
            except Exception:
                pass
            try:
                from .transcribe import CloudListener

                self._cloud = CloudListener()
            except Exception as exc:  # noqa: BLE001
                print(f"  · could not reopen the microphone: {exc}")
            return
        if self.recorder is not None:
            for method in ("shutdown", "stop"):
                fn = getattr(self.recorder, method, None)
                if fn is not None:
                    try:
                        fn()
                    except Exception:
                        pass
            try:
                self.recorder = self._build_recorder()
            except Exception as exc:  # noqa: BLE001
                print(f"  · could not reopen the microphone: {exc}")

    def _discard_buffered_audio(self) -> None:
        """Throw away anything captured before the user pressed talk.

        The recorder runs continuously from construction, so without this the
        next `text()` can return an utterance from minutes ago — which is how
        the same greeting came back four times in a row.
        """
        for method in ("clear_audio_queue", "abort"):
            fn = getattr(self.recorder, method, None)
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                pass

    def is_noise(self, text: str) -> bool:
        norm = _normalise(text)
        if len(norm) < MIN_UTTERANCE_CHARS:
            return True
        return norm in HALLUCINATIONS

    # --- permission over voice --------------------------------------------

    async def ask_permission(self, tool_name: str, description: str) -> bool:
        await self.speaker.say(f"I need approval to {description}. Press space and say yes.")
        await self.speaker.wait_idle(timeout=30)
        await self._await_talk()
        reply = _normalise(await self.listen())
        return any(w in reply for w in ("yes", "yeah", "go ahead", "approved", "do it", "confirm"))

    async def _await_talk(self) -> None:
        """Block until the user presses space (push mode) or immediately."""
        if LISTEN_MODE != "push":
            return
        # Wait first, clear after. Clearing first throws away a press made
        # while FROST was still answering, which looked like the space bar
        # having simply stopped working.
        await self._talk.wait()
        self._talk.clear()

    # --- main loop ---------------------------------------------------------

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        if STT_PROVIDER == "local":
            print(f"Loading {STT_MODEL} (first run downloads the model)...")
            self.recorder = self._build_recorder()
        else:
            from .transcribe import CloudListener

            self._cloud = CloudListener()
            print(f"Using {STT_PROVIDER} for speech-to-text.")
        self._start_keys()

        def status(msg: str) -> None:
            print(f"{DIM}  · {msg}{RESET}")
            self.on_event("status", msg)

        async with FROST(
            ask_permission=self.ask_permission,
            on_status=status,
            effort=VOICE_EFFORT,
            model=VOICE_MODEL,
        ) as frost:
            self._frost = frost

            async def speak(text: str) -> None:
                print(f"{BOLD}FROST ›{RESET} {text}")
                self.on_event("frost", text)
                self.on_event("state", "speaking")
                await self.speaker.say(text)

            await self.speaker.say("FROST online.")
            self._banner()
            mic_failures = 0

            while not self._quit.is_set():
                typed: str | None = None
                if LISTEN_MODE == "push":
                    await self._await_talk()
                    if self._quit.is_set():
                        break
                    if not self._typed.empty():
                        # Typed from the UI — skip the microphone entirely.
                        typed = (await self._typed.get()).strip()
                    else:
                        self._discard_buffered_audio()
                        print(f"{DIM}  · listening... (speak now){RESET}")
                        self.on_event("state", "listening")

                if typed is not None:
                    heard = typed
                else:
                    try:
                        heard = (await self.listen()).strip()
                        mic_failures = 0
                    except Exception as exc:  # noqa: BLE001
                        # A Bluetooth headset that drops for a second gives
                        # PortAudio `[Errno -9999] Unanticipated host error` and
                        # leaves a dead stream handle behind. This used to
                        # `break` -- one hiccup from a pair of earbuds ended
                        # voice for the whole session, and the only clue was a
                        # single grey line. The device is fine a second later;
                        # what needs replacing is the stream, not the session.
                        mic_failures += 1
                        print(f"  · microphone error: {exc}")
                        self.on_event("status", f"microphone error: {exc}")
                        if mic_failures >= MAX_MIC_FAILURES:
                            self.on_event(
                                "status",
                                "microphone failed repeatedly — voice stopped; "
                                "you can still type",
                            )
                            await self.speaker.say(
                                "My microphone keeps dropping out, so I've "
                                "stopped listening. You can still type to me."
                            )
                            break
                        await self.speaker.say("Lost the microphone. Reconnecting.")
                        self._rebuild_listener()
                        self._ready()
                        continue

                # Strip hallucinated filler before anything else looks at it.
                # Whisper wraps real speech in invented repeats ("you Yes, sir.
                # What time is it? Yes, sir. Yes, sir."), and the noise check
                # below only ever caught a turn made *entirely* of filler.
                cleaned = clean_transcript(heard)
                if cleaned != heard:
                    print(f"{DIM}  · cleaned: {heard!r} -> {cleaned!r}{RESET}")
                heard = cleaned

                if self.is_noise(heard):
                    if heard:
                        print(f"{DIM}  · ignored noise: {heard!r}{RESET}")
                    continue

                norm = _normalise(heard)
                print(f"{BOLD}you ›{RESET} {heard}")
                self.on_event("user", heard)
                self.on_event("state", "thinking")

                if norm in STOP_WORDS:
                    self.speaker.stop()
                    await frost.interrupt()
                    print("  · stopped")
                    continue

                if norm in {_normalise(p) for p in RESET_PHRASES}:
                    self.speaker.stop()
                    await frost.reset()
                    await self.speaker.say("Fresh start. I still have my memory.")
                    await self.speaker.wait_idle(timeout=10)
                    self._ready()
                    continue

                if norm in {_normalise(p) for p in EXIT_PHRASES}:
                    self.speaker.stop()
                    await self.speaker.say("Shutting down. Goodbye.")
                    await self.speaker.wait_idle(timeout=10)
                    break

                self._speaking_turn = True
                turn = asyncio.create_task(frost.ask(heard, on_text=speak))

                if LISTEN_MODE == "push" or HALF_DUPLEX:
                    # Nothing is listening, so nothing can interrupt except a
                    # keypress -- which the key thread handles independently.
                    before = _workspace_snapshot()
                    try:
                        await turn
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:  # noqa: BLE001
                        # Printing to the terminal only is how FROST came to
                        # look like it was ignoring Naveen: the browser showed
                        # his question, then nothing, forever. A failure has to
                        # reach the surface he is actually looking at, and be
                        # said out loud, or silence reads as contempt.
                        print(f"  · turn failed: {exc}")
                        self.on_event("status", f"turn failed: {exc}")
                        await self.speaker.say(
                            "Something broke on my side and I lost that. Say it "
                            "again and I'll try to reconnect."
                        )
                    await self.speaker.wait_idle()
                    self._report_new_files(before)
                    self._speaking_turn = False
                    self._ready()
                    continue

                await self._turn_with_bargein(turn, frost)
                self._speaking_turn = False

            # Printed on the way out so a session leaves a number behind rather
            # than an impression. This is the only honest answer to "is this
            # getting expensive?".
            print(f"{DIM}  · session: {frost.usage_report()}{RESET}")

        self._quit.set()

    async def _turn_with_bargein(self, turn: asyncio.Task, frost: FROST) -> None:
        """Always-on mode only: race the answer against the next utterance."""
        pending = asyncio.create_task(self.listen())
        done, _ = await asyncio.wait({turn, pending}, return_when=asyncio.FIRST_COMPLETED)

        if pending in done and not turn.done():
            interrupting = await pending
            if self.is_noise(interrupting):
                print(f"{DIM}  · ignored noise mid-answer: {interrupting!r}{RESET}")
                await turn
            else:
                self.speaker.stop()
                await frost.interrupt()
                turn.cancel()
                print("  · interrupted")
                try:
                    await turn
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                return

        pending.cancel()
        try:
            await turn
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await self.speaker.wait_idle()

    def _ready(self) -> None:
        self.on_event("state", "idle")
        if LISTEN_MODE == "push":
            queued = " (queued)" if self._talk.is_set() else ""
            print(f"{DIM}  · ready — press SPACE to talk{queued}{RESET}")

    def request_talk(self) -> None:
        """Let a UI trigger a turn the same way the space bar does."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._talk.set)

    def submit_text(self, text: str) -> None:
        """Typed input from the UI, handled exactly like a spoken utterance."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._typed.put_nowait, text)
            self._loop.call_soon_threadsafe(self._talk.set)

    def _report_new_files(self, before: dict[str, float]) -> None:
        """Say where things actually landed.

        "I wrote the notes to your workspace" is useless if you don't know
        where the workspace is. This prints the real, clickable path for
        anything that appeared or changed during the turn.
        """
        after = _workspace_snapshot()
        changed = [p for p, m in after.items() if before.get(p) != m]
        for path in sorted(changed)[:10]:
            print(f"{DIM}  · wrote {path}{RESET}")

    def _banner(self) -> None:
        if LISTEN_MODE == "push":
            how = (
                f"{BOLD}press SPACE, then say your request{RESET}\n"
                f"{DIM}  Don't say 'Hey FROST' first — SPACE is the wake word.\n"
                f"  A greeting followed by a pause ends the recording early,\n"
                f"  so the request that follows it gets lost.{RESET}"
            )
        elif LISTEN_MODE == "wake":
            how = f"say {WAKE_WORD!r} to wake me"
        else:
            how = "just start talking (always-on: expect phantom turns)"
        print(
            f"\n{how}\n"
            f"{DIM}  P{RESET} pause/resume speech   "
            f"{DIM}S{RESET} stop speaking   "
            f"{DIM}Q{RESET} quit\n"
            f"{DIM}  files land in {WORKSPACE}{RESET}\n"
        )
        self._ready()

    def close(self) -> None:
        self._quit.set()
        self.speaker.close()
        if self._cloud is not None:
            self._cloud.close()
        if self.recorder is not None:
            try:
                self.recorder.shutdown()
            except Exception:
                pass


async def main() -> int:
    session = VoiceSession()
    try:
        await session.run()
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
