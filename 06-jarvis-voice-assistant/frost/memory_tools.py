"""Persistent memory exposed to FROST as in-process MCP tools.

Storage is one markdown file per fact under `memory/`, plus an INDEX.md that is
cheap to load into the system prompt at startup. Flat files beat a vector store
at this scale: they are greppable, diffable, and the user can edit them by hand.

Upgrade path when the index outgrows the prompt (roughly a few hundred entries):
keep these tool signatures identical and swap the body of `_search` for an
embedding lookup. Nothing else has to change.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from .config import INDEX_FILE, MEMORY_DIR
from .permissions import looks_like_secret

# Refusing at the write site rather than trusting the persona. A memory is
# injected into the system prompt of every future session, so a credential
# stored once is a credential resent forever — and into a plain-text file.
_SECRET_REFUSAL = (
    "Refused: that contains something shaped like a live credential (a key, "
    "token, or card number), and memory is loaded into every future session in "
    "plain text. Record where it lives instead of what it is — "
    "\"the Groq key is in .env\" — and try again."
)

_STOPWORDS = {
    "the", "a", "an", "is", "was", "are", "were", "and", "or", "of", "to", "in",
    "on", "for", "with", "my", "me", "i", "it", "that", "this", "what", "do",
    "does", "did", "you", "your", "about", "at", "be", "have", "has",
}

_VALID_TYPES = {"user", "preference", "project", "reference", "event"}

# Lessons live in their own directory because they are used differently from
# facts. A fact is fetched on demand via `recall`. A lesson has to be in front of
# FROST *before* it acts, or it repeats the mistake — you cannot recall a lesson
# you have forgotten you need. So lessons are injected into the system prompt in
# full, every session, and are kept terse to earn that space.
LESSONS_DIR = MEMORY_DIR / "lessons"
MAX_INJECTED_LESSONS = 40

# Bounds on everything that flows from disk into the model.
#
# `recall` returned the full text of up to five files with no cap, and the index
# is injected into the system prompt of *every* session — so an oversized
# INDEX.md is a cost paid on every startup forever, which is the worst shape a
# cost can have. Nothing here is close to these limits today (the largest memory
# file is under 1KB); the point is that nothing was stopping it.
MAX_RECALL_CHARS = 8_000          # per memory returned
MAX_RECALL_TOTAL = 40_000         # per recall call
MAX_INDEX_CHARS = 16_000          # injected into every system prompt
MAX_LESSON_CHARS = 300            # a lesson is one sentence by design


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:60] or "note").rstrip("-")


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{2,}", text.lower()) if w not in _STOPWORDS}


def _rebuild_index() -> None:
    lines = ["# Memory index", "", "One line per memory. FROST maintains this.", ""]
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name == "INDEX.md":
            continue
        head = _summary_of(path)
        lines.append(f"- **{path.stem}** — {head}")
    INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary_of(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "(unreadable)"
    m = re.search(r"^summary:\s*(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    body = text.split("---")[-1].strip()
    return body.splitlines()[0][:120] if body else "(empty)"


def _search(query: str, limit: int = 5) -> list[tuple[float, Path]]:
    q = _tokens(query)
    if not q:
        return []
    scored: list[tuple[float, Path]] = []
    for path in MEMORY_DIR.glob("*.md"):
        if path.name == "INDEX.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        words = _tokens(text)
        overlap = len(q & words)
        if not overlap:
            continue
        # Favour focused notes over long ones that match by sheer size.
        score = overlap / (len(q) ** 0.5 * max(len(words), 1) ** 0.25)
        scored.append((score, path))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]


@tool(
    "remember",
    "Save one durable fact to long-term memory so it survives across sessions. "
    "Use for preferences, ongoing projects, people, routines, and corrections "
    "the user gave you. One fact per call. Never store secrets or credentials.",
    {"summary": str, "detail": str, "kind": str},
)
async def remember(args: dict) -> dict:
    summary = (args.get("summary") or "").strip()
    detail = (args.get("detail") or "").strip()
    kind = (args.get("kind") or "user").strip().lower()

    if not summary:
        return _text("Refused: `summary` is required.", is_error=True)
    if looks_like_secret(f"{summary}\n{detail}"):
        return _text(_SECRET_REFUSAL, is_error=True)
    if kind not in _VALID_TYPES:
        kind = "user"

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = MEMORY_DIR / f"{_slug(summary)}.md"
    now = _dt.datetime.now().isoformat(timespec="seconds")
    verb = "Updated" if path.exists() else "Saved"

    path.write_text(
        "---\n"
        f"summary: {summary}\n"
        f"kind: {kind}\n"
        f"updated: {now}\n"
        "---\n\n"
        f"{detail or summary}\n",
        encoding="utf-8",
    )
    _rebuild_index()
    return _text(f"{verb} memory '{path.stem}'.")


@tool(
    "recall",
    "Search long-term memory for facts relevant to a topic. Call this before "
    "answering anything that depends on prior sessions or personal context.",
    {"query": str},
)
async def recall(args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return _text("Refused: `query` is required.", is_error=True)

    hits = _search(query)
    if not hits:
        return _text(f"No memories matched '{query}'.")

    parts: list[str] = []
    budget = MAX_RECALL_TOTAL
    for _score, path in hits:
        try:
            body = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if len(body) > MAX_RECALL_CHARS:
            body = body[:MAX_RECALL_CHARS] + "\n... [memory truncated]"
        if len(body) > budget:
            parts.append(f"... [{len(hits) - len(parts)} more matches not shown]")
            break
        budget -= len(body)
        parts.append(f"### {path.stem}\n{body}")
    return _text("\n\n".join(parts))


@tool(
    "forget",
    "Delete a memory by its slug (the name shown by recall) when it is wrong or "
    "no longer true. Confirm with the user before calling this.",
    {"slug": str},
)
async def forget(args: dict) -> dict:
    slug = (args.get("slug") or "").strip()
    path = MEMORY_DIR / f"{_slug(slug)}.md"
    if not path.exists():
        return _text(f"No memory named '{slug}'.", is_error=True)
    path.unlink()
    _rebuild_index()
    return _text(f"Deleted memory '{path.stem}'.")


@tool(
    "record_lesson",
    "Record a mistake so it is not repeated. Call this whenever something you "
    "did failed and you worked out why: a command that errored, a script that "
    "crashed, an approach that turned out wrong, or a correction the user gave "
    "you. Every lesson is loaded at the start of every future session, so keep "
    "`rule` to one actionable sentence.",
    {"what_failed": str, "root_cause": str, "rule": str},
)
async def record_lesson(args: dict) -> dict:
    what = (args.get("what_failed") or "").strip()
    cause = (args.get("root_cause") or "").strip()
    rule = (args.get("rule") or "").strip()

    if not (what and rule):
        return _text("Refused: `what_failed` and `rule` are both required.", is_error=True)
    if looks_like_secret(f"{what}\n{cause}\n{rule}"):
        return _text(_SECRET_REFUSAL, is_error=True)

    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    path = LESSONS_DIR / f"{_slug(rule)}.md"
    existed = path.exists()
    now = _dt.datetime.now().isoformat(timespec="seconds")

    path.write_text(
        "---\n"
        f"rule: {rule}\n"
        "kind: lesson\n"
        f"updated: {now}\n"
        "---\n\n"
        f"**What failed:** {what}\n\n"
        f"**Root cause:** {cause or 'not established'}\n",
        encoding="utf-8",
    )
    verb = "Refined existing" if existed else "Recorded"
    return _text(f"{verb} lesson: {rule}")


@tool(
    "list_lessons",
    "List every lesson learned so far. Use when the user asks what you've "
    "learned, or when you want to review before a complex multi-step task.",
    {},
)
async def list_lessons(_args: dict) -> dict:
    lessons = _read_lessons()
    if not lessons:
        return _text("No lessons recorded yet.")
    body = "\n".join(f"- {rule}" for rule, _detail in lessons)
    return _text(f"{len(lessons)} lesson(s):\n{body}")


def _read_lessons() -> list[tuple[str, str]]:
    """Return (rule, detail) pairs, newest first."""
    out: list[tuple[float, str, str]] = []
    if not LESSONS_DIR.exists():
        return []
    for path in LESSONS_DIR.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^rule:\s*(.+)$", text, re.MULTILINE)
        if not m:
            continue
        detail = text.split("---", 2)[-1].strip()
        out.append((path.stat().st_mtime, m.group(1).strip(), detail))
    out.sort(key=lambda row: row[0], reverse=True)
    return [(rule, detail) for _mtime, rule, detail in out]


def load_lessons() -> str:
    """Lesson rules for the system prompt. Newest first, capped."""
    lessons = _read_lessons()
    if not lessons:
        return ""
    shown = lessons[:MAX_INJECTED_LESSONS]
    lines = [
        f"- {rule if len(rule) <= MAX_LESSON_CHARS else rule[:MAX_LESSON_CHARS] + '...'}"
        for rule, _detail in shown
    ]
    if len(lessons) > len(shown):
        lines.append(
            f"- ...and {len(lessons) - len(shown)} older lessons; call "
            "`list_lessons` to see them all."
        )
    return "\n".join(lines)


def _text(message: str, *, is_error: bool = False) -> dict:
    result: dict = {"content": [{"type": "text", "text": message}]}
    if is_error:
        result["is_error"] = True
    return result


def build_memory_server():
    """Return the in-process MCP server exposing the memory tools."""
    return create_sdk_mcp_server(
        name="memory",
        version="1.1.0",
        tools=[remember, recall, forget, record_lesson, list_lessons],
    )


MEMORY_TOOL_NAMES = [
    "mcp__memory__remember",
    "mcp__memory__recall",
    "mcp__memory__forget",
    "mcp__memory__record_lesson",
    "mcp__memory__list_lessons",
]


def load_index() -> str:
    """Index text to inject into the system prompt, so FROST knows what it knows."""
    try:
        text = INDEX_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) > MAX_INDEX_CHARS:
        text = (text[:MAX_INDEX_CHARS]
                + "\n- ...index truncated; call `recall` to search the rest.")
    return text
