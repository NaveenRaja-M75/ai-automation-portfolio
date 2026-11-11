"""Text to speech, with sentence streaming and barge-in.

Two things matter for a voice assistant to feel alive:

1. Time to first audio. We speak sentence one while the model is still writing
   sentence three, instead of waiting for the whole reply.
2. Interruptibility. If you start talking, FROST stops mid-word. A voice agent
   you cannot interrupt is exhausting to use.

`edge` backend: Microsoft neural voices via edge-tts. Free, no API key, genuinely
good. `sapi` backend: the Windows built-in voice — robotic, but zero install and
a useful fallback. `none`: print only.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

from .config import TTS_BACKEND, TTS_RATE, TTS_VOICE

# --- Turning markdown into something worth hearing -------------------------

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"[A-Za-z]:\\[^\s,;]+|(?:/[\w.-]+){2,}")
_MD_MARKS = re.compile(r"[*_~#>|`]+")
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_WS = re.compile(r"\s+")


def clean_for_speech(text: str) -> str:
    """Strip everything a speech engine would read out as noise."""
    text = _CODE_FENCE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    # Unwrap inline code before the URL/path passes, so replacing the contents of
    # a `code span` cannot strand its opening backtick.
    text = _INLINE_CODE.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _PATH.sub("a file path", text)
    text = _BULLET.sub("", text)
    text = _NUMBERED.sub("", text)
    text = _MD_MARKS.sub("", text)
    return _WS.sub(" ", text).strip()


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str, min_len: int = 12) -> list[str]:
    """Split into speakable chunks, merging fragments too short to be worth a call."""
    out: list[str] = []
    for part in _SENTENCE_END.split(text):
        part = part.strip()
        if not part:
            continue
        if out and len(part) < min_len:
            out[-1] += " " + part
        else:
            out.append(part)
    return out


# --- Backends --------------------------------------------------------------


class Speaker:
    """Plays speech, one chunk at a time, and can be stopped instantly."""

    def __init__(self, backend: str = TTS_BACKEND, voice: str = TTS_VOICE) -> None:
        self.voice = voice
        self.backend = backend
        self._mixer = None
        self._sapi = None
        self._tmp = Path(tempfile.gettempdir()) / "FROST_tts"
        self._tmp.mkdir(exist_ok=True)
        # Sweep anything a previous run couldn't delete (killed mid-playback).
        for stale in self._tmp.glob("chunk_*.mp3"):
            try:
                stale.unlink()
            except OSError:
                pass
        self._counter = 0

        # Pipeline state. Queues are created lazily so a Speaker can be built
        # outside a running event loop.
        self._generation = 0
        self._text_q: asyncio.Queue | None = None
        self._audio_q: asyncio.Queue | None = None
        self._workers: list[asyncio.Task] = []
        self._idle = asyncio.Event()
        self._idle.set()
        self._inflight = 0
        self._paused = False

        if backend == "edge":
            try:
                import pygame  # noqa: F401
                import edge_tts  # noqa: F401
            except ImportError:
                print("[FROST] edge-tts/pygame missing, falling back to Windows voice.")
                self.backend = "sapi"

        if self.backend == "edge":
            import pygame

            pygame.mixer.init()
            self._mixer = pygame.mixer
        elif self.backend == "sapi":
            try:
                import pyttsx3

                self._sapi = pyttsx3.init()
            except Exception:
                print("[FROST] no TTS backend available; running text-only.")
                self.backend = "none"

    # --- public API --------------------------------------------------------

    async def say(self, text: str) -> None:
        """Queue a chunk for speech. Returns as soon as it is queued.

        Deliberately does not wait for playback: the caller is usually the
        model's token stream, and blocking it would stall the pipeline this
        class exists to build. Use `wait_idle()` to await the end of speech.
        """
        cleaned = clean_for_speech(text)
        if not cleaned or self.backend == "none":
            return

        if self.backend == "sapi":
            await asyncio.to_thread(self._say_sapi, cleaned)
            return

        self._ensure_workers()
        self._inflight += 1
        self._idle.clear()
        await self._text_q.put((self._generation, cleaned))

    async def wait_idle(self, timeout: float = 120.0) -> None:
        """Block until everything queued has finished playing (or was stopped)."""
        if self.backend != "edge" or self._text_q is None:
            return
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def pause(self) -> bool:
        """Hold playback where it is. Returns the new paused state."""
        if self.backend != "edge" or self._mixer is None:
            return False
        self._paused = not self._paused
        try:
            if self._paused:
                self._mixer.music.pause()
            else:
                self._mixer.music.unpause()
        except Exception:
            pass
        return self._paused

    def stop(self) -> None:
        """Cancel everything: queued text, in-flight synthesis, and playback.

        Bumping the generation is what makes this work during synthesis. A
        stop() that only silenced the mixer would let audio that was mid-render
        start playing a second later, which is the bug this replaced.
        """
        self._generation += 1
        self._paused = False
        for queue in (self._text_q, self._audio_q):
            if queue is None:
                continue
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except Exception:
                    break
        self._inflight = 0
        self._idle.set()

        if self.backend == "edge" and self._mixer is not None:
            try:
                self._mixer.music.stop()
            except Exception:
                pass
        elif self.backend == "sapi" and self._sapi is not None:
            try:
                self._sapi.stop()
            except Exception:
                pass

    # --- pipeline ----------------------------------------------------------

    def _ensure_workers(self) -> None:
        if self._workers:
            return
        self._text_q = asyncio.Queue()
        # Bounded so we synthesise at most one sentence ahead of playback;
        # unbounded would render a whole reply that barge-in then discards.
        self._audio_q = asyncio.Queue(maxsize=1)
        self._workers = [
            asyncio.create_task(self._synth_worker()),
            asyncio.create_task(self._play_worker()),
        ]

    async def _synth_worker(self) -> None:
        import edge_tts

        while True:
            generation, text = await self._text_q.get()
            try:
                if generation != self._generation:
                    self._finish_one()
                    continue
                self._counter += 1
                # Unique per chunk, never recycled. A fixed pool of filenames
                # races the player: pygame still holds slot N open when the
                # counter wraps back to it, and Windows refuses the overwrite
                # with EACCES, silently dropping that sentence.
                path = self._tmp / f"chunk_{id(self)}_{self._counter}.mp3"
                comm = edge_tts.Communicate(text, self.voice, rate=TTS_RATE)
                wrote = False
                with open(path, "wb") as fh:
                    async for chunk in comm.stream():
                        if self._generation != generation:
                            break
                        if chunk["type"] == "audio":
                            fh.write(chunk["data"])
                            wrote = True
                if wrote and self._generation == generation:
                    await self._audio_q.put((generation, path))
                else:
                    self._finish_one()
            except Exception as exc:  # noqa: BLE001
                print(f"[FROST] speech synthesis failed: {exc}")
                self._finish_one()
            finally:
                self._text_q.task_done()

    async def _play_worker(self) -> None:
        while True:
            generation, path = await self._audio_q.get()
            try:
                if generation != self._generation:
                    continue
                self._mixer.music.load(str(path))
                self._mixer.music.play()
                # `get_busy()` goes False while paused, so the pause flag has to
                # hold the loop open or a paused track silently ends.
                while self._mixer.music.get_busy() or self._paused:
                    if self._generation != generation:
                        self._mixer.music.stop()
                        break
                    await asyncio.sleep(0.02)
            except Exception as exc:  # noqa: BLE001
                print(f"[FROST] playback failed: {exc}")
            finally:
                try:
                    self._mixer.music.unload()
                except Exception:
                    pass
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass  # cleaned up on next startup instead
                self._audio_q.task_done()
                self._finish_one()

    def _finish_one(self) -> None:
        self._inflight = max(0, self._inflight - 1)
        if self._inflight == 0:
            self._idle.set()

    def _say_sapi(self, text: str) -> None:
        self._sapi.say(text)
        self._sapi.runAndWait()

    def close(self) -> None:
        self.stop()
        for task in self._workers:
            task.cancel()
        self._workers = []
        if self.backend == "edge" and self._mixer is not None:
            try:
                self._mixer.quit()
            except Exception:
                pass
