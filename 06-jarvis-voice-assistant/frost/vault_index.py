"""Turn a notes folder into a graph: nodes, edges, hubs.

`[[wikilinks]]` between markdown files are already a graph — this just reads it
out. Nodes are notes, edges are links, and a node's radius comes from how many
things point at it, so hubs are visibly bigger without anyone tagging them.

Deliberately tolerant: a link to a note that doesn't exist yet is still an edge
worth drawing (it marks an intention), and a folder with no links at all still
produces a valid, if lonely, graph.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import VAULT, WORKSPACE

# The character class must exclude newlines. Without that, an unclosed `[[` in
# prose swallows everything up to the next `]]` several paragraphs later and
# becomes a "note" whose title is an entire sentence.
WIKILINK = re.compile(r"\[\[([^\]\[|#\n]{1,80})(?:[#|][^\]\n]*)?\]\]")
CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
TAG = re.compile(r"(?:^|\s)#([a-zA-Z][\w/-]{1,30})")
FRONT_TAGS = re.compile(r"^tags:\s*\[([^\]]*)\]", re.MULTILINE)

SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules", ".home", "__pycache__"}
MAX_BYTES = 2 * 1024 * 1024
# `build_graph` has always skipped huge files; `read_note` did not, so one
# oversized note could return unbounded text straight into the context. A note
# past this is a pasted dump, not a note, and the first 200KB of it is enough to
# say so. Bounds like this cost nothing until the day they are the difference
# between a slow turn and a dead session.
MAX_NOTE_CHARS = 200_000


@dataclass
class Node:
    id: str
    label: str
    kind: str
    folder: str
    links: int = 0
    backlinks: int = 0
    words: int = 0
    tags: list[str] = field(default_factory=list)
    path: str = ""


def _kind_of(path: Path, root: Path, tags: list[str]) -> str:
    """Colour category. Folder first, then tags, then a default."""
    try:
        top = path.relative_to(root).parts[0]
    except (ValueError, IndexError):
        top = ""
    folder_kinds = {
        "01 Projects": "project",
        "02 Areas": "area",
        "03 Resources": "resource",
        "04 Archive": "archive",
        "People": "person",
        "Daily": "daily",
        "00 Inbox": "inbox",
        "_templates": "template",
    }
    if top in folder_kinds:
        return folder_kinds[top]
    for tag in tags:
        if tag in {"project", "area", "person", "resource", "daily", "meta"}:
            return tag
    return "note"


def _tags_of(text: str) -> list[str]:
    tags: list[str] = []
    m = FRONT_TAGS.search(text)
    if m:
        tags += [t.strip().strip("\"'") for t in m.group(1).split(",") if t.strip()]
    tags += TAG.findall(text)
    seen, out = set(), []
    for t in tags:
        low = t.lower()
        if low and low not in seen:
            seen.add(low)
            out.append(low)
    return out[:8]


def build_graph(roots: list[Path] | None = None) -> dict:
    """Scan the vault (and workspace) and return a JSON-ready graph."""
    if roots is None:
        roots = []
        if VAULT:
            roots.append(Path(VAULT))
        roots.append(WORKSPACE)

    nodes: dict[str, Node] = {}
    raw_links: list[tuple[str, str]] = []

    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            key = path.stem.lower()
            tags = _tags_of(text)
            try:
                folder = str(path.parent.relative_to(root))
            except ValueError:
                folder = ""
            nodes[key] = Node(
                id=key,
                label=path.stem,
                kind=_kind_of(path, root, tags),
                folder="" if folder == "." else folder,
                words=len(text.split()),
                tags=tags,
                path=str(path),
            )
            # Links shown as examples inside code spans are documentation, not
            # real connections — strip code before looking for them.
            linkable = INLINE_CODE.sub(" ", CODE_FENCE.sub(" ", text))
            for target in WIKILINK.findall(linkable):
                target = target.split("/")[-1].strip().lower()
                if target:
                    raw_links.append((key, target))

    edges = []
    for src, dst in raw_links:
        if src == dst:
            continue
        if dst not in nodes:
            # A link to a note that doesn't exist yet is a real intention.
            nodes[dst] = Node(id=dst, label=dst, kind="missing", folder="")
        nodes[src].links += 1
        nodes[dst].backlinks += 1
        edges.append({"source": src, "target": dst})

    ranked = sorted(nodes.values(), key=lambda n: n.backlinks + n.links, reverse=True)
    return {
        "nodes": [asdict(n) for n in nodes.values()],
        "edges": edges,
        "hubs": [{"id": n.id, "label": n.label, "score": n.backlinks + n.links}
                 for n in ranked[:12]],
        "counts": _counts(nodes),
        "stats": {"notes": len(nodes), "connections": len(edges)},
    }


def _counts(nodes: dict[str, Node]) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in nodes.values():
        out[n.kind] = out.get(n.kind, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))


def read_note(note_id: str) -> dict:
    graph = build_graph()
    for n in graph["nodes"]:
        if n["id"] == note_id and n.get("path"):
            try:
                text = Path(n["path"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                break
            if len(text) > MAX_NOTE_CHARS:
                text = (text[:MAX_NOTE_CHARS]
                        + f"\n\n... [note truncated at {MAX_NOTE_CHARS:,} of "
                          f"{len(text):,} characters]")
            return {"id": note_id, "label": n["label"], "text": text}
    return {"id": note_id, "label": note_id, "text": "(note not found on disk)"}


if __name__ == "__main__":  # pragma: no cover
    g = build_graph()
    print(f"{g['stats']['notes']} notes, {g['stats']['connections']} connections")
    print("kinds:", g["counts"])
    print("top hubs:")
    for h in g["hubs"][:8]:
        print(f"  {h['score']:>3}  {h['label']}")
