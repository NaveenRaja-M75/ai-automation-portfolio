"""Central configuration. Everything tunable lives here."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- Paths -----------------------------------------------------------------
MEMORY_DIR = ROOT / "memory"
INDEX_FILE = MEMORY_DIR / "INDEX.md"
PERSONA_FILE = ROOT / "FROST.md"
WORKSPACE = Path(os.getenv("FROST_WORKSPACE", ROOT / "workspace"))

# The audit log deliberately lives OUTSIDE the workspace and outside ROOT, so
# the write gate (which confines writes to those two) cannot be used to edit or
# delete the record of what happened. An agent must not be able to rewrite its
# own audit trail.
AUDIT_LOG = Path(
    os.getenv(
        "FROST_AUDIT_LOG",
        Path(os.getenv("LOCALAPPDATA", Path.home() / ".local" / "share"))
        / "FROST"
        / "audit.jsonl",
    )
)

# --- Brain -----------------------------------------------------------------
MODEL = os.getenv("FROST_MODEL", "claude-opus-5")
# Voice turns are short, conversational, and latency-sensitive — the wrong shape
# for the most expensive model. Sonnet 5 is faster and cheaper per token, and
# the quality difference barely shows on "what's on my calendar". Deep research
# in text mode still gets the default.
VOICE_MODEL = os.getenv("FROST_VOICE_MODEL", "claude-sonnet-5")

# --- Knowledge base ---------------------------------------------------------
# An Obsidian vault is just markdown plus [[wikilinks]], so FROST can read one
# with no extra code. Read-only by default: your notes are the one thing you
# cannot regenerate, and an assistant that edits them unasked is a bad trade.
VAULT = os.getenv("FROST_VAULT", "")
VAULT_WRITABLE = os.getenv("FROST_VAULT_WRITABLE", "0") == "1"
# Additive-only drop zone. FROST can file new notes here even when the rest of
# the vault is read-only, which is the useful middle ground: it can contribute
# to your second brain without ever being able to overwrite your own thinking.
VAULT_INBOX = (Path(VAULT) / "00 Inbox") if VAULT else None

# --- Speech to text ---------------------------------------------------------
# local  faster-whisper on your CPU. Free and private, ~1.5s, accuracy limited.
# groq   whisper-large-v3-turbo. Fast, has a free tier, far more accurate.
# deepgram / elevenlabs  same idea, different vendors.
#
# Cloud STT sends your microphone audio to a third party. That is a real
# tradeoff, not a free upgrade — but it is the only remaining fix for accuracy
# short of buying a GPU.
STT_PROVIDER = os.getenv("FROST_STT_PROVIDER", "local").lower()
STT_API_KEY = (
    os.getenv("FROST_STT_API_KEY")
    or os.getenv("GROQ_API_KEY")
    or os.getenv("DEEPGRAM_API_KEY")
    or os.getenv("ELEVENLABS_API_KEY")
    or ""
)
STT_CLOUD_MODEL = os.getenv("FROST_STT_CLOUD_MODEL", "")

# Vocabulary hint sent with each transcription. Whisper uses it to bias toward
# terms it would otherwise guess at from sound alone — which is how "AI" comes
# back as "AA": acoustically they are nearly identical, and only context can
# separate them. Put names, tools and jargon you actually say here.
# Written as example *sentences*, not a comma list. Whisper's prompt is a
# continuation hint: it predicts what follows text in this style, so a list of
# nouns biases it toward producing lists of nouns. Real commands in the same
# shape as the ones actually spoken work far better.
#
# Two failures this is aimed at, both observed:
#   "hey FROST"  -> "Afrost"   (no word boundary; the name is unknown as a name)
#   "roast this" -> "post this" (/r/ and /p/ before the same vowel; only context
#                                separates them, and there was none)
STT_VOCABULARY = os.getenv(
    "FROST_STT_VOCABULARY",
    "Hey FROST, look at my screen. FROST, roast this screenshot. "
    "FROST, roast my resume. What is on my calendar tomorrow? "
    "Search my Obsidian vault for the AI automation agency notes. "
    "Naveen is in Chennai. Remember that. Record a lesson. Fresh start. "
    "Run git status in the sandbox. Draft a retainer invoice. "
    "We use Claude, Sonnet, Opus, MCP, n8n, Zapier, Make.com, LangGraph, "
    "Groq, Docker, Python, workflow automation, lead generation and APIs.",
)

# Push-to-talk recording limits for the cloud path.
RECORD_MAX_SECONDS = float(os.getenv("FROST_RECORD_MAX", "30"))
RECORD_SILENCE_RMS = int(os.getenv("FROST_RECORD_SILENCE_RMS", "500"))
# "low" keeps voice replies snappy; raise to "high" for hard multi-step work.
EFFORT = os.getenv("FROST_EFFORT", "medium")

# --- Voice -----------------------------------------------------------------
TTS_BACKEND = os.getenv("FROST_TTS", "edge")  # "edge" | "sapi" | "none"
TTS_VOICE = os.getenv("FROST_VOICE", "en-GB-RyanNeural")
TTS_RATE = os.getenv("FROST_TTS_RATE", "+8%")

WAKE_WORD = os.getenv("FROST_WAKE_WORD", "frost")
# Benchmarked on this machine (CPU, int8), seconds per short phrase:
#   tiny.en 0.86s   base.en 1.51s   small.en 4.94s
# small.en would add ~5s to every turn, which no conversation survives. base.en
# is the ceiling worth paying for on CPU. Note the benchmark also found all
# three ~100% accurate on clean audio — meaning the real-world misrecognition
# was the microphone, not the model. Fix the mic before reaching for a bigger
# model, and only move to small.en if you get a GPU.
STT_MODEL = os.getenv("FROST_STT_MODEL", "base.en")
USE_WAKE_WORD = os.getenv("FROST_USE_WAKE_WORD", "0") == "1"

# How FROST decides you are talking to it.
#
#   push  (default)  Press SPACE, then speak. One utterance per press.
#   wake             Say the wake word first. Hands-free, but a small local
#                    wake model still misfires in a noisy room.
#   always           Transcribe everything it hears. This is what produced
#                    phantom turns from coughing and from FROST's own voice:
#                    every sound in the room became a command. Not recommended.
#
# Always-on listening is the single largest source of wrong input. A short
# phrase like "Hi FROST" is only ~0.4s of audio with no surrounding context,
# so a small model guesses — "Air process", "Air philosophy". Push-to-talk
# removes the guess entirely, because it only transcribes when you asked it to.
LISTEN_MODE = os.getenv("FROST_LISTEN_MODE", "wake" if USE_WAKE_WORD else "push")

# Which microphone. A laptop's far-field array picks up the room and your own
# speakers; a headset mic is worth more than any model upgrade. Run
# `python -m frost.mics` to list indices.
# Accepts a name fragment ("Airdopes") or an index ("3"). Prefer the name:
# indices are reassigned whenever a Bluetooth device connects or the audio
# stack re-enumerates, so yesterday's headset index can be today's speaker.
INPUT_DEVICE = os.getenv("FROST_INPUT_DEVICE") or None
INPUT_DEVICE_INDEX = INPUT_DEVICE  # resolved at runtime against live devices

# Measured, not assumed: on short conversational turns `low` gave no speed-up
# over `medium` (2.22s vs 1.94s median to first audio — inside the noise),
# because the model already thinks very little on turns like these. So it keeps
# the normal default and the better answers. Set this to "low" only if you
# measure a win on your own workload.
VOICE_EFFORT = os.getenv("FROST_VOICE_EFFORT", EFFORT)

# Said mid-answer, these stop FROST immediately and are never sent to the model.
# Half-duplex: stop listening while FROST speaks. On laptop speakers the mic
# hears FROST's own voice and treats it as an interruption, so the assistant
# talks over itself and stops mid-answer. Headphones make that impossible,
# which is why this defaults off — barge-in is worth more when it works. Turn
# it on if you must use open speakers.
HALF_DUPLEX = os.getenv("FROST_HALF_DUPLEX", "0") == "1"

STOP_WORDS = {
    "stop", "stop it", "shut up", "quiet", "cancel", "nevermind", "never mind",
    "frost stop", "stop frost", "enough", "abort", "wait",
}

# Whisper invents these when it is fed silence or room noise. They are the
# single biggest source of phantom turns; "Thank you." on an empty room is the
# classic. Dropped only when they are the *entire* utterance.
HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "thank you for watching", "thank you very much", "you", "bye", "bye.",
    "okay", "ok", ".", "..", "...", "sure", "yeah", "uh", "um", "ah", "ahh",
    "ahhh", "hmm", "mm", "mhm", "so", "and", "the", "i", "oh",
}

# How long a pause means "I've finished speaking". The single most important
# feel dial: too low and it cuts you off mid-sentence, too high and it feels
# sluggish. 0.4s is a good starting point for normal speech.
SILENCE_DURATION = float(os.getenv("FROST_SILENCE_DURATION", "0.6"))

# Push-to-talk can afford to wait much longer than hands-free mode: you pressed
# a key, so nothing is guessing whether you meant to speak. A short cutoff here
# is actively harmful — say "Hey FROST, can you research X" and the natural
# pause after the greeting ends the utterance, so only the greeting is
# transcribed and the actual request becomes a separate fragment. That is what
# produced the "A frost" / "High first" loop.
PUSH_SILENCE_DURATION = float(os.getenv("FROST_PUSH_SILENCE", "1.5"))

# Compared against lowercased input, so these must stay lowercase.
EXIT_PHRASES = {"goodbye frost", "shut down", "shutdown frost", "exit", "quit"}

# Drop the conversation and start a new one, keeping tools, memory and lessons.
# In a persistent session the whole history is resent every turn, so context is
# the running cost — and a screenshot adds ~1.5k tokens that never leave again.
# This is the manual, honest version of "context pruning": you decide when to
# forget, instead of a background process silently dropping something you needed.
RESET_PHRASES = {
    "fresh start", "start fresh", "reset", "reset context", "new conversation",
    "clear context", "forget this conversation", "start over",
}

# --- Sandbox ---------------------------------------------------------------
# Docker is used automatically when a daemon is reachable; otherwise FROST falls
# back to a scrubbed subprocess. See frost/sandbox.py for what each buys you.
SANDBOX_IMAGE = os.getenv("FROST_SANDBOX_IMAGE", "python:3.11-slim")
SANDBOX_TIMEOUT = int(os.getenv("FROST_SANDBOX_TIMEOUT", "120"))
SANDBOX_NETWORK = os.getenv("FROST_SANDBOX_NETWORK", "0") == "1"


def ensure_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text(
            "# Memory index\n\nOne line per memory. FROST maintains this.\n",
            encoding="utf-8",
        )
