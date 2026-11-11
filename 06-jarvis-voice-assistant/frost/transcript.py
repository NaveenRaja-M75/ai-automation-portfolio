"""Clean what the speech recogniser actually returns.

Whisper does not fail by returning nothing. Fed silence or room tone it
*invents* fluent text, because a language model with nothing to transcribe still
predicts the most likely next words. The classic outputs are "Thank you.",
"Thanks for watching!", and — the one that started this file — a short phrase
repeated several times around the real sentence:

    "you Yes, sir. What time is it? Yes, sir. Yes, sir."

`config.HALLUCINATIONS` already listed these phrases, but the check was
`whole utterance in HALLUCINATIONS`. That catches a turn made *entirely* of
filler and misses the far more common case: filler wrapped around a real
question. The junk then reaches the model, which dutifully tries to make sense
of "Yes, sir" and answers something odd.

Two deterministic passes fix it, in this order:

1. **Collapse consecutive repeats.** Push-to-talk records a fixed trailing
   silence, and repetition is what Whisper does with silence. A short sentence
   said twice in a row is nearly always the artefact, not the speaker.
2. **Strip filler that flanks real content.** Only ever when something real
   survives — a turn that is genuinely just "yes" must still reach the model,
   because that is how permission prompts get answered.

Deliberately not solved with a model. A second model deciding what you "really
said" is a new way to be confidently wrong about your own words.
"""

from __future__ import annotations

import re

from .config import HALLUCINATIONS

# Sentence-ish boundaries. Whisper punctuates its own hallucinations, which is
# precisely what makes them separable.
_SPLIT = re.compile(r"(?<=[.!?])\s+")

# A repeat only counts as an artefact if it is short. Someone repeating a long
# sentence verbatim meant it; Whisper looping on "Yes, sir." did not.
MAX_REPEAT_CHARS = 40


def _norm(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum() or c.isspace()).strip()


def _is_filler(sentence: str) -> bool:
    return _norm(sentence) in HALLUCINATIONS


def clean_transcript(text: str) -> str:
    """Return `text` with hallucinated filler and looped repeats removed.

    Returns "" when nothing real survives, which the caller treats as noise.
    """
    if not text or not text.strip():
        return ""

    pieces = [p.strip() for p in _SPLIT.split(text.strip()) if p.strip()]
    if not pieces:
        return ""

    # A leading bare "you" / "so" / "the" is Whisper priming itself on the first
    # fragment of audio. Strip it before anything else, or it hides the sentence
    # behind it from the repeat counter.
    head = pieces[0].split(None, 1)
    if len(head) == 2 and _norm(head[0]) in HALLUCINATIONS:
        pieces[0] = head[1]

    # Repetition is the signal, not the wording. "Yes, sir." is in no filler
    # list and never will be — the tell is that it appears three times around
    # one real question. Counting beats enumerating: it catches the artefact
    # whatever phrase Whisper happens to loop on today.
    counts: dict[str, int] = {}
    for piece in pieces:
        counts[_norm(piece)] = counts.get(_norm(piece), 0) + 1

    def looped(piece: str) -> bool:
        norm = _norm(piece)
        return counts[norm] >= 2 and len(norm) <= MAX_REPEAT_CHARS

    real = [p for p in pieces if not _is_filler(p) and not looped(p)]
    if real:
        return " ".join(real).strip()

    # Nothing survived: the whole turn was filler or a loop. Return one copy so
    # the caller's noise check sees a recognisable phrase rather than a wall of
    # repeats — "Okay. Okay. Okay." becomes "Okay.", which it already knows.
    deduped: list[str] = []
    for piece in pieces:
        if not deduped or _norm(piece) != _norm(deduped[-1]):
            deduped.append(piece)
    return " ".join(deduped).strip()
