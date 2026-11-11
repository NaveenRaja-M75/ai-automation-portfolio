"""Cloud speech-to-text, and the push-to-talk recorder that feeds it.

Why this exists as a separate path rather than a setting on RealtimeSTT: the
local stack couples recording, voice-activity detection and transcription into
one object. For cloud transcription we only want the first two, and we want the
audio bytes back. Recording directly with pyaudio is both simpler and more
controllable than fighting that coupling, and it removes torch from the loop.

Measured on this machine, local base.en transcribes a short phrase in ~1.5s and
still misreads short utterances. A hosted whisper-large-v3 answers in roughly
200-400ms and is dramatically better on accented and far-field speech. The cost
is real though: the audio leaves your machine. That is a decision for the user,
so `local` stays the default and this is opt-in.
"""

from __future__ import annotations

import asyncio
import audioop
import io
import time
import wave

from .config import (
    INPUT_DEVICE_INDEX,
    PUSH_SILENCE_DURATION,
    RECORD_MAX_SECONDS,
    RECORD_SILENCE_RMS,
    STT_API_KEY,
    STT_CLOUD_MODEL,
    STT_PROVIDER,
    STT_VOCABULARY,
)

SAMPLE_RATE = 16_000
CHANNELS = 1
CHUNK = 1024
SAMPLE_WIDTH = 2  # 16-bit

# provider -> (url, default model, builder for the request)
_DEFAULT_MODELS = {
    # `turbo` is a distilled model: cheaper and *slightly* faster, and weaker on
    # exactly the input that matters here — short commands carrying a proper
    # noun. Benchmarked on this account, same audio, prompt attached:
    #
    #   "Hey FROST, look at my screen and roast it."
    #     turbo     2.1s  ->  "Hey, Frost, look at my screen and roast it."
    #     large-v3  1.7s  ->  "Hey FROST, look at my screen and roast it."
    #
    # Same latency, better transcript, so there is nothing to trade off. Note
    # the benchmark used synthesised speech, which is far kinder than a real
    # microphone in a real room — it shows turbo is the weaker model, not that
    # large-v3 will fix every misheard word.
    "groq": "whisper-large-v3",
    "deepgram": "nova-2",
    "elevenlabs": "scribe_v1",
}


def resolve_input_device(pa, wanted) -> int | None:
    """Turn a configured device (index or name fragment) into a usable index.

    Indices are not stable: they shift whenever a Bluetooth device connects or
    the audio stack re-enumerates, so the index that was your headset an hour
    ago can be a *speaker* now — which is what produced
    `[Errno -9998] Invalid number of channels`. So validate that whatever we
    resolve is genuinely an input, and fall back loudly rather than crash.
    """
    if wanted in (None, ""):
        return None

    def usable(index: int) -> bool:
        try:
            return pa.get_device_info_by_index(index)["maxInputChannels"] > 0
        except Exception:
            return False

    # A name fragment survives renumbering; an index does not.
    if isinstance(wanted, str) and not wanted.lstrip("-").isdigit():
        needle = wanted.lower()
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
            except Exception:
                continue
            if info["maxInputChannels"] > 0 and needle in str(info["name"]).lower():
                return i
        print(f"[FROST] no input device matching {wanted!r}; using system default")
        return None

    index = int(wanted)
    if usable(index):
        return index
    try:
        name = pa.get_device_info_by_index(index)["name"]
    except Exception:
        name = "unknown"
    print(
        f"[FROST] device {index} ({name}) is not an input device — indices shift "
        f"when Bluetooth reconnects. Using system default. "
        f"Run `python -m frost.mics` and set FROST_INPUT_DEVICE to a name instead."
    )
    return None


class PushRecorder:
    """Records one utterance: starts on demand, stops when you stop talking.

    Endpointing is plain RMS rather than a neural VAD. In push-to-talk that is
    enough — we already know the user intends to speak, so the only question is
    when they stopped, and loudness answers it without loading a model.
    """

    def __init__(self) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        self._format = pyaudio.paInt16
        self._device = resolve_input_device(self._pa, INPUT_DEVICE_INDEX)
        self._rate, self._channels = self._negotiate()
        if (self._rate, self._channels) != (SAMPLE_RATE, CHANNELS):
            print(
                f"[FROST] mic runs at {self._rate}Hz x{self._channels}; "
                f"converting to {SAMPLE_RATE}Hz mono for transcription"
            )

    def _negotiate(self) -> tuple[int, int]:
        """Find a format this microphone will actually open.

        Not every device supports 16kHz mono — the WDM-KS Realtek endpoint, for
        one, refuses 16kHz entirely. Rather than fail, record at whatever the
        device offers and convert afterwards.
        """
        import pyaudio

        try:
            default_rate = int(
                self._pa.get_device_info_by_index(self._device)["defaultSampleRate"]
                if self._device is not None
                else self._pa.get_default_input_device_info()["defaultSampleRate"]
            )
        except Exception:
            default_rate = 44100

        for rate in (SAMPLE_RATE, default_rate, 48000, 44100):
            for channels in (1, 2):
                try:
                    if self._pa.is_format_supported(
                        rate,
                        input_device=self._device,
                        input_channels=channels,
                        input_format=pyaudio.paInt16,
                    ):
                        return rate, channels
                except Exception:
                    continue
        return default_rate, 1  # let open() raise a real error if even this fails

    def record(self) -> bytes:
        """Blocking. Returns WAV bytes, or b"" if nothing audible was said."""
        kwargs = dict(
            format=self._format,
            channels=self._channels,
            rate=self._rate,
            input=True,
            frames_per_buffer=CHUNK,
        )
        if self._device is not None:
            kwargs["input_device_index"] = self._device

        stream = self._pa.open(**kwargs)
        frames: list[bytes] = []
        started = time.monotonic()
        last_loud = None
        heard_anything = False

        try:
            while True:
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
                # Measure loudness on mono so the silence threshold means the
                # same thing regardless of what the device handed us.
                mono = audioop.tomono(data, SAMPLE_WIDTH, 0.5, 0.5) if self._channels == 2 else data
                rms = audioop.rms(mono, SAMPLE_WIDTH)
                now = time.monotonic()

                if rms > RECORD_SILENCE_RMS:
                    heard_anything = True
                    last_loud = now
                elif heard_anything and last_loud is not None:
                    if now - last_loud > PUSH_SILENCE_DURATION:
                        break
                # Give them a moment to start before giving up on silence.
                if not heard_anything and now - started > 5.0:
                    break
                if now - started > RECORD_MAX_SECONDS:
                    break
        finally:
            stream.stop_stream()
            stream.close()

        if not heard_anything:
            return b""

        pcm = b"".join(frames)
        if self._channels == 2:
            pcm = audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5)
        if self._rate != SAMPLE_RATE:
            pcm, _ = audioop.ratecv(pcm, SAMPLE_WIDTH, 1, self._rate, SAMPLE_RATE, None)
        return _to_wav(pcm)

    def close(self) -> None:
        try:
            self._pa.terminate()
        except Exception:
            pass


def _to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


class CloudTranscriber:
    """One interface over the hosted STT vendors worth using."""

    def __init__(self, provider: str = STT_PROVIDER, api_key: str = STT_API_KEY) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = STT_CLOUD_MODEL or _DEFAULT_MODELS.get(provider, "")
        if provider not in _DEFAULT_MODELS:
            raise ValueError(
                f"Unknown STT provider {provider!r}. "
                f"Use one of: local, {', '.join(_DEFAULT_MODELS)}"
            )
        if not api_key:
            raise ValueError(
                f"{provider} needs an API key. Put it in .env as FROST_STT_API_KEY."
            )

    async def transcribe(self, wav: bytes) -> str:
        if not wav:
            return ""
        import httpx

        url, headers, files, data = self._request(wav)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
        if resp.status_code >= 400:
            # Loudly, not silently: a dead key must not look like silence.
            raise RuntimeError(f"{self.provider} STT failed [{resp.status_code}]: {resp.text[:200]}")
        return self._extract(resp.json())

    def _request(self, wav: bytes):
        auth = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider == "groq":
            data = {"model": self.model, "language": "en", "response_format": "json"}
            if STT_VOCABULARY:
                data["prompt"] = STT_VOCABULARY
            return (
                "https://api.groq.com/openai/v1/audio/transcriptions",
                auth,
                {"file": ("speech.wav", wav, "audio/wav")},
                data,
            )
        if self.provider == "elevenlabs":
            return (
                "https://api.elevenlabs.io/v1/speech-to-text",
                {"xi-api-key": self.api_key},
                {"file": ("speech.wav", wav, "audio/wav")},
                {"model_id": self.model},
            )
        # deepgram takes raw audio as the body, not multipart
        return (
            f"https://api.deepgram.com/v1/listen?model={self.model}&smart_format=true&language=en",
            {"Authorization": f"Token {self.api_key}", "Content-Type": "audio/wav"},
            None,
            wav,
        )

    def _extract(self, payload: dict) -> str:
        if self.provider == "deepgram":
            try:
                alt = payload["results"]["channels"][0]["alternatives"][0]
                return (alt.get("transcript") or "").strip()
            except (KeyError, IndexError):
                return ""
        return (payload.get("text") or "").strip()


class CloudListener:
    """Drop-in replacement for the local listen() used by voice mode."""

    def __init__(self) -> None:
        self.recorder = PushRecorder()
        self.transcriber = CloudTranscriber()

    async def listen(self) -> str:
        wav = await asyncio.to_thread(self.recorder.record)
        if not wav:
            return ""
        return await self.transcriber.transcribe(wav)

    def close(self) -> None:
        self.recorder.close()
